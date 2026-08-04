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


# ---- the Body's feature chain ----------------------------------------------
# A PartDesign Body's shape is the run of solid features linked tip->base by
# BaseFeature. A feature can sit in the Body, recompute cleanly, report a
# perfectly good shape of its own -- and contribute NOTHING, because the chain no
# longer runs through it. Nothing else surfaces that: it has no error state, and
# the volume diff only looks at features whose own contribution moved.
#
# ShapeBinder/SubShapeBinder derive from Part::Feature, not PartDesign::Feature
# (FreeCAD 1.1, ShapeBinder.h:46,89), so the filter below already excludes the
# reference geometry that legitimately lives in a Body outside the chain.
# Measured over 58 bodies in real documents: zero features off-chain for any
# reason other than a deliberately moved-back Tip, which is classified apart.


def _is_solid_feature(obj):
    """True for the features that belong in a Body's tip chain."""
    try:
        return bool(obj.isDerivedFrom("PartDesign::Feature"))
    except Exception:  # noqa: BLE001
        return False


def _walk_base_chain(obj, stop=None):
    """``(names, cycle)`` walking BaseFeature from ``obj`` inclusive, tip-first.

    Stops early -- with the name included -- on ``stop``, which is how a feature
    is tested for sitting downstream of the Tip. ``cycle`` is True when the walk
    revisits a feature: the actual DAG cycle, checked rather than inferred.
    """
    names, seen, cur = [], set(), obj
    while cur is not None:
        name = getattr(cur, "Name", None)
        if name is None:
            break
        if name in seen:
            return names, True
        seen.add(name)
        names.append(name)
        if stop is not None and name == stop:
            break
        try:
            cur = getattr(cur, "BaseFeature", None)
        except Exception:  # noqa: BLE001 - a deleted/odd object is not a cycle
            break
    return names, False


def _body_states(doc):
    """{Name: {label, tip, chain, orphans, after_tip, cycle}} for every Body.

    ``chain`` is the solid features that actually reach the Tip, base-first --
    the order they build in. ``orphans`` are solid features in the Body the chain
    never reaches AND that aren't downstream of the Tip either: a genuine side
    branch, contributing nothing. ``after_tip`` are the ones that ARE downstream
    of the Tip -- the ordinary "Tip moved back to edit an earlier feature" state,
    kept apart so it reads as context rather than as breakage.
    """
    states = {}
    if doc is None:
        return states
    for body in doc.Objects:
        try:
            if (getattr(body, "TypeId", "") or "") != "PartDesign::Body":
                continue
            tip_name = getattr(getattr(body, "Tip", None), "Name", None)
            chain, cycle = _walk_base_chain(getattr(body, "Tip", None))
            chain.reverse()  # base -> tip: the order they build in
            in_chain = set(chain)
            orphans, after_tip = [], []
            for member in getattr(body, "Group", None) or []:
                name = getattr(member, "Name", None)
                if name is None or name in in_chain or not _is_solid_feature(member):
                    continue
                reached, member_cycle = _walk_base_chain(member, stop=tip_name)
                cycle = cycle or member_cycle
                downstream = bool(tip_name) and tip_name in reached
                (after_tip if downstream else orphans).append(name)
            states[body.Name] = {
                "label": body.Label, "tip": tip_name, "chain": chain,
                "orphans": orphans, "after_tip": after_tip, "cycle": cycle,
            }
        except Exception:  # noqa: BLE001
            continue
    return states


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
    return _walk_base_chain(obj)[1]


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


#: Measured shape facts, keyed by (object name, OCCT shape hash). Sound to cache
#: because they are a pure function of the shape: a rebuilt shape gets a new hash
#: and therefore misses. Keyed on the name as well as the hash so a hash
#: collision can't hand one object another's numbers.
#:
#: This is what makes the richer snapshot cheaper than the old thin one. Measured
#: on a 10-feature chain: hashing the whole chain is 0.03 ms, against 99 ms to
#: read Volume/Solids and 51 ms to check validity. A typical call rebuilds 1-3
#: features of 30, so everything else is a cache hit -- and note the ordering
#: that follows, Volume being TWICE the cost of isValid(): validity was never the
#: expensive part, we were just already paying more for less.
_shape_metrics_cache = {}
_METRICS_CACHE_CAP = 512


