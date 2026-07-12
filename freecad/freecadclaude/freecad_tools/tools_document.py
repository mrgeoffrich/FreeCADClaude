# SPDX-License-Identifier: LGPL-2.1-or-later
"""Read-only document probes: get_objects (what exists) and get_selection
(what the user is pointing at)."""

from .diagnostics import _ERROR_FLAGS
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
        "capture_view/view_sketch_svg's crop params."
    ),
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}

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
        "rather than guessing from the document. Read-only -- no approval needed."
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
