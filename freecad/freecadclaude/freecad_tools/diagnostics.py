# SPDX-License-Identifier: LGPL-2.1-or-later
"""Post-tool notes: what a run_python call actually changed, and what broke.

A failed recompute flags a feature Invalid WITHOUT raising, so a tool can
"succeed" while the model is broken. These snapshot/compare passes run around
every mutating tool call (see gui_bridge) and append a note to its result.
"""


# A failed recompute flags the object Invalid/Error (the red marks in the tree)
# WITHOUT raising, so a tool can "succeed" while a feature is broken.
_ERROR_FLAGS = ("Invalid", "Error")


def _solver_constraint_indices(values):
    """Normalise the solver's constraint indices to 0-based.

    Verified against FreeCAD 1.1: ConflictingConstraints/RedundantConstraints/
    MalformedConstraints come back 1-BASED (they're what the GUI prints), while
    sk.Constraints, setDatum() and delConstraint() are all 0-based. Reporting the
    raw numbers would point at the constraint NEXT TO the broken one -- so a
    "drop the redundant constraint" fix would silently delete the wrong one.
    """
    out = []
    for value in values or []:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if index >= 1:
            out.append(index - 1)
    return out


# ---- recompute diagnostics -------------------------------------------------
# Names already summarised, so a persistently-broken feature isn't re-announced
# on every subsequent tool call.
_reported_invalid = set()


def _scan_invalid(doc):
    """Objects whose last recompute failed (Invalid/Error in their State)."""
    bad = []
    if doc is not None:
        for obj in doc.Objects:
            state = list(getattr(obj, "State", None) or [])
            if any(flag in state for flag in _ERROR_FLAGS):
                bad.append({"name": obj.Name, "label": obj.Label,
                            "type": obj.TypeId, "state": state})
    return bad


def summarize_new_failures():
    """One-line note about features that NEWLY failed to recompute, or "".

    Called by the bridge on the GUI thread after each tool call. Console warning
    *text* isn't capturable in FreeCAD 1.1, so this reports failed-recompute
    state -- the substance of the red errors -- not raw warning messages.
    """
    import FreeCAD

    global _reported_invalid
    bad = _scan_invalid(FreeCAD.ActiveDocument)
    names = {b["name"] for b in bad}
    new = [b for b in bad if b["name"] not in _reported_invalid]
    _reported_invalid = names  # recovered objects drop out; still-broken stay quiet
    if not new:
        return ""
    labels = ", ".join(b["label"] for b in new)
    return (f"⚠ {len(new)} feature(s) failed to recompute: {labels}. "
            "Call get_diagnostics for details.")


# ---- per-operation feature-change report -----------------------------------
# A PartDesign feature can "succeed" (valid shape, no recompute error) while
# doing the wrong thing, and nothing flags it: a cut that removes no material
# (wrong direction), a feature disconnected from the solid, a no-op dress-up.
# Rather than special-case each, we snapshot every solid feature's volume
# contribution and solid count BEFORE a mutating tool runs and diff AFTER, then
# report -- for every feature the operation created or changed -- how much
# material it added/removed and how the solid count changed (old -> new). Two
# specific traps get an escalated, actionable note on top: an empty subtractive
# cut (with the exact Reversed fix) and a Body that split into >1 disconnected
# solid.

#: Tools that can mutate document geometry -- the only ones worth snapshotting
#: for a before/after volume diff (every other tool is read-only, so its diff is
#: always empty). run_python is the sole document-mutating tool.
MUTATING_TOOLS = {"run_python"}

#: TypeId substrings identifying material-removing PartDesign features. Matched
#: by substring so the whole family (Pocket/Groove/Hole plus every Subtractive*
#: primitive/loft/pipe) is covered without enumerating each concrete TypeId.
_SUBTRACTIVE_MARKERS = ("Pocket", "Groove", "Hole", "Subtractive")

