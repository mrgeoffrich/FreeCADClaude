# SPDX-License-Identifier: LGPL-2.1-or-later
"""SVG helpers: framing a TechDraw projection, and cropping a flat export.

Used by view_sketch_svg (both its flat-sketch and 3D-projection paths).
"""


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
