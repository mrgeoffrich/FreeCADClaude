"""Split a solid that is too big to print, and register the halves so they can
only go back together one way.

Two joints, because one does not cover the range:

    bisect_with_dowels()  round dowels in blind holes -- the default
    bisect_with_key()     one flat rectangular key -- for sections too thin for
                          a dowel plus a wall around it

Why the cut is always perpendicular to the build direction
----------------------------------------------------------
Both halves are printed with the cut face flat on the plate. That is the whole
point: the cut face becomes a perfect first layer, and any down-facing surface
that lay *in* the cut plane stops being an overhang. A cut on any other plane
leaves both halves needing support exactly where they already did. Choose
`height` with an overhang scan, not by eye.

Why separate dowels rather than integral pegs
---------------------------------------------
An earlier version grew pegs out of the lower half's cut face. That prints
badly: a short thin post standing proud of a large flat face is fragile to
handle, it is the first thing to catch a nozzle on a later layer, and any
elephant foot or stringing on it lands exactly where the fit has to be accurate.
Worse, it stops the cut face being flat, so the half cannot simply be laid down.

Separate dowels avoid all of that. Both halves get plain blind holes -- drilled
straight down from a flat face, the easiest feature there is to print -- and the
dowels print standing on end, where they come out round. They can also be
swapped for a cut length of steel rod, far stronger than anything printed at
this size.

Hole placement is automatic: the cut section is eroded inward by (hole radius +
margin) with makeOffset2D, so every candidate point is guaranteed a full hole
plus a wall of material around it, and holes stay clear of existing bores. The
points are then chosen by greedy farthest-point sampling, which spreads them to
the corners of the section and maximises resistance to twist.

Usage:
    from split_for_print import bisect_with_dowels
    bisect_with_dowels("Nudillo gancho", App.Vector(0, -1, 0), 18.0,
                       dowel_d=4.0, dowel_len=6.0, ndowels=3)
"""

import FreeCAD as App
import Part

#: The library index shows these, in this order -- the two entry points first,
#: then the geometry helpers, which stay importable because they are useful on
#: their own (halfspace_box and spread in particular).
__all__ = ["bisect_with_dowels", "bisect_with_key",
           "halfspace_box", "hole_candidates", "spread", "make_dowel"]

#: DIAMETRAL clearance, MEASURED not guessed. A project's usual snug-fit figure
#: (~0.15) is right for a printed part onto a BOUGHT pin -- a steel rod really is
#: 4.00 mm -- but for a PRINTED dowel in a PRINTED hole it vanishes: the hole
#: shrinks and a cylinder printed standing on end comes out slightly fat. At 0.15
#: the halves would not go together at all. A graded ladder (3.90/3.80/3.70/3.60
#: into 4.15 holes) printed on a Bambu P2S in PLA gave 3.90 -- 0.25 mm -- as the
#: winner: slides home under thumb pressure, no rocking. Re-ladder it for a
#: different printer or material rather than trusting this number blind.
CLEAR = 0.25

#: Extra hole depth PER SIDE, so the dowel ends up 2*RELIEF shorter than the two
#: holes combined and can never bottom out and hold the cut faces apart.
RELIEF = 0.5

#: 45 deg lead-in at each hole or slot mouth. The mouth sits IN the cut face,
#: which is the first layer of both halves, so elephant foot squeezes it
#: undersize and closes the lead-in. The chamfer gives the dowel something to
#: find even when the first layer has spread.
HOLE_CHAMFER = 0.5

#: Lead-in on each dowel or key end.
CHAMFER = 0.4


# ------------------------------------------------------------------ geometry

def halfspace_box(bb, axis, cut, pad=10.0):
    """Axis-aligned box covering everything with point.dot(axis) <= cut."""
    mn = [bb.XMin - pad, bb.YMin - pad, bb.ZMin - pad]
    mx = [bb.XMax + pad, bb.YMax + pad, bb.ZMax + pad]
    i = [abs(axis.x), abs(axis.y), abs(axis.z)].index(1.0)
    if (axis.x + axis.y + axis.z) > 0:
        mx[i] = cut
    else:
        mn[i] = -cut
    return Part.makeBox(mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2], App.Vector(*mn))


def hole_candidates(faces, need_margin):
    """Points on the section, each need_margin in from every edge and bore."""
    pts = []
    for f in faces:
        try:
            off = f.makeOffset2D(-need_margin)
        except Exception:
            continue                      # region too narrow for a dowel -- skip it
        for w in off.Wires:
            try:
                pts += list(w.discretize(Number=24))
            except Exception:
                pts += [v.Point for v in w.Vertexes]
    return pts


def spread(pts, n):
    """Greedy farthest-point sampling: n points as far apart as possible."""
    if not pts:
        return []
    chosen = [max(pts, key=lambda p: p.Length)]
    while len(chosen) < n and len(chosen) < len(pts):
        chosen.append(max(pts, key=lambda p: min((p - q).Length for q in chosen)))
    return chosen


