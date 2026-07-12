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


#: When on, _run_python saves a numbered .FCStd snapshot of the document after
#: every successful commit, under <session_dir>/steps/, so the model can be
#: opened at each step of a build. Off by default; the eval turns it on
#: (eval_runner), and interactive sessions can enable it via the "SaveSteps"
#: FreeCADClaude preference or the FREECADCLAUDE_SAVE_STEPS=1 env var.
_save_steps = {"on": os.environ.get("FREECADCLAUDE_SAVE_STEPS") == "1"}


def _save_steps_enabled():
    """Whether per-step .FCStd snapshots are on (in-process flag OR preference)."""
    if _save_steps["on"]:
        return True
    try:
        import FreeCAD

        return bool(FreeCAD.ParamGet(_PARAM_PATH).GetBool("SaveSteps", False))
    except Exception:  # noqa: BLE001
        return False


def _save_step_snapshot(doc, description):
    """Save a numbered .FCStd snapshot of `doc` under <session_dir>/steps/.

    Uses doc.saveCopy so the document's own FileName / modified flag is left
    untouched -- an interactive user's real save location is never hijacked.
    Named "<NNN>_<description>.FCStd" (zero-padded so a plain listing sorts in
    build order); the number is max-existing + 1, staying monotonic even after
    pruning removes early steps. Best effort -- a save failure must not block the
    run_python result. Returns the path or None.
    """
    try:
        folder = os.path.join(session_dir(), "steps")
        os.makedirs(folder, exist_ok=True)
        _prune_folder(folder, keep=60)
        n = 0
        for f in os.listdir(folder):
            head = f.split("_", 1)[0]
            if head.isdigit():
                n = max(n, int(head))
        safe = "".join(c if c.isalnum() or c in "-_" else "_"
                       for c in (description or "")) or "step"
        path = os.path.join(folder, f"{n + 1:03d}_{safe}.FCStd")
        doc.saveCopy(path)
        return path
    except Exception:  # noqa: BLE001
        return None


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


def _document_bbox(doc, names=None):
    """Union BoundBox of every real (finite, Shape-bearing) object in `doc`
    -- the same population fitAll() frames -- used to default any crop axis
    the caller didn't specify for capture_view. Pass `names` (a set/iterable of
    internal Names) to restrict the union to just those objects, e.g. so a
    cutaway's default cut bisects the shown objects, not the whole scene."""
    import FreeCAD

    subset = set(names) if names is not None else None
    box = FreeCAD.BoundBox()
    for obj in doc.Objects:
        if subset is not None and obj.Name not in subset:
            continue
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


def _extent_report(bbox):
    """One-line 'X a..b, Y c..d, Z e..f mm' of a world BoundBox for capture
    results, or None if the box is empty/degenerate.

    Reported alongside the camera angle so Claude can read off the shown
    geometry's position and size in world coords -- and, combined with the
    azimuth/elevation, work out which way X/Y/Z run in the image -- without a
    follow-up get_objects call. Uses the same axis order as the crop params.
    """
    if bbox is None or bbox.XMin > bbox.XMax:
        return None
    d = _bbox_dict(bbox)
    return (
        f"X {d['x_min']:g}..{d['x_max']:g}, "
        f"Y {d['y_min']:g}..{d['y_max']:g}, "
        f"Z {d['z_min']:g}..{d['z_max']:g} mm"
    )


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


def _doc_alive(doc):
    """True while `doc` still references a live document. Running code can close
    the document out from under us (e.g. App.closeDocument); the handle then
    becomes a deleted C++ object and ANY attribute access on it raises, so this
    is how we detect that before touching the stale transaction. Checks the
    handle, not the name -- a closed document's name can be reused by a new one."""
    try:
        doc.Name
        return True
    except Exception:  # noqa: BLE001 - ReferenceError on a deleted document
        return False


def _document_closed_msg(doc_name, stdout_text, tb=None):
    """Reply for when run_python code closed the document it was operating on.

    The transaction we opened went with the document, so there's nothing to
    commit or roll back on our now-deleted handle -- steer back to the supported
    pattern instead of surfacing a bare 'deleted object' ReferenceError."""
    import FreeCAD

    active = FreeCAD.ActiveDocument
    parts = [
        f"run_python closed the active document '{doc_name}' mid-call. Avoid "
        "closing or recreating the document from inside run_python: each call "
        "runs in an undoable transaction on that document, so closing it leaves "
        "nothing to commit and undo can't cover the change. To redo a document's "
        "contents, remove the objects with doc.removeObject(name) and rebuild "
        "them in place instead.",
        f"The active document is now '{active.Name}'." if active is not None
        else "There is no active document now.",
    ]
    if tb:
        parts.append(
            "The code also raised before finishing (no rollback was possible -- "
            "the document was already gone):\n" + tb
        )
    if stdout_text:
        parts.append("stdout:\n" + stdout_text)
    return "\n".join(parts)


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
    doc_name = doc.Name  # remember it now -- the handle dies if the code closes it
    doc.openTransaction("FreeCADClaude: run_python")
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            exec(code, namespace)  # noqa: S102 - intentional, user-approved
        if not _doc_alive(doc):
            # The code closed the document mid-run; the transaction went with it,
            # so don't touch the stale handle -- report it and bail cleanly.
            return _document_closed_msg(doc_name, stdout.getvalue())
        doc.recompute()
        doc.commitTransaction()
    except Exception:
        tb = traceback.format_exc()
        captured = stdout.getvalue()
        if not _doc_alive(doc):
            # Same case, but the code also raised: no rollback is possible on a
            # document that no longer exists, so don't crash trying to abort it.
            return _document_closed_msg(doc_name, captured, tb)
        doc.abortTransaction()
        # Safety net: if undo is disabled (so abort didn't roll back), remove any
        # objects this failed run added. No-op when abort already removed them.
        for obj in list(doc.Objects):
            if obj.Name not in existing:
                try:
                    doc.removeObject(obj.Name)
                except Exception:  # noqa: BLE001
                    pass
        msg = "Execution failed (rolled back):\n" + tb
        if captured:
            msg += "\n--- stdout before error ---\n" + captured
        return msg

    # Optional: snapshot the committed document so the build can be reviewed step
    # by step (off by default; see _save_steps_enabled). Kept out of the reply so
    # it stays a purely on-disk artifact and doesn't nudge the model.
    if _save_steps_enabled():
        _save_step_snapshot(doc, args.get("description") or "")

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

        # PropertiesList covers only the App *properties*. A document object's
        # METHODS -- and the plain Python attributes that aren't App properties
        # (a sketch's DoF, ConflictingConstraints, RedundantConstraints ...) --
        # are invisible in it, which used to make them undiscoverable: the only
        # way to find moveGeometry/setDatum/DoF was to already know the name and
        # guess. Walk dir() so they're listed.
        extras, methods = [], []
        for member in sorted(dir(obj)):
            if member.startswith("_") or member in props:
                continue
            try:
                value = getattr(obj, member)
            except Exception:  # noqa: BLE001
                continue
            if callable(value):
                methods.append(member)
                continue
            text = repr(value)
            if len(text) > 120:
                text = text[:120] + " […]"
            extras.append(f"{member}={text}")
        if extras:
            lines.append("other attributes (NOT in PropertiesList): " + ", ".join(extras))
        if methods:
            lines.append("methods: " + ", ".join(methods))
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


def _find_instance_of_type(type_id):
    """The first object in the active document whose TypeId is (or derives from)
    `type_id`, e.g. 'Sketcher::SketchObject' -> the document's first sketch."""
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return None
    for obj in doc.Objects:
        try:
            if obj.TypeId == type_id or obj.isDerivedFrom(type_id):
                return obj
        except Exception:  # noqa: BLE001
            continue
    return None


