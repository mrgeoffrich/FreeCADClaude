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


#: Folder name of the chat conversation currently being logged, set by
#: new_session_id() (called from chat_panel on the GUI thread when a chat
#: starts or "New" resets it).
_active_session = {"id": None}

#: Top-level folders under artifacts_dir() that are NOT per-session and must
#: be skipped by session-folder pruning.
_NON_SESSION_DIRS = {"sketches"}


def new_session_id():
    """Mint a fresh id for the current chat conversation and make it active.

    Everything logged for this conversation -- captures, run_python scripts,
    and the CLI's raw JSON stream -- lands under
    <artifacts_dir>/<session_id>/ (see session_dir). Prunes old session
    folders first so a long history of chats doesn't grow the folder forever.
    """
    import secrets
    import time

    _prune_session_dirs()
    session_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    _active_session["id"] = session_id
    return session_id


def session_dir():
    """Absolute path to the active chat conversation's log folder.

    Falls back to a shared "unsaved" folder if called before new_session_id()
    -- shouldn't happen via the bridge, which only runs during a live turn.
    """
    path = os.path.join(artifacts_dir(), _active_session["id"] or "unsaved")
    os.makedirs(path, exist_ok=True)
    return path


def _prune_session_dirs(keep=40):
    """Keep only the most recent `keep` session folders (best effort)."""
    import shutil

    base = artifacts_dir()
    try:
        entries = [os.path.join(base, d) for d in os.listdir(base)]
    except OSError:
        return
    dirs = [d for d in entries
            if os.path.isdir(d) and os.path.basename(d) not in _NON_SESSION_DIRS]
    dirs.sort(key=os.path.getmtime, reverse=True)
    for old in dirs[keep:]:
        try:
            shutil.rmtree(old)
        except OSError:
            pass


def _artifact_path(subdir, base, suffix):
    """A unique, readably-named file under <session_dir>/<subdir>/."""
    folder = os.path.join(session_dir(), subdir)
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
    """Archive an approved run_python call under <session_dir>/scripts/.

    Named "<HHMMSS>_<description>.py" -- just the time, not the date, so
    names stay short but a plain alphabetical directory listing still sorts
    chronologically. Mirrors the captures/exports artifact pattern (pruned to
    the most recent 60) so past runs stay browsable/diffable. Best effort --
    a write failure shouldn't block the actual code execution.
    """
    import time

    try:
        folder = os.path.join(session_dir(), "scripts")
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

#: Optional world-space crop bounds shared by capture_view and view_sketch_svg.
_EXTENT_KEYS = ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")
_EXTENT_SCHEMA_PROPS = {
    key: {"type": "number", "description": f"World-space {key.replace('_', ' ')} (mm) -- omit to use the full extent"}
    for key in _EXTENT_KEYS
}


def _extent_args(args):
    """Pull optional x_min/x_max/y_min/y_max/z_min/z_max floats out of `args`.

    Returns None if the caller gave none of them (skip cropping entirely),
    else a dict with only the keys that were actually provided.
    """
    given = {k: float(args[k]) for k in _EXTENT_KEYS if args.get(k) is not None}
    return given or None


def _crop_bbox(base_bbox, extents):
    """A FreeCAD.BoundBox combining `extents` (from _extent_args) with
    `base_bbox` for any axis the caller didn't specify."""
    import FreeCAD

    return FreeCAD.BoundBox(
        extents.get("x_min", base_bbox.XMin), extents.get("y_min", base_bbox.YMin),
        extents.get("z_min", base_bbox.ZMin), extents.get("x_max", base_bbox.XMax),
        extents.get("y_max", base_bbox.YMax), extents.get("z_max", base_bbox.ZMax),
    )


def _document_bbox(doc):
    """Union BoundBox of every Shape-bearing object in `doc` -- the same
    population fitAll() frames -- used to default any crop axis the caller
    didn't specify for capture_view."""
    import FreeCAD

    box = FreeCAD.BoundBox()
    for obj in doc.Objects:
        shape = getattr(obj, "Shape", None)
        if shape is not None and not shape.isNull():
            box.add(shape.BoundBox)
    return box


