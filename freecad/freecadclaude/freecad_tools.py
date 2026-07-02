# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD tools exposed to Claude, plus their execution functions.

Each ``run`` function executes ON THE GUI MAIN THREAD (the bridge marshals it
there) and returns either a human-readable result string, or -- for
``capture_view``, the one tool that renders a raster image -- a
``(text, png_path)`` tuple; the bridge reads and base64-encodes ``png_path``
and ships it back as an inline MCP image content block, so Claude sees the
picture directly in the tool result instead of needing a separate Read call.
(``view_sketch_svg`` writes an SVG file too, but returns only its path as
plain text -- SVG isn't a raster format the Claude API can render as an
image, so Claude reads it as text via the Read tool instead.) FreeCAD imports
happen inside the functions so this module stays importable from any thread
for its schema data alone.
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


#: Shapes with any extent beyond this (mm) are excluded from _document_bbox --
#: PartDesign datum axes/planes (App::Line, App::Plane) report a literally
#: infinite (~1e100) BoundBox for their visualization geometry, which would
#: otherwise swamp any real part's extent and poison every axis a caller
#: didn't explicitly override with an x_min/x_max/etc crop bound.
_MAX_SANE_EXTENT = 1e6


def _document_bbox(doc):
    """Union BoundBox of every real (finite, Shape-bearing) object in `doc`
    -- the same population fitAll() frames -- used to default any crop axis
    the caller didn't specify for capture_view."""
    import FreeCAD

    box = FreeCAD.BoundBox()
    for obj in doc.Objects:
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        bbox = shape.BoundBox
        if max(abs(bbox.XMin), abs(bbox.XMax), abs(bbox.YMin), abs(bbox.YMax),
               abs(bbox.ZMin), abs(bbox.ZMax)) > _MAX_SANE_EXTENT:
            continue
        box.add(bbox)
    return box


def _bbox_dict(bbox):
    """A FreeCAD.BoundBox as {x_min, x_max, y_min, y_max, z_min, z_max} (mm,
    rounded) -- the same key names capture_view/view_sketch_svg's crop
    params take, so a caller can copy these straight over."""
    return {
        "x_min": round(bbox.XMin, 3), "x_max": round(bbox.XMax, 3),
        "y_min": round(bbox.YMin, 3), "y_max": round(bbox.YMax, 3),
        "z_min": round(bbox.ZMin, 3), "z_max": round(bbox.ZMax, 3),
    }


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


