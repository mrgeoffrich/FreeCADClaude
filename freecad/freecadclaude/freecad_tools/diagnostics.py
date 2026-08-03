# SPDX-License-Identifier: LGPL-2.1-or-later
"""Post-tool notes: what a run_python call actually changed, and what broke.

A failed recompute flags a feature Invalid WITHOUT raising, so a tool can
"succeed" while the model is broken. These snapshot/compare passes run around
every mutating tool call (see gui_bridge) and append a note to its result.

A note is also where a scripting reference gets cited, when the condition it
documents is the one we just detected: a pointer that arrives with the evidence
is read, whereas the same pointer sitting in the system prompt asks Claude to
remember it at a moment it has no reason to notice.
"""

import os

from .session import REFS_DIR, active_session_id

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


def summarize_new_failures(before=None):
    """One-line note about features that NEWLY failed to recompute, or "".

    Called by the bridge on the GUI thread after each tool call. Console warning
    *text* isn't capturable in FreeCAD 1.1, so this reports failed-recompute
    state -- the substance of the red errors -- not raw warning messages.

    ``before`` (feature_snapshot's dict, None for a read-only tool) is used only
    to recognise one thing: a feature that already existed and was fine before
    this call has now gone Invalid. That symptom is worth surfacing on its own --
    nothing else does -- but it does NOT identify a cause on its own; see
    _pre_existing_failure_note for which one it actually is.
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
    note = (f"⚠ {len(new)} feature(s) failed to recompute: {labels}. "
            "Call get_diagnostics for details.")
    if _broke_an_existing_feature(new, before):
        note += _pre_existing_failure_note(new)
    return note


def _broke_an_existing_feature(new, before):
    """True when any newly-Invalid feature already existed before this call.

    A feature the call itself created failing is ordinary (bad parameters, empty
    profile) and the traceback or the volume note covers it. A PREVIOUSLY-FINE
    feature failing is worth escalating, because nothing else surfaces it.
    """
    if not before:
        return False
    existed = (before.get("features") or {}) if isinstance(before, dict) else {}
    return any(b["name"] in existed for b in new)


def _on_basefeature_cycle(obj):
    """True when following BaseFeature from ``obj`` revisits a feature.

    This is the actual DAG cycle, checked rather than inferred. Cheap: the chain
    is a handful of links and it stops the moment it repeats itself.
    """
    seen = set()
    cur = obj
    while cur is not None:
        name = getattr(cur, "Name", None)
        if name is None:
            return False
        if name in seen:
            return True
        seen.add(name)
        try:
            cur = getattr(cur, "BaseFeature", None)
        except Exception:  # noqa: BLE001 - a deleted/odd object is not a cycle
            return False
    return False


def _pinned_subelements(obj):
    """Literal sub-element names a dress-up feature hardcodes in ``Base``.

    ``Base`` is a PropertyLinkSub, read back as ``(feature, ['Edge3', 'Edge7'])``.
    Those names are POSITIONAL, so an earlier feature that changes renumbers them
    -- which is topological naming, the other cause of a previously-fine feature
    going Invalid, and by the logs much the more common one.
    """
    try:
        base = getattr(obj, "Base", None)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(base, (tuple, list)) or len(base) != 2:
        return []
    subs = base[1]
    return [s for s in subs if s] if isinstance(subs, (tuple, list)) else []


def _pre_existing_failure_note(new):
    """Explain a feature that was fine BEFORE this call and is now Invalid.

    Two different causes share this exact symptom, and this note used to assert
    the rarer one outright ("That is the BaseFeature cycle"). Measured over the
    logged sessions: since the trigger landed it fired on two sessions and
    NEITHER was a cycle -- no 'must be a DAG' anywhere in either log -- while in
    one, Claude had to diagnose over the top of it ("its Base pins literal edge
    names Edge4..Edge18 ... Not a BaseFeature [cycle]"). The genuine cycle shows
    up only in the two 12 Jul sessions that produced the reference file.

    So: confirm the cycle before naming it, lead with topological naming when the
    evidence points there instead, and when neither is confirmable, say both are
    possible rather than picking one. A note that names the wrong cause
    confidently is worse than one that names none.
    """
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    lead = ("\n    A feature that was FINE BEFORE this call is now Invalid -- not "
            "just the one you added. ")
    objs = [(b, doc.getObject(b["name"])) for b in new] if doc is not None else []

    cyclic = [b["label"] for b, obj in objs if obj is not None and _on_basefeature_cycle(obj)]
    if cyclic:
        return lead + (
            f"Its BaseFeature chain loops back on itself ({', '.join(cyclic)}) -- "
            "the cycle a scripted newObject wires when a datum sits between the "
            "Tip and its predecessor. Read "
            f"{os.path.join(REFS_DIR, 'partdesign-body-tip-cycle-gotcha.md')} "
            "before trying anything else: the fix is to reassign the older "
            "feature's BaseFeature straight back to its true predecessor, and "
            "reordering Body.Group reproduces the cycle instead of fixing it."
        )

    pinned = []
    for b, obj in objs:
        subs = _pinned_subelements(obj) if obj is not None else []
        if subs:
            shown = ", ".join(subs[:4]) + ("..." if len(subs) > 4 else "")
            pinned.append(f"{b['label']} pins {shown}")
    if pinned:
        return lead + (
            f"No BaseFeature cycle -- this is topological naming: {'; '.join(pinned)}. "
            "Those sub-element names are positional, so an earlier feature that "
            "changed renumbered them and the dress-up is now on the wrong edges "
            "or none. Re-derive the edges from the base feature's Shape "
            "(by position, length or direction) instead of guessing new indices."
        )

    return lead + (
        "Call get_diagnostics for which feature and why. Two causes produce this "
        "same symptom: topological naming (a Fillet/Chamfer/Thickness pinning "
        "literal EdgeNN names that an earlier change renumbered -- much the more "
        "common) and a BaseFeature cycle from a scripted newObject, covered in "
        f"{os.path.join(REFS_DIR, 'partdesign-body-tip-cycle-gotcha.md')}."
    )


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


#: Session id this process last cited the PartDesign reference for, so the pointer
#: lands once per conversation instead of on every feature. Keyed on the id (not a
#: bool) so "New" in the chat panel re-arms it -- same pattern as the sketch rules
#: in tools_sketch.
_pd_ref_shown_for = {"session": None}


#: The PartDesign facts worth spending a tool result on, INLINE. This note used
#: to cite partdesign-scripting.md and stop there, and that was measured: over
#: the 13 logged sessions since it landed the pointer fired 9 times and was
#: followed by a Read once, with 171 run_python calls running past it unopened.
#: Arriving on a detected condition fixed *when* the pointer shows up, not
#: whether the file gets opened -- so carry the payload the way _EDITING_RULES
#: does in tools_sketch and keep the file for the long tail. Contents are the
#: items that (a) match the errors actually logged (KeyError 'XY_Plane', a
#: feature object passed where newObject wants a type string, Tip read as None,
#: 'Transformed'/'SubElementNames' guessed on a pattern, "Body: object is not
#: allowed") and (b) fail confusingly rather than obviously.
_PARTDESIGN_ESSENTIALS = (
    "This document now has a PartDesign Body. The essentials, so the usual "
    "guesses don't cost a round-trip:\n"
    "- body.newObject('<Type>', '<Name>') both creates the feature AND inserts "
    "it. The first argument is the type STRING ('PartDesign::Pad'), not a "
    "feature object.\n"
    "- Tip auto-advances, but only for SOLID features -- sketches and datums "
    "never move it, so a Sketch -> Pad -> Sketch -> Pocket flow needs no Tip "
    "handling at all. body.Tip is None until the first solid feature exists.\n"
    "- Origin planes: doc.getObject('XY_Plane') resolves only for the FIRST "
    "Body in the document; a second Body's planes get suffixed names. Look up "
    "by Role instead: next(o for o in body.Origin.Group if o.Role == "
    "'XY_Plane'). body.Origin.OriginFeatures is gone in 1.1.\n"
    "- Pad/Pocket: Profile, Length, and Type -- Pad 'Length'|'UpToLast'|"
    "'UpToFirst'|'UpToFace'|'UpToShape', Pocket the same but 'ThroughAll' in "
    "place of 'UpToLast'. For symmetry use SideType ('One side'|'Two sides'|"
    "'Symmetric'), not the deprecated Midplane.\n"
    "- Fillet/Chamfer/Thickness take Base as ONE LinkSub tuple -- "
    "(feature, ['Edge3', 'Edge7']) -- plus Radius / Size+Angle / Value. Those "
    "literal edge names are positional and renumber when an earlier feature "
    "changes, so derive them from the shape rather than hardcoding indices.\n"
    "- Patterns (LinearPattern/PolarPattern/Mirrored) repeat Originals, a link "
    "LIST. PartDesign::Boolean takes its tool Bodies in Group.\n"
    "- Recompute between dependent steps: a feature built against a "
    "not-yet-recomputed Profile fails or builds on stale topology.\n"
    "- Revolution/Groove, Loft/Pipe, Hole's ~25 properties, datum attachment "
    "and MultiTransform: "
    f"{os.path.join(REFS_DIR, 'partdesign-scripting.md')}"
)


def _partdesign_reference_note(before):
    """Hand over the PartDesign essentials the first time a conversation touches a Body.

    Measured over the logged sessions, this is where the guessing actually costs
    something: run_python calls that create a PartDesign feature fail at 17%
    against a 9% baseline across all calls. The trigger is "a Body now exists"
    rather than "a feature was created" because a Body is usually made in its own
    call, which puts this in front of the FIRST feature; and when Body and
    first feature arrive together it still front-runs the rest, since 11 of the 14
    sessions that created one feature went on to create more.

    Deliberately no equivalent for part-draft-recipes.md: Part-primitive/Draft
    calls errored at 1/37 (below baseline) and in 7 of 10 sessions nothing
    followed the first one, so a note there would be noise.
    """
    if before is None:  # read-only tool
        return ""
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return ""
    if not any((getattr(o, "TypeId", "") or "") == "PartDesign::Body" for o in doc.Objects):
        return ""
    current = active_session_id()
    if _pd_ref_shown_for["session"] == current:
        return ""
    _pd_ref_shown_for["session"] = current
    return _PARTDESIGN_ESSENTIALS


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
    notes = [summarize_new_failures(before),
             summarize_feature_changes(before),
             summarize_sketch_changes(before),
             _partdesign_reference_note(before)]
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