def _shape_metrics(name, shape):
    """Volume/topology counts/bbox/validity for ``shape``, cached by its hash."""
    key = None
    try:
        key = (name, shape.hashCode())
        hit = _shape_metrics_cache.get(key)
        if hit is not None:
            return hit
    except Exception:  # noqa: BLE001 - no usable hash: just measure it
        key = None
    try:
        bb = shape.BoundBox
        metrics = {
            "volume": shape.Volume,
            "solids": len(shape.Solids),
            "faces": len(shape.Faces),
            "edges": len(shape.Edges),
            "vertexes": len(shape.Vertexes),
            "bbox": tuple(round(v, 4) for v in (bb.XMin, bb.XMax, bb.YMin,
                                                bb.YMax, bb.ZMin, bb.ZMax)),
            "valid": bool(shape.isValid()),
        }
    except Exception:  # noqa: BLE001
        return None
    if key is not None:
        if len(_shape_metrics_cache) >= _METRICS_CACHE_CAP:
            _shape_metrics_cache.clear()  # bounded; a cold cache only costs a re-measure
        _shape_metrics_cache[key] = metrics
    return metrics


def _feature_states(doc):
    """{Name: {label, typeid, contribution, old_solids, new_solids, faces, edges,
    vertexes, bbox, valid}} for every PartDesign SOLID feature (Pad/Pocket/
    Revolution/.../Fillet/pattern -- anything derived from PartDesign::Feature,
    which excludes the Body container, datums and sketches).

    ``contribution`` is what the feature itself added (+) or removed (-) from the
    running solid: its Shape.Volume minus its BaseFeature's (0 for the body's
    first feature). ``old_solids``/``new_solids`` are the disconnected-solid count
    before and after it.

    The rest is the shape FINGERPRINT, and it exists because volume alone is a
    lossy way to ask "did this change": a feature that was re-topologised, moved,
    or left invalid at constant volume was invisible to the old diff. Topology
    counts and the bbox close that; ``valid`` closes the case where the shape is
    geometrically broken while every other number looks right. Best-effort per
    object; anything unreadable is skipped.
    """
    states = {}
    if doc is None:
        return states
    measured = {}
    for obj in doc.Objects:
        try:
            if not obj.isDerivedFrom("PartDesign::Feature"):
                continue
        except Exception:  # noqa: BLE001
            continue
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        metrics = _shape_metrics(obj.Name, shape)
        if metrics is not None:
            measured[obj.Name] = (obj, metrics)
    for name, (obj, metrics) in measured.items():
        base = getattr(obj, "BaseFeature", None)
        base_name = getattr(base, "Name", None)
        base_metrics = measured[base_name][1] if base_name in measured else None
        if base_metrics is None and base is not None:
            base_shape = getattr(base, "Shape", None)
            if base_shape is not None and not base_shape.isNull():
                base_metrics = _shape_metrics(base_name or "", base_shape)
        states[name] = {
            "label": obj.Label, "typeid": obj.TypeId,
            "contribution": metrics["volume"] - (base_metrics["volume"]
                                                 if base_metrics else 0.0),
            "old_solids": base_metrics["solids"] if base_metrics else 0,
            "new_solids": metrics["solids"],
            "faces": metrics["faces"], "edges": metrics["edges"],
            "vertexes": metrics["vertexes"], "bbox": metrics["bbox"],
            "valid": metrics["valid"],
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

    Bundles PartDesign solid-feature states, Sketcher sketch states and each
    Body's tip chain, so the reply can flag what the operation added/removed,
    what each new or edited sketch looks like (extents, constraint state,
    closure), AND whether it changed which features reach the Tip."""
    if tool_name not in MUTATING_TOOLS:
        return None
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    return {"features": _feature_states(doc), "sketches": _sketch_states(doc),
            "bodies": _body_states(doc)}


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
    if not st.get("valid", True):
        # The detail is in summarize_validity_changes; this just stops the
        # per-feature line reading as a clean result on its own.
        line += " · shape INVALID"

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
        if prev is not None and _same_fingerprint(prev, st):
            continue  # untouched by this operation
        obj = doc.getObject(name) if doc is not None else None
        lines.append(_format_feature_change(obj, st))
    return "\n".join(lines)


#: Fingerprint fields compared exactly. ``contribution`` is compared separately,
#: rounded, because it's a float. A feature is "touched by this operation" iff one
#: of these moved -- which is why a re-topologised or moved feature at constant
#: volume is now reported, where the old volume-only test skipped it silently.
_FINGERPRINT_FIELDS = ("new_solids", "faces", "edges", "vertexes", "bbox", "valid")


def _same_fingerprint(a, b):
    """True when two feature states describe the same shape."""
    if round(a.get("contribution", 0.0), 6) != round(b.get("contribution", 0.0), 6):
        return False
    return all(a.get(k) == b.get(k) for k in _FINGERPRINT_FIELDS)


#: What silently moves a dress-up up the chain. Verified in FreeCAD 1.1:
#: DressUp::onChanged (FeatureDressUp.cpp:276) reassigns BaseFeature to whatever
#: Base was just pointed at. So `fillet.Base = (other_feature, [...])` is not the
#: edge-list edit it reads as -- it re-parents the feature. Carried INLINE rather
#: than cited, per the measured read rate on reference files: a note that has to
#: be followed to be useful mostly isn't.
_DRESSUP_BASE_RULE = (
    "Most common cause: assigning a dress-up's Base (Fillet/Chamfer/Thickness) a "
    "DIFFERENT feature. FreeCAD reassigns BaseFeature to match, so that moves the "
    "dress-up up the chain and drops everything that was in between. If you were "
    "re-deriving stale EdgeNN names, keep Base on the SAME feature it already "
    "has: recompute that feature first, THEN read the edge names off its Shape "
    "(reading them off a not-yet-recomputed shape is what makes the fix look like "
    "it failed)."
)


def _label_of(doc, name):
    """A feature's Label for a note, falling back to its internal name."""
    obj = doc.getObject(name) if doc is not None else None
    return getattr(obj, "Label", None) or name


def _chain_labels(doc, names):
    """Chain rendered for a note: labels where resolvable, arrow-joined."""
    if not names:
        return "(empty)"
    return " → ".join(_label_of(doc, n) for n in names)


def summarize_chain_changes(before):
    """Note when this call changed WHICH features reach a Body's Tip, or "".

    The one structural break nothing else catches. A dropped feature keeps a
    valid shape and recomputes without error -- it just stops contributing, so
    neither the Invalid scan nor the volume diff says a word. Measured on the
    session that prompted this: two features left the chain on call 3 of 40 and
    the damage wasn't noticed until call 30.

    Reports only features that LEFT (a feature joining is every ordinary
    creation) and that still exist (one the call deleted is not a break). The
    pre-call chain is echoed back because that is the thing most expensive to
    recover later: once the break is noticed, the only chain still in context is
    the broken one, and a repair then rebuilds toward a guess.

    Two other ways a chain shrinks are NOT breakage and must not read as it: the
    Tip deliberately moved back to edit an earlier feature (those features are
    still linked, just past the Tip -- reported as a plain line), and the Tip
    left dangling by a scripted delete, which needs its own fix and not this
    one's. Both were false alarms until the pristine-document tests caught them.
    """
    if before is None:
        return ""
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    after = _body_states(doc)
    prev_states = before.get("bodies", {}) if isinstance(before, dict) else {}
    notes = []
    for name, st in after.items():
        prev = prev_states.get(name)
        if prev is None:
            continue  # Body created by this call: nothing to compare against
        now, past_tip = set(st["chain"]), set(st["after_tip"])
        left = [n for n in prev["chain"]
                if n not in now and doc is not None and doc.getObject(n) is not None]
        dropped = [n for n in left if n not in past_tip]
        moved_back = [n for n in left if n in past_tip]
        new_cycle = st["cycle"] and not prev["cycle"]
        lost_tip = bool(prev["tip"]) and not st["tip"]

        lines = []
        if lost_tip:
            lines.append(
                f"⚠ {st['label']} no longer has a Tip, so the Body builds NO shape "
                "and every feature in it is inert. Deleting the tip feature with "
                "doc.removeObject does not move the Tip back -- set body.Tip to "
                "whichever feature should now be last."
            )
        elif new_cycle:
            lines.append(
                f"⚠ {st['label']}'s BaseFeature chain now loops back on itself. "
                "Reassign the older feature's BaseFeature straight back to its true "
                "predecessor -- reordering Body.Group reproduces the cycle instead of "
                f"fixing it. Full writeup: "
                f"{os.path.join(REFS_DIR, 'partdesign-body-tip-cycle-gotcha.md')}."
            )
        elif dropped:
            lines.append(
                f"⚠ {len(dropped)} feature(s) dropped out of {st['label']}'s tip "
                f"chain: {', '.join(_label_of(doc, n) for n in dropped)}. They still "
                "exist and "
                "still recompute cleanly on their own branch -- they just no longer "
                "contribute to the Body's shape, and nothing else will flag that."
            )
        elif moved_back:
            # Deliberate: Tip stepped back to edit an earlier feature. Worth one
            # plain line (a scripted Tip move is easy to leave behind), not a ⚠.
            lines.append(
                f"{st['label']}'s Tip moved back to {_label_of(doc, st['tip'])}: "
                f"{', '.join(_label_of(doc, n) for n in moved_back)} now sit after the "
                "Tip and "
                "stop contributing to its shape until the Tip moves forward again."
            )
        else:
            continue

        lines.append(f"Chain before: {_chain_labels(doc, prev['chain'])}")
        lines.append(f"Chain now:    {_chain_labels(doc, st['chain'])}")
        if dropped and not lost_tip and not new_cycle:
            lines.append(_DRESSUP_BASE_RULE)
            lines.append(
                "To repair: reassign BaseFeature along the 'before' order above, then "
                "re-derive each dress-up's edges bottom-up, recomputing each base "
                "before reading its Shape."
            )
        notes.append("\n    ".join(lines))
    return "\n\n".join(notes)


#: What usually produces an invalid shape, by the feature type that produced it.
#: Substring-matched on TypeId so each whole family is covered at once, and
#: ordered most-specific first (Loft/Pipe before the generic additive/subtractive
#: entries). Carried inline for the same measured reason as the rest.
_INVALID_CAUSE_HINTS = (
    ("Loft", "loft sections that cross, twist, or pair up mismatched vertices. "
             "Check the sections are ordered along the sweep, wound the same way "
             "and built in the same vertex order, and that consecutive ones "
             "aren't so close (or so differently shaped) that the interpolated "
             "surface doubles back. Ruled=True removes that interpolation "
             "entirely and is the quickest way to tell if it's the cause."),
    ("Pipe", "a sweep path with a corner tighter than the profile can turn "
             "through without the surface folding back on itself."),
    ("Fillet", "a radius larger than the local curvature or than an adjacent "
               "face is wide, so the rolling ball cuts past the far side. Shrink "
               "it, or drop the tight edges from the selection."),
    ("Chamfer", "a size larger than an adjacent face is wide."),
    ("Thickness", "a wall thicker than the smallest internal radius."),
    ("Draft", "a draft angle steep enough to invert a face."),
)

#: How much of the localisation to spell out. Enough to point at the region
#: without pasting a face dump into the reply.
_INVALID_SUBSHAPE_LIMIT = 3


def _invalid_cause_hint(typeid):
    """The likely-cause sentence for the feature type, or "" if we have none."""
    for marker, hint in _INVALID_CAUSE_HINTS:
        if marker in (typeid or ""):
            return "Most likely cause for a %s: %s" % (typeid.split("::")[-1], hint)
    return ""


def _box(bb):
    """A BoundBox as a plain 6-tuple, so boxes can be unioned and compared."""
    return (bb.XMin, bb.XMax, bb.YMin, bb.YMax, bb.ZMin, bb.ZMax)


def _union_box(a, b):
    if a is None:
        return b
    return (min(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]),
            max(a[3], b[3]), min(a[4], b[4]), max(a[5], b[5]))