def _projection_degeneracy_warning(content_bounds, crop_viewbox, view):
    """A warning to surface to Claude if a projection render is likely blank
    or useless, else None -- both failure modes below still "succeed" (valid
    SVG/PNG written, no exception) so nothing else catches them.

    - content_bounds is None or a zero-width/zero-height box: the shape has
      no extent left in one screen axis, e.g. a flat/planar object (a sketch
      always has zero thickness) viewed edge-on collapses to a line/point.
    - crop_viewbox (the requested crop, already in the same projected screen
      frame -- see _projected_crop_viewbox) doesn't overlap content_bounds at
      all: the crop excludes 100% of the actual geometry, e.g. a z_min/z_max
      that doesn't include the shape's real Z range.
    """
    if content_bounds is None:
        return (
            f"Warning: nothing projected from the '{view}' direction -- this shape has "
            "no visible edges from this view."
        )
    cminx, cminy, cmaxx, cmaxy = content_bounds
    span = max(cmaxx - cminx, cmaxy - cminy, 1.0)
    eps = span * 1e-4
    if (cmaxx - cminx) < eps or (cmaxy - cminy) < eps:
        return (
            f"Warning: from the '{view}' direction this shape's projection collapses to "
            "a degenerate line or point (no width or height) -- it's likely flat/planar "
            "and edge-on from this view. Try a different 'view', or if this is a 2D "
            "sketch, omit 'view' entirely to get its exact flat geometry instead."
        )
    if crop_viewbox is not None:
        vminx, vminy, vmaxx, vmaxy = crop_viewbox
        if cmaxx < vminx or cminx > vmaxx or cmaxy < vminy or cminy > vmaxy:
            return (
                "Warning: the requested crop (x_min/x_max/y_min/y_max/z_min/z_max) "
                "doesn't overlap this shape's projected geometry at all -- the rendered "
                "image is blank. Check the crop values against the object's real extent "
                "(get_objects reports each object's bounding box), or omit them to see "
                "the full extent."
            )
    return None


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
        "out the accepted argument forms), and then either -- for modules/classes "
        "-- the list of public members, -- for a list/tuple value -- its items, "
        "or -- for a document object instance (has a PropertiesList) -- every "
        "property name AND its current value in one shot (e.g. 'doc.ExampleBox"
        "Instance' already returns 'Length=20.0 mm, Placement=..., ...' with no "
        "extra round trip needed). Examples: ['Sketcher.Constraint', "
        "'Part.makeBox', 'doc.ExampleBodyInstance', "
        "'doc.ExampleSketchInstance.addGeometry'] -- the last two are "
        "illustrative; substitute the real internal Name of a body/sketch "
        "already in the document (check get_objects if unsure -- it's rarely "
        "literally 'Body'/'Sketch'). Only resolves things already reachable by "
        "attribute access -- NOT 'Type::String' names like "
        "'PartDesign::AdditiveBox' or 'PartDesign::Body' (those are passed as "
        "strings to addObject/newObject, not imported -- and don't swap '::' "
        "for '.' and guess a module attribute either, e.g. 'PartDesign.Body' "
        "is NOT a thing; the PartDesign/Part/Sketcher modules expose almost no "
        "feature classes directly, only a few free functions like "
        "Part.makeBox). There is nothing to inspect until you've created one "
        "-- go straight to doc.addObject('PartDesign::Body', 'Body') (or "
        "body.newObject(...) inside a Body), THEN inspect the resulting "
        "object, e.g. 'doc.ExampleBoxInstance'. Watch out for 'Sketcher.Sketch' "
        "specifically -- it resolves (no error) but is a different, lower-level "
        "class from the one your sketches actually are ('Sketcher::SketchObject', "
        "which isn't reachable as a module attribute at all), so its methods carry "
        "thin/misleading docstrings, e.g. 'Sketcher.Sketch.addGeometry' gives just "
        "one line while the real 'doc.ExampleSketchInstance.addGeometry' spells out "
        "every overload and argument. Always inspect sketch methods via an actual "
        "instance, never via 'Sketcher.Sketch'. "
        "Read-only and needs no approval: it only walks attribute chains, "
        "never calls or subscripts. Look up everything you're unsure of in ONE "
        "call, then write the code."
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

    props = getattr(obj, "PropertiesList", None)
    if isinstance(props, (list, tuple)) and not (inspect.ismodule(obj) or inspect.isclass(obj)):
        rows = []
        for prop in props:
            if prop.startswith("_"):
                continue
            try:
                value = repr(getattr(obj, prop))
            except Exception as exc:  # noqa: BLE001
                value = f"<error: {exc!r}>"
            if len(value) > 200:
                value = value[:200] + " […]"
            rows.append(f"{prop}={value}")
        if rows:
            lines.append("properties: " + ", ".join(rows[:60]) + (" …" if len(rows) > 60 else ""))
    elif inspect.ismodule(obj) or inspect.isclass(obj):
        members = [m for m in dir(obj) if not m.startswith("_")]
        if members:
            shown = ", ".join(members[:60])
            lines.append("members: " + shown + (" …" if len(members) > 60 else ""))
    elif isinstance(obj, (list, tuple)):
        items = [repr(x) for x in obj[:60]]
        if items:
            lines.append("items: " + ", ".join(items) + (" …" if len(obj) > 60 else ""))
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
        "See geometry as SVG (exact vector lines). Writes an SVG file and returns "
        "its path -- open it with the Read tool to read the raw vector source "
        "(clean parametric M/L/A commands, exact coordinates). This is text, not "
        "an image -- Claude cannot visually see the shape from it, only reason "
        "about the path data. PREFER this over capture_view whenever exact "
        "coordinates matter more than a visual look:\n"
        "- Flat/2D (sketches, profiles): exports the geometry directly -- the "
        "path data reads cleanly.\n"
        "- 3D solids: pass 'view' (front/rear/top/bottom/left/right/iso) to get a "
        "hidden-line-removed orthographic projection. This path is tessellated "
        "into many small segments and is hard to reason about directly -- prefer "
        "capture_view if you need to visually inspect a 3D shape.\n"
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

    is_projection = bool(view and shape is not None)
    warning = None
    if is_projection:
        # Orthographic projection of 3D geometry (hidden-line removed).
        try:
            import TechDraw

            direction = _PROJECTION_DIRS.get(view, _PROJECTION_DIRS["front"])
            fragment = TechDraw.projectToSVG(shape, FreeCAD.Vector(*direction))
            content_bounds = _svg_fragment_bounds(fragment)
            crop_viewbox = (
                _projected_crop_viewbox(shape, direction, crop_box) if crop_box else None
            )
            warning = _projection_degeneracy_warning(content_bounds, crop_viewbox, view)
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
    if warning:
        parts.append(warning)
    if is_projection:
        # Hidden-line-removed projections tessellate curves/fillets into dozens
        # of near-duplicate micro-segments (and reuse path ids across groups) --
        # noise to reason about even though it's exact. Flagging this steers
        # Claude toward capture_view for 3D shapes instead of guessing from it.
        parts.append(
            "(This projection's raw path data is tessellated into many tiny "
            "segments -- hard to reason about directly. For a visual look at "
            "a 3D shape, use capture_view instead.)"
        )
    parts.append(f"SVG saved to: {svg_path}\n(Open it with the Read tool to read the source.)")
    return "\n\n".join(parts)


