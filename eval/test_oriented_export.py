#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Print-direction rotation: the geometry that comes out, and the document that
must not change.

``print_export.oriented_export`` stands each part up the way it prints before
writing the plate. A sign error or an axis swap there is invisible everywhere
else -- the export succeeds, the file opens, the slicer slices, and the part
comes off the printer built on the wrong face. So the assertion is the bounding
box of the vertices that actually landed in the 3MF, computed here from the
file, not read back off any tool that might share the same mistake.

The asymmetric cases are the ones worth having. A 60x10x10 bar at
"+X up" must come back 10x10x60: any rotation that isn't about the right axis
changes that triple. A bounding box cannot see everything, though -- a rotation
and its inverse aim the same local axis at the vertical, so both give 10x10x60
while one of them stands the part on the wrong end. That is what the two tapered
parts are for: which end their wide vertices are at is the only thing that
separates the rotation from its inverse, or a flip from doing nothing.

The other half is that none of it reaches the user's document. Rotating the real
objects would mutate placements the user owns and recompute everything
downstream, so the whole point of the scratch document is that the document
afterwards is the one from before: same objects, same placements, nothing left
touched, and no scratch document still open.

    /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd /abs/path/to/eval/test_oriented_export.py

(freecadcmd needs an ABSOLUTE path -- given a relative one it silently runs
nothing and still exits 0.)

Exit: 0 = all passed, 1 = a failure.
"""

import os
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The tools package as a top-level `freecad_tools`: FreeCAD owns the `freecad`
# namespace package in its own installation and a repo checkout can't shadow it.
sys.path.insert(0, os.path.join(_ROOT, "freecad", "freecadclaude"))

import FreeCAD  # noqa: E402
import Part  # noqa: E402

from freecad_tools import print_export  # noqa: E402
from freecad_tools.print_meta import (  # noqa: E402
    CUSTOM,
    NOT_PRINTED,
    NOT_SET,
    set_direction,
    up_vector,
)

#: 3MF core spec namespace, as FreeCAD writes it into 3D/3dmodel.model.
_NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"

_failures = []


def check(name, condition, detail=""):
    status = "ok  " if condition else "FAIL"
    print(f"  {status} {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(name)


def _objects(path):
    """The ``<object>`` elements of the 3MF at `path`, in file order -- which is
    the order they were handed to Mesh.export, and the only way to match them to
    the report. FreeCAD writes no name onto a 3MF object."""
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
    return list(root.iter(_NS + "object"))


def _vertices(element):
    return [(float(v.get("x")), float(v.get("y")), float(v.get("z")))
            for v in element.iter(_NS + "vertex")]


def _boxes(path):
    """``(xmin, xmax, ymin, ymax, zmin, zmax)`` per object, off the vertices
    themselves. Every ``<item>`` oriented_export writes carries the identity
    transform, so an object's own vertices are its plate coordinates."""
    boxes = []
    for element in _objects(path):
        verts = _vertices(element)
        boxes.append((min(v[0] for v in verts), max(v[0] for v in verts),
                      min(v[1] for v in verts), max(v[1] for v in verts),
                      min(v[2] for v in verts), max(v[2] for v in verts)))
    return boxes


def _size(box):
    return (box[1] - box[0], box[3] - box[2], box[5] - box[4])


def _near(actual, expected, tol=0.01):
    return all(abs(a - b) <= tol for a, b in zip(actual, expected))


def _face_span(element, at_top):
    """How wide the object is in X across its topmost (or bottommost) vertices.

    The measurement a bounding box cannot make: a cone is 16x16x20 whichever way
    up it is, and only the width at one end says which end is which.
    """
    verts = _vertices(element)
    z_min = min(v[2] for v in verts)
    z_max = max(v[2] for v in verts)
    band = 0.05 * (z_max - z_min)
    face = [v for v in verts
            if (v[2] >= z_max - band if at_top else v[2] <= z_min + band)]
    return max(v[0] for v in face) - min(v[0] for v in face)


# -- what a recorded direction means as a vector ----------------------------
print("up_vector")

doc = FreeCAD.newDocument("Oriented")
probe = doc.addObject("Part::Box", "Probe")
doc.recompute()