def _boxes_overlap(a, b, tol=1e-6):
    return (a[0] <= b[1] + tol and b[0] <= a[1] + tol
            and a[2] <= b[3] + tol and b[2] <= a[3] + tol
            and a[4] <= b[5] + tol and b[4] <= a[5] + tol)


def _operation_region(obj):
    """World-space box of what this feature actually operates on, or None.

    Exists to stop the cause hint asserting something the geometry contradicts.
    The replay that this was built against named a Fillet working on the plate at
    Z 48+ while every bad face sat at Z 0..14 in the lofted foot -- so "your
    radius is too big" would have been confidently wrong, which this repo has
    already learned once (see _pre_existing_failure_note) costs a turn to argue
    with. A dress-up's region is its pinned sub-elements; an additive/subtractive
    feature's is its tool shape.
    """
    try:
        base = getattr(obj, "Base", None)
        if isinstance(base, (tuple, list)) and len(base) == 2 and base[0] is not None:
            shape = getattr(base[0], "Shape", None)
            if shape is not None and not shape.isNull():
                edges, box = shape.Edges, None
                for sub in base[1] or []:
                    name = str(sub).lstrip("?")
                    if not name.startswith("Edge"):
                        continue
                    try:
                        index = int(name[4:]) - 1
                    except ValueError:
                        continue
                    if 0 <= index < len(edges):
                        box = _union_box(box, _box(edges[index].BoundBox))
                if box is not None:
                    return box
        add = getattr(obj, "AddSubShape", None)
        if add is not None and not add.isNull():
            return _box(add.BoundBox)
    except Exception:  # noqa: BLE001
        return None
    return None