_CAPTURE_VIEW_SCHEMA = {
    "name": "capture_view",
    "description": (
        "Take a PNG screenshot of the active document's 3D geometry and return "
        "it inline. Renders through a separate offscreen "
        "camera, so it never disturbs whatever view/tab the user has open. Use "
        "for 3D solids/assemblies (for flat 2D geometry, prefer view_sketch_svg). "
        "Set the camera angle EITHER with 'view' (a named preset: iso, front, "
        "rear, top, bottom, left, right -- the default is iso) OR with "
        "'azimuth'+'elevation' in degrees for any custom orbit angle. Azimuth "
        "swings around the vertical axis: 0 faces the front, +90 the right "
        "side, 180 the back, -90 the left. Elevation tilts above/below eye "
        "level: 0 is side-on, +90 looks straight down from the top, -90 "
        "straight up from below. So a 3/4 view from above-front-right is about "
        "azimuth 45, elevation 30; to see a feature from below-left try azimuth "
        "-45, elevation -30. The whole part is always framed to fit, so "
        "changing the angle re-frames predictably (part centred) rather than "
        "moving the camera nearer/further. "
        "Optionally zoom to a region by giving one or more of x_min/x_max/"
        "y_min/y_max/z_min/z_max (world mm) -- any axis you omit uses the full "
        "document extent, so e.g. for 'top' you'd typically only give x_min/"
        "x_max/y_min/y_max. To instead zoom into part of the image you just got "
        "back -- without working out world coordinates -- use crop_view, which "
        "re-renders a sub-region you point at in normalized 0-1 image space."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "view": {"type": "string", "description": "Camera preset: iso/front/rear/top/bottom/left/right (default iso). Ignored when azimuth/elevation are given."},
            "azimuth": {"type": "number", "description": "Custom orbit angle around the vertical axis, degrees: 0=front, +90=right, 180=back, -90=left. Use with elevation for an angle no preset covers."},
            "elevation": {"type": "number", "description": "Custom orbit angle above/below eye level, degrees: 0=side-on, +90=straight down (top), -90=straight up (bottom). Use with azimuth."},
            "width": {"type": "integer", "description": "Image width px (default 1280)"},
            "height": {"type": "integer", "description": "Image height px (default 960)"},
            **_EXTENT_SCHEMA_PROPS,
        },
        "required": [],
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

#: Background for saveImage on every raster capture (capture_view/crop_view/
#: cutaway). Black rather than white: FreeCAD's default shaded geometry is mid
#: grey, which is low-contrast on white -- a black backdrop makes the part's
#: silhouette and shading read far more clearly for the vision model. (The SVG
#: path keeps its white background: that's black line-art, not shaded geometry.)
_CAPTURE_BG = "Black"


def _mdi_subwindows():
    """The main window's current set of MDI subwindows (one per open document
    view/tab) -- diffed before/after creating a view to spot which subwindow
    it landed in, since FreeCAD's own Python view objects don't expose
    hide/show/close (those are plain Qt widget operations)."""
    from PySide import QtWidgets

    import FreeCADGui

    mdi_area = FreeCADGui.getMainWindow().findChild(QtWidgets.QMdiArea)
    return set(mdi_area.subWindowList()) if mdi_area else set()


def _force_flat_lines(view):
    """Force `view` to render every object shaded-with-edges ("Flat Lines"),
    regardless of each object's own DisplayMode or whatever draw style the
    user's real view currently happens to be set to -- so a capture always
    reads clearly instead of silently inheriting e.g. Wireframe/Points if
    that's what a particular object (or the user) is using elsewhere.

    setOverrideMode is per-viewer state on the throwaway Coin viewer this
    view owns; it never touches the user's real view or any ViewObject
    property. No-op on FreeCAD builds that predate the Python binding for
    View3DInventorViewer.setOverrideMode (FreeCAD/FreeCAD#19044, Jan 2025).
    """
    try:
        view.getViewer().setOverrideMode("Flat Lines")
    except Exception:  # noqa: BLE001
        pass


def _offscreen_view(doc):
    """A throwaway, hidden 3D view of `doc`, for capture_view to render
    through instead of whatever view/tab the user actually has open --
    so a screenshot never hijacks their camera, and never fails just because
    a non-3D tab (e.g. a Spreadsheet) or a different document happens to be
    focused. Returns (view, subwindow, prev_view); view/subwindow may be
    None on failure.

    Gui::Document::createView() unconditionally shows and activates the new
    view -- it exists for the "split view" feature, not headless use -- so we
    hide the Qt subwindow it lands in and restore whatever was active
    immediately after, all within this one call. Qt only actually paints a
    widget on the next event-loop turn, never synchronously inside show(), so
    nothing visibly flashes and other tools' SendMsgToActiveView keeps
    targeting the user's real view.

    prev_view is also handed back to the caller so _close_offscreen_view can
    reassert it once more after the throwaway subwindow is actually closed
    (see that function -- this is what fixes the rare case where the user's
    tabbed layout gets scrambled, e.g. the Start tab reappearing).
    """
    import FreeCADGui

    gui_doc = FreeCADGui.getDocument(doc.Name)
    if gui_doc is None:
        return None, None, None

    prev_view = FreeCADGui.activeView()
    before = _mdi_subwindows()
    view = gui_doc.createView("Gui::View3DInventor")
    if view is None:
        return None, None, prev_view

    # viewTop()/viewIsometric()/fitAll() etc. animate the camera over several
    # QTimer ticks by default and return before the animation finishes; since
    # this view is never shown, the event loop never advances the animation
    # and saveImage() would capture the pre-transition (default) orientation.
    # Disabling animation makes those calls apply immediately/synchronously.
    view.setAnimationEnabled(False)
    _force_flat_lines(view)

    subwindow = next(iter(_mdi_subwindows() - before), None)
    if subwindow is not None:
        subwindow.hide()
    if prev_view is not None:
        FreeCADGui.getMainWindow().setActiveWindow(prev_view)
    return view, subwindow, prev_view


def _close_offscreen_view(subwindow, prev_view=None):
    if subwindow is not None:
        try:
            subwindow.close()  # WA_DeleteOnClose -- also destroys the inner view
        except Exception:  # noqa: BLE001
            pass
    # Closing a QMdiSubWindow (even a hidden one) makes Qt re-pick an active
    # subwindow via its own activation-history bookkeeping, which can land on
    # the wrong one and desync FreeCAD's tabbed MDI area from what's actually
    # active -- seen in the wild as the Start tab and the document both
    # reappearing as untabbed floating windows. Reassert the real previous
    # view now that the close event has fully processed, not just beforehand.
    if prev_view is not None:
        try:
            import FreeCADGui

            FreeCADGui.getMainWindow().setActiveWindow(prev_view)
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


#: The exact camera + render size of the most recent capture_view, saved so
#: crop_view can reproduce the framing Claude just saw and zoom into a
#: sub-rectangle of it (see _run_crop_view). Written at the tail of
#: _run_capture_view while the offscreen view is still alive.
_last_capture = {"camera": None, "width": None, "height": None, "doc": None}

#: crop_view maps a normalized image y (0 = TOP, matching how saveImage writes
#: PNG rows top-first) straight to boxZoom's pixel y. That holds when boxZoom
#: uses a top-left pixel origin -- which is the same convention
#: _pixel_bounds_for_box already relies on (it labels the +y focal-plane
#: direction "down" and feeds the result to boxZoom, and world-space cropping
#: works). Flip to False only if a build renders crops vertically mirrored.
_BOXZOOM_Y_TOPLEFT = True


def _orbit_rotation(azimuth_deg, elevation_deg):
    """Camera orientation for an orbit-style (azimuth, elevation) angle around
    the model, in FreeCAD's Z-up world.

    azimuth: degrees around the vertical Z axis. 0 looks at the model's FRONT
      (camera on -Y, same as the 'front' preset); +90 swings to the right side
      (camera on +X), 180 to the back, -90 to the left. Turning right is +.
    elevation: degrees above the horizon. 0 is eye-level/side-on, +90 looks
      straight down (top), -90 straight up (bottom).

    Returns a FreeCAD.Rotation mapping the camera's local axes (X=right, Y=up,
    Z=toward the viewer) to world -- feed rot.Q to setCameraOrientation and
    then fitAll() to frame the model and fix the focal depth, exactly as
    FreeCAD's own BIM/Draft code does. The cardinal (azimuth, elevation) pairs
    reproduce the matching presets (verified against front/right/back/left/
    top/bottom), so orbit and preset framing agree.
    """
    import math

    import FreeCAD

    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    ca, sa = math.cos(az), math.sin(az)
    ce, se = math.cos(el), math.sin(el)
    # zc: unit vector from the model centre toward the eye (camera "backward").
    zc = FreeCAD.Vector(sa * ce, -ca * ce, se)
    # yc: screen-up = world +Z carried onto the view plane. This closed form is
    # exact even looking straight down/up (where projecting +Z would collapse):
    # there it tends to the in-plane heading, keeping up continuous.
    yc = FreeCAD.Vector(-sa * se, ca * se, ce)
    xc = yc.cross(zc)  # screen-right; completes a right-handed camera basis
    return FreeCAD.Rotation(xc, yc, zc, "ZXY")


def _orbit_angles_from_view(view):
    """The (azimuth, elevation) degrees describing where `view`'s camera
    currently sits, read back from its actual view direction -- the inverse of
    _orbit_rotation. Lets a preset (iso/front/...) report the concrete angle it
    resolved to, so the next capture_view can orbit a little off it. Returns
    None if the direction can't be read. Elevation is +/-90 looking straight
    down/up (azimuth is then indeterminate and reported as ~0)."""
    import math

    try:
        d = view.getViewDirection()  # unit vector the camera looks ALONG
    except Exception:  # noqa: BLE001
        return None
    # zc = model-centre -> eye = the reverse of the look direction.
    zx, zy, zz = -d.x, -d.y, -d.z
    norm = math.sqrt(zx * zx + zy * zy + zz * zz) or 1.0
    zx, zy, zz = zx / norm, zy / norm, zz / norm
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, zz))))
    azimuth = math.degrees(math.atan2(zx, -zy))  # inverse of the zc formula
    return azimuth, elevation