# A feature whose |volume contribution| is under this (mm³) is treated as having
# changed nothing -- a true no-op returns the base shape so the delta is exactly
# 0.0; this only absorbs float dust. Any real feature contributes far more.
_NEGLIGIBLE_VOLUME = 1e-3


def _is_subtractive_feature(obj):
    """True iff `obj` is a PartDesign feature whose job is to remove material."""
    tid = getattr(obj, "TypeId", "") or ""
    return tid.startswith("PartDesign::") and any(m in tid for m in _SUBTRACTIVE_MARKERS)


def _feature_states(doc):
    """{Name: {label, typeid, contribution, new_solids, old_solids}} for every
    PartDesign SOLID feature (Pad/Pocket/Revolution/.../Fillet/pattern -- anything
    derived from PartDesign::Feature, which excludes the Body container, datums
    and sketches).

    ``contribution`` is what the feature itself added (+) or removed (-) from the
    running solid: its Shape.Volume minus its BaseFeature's (0 for the body's
    first feature). ``old_solids``/``new_solids`` are the disconnected-solid count
    before and after it. Best-effort per object; anything unreadable is skipped.
    """
    states = {}
    if doc is None:
        return states
    for obj in doc.Objects:
        try:
            if not obj.isDerivedFrom("PartDesign::Feature"):
                continue
        except Exception:  # noqa: BLE001
            continue
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        try:
            new_solids, vol = len(shape.Solids), shape.Volume
        except Exception:  # noqa: BLE001
            continue
        base_shape = getattr(getattr(obj, "BaseFeature", None), "Shape", None)
        if base_shape is not None and not base_shape.isNull():
            try:
                base_vol, old_solids = base_shape.Volume, len(base_shape.Solids)
            except Exception:  # noqa: BLE001
                base_vol, old_solids = 0.0, 0
        else:
            base_vol, old_solids = 0.0, 0
        states[obj.Name] = {
            "label": obj.Label, "typeid": obj.TypeId,
            "contribution": vol - base_vol,
            "new_solids": new_solids, "old_solids": old_solids,
        }
    return states


def _sketch_states(doc):
    """{Name: {label, bbox, fully_constrained, closed_wires, open_wires, edges}}
    for every Sketcher::SketchObject in `doc`. Best-effort; anything unreadable is
    skipped.

    Lets the reply surface a new or edited sketch's actual extents (bbox, in world
    coords -- placement applied), whether it's fully constrained, and its wire
    closure, so a mirrored/mis-placed or unclosed profile is caught at the sketch
    step -- before it's padded. The bbox is rounded so float dust doesn't read as
    a change in the before/after diff."""
    states = {}
    if doc is None:
        return states
    for obj in doc.Objects:
        try:
            if obj.TypeId != "Sketcher::SketchObject":
                continue
            shape = getattr(obj, "Shape", None)
            has_shape = shape is not None and not shape.isNull()
            bbox = None
            if has_shape and shape.BoundBox.isValid():
                bb = shape.BoundBox
                bbox = tuple(round(v, 3) for v in
                             (bb.XMin, bb.XMax, bb.YMin, bb.YMax, bb.ZMin, bb.ZMax))
            wires = shape.Wires if has_shape else []
            closed = sum(1 for w in wires if w.isClosed())
            states[obj.Name] = {
                "label": obj.Label,
                "bbox": bbox,
                "fully_constrained": bool(getattr(obj, "FullyConstrained", False)),
                "closed_wires": closed,
                "open_wires": len(wires) - closed,
                "edges": len(shape.Edges) if has_shape else 0,
                # Solver state. These are plain attributes, not App properties, so
                # they're easy to miss -- but "under-constrained" alone doesn't say
                # HOW loose (DoF), and a conflicting/redundant constraint set is a
                # real breakage that no recompute error and no volume delta catches.
                # (0-based, like setDatum/delConstraint -- the solver reports them
                # 1-based; see _solver_constraint_indices.)
                "dof": int(getattr(obj, "DoF", -1)),
                "conflicting": _solver_constraint_indices(
                    getattr(obj, "ConflictingConstraints", [])),
                "redundant": _solver_constraint_indices(
                    getattr(obj, "RedundantConstraints", [])),
                "malformed": _solver_constraint_indices(
                    getattr(obj, "MalformedConstraints", [])),
            }
        except Exception:  # noqa: BLE001
            continue
    return states


