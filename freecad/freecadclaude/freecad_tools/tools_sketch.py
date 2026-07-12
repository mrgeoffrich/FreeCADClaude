# SPDX-License-Identifier: LGPL-2.1-or-later
"""get_sketch and view_sketch_svg -- reading a sketch's exact structure.

Every Sketcher mutation is addressed by GeoId: moveGeometry(geoId, posId, ...),
setDatum(constraintIndex, value), Constraint('Symmetric', geoId, posId, ...).
Nothing else in this tool set exposes GeoIds -- get_objects gives a bounding box
and view_sketch_svg's exported paths are merged, unlabelled wires -- so without
get_sketch the only way to learn a sketch's structure is a pile of exploratory
run_python dumps, each one a user approval.
"""

from .diagnostics import _solver_constraint_indices
from .geometry import (
    _EXTENT_SCHEMA_PROPS,
    _PROJECTION_DIRS,
    _crop_bbox,
    _extent_args,
)
from .gui_state import _active_edit_object, _is_open_in_editor
from .session import _artifact_path
from .svg import (
    _flat_crop_svg,
    _projected_crop_viewbox,
    _projection_degeneracy_warning,
    _svg_fragment_bounds,
    _wrap_svg_fragment,
)

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
        # Whether the user is sitting in this sketch right now. Without it, a
        # no-'name' call can't tell "the user has this open in the Sketcher" from
        # "the document just happens to have one sketch".
        "open_in_editor": _is_open_in_editor(sk),
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
    editing = _active_edit_object()
    if editing is not None and getattr(editing, "TypeId", "") == "Sketcher::SketchObject":
        return editing, None

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