def _apply_camera_orientation(view, rot):
    """Aim `view`'s camera with a Base.Rotation (camera axes -> world), the way
    FreeCAD's BIM code does (setCameraOrientation(rot.Q) + fitAll). Returns True
    on success; the caller fitAll()s afterwards to frame the model and set the
    focal depth. Falls back to the raw Coin camera node if the high-level call
    is missing on some build."""
    try:
        view.setCameraOrientation(rot.Q)
        return True
    except Exception:  # noqa: BLE001 - drop to the underlying Coin camera node
        try:
            view.getCameraNode().orientation.setValue(list(rot.Q))
            return True
        except Exception:  # noqa: BLE001
            return False


def _resolve_camera_args(args):
    """Parse the shared capture_view/cutaway camera args into a plan, or an error.

    Angle comes EITHER from a named preset ('view') OR a custom orbit
    (azimuth/elevation degrees); azimuth/elevation win if given, otherwise fall
    back to the preset, defaulting to iso when 'view' is omitted too. Returns
    ``(plan, None)`` or ``(None, error_string)``. plan keys: ``orbit`` (bool);
    ``azimuth``/``elevation`` (floats, when orbit); ``preset`` (View method
    name) / ``view_arg`` (str, when preset); ``label`` (for the artifact name).
    """
    az_arg, el_arg = args.get("azimuth"), args.get("elevation")
    orbit = az_arg is not None or el_arg is not None
    if orbit:
        try:
            azimuth = float(az_arg) if az_arg is not None else 0.0
            elevation = float(el_arg) if el_arg is not None else 0.0
        except (TypeError, ValueError):
            return None, "azimuth and elevation must be numbers in degrees."
        # Elevation is a tilt above/below the horizon; past +/-90 you'd just
        # cross over to the other side, so clamp it. Azimuth wraps freely.
        elevation = max(-90.0, min(90.0, elevation))
        return {
            "orbit": True, "azimuth": azimuth, "elevation": elevation,
            "label": f"orbit_az{azimuth:g}_el{elevation:g}",
        }, None

    view_arg = str(args.get("view") or "").strip().lower() or "iso"
    preset = _VIEW_PRESETS.get(view_arg)
    if preset is None:
        return None, (
            f"Unknown 'view' {args.get('view')!r}. Pick one of: "
            f"{', '.join(sorted(set(_VIEW_PRESETS)))}, or pass azimuth/"
            "elevation (degrees) for a custom angle."
        )
    return {
        "orbit": False, "preset": preset, "view_arg": view_arg,
        "label": f"view_{view_arg}",
    }, None


def _apply_camera_plan(view, plan):
    """Aim `view`'s camera per a _resolve_camera_args plan, then fitAll to frame
    the model and set the focal depth. Returns an error string, or None."""
    if plan["orbit"]:
        if not _apply_camera_orientation(view, _orbit_rotation(plan["azimuth"], plan["elevation"])):
            return "Could not set a custom camera angle on this FreeCAD build -- use a named 'view' preset instead."
    elif hasattr(view, plan["preset"]):
        getattr(view, plan["preset"])()
    try:
        view.fitAll()
    except Exception:  # noqa: BLE001
        pass
    return None