def _describe_by_type_id(name):
    """Describe a FreeCAD *type* by finding a live instance of it.

    The classes document objects actually are ('Sketcher::SketchObject',
    'PartDesign::Body') are not reachable as module attributes -- 'Sketcher.
    SketchObject' raises AttributeError -- so asking about one used to return a
    bare "could not resolve" and nothing else. Since the real API lives on the
    instance anyway, resolve it to one. Accepts either the 'Module::Type' form or
    the 'Module.Type' spelling that doesn't resolve as an attribute chain.
    """
    type_id = name.replace(".", "::") if "::" not in name else name
    if "::" not in type_id:
        return None
    obj = _find_instance_of_type(type_id)
    if obj is None:
        return None
    described = _describe_api(obj, f"{name}  (via the live instance '{obj.Name}')")
    return (
        described
        + f"\n\n(NOTE: '{name}' is a FreeCAD type name, not an importable class -- "
        f"'{type_id}' is what you pass to addObject() as a STRING. There is nothing "
        f"to import, so the above describes '{obj.Name}', an actual "
        f"{type_id} in this document, which carries the real API.)"
    )


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

        # 'Sketcher::SketchObject' isn't valid Python, so it never reaches eval --
        # handle the type-name form before the dotted-name gate rejects it.
        if "::" in name:
            described = _describe_by_type_id(name)
            blocks.append(
                described
                or f"## {name}\n(no object of type '{name}' exists in this document "
                "yet -- create one with addObject('{0}', ...) first, then inspect the "
                "resulting object by its Name.)".format(name.replace(".", "::"))
            )
            continue

        if not _is_dotted_name(name):
            blocks.append(
                f"## {name}\n(skipped: inspect_api only resolves dotted names like "
                "'Sketcher.Constraint' -- it never calls functions or subscripts.)"
            )
            continue
        try:
            obj = eval(name, dict(ns))  # noqa: S307 - validated as a dotted name only
        except Exception as exc:  # noqa: BLE001
            # e.g. 'Sketcher.SketchObject' -- a real FreeCAD type, but not a module
            # attribute. Fall back to a live instance rather than giving up.
            described = _describe_by_type_id(name)
            blocks.append(described or f"## {name}\n(could not resolve: {exc!r})")
            continue
        blocks.append(_describe_api(obj, name))
    return "\n\n".join(blocks)


# --- Sketch introspection (get_sketch, and the SVG GeoId overlay) -------------
#
# Every Sketcher mutation is addressed by GeoId: moveGeometry(geoId, posId, ...),
# setDatum(constraintIndex, value), Constraint('Symmetric', geoId, posId, ...).
# Nothing else in this tool set exposes GeoIds -- get_objects gives a bounding box
# and view_sketch_svg's exported paths are merged, unlabelled wires -- so without
# get_sketch the only way to learn a sketch's structure is a pile of exploratory
# run_python dumps, each one a user approval.

#: PosId (the "point" half of a GeoId/PosId pair) as Sketcher numbers them.
_POS_ID_NAMES = {0: "edge", 1: "start", 2: "end", 3: "mid/center"}

#: Constraint types that carry a driving datum in .Value (everything else's Value
#: is meaningless). Angle/SnellsLaw are stored in RADIANS -- a classic wrong-units
#: trap when calling setDatum, so we report degrees alongside.
_DATUM_CONSTRAINTS = {
    "Distance", "DistanceX", "DistanceY", "Radius", "Diameter",
    "Angle", "Weight", "SnellsLaw",
}
_ANGULAR_CONSTRAINTS = {"Angle", "SnellsLaw"}

#: Sketcher's sentinel for "this constraint slot is unused".
_CONSTRAINT_UNUSED = -2000


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


def _xy(vec, places=4):
    """A sketch-local point as [x, y] -- sketch geometry is always z=0 locally."""
    return [round(vec.x, places), round(vec.y, places)]


def _describe_sketch_geometry(geo):
    """Compact dict for one piece of sketch geometry (coords in sketch-local mm)."""
    import Part

    info = {"type": type(geo).__name__}
    try:
        if isinstance(geo, Part.LineSegment):
            info["start"] = _xy(geo.StartPoint)
            info["end"] = _xy(geo.EndPoint)
            info["length"] = round(geo.StartPoint.distanceToPoint(geo.EndPoint), 4)
        elif isinstance(geo, Part.ArcOfCircle):
            info["center"] = _xy(geo.Center)
            info["radius"] = round(geo.Radius, 4)
            info["start"] = _xy(geo.StartPoint)
            info["end"] = _xy(geo.EndPoint)
        elif isinstance(geo, Part.Circle):
            info["center"] = _xy(geo.Center)
            info["radius"] = round(geo.Radius, 4)
        elif isinstance(geo, Part.ArcOfEllipse):
            info["center"] = _xy(geo.Center)
            info["major_radius"] = round(geo.MajorRadius, 4)
            info["minor_radius"] = round(geo.MinorRadius, 4)
            info["start"] = _xy(geo.StartPoint)
            info["end"] = _xy(geo.EndPoint)
        elif isinstance(geo, Part.Ellipse):
            info["center"] = _xy(geo.Center)
            info["major_radius"] = round(geo.MajorRadius, 4)
            info["minor_radius"] = round(geo.MinorRadius, 4)
        elif isinstance(geo, Part.Point):
            info["at"] = [round(geo.X, 4), round(geo.Y, 4)]
        elif isinstance(geo, Part.BSplineCurve):
            info["degree"] = geo.Degree
            info["poles"] = len(geo.getPoles())
            info["start"] = _xy(geo.StartPoint)
            info["end"] = _xy(geo.EndPoint)
        else:
            info["repr"] = str(geo)
    except Exception:  # noqa: BLE001
        info["repr"] = str(geo)
    return info


def _describe_sketch_constraint(index, con):
    """Compact dict for one constraint, including the GeoId/PosId pairs it binds
    and -- for a dimensional constraint -- the datum you'd pass to setDatum."""
    info = {"index": index, "type": con.Type}
    if getattr(con, "Name", ""):
        info["name"] = con.Name
    info["first"] = [con.First, con.FirstPos]
    if con.Second != _CONSTRAINT_UNUSED:
        info["second"] = [con.Second, con.SecondPos]
    if con.Third != _CONSTRAINT_UNUSED:
        info["third"] = [con.Third, con.ThirdPos]
    if con.Type in _DATUM_CONSTRAINTS:
        value = con.Value
        info["value"] = round(value, 6)
        if con.Type in _ANGULAR_CONSTRAINTS:
            import math

            info["value_units"] = "radians"
            info["value_degrees"] = round(math.degrees(value), 4)
        else:
            info["value_units"] = "mm"
        # A non-driving ("reference") constraint measures but does not drive --
        # setDatum on it changes nothing about the geometry.
        info["driving"] = bool(getattr(con, "Driving", True))
    return info


def _external_geo_role(index, geo):
    """What negative GeoId `index` actually is.

    Verified against FreeCAD 1.1: sk.ExternalGeo[i] has GeoId -(i+1), and the
    first three slots are the sketch's own axes and origin -- so real external
    geometry starts at GeoId -4, NOT -3 as the widely-repeated lore says.
    """
    import Part

    if index == 0:
        return "X axis (H_Axis)"
    if index == 1:
        return "Y axis (V_Axis)"
    if index == 2 and isinstance(geo, Part.Point):
        return "origin point (RootPoint)"
    return "external geometry"