def feature_snapshot(tool_name):
    """State before a mutating tool runs, for post_tool_notes to diff against
    afterwards -- or None for read-only tools (nothing they do changes geometry).

    Bundles PartDesign solid-feature states and Sketcher sketch states so the
    reply can flag both what the operation added/removed AND what each new or
    edited sketch actually looks like (extents, constraint state, closure)."""
    if tool_name not in MUTATING_TOOLS:
        return None
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    return {"features": _feature_states(doc), "sketches": _sketch_states(doc)}


def _wrong_direction_hint(obj):
    """A concrete 'here is the profile normal and which way to cut' sentence for
    an extrude-based subtractive feature (Pocket/Hole) that removed nothing, or
    None when the geometry to work it out isn't available (caller falls back to
    generic wording).

    A Pocket/Hole cuts OPPOSITE the profile's sketch normal by default. Since the
    feature removed nothing, the solid must sit on the far side of the profile
    plane from where the cut is heading -- so we report the profile normal, which
    side the solid is actually on, and the exact Reversed value that aims the cut
    back into the material. Skipped when a custom cut vector is in play (then the
    sketch normal no longer decides the direction) or when the solid straddles
    the plane (no single side to name)."""
    import FreeCAD

    tid = getattr(obj, "TypeId", "") or ""
    if "Pocket" not in tid and "Hole" not in tid:
        return None  # Groove revolves; Subtractive primitives are placed solids
    if getattr(obj, "UseCustomVector", False):
        return None  # direction comes from a custom vector, not the sketch normal
    try:
        prof = getattr(obj, "Profile", None)
        sketch = prof[0] if isinstance(prof, (tuple, list)) and prof else prof
        placement = getattr(sketch, "Placement", None)
        base_shape = getattr(getattr(obj, "BaseFeature", None), "Shape", None)
        if placement is None or base_shape is None or base_shape.isNull():
            return None
        # The profile plane: a point on it (the sketch origin) and its normal
        # (the sketch's local +Z rotated into world). Any in-plane point works for
        # the signed distance below, so the sketch origin is fine.
        normal = placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
        offset = (base_shape.BoundBox.Center - placement.Base).dot(normal)
        if abs(offset) < 1e-6:
            return None  # solid straddles the plane -- can't call one side
        n = FreeCAD.Vector(round(normal.x, 3), round(normal.y, 3), round(normal.z, 3))
        side = "+" if offset > 0 else "-"
        # Default cut runs along -normal; Reversed aims it along +normal. To cut
        # toward the material, Reversed must be True iff the solid is on +side.
        want_reversed = offset > 0
        return (
            f"Its profile normal is ({n.x:g}, {n.y:g}, {n.z:g}) and the solid is on "
            f"the {side}normal side of the profile, but a Pocket/Hole cuts the "
            f"OPPOSITE way by default -- so the cut is heading into empty space. Set "
            f"Reversed={want_reversed} on {obj.Label} and recompute so it cuts toward "
            "the solid. (Sketching the cut on the solid's own face avoids this: a "
            "face normal points out of the material, so the default cut goes in.)"
        )
    except Exception:  # noqa: BLE001
        return None