def _run_capture_view(args):
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return "No active document."

    plan, err = _resolve_camera_args(args)
    if err:
        return err
    orbit = plan["orbit"]
    label = plan["label"]
    if orbit:
        azimuth, elevation = plan["azimuth"], plan["elevation"]
    else:
        view_arg = plan["view_arg"]

    view, subwindow, prev_view = _offscreen_view(doc)
    if view is None:
        return "Could not create an offscreen view to capture."

    # 1280x960 (1.23 MP) sits near Claude's image ceiling (~1.15-1.2 MP / 1568px
    # long edge); larger just gets downscaled again, so this is the detail sweet spot.
    width = int(args.get("width", 1280))
    height = int(args.get("height", 960))
    png_path = _artifact_path("captures", label, ".png")
    extents = _extent_args(args)

    crop_warning = None
    measured = None
    try:
        # Match the render's own pixel size before framing -- boxZoom below
        # works in this widget's pixel space, which must line up with the
        # width/height saveImage renders at or the crop lands off-target.
        if subwindow is not None:
            subwindow.resize(width, height)

        err = _apply_camera_plan(view, plan)
        if err:
            return err

        if extents:
            scene_bbox = _document_bbox(doc)
            if scene_bbox.XMin <= scene_bbox.XMax or all(k in extents for k in _EXTENT_KEYS):
                crop_box = _crop_bbox(scene_bbox, extents)
                pixels = _pixel_bounds_for_box(view, crop_box, width, height)
                if pixels:
                    try:
                        view.boxZoom(*pixels)
                    except Exception as exc:  # noqa: BLE001
                        crop_warning = (
                            f"Warning: could not apply the requested crop ({exc!r}) -- "
                            "showing the full extent instead."
                        )
                else:
                    crop_warning = (
                        "Warning: could not compute a pixel region for the requested "
                        "crop -- showing the full extent instead."
                    )
            else:
                crop_warning = (
                    "Warning: the document has no real geometry to crop against -- "
                    "showing the full extent instead."
                )

        params = FreeCAD.ParamGet(_VIEW_PREF_PATH)
        prev_method = params.GetString("SavePicture", "")
        params.SetString("SavePicture", "FramebufferObject")
        try:
            view.saveImage(png_path, width, height, _CAPTURE_BG)
        finally:
            params.SetString("SavePicture", prev_method)
        # Read back the actual camera angle so the result can report it (e.g.
        # what az/el 'iso' resolved to) -- direction is unchanged by fitAll,
        # boxZoom or saveImage, so measuring here matches the saved image.
        measured = _orbit_angles_from_view(view)
        # Remember this exact framing so crop_view can reproduce it and zoom
        # into a sub-region (getCamera() serializes the Inventor camera node;
        # setCamera() restores it -- independent of preset/fitAll/boxZoom).
        try:
            _last_capture.update(
                camera=view.getCamera(), width=width, height=height, doc=doc.Name
            )
        except Exception:  # noqa: BLE001 - crop_view just falls back to "capture first"
            _last_capture.update(camera=None)
    finally:
        _close_offscreen_view(subwindow, prev_view)

    # Report the resolved camera angle (measured from the real view direction,
    # so presets like iso report their concrete az/el too) and how to nudge it.
    if measured is not None:
        meas_az, meas_el = measured
    elif orbit:
        meas_az, meas_el = azimuth, elevation
    else:
        meas_az = meas_el = None

    if orbit:
        text = f"Captured a custom view of the 3D geometry, saved to {png_path}."
    else:
        text = f"Captured the {view_arg} view, saved to {png_path}."
    if meas_az is not None:
        text += (
            f" Camera angle: azimuth {meas_az:.0f} deg, elevation {meas_el:.0f} deg. "
            "To orbit from here, call capture_view again with adjusted azimuth/"
            "elevation (azimuth + swings right / - left; elevation + lifts the "
            "camera for a more top-down look / - drops it to look upward)."
        )
    if crop_warning:
        text += f"\n\n{crop_warning}"
    return text, png_path


_CROP_VIEW_SCHEMA = {
    "name": "crop_view",
    "description": (
        "Zoom into a sub-region of the image from your LAST capture_view and "
        "re-render it at full resolution -- the way to read fine detail or a "
        "small feature. Give the region in normalized 0-1 coordinates of the "
        "image you just saw: (0,0) is the top-left corner, (1,1) the "
        "bottom-right, (0.5,0.5) the center. x1,y1 is the top-left of the crop "
        "and x2,y2 the bottom-right (so x1<x2 and y1<y2). Unlike cropping a "
        "static picture, this re-renders the 3D scene for that region, so you "
        "get genuinely sharper geometry, not just enlarged pixels. Call "
        "capture_view first; then call crop_view (repeatedly, narrowing in) to "
        "inspect any part of it more closely. It reuses the last capture's "
        "camera, so you don't pass a 'view'."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "x1": {"type": "number", "minimum": 0, "maximum": 1,
                   "description": "Left edge of the crop, 0-1 (0=left edge, 0.5=center)"},
            "y1": {"type": "number", "minimum": 0, "maximum": 1,
                   "description": "Top edge of the crop, 0-1 (0=top edge, 0.5=center)"},
            "x2": {"type": "number", "minimum": 0, "maximum": 1,
                   "description": "Right edge of the crop, 0-1 (must be > x1)"},
            "y2": {"type": "number", "minimum": 0, "maximum": 1,
                   "description": "Bottom edge of the crop, 0-1 (must be > y1)"},
        },
        "required": ["x1", "y1", "x2", "y2"],
        "additionalProperties": False,
    },
}