check("no property at all reads as Not set", up_vector(probe) == (NOT_SET, None),
      repr(up_vector(probe)))
set_direction(probe, NOT_PRINTED)
check("Not printed has no axis", up_vector(probe) == (NOT_PRINTED, None),
      repr(up_vector(probe)))
set_direction(probe, NOT_SET)
check("Not set has no axis", up_vector(probe) == (NOT_SET, None), repr(up_vector(probe)))
set_direction(probe, "+Z up")
check("+Z up is world up", up_vector(probe) == ("+Z up", (0.0, 0.0, 1.0)),
      repr(up_vector(probe)))
set_direction(probe, "-Z up")
check("-Z up is its negation", up_vector(probe) == ("-Z up", (0.0, 0.0, -1.0)),
      repr(up_vector(probe)))
# Written non-unit: set_direction normalises on write, and up_vector must not
# hand a caller a vector it then has to normalise a second time.
set_direction(probe, CUSTOM, [0, -3, 0])
direction, up = up_vector(probe)
check("Custom reads PrintDirectionCustom, normalised",
      direction == CUSTOM and _near(up, (0.0, -1.0, 0.0), 1e-9), repr((direction, up)))
doc.removeObject(probe.Name)

# -- the plate ---------------------------------------------------------------
print("the exported plate")

bar = doc.addObject("Part::Box", "Bar")
bar.Length, bar.Width, bar.Height = 60, 10, 10
set_direction(bar, "+X up")

# Wide end at local z=0. Its bounding box is the same both ways up, so this is
# the part that catches a rotation which is 180 degrees out.
cone = doc.addObject("Part::Cone", "Cone")
cone.Radius1, cone.Radius2, cone.Height = 8, 2, 20
set_direction(cone, "-Z up")

plain = doc.addObject("Part::Box", "Plain")
plain.Length, plain.Width, plain.Height = 12, 8, 4
set_direction(plain, "+Z up")

undecided = doc.addObject("Part::Box", "Undecided")
undecided.Length, undecided.Width, undecided.Height = 6, 6, 6

jig = doc.addObject("Part::Box", "Jig")
jig.Length, jig.Width, jig.Height = 30, 30, 30
set_direction(jig, NOT_PRINTED)

# Modelled standing up (60 along local Z) but placed on its side. "+Z up" means
# the part as modelled, so it has to come back standing up -- which it only does
# if the local axis is carried through the Placement before being aimed at world +Z.
placed = doc.addObject("Part::Box", "Placed")
placed.Length, placed.Width, placed.Height = 10, 10, 60
placed.Placement = FreeCAD.Placement(FreeCAD.Vector(0, 0, 0),
                                     FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 90))
set_direction(placed, "+Z up")

# A Custom vector no enum value could have supplied: if PrintDirectionCustom is
# ignored, nothing rotates and the 40 stays on Y.
tilted = doc.addObject("Part::Box", "Tilted")
tilted.Length, tilted.Width, tilted.Height = 10, 40, 10
set_direction(tilted, CUSTOM, [0, -3, 0])

# A cone lying along local +X, wide end at the local origin. This is the part
# that separates the rotation from its inverse: both aim the local +X axis at the
# vertical, so the bounding box is 16x16x20 either way, but only the right one
# puts the narrow end at the top. The bar cannot tell them apart -- a box is
# symmetric about its own axis -- and neither can the cone above, since a "-Z up"
# flip is 180 degrees in both directions.
taper = doc.addObject("Part::Feature", "Taper")
taper.Shape = Part.makeCone(8, 2, 20, FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0))
set_direction(taper, "+X up")

doc.recompute()

parts = [bar, cone, plain, undecided, jig, placed, tilted, taper]
before_names = [o.Name for o in doc.Objects]
before_placements = {o.Name: FreeCAD.Placement(o.Placement) for o in doc.Objects}
before_touched = sorted(o.Name for o in doc.Objects if "Touched" in o.State)
before_docs = sorted(FreeCAD.listDocuments())