def _sketch_report(sk):
    """The full structured picture of a sketch: GeoIds, construction flags,
    constraints (with their indices and datums), solver state, external geometry.

    This is what makes a sketch *editable* -- an edit has to name a GeoId or a
    constraint index, and this is the only place either is exposed."""
    geometry = []
    for i, geo in enumerate(sk.Geometry):
        row = {"geoId": i}
        try:
            row["construction"] = bool(sk.getConstruction(i))
        except Exception:  # noqa: BLE001
            pass
        row.update(_describe_sketch_geometry(geo))
        geometry.append(row)

    constraints = []
    for i, con in enumerate(sk.Constraints):
        try:
            constraints.append(_describe_sketch_constraint(i, con))
        except Exception:  # noqa: BLE001
            constraints.append({"index": i, "type": "?", "repr": str(con)})

    # Reverse index: which constraints pin a given GeoId. The question you ask
    # when geometry won't move -- moveGeometry only shifts UNDERCONSTRAINED
    # geometry, so if a GeoId is held by a datum you must setDatum it instead.
    by_geo = {}
    for con in constraints:
        for key in ("first", "second", "third"):
            pair = con.get(key)
            if not pair:
                continue
            by_geo.setdefault(str(pair[0]), []).append(con["index"])
    for key in by_geo:
        by_geo[key] = sorted(set(by_geo[key]))

    external = []
    try:
        for i, geo in enumerate(sk.ExternalGeo):
            row = {"geoId": -(i + 1), "role": _external_geo_role(i, geo)}
            row.update(_describe_sketch_geometry(geo))
            external.append(row)
    except Exception:  # noqa: BLE001
        pass

    report = {
        "name": sk.Name,
        "label": sk.Label,
        "geometry_count": len(geometry),
        "constraint_count": len(constraints),
    }

    # Solver state. DoF and the conflict/redundancy lists are plain Python
    # attributes, NOT App properties -- they never show up in PropertiesList, so
    # nothing else surfaces them. DoF>0 with no conflicts just means "loose".
    solver = {}
    for attr, key in (
        ("DoF", "degrees_of_freedom"),
        ("FullyConstrained", "fully_constrained"),
    ):
        try:
            solver[key] = getattr(sk, attr)
        except Exception:  # noqa: BLE001
            continue
    # Normalised to 0-based, matching the "index" field above and the argument
    # setDatum/delConstraint expect (the solver itself reports these 1-based).
    for attr, key in (
        ("ConflictingConstraints", "conflicting_constraints"),
        ("RedundantConstraints", "redundant_constraints"),
        ("PartiallyRedundantConstraints", "partially_redundant_constraints"),
        ("MalformedConstraints", "malformed_constraints"),
    ):
        try:
            solver[key] = _solver_constraint_indices(getattr(sk, attr))
        except Exception:  # noqa: BLE001
            continue
    report["solver"] = solver

    shape = getattr(sk, "Shape", None)
    if shape is not None and not shape.isNull():
        wires = shape.Wires
        closed = sum(1 for w in wires if w.isClosed())
        report["wires"] = {"closed": closed, "open": len(wires) - closed}
        if shape.BoundBox.isValid():
            bb = shape.BoundBox
            report["bounding_box_world"] = {
                "x": [round(bb.XMin, 4), round(bb.XMax, 4)],
                "y": [round(bb.YMin, 4), round(bb.YMax, 4)],
                "z": [round(bb.ZMin, 4), round(bb.ZMax, 4)],
            }

    attachment = {"map_mode": getattr(sk, "MapMode", None)}
    try:
        support = sk.AttachmentSupport
        if support:
            attachment["support"] = [
                f"{o.Name}:{','.join(subs)}" if subs else o.Name for o, subs in support
            ]
    except Exception:  # noqa: BLE001
        pass
    try:
        offset = sk.AttachmentOffset
        if not offset.isIdentity():
            attachment["offset"] = str(offset)
    except Exception:  # noqa: BLE001
        pass
    try:
        attachment["placement"] = str(sk.Placement)
    except Exception:  # noqa: BLE001
        pass
    report["attachment"] = attachment

    try:
        refs = []
        for obj, subs in sk.ExternalGeometry:
            refs.append(f"{obj.Name} ({obj.Label}): {', '.join(subs)}")
        if refs:
            report["external_geometry_sources"] = refs
    except Exception:  # noqa: BLE001
        pass

    report["geometry"] = geometry
    report["constraints"] = constraints
    report["constraints_by_geoId"] = by_geo
    if external:
        report["external_geo"] = external

    report["legend"] = {
        "posId": _POS_ID_NAMES,
        "negative_geoIds": (
            "-1 = X axis, -2 = Y axis, -3 = origin point, -4 and below = external "
            "geometry (external starts at -4 in FreeCAD 1.1, not -3)"
        ),
        "constraint_indices": (
            "Every constraint index here is 0-based -- exactly what setDatum(i, v) "
            "and delConstraint(i) take, including the solver's conflicting/redundant/"
            "malformed lists (already converted from the 1-based numbers FreeCAD "
            "reports internally)."
        ),
        "editing": (
            "To MOVE geometry that a dimensional constraint holds, call "
            "setDatum(constraintIndex, newValue) -- do NOT overwrite sk.Geometry "
            "and do not expect moveGeometry to work (it only shifts "
            "UNDERCONSTRAINED geometry). Check constraints_by_geoId to see what "
            "pins a GeoId before trying to move it."
        ),
    }
    return report


_GET_SKETCH_SCHEMA = {
    "name": "get_sketch",
    "description": (
        "Read a sketch's full internal structure -- the ONLY way to see the GeoIds "
        "and constraint indices that every Sketcher edit has to name. Returns JSON: "
        "every geometry element with its GeoId, type, exact coordinates and "
        "construction flag; every constraint with its index, type, the GeoId/PosId "
        "pairs it binds and (for dimensional ones) its datum value -- the value you "
        "pass to setDatum; a constraints_by_geoId reverse index (what pins a given "
        "GeoId); the solver state (degrees_of_freedom, plus any conflicting, "
        "redundant or malformed constraints); external geometry with its negative "
        "GeoIds; and the sketch's attachment/placement, wire closure and world "
        "bounding box. "
        "Call this BEFORE editing any existing sketch -- it replaces the pile of "
        "exploratory run_python dumps you would otherwise need, and it is the only "
        "tool that reveals GeoIds (view_sketch_svg's exported paths merge several "
        "geometries into one unlabelled wire and omit construction geometry "
        "entirely). Optional 'name' = the sketch's internal Name (e.g. 'Sketch001'); "
        "defaults to the sketch being edited, else the selected one, else the "
        "document's only/first sketch. Read-only -- no approval needed."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Internal Name of the sketch, e.g. 'Sketch001'.",
            },
        },
        "required": [],
    },
}


def _resolve_sketch(doc, name=None):
    """The sketch to report on: the named one, else the one open in the Sketcher
    editor, else the selected one, else the document's first."""
    if name:
        sk = doc.getObject(str(name))
        if sk is None:
            return None, f"No object named '{name}' in the document."
        if sk.TypeId != "Sketcher::SketchObject":
            return None, f"'{name}' is a {sk.TypeId}, not a sketch."
        return sk, None

    # Whatever the user currently has open in the Sketcher editor wins -- if they
    # are staring at a sketch and ask about "this sketch", that's the one.
    try:
        import FreeCADGui

        in_edit = FreeCADGui.ActiveDocument.getInEdit()
        if in_edit is not None and in_edit.Object.TypeId == "Sketcher::SketchObject":
            return in_edit.Object, None
    except Exception:  # noqa: BLE001
        pass

    try:
        import FreeCADGui

        for sel in FreeCADGui.Selection.getSelectionEx():
            if sel.Object.TypeId == "Sketcher::SketchObject":
                return sel.Object, None
    except Exception:  # noqa: BLE001
        pass

    sketches = [o for o in doc.Objects if o.TypeId == "Sketcher::SketchObject"]
    if not sketches:
        return None, "This document has no sketches."
    if len(sketches) > 1:
        names = ", ".join(f"{s.Name} ({s.Label})" for s in sketches)
        return sketches[0], (
            f"(No 'name' given and nothing is open/selected -- reporting the first "
            f"of {len(sketches)} sketches. All: {names})"
        )
    return sketches[0], None


def _run_get_sketch(args):
    import json

    import FreeCAD

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return "No active document."

    sk, note = _resolve_sketch(doc, args.get("name"))
    if sk is None:
        return note
    try:
        report = _sketch_report(sk)
    except Exception as exc:  # noqa: BLE001
        return f"Could not read sketch '{sk.Name}': {exc!r}"

    text = json.dumps(report, indent=2)
    # `note` is only set for the ambiguous no-argument case, where it disambiguates
    # which sketch we picked.
    return f"{note}\n{text}" if note else text


def _sketch_anchor(geo):
    """A sketch-local point to hang a GeoId label on -- the middle of the edge."""
    import Part

    try:
        if isinstance(geo, Part.Point):
            return (geo.X, geo.Y)
        shape = geo.toShape()
        mid = shape.valueAt((shape.FirstParameter + shape.LastParameter) / 2.0)
        return (mid.x, mid.y)
    except Exception:  # noqa: BLE001
        try:
            return (geo.StartPoint.x, geo.StartPoint.y)
        except Exception:  # noqa: BLE001
            return None


def _sketch_polyline(geo, segments=48):
    """Sketch-local points tracing `geo` -- for drawing the geometry importSVG
    leaves out entirely (construction and external geometry)."""
    import Part

    try:
        if isinstance(geo, Part.Point):
            return [(geo.X, geo.Y)]
        points = geo.toShape().discretize(Number=max(2, segments))
        return [(p.x, p.y) for p in points]
    except Exception:  # noqa: BLE001
        return []


