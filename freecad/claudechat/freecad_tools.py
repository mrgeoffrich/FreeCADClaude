# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD tools exposed to Claude, plus their execution functions.

Each ``run`` function executes ON THE GUI MAIN THREAD (the bridge marshals it
there) and returns a human-readable result string. FreeCAD imports happen
inside the functions so this module stays importable from any thread for its
schema data alone.
"""

#: MCP tool schema for create_box (name/description/inputSchema).
_CREATE_BOX_SCHEMA = {
    "name": "create_box",
    "description": (
        "Create a rectangular box (Part::Box) in the active FreeCAD document. "
        "Creates a new document if none is open. Dimensions are in millimetres."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "length": {"type": "number", "description": "Length along X in mm"},
            "width": {"type": "number", "description": "Width along Y in mm"},
            "height": {"type": "number", "description": "Height along Z in mm"},
        },
        "required": ["length", "width", "height"],
    },
}


def _run_create_box(args):
    import FreeCAD
    import Part  # noqa: F401 - ensures Part::Box type is registered

    length = float(args["length"])
    width = float(args["width"])
    height = float(args["height"])

    doc = FreeCAD.ActiveDocument or FreeCAD.newDocument()
    doc.openTransaction("ClaudeChat: create box")
    try:
        box = doc.addObject("Part::Box", "Box")
        box.Length = length
        box.Width = width
        box.Height = height
        doc.recompute()
        doc.commitTransaction()
    except Exception:
        doc.abortTransaction()
        raise

    # Best-effort: frame the result in the active view.
    try:
        import FreeCADGui

        FreeCADGui.SendMsgToActiveView("ViewFit")
    except Exception:  # noqa: BLE001
        pass

    return (
        f"Created box '{box.Name}' "
        f"({length:g} x {width:g} x {height:g} mm) "
        f"in document '{doc.Label}'."
    )


_GET_OBJECTS_SCHEMA = {
    "name": "get_objects",
    "description": (
        "Inspect the active FreeCAD document: returns its name and a list of "
        "every object with its internal name, label, type, position, key "
        "dimensions, and visibility (as JSON). Call this before modifying or "
        "referring to existing geometry so you know what's there."
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

        view = getattr(obj, "ViewObject", None)
        if view is not None:
            try:
                info["visible"] = bool(view.Visibility)
            except Exception:  # noqa: BLE001
                pass

        objects.append(info)

    return json.dumps(
        {"document": doc.Label, "object_count": len(objects), "objects": objects},
        indent=2,
    )


_RUN_PYTHON_SCHEMA = {
    "name": "run_python",
    "description": (
        "Execute FreeCAD Python in the running FreeCAD instance. This is how you "
        "do Sketcher work (geometry + constraints), PartDesign features "
        "(Body, Pad, Pocket, Revolution, Loft, Fillet, Chamfer, ...), Part "
        "booleans, Draft, arrays, and anything else in the API. "
        "Pre-bound names: FreeCAD, App, FreeCADGui, Gui, Part, Sketcher, "
        "PartDesign, Draft, and doc (the active document, created if none). "
        "The code runs on the GUI thread inside ONE undoable transaction. "
        "Return data by printing or by assigning to a variable named `result` "
        "(both are returned to you). On error you get the full traceback and the "
        "transaction is rolled back -- fix it and try again. "
        "Work in small steps and verify with get_objects. For PartDesign, create "
        "a PartDesign::Body first and add features inside it. The user is shown "
        "your code and must approve it before it runs."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "FreeCAD Python source to execute"},
            "description": {
                "type": "string",
                "description": "One-line summary of what the code does (shown to the user for approval)",
            },
        },
        "required": ["code"],
    },
}


def _run_python(args):
    import contextlib
    import io
    import traceback

    import FreeCAD

    code = args.get("code", "")
    doc = FreeCAD.ActiveDocument or FreeCAD.newDocument()

    namespace = {"FreeCAD": FreeCAD, "App": FreeCAD, "doc": doc}
    try:
        import FreeCADGui

        namespace["FreeCADGui"] = FreeCADGui
        namespace["Gui"] = FreeCADGui
    except Exception:  # noqa: BLE001
        pass
    for mod_name in ("Part", "Sketcher", "PartDesign", "Draft"):
        try:
            namespace[mod_name] = __import__(mod_name)
        except Exception:  # noqa: BLE001
            pass

    existing = {obj.Name for obj in doc.Objects}
    doc.openTransaction("ClaudeChat: run_python")
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            exec(code, namespace)  # noqa: S102 - intentional, user-approved
        doc.recompute()
        doc.commitTransaction()
    except Exception:
        doc.abortTransaction()
        tb = traceback.format_exc()
        # Safety net: if undo is disabled (so abort didn't roll back), remove any
        # objects this failed run added. No-op when abort already removed them.
        for obj in list(doc.Objects):
            if obj.Name not in existing:
                try:
                    doc.removeObject(obj.Name)
                except Exception:  # noqa: BLE001
                    pass
        captured = stdout.getvalue()
        msg = "Execution failed (rolled back):\n" + tb
        if captured:
            msg += "\n--- stdout before error ---\n" + captured
        return msg

    try:
        import FreeCADGui

        FreeCADGui.SendMsgToActiveView("ViewFit")
    except Exception:  # noqa: BLE001
        pass

    parts = ["OK (committed)."]
    captured = stdout.getvalue()
    if captured:
        parts.append("stdout:\n" + captured)
    if namespace.get("result") is not None:
        parts.append("result: " + repr(namespace["result"]))
    return "\n".join(parts)


#: Registry: tool name -> {schema, run, confirm?}.
#: ``confirm: True`` means the bridge asks the user to approve before running.
TOOLS = {
    "create_box": {"schema": _CREATE_BOX_SCHEMA, "run": _run_create_box},
    "get_objects": {"schema": _GET_OBJECTS_SCHEMA, "run": _run_get_objects},
    "run_python": {"schema": _RUN_PYTHON_SCHEMA, "run": _run_python, "confirm": True},
}


def list_schemas():
    """Return the MCP tool schemas for tools/list."""
    return [entry["schema"] for entry in TOOLS.values()]