def _invalid_subshapes(shape):
    """``(text, bad_box)`` -- where the invalidity actually sits, in world coords.

    A bare False is unactionable, so this names the faces/edges that fail their
    own check and roughly where they are. It only ever runs on an already-invalid
    shape (rare), so the per-sub-shape checks stay off the hot path -- which is
    also why this deliberately does NOT call Shape.check(): the BOP check can run
    for seconds on a large solid, and this executes on the GUI thread.

    A self-intersection can leave every individual face and edge valid while the
    shell is broken, so "nothing individually bad" is a real answer and is
    reported as one rather than as an empty result.
    """
    try:
        faces, edges = shape.Faces, shape.Edges
        bad_faces = [i for i, f in enumerate(faces, 1) if not f.isValid()]
        bad_edges = [i for i, e in enumerate(edges, 1) if not e.isValid()]
    except Exception:  # noqa: BLE001
        return "", None
    if not bad_faces and not bad_edges:
        return ("No single face or edge is individually bad, so the fault is at "
                "the shell/solid level -- typically surfaces that intersect each "
                "other."), None
    parts, box = [], None
    if bad_faces:
        shown = []
        for i in bad_faces:
            bb = _box(faces[i - 1].BoundBox)
            box = _union_box(box, bb)
            if len(shown) < _INVALID_SUBSHAPE_LIMIT:
                shown.append("Face%d (X %.1f..%.1f, Y %.1f..%.1f, Z %.1f..%.1f)"
                             % (i, bb[0], bb[1], bb[2], bb[3], bb[4], bb[5]))
        more = ("" if len(bad_faces) <= _INVALID_SUBSHAPE_LIMIT
                else " (+%d more)" % (len(bad_faces) - _INVALID_SUBSHAPE_LIMIT))
        parts.append("%d of %d faces fail their own check: %s%s"
                     % (len(bad_faces), len(faces), "; ".join(shown), more))
    for i in bad_edges:
        box = _union_box(box, _box(edges[i - 1].BoundBox))
    if bad_edges:
        parts.append("%d edge(s) too (Edge%s)"
                     % (len(bad_edges),
                        ", Edge".join(str(i) for i in bad_edges[:6])))
    return "Where: " + "; ".join(parts) + ".", box