#: importSVG's wrapper group: translate(tx,ty) scale(sx,sy) (the CAD Y-up -> SVG
#: Y-down flip). _flat_crop_svg regenerates this same pair when cropping, so we
#: parse whatever is actually there rather than assuming the uncropped values.
_SVG_XFORM_RE = (
    r'transform="translate\(\s*([-0-9.eE]+)\s*,\s*([-0-9.eE]+)\s*\)'
    r'\s*scale\(\s*([-0-9.eE]+)\s*,\s*([-0-9.eE]+)\s*\)"'
)
_SVG_VIEWBOX_RE = (
    r'viewBox="\s*([-0-9.eE]+)\s+([-0-9.eE]+)\s+([-0-9.eE]+)\s+([-0-9.eE]+)\s*"'
)

_OVERLAY_COLOURS = {"geo": "#d24000", "construction": "#1e6fd9", "external": "#0a8f3c"}


def _annotate_sketch_svg(svg_text, sk, expand=True):
    """Overlay GeoId labels, construction/external geometry and the origin axes
    onto importSVG's export.

    importSVG exports only the real (non-construction) geometry, FUSES connected
    edges into single unlabelled wire paths, and drops construction and external
    geometry altogether -- so on its own the file cannot tell you which GeoId is
    which, the one thing you need in order to edit the sketch. We keep its exact
    path data untouched and draw a labelled overlay on top, in the same coordinate
    space (read off the wrapper group's transform, so this works cropped or not).

    `expand` grows the viewBox to fit the added geometry; suppressed when the
    caller asked for a crop, so the crop still wins.
    """
    import re

    xform = re.search(_SVG_XFORM_RE, svg_text)
    viewbox = re.search(_SVG_VIEWBOX_RE, svg_text)
    if not xform or not viewbox:
        return svg_text  # unfamiliar export shape -- leave it exactly as it is
    tx, ty, sx, sy = (float(g) for g in xform.groups())
    vx, vy, vw, vh = (float(g) for g in viewbox.groups())

    def to_svg(point):
        """sketch-local mm -> SVG user units (the wrapper group's own transform)."""
        return (tx + sx * point[0], ty + sy * point[1])

    labels = []  # (x, y, text, kind)
    strokes = []  # (kind, [(x, y), ...])

    for geo_id, geo in enumerate(sk.Geometry):
        try:
            construction = bool(sk.getConstruction(geo_id))
        except Exception:  # noqa: BLE001
            construction = False
        if construction:  # absent from the export -- draw it ourselves
            points = [to_svg(p) for p in _sketch_polyline(geo)]
            if points:
                strokes.append(("construction", points))
        anchor = _sketch_anchor(geo)
        if anchor is not None:
            x, y = to_svg(anchor)
            labels.append((x, y, str(geo_id), "construction" if construction else "geo"))

    # External geometry (GeoId -4 and below). Also absent from the export, but it
    # is what the profile is constrained AGAINST, so it's what makes the sketch's
    # position readable at all.
    try:
        for index, geo in enumerate(sk.ExternalGeo):
            geo_id = -(index + 1)
            if geo_id >= -3:
                continue  # the sketch's own axes/origin -- we draw those below
            points = [to_svg(p) for p in _sketch_polyline(geo)]
            if points:
                strokes.append(("external", points))
            anchor = _sketch_anchor(geo)
            if anchor is not None:
                x, y = to_svg(anchor)
                labels.append((x, y, str(geo_id), "external"))
    except Exception:  # noqa: BLE001
        pass

    origin = to_svg((0.0, 0.0))
    if expand:
        xs = [vx, vx + vw, origin[0]]
        ys = [vy, vy + vh, origin[1]]
        for _, points in strokes:
            xs += [p[0] for p in points]
            ys += [p[1] for p in points]
        for x, y, _, _ in labels:
            xs.append(x)
            ys.append(y)
        pad = 0.06 * max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
        vx, vy = min(xs) - pad, min(ys) - pad
        vw, vh = (max(xs) - min(xs)) + 2 * pad, (max(ys) - min(ys)) + 2 * pad

    font = max(max(vw, vh) * 0.028, 0.01)
    width = max(max(vw, vh) * 0.004, 0.001)

    out = ['<g id="freecadclaude-geoids" font-family="sans-serif">']
    # Origin + axes: "is this profile actually centred on the origin?" is the
    # question the raw export can never answer, since it frames to the geometry.
    out.append(
        f'<line x1="{vx:.3f}" y1="{origin[1]:.3f}" x2="{vx + vw:.3f}" y2="{origin[1]:.3f}" '
        f'stroke="#c8102e" stroke-width="{width:.3f}" opacity="0.35"/>'
    )
    out.append(
        f'<line x1="{origin[0]:.3f}" y1="{vy:.3f}" x2="{origin[0]:.3f}" y2="{vy + vh:.3f}" '
        f'stroke="#c8102e" stroke-width="{width:.3f}" opacity="0.35"/>'
    )
    for kind, points in strokes:
        colour = _OVERLAY_COLOURS[kind]
        if len(points) == 1:
            x, y = points[0]
            out.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{width * 2:.3f}" fill="{colour}"/>')
            continue
        data = " ".join(f"{x:.3f},{y:.3f}" for x, y in points)
        dashes = (
            f"{font * 0.35:.3f},{font * 0.25:.3f}"
            if kind == "construction"
            else f"{font * 0.6:.3f},{font * 0.2:.3f},{font * 0.12:.3f},{font * 0.2:.3f}"
        )
        out.append(
            f'<polyline points="{data}" fill="none" stroke="{colour}" '
            f'stroke-width="{width:.3f}" stroke-dasharray="{dashes}"/>'
        )
    for x, y, text, kind in labels:
        out.append(
            f'<text x="{x:.3f}" y="{y:.3f}" font-size="{font:.3f}" '
            f'fill="{_OVERLAY_COLOURS[kind]}" text-anchor="middle" '
            f'dominant-baseline="middle">{text}</text>'
        )
    out.append("</g>")

    svg_text = re.sub(
        _SVG_VIEWBOX_RE, f'viewBox="{vx:.3f} {vy:.3f} {vw:.3f} {vh:.3f}"', svg_text, count=1
    )
    svg_text = re.sub(r'\swidth="[^"]*"', f' width="{vw:.3f}mm"', svg_text, count=1)
    svg_text = re.sub(r'\sheight="[^"]*"', f' height="{vh:.3f}mm"', svg_text, count=1)
    return svg_text.replace("</svg>", "\n".join(out) + "\n</svg>")


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

            # importSVG.export builds its output through a throwaway "hidden"
            # document (a Part2DObjectPython) and leaves it open, so it piles up
            # a stray doc per call. Close whatever it opened and restore the
            # active document it may have stolen.
            active = FreeCAD.ActiveDocument
            before_docs = set(FreeCAD.listDocuments())
            importSVG.export([obj], svg_path)
            for leaked in set(FreeCAD.listDocuments()) - before_docs:
                try:
                    FreeCAD.closeDocument(leaked)
                except Exception:  # noqa: BLE001
                    pass
            if active is not None:
                FreeCAD.setActiveDocument(active.Name)
            svg_text = open(svg_path, encoding="utf-8").read()
            if crop_box:
                svg_text = _flat_crop_svg(svg_text, obj, crop_box)
            # Label GeoIds LAST: _flat_crop_svg regenerates the wrapper group's
            # transform, and the overlay is positioned from whatever transform
            # ends up in the file.
            if obj.TypeId == "Sketcher::SketchObject":
                try:
                    svg_text = _annotate_sketch_svg(svg_text, obj, expand=not crop_box)
                except Exception:  # noqa: BLE001
                    pass  # never lose the exported geometry over a failed overlay
            with open(svg_path, "w", encoding="utf-8") as fh:
                fh.write(svg_text)
        except Exception as exc:  # noqa: BLE001
            return f"SVG export failed for '{obj.Label}': {exc!r}"
        header = f"Exported '{obj.Label}' ({obj.TypeId}) to SVG."
        if obj.TypeId == "Sketcher::SketchObject":
            annotations = (
                "GeoIds are labelled on the overlay: orange = normal geometry, "
                "blue dashed = construction, green dash-dot = external geometry "
                "(negative GeoIds); the faint red cross is the sketch origin. "
                "Note the exported <path> data FUSES connected edges into one wire, "
                "so a path is not a GeoId -- use get_sketch for the authoritative "
                "GeoId/constraint listing before editing."
            )
            header = f"{header}\n{annotations}"

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
        "-45, elevation -30. The chosen object(s) are always framed to fit, so "
        "changing the angle re-frames predictably (object centred) rather than "
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
            "objects": {
                "type": "array", "items": {"type": "string"}, "minItems": 1,
                "description": (
                    "REQUIRED. Internal Names (from get_objects, e.g. 'Body', "
                    "'Box001' -- NOT Labels) of the object(s) to show. Only these "
                    "are made visible for the shot and everything else in the "
                    "document is hidden, so the capture is precisely controlled "
                    "and auto-framed on exactly them. Naming a container (an "
                    "App::Part/Group, or a PartDesign Body) shows its contents. "
                    "The user's real view is left untouched -- prior visibility is "
                    "restored right after the capture."
                ),
            },
            "view": {"type": "string", "description": "Camera preset: iso/front/rear/top/bottom/left/right (default iso). Ignored when azimuth/elevation are given."},
            "azimuth": {"type": "number", "description": "Custom orbit angle around the vertical axis, degrees: 0=front, +90=right, 180=back, -90=left. Use with elevation for an angle no preset covers."},
            "elevation": {"type": "number", "description": "Custom orbit angle above/below eye level, degrees: 0=side-on, +90=straight down (top), -90=straight up (bottom). Use with azimuth."},
            "width": {"type": "integer", "description": "Image width px (default 1280)"},
            "height": {"type": "integer", "description": "Image height px (default 960)"},
            **_EXTENT_SCHEMA_PROPS,
        },
        "required": ["objects"],
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
#: reads whatever's currently painted on screen, which our throwaway view never has.
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
    """A throwaway 3D view of `doc`, for capture_view to render through
    instead of whatever view/tab the user actually has open -- so a
    screenshot never hijacks their camera, and never fails just because a
    non-3D tab (e.g. a Spreadsheet) or a different document happens to be
    focused. Returns (view, subwindow, prev_view); view/subwindow may be
    None on failure.

    Gui::Document::createView() unconditionally shows and activates the new
    view (it exists for the "split view" feature, not headless use), so it
    briefly becomes the active tab while the capture runs. That's fine to let
    happen -- the whole tool call is one blocked GUI-thread event, so Qt never
    gets a turn to paint it anyway. An earlier version tried to hide the
    subwindow and restore focus immediately, before the capture even ran; that
    extra churn (deactivating/hiding a window Qt still considered "active")
    was what confused QMdiArea's own activation-history bookkeeping and left
    the user's tabbed layout scrambled after close() -- e.g. the Start tab or
    the document reappearing untabbed. Letting the new view become active
    normally, then closing it and reasserting `prev_view` exactly once (see
    _close_offscreen_view), is the sequence Qt's bookkeeping handles cleanly.

    prev_view is handed back to the caller so _close_offscreen_view can
    reactivate it once the throwaway subwindow is actually closed.
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
    # the event loop never turns during this call, disable animation so those
    # calls apply immediately/synchronously instead of capturing mid-transition.
    view.setAnimationEnabled(False)
    _force_flat_lines(view)

    subwindow = next(iter(_mdi_subwindows() - before), None)
    return view, subwindow, prev_view


def _close_offscreen_view(subwindow, prev_view=None):
    """Tear down the throwaway view and hand focus back to whatever the user
    actually had open. Closing a QMdiSubWindow makes Qt re-pick an active
    subwindow via its own activation-history bookkeeping; reasserting
    `prev_view` afterwards makes sure that pick is the user's real previous
    view, not whatever QMdiArea happened to land on."""
    if subwindow is not None:
        try:
            subwindow.close()  # WA_DeleteOnClose -- also destroys the inner view
        except Exception:  # noqa: BLE001
            pass
    if prev_view is not None:
        try:
            import FreeCADGui

            FreeCADGui.getMainWindow().setActiveWindow(prev_view)
        except Exception:  # noqa: BLE001
            pass


def _camera_basis(cam):
    """(right, up, forward) unit FreeCAD.Vectors of an SoCamera's orientation in
    world coords -- forward is the look-along direction. Derived from the node's
    orientation quaternion so it's exact regardless of preset/orbit."""
    import FreeCAD

    q = cam.orientation.getValue().getValue()  # (x, y, z, w) quaternion
    rot = FreeCAD.Rotation(q[0], q[1], q[2], q[3])
    return (
        rot.multVec(FreeCAD.Vector(1, 0, 0)),
        rot.multVec(FreeCAD.Vector(0, 1, 0)),
        rot.multVec(FreeCAD.Vector(0, 0, -1)),
    )