out_dir = tempfile.mkdtemp(prefix="fcc-oriented-")
try:
    path = os.path.join(out_dir, "plate.3mf")
    report = print_export.oriented_export(parts, path)

    check("the plate was written", os.path.isfile(path) and os.path.getsize(path) > 0)
    check("the Not printed part is off the plate", report["omitted"] == ["Jig"],
          repr(report["omitted"]))
    check("the undecided part is named", report["not_set"] == ["Undecided"],
          repr(report["not_set"]))
    check("...and still exported",
          "Undecided" in [e["name"] for e in report["exported"]],
          repr(report["exported"]))
    check("nothing was skipped", report["skipped"] == [], repr(report["skipped"]))
    check("the deviation defaults to FreeCAD's own", report["deviation"] == 0.1,
          repr(report["deviation"]))

    boxes = _boxes(path)
    check("seven objects on the plate, the eighth left off",
          len(boxes) == 7 and len(report["exported"]) == 7,
          f"{len(boxes)} in the file, {len(report['exported'])} reported")

    # The report is matched to the file by index, which is the contract the tool
    # that formats this depends on -- so check the two agree object by object.
    expected = [
        ("Bar", (10.0, 10.0, 60.0), True, 0.01),
        ("Cone", (16.0, 16.0, 20.0), True, 0.6),
        ("Plain", (12.0, 8.0, 4.0), False, 0.01),
        ("Undecided", (6.0, 6.0, 6.0), False, 0.01),
        ("Placed", (10.0, 10.0, 60.0), True, 0.01),
        ("Tilted", (10.0, 10.0, 40.0), True, 0.01),
        ("Taper", (16.0, 16.0, 20.0), True, 0.6),
    ]
    for index, (name, size, rotated, tol) in enumerate(expected):
        entry = report["exported"][index]
        actual = _size(boxes[index])
        check(f"{name} is object {index + 1} in the file", entry["name"] == name,
              repr(entry))
        check(f"...{'rotated' if rotated else 'not rotated'}",
              entry["rotated"] is rotated, repr(entry))
        check(f"...and measures {size[0]:g}x{size[1]:g}x{size[2]:g} in the 3MF",
              _near(actual, size, tol),
              f"got {tuple(round(v, 3) for v in actual)}")
        check("...with the report's size_mm agreeing",
              _near(entry["size_mm"], actual, 0.01),
              f"{entry['size_mm']} vs {tuple(round(v, 3) for v in actual)}")
        # Stacked at the origin: layout is the slicer's job, and it needs them
        # all starting from the same corner.
        check("...sitting on the plate at the origin",
              _near((boxes[index][0], boxes[index][2], boxes[index][4]),
                    (0.0, 0.0, 0.0), 1e-6),
              repr(boxes[index][:6]))

    # Same box either way up, so only the ends distinguish them.
    cone_element = _objects(path)[1]
    check("the -Z up cone stands on its narrow end",
          _face_span(cone_element, at_top=False) <= 5.0,
          f"bottom span {_face_span(cone_element, at_top=False):.3f}")
    check("...with its wide end at the top",
          _face_span(cone_element, at_top=True) >= 15.0,
          f"top span {_face_span(cone_element, at_top=True):.3f}")

    # The rotation, not its inverse: both stand the taper on end, and only one
    # puts the end that was at local +X at the top.
    taper_element = _objects(path)[6]
    check("the +X up taper stands on its wide end",
          _face_span(taper_element, at_top=False) >= 15.0,
          f"bottom span {_face_span(taper_element, at_top=False):.3f}")
    check("...with its narrow end at the top",
          _face_span(taper_element, at_top=True) <= 5.0,
          f"top span {_face_span(taper_element, at_top=True):.3f}")

    # -- the user's document, afterwards ---------------------------------
    print("the document is untouched")

    check("no objects were added or removed",
          [o.Name for o in doc.Objects] == before_names,
          repr([o.Name for o in doc.Objects]))
    moved = [name for name, placement in before_placements.items()
             if str(doc.getObject(name).Placement) != str(placement)]
    check("every Placement is identical", moved == [], repr(moved))
    check("nothing was left touched",
          sorted(o.Name for o in doc.Objects if "Touched" in o.State) == before_touched,
          repr(sorted(o.Name for o in doc.Objects if "Touched" in o.State)))
    check("the scratch document was closed",
          sorted(FreeCAD.listDocuments()) == before_docs,
          repr(sorted(FreeCAD.listDocuments())))
    check("...and the user's document is active again",
          FreeCAD.ActiveDocument is not None and FreeCAD.ActiveDocument.Name == doc.Name,
          repr(FreeCAD.ActiveDocument))

    # -- orient=False ----------------------------------------------------
    print("orient=False")

    as_modelled = os.path.join(out_dir, "as_modelled.3mf")
    plain_report = print_export.oriented_export(parts, as_modelled, orient=False)
    flat = _boxes(as_modelled)
    check("the bar goes out as modelled", _near(_size(flat[0]), (60.0, 10.0, 10.0)),
          repr(tuple(round(v, 3) for v in _size(flat[0]))))
    check("nothing reports a rotation",
          not any(e["rotated"] for e in plain_report["exported"]),
          repr(plain_report["exported"]))
    check("...and nothing is moved onto the plate either",
          _near((flat[4][2],), (-60.0,), 0.01), repr(flat[4]))
    # Whether a part is printed at all is a separate decision from which way up.
    check("the Not printed part is still off the plate",
          plain_report["omitted"] == ["Jig"] and len(flat) == 7,
          repr(plain_report["omitted"]))

    # -- deviation -------------------------------------------------------
    print("deviation")

    coarse = os.path.join(out_dir, "coarse.3mf")
    fine = os.path.join(out_dir, "fine.3mf")
    print_export.oriented_export([cone], coarse, deviation=0.1)
    print_export.oriented_export([cone], fine, deviation=0.005)
    coarse_facets = len(list(_objects(coarse)[0].iter(_NS + "triangle")))
    fine_facets = len(list(_objects(fine)[0].iter(_NS + "triangle")))
    check("a finer deviation tessellates finer", fine_facets > coarse_facets * 2,
          f"{coarse_facets} facets at 0.1, {fine_facets} at 0.005")
    check("a nonsense deviation falls back to the default",
          print_export.oriented_export([cone], coarse, deviation=0)["deviation"] == 0.1)

    # -- nothing to write ------------------------------------------------
    print("an empty plate")

    empty = os.path.join(out_dir, "empty.3mf")
    empty_report = print_export.oriented_export([jig], empty)
    check("a plate of nothing but Not printed writes no file",
          empty_report["exported"] == [] and not os.path.exists(empty),
          repr(empty_report))
    check("...and still closes the scratch document",
          sorted(FreeCAD.listDocuments()) == before_docs,
          repr(sorted(FreeCAD.listDocuments())))