def summarize_validity_changes(before):
    """Note the feature whose shape stopped being geometrically valid, or "".

    The silent failure the volume diff was blind to by construction: an invalid
    solid recomputes without error, keeps its solid count, and reports a
    plausible volume, so every existing gate passes it. Found on the eval run
    that followed the tip-chain work -- the agent signed off "document recomputes
    clean, one solid" while the tip shape failed OCCT validation.

    Reports the FIRST such feature in each Body's tip chain, not the set:
    invalidity introduced upstream is inherited by everything after it, so a list
    would name four innocent features alongside the guilty one. Rare enough to be
    worth saying: 1 of 57 bodies across real documents has an invalid tip shape.
    """
    if before is None:
        return ""
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    after = _feature_states(doc)
    prev = before.get("features", {}) if isinstance(before, dict) else {}
    # A feature this call CREATED has no prior state; an invalid one is still
    # worth reporting, so absence counts as "was fine".
    newly = {n for n, st in after.items()
             if not st["valid"] and prev.get(n, {}).get("valid", True)}
    if not newly:
        return ""

    notes, claimed = [], set()
    for bst in _body_states(doc).values():
        hits = [n for n in bst["chain"] if n in newly]
        if not hits:
            continue
        claimed.update(hits)
        notes.append(_format_invalid_feature(doc, hits[0], after[hits[0]], hits[1:]))
    for name in (n for n in newly if n not in claimed):
        notes.append(_format_invalid_feature(doc, name, after[name], []))
    return "\n\n".join(n for n in notes if n)