def _ortho_camera(view):
    """`view`'s camera node iff it's an orthographic camera, else None. The
    analytic framing below only holds for orthographic projection (which is
    what capture_view uses); callers fall back to the plain fitAll frame
    otherwise."""
    try:
        from pivy import coin

        cam = view.getCameraNode()
        if cam is not None and cam.isOfType(coin.SoOrthographicCamera.getClassTypeId()):
            return cam
    except Exception:  # noqa: BLE001
        pass
    return None


def _frame_camera_on_box(view, box, aspect, margin=1.06):
    """Aim `view`'s ORTHOGRAPHIC camera at world BoundBox `box` and scale it so
    the box fills a viewport of `aspect` (= render width/height), by writing the
    camera fields directly.

    This replaces the old boxZoom-based crop, which worked in the offscreen
    viewer's pixel space -- but that throwaway view is never realized at the render
    size, so boxZoom's pixel math ran against a mismatched/degenerate viewport
    and mis-framed (blank images, or a sliver of unrelated geometry over-zoomed),
    worst of all under rotated iso/orbit cameras. Setting height/position/aspect
    on the camera node is viewport-independent, so it frames the same at any
    (or no) realized widget size. Returns True on success, False if the camera
    isn't orthographic or the box is degenerate (caller keeps the fitAll frame).
    """
    import FreeCAD

    cam = _ortho_camera(view)
    if cam is None:
        return False
    try:
        right, up, fwd = _camera_basis(cam)
        center = FreeCAD.Vector(
            (box.XMin + box.XMax) / 2.0,
            (box.YMin + box.YMax) / 2.0,
            (box.ZMin + box.ZMax) / 2.0,
        )
        # Half-extents of the box measured along the camera's own axes: how wide
        # (hu), tall (hv) and deep (hd) it is on screen. min/max over the 8
        # corners gives the tight screen-aligned bound for any orientation.
        hu = hv = hd = 0.0
        for cx in (box.XMin, box.XMax):
            for cy in (box.YMin, box.YMax):
                for cz in (box.ZMin, box.ZMax):
                    d = FreeCAD.Vector(cx, cy, cz) - center
                    hu = max(hu, abs(d.dot(right)))
                    hv = max(hv, abs(d.dot(up)))
                    hd = max(hd, abs(d.dot(fwd)))
        if hu <= 1e-9 and hv <= 1e-9:
            return False
        # Ortho height that contains the box both ways at the render's aspect.
        height = 2.0 * max(hv, hu / aspect) * margin
        if height <= 1e-9:
            return False
        # Ortho scale is set by `height`, not distance, so the standoff only has
        # to keep the box comfortably between the near/far planes.
        standoff = 2.0 * hd + height + 1.0
        eye = center - fwd * standoff
        cam.position.setValue(eye.x, eye.y, eye.z)
        cam.focalDistance.setValue(standoff)
        cam.aspectRatio.setValue(aspect)
        cam.height.setValue(height)
        pad = height * 0.1 + 1.0
        cam.nearDistance.setValue(max(1e-4, standoff - hd - pad))
        cam.farDistance.setValue(standoff + hd + pad)
        return True
    except Exception:  # noqa: BLE001 - any coin/API hiccup -> keep the fitAll frame
        return False