def _svg_fragment_bounds(fragment):
    """(minx, miny, maxx, maxy) spanning every coordinate in `fragment`'s
    path data, or None if it has none.

    Applies `fragment`'s own <g transform="scale(sx,sy)">, if any -- e.g.
    TechDraw.projectToSVG wraps its paths in one (always scale(1,-1), to
    flip CAD's Y-up into SVG's Y-down) that the raw d= numbers alone don't
    reflect, so bounds computed straight from those numbers land in the
    wrong place once actually rendered.
    """
    import re

    scale_match = re.search(r'transform="scale\(([^,]+),\s*([^)]+)\)"', fragment)
    sx, sy = (
        (float(scale_match.group(1)), float(scale_match.group(2))) if scale_match else (1.0, 1.0)
    )

    coords = []
    for d in re.findall(r'd="([^"]*)"', fragment):
        coords += [float(n) for n in re.findall(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?", d)]
    xs, ys = [x * sx for x in coords[0::2]], [y * sy for y in coords[1::2]]
    if not (xs and ys):
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _wrap_svg_fragment(fragment, viewbox=None):
    """Wrap a TechDraw projection fragment in a full SVG (viewBox + stroke).

    `viewbox`, if given, is an explicit (minx, miny, maxx, maxy) to frame
    instead of auto-fitting to every coordinate in `fragment` -- used to crop
    to a caller-requested region (see _run_view_sketch_svg).
    """
    bounds = viewbox or _svg_fragment_bounds(fragment)
    if bounds:
        minx, miny, maxx, maxy = bounds
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


def _projected_crop_viewbox(shape, direction, crop_box):
    """(minx, miny, maxx, maxy) that `crop_box` occupies in the SAME 2D frame
    TechDraw.projectToSVG(shape, direction) draws into.

    TechDraw derives its projection axes from OpenCascade's automatic
    perpendicular-to-direction pick, which isn't a documented/stable
    convention (verified empirically -- it doesn't match "screen-right=X,
    screen-up=Y/Z" for any of the standard presets). Rather than guess it,
    project a marker box built from crop_box with the SAME call and read
    back where ITS corners land -- self-calibrating regardless of direction.
    """
    import FreeCAD
    import Part
    import TechDraw

    xlen = max(crop_box.XLength, 0.01)
    ylen = max(crop_box.YLength, 0.01)
    zlen = max(crop_box.ZLength, 0.01)
    marker = Part.makeBox(
        xlen, ylen, zlen, FreeCAD.Vector(crop_box.XMin, crop_box.YMin, crop_box.ZMin)
    )
    fragment = TechDraw.projectToSVG(marker, FreeCAD.Vector(*direction))
    return _svg_fragment_bounds(fragment)


def _flat_crop_svg(svg_text, obj, crop_box):
    """Rewrite a flat importSVG.export() SVG's outer viewBox/<g transform>
    to frame just `crop_box` (world-space mm), converted into the object's
    own local/Placement-relative frame first.

    importSVG emits raw LOCAL path coordinates -- Placement is applied only
    by the wrapping <g transform="translate(...) scale(...)">, not baked
    into the `d=` data (verified empirically) -- so cropping by world bounds
    means inverse-transforming crop_box into that local frame, then
    generating a fresh transform/viewBox pair from scratch (matching the
    original's flip sign, whatever it is, rather than assuming it).
    Returns svg_text unchanged if the expected structure isn't found.
    """
    import re

    import FreeCAD

    match = re.search(
        r'transform="translate\(([^,]+),\s*([^)]+)\)\s*scale\(([^,]+),\s*([^)]+)\)"', svg_text
    )
    if match is None:
        return svg_text
    sx, sy = float(match.group(3)), float(match.group(4))

    inv = obj.Placement.inverse()
    corners = [
        inv.multVec(FreeCAD.Vector(x, y, z))
        for x in (crop_box.XMin, crop_box.XMax)
        for y in (crop_box.YMin, crop_box.YMax)
        for z in (crop_box.ZMin, crop_box.ZMax)
    ]
    xs, ys = [c.x for c in corners], [c.y for c in corners]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)

    pad = max(1.0, ((xmax - xmin) + (ymax - ymin)) * 0.03)
    w = pad * 2 + abs(sx) * (xmax - xmin)
    h = pad * 2 + abs(sy) * (ymax - ymin)
    tx = pad - min(sx * xmin, sx * xmax)
    ty = pad - min(sy * ymin, sy * ymax)

    svg_text = re.sub(r'viewBox="[^"]*"', f'viewBox="0 0 {w:.4f} {h:.4f}"', svg_text, count=1)
    svg_text = re.sub(r'width="[^"]*mm"', f'width="{w:.4f}mm"', svg_text, count=1)
    svg_text = re.sub(r'height="[^"]*mm"', f'height="{h:.4f}mm"', svg_text, count=1)
    svg_text = re.sub(
        r'transform="translate\([^,]+,\s*[^)]+\)\s*scale\([^,]+,\s*[^)]+\)"',
        f'transform="translate({tx:.4f},{ty:.4f}) scale({sx:g},{sy:g})"',
        svg_text,
        count=1,
    )
    return svg_text


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
        "object, or the first sketch in the document. Optionally crop to a region "
        "by giving one or more of x_min/x_max/y_min/y_max/z_min/z_max (world mm) "
        "-- any axis you omit uses the object's full extent."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Internal Name of the object to view"},
            "view": {
                "type": "string",
                "description": "For 3D objects: front/rear/top/bottom/left/right/iso (orthographic projection)",
            },
            **_EXTENT_SCHEMA_PROPS,
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

    extents = _extent_args(args)
    crop_box = None
    if extents and shape is not None and not shape.isNull():
        crop_box = _crop_bbox(shape.BoundBox, extents)

    if view and shape is not None:
        # Orthographic projection of 3D geometry (hidden-line removed).
        try:
            import TechDraw

            direction = _PROJECTION_DIRS.get(view, _PROJECTION_DIRS["front"])
            fragment = TechDraw.projectToSVG(shape, FreeCAD.Vector(*direction))
            crop_viewbox = (
                _projected_crop_viewbox(shape, direction, crop_box) if crop_box else None
            )
            svg_text = _wrap_svg_fragment(fragment, viewbox=crop_viewbox)
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
            if crop_box:
                svg_text = _flat_crop_svg(svg_text, obj, crop_box)
                with open(svg_path, "w", encoding="utf-8") as fh:
                    fh.write(svg_text)
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
        "Take a PNG screenshot of the active document's 3D geometry and return a "
        "path to open with the Read tool. Renders through a separate offscreen "
        "camera, so it never disturbs whatever view/tab the user has open. Use "
        "for 3D solids/assemblies (for flat 2D geometry, prefer view_sketch_svg). "
        "'view' sets the camera angle: iso, front, rear, top, bottom, left, right. "
        "Optionally zoom to a region by giving one or more of x_min/x_max/"
        "y_min/y_max/z_min/z_max (world mm) -- any axis you omit uses the full "
        "document extent, so e.g. for 'top' you'd typically only give x_min/"
        "x_max/y_min/y_max."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "view": {"type": "string", "description": "Camera preset: iso/front/rear/top/bottom/left/right"},
            "width": {"type": "integer", "description": "Image width px (default 1280)"},
            "height": {"type": "integer", "description": "Image height px (default 960)"},
            **_EXTENT_SCHEMA_PROPS,
        },
        "required": ["view"],
        "additionalProperties": False,
    },
}