def _run_crop_view(args):
    import FreeCAD

    camera = _last_capture.get("camera")
    if not camera:
        return (
            "No image to crop yet -- call capture_view first, then crop_view to "
            "zoom into a region of it."
        )

    try:
        x1, y1, x2, y2 = (float(args[k]) for k in ("x1", "y1", "x2", "y2"))
    except (KeyError, TypeError, ValueError):
        return "Pass x1, y1, x2, y2 as numbers in 0-1 (top-left of the crop through bottom-right)."

    # Order/clamp so a swapped or out-of-range corner still yields a sane box.
    x1, x2 = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
    y1, y2 = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))
    if (x2 - x1) < 1e-3 or (y2 - y1) < 1e-3:
        return (
            "Crop region is empty or too small -- give x1<x2 and y1<y2 spanning a "
            "visible area of the last image (values in 0-1)."
        )

    doc = None
    doc_name = _last_capture.get("doc")
    if doc_name:
        try:
            doc = FreeCAD.getDocument(doc_name)
        except Exception:  # noqa: BLE001
            doc = None
    if doc is None:
        doc = FreeCAD.ActiveDocument
    if doc is None:
        return "The document from the last capture is no longer open -- capture_view again first."

    view, subwindow, prev_view = _offscreen_view(doc)
    if view is None:
        return "Could not create an offscreen view to capture."

    width = int(_last_capture.get("width") or 1280)
    height = int(_last_capture.get("height") or 960)

    try:
        # boxZoom works in the viewport's pixel space, so match the render size
        # (and hence the pixel frame) the saved camera was captured at.
        if subwindow is not None:
            subwindow.resize(width, height)
        try:
            view.setCamera(camera)  # reproduce EXACTLY what Claude last saw
        except Exception as exc:  # noqa: BLE001
            return f"Could not reproduce the last camera to crop from: {exc!r}"

        # Normalized image y has its origin at the TOP; flip if this build's
        # boxZoom counts pixels from the bottom (see _BOXZOOM_Y_TOPLEFT).
        py1 = y1 if _BOXZOOM_Y_TOPLEFT else (1.0 - y2)
        py2 = y2 if _BOXZOOM_Y_TOPLEFT else (1.0 - y1)
        pixels = (int(x1 * width), int(py1 * height), int(x2 * width), int(py2 * height))
        try:
            view.boxZoom(*pixels)
        except Exception as exc:  # noqa: BLE001
            return f"Could not zoom into the requested region: {exc!r}"

        png_path = _artifact_path("captures", "crop", ".png")
        params = FreeCAD.ParamGet(_VIEW_PREF_PATH)
        prev_method = params.GetString("SavePicture", "")
        params.SetString("SavePicture", "FramebufferObject")
        try:
            view.saveImage(png_path, width, height, _CAPTURE_BG)
        finally:
            params.SetString("SavePicture", prev_method)
    finally:
        _close_offscreen_view(subwindow, prev_view)

    text = (
        f"Zoomed into ({x1:.2f},{y1:.2f})-({x2:.2f},{y2:.2f}) of the last view and "
        "re-rendered that region at full resolution."
    )
    return text, png_path


#: axis name -> index into (x, y, z), for cutaway's convenience axis mode.
_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def _resolve_clip_plane(args, doc):
    """Build a Coin ``SbPlane`` for the cutaway from `args`.

    Two ways to specify it:
      - ``point`` [x,y,z] + ``normal`` [x,y,z]: an arbitrary plane; the half
        that is KEPT (drawn) is the side the normal points toward.
      - ``axis`` (x/y/z) + ``position`` (mm) + ``keep`` (low/high): a plane
        perpendicular to that axis. ``position`` defaults to the document's
        bbox midpoint on that axis (so a bare ``axis`` just halves the model);
        ``keep`` (default low) chooses the smaller- or larger-coordinate side.

    Returns ``(SbPlane, description, (nx, ny, nz), None)`` on success, or
    ``(None, None, None, error_string)``. The kept-side convention was verified
    against Coin: SoClipPlane keeps points where the plane's normal points, so
    the normal below always points at the half we want to see -- the normal is
    handed back too so the caller can tell whether the camera ended up looking
    INTO the kept half (reveals the cut) or AT its outer surface (looks
    unclipped) without re-deriving it.
    """
    from pivy import coin

    point, normal = args.get("point"), args.get("normal")
    if point is not None or normal is not None:
        if point is None or normal is None:
            return None, None, None, "For a general plane give BOTH 'point' [x,y,z] and 'normal' [x,y,z]."
        try:
            px, py, pz = (float(c) for c in point)
            nx, ny, nz = (float(c) for c in normal)
        except (TypeError, ValueError):
            return None, None, None, "'point' and 'normal' must each be a list of three numbers [x, y, z]."
        n = coin.SbVec3f(nx, ny, nz)
        if n.length() < 1e-9:
            return None, None, None, "'normal' must be a non-zero vector."
        plane = coin.SbPlane(n, coin.SbVec3f(px, py, pz))
        desc = f"a plane through ({px:g}, {py:g}, {pz:g}) with normal ({nx:g}, {ny:g}, {nz:g})"
        return plane, desc, (nx, ny, nz), None

    axis = str(args.get("axis") or "").strip().lower()
    if axis not in _AXIS_INDEX:
        return None, None, None, (
            "Give a clip plane: either 'axis' (x/y/z) [with optional 'position'/'keep'], "
            "or a general 'point' [x,y,z] plus 'normal' [x,y,z]."
        )
    idx = _AXIS_INDEX[axis]

    if args.get("position") is not None:
        try:
            position = float(args["position"])
        except (TypeError, ValueError):
            return None, None, None, "'position' must be a number (mm)."
    else:
        bbox = _document_bbox(doc)
        if bbox.XMin > bbox.XMax:
            return None, None, None, "No geometry to pick a default cut position from -- give 'position' (mm)."
        position = ((bbox.XMin + bbox.XMax) / 2, (bbox.YMin + bbox.YMax) / 2,
                    (bbox.ZMin + bbox.ZMax) / 2)[idx]

    keep = str(args.get("keep") or "low").strip().lower()
    if keep not in ("low", "high"):
        return None, None, None, "'keep' must be 'low' (smaller coordinate) or 'high' (larger)."

    # Normal points toward the half we KEEP: -axis keeps the low side, +axis high.
    nvec, pvec = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    nvec[idx] = -1.0 if keep == "low" else 1.0
    pvec[idx] = position
    plane = coin.SbPlane(coin.SbVec3f(*nvec), coin.SbVec3f(*pvec))
    desc = f"{axis} = {position:g} mm, keeping the {keep} side"
    return plane, desc, tuple(nvec), None