def _crop_camera_frame(view, x1, y1, x2, y2, aspect):
    """Zoom `view`'s ORTHOGRAPHIC camera into the normalized sub-rectangle
    (x1,y1)-(x2,y2) of what it currently frames (0-1, y from the TOP), by
    offsetting and rescaling the camera node directly -- the viewport-independent
    equivalent of the old boxZoom, for crop_view. Returns True on success, False
    if the camera isn't orthographic."""
    import FreeCAD

    cam = _ortho_camera(view)
    if cam is None:
        return False
    try:
        right, up, _fwd = _camera_basis(cam)
        height = cam.height.getValue()
        vis_w = height * aspect
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        # Shift the eye laterally so the sub-rect's centre becomes the new centre
        # (image y grows downward, i.e. against +up).
        offset = right * ((cx - 0.5) * vis_w) + up * ((0.5 - cy) * height)
        p = cam.position.getValue().getValue()
        eye = FreeCAD.Vector(p[0], p[1], p[2]) + offset
        cam.position.setValue(eye.x, eye.y, eye.z)
        # Grow the smaller side to the render aspect so nothing is squashed.
        cam.height.setValue(max(height * max(y2 - y1, x2 - x1), 1e-4))
        cam.aspectRatio.setValue(aspect)
        return True
    except Exception:  # noqa: BLE001
        return False


def _save_view_png(view, png_path, width, height):
    """Render `view` to `png_path` at width x height on the _CAPTURE_BG
    background, forcing the FBO save method (and restoring the user's) -- the
    one place capture_view/crop_view/cutaway actually write an image."""
    import FreeCAD

    params = FreeCAD.ParamGet(_VIEW_PREF_PATH)
    prev_method = params.GetString("SavePicture", "")
    params.SetString("SavePicture", "FramebufferObject")
    try:
        view.saveImage(png_path, width, height, _CAPTURE_BG)
    finally:
        params.SetString("SavePicture", prev_method)