_VIEW_PRESETS = {
    "iso": "viewIsometric", "isometric": "viewIsometric", "axonometric": "viewAxonometric",
    "front": "viewFront", "rear": "viewRear", "back": "viewRear", "top": "viewTop",
    "bottom": "viewBottom", "left": "viewLeft", "right": "viewRight",
}

#: View's own render-backend preference. Forced to an offscreen-safe value
#: for the duration of a capture (see _run_capture_view) -- "GrabFramebuffer"
#: reads whatever's currently painted on screen, which our hidden view never has.
_VIEW_PREF_PATH = "User parameter:BaseApp/Preferences/View"


def _mdi_subwindows():
    """The main window's current set of MDI subwindows (one per open document
    view/tab) -- diffed before/after creating a view to spot which subwindow
    it landed in, since FreeCAD's own Python view objects don't expose
    hide/show/close (those are plain Qt widget operations)."""
    from PySide import QtWidgets

    import FreeCADGui

    mdi_area = FreeCADGui.getMainWindow().findChild(QtWidgets.QMdiArea)
    return set(mdi_area.subWindowList()) if mdi_area else set()


def _offscreen_view(doc):
    """A throwaway, hidden 3D view of `doc`, for capture_view to render
    through instead of whatever view/tab the user actually has open --
    so a screenshot never hijacks their camera, and never fails just because
    a non-3D tab (e.g. a Spreadsheet) or a different document happens to be
    focused. Returns (view, subwindow); either may be None on failure.

    Gui::Document::createView() unconditionally shows and activates the new
    view -- it exists for the "split view" feature, not headless use -- so we
    hide the Qt subwindow it lands in and restore whatever was active
    immediately after, all within this one call. Qt only actually paints a
    widget on the next event-loop turn, never synchronously inside show(), so
    nothing visibly flashes and other tools' SendMsgToActiveView keeps
    targeting the user's real view.
    """
    import FreeCADGui

    gui_doc = FreeCADGui.getDocument(doc.Name)
    if gui_doc is None:
        return None, None

    prev_view = FreeCADGui.activeView()
    before = _mdi_subwindows()
    view = gui_doc.createView("Gui::View3DInventor")
    if view is None:
        return None, None

    # viewTop()/viewIsometric()/fitAll() etc. animate the camera over several
    # QTimer ticks by default and return before the animation finishes; since
    # this view is never shown, the event loop never advances the animation
    # and saveImage() would capture the pre-transition (default) orientation.
    # Disabling animation makes those calls apply immediately/synchronously.
    view.setAnimationEnabled(False)

    subwindow = next(iter(_mdi_subwindows() - before), None)
    if subwindow is not None:
        subwindow.hide()
    if prev_view is not None:
        FreeCADGui.getMainWindow().setActiveWindow(prev_view)
    return view, subwindow


