# SPDX-License-Identifier: LGPL-2.1-or-later
"""Read-only document probes: get_objects (what exists) and get_selection
(what the user is pointing at)."""

from .diagnostics import _body_states, _ERROR_FLAGS
from .geometry import _bbox_dict, _document_bbox
from .gui_state import _active_edit_summary

_GET_OBJECTS_SCHEMA = {
    "name": "get_objects",
    "description": (
        "Inspect the active FreeCAD document: returns its name, its overall "
        "bounding box, and a list of every object with its internal name, "
        "label, type, position, key dimensions, bounding box, and visibility "
        "(as JSON). Call this before modifying or referring to existing "
        "geometry so you know what's there -- the bounding boxes are also the "
        "quickest way to find x_min/x_max/y_min/y_max/z_min/z_max values for "
        "capture_view/view_sketch_svg's crop params. For a PartDesign document "
        "it also returns each Body's 'chain': the features that actually build "
        "its shape, in order. Read that before editing an existing Body -- it "
        "is the only place the build order is visible, and a feature missing "
        "from it contributes nothing however healthy it looks."
    ),
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}

#: Spelled out only when a Body actually has an off-chain feature, which measured
#: across 58 real bodies is never unless something went wrong -- so the common
#: case costs nothing and the broken one arrives explained.
_OFF_CHAIN_NOTE = (
    "These solid features sit in the Body but nothing between them and the Tip "
    "references them, so they contribute NOTHING to its shape -- they still "
    "recompute cleanly on their own branch, which is why no error mentions them. "
    "Either re-link them into the chain via BaseFeature, or delete them."
)

#: With no Tip there is no chain at all, so every feature lands off it -- a
#: different fault from a side branch, and _OFF_CHAIN_NOTE's advice (re-link via
#: BaseFeature) would be the wrong repair.
_NO_TIP_NOTE = (
    "This Body has no Tip, so it builds NO shape and every feature below is "
    "inert. Set body.Tip to whichever feature should be last -- deleting the tip "
    "feature with doc.removeObject does not move the Tip back on its own."
)


def _body_chains(doc):
    """Per-Body build order for get_objects, plus anything that fell off it."""
    bodies = []
    for name, st in _body_states(doc).items():
        entry = {"name": name, "label": st["label"], "tip": st["tip"],
                 "chain": st["chain"]}
        if st["orphans"]:
            entry["not_in_chain"] = st["orphans"]
            entry["not_in_chain_note"] = _NO_TIP_NOTE if not st["tip"] else _OFF_CHAIN_NOTE
        if st["after_tip"]:
            # Ordinary "Tip moved back to edit an earlier feature", not breakage.
            entry["after_tip"] = st["after_tip"]
        if st["cycle"]:
            entry["basefeature_cycle"] = True
        bodies.append(entry)
    return bodies

# Properties worth reporting when present (most are FreeCAD Quantities).
_REPORTED_PROPS = ("Length", "Width", "Height", "Radius", "Radius1", "Radius2", "Angle")


def _run_get_objects(args):
    import json

    import FreeCAD

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return json.dumps({"document": None, "object_count": 0, "objects": []})

    objects = []
    for obj in doc.Objects:
        info = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}

        placement = getattr(obj, "Placement", None)
        if placement is not None:
            base = placement.Base
            info["position"] = [round(base.x, 3), round(base.y, 3), round(base.z, 3)]

        dims = {}
        for prop in _REPORTED_PROPS:
            if hasattr(obj, prop):
                value = getattr(obj, prop)
                dims[prop] = getattr(value, "Value", value)  # Quantity -> float
        if dims:
            info["dimensions"] = dims

        shape = getattr(obj, "Shape", None)
        if shape is not None and not shape.isNull():
            info["bounding_box"] = _bbox_dict(shape.BoundBox)

        view = getattr(obj, "ViewObject", None)
        if view is not None:
            try:
                info["visible"] = bool(view.Visibility)
            except Exception:  # noqa: BLE001
                pass

        if any(flag in (getattr(obj, "State", None) or []) for flag in _ERROR_FLAGS):
            info["invalid"] = True  # last recompute failed

        objects.append(info)

    result = {"document": doc.Label, "object_count": len(objects), "objects": objects}
    bodies = _body_chains(doc)
    if bodies:
        result["bodies"] = bodies
    scene_bbox = _document_bbox(doc)
    if scene_bbox.XMin <= scene_bbox.XMax:
        result["bounding_box"] = _bbox_dict(scene_bbox)
    return json.dumps(result, indent=2)


_GET_SELECTION_SCHEMA = {
    "name": "get_selection",
    "description": (
        "The user's current GUI context as JSON -- what they are pointing at. Two "
        "parts: 'editing' = the object they have OPEN IN AN EDITOR right now, by "
        "name (e.g. the sketch open in the Sketcher editor), or null if they are "
        "not editing anything; and 'selection' = what they have SELECTED (objects "
        "plus sub-elements like Edge3/Face2/Vertex1). Use it to act on what the "
        "user clicked ('fillet this edge') or on whatever they are currently "
        "inside ('add a circle here' while a sketch is open). When 'editing' names "
        "a sketch, that is the sketch the user means -- pass its name to get_sketch "
        "rather than guessing from the document. Read-only and cheap."
    ),
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}


def _run_get_selection(args):
    import json

    # Edit state first: it's the headline, and it's the one signal that survives
    # the user having clicked nothing at all.
    out = {"editing": _active_edit_summary()}

    try:
        import FreeCADGui

        selection = FreeCADGui.Selection.getSelectionEx()
    except Exception as exc:  # noqa: BLE001
        out.update({"error": repr(exc), "selection_count": 0, "selection": []})
        return json.dumps(out, indent=2)

    picked = []
    for sel in selection:
        obj = sel.Object
        picked.append({
            "name": obj.Name,
            "label": obj.Label,
            "type": obj.TypeId,
            "subelements": list(sel.SubElementNames),
        })
    out["selection_count"] = len(picked)
    out["selection"] = picked
    return json.dumps(out, indent=2)