finally:
    shutil.rmtree(out_dir, ignore_errors=True)


# -- the teardown, on the failing path --------------------------------------
print("a failure mid-export")


class _Exploding:
    """An object whose Shape read raises, standing in for anything that can fail
    partway through. What is under test is the teardown, not the failure."""

    Name = "Exploding"
    Label = "Exploding"

    @property
    def Shape(self):
        raise RuntimeError("shape read failed")


raised = None
try:
    print_export.oriented_export([bar, _Exploding()], os.path.join(tempfile.gettempdir(),
                                                                   "unreached.3mf"))
except RuntimeError as exc:
    raised = exc
check("the failure reaches the caller", raised is not None, repr(raised))
check("the scratch document is closed anyway",
      sorted(FreeCAD.listDocuments()) == before_docs,
      repr(sorted(FreeCAD.listDocuments())))
check("...and the user's document is active again",
      FreeCAD.ActiveDocument is not None and FreeCAD.ActiveDocument.Name == doc.Name,
      repr(FreeCAD.ActiveDocument))

FreeCAD.closeDocument(doc.Name)

print()
if _failures:
    print(f"FAIL: {len(_failures)} check(s) failed: {', '.join(_failures)}")
else:
    print("PASS")
# freecadcmd tears the process down on SystemExit WITHOUT flushing, so do it
# here or the entire report above is discarded and the run looks like it printed
# nothing at all.
sys.stdout.flush()
sys.exit(1 if _failures else 0)
