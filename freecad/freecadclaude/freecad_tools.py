# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD tools exposed to Claude, plus their execution functions.

Each ``run`` function executes ON THE GUI MAIN THREAD (the bridge marshals it
there) and returns a human-readable result string. FreeCAD imports happen
inside the functions so this module stays importable from any thread for its
schema data alone.
"""

import os
import tempfile

#: Default working-files folder: a "FreeCADClaude" subfolder of the user's home
#: (profile) directory, so captures/exports are easy to find -- not buried in
#: FreeCAD's hidden app-data dir. Override with the "ArtifactsDir" preference.
_DEFAULT_ARTIFACTS_DIR = os.path.join(os.path.expanduser("~"), "FreeCADClaude")
_PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/FreeCADClaude"


def artifacts_dir():
    """The browsable folder where captures/exports are written.

    Defaults to ``~/FreeCADClaude`` (captures/ and exports/ live beneath it).
    Override via the FreeCADClaude ``ArtifactsDir`` preference (an absolute path).
    """
    import FreeCAD

    configured = FreeCAD.ParamGet(_PARAM_PATH).GetString("ArtifactsDir", "").strip()
    path = os.path.expanduser(configured) if configured else _DEFAULT_ARTIFACTS_DIR
    os.makedirs(path, exist_ok=True)
    return path


def ensure_sketches_dir():
    """Absolute path to the lo-fi sketch folder (freecad-lofi-sketch), created
    up front so Write -- used directly by Claude, outside the MCP bridge --
    always has somewhere to write."""
    path = os.path.join(artifacts_dir(), "sketches")
    os.makedirs(path, exist_ok=True)
    return path


def _artifact_path(subdir, base, suffix):
    """A unique, readably-named file under <FreeCADClaude>/<subdir>/."""
    folder = os.path.join(artifacts_dir(), subdir)
    os.makedirs(folder, exist_ok=True)
    _prune_folder(folder, keep=60)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in base) or "item"
    fd, path = tempfile.mkstemp(prefix=safe + "_", suffix=suffix, dir=folder)
    os.close(fd)
    return path


def _prune_folder(folder, keep):
    """Keep only the most recent `keep` files in a folder (best effort)."""
    try:
        files = [os.path.join(folder, f) for f in os.listdir(folder)]
        files = [f for f in files if os.path.isfile(f)]
        files.sort(key=os.path.getmtime, reverse=True)
        for old in files[keep:]:
            try:
                os.remove(old)
            except OSError:
                pass
    except OSError:
        pass


def _save_run_python_script(code, description):
    """Archive an approved run_python call under <FreeCADClaude>/scripts/.

    Named "<HHMMSS>_<description>.py" -- just the time, not the date, so
    names stay short but a plain alphabetical directory listing still sorts
    chronologically. Mirrors the captures/exports artifact pattern (pruned to
    the most recent 60) so past runs stay browsable/diffable. Best effort --
    a write failure shouldn't block the actual code execution.
    """
    import time

    try:
        folder = os.path.join(artifacts_dir(), "scripts")
        os.makedirs(folder, exist_ok=True)
        _prune_folder(folder, keep=60)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in description) or "run_python"
        name = time.strftime("%H%M%S") + "_" + safe
        path = os.path.join(folder, name + ".py")
        n = 2
        while os.path.exists(path):  # two runs in the same second
            path = os.path.join(folder, f"{name}-{n}.py")
            n += 1
        with open(path, "w", encoding="utf-8") as f:
            if description:
                f.write(f"# {description}\n")
            f.write(code)
    except OSError:
        pass


def _rasterize_svg(svg_path, png_path, target=768):
    """Render an SVG file to a PNG using bundled QtSvg. Returns True on success."""
    from PySide import QtGui, QtSvg

    renderer = QtSvg.QSvgRenderer(svg_path)
    if not renderer.isValid():
        return False
    box = renderer.viewBoxF()
    w = box.width() or target
    h = box.height() or target
    scale = target / max(w, h)
    image = QtGui.QImage(max(1, int(w * scale)), max(1, int(h * scale)),
                         QtGui.QImage.Format_ARGB32)
    image.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(image)
    renderer.render(painter)
    painter.end()
    return bool(image.save(png_path))


#: Orthographic projection directions for 3D -> SVG views.
_PROJECTION_DIRS = {
    "front": (0, -1, 0), "rear": (0, 1, 0), "back": (0, 1, 0),
    "top": (0, 0, -1), "bottom": (0, 0, 1),
    "right": (-1, 0, 0), "left": (1, 0, 0),
    "iso": (1, -1, 1), "isometric": (1, -1, 1),
}


def _wrap_svg_fragment(fragment):
    """Wrap a TechDraw projection fragment in a full SVG (viewBox + stroke)."""
    import re

    coords = []
    for d in re.findall(r'd="([^"]*)"', fragment):
        coords += [float(n) for n in re.findall(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?", d)]
    xs, ys = coords[0::2], coords[1::2]
    if xs and ys:
        minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
        pad = max(1.0, (maxx - minx + maxy - miny) * 0.03)
        minx, miny, maxx, maxy = minx - pad, miny - pad, maxx + pad, maxy + pad
    else:
        minx, miny, maxx, maxy = 0, 0, 100, 100
    w, h = (maxx - minx) or 1, (maxy - miny) or 1
    stroke = max(0.2, (w + h) / 400.0)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{minx} {miny} {w} {h}" '
        f'width="{w}" height="{h}">'
        f'<rect x="{minx}" y="{miny}" width="{w}" height="{h}" fill="white"/>'
        f'<style>path{{fill:none;stroke:#000000;stroke-width:{stroke};}}</style>'
        f"{fragment}</svg>"
    )


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
    doc.openTransaction("FreeCADClaude: create box")
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

# A failed recompute flags the object Invalid/Error (the red marks in the tree)
# WITHOUT raising, so a tool can "succeed" while a feature is broken.
_ERROR_FLAGS = ("Invalid", "Error")


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

        if any(flag in (getattr(obj, "State", None) or []) for flag in _ERROR_FLAGS):
            info["invalid"] = True  # last recompute failed

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
        "transaction is rolled back -- fix it and try again. If you're unsure of "
        "a method's parameters, call inspect_api first rather than guessing. "
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


def _precheck_python(args):
    """Reject code that won't even compile, BEFORE the user is asked to approve it.

    Run by the bridge ahead of the confirmation dialog: there's no point making
    the user approve code that can't run, and Claude gets the error a turn
    sooner. Returns an error string to relay to Claude, or "" when the code is
    syntactically fine. NB this only catches Python-level syntax errors -- a
    linter can't validate FreeCAD's C++ call signatures, so for *parameter*
    mistakes the agent should reach for inspect_api instead.
    """
    code = args.get("code", "")
    if not code.strip():
        return "No code provided."
    try:
        compile(code, "<run_python>", "exec")
    except SyntaxError as exc:
        where = f"line {exc.lineno}" + (f", col {exc.offset}" if exc.offset else "")
        lines = [f"SyntaxError at {where}: {exc.msg}. Nothing ran -- fix and resend."]
        detail = (exc.text or "").rstrip("\n")
        if detail:
            lines.append(detail)
            lines.append(" " * (max(1, exc.offset or 1) - 1) + "^")
        return "\n".join(lines)
    return ""


def _run_python(args):
    import contextlib
    import io
    import traceback

    import FreeCAD

    code = args.get("code", "")
    doc = FreeCAD.ActiveDocument or FreeCAD.newDocument()

    _save_run_python_script(code, args.get("description") or "")

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
    doc.openTransaction("FreeCADClaude: run_python")
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


_INSPECT_API_SCHEMA = {
    "name": "inspect_api",
    "description": (
        "Look up the real signatures and docstrings of FreeCAD API names BEFORE "
        "writing run_python, so you don't guess parameters. Pass 'names': a LIST "
        "of dotted names to resolve in the run_python namespace (FreeCAD, App, "
        "Part, Sketcher, PartDesign, Draft, Gui, doc, and the active document's "
        "objects). For each it returns the type, a Python signature when one is "
        "available, the docstring (which for FreeCAD's C++ methods usually spells "
        "out the accepted argument forms), and -- for modules/classes -- the list "
        "of public members. Examples: ['Sketcher.Constraint', 'Part.makeBox', "
        "'PartDesign.Body', 'doc.Sketch.addGeometry']. Read-only and needs no "
        "approval: it only walks attribute chains, never calls or subscripts. "
        "Look up everything you're unsure of in ONE call, then write the code."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Dotted API names to inspect, e.g. 'Sketcher.Constraint'",
            },
        },
        "required": ["names"],
    },
}


def _inspect_namespace():
    """The names run_python binds, for read-only API lookups (doc may be absent)."""
    import FreeCAD

    ns = {"FreeCAD": FreeCAD, "App": FreeCAD}
    if FreeCAD.ActiveDocument is not None:
        ns["doc"] = FreeCAD.ActiveDocument
    try:
        import FreeCADGui

        ns["FreeCADGui"] = FreeCADGui
        ns["Gui"] = FreeCADGui
    except Exception:  # noqa: BLE001
        pass
    for mod_name in ("Part", "Sketcher", "PartDesign", "Draft"):
        try:
            ns[mod_name] = __import__(mod_name)
        except Exception:  # noqa: BLE001
            pass
    return ns


def _is_dotted_name(expr):
    """True iff `expr` is only attribute access on a name -- no calls/subscripts.

    Guarantees eval()-ing it can't execute a function or run arbitrary code, so
    inspect_api stays a read-only path (the one mutation path is run_python).
    """
    import ast

    try:
        node = ast.parse(expr.strip(), mode="eval").body
    except SyntaxError:
        return False
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name)


def _describe_api(obj, name):
    """A compact signature/doc/members block for one resolved API object."""
    import inspect

    lines = [f"## {name}"]
    try:
        sig = str(inspect.signature(obj))
    except (TypeError, ValueError):
        sig = None
    if sig:
        lines.append(f"signature: {name.split('.')[-1]}{sig}")
    else:
        lines.append(f"type: {type(obj).__name__}")

    doc = inspect.getdoc(obj)
    if doc:
        doc = doc.strip()
        if len(doc) > 2000:
            doc = doc[:2000] + " […]"
        lines.append(doc)

    if inspect.ismodule(obj) or inspect.isclass(obj):
        members = [m for m in dir(obj) if not m.startswith("_")]
        if members:
            shown = ", ".join(members[:60])
            lines.append("members: " + shown + (" …" if len(members) > 60 else ""))
    return "\n".join(lines)


def _run_inspect_api(args):
    names = args.get("names")
    if isinstance(names, str):
        names = [names]
    if not names:
        return "Pass 'names': a list of dotted API names to look up (e.g. ['Sketcher.Constraint'])."

    ns = _inspect_namespace()
    blocks = []
    for raw in names:
        name = str(raw).strip()
        if not _is_dotted_name(name):
            blocks.append(
                f"## {name}\n(skipped: inspect_api only resolves dotted names like "
                "'Sketcher.Constraint' -- it never calls functions or subscripts.)"
            )
            continue
        try:
            obj = eval(name, dict(ns))  # noqa: S307 - validated as a dotted name only
        except Exception as exc:  # noqa: BLE001
            blocks.append(f"## {name}\n(could not resolve: {exc!r})")
            continue
        blocks.append(_describe_api(obj, name))
    return "\n\n".join(blocks)


_VIEW_SKETCH_SVG_SCHEMA = {
    "name": "view_sketch_svg",
    "description": (
        "See geometry as SVG (exact vector lines). Returns the SVG source plus a "
        "path to a rendered PNG you can open with the Read tool. PREFER this over "
        "capture_view whenever crisp line geometry helps:\n"
        "- Flat/2D (sketches, profiles): exports the geometry directly.\n"
        "- 3D solids: pass 'view' (front/rear/top/bottom/left/right/iso) to get a "
        "clean orthographic projection -- ideal for DIAGNOSING 3D parts (checking "
        "profiles, alignment, holes) from standard views.\n"
        "Optional 'name' = the object's internal Name; defaults to the selected "
        "object, or the first sketch in the document."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Internal Name of the object to view"},
            "view": {
                "type": "string",
                "description": "For 3D objects: front/rear/top/bottom/left/right/iso (orthographic projection)",
            },
        },
        "additionalProperties": False,
    },
}


def _run_view_sketch_svg(args):
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return "No active document."

    obj = None
    name = args.get("name")
    if name:
        obj = doc.getObject(name)
        if obj is None:
            return f"No object named '{name}' in the document."
    else:
        try:
            import FreeCADGui

            selected = [s.Object for s in FreeCADGui.Selection.getSelectionEx()]
        except Exception:  # noqa: BLE001
            selected = []
        if selected:
            obj = selected[0]
        else:
            for candidate in doc.Objects:
                if candidate.TypeId == "Sketcher::SketchObject":
                    obj = candidate
                    break
        if obj is None:
            return "No object found. Pass a 'name', or create/select something first."

    view = str(args.get("view") or "").lower()
    shape = getattr(obj, "Shape", None)
    base = obj.Name + (f"_{view}" if view else "_flat")
    svg_path = _artifact_path("captures", base, ".svg")

    if view and shape is not None:
        # Orthographic projection of 3D geometry (hidden-line removed).
        try:
            import TechDraw

            direction = _PROJECTION_DIRS.get(view, _PROJECTION_DIRS["front"])
            fragment = TechDraw.projectToSVG(shape, FreeCAD.Vector(*direction))
            svg_text = _wrap_svg_fragment(fragment)
            with open(svg_path, "w", encoding="utf-8") as fh:
                fh.write(svg_text)
        except Exception as exc:  # noqa: BLE001
            return f"Projection failed for '{obj.Label}': {exc!r}"
        header = f"Projected '{obj.Label}' ({obj.TypeId}) to a {view} SVG view."
    else:
        # Flat/planar export (sketches etc.).
        try:
            import importSVG

            importSVG.export([obj], svg_path)
            svg_text = open(svg_path, encoding="utf-8").read()
        except Exception as exc:  # noqa: BLE001
            return f"SVG export failed for '{obj.Label}': {exc!r}"
        header = f"Exported '{obj.Label}' ({obj.TypeId}) to SVG."

    parts = [header]
    png_path = _artifact_path("captures", base, ".png")
    if _rasterize_svg(svg_path, png_path):
        parts.append(f"Rendered image (open with the Read tool): {png_path}")
    if len(svg_text) <= 8000:
        parts.append("SVG source:\n" + svg_text)
    else:
        parts.append(f"(SVG source is {len(svg_text)} chars — Read the rendered image instead.)")
    return "\n\n".join(parts)


_CAPTURE_VIEW_SCHEMA = {
    "name": "capture_view",
    "description": (
        "Take a PNG screenshot of the active 3D view and return a path to open "
        "with the Read tool. Use for 3D solids/assemblies (for flat 2D geometry, "
        "prefer view_sketch_svg). Optional 'view' to set the camera first: "
        "iso, front, rear, top, bottom, left, right."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "view": {"type": "string", "description": "Camera preset: iso/front/rear/top/bottom/left/right"},
            "width": {"type": "integer", "description": "Image width px (default 1280)"},
            "height": {"type": "integer", "description": "Image height px (default 960)"},
        },
        "additionalProperties": False,
    },
}

_VIEW_PRESETS = {
    "iso": "viewIsometric", "isometric": "viewIsometric", "axonometric": "viewAxonometric",
    "front": "viewFront", "rear": "viewRear", "back": "viewRear", "top": "viewTop",
    "bottom": "viewBottom", "left": "viewLeft", "right": "viewRight",
}


def _run_capture_view(args):
    import FreeCADGui

    view = FreeCADGui.activeView() if hasattr(FreeCADGui, "activeView") else None
    if view is None or not hasattr(view, "saveImage"):
        return "No active 3D view to capture (open a document with geometry first)."

    preset = _VIEW_PRESETS.get(str(args.get("view") or "").lower())
    if preset and hasattr(view, preset):
        getattr(view, preset)()
    try:
        view.fitAll()
    except Exception:  # noqa: BLE001
        pass

    # 1280x960 (1.23 MP) sits near Claude's image ceiling (~1.15-1.2 MP / 1568px
    # long edge); larger just gets downscaled again, so this is the detail sweet spot.
    width = int(args.get("width", 1280))
    height = int(args.get("height", 960))
    png_path = _artifact_path("captures", f"view_{args.get('view') or 'current'}", ".png")
    view.saveImage(png_path, width, height, "White")
    return f"Captured the 3D view to: {png_path}\n(Open it with the Read tool to see it.)"


_GET_SELECTION_SCHEMA = {
    "name": "get_selection",
    "description": (
        "Return what the user currently has selected in FreeCAD (objects and "
        "sub-elements like Edge3/Face2/Vertex1) as JSON. Use this to act on "
        "what the user clicked (e.g. 'fillet this edge')."
    ),
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}


def _run_get_selection(args):
    import json

    try:
        import FreeCADGui

        selection = FreeCADGui.Selection.getSelectionEx()
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": repr(exc), "selection_count": 0, "selection": []})

    out = []
    for sel in selection:
        obj = sel.Object
        out.append({
            "name": obj.Name,
            "label": obj.Label,
            "type": obj.TypeId,
            "subelements": list(sel.SubElementNames),
        })
    return json.dumps({"selection_count": len(out), "selection": out}, indent=2)


_EXPORT_SCHEMA = {
    "name": "export",
    "description": (
        "Export geometry to a file. Supported formats (by extension): STEP "
        "(.step/.stp), IGES (.iges/.igs), BREP (.brep) for CAD; STL (.stl) for "
        "3D printing/mesh. Provide 'path' (full output path); if omitted, writes "
        "to a temp file using 'format' (default step) and returns the path. "
        "'names' picks objects (default: current selection, else all solids in "
        "the document)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Output file path (extension sets the format)"},
            "format": {"type": "string", "description": "step/iges/brep/stl (used if path has no extension)"},
            "names": {"type": "array", "items": {"type": "string"}, "description": "Object internal Names to export"},
        },
        "additionalProperties": False,
    },
}


def _run_export(args):
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return "No active document."

    names = args.get("names")
    objs = []
    if names:
        for n in names:
            obj = doc.getObject(n)
            if obj is None:
                return f"No object named '{n}'."
            objs.append(obj)
    else:
        try:
            import FreeCADGui

            objs = [s.Object for s in FreeCADGui.Selection.getSelectionEx()]
        except Exception:  # noqa: BLE001
            objs = []
        if not objs:
            objs = [o for o in doc.Objects
                    if getattr(o, "Shape", None) is not None and not o.Shape.isNull()]
    objs = [o for o in objs if getattr(o, "Shape", None) is not None]
    if not objs:
        return "No objects with a shape to export."

    path = args.get("path")
    fmt = str(args.get("format") or "").lower().lstrip(".")
    if path:
        ext = os.path.splitext(path)[1].lower().lstrip(".") or fmt or "step"
        if not os.path.splitext(path)[1]:
            path = f"{path}.{ext}"
    else:
        ext = fmt or "step"
        path = _artifact_path("exports", "export", "." + ext)

    try:
        if ext in ("step", "stp", "iges", "igs", "brep", "brp"):
            import Part

            Part.export(objs, path)
        elif ext == "stl":
            import Part

            Part.Compound([o.Shape for o in objs]).exportStl(path)
        else:
            import Mesh

            Mesh.export(objs, path)
    except Exception as exc:  # noqa: BLE001
        return f"Export failed: {exc!r}"

    names_str = ", ".join(o.Label for o in objs)
    return f"Exported {len(objs)} object(s) [{names_str}] to {path} ({ext.upper()})."


#: Registry: tool name -> {schema, run, confirm?}.
#: ``confirm: True`` means the bridge asks the user to approve before running.
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


TOOLS = {
    "create_box": {"schema": _CREATE_BOX_SCHEMA, "run": _run_create_box},
    "get_objects": {"schema": _GET_OBJECTS_SCHEMA, "run": _run_get_objects},
    "get_selection": {"schema": _GET_SELECTION_SCHEMA, "run": _run_get_selection},
    "view_sketch_svg": {"schema": _VIEW_SKETCH_SVG_SCHEMA, "run": _run_view_sketch_svg},
    "capture_view": {"schema": _CAPTURE_VIEW_SCHEMA, "run": _run_capture_view},
    "export": {"schema": _EXPORT_SCHEMA, "run": _run_export},
    "inspect_api": {"schema": _INSPECT_API_SCHEMA, "run": _run_inspect_api},
    "run_python": {
        "schema": _RUN_PYTHON_SCHEMA,
        "run": _run_python,
        "confirm": True,
        "precheck": _precheck_python,
    },
    "get_diagnostics": {"schema": _GET_DIAGNOSTICS_SCHEMA, "run": _run_get_diagnostics},
}


def list_schemas():
    """Return the MCP tool schemas for tools/list."""
    return [entry["schema"] for entry in TOOLS.values()]