def make_dowel(centre, axis, r, length, chamfer=CHAMFER):
    """A dowel centred on the cut plane, chamfered both ends for lead-in."""
    d = Part.makeCylinder(r, length, centre - axis * (length / 2.0), axis)
    circles = [e for e in d.Edges if len(e.Vertexes) <= 1]
    try:
        return d.makeChamfer(chamfer, circles)
    except Exception:
        return d                          # a nicety, not worth failing the split over


def _resolve(label, doc):
    doc = doc or App.ActiveDocument
    matches = [o for o in doc.Objects if o.Label == label]
    if not matches:
        raise ValueError("no object labelled %r in %s" % (label, doc.Name))
    return doc, matches[0]


def _section_faces(shape, axis, cut, tol=1e-6):
    """The planar faces lying IN the cut plane, largest first."""
    sec = [f for f in shape.Faces
           if f.Surface.TypeId == "Part::GeomPlane"
           and abs(f.Surface.Axis.dot(axis)) > 0.999
           and abs(f.CenterOfMass.dot(axis) - cut) < tol]
    sec.sort(key=lambda f: -f.Area)
    return sec


def _split(obj, axis, height):
    """(lower, upper, cut) -- the two halves and the plane coordinate."""
    s = obj.Shape
    cut = min(v.Point.dot(axis) for v in s.Vertexes) + height
    box = halfspace_box(s.BoundBox, axis, cut)
    lower, upper = s.common(box), s.cut(box)
    if len(lower.Solids) != 1 or len(upper.Solids) != 1:
        raise ValueError("split gave %d/%d solids -- the cut plane crosses a gap"
                         % (len(lower.Solids), len(upper.Solids)))
    return lower, upper, cut


def _add_halves(doc, label, lower, upper):
    for suffix, shp in (("lower", lower), ("upper", upper)):
        f = doc.addObject("Part::Feature", "half")
        f.Label = "%s %s" % (label, suffix)
        f.Shape = shp
        print("  %-24s vol %6.2f cm3  bbox %.1f x %.1f x %.1f mm"
              % (f.Label, shp.Volume / 1000, shp.BoundBox.XLength,
                 shp.BoundBox.YLength, shp.BoundBox.ZLength))


def _retire(obj):
    """Mark the original as not printed, leaving it in the document.

    Guarded: PrintDirection is this addon's own dynamic property, so a document
    that never had one set has no attribute to write.
    """
    if hasattr(obj, "PrintDirection"):
        obj.PrintDirection = "Not printed"


# -------------------------------------------------------------------- dowels

def bisect_with_dowels(label, axis, height, dowel_d=4.0, dowel_len=6.0,
                       margin=1.5, ndowels=3, clear=CLEAR, doc=None):
    """Split `label` at `height` along `axis`, joined by round dowels.

    Both halves get blind holes of (dowel_d + clear) diameter, chamfered at the
    mouth. The dowels are added as separate document objects so they land on the
    plate with everything else; the original is left in place, marked
    'Not printed'. Returns the list of dowel centres.

    `height` is measured from the shape's lowest extent ALONG `axis`, so the
    same number means the same place whichever way the part is oriented.

    ndowels=0 gives a plain glued butt joint. On a small or thin section there
    is no room for a dowel plus a wall around it, and a dowel too small to print
    well is worse than none -- it becomes the thing stopping the faces closing.
    For a section that is thin in one direction only, use bisect_with_key().
    """
    doc, obj = _resolve(label, doc)
    lower, upper, cut = _split(obj, axis, height)

    sec = _section_faces(lower, axis, cut)
    print("  section: %d face(s), total %.1f mm2, largest %.1f mm2"
          % (len(sec), sum(f.Area for f in sec), sec[0].Area if sec else 0.0))

    centres, hole_r, depth = [], (dowel_d + clear) / 2.0, dowel_len / 2.0 + RELIEF
    if not ndowels:
        print("  no dowels -- plain glued butt joint on %.1f mm2 of section"
              % sum(f.Area for f in sec))
    else:
        centres = spread(hole_candidates(sec, hole_r + margin), ndowels)
        if not centres:
            raise ValueError("no room for dowels -- reduce dowel_d or margin, "
                             "pass ndowels=0, or use bisect_with_key()")

    # Blind holes: down into the lower half, up into the upper half.
    down, up = [], []
    for c in centres:                     # empty when ndowels=0 -> cut() is a no-op
        down.append(Part.makeCylinder(hole_r, depth, c - axis * depth, axis))
        down.append(Part.makeCone(hole_r + HOLE_CHAMFER, hole_r, HOLE_CHAMFER,
                                  c, axis * -1))
        up.append(Part.makeCylinder(hole_r, depth, c, axis))
        up.append(Part.makeCone(hole_r + HOLE_CHAMFER, hole_r, HOLE_CHAMFER, c, axis))
    lower = lower.cut(Part.makeCompound(down))
    upper = upper.cut(Part.makeCompound(up))
    if not (lower.isValid() and upper.isValid()):
        raise ValueError("invalid shape after hole operations")
    if len(lower.Solids) != 1 or len(upper.Solids) != 1:
        raise ValueError("hole ops gave %d/%d solids -- a hole broke through a "
                         "thin wall" % (len(lower.Solids), len(upper.Solids)))

    _add_halves(doc, label, lower, upper)
    for i, c in enumerate(centres, 1):
        f = doc.addObject("Part::Feature", "dowel")
        f.Label = "Dowel %s %d" % (label.split()[0], i)
        f.Shape = make_dowel(c, axis, dowel_d / 2.0, dowel_len)
    if centres:
        print("  %d dowels d%.1f x %.1f mm; holes d%.2f x %.1f deep each side "
              "(%.1f mm total, dowel %.1f mm short), %.1f mm chamfered mouths"
              % (len(centres), dowel_d, dowel_len, dowel_d + clear, depth,
                 2 * depth, 2 * depth - dowel_len, HOLE_CHAMFER))

    _retire(obj)
    return centres