def _insert_clip_plane(view, clip):
    """Insert `clip` (an SoClipPlane) into `view`'s scene graph so it clips all
    geometry in world space.

    Coin issues glClipPlane under the camera's viewing matrix, so the node must
    be traversed AFTER the camera or the plane lands in the wrong space.
    FreeCAD's getSceneGraph() may or may not include the camera: if it does
    (camera is a direct child), we place the clip right after it; if it doesn't
    (the viewer applies the camera in a super-scene the Python API doesn't
    return), the returned graph is already post-camera and index 0 is correct.
    Both arrangements were exercised against pivy; the fallback covers either.
    """
    from pivy import coin

    sg = view.getSceneGraph()
    try:
        search = coin.SoSearchAction()
        search.setType(coin.SoCamera.getClassTypeId())
        search.setInterest(coin.SoSearchAction.FIRST)
        search.apply(sg)
        path = search.getPath()
        if path is not None and path.getLength() == 2:
            parent, cam = path.getNodeFromTail(1), path.getTail()
            if parent.isOfType(coin.SoGroup.getClassTypeId()):
                idx = parent.findChild(cam)
                if idx >= 0:
                    parent.insertChild(clip, idx + 1)
                    return
    except Exception:  # noqa: BLE001 - fall back to the simple post-camera insert
        pass
    sg.insertChild(clip, 0)


def _frame_on_objects(view, doc, names, width, height):
    """Zoom `view` to frame just the named objects. Returns a warning string to
    surface, or None.

    The cutaway clips every visible object regardless; this only tightens the
    camera onto a subset so a specific part fills the frame. Reuses the same
    self-calibrating pixel-box math as capture_view's world crop, so it works
    under any preset/orbit camera.
    """
    import FreeCAD

    box = FreeCAD.BoundBox()
    missing = []
    for n in names:
        obj = doc.getObject(n)
        if obj is None:
            missing.append(n)
            continue
        shape = getattr(obj, "Shape", None)
        if shape is not None and not shape.isNull():
            box.add(shape.BoundBox)
    if box.XMin > box.XMax:
        return "Warning: none of the requested 'names' have geometry to frame -- showing the whole model."
    pixels = _pixel_bounds_for_box(view, box, width, height)
    if pixels:
        try:
            view.boxZoom(*pixels)
        except Exception as exc:  # noqa: BLE001
            return f"Warning: could not frame the requested objects ({exc!r}) -- showing the whole model."
    if missing:
        return f"Note: no object(s) named {', '.join(missing)} -- framed the rest."
    return None


_CUTAWAY_SCHEMA = {
    "name": "cutaway",
    "description": (
        "Screenshot like capture_view, but with a CLIP PLANE that slices the "
        "model open so you can see INSIDE it -- for inspecting internal features "
        "(bores, ribs, wall thickness, pockets, cavities). Renders through the "
        "same offscreen camera, so it never disturbs the user's view or the "
        "document. The cut is HOLLOW: the clip removes the near half and exposes "
        "the interior surfaces, but the cut face itself is open, not a filled "
        "cross-section.\n"
        "Define the plane EITHER the easy way with 'axis' (x/y/z) -- a plane "
        "perpendicular to that axis at 'position' mm (defaults to the model's "
        "midpoint on that axis, i.e. cut it in half), keeping the 'low' "
        "(smaller-coordinate) or 'high' side ('keep', default low) -- OR "
        "generally with 'point' [x,y,z] and 'normal' [x,y,z], where the kept "
        "half is the side the normal points toward.\n"
        "Set the camera angle exactly as capture_view does: a 'view' preset "
        "(iso/front/rear/top/bottom/left/right, default iso) OR 'azimuth'+"
        "'elevation' in degrees for a custom orbit. Optionally pass 'names' "
        "(object internal Names) to frame the shot on specific objects, or "
        "x_min/x_max/y_min/y_max/z_min/z_max (mm, same as capture_view) to crop "
        "the shot to a world-space region; the clip still applies to all visible "
        "geometry regardless of framing.\n"
        "Tip: aim the camera at the cut -- e.g. cut axis=x and view from the "
        "left/right, or cut axis=z and view top/bottom -- so you look squarely "
        "into the opened part. If a cut looks flat/empty/unchanged, change "
        "EXACTLY ONE of 'keep' or 'view' (not both -- flipping both together "
        "cancels out and lands back on the same unclipped-looking angle)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "axis": {"type": "string", "description": "Cut perpendicular to this axis: x, y, or z (the simple way to define the plane)."},
            "position": {"type": "number", "description": "Where along 'axis' to cut, in mm (default: the model's midpoint on that axis)."},
            "keep": {"type": "string", "description": "Which half of an 'axis' cut to keep: 'low' (smaller coordinate) or 'high' (default low)."},
            "point": {"type": "array", "items": {"type": "number"}, "description": "A point on the clip plane [x,y,z] in mm. Use with 'normal' for an arbitrary plane instead of 'axis'."},
            "normal": {"type": "array", "items": {"type": "number"}, "description": "Clip plane normal [x,y,z]; the kept (visible) half is the side it points toward. Use with 'point'."},
            "view": {"type": "string", "description": "Camera preset: iso/front/rear/top/bottom/left/right (default iso). Ignored when azimuth/elevation are given."},
            "azimuth": {"type": "number", "description": "Custom orbit angle around the vertical axis, degrees: 0=front, +90=right, 180=back, -90=left."},
            "elevation": {"type": "number", "description": "Custom orbit angle above/below eye level, degrees: 0=side-on, +90=top-down, -90=bottom-up."},
            "names": {"type": "array", "items": {"type": "string"}, "description": "Internal Names of objects to frame the shot on (optional; the clip still applies to all visible geometry)."},
            **_EXTENT_SCHEMA_PROPS,
            "width": {"type": "integer", "description": "Image width px (default 1280)"},
            "height": {"type": "integer", "description": "Image height px (default 960)"},
        },
        "required": [],
        "additionalProperties": False,
    },
}