def _format_feature_change(obj, st):
    """One report line for a feature the operation created or changed: how much
    material it added/removed and how the solid count moved -- plus an escalated
    note for the two silent traps (an empty subtractive cut, or a split into
    disconnected solids)."""
    contribution = st["contribution"]
    old_solids, new_solids = st["old_solids"], st["new_solids"]
    typ = st["typeid"].split("::")[-1]
    changed_nothing = abs(contribution) <= _NEGLIGIBLE_VOLUME
    if contribution > _NEGLIGIBLE_VOLUME:
        vol_part = f"added {contribution:.1f} mm³"
    elif contribution < -_NEGLIGIBLE_VOLUME:
        vol_part = f"removed {-contribution:.1f} mm³"
    else:
        vol_part = "no volume change"
    line = f"{st['label']} ({typ}): {vol_part} · solids {old_solids}→{new_solids}"

    escalations = []
    if changed_nothing and _is_subtractive_feature(obj):
        hint = _wrong_direction_hint(obj)
        escalations.append(
            hint or (
                "This cut removed nothing -- almost always the wrong direction "
                "(Pocket/Groove/Hole cut OPPOSITE the sketch normal by default). "
                "Toggle Reversed, or sketch the cut on the solid's own face, and "
                "re-check the volume."
            )
        )
    elif changed_nothing:
        escalations.append(
            "This feature changed nothing -- check its inputs/references (e.g. an "
            "empty face/edge selection, or a profile that misses the solid)."
        )
    if new_solids > 1:
        escalations.append(
            f"The Body is now {new_solids} disconnected solids -- a PartDesign Body "
            "must be ONE contiguous lump. Make this feature touch/intersect the "
            "existing solid (or move it to its own Body); a disconnected or split "
            "solid breaks downstream features."
        )
    if escalations:
        return "⚠ " + line + "\n    " + "\n    ".join(escalations)
    return line


def summarize_feature_changes(before):
    """Per-operation report of what each PartDesign feature added/removed and how
    the solid count changed, or "".

    ``before`` is the feature_snapshot() taken before the mutating tool ran; this
    diffs it against the current state and reports every feature the operation
    created or changed (a feature whose contribution volume and solid count are
    both unchanged is skipped). The before/after diff means each note is tied to
    the operation that caused it -- no cross-call bookkeeping, and read-only tools
    (before is None) produce nothing.
    """
    if before is None:
        return ""
    import FreeCAD

    after = _feature_states(FreeCAD.ActiveDocument)
    doc = FreeCAD.ActiveDocument
    prev_states = before.get("features", {}) if isinstance(before, dict) else {}
    lines = []
    for name, st in after.items():
        prev = prev_states.get(name)
        if prev is not None and (
            round(prev["contribution"], 6) == round(st["contribution"], 6)
            and prev["new_solids"] == st["new_solids"]
        ):
            continue  # untouched by this operation
        obj = doc.getObject(name) if doc is not None else None
        lines.append(_format_feature_change(obj, st))
    return "\n".join(lines)


def _format_sketch_change(st):
    """One-line report of a new/edited sketch: extents, constraint state, closure.
    Escalates (⚠) an unclosed profile, which can't pad into a solid."""
    bbox = st["bbox"]
    if bbox:
        span = (f"X {bbox[0]:g}..{bbox[1]:g}, Y {bbox[2]:g}..{bbox[3]:g}, "
                f"Z {bbox[4]:g}..{bbox[5]:g} mm")
    else:
        span = "empty (no geometry)"
    dof = st.get("dof", -1)
    if st["fully_constrained"]:
        constraint = "fully constrained"
    elif dof > 0:
        # The number matters: it's what tells you whether a moveGeometry will take
        # (only underconstrained geometry moves) and how much is still free to drift.
        constraint = f"under-constrained ({dof} DoF)"
    else:
        constraint = "under-constrained"
    n = st["closed_wires"]
    line = (f"{st['label']} (Sketch): {span} · {constraint} · "
            f"{n} closed wire{'' if n == 1 else 's'}")

    problems = []
    if st.get("malformed"):
        problems.append(
            f"malformed constraint(s) at index {st['malformed']} -- the solver cannot "
            "even evaluate them; delete/replace them before doing anything else"
        )
    if st.get("conflicting"):
        problems.append(
            f"CONFLICTING constraint(s) at index {st['conflicting']} -- they contradict "
            "each other, so the solver cannot satisfy the sketch and the geometry you "
            "see is not what the constraints say; remove one side of the conflict"
        )
    if st.get("redundant"):
        problems.append(
            f"redundant constraint(s) at index {st['redundant']} -- harmless to the "
            "shape but they make later edits fail unpredictably; drop them "
            "(delConstraint, or autoRemoveRedundants())"
        )
    if st["open_wires"] > 0:
        problems.append(
            f"{st['open_wires']} open (unclosed) wire(s) -- a Pad/Pocket/Revolution "
            "needs a closed profile; make the endpoints coincident (for a revolve, "
            "close the wire ALONG the axis -- ends merely touching the axis is not "
            "enough) or the feature produces no solid"
        )
    if problems:
        return "⚠ " + line + "".join(f"\n    {p}." for p in problems)
    return line