# ----------------------------------------------------------------------- key

def _box(centre, ext):
    """Axis-aligned box of extents ext=[dx,dy,dz] centred on `centre`."""
    return Part.makeBox(ext[0], ext[1], ext[2],
                        App.Vector(centre.x - ext[0] / 2.0,
                                   centre.y - ext[1] / 2.0,
                                   centre.z - ext[2] / 2.0))


def _rect_wire(centre, ext):
    """Rectangular wire: the face of _box with zero thickness on the flat axis."""
    i = [n for n in (0, 1, 2) if ext[n] == 0.0][0]
    j, k = [n for n in (0, 1, 2) if n != i]
    pts = []
    for sj, sk in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        c = [centre.x, centre.y, centre.z]
        c[j] += sj * ext[j] / 2.0
        c[k] += sk * ext[k] / 2.0
        pts.append(App.Vector(*c))
    return Part.makePolygon(pts + [pts[0]])


def _scan(face, i, const_ax, line_ax, coord_i, n):
    """Walk `const_ax` in n steps; at each, the widest run of material along line_ax.

    Returns [(coord on const_ax, (lo, hi) on line_ax), ...], skipping empty rows.
    One common() against a whole line per row, never a point-by-point isInside
    scan: isInside rebuilds a solid classifier on every call, so a sampled sweep
    of it freezes the GUI thread for minutes.
    """
    b = face.BoundBox
    lo_c, hi_c = [b.XMin, b.YMin, b.ZMin][const_ax], [b.XMax, b.YMax, b.ZMax][const_ax]
    lo_l, hi_l = [b.XMin, b.YMin, b.ZMin][line_ax], [b.XMax, b.YMax, b.ZMax][line_ax]
    rows = []
    for step in range(n):
        cc = lo_c + (hi_c - lo_c) * step / (n - 1.0)
        p0, p1 = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
        p0[i] = p1[i] = coord_i
        p0[const_ax] = p1[const_ax] = cc
        p0[line_ax], p1[line_ax] = lo_l - 1.0, hi_l + 1.0
        ed = face.common(Part.makeLine(App.Vector(*p0), App.Vector(*p1))).Edges
        iv = [(min(v.Point[line_ax] for v in e.Vertexes),
               max(v.Point[line_ax] for v in e.Vertexes)) for e in ed]
        if iv:
            rows.append((cc, max(iv, key=lambda t: t[1] - t[0])))
    return rows


def _longest(rows, thk):
    """Longest rectangle of thickness `thk` across contiguous scan rows.

    Returns (length, centre on the scanned axis, centre on the line axis) or None.
    """
    best = None
    for a in range(len(rows)):
        lo, hi = rows[a][1]
        for b in range(a, len(rows)):
            lo, hi = max(lo, rows[b][1][0]), min(hi, rows[b][1][1])
            if hi <= lo:
                break
            if rows[b][0] - rows[a][0] < thk:
                continue                      # not yet thick enough to hold the key
            cand = (hi - lo, (rows[a][0] + rows[b][0]) / 2.0, (lo + hi) / 2.0)
            if best is None or cand[0] > best[0]:
                best = cand
    return best