def _format_invalid_feature(doc, name, st, inherited):
    """The note for one feature that produced an invalid shape."""
    obj = doc.getObject(name) if doc is not None else None
    shape = getattr(obj, "Shape", None)
    lines = [
        "⚠ %s (%s) produced an INVALID shape. It recomputes without error, keeps "
        "its solid count and reports a plausible volume, so nothing else flags "
        "this -- but an invalid solid slices and prints unpredictably, and later "
        "booleans on it tend to fail in confusing ways."
        % (st["label"], st["typeid"].split("::")[-1]),
    ]
    usable = shape is not None and not shape.isNull()
    where, bad_box = _invalid_subshapes(shape) if usable else ("", None)
    if where:
        lines.append(where)
    if inherited:
        lines.append(
            "%d downstream feature(s) inherit it (%s) -- fix this one and they "
            "clear; they are not separately broken."
            % (len(inherited), ", ".join(_label_of(doc, n) for n in inherited)))

    # Only name a cause when the bad geometry is actually in the region this
    # feature touched. Otherwise say so: a wrong cause costs a turn to argue with.
    region = _operation_region(obj) if obj is not None else None
    if bad_box is not None and region is not None and not _boxes_overlap(bad_box, region):
        lines.append(
            "The bad geometry is NOT where this feature operates (it works on "
            "X %.1f..%.1f, Y %.1f..%.1f, Z %.1f..%.1f), so its own parameters are "
            "probably not the cause -- more likely it re-tessellated marginal "
            "geometry it inherited. Find the upstream feature that owns the bad "
            "region and check that one first." % region)
    else:
        hint = _invalid_cause_hint(st["typeid"])
        if hint:
            lines.append(hint)
    lines.append("Re-check with shape.isValid() after the fix -- a clean "
                 "recompute is not the same thing.")
    return "\n    ".join(lines)


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
    "changes, so derive them from the shape rather than hardcoding indices -- "
    "but from the dress-up's OWN base feature, recomputed first. Pointing Base "
    "at a DIFFERENT feature also reassigns BaseFeature to it, which moves the "
    "dress-up up the chain and silently drops every feature in between out of "
    "the Body's shape.\n"
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
             summarize_chain_changes(before),
             summarize_validity_changes(before),
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