def summarize_sketch_changes(before):
    """Per-operation report of each sketch the operation created or edited -- its
    world-space extents, whether it's fully constrained, and its wire closure, or
    "".

    Same before/after diff as summarize_feature_changes (an unchanged sketch is
    skipped), so a call that only pads an existing sketch re-reports nothing. Read
    the extents to confirm the profile landed where and how you intended: a
    fully-constrained sketch can still be mirrored or mis-placed and neither the
    volume delta nor a recompute error would catch it."""
    if before is None:
        return ""
    import FreeCAD

    after = _sketch_states(FreeCAD.ActiveDocument)
    prev_states = before.get("sketches", {}) if isinstance(before, dict) else {}
    lines = []
    for name, st in after.items():
        prev = prev_states.get(name)
        if prev is not None and all(
            prev.get(k) == st[k]
            for k in ("bbox", "fully_constrained", "closed_wires", "open_wires", "edges",
                      "dof", "conflicting", "redundant", "malformed")
        ):
            continue  # untouched by this operation
        lines.append(_format_sketch_change(st))
    return "\n".join(lines)


def post_tool_notes(tool_name, before=None):
    """Combined post-call notes to fold into a tool reply: features that newly
    failed to recompute, and -- for a mutating tool -- what each PartDesign
    feature added/removed and how the solid count changed (with the empty-cut and
    disconnected-solid escalations), plus each new/edited sketch's extents,
    constraint state and wire closure.

    ``before`` is feature_snapshot(tool_name), taken by the bridge just before the
    tool ran (None for read-only tools). Skipped entirely for get_diagnostics (it
    reports failures itself and shouldn't carry mutation notes).
    """
    if tool_name == "get_diagnostics":
        return ""
    notes = [summarize_new_failures(),
             summarize_feature_changes(before),
             summarize_sketch_changes(before)]
    return "\n\n".join(n for n in notes if n)


_GET_DIAGNOSTICS_SCHEMA = {
    "name": "get_diagnostics",
    "description": (
        "Details of features that failed their last recompute -- the objects "
        "flagged Invalid/Error (the red marks in the tree). Other tools only "
        "note these in a one-line summary; call this for the full list (each "
        "object's name, label, type and state) so you can fix them. Note: "
        "FreeCAD console warning text is not capturable, so this reports "
        "failed-recompute state, not raw warning messages."
    ),
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}


def _run_get_diagnostics(args):
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return "No active document."
    bad = _scan_invalid(doc)
    if not bad:
        return "No invalid objects -- the document recomputed cleanly."
    lines = [f"{len(bad)} object(s) currently invalid in '{doc.Label}':"]
    for b in bad:
        lines.append(f"- {b['label']} ({b['name']}, {b['type']}) -- state: {', '.join(b['state'])}")
    lines.append(
        "These features failed their last recompute. Inspect their inputs "
        "(profile, constraints, references) and recompute to clear them."
    )
    return "\n".join(lines)
