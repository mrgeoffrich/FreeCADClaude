"""Lay oriented shapes out side by side on a build plate, and say what did not fit.

Scope, and what NOT to use this for
-----------------------------------
This is the packing step ONLY. The `export` tool already does everything around
it -- it rotates each part onto its recorded PrintDirection, drops it onto the
plate and writes the parts as one multi-object 3MF, meshing through a scratch
document so nothing the user owns is touched. Reach for `export` first. This
module exists for the one thing it does not do: deciding WHERE on the plate each
part goes, and reporting what will not fit at all.

(The script this was lifted from re-implemented the rotate/mesh/export path by
hand, not knowing the tool was there. It was ~120 lines to arrive back at the
same 3MF. Check the tool list before rebuilding a tool.)

Shelf packing, not a true 2D bin pack: sort by footprint depth, fill rows across
X, start a new row when the current one is full. It is a few percent off optimal
and takes no time to run, which is the right trade -- the slicer will let the
user nudge parts anyway, and a plate that is 5% under-packed still prints.

Usage:
    from plate_pack import pack
    placed, overflow = pack(shapes, plate_x=250, plate_y=250, gap=5)

`shapes` is [(label, Part.Shape), ...] already rotated into print orientation
and sitting at the origin -- which is exactly what you have mid-way through an
export. Shapes are translated IN PLACE, so pass copies unless you mean it.
"""

import FreeCAD as App

#: A Bambu 256^3 plate with ~3 mm of margin each side. Every printer differs --
#: pass the real numbers rather than trusting these.
PLATE_X, PLATE_Y = 250.0, 250.0

#: Between parts. Enough that a brim or a stray skirt on one does not fuse to
#: its neighbour, and enough to get a scraper in.
GAP = 5.0


def to_origin(shapes):
    """Move each shape so its bounding box corner sits at the origin, on Z=0.

    Packing assumes it. Returns the same list for chaining.
    """
    for _, s in shapes:
        b = s.BoundBox
        s.translate(App.Vector(-b.XMin, -b.YMin, -b.ZMin))
    return shapes


def pack(shapes, plate_x=PLATE_X, plate_y=PLATE_Y, gap=GAP):
    """Shelf-pack (label, shape) pairs onto the plate; returns (placed, overflow).

    Deepest footprint first, filling rows across X. `placed` is the subset that
    fitted, translated into position; `overflow` is the labels of those that did
    not -- returned rather than raised, because the usual answer to an overflow
    is a second plate, not a failed export.
    """
    ordered = sorted(shapes, key=lambda t: -t[1].BoundBox.YLength)
    x = y = row_depth = 0.0
    placed, overflow = [], []
    for label, s in ordered:
        w, d = s.BoundBox.XLength, s.BoundBox.YLength
        if x + w > plate_x and x > 0:          # start a new row
            x, y = 0.0, y + row_depth + gap
            row_depth = 0.0
        if y + d > plate_y or w > plate_x:
            overflow.append(label)
            continue
        s.translate(App.Vector(x, y, 0))
        placed.append((label, s))
        x += w + gap
        row_depth = max(row_depth, d)
    return placed, overflow


def report(placed, overflow):
    """A printable summary: footprint used, tallest part, and per-part positions."""
    if not placed:
        return "Nothing fitted on the plate." + _overflow_line(overflow)
    lines = ["%d parts, footprint %.1f x %.1f mm, tallest %.1f mm" % (
        len(placed),
        max(s.BoundBox.XMax for _, s in placed),
        max(s.BoundBox.YMax for _, s in placed),
        max(s.BoundBox.ZMax for _, s in placed))]
    for label, s in placed:
        b = s.BoundBox
        lines.append("  %-24s at (%6.1f, %6.1f)  %5.1f x %5.1f x %5.1f mm"
                     % (label, b.XMin, b.YMin, b.XLength, b.YLength, b.ZLength))
    return "\n".join(lines) + _overflow_line(overflow)


def _overflow_line(overflow):
    if not overflow:
        return ""
    return "\nDID NOT FIT: " + ", ".join(overflow)