def _run_cutaway(args):
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return "No active document."

    plan, err = _resolve_camera_args(args)
    if err:
        return err

    try:
        plane, clip_desc, clip_normal, err = _resolve_clip_plane(args, doc)
    except Exception as exc:  # noqa: BLE001 - e.g. pivy/coin unavailable
        return f"Could not build the clip plane: {exc!r}"
    if err:
        return err

    try:
        from pivy import coin
    except Exception as exc:  # noqa: BLE001
        return f"Could not load the Coin3D scene-graph library for clipping: {exc!r}"

    view, subwindow, prev_view = _offscreen_view(doc)
    if view is None:
        return "Could not create an offscreen view to capture."

    width = int(args.get("width", 1280))
    height = int(args.get("height", 960))
    png_path = _artifact_path("captures", "cutaway", ".png")
    frame_names = args.get("names")
    extents = _extent_args(args)

    frame_warning = None
    crop_warning = None
    measured = None
    degenerate_warning = None
    try:
        # Match the render's pixel size before framing (boxZoom in _frame_on_objects
        # works in this widget's pixel space), same as capture_view.
        if subwindow is not None:
            subwindow.resize(width, height)

        # Clip THIS offscreen view's scene graph (world coords -- see
        # _insert_clip_plane for the camera-order nuance). It clips only this
        # throwaway view and is discarded when the view closes, so the user's
        # real view and the document stay untouched.
        clip = coin.SoClipPlane()
        clip.plane.setValue(plane)
        clip.on.setValue(True)
        try:
            _insert_clip_plane(view, clip)
        except Exception as exc:  # noqa: BLE001
            return f"Could not apply the clip plane to the view: {exc!r}"

        err = _apply_camera_plan(view, plan)
        if err:
            return err

        if extents:
            scene_bbox = _document_bbox(doc)
            if scene_bbox.XMin <= scene_bbox.XMax or all(k in extents for k in _EXTENT_KEYS):
                crop_box = _crop_bbox(scene_bbox, extents)
                pixels = _pixel_bounds_for_box(view, crop_box, width, height)
                if pixels:
                    try:
                        view.boxZoom(*pixels)
                    except Exception as exc:  # noqa: BLE001
                        crop_warning = (
                            f"Warning: could not apply the requested crop ({exc!r}) -- "
                            "showing the full extent instead."
                        )
                else:
                    crop_warning = (
                        "Warning: could not compute a pixel region for the requested "
                        "crop -- showing the full extent instead."
                    )
            else:
                crop_warning = (
                    "Warning: the document has no real geometry to crop against -- "
                    "showing the full extent instead."
                )

        if frame_names:
            frame_warning = _frame_on_objects(view, doc, frame_names, width, height)

        # Direction is unchanged by fitAll/boxZoom, so this matches the saved image.
        measured = _orbit_angles_from_view(view)

        # Detect the "cut looks unclipped" degenerate case: the camera sits ON
        # the kept side, looking further into it, so the (removed) far half was
        # already hidden behind the intact near half -- nothing visibly changes
        # from an ordinary capture_view. That's the case exactly when the
        # camera's view direction points opposite the plane's kept-side normal
        # (dot near -1); dot near +1 means the camera is on the removed side
        # looking INTO the opened cavity, which is what reveals the cut.
        try:
            d = view.getViewDirection()
            dot = d.x * clip_normal[0] + d.y * clip_normal[1] + d.z * clip_normal[2]
        except Exception:  # noqa: BLE001
            dot = None
        if dot is not None and dot < -0.75:
            degenerate_warning = (
                "Warning: this camera angle looks straight at the KEPT half's outer "
                "surface, not into the cut -- the image likely looks identical to an "
                "uncropped capture_view. To actually see inside, change EXACTLY ONE "
                "of 'keep' or 'view' (not both -- flipping both together cancels out "
                "and lands back on this same angle): either keep this 'view' and flip "
                "'keep' to the other side, or keep 'keep' as-is and move the camera to "
                "the opposite side (e.g. the opposite 'view' preset, or azimuth+180)."
            )

        params = FreeCAD.ParamGet(_VIEW_PREF_PATH)
        prev_method = params.GetString("SavePicture", "")
        params.SetString("SavePicture", "FramebufferObject")
        try:
            view.saveImage(png_path, width, height, _CAPTURE_BG)
        finally:
            params.SetString("SavePicture", prev_method)
    finally:
        _close_offscreen_view(subwindow, prev_view)

    text = f"Cutaway at {clip_desc}, saved to {png_path}."
    if measured is not None:
        meas_az, meas_el = measured
        text += f" Camera angle: azimuth {meas_az:.0f} deg, elevation {meas_el:.0f} deg."
    text += (
        " The cut is hollow -- you're seeing the interior surfaces the clip "
        "exposed, not a filled cross-section."
    )
    if degenerate_warning:
        text += f"\n\n{degenerate_warning}"
    if crop_warning:
        text += f"\n\n{crop_warning}"
    if frame_warning:
        text += f"\n\n{frame_warning}"
    return text, png_path


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


#: Plain container types whose own .Shape aggregation can't be trusted (e.g. FreeCAD
#: excludes a child that appears in another object's InList, treating it as an
#: intermediate/consumed shape -- wrong for cases like a body used as a Mirror source
#: where both the original and the mirror are separate final solids). Expand these into
#: their Group children instead of exporting the container's own Shape.
_CONTAINER_TYPES = ("App::Part", "App::DocumentObjectGroup")


def _expand_containers(objs):
    expanded = []
    seen = set()
    stack = list(objs)
    while stack:
        o = stack.pop(0)
        if o.Name in seen:
            continue
        seen.add(o.Name)
        if getattr(o, "TypeId", "") in _CONTAINER_TYPES:
            stack = list(getattr(o, "Group", None) or []) + stack
            continue
        expanded.append(o)
    return expanded


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
    objs = _expand_containers(objs)
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
    "crop_view": {"schema": _CROP_VIEW_SCHEMA, "run": _run_crop_view},
    "cutaway": {"schema": _CUTAWAY_SCHEMA, "run": _run_cutaway},
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