def _close_offscreen_view(subwindow):
    if subwindow is not None:
        try:
            subwindow.close()  # WA_DeleteOnClose -- also destroys the inner view
        except Exception:  # noqa: BLE001
            pass


def _pixel_bounds_for_box(view, box, width, height):
    """Pixel-space (xmin, ymin, xmax, ymax) framing `box` (a FreeCAD.BoundBox)
    under `view`'s CURRENT camera, for boxZoom.

    Self-calibrated by sampling getPointOnFocalPlane at three known pixel
    corners rather than assuming any particular screen-axis convention --
    orthographic projection makes that sampling exact, and it works
    regardless of which preset (including iso) set up the camera. Returns
    None if the calibration is degenerate (e.g. a zero-size viewport).
    """
    import FreeCAD

    p00 = view.getPointOnFocalPlane(0, 0)
    pw0 = view.getPointOnFocalPlane(width, 0)
    p0h = view.getPointOnFocalPlane(0, height)
    right = (pw0 - p00) * (1.0 / width)
    down = (p0h - p00) * (1.0 / height)

    rr, rd, dd = right.dot(right), right.dot(down), down.dot(down)
    det = rr * dd - rd * rd
    if abs(det) < 1e-9:
        return None

    def pixel_of(point):
        diff = point - p00
        br, bd = diff.dot(right), diff.dot(down)
        return (dd * br - rd * bd) / det, (rr * bd - rd * br) / det

    corners = [
        FreeCAD.Vector(x, y, z)
        for x in (box.XMin, box.XMax)
        for y in (box.YMin, box.YMax)
        for z in (box.ZMin, box.ZMax)
    ]
    xs, ys = zip(*(pixel_of(c) for c in corners))
    return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))


def _run_capture_view(args):
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return "No active document."

    preset = _VIEW_PRESETS.get(str(args.get("view") or "").lower())
    if preset is None:
        return f"Unknown 'view' {args.get('view')!r}. Pick one of: {', '.join(sorted(set(_VIEW_PRESETS)))}."

    view, subwindow = _offscreen_view(doc)
    if view is None:
        return "Could not create an offscreen view to capture."

    # 1280x960 (1.23 MP) sits near Claude's image ceiling (~1.15-1.2 MP / 1568px
    # long edge); larger just gets downscaled again, so this is the detail sweet spot.
    width = int(args.get("width", 1280))
    height = int(args.get("height", 960))
    png_path = _artifact_path("captures", f"view_{args['view']}", ".png")
    extents = _extent_args(args)

    try:
        # Match the render's own pixel size before framing -- boxZoom below
        # works in this widget's pixel space, which must line up with the
        # width/height saveImage renders at or the crop lands off-target.
        if subwindow is not None:
            subwindow.resize(width, height)

        if hasattr(view, preset):
            getattr(view, preset)()
        try:
            view.fitAll()
        except Exception:  # noqa: BLE001
            pass

        if extents:
            scene_bbox = _document_bbox(doc)
            if scene_bbox.XMin <= scene_bbox.XMax or all(k in extents for k in _EXTENT_KEYS):
                crop_box = _crop_bbox(scene_bbox, extents)
                pixels = _pixel_bounds_for_box(view, crop_box, width, height)
                if pixels:
                    try:
                        view.boxZoom(*pixels)
                    except Exception:  # noqa: BLE001
                        pass

        params = FreeCAD.ParamGet(_VIEW_PREF_PATH)
        prev_method = params.GetString("SavePicture", "")
        params.SetString("SavePicture", "FramebufferObject")
        try:
            view.saveImage(png_path, width, height, "White")
        finally:
            params.SetString("SavePicture", prev_method)
    finally:
        _close_offscreen_view(subwindow)

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