def _looks_blank(png_path):
    """True if the saved PNG is essentially just the render background (plus the
    tiny axis gizmo) -- i.e. the framing missed the geometry. Lets the capture
    tools tell Claude 'that came out empty' instead of silently handing back a
    background-only image it can't tell apart from a genuinely wrong/hidden model.

    Blank means "predominantly the KNOWN capture background" (`_CAPTURE_BG`), not
    "predominantly whatever colour the corner pixel happens to be". Sampling the
    corner as background looks right until a crop (crop_view, or capture_view with
    x_min..z_max) zooms entirely inside a solid face: then the whole frame -- corner
    included -- is the object's shaded grey, so a corner-relative test reads a
    perfectly-framed close-up as uniform "background" and fires a bogus 'came out
    empty' warning (and, on the capture_view path, throws the crop away for a
    fitAll fallback). Anchoring to the real background instead means a uniform
    *non-background* fill correctly counts as content. All three callers render via
    _save_view_png on _CAPTURE_BG, so this is exact for every one. Best-effort: any
    read failure returns False (assume not blank)."""
    try:
        from PySide import QtGui

        img = QtGui.QImage(png_path)
        if img.isNull() or img.width() == 0 or img.height() == 0:
            return False
        w, h = img.width(), img.height()
        bg = QtGui.QColor(_CAPTURE_BG)  # the background we actually rendered on
        if not bg.isValid():  # fall back to a corner only if the name won't parse
            bg = img.pixelColor(0, 0)
        br, bgc, bb = bg.red(), bg.green(), bg.blue()
        step = max(1, min(w, h) // 150)  # subsample to ~30k points
        content = total = 0
        y = 0
        while y < h:
            x = 0
            while x < w:
                c = img.pixelColor(x, y)
                if abs(c.red() - br) + abs(c.green() - bgc) + abs(c.blue() - bb) > 60:
                    content += 1
                total += 1
                x += step
            y += step
        return total > 0 and (content / total) < 0.004
    except Exception:  # noqa: BLE001
        return False


#: The exact camera + render size of the most recent capture_view, saved so
#: crop_view can reproduce the framing Claude just saw and zoom into a
#: sub-rectangle of it (see _run_crop_view). Written at the tail of
#: _run_capture_view while the offscreen view is still alive.
_last_capture = {"camera": None, "width": None, "height": None, "doc": None, "keep": None}


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
    import FreeCADGui

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return "No active document."

    names = args.get("objects")
    if not names:
        return ("capture_view requires 'objects': a list of object Names to show "
                "(everything else is hidden for the shot). Call get_objects first.")
    for n in names:
        if doc.getObject(n) is None:
            return f"No object named '{n}'."
    keep_set = _visibility_keep_set(doc, names)

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

    # Toggling Visibility dirties the GUI document; snapshot the flag so we can
    # restore it (we net-restore the visibility values themselves too).
    gui_doc = FreeCADGui.getDocument(doc.Name)
    prev_modified = getattr(gui_doc, "Modified", None)

    # 1280x960 (1.23 MP) sits near Claude's image ceiling (~1.15-1.2 MP / 1568px
    # long edge); larger just gets downscaled again, so this is the detail sweet spot.
    width = int(args.get("width", 1280))
    height = int(args.get("height", 960))
    png_path = _artifact_path("captures", label, ".png")
    extents = _extent_args(args)

    warnings = []
    measured = None
    saved = []
    saved_sel = []
    try:
        # Show only the requested objects; fitAll (in _apply_camera_plan) then
        # frames tightly on exactly them. Restored in the finally below.
        saved = _isolate_visibility(doc, keep_set)
        saved_sel = _suspend_selection(doc)  # drop selection highlight for the shot
        if subwindow is not None:
            subwindow.resize(width, height)

        err = _apply_camera_plan(view, plan)
        if err:
            return err

        aspect = float(width) / float(height)
        if extents:
            scene_bbox = _document_bbox(doc)
            if scene_bbox.XMin <= scene_bbox.XMax or all(k in extents for k in _EXTENT_KEYS):
                crop_box = _crop_bbox(scene_bbox, extents)
                if not _frame_camera_on_box(view, crop_box, aspect):
                    warnings.append(
                        "Warning: could not frame the requested crop on this build -- "
                        "showing the full extent instead."
                    )
                    view.fitAll()
            else:
                warnings.append(
                    "Warning: the document has no real geometry to crop against -- "
                    "showing the full extent instead."
                )

        _save_view_png(view, png_path, width, height)

        # Don't silently hand back a black frame: if it's essentially empty, tell
        # Claude (and for a crop, retry once at the full fitAll frame) so it gets
        # a signal instead of burning turns re-shooting a blank it can't diagnose.
        if _looks_blank(png_path):
            if extents and not warnings:
                view.fitAll()
                _save_view_png(view, png_path, width, height)
                if _looks_blank(png_path):
                    warnings.append(
                        "Warning: the view is empty -- no visible geometry to show "
                        "(is everything hidden?)."
                    )
                else:
                    warnings.append(
                        "Warning: the requested crop region came out empty at this "
                        "camera angle -- showing the full view instead. Re-check the "
                        "x_min..z_max values against get_objects."
                    )
            elif not warnings:
                warnings.append(
                    "Warning: the view is empty -- no visible geometry to show at this "
                    "angle (is everything hidden, or is the object off to one side?)."
                )

        # Read back the actual camera angle so the result can report it (e.g.
        # what az/el 'iso' resolved to) -- direction is unchanged by fitAll or
        # saveImage, so measuring here matches the saved image.
        measured = _orbit_angles_from_view(view)
        # Remember this exact framing so crop_view can reproduce it and zoom
        # into a sub-region (getCamera() serializes the Inventor camera node;
        # setCamera() restores it -- independent of how the frame was set).
        try:
            _last_capture.update(
                camera=view.getCamera(), width=width, height=height,
                doc=doc.Name, keep=keep_set,
            )
        except Exception:  # noqa: BLE001 - crop_view just falls back to "capture first"
            _last_capture.update(camera=None)
    finally:
        _restore_visibility(saved)
        _restore_selection(saved_sel)
        if prev_modified is not None:
            try:
                gui_doc.Modified = prev_modified
            except Exception:  # noqa: BLE001
                pass
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
    framed_extents = _extent_report(_document_bbox(doc, names=keep_set))
    if framed_extents:
        text += f" Shown geometry spans {framed_extents} (world coords)."
    if warnings:
        text += "\n\n" + "\n".join(warnings)
    return text, png_path


_CAPTURE_USER_VIEW_SCHEMA = {
    "name": "capture_user_view",
    "description": (
        "Take a PNG screenshot of EXACTLY what the user is currently looking at "
        "in their own 3D view -- their real camera angle, zoom, pan, draw style "
        "(shaded/wireframe/etc.) and background. Unlike capture_view (which "
        "renders through a separate auto-framed offscreen camera and never "
        "touches the user's view), this reads the already-rendered pixels of "
        "the user's actual active view, so it never moves their camera or "
        "changes anything about the document either -- it's purely read-only. "
        "Reach for this when the user is pointing at or describing something "
        "in front of them right now ('look at this', 'why does this edge look "
        "wrong', 'see what I mean?') and you want to see precisely what they "
        "see, instead of guessing an angle with capture_view. Fails if the "
        "active tab isn't a 3D view (e.g. a Spreadsheet or TechDraw page is "
        "focused) -- ask the user to click into their 3D view and try again."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "width": {
                "type": "integer",
                "description": (
                    "Max image width px (default 1280). The user's real view's "
                    "current aspect ratio is preserved, so height follows "
                    "automatically -- this only caps output size."
                ),
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}


def _run_capture_user_view(args):
    import FreeCAD
    import FreeCADGui

    view = FreeCADGui.activeView()
    if view is None or not hasattr(view, "saveImage"):
        return (
            "The active tab isn't a 3D view -- click into the user's 3D view "
            "(not a Spreadsheet/TechDraw/other tab) and try again."
        )

    width = int(args.get("width") or 1280)
    if width <= 0:
        width = 1280
    png_path = _artifact_path("captures", "user_view", ".png")

    # GrabFramebuffer reads whatever's already painted on screen -- the user's
    # real camera, draw style and background, unchanged -- as opposed to
    # FramebufferObject/CoinOffscreenRenderer, which re-render the scene (and
    # are what the throwaway offscreen views elsewhere in this file need, since
    # they're never actually painted). Only valid because this view IS visible.
    params = FreeCAD.ParamGet(_VIEW_PREF_PATH)
    prev_method = params.GetString("SavePicture", "")
    params.SetString("SavePicture", "GrabFramebuffer")
    try:
        # Passing only width (no height) -- the GrabFramebuffer backend scales
        # the captured framebuffer to this width and derives height from its
        # own aspect ratio, so the user's real window shape is preserved.
        view.saveImage(png_path, width)
    except Exception as exc:  # noqa: BLE001
        return f"Could not capture the active view: {exc!r}"
    finally:
        params.SetString("SavePicture", prev_method)

    return (
        "Captured exactly what the user currently sees in their 3D view, "
        f"saved to {png_path}."
    ), png_path


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
    import FreeCADGui

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

    # Re-apply the same object isolation as the capture we're zooming into, so
    # the crop stays visually consistent with it. Restored in the finally.
    keep_set = _last_capture.get("keep") or set()
    gui_doc = FreeCADGui.getDocument(doc.Name)
    prev_modified = getattr(gui_doc, "Modified", None)

    width = int(_last_capture.get("width") or 1280)
    height = int(_last_capture.get("height") or 960)

    blank = False
    saved = []
    saved_sel = []
    try:
        saved = _isolate_visibility(doc, keep_set)
        saved_sel = _suspend_selection(doc)  # drop selection highlight for the shot
        if subwindow is not None:
            subwindow.resize(width, height)
        try:
            view.setCamera(camera)  # reproduce EXACTLY what Claude last saw
        except Exception as exc:  # noqa: BLE001
            return f"Could not reproduce the last camera to crop from: {exc!r}"

        if not _crop_camera_frame(view, x1, y1, x2, y2, float(width) / float(height)):
            return (
                "Could not zoom into the requested region on this build "
                "(the last view isn't an orthographic camera)."
            )

        png_path = _artifact_path("captures", "crop", ".png")
        _save_view_png(view, png_path, width, height)
        blank = _looks_blank(png_path)
    finally:
        _restore_visibility(saved)
        _restore_selection(saved_sel)
        if prev_modified is not None:
            try:
                gui_doc.Modified = prev_modified
            except Exception:  # noqa: BLE001
                pass
        _close_offscreen_view(subwindow, prev_view)

    text = (
        f"Zoomed into ({x1:.2f},{y1:.2f})-({x2:.2f},{y2:.2f}) of the last view and "
        f"re-rendered that region at full resolution, saved to {png_path}."
    )
    if blank:
        text += (
            "\n\nWarning: that region came out empty -- nothing there in the last "
            "view. Pick a sub-rectangle over a visible part of it, or capture_view "
            "again to reframe."
        )
    return text, png_path


#: axis name -> index into (x, y, z), for cutaway's convenience axis mode.
_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def _resolve_clip_plane(args, doc, keep_names=None):
    """Build a Coin ``SbPlane`` for the cutaway from `args`.

    Two ways to specify it:
      - ``point`` [x,y,z] + ``normal`` [x,y,z]: an arbitrary plane; the half
        that is KEPT (drawn) is the side the normal points toward.
      - ``axis`` (x/y/z) + ``position`` (mm) + ``keep`` (low/high): a plane
        perpendicular to that axis. ``position`` defaults to the bbox midpoint
        on that axis of the SHOWN objects (``keep_names``, so a bare ``axis``
        just halves what's in the shot), falling back to the whole document;
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
        bbox = _document_bbox(doc, keep_names)
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
        "Like capture_view, this takes a REQUIRED 'objects' list of internal "
        "Names -- only those are shown (everything else is hidden for the shot) "
        "and the clip applies to just them; the user's real view is restored "
        "after.\n"
        "Set the camera angle exactly as capture_view does: a 'view' preset "
        "(iso/front/rear/top/bottom/left/right, default iso) OR 'azimuth'+"
        "'elevation' in degrees for a custom orbit. Optionally pass "
        "x_min/x_max/y_min/y_max/z_min/z_max (mm, same as capture_view) to crop "
        "the shot to a world-space region.\n"
        "Tip: aim the camera at the cut -- e.g. cut axis=x and view from the "
        "left/right, or cut axis=z and view top/bottom -- so you look squarely "
        "into the opened part. If a cut looks flat/empty/unchanged, change "
        "EXACTLY ONE of 'keep' or 'view' (not both -- flipping both together "
        "cancels out and lands back on the same unclipped-looking angle)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "objects": {
                "type": "array", "items": {"type": "string"}, "minItems": 1,
                "description": (
                    "REQUIRED. Internal Names (from get_objects -- NOT Labels) of "
                    "the object(s) to show and cut. Only these are made visible for "
                    "the shot; everything else in the document is hidden and the "
                    "clip applies to just them. Naming a container (App::Part/Group "
                    "or a PartDesign Body) shows its contents. The user's real view "
                    "is left untouched -- prior visibility is restored right after."
                ),
            },
            "axis": {"type": "string", "description": "Cut perpendicular to this axis: x, y, or z (the simple way to define the plane)."},
            "position": {"type": "number", "description": "Where along 'axis' to cut, in mm (default: the model's midpoint on that axis)."},
            "keep": {"type": "string", "description": "Which half of an 'axis' cut to keep: 'low' (smaller coordinate) or 'high' (default low)."},
            "point": {"type": "array", "items": {"type": "number"}, "description": "A point on the clip plane [x,y,z] in mm. Use with 'normal' for an arbitrary plane instead of 'axis'."},
            "normal": {"type": "array", "items": {"type": "number"}, "description": "Clip plane normal [x,y,z]; the kept (visible) half is the side it points toward. Use with 'point'."},
            "view": {"type": "string", "description": "Camera preset: iso/front/rear/top/bottom/left/right (default iso). Ignored when azimuth/elevation are given."},
            "azimuth": {"type": "number", "description": "Custom orbit angle around the vertical axis, degrees: 0=front, +90=right, 180=back, -90=left."},
            "elevation": {"type": "number", "description": "Custom orbit angle above/below eye level, degrees: 0=side-on, +90=top-down, -90=bottom-up."},
            **_EXTENT_SCHEMA_PROPS,
            "width": {"type": "integer", "description": "Image width px (default 1280)"},
            "height": {"type": "integer", "description": "Image height px (default 960)"},
        },
        "required": ["objects"],
        "additionalProperties": False,
    },
}


def _run_cutaway(args):
    import FreeCAD
    import FreeCADGui

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return "No active document."

    names = args.get("objects")
    if not names:
        return ("cutaway requires 'objects': a list of object Names to show "
                "(everything else is hidden for the shot). Call get_objects first.")
    for n in names:
        if doc.getObject(n) is None:
            return f"No object named '{n}'."
    keep_set = _visibility_keep_set(doc, names)

    plan, err = _resolve_camera_args(args)
    if err:
        return err

    try:
        plane, clip_desc, clip_normal, err = _resolve_clip_plane(args, doc, keep_set)
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

    # Toggling Visibility dirties the GUI document; snapshot the flag to restore.
    gui_doc = FreeCADGui.getDocument(doc.Name)
    prev_modified = getattr(gui_doc, "Modified", None)

    width = int(args.get("width", 1280))
    height = int(args.get("height", 960))
    png_path = _artifact_path("captures", "cutaway", ".png")
    extents = _extent_args(args)

    crop_warning = None
    measured = None
    degenerate_warning = None
    saved = []
    saved_sel = []
    try:
        # Show only the requested objects; the clip then applies to just them and
        # fitAll frames them. Restored in the finally below.
        saved = _isolate_visibility(doc, keep_set)
        saved_sel = _suspend_selection(doc)  # drop selection highlight for the shot
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
                if not _frame_camera_on_box(view, crop_box, float(width) / float(height)):
                    crop_warning = (
                        "Warning: could not frame the requested crop on this build -- "
                        "showing the full extent instead."
                    )
                    view.fitAll()
            else:
                crop_warning = (
                    "Warning: the document has no real geometry to crop against -- "
                    "showing the full extent instead."
                )

        # Direction is unchanged by fitAll, so this matches the saved image.
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

        _save_view_png(view, png_path, width, height)
    finally:
        _restore_visibility(saved)
        _restore_selection(saved_sel)
        if prev_modified is not None:
            try:
                gui_doc.Modified = prev_modified
            except Exception:  # noqa: BLE001
                pass
        _close_offscreen_view(subwindow, prev_view)

    text = f"Cutaway at {clip_desc}, saved to {png_path}."
    if measured is not None:
        meas_az, meas_el = measured
        text += f" Camera angle: azimuth {meas_az:.0f} deg, elevation {meas_el:.0f} deg."
    framed_extents = _extent_report(_document_bbox(doc, names=keep_set))
    if framed_extents:
        text += f" Shown geometry spans {framed_extents} (world coords)."
    text += (
        " The cut is hollow -- you're seeing the interior surfaces the clip "
        "exposed, not a filled cross-section."
    )
    if degenerate_warning:
        text += f"\n\n{degenerate_warning}"
    if crop_warning:
        text += f"\n\n{crop_warning}"
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


# --- Visibility isolation (capture_view / cutaway / crop_view) --------------
# The offscreen capture views render the DOCUMENT's own scene graph, and
# ViewObject.Visibility is document-level (shared by every view), not per-view.
# So "show only these objects for the shot" means toggling Visibility on the
# document -- which we save and restore in the tool's finally so the user's real
# view/document stays untouched (the GUI thread is blocked for the whole call,
# so nothing repaints mid-call and there's no flicker).

def _descendants(obj):
    """Everything nested under obj via .Group (Part/Group children, Body
    features), recursively."""
    out, seen, stack = [], set(), list(getattr(obj, "Group", None) or [])
    while stack:
        o = stack.pop()
        if o.Name in seen:
            continue
        seen.add(o.Name)
        out.append(o)
        stack.extend(getattr(o, "Group", None) or [])
    return out


def _ancestor_containers(obj):
    """The App::Part / Group / Body chain obj sits inside, so their group
    switches stay ON when a nested object is isolated."""
    out, cur, seen = [], obj, set()
    while cur is not None:
        parent = None
        for getter in ("getParentGeoFeatureGroup", "getParentGroup"):
            fn = getattr(cur, getter, None)
            if fn is not None:
                try:
                    parent = fn()
                except Exception:  # noqa: BLE001
                    parent = None
            if parent is not None:
                break
        if parent is None or parent.Name in seen:
            break
        seen.add(parent.Name)
        out.append(parent)
        cur = parent
    return out


def _visibility_keep_set(doc, requested_names):
    """Names that must end up VISIBLE for the shot: the requested objects, their
    container ancestors, and their CURRENTLY-visible descendants.

    Keeping only currently-visible descendants is what makes a PartDesign Body
    render correctly -- its tip feature is the visible one, so it's kept and
    shown; the intermediate features stay hidden (we don't force them True), so
    there are no overlapping solids. An App::Part keeps its curated child
    visibility the same way. (A naive "Visibility = (Name in requested)" would
    hide a Body's tip feature and render it blank -- hence the descendant walk.)
    """
    keep = set()
    for name in requested_names:
        obj = doc.getObject(name)
        if obj is None:
            continue  # caller has already error-checked; belt-and-suspenders
        keep.add(obj.Name)
        for anc in _ancestor_containers(obj):
            keep.add(anc.Name)
        for desc in _descendants(obj):
            vo = getattr(desc, "ViewObject", None)
            if vo is None:
                continue
            try:
                if bool(vo.Visibility):
                    keep.add(desc.Name)
            except Exception:  # noqa: BLE001
                pass
    return keep


def _isolate_visibility(doc, keep_names):
    """Force every object's ViewObject.Visibility to (Name in keep_names) and
    return [(view_object, prior_bool), ...] for _restore_visibility.

    Never raises: guards objects without a usable ViewObject and per-object set
    failures, and records the prior value BEFORE changing it so the restore is
    always exact.
    """
    keep, saved = set(keep_names), []
    for obj in doc.Objects:
        vo = getattr(obj, "ViewObject", None)
        if vo is None:
            continue
        try:
            prior = bool(vo.Visibility)
        except Exception:  # noqa: BLE001
            continue
        want = obj.Name in keep
        if prior == want:
            continue
        saved.append((vo, prior))
        try:
            vo.Visibility = want
        except Exception:  # noqa: BLE001
            pass  # couldn't set; prior already recorded so restore is a no-op
    return saved


def _restore_visibility(saved):
    for vo, prior in reversed(saved):
        try:
            vo.Visibility = prior
        except Exception:  # noqa: BLE001
            pass


def _suspend_selection(doc):
    """Clear `doc`'s 3D selection for an offscreen capture and return the saved
    selection for _restore_selection to put back.

    A selected object renders in the highlight colour (green), which would
    misrepresent its real appearance in the screenshot, so we drop the selection
    just for the render. Selection is transient Gui state (not a document
    property), so this doesn't dirty Modified; and because the whole tool call is
    one blocked GUI-thread event, the user's real view never repaints in between,
    so restoring it afterwards is invisible to them. Clear is scoped to `doc` so a
    selection in another open document is left untouched. Mirrors
    _isolate_visibility/_restore_visibility. Never raises.
    """
    import FreeCADGui

    try:
        saved = list(FreeCADGui.Selection.getSelectionEx(doc.Name))
    except Exception:  # noqa: BLE001
        return []
    if saved:
        try:
            FreeCADGui.Selection.clearSelection(doc.Name)
        except Exception:  # noqa: BLE001
            return []
    return saved


def _restore_selection(saved):
    """Re-add the selection cleared by _suspend_selection, sub-elements and all."""
    if not saved:
        return
    import FreeCADGui

    for sel in saved:
        try:
            subs = list(getattr(sel, "SubElementNames", None) or [])
            if subs:
                for sub in subs:
                    FreeCADGui.Selection.addSelection(sel.Object, sub)
            else:
                FreeCADGui.Selection.addSelection(sel.Object)
        except Exception:  # noqa: BLE001
            pass


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


TOOLS = {
    "get_objects": {"schema": _GET_OBJECTS_SCHEMA, "run": _run_get_objects},
    "get_selection": {"schema": _GET_SELECTION_SCHEMA, "run": _run_get_selection},
    "get_sketch": {"schema": _GET_SKETCH_SCHEMA, "run": _run_get_sketch},
    "view_sketch_svg": {"schema": _VIEW_SKETCH_SVG_SCHEMA, "run": _run_view_sketch_svg},
    "capture_view": {"schema": _CAPTURE_VIEW_SCHEMA, "run": _run_capture_view},
    "capture_user_view": {"schema": _CAPTURE_USER_VIEW_SCHEMA, "run": _run_capture_user_view},
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