def bisect_with_key(label, axis, height, key_len, key_thk, key_depth,
                    margin=0.8, clear=CLEAR, doc=None):
    """Split `label`, joined by a flat rectangular KEY rather than round dowels.

    A round dowel needs a circle of material plus a wall all the way round it. On
    a thin section that does not fit -- and a dowel small enough to squeeze in
    prints badly, then becomes the thing holding the faces apart. A flat key
    needs only a slot, so it can run long in the section's generous direction
    while staying thin in its tight one. Being long, it also resists twist far
    better than a single dowel.

    key_len/key_thk are the KEY's cross-section; slots are cut `clear` larger on
    both. key_depth is the slot depth PER SIDE, and the key is made 1.0 mm
    shorter than the two slots combined so it can never bottom out. Slot mouths
    get the same 45 deg chamfer as the dowel holes, for the same elephant-foot
    reason. key_len is reduced automatically if the section has less room.

    Returns (key_shape, long_axis_index, thickness_axis_index).
    """
    doc, obj = _resolve(label, doc)
    lower, upper, cut = _split(obj, axis, height)

    sec = _section_faces(lower, axis, cut, tol=1e-4)
    if not sec:
        raise ValueError("no planar section face found in the cut plane")
    face = sec[0]
    print("  section: %.1f mm2" % face.Area)

    i = [abs(axis.x), abs(axis.y), abs(axis.z)].index(1.0)
    er = max(face.makeOffset2D(-margin).Faces, key=lambda f: f.Area)
    j, k = [n for n in (0, 1, 2) if n != i]

    # The eroded region's BOUNDING BOX is a bad guide to where a key fits: on a
    # lever the long direction is usually a thin arm no key will go in, while the
    # only generous patch is a short boss. (One real section was a T -- a 0.8 mm
    # arm up one axis, a 5.8 mm boss across the other.) So probe it with scan
    # lines and take the largest genuinely inscribed rectangle, both orientations.
    best = None
    for cand_la, cand_sa in ((j, k), (k, j)):
        rows = _scan(er, i, cand_sa, cand_la, face.CenterOfMass[i], 25)
        run = _longest(rows, key_thk + clear)
        if run and (best is None or run[0] > best[0]):
            best = (run[0], run[1], run[2], cand_la, cand_sa)
    if best is None or best[0] <= key_thk:
        raise ValueError("no room for a key of thickness %.2f -- reduce key_thk "
                         "or margin" % key_thk)
    avail, ctr_sa, ctr_la, la, sa = best

    key_len = min(key_len, avail - clear)
    c = App.Vector(0, 0, 0)
    c[i], c[la], c[sa] = face.CenterOfMass[i], ctr_la, ctr_sa
    ext = [0.0, 0.0, 0.0]
    ext[la], ext[sa] = key_len + clear, key_thk + clear

    foot = Part.Face(_rect_wire(c, ext))
    if foot.common(er).Area < foot.Area - 1e-6:
        raise ValueError("key footprint escapes the section")
    print("  key runs along %s (%.2f mm of room), thickness on %s"
          % ("XYZ"[la], avail, "XYZ"[sa]))
    print("  key %.2f x %.2f mm, slot %.2f x %.2f mm"
          % (key_len, key_thk, key_len + clear, key_thk + clear))

    # slot spans both sides of the plane; each half keeps only its own side
    slot = [_box(c, [ext[n] if n != i else 2 * key_depth for n in (0, 1, 2)])]
    for sgn in (1, -1):                                   # chamfered mouth each side
        big = list(ext)
        big[la] += 2 * HOLE_CHAMFER
        big[sa] += 2 * HOLE_CHAMFER
        cc = App.Vector(c.x, c.y, c.z)
        cc[i] = c[i] + sgn * HOLE_CHAMFER
        slot.append(Part.makeLoft([_rect_wire(c, big), _rect_wire(cc, ext)], True))
    tool = Part.makeCompound(slot)

    lower, upper = lower.cut(tool), upper.cut(tool)
    if not (lower.isValid() and upper.isValid()):
        raise ValueError("invalid shape after slotting")
    if len(lower.Solids) != 1 or len(upper.Solids) != 1:
        raise ValueError("slot ops gave %d/%d solids -- the slot broke through a "
                         "wall" % (len(lower.Solids), len(upper.Solids)))

    _add_halves(doc, label, lower, upper)

    kext = [0.0, 0.0, 0.0]
    kext[la], kext[sa] = key_len, key_thk
    kext[i] = 2 * key_depth - 1.0
    key = _box(c, kext)
    try:
        ends = [e for e in key.Edges
                if abs(e.CenterOfMass[i] - c[i]) > kext[i] / 2 - 1e-6]
        key = key.makeChamfer(CHAMFER, ends)
    except Exception:
        pass
    kf = doc.addObject("Part::Feature", "key")
    kf.Label = "Key %s" % label.split()[0]
    kf.Shape = key
    print("  key solid %.2f x %.2f x %.2f mm (slots %.1f deep each side, key %.1f short)"
          % (kext[0], kext[1], kext[2], key_depth, 2 * key_depth - kext[i]))

    _retire(obj)
    return key, la, sa
