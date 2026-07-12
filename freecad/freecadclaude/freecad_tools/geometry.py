# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bounding boxes and world-space crop extents, shared by the view tools."""


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
