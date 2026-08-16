#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""BREP export and the per-face map: freecad/freecadclaude/freecad_tools/
model_export.py.

The export half of the face-markup round trip, under a real FreeCAD: a known
multi-face primitive is exported and the returned dict is checked against the
live shape it came from -- the face count matches ``len(shape.Faces)``, the
face entries line up with ``Shape.Faces`` by the index that a later phase's
``"FaceN"`` is built from, the ``.brp`` file exists and is non-empty, and
``shape_hash`` changes after mutating the object and recomputing (that hash is
the whole staleness check of a later phase, so it has to actually move when
the geometry does). Nothing here re-proves Phase 1's correlation claim; a box
is enough.

The other half of the test is the read-only contract: exporting mutates
nothing in the document -- no object added or removed, nothing left touched --
and the module imports without FreeCAD at module level.

Runs under freecadcmd (needs an ABSOLUTE path -- given a relative one it
silently runs nothing and still exits 0):

    freecadcmd /abs/path/to/eval/test_model_export.py

freecadcmd suppresses a script's stdout when headless, so the report goes to
stderr instead.

Exit: 0 = all passed, 1 = a failure.
"""

import os
import shutil
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Import model_export directly by path, not through the freecad_tools package:
# the package's __init__ pulls in every tools_* module, while the module under
# test stands alone and must import with no FreeCAD on sys.modules. It comes
# first for the same reason.
sys.path.insert(0, os.path.join(_ROOT, "freecad", "freecadclaude", "freecad_tools"))

import model_export  # noqa: E402

import FreeCAD  # noqa: E402

_failures = []


def check(name, condition, detail=""):
    status = "ok  " if condition else "FAIL"
    line = f"  {status} {name}" + (f" -- {detail}" if detail and not condition else "")
    print(line, file=sys.stderr)
    if not condition:
        _failures.append(name)


def _near(actual, expected, tol=1e-9):
    return len(actual) == len(expected) and all(
        abs(a - b) <= tol for a, b in zip(actual, expected)
    )


def _unit(v):
    return (v[0] ** 2 + v[1] ** 2 + v[2] ** 2) ** 0.5


print("model_export", file=sys.stderr)

doc = FreeCAD.newDocument("ModelExport")
box = doc.addObject("Part::Box", "Box")
box.Length, box.Width, box.Height = 20, 30, 10
doc.recompute()

out_dir = tempfile.mkdtemp(prefix="fcc-model-export-")
try:
    result = model_export.export_brep([box], out_dir)

    check("the export dict is the documented shape", set(result) == {"objects"}
          and set(result["objects"]) == {"Box"}, repr(result))
    entry = result["objects"]["Box"]
    check("the path names <out_dir>/<ObjectName>.brp",
          entry["path"] == os.path.join(out_dir, "Box.brp"), entry["path"])
    check("...as an absolute path", os.path.isabs(entry["path"]), entry["path"])
    check("the .brp file exists and is non-empty",
          os.path.isfile(entry["path"]) and os.path.getsize(entry["path"]) > 0,
          entry["path"])
    check("the shape hash is an int", isinstance(entry["shape_hash"], int),
          repr(entry["shape_hash"]))

    faces = entry["faces"]
    live = box.Shape.Faces
    check("face count matches len(shape.Faces)", len(faces) == len(live) == 6,
          f"{len(faces)} vs {len(live)}")
    check("face keys are the 0-based indices, in order",
          list(faces) == [str(i) for i in range(len(live))], list(faces))
    for index, face in enumerate(live):
        name = f"face {index}"
        data = faces[str(index)]
        check(f"{name} has centroid/normal/area",
              set(data) == {"centroid", "normal", "area"}, repr(data))
        check(f"{name} centroid is the face's CenterOfMass",
              _near(data["centroid"],
                    [face.CenterOfMass.x, face.CenterOfMass.y, face.CenterOfMass.z]),
              repr(data["centroid"]))
        check(f"{name} area is the face's Area", data["area"] == face.Area,
              f"{data['area']} vs {face.Area}")
        # A box face is a plane: its normal is unit length and axis-aligned,
        # which is an independent fact the map can be checked against without
        # re-running the same code path that built it.
        normal = data["normal"]
        check(f"{name} normal is unit length", abs(_unit(normal) - 1.0) < 1e-9,
              repr(normal))
        axis_aligned = sorted(abs(v) for v in normal)
        check(f"{name} normal is axis-aligned (a box face)",
              _near(axis_aligned, [0.0, 0.0, 1.0], 1e-9), repr(normal))

    # The hash is the staleness check a later phase's read_model_markup runs,
    # so it must be stable across identical exports and move when the geometry
    # does.
    again = model_export.export_brep([box], out_dir)
    check("an identical re-export keeps the same hash",
          again["objects"]["Box"]["shape_hash"] == entry["shape_hash"])

    box.Length = 40
    doc.recompute()
    changed = model_export.export_brep([box], out_dir)
    check("mutating the object changes shape_hash",
          changed["objects"]["Box"]["shape_hash"] != entry["shape_hash"],
          f"{entry['shape_hash']} -> {changed['objects']['Box']['shape_hash']}")

    # -- several objects, one call --------------------------------------
    cylinder = doc.addObject("Part::Cylinder", "Cylinder")
    cylinder.Radius, cylinder.Height = 5, 12
    doc.recompute()
    multi = model_export.export_brep([box, cylinder], out_dir)
    check("one entry per object", set(multi["objects"]) == {"Box", "Cylinder"},
          repr(sorted(multi["objects"])))
    check("each object has its own .brp",
          all(os.path.isfile(multi["objects"][name]["path"])
              and os.path.getsize(multi["objects"][name]["path"]) > 0
              for name in ("Box", "Cylinder")))
    check("the cylinder's face count matches its shape",
          len(multi["objects"]["Cylinder"]["faces"])
          == len(cylinder.Shape.Faces) == 3,
          len(multi["objects"]["Cylinder"]["faces"]))
    check("a curved face gets a real normal",
          _unit(multi["objects"]["Cylinder"]["faces"]["2"]["normal"]) - 1.0 < 1e-9,
          multi["objects"]["Cylinder"]["faces"]["2"]["normal"])

    # -- an object with no shape is named, not silently dropped ---------
    group = doc.addObject("App::DocumentObjectGroup", "Group")
    doc.recompute()
    raised = None
    try:
        model_export.export_brep([box, group], out_dir)
    except ValueError as exc:
        raised = exc
    check("an object with no shape raises, naming it",
          raised is not None and "Group" in str(raised), repr(raised))

    # -- the read-only contract -----------------------------------------
    # Exporting must mutate nothing: no object added or removed, nothing left
    # touched. The baseline is captured after all of the test's own mutations
    # (the dimension change and the added objects), so only what the export
    # itself does can show up here.
    baseline_names = [o.Name for o in doc.Objects]
    baseline_touched = sorted(o.Name for o in doc.Objects if "Touched" in o.State)
    model_export.export_brep([box, cylinder], out_dir)
    check("no objects were added or removed",
          [o.Name for o in doc.Objects] == baseline_names,
          repr([o.Name for o in doc.Objects]))
    check("nothing was left touched",
          sorted(o.Name for o in doc.Objects if "Touched" in o.State)
          == baseline_touched,
          repr(sorted(o.Name for o in doc.Objects if "Touched" in o.State)))
    check("the export wrote nothing outside out_dir",
          sorted(os.listdir(out_dir))
          == ["Box.brp", "Cylinder.brp"],
          repr(sorted(os.listdir(out_dir))))
finally:
    shutil.rmtree(out_dir, ignore_errors=True)

FreeCAD.closeDocument(doc.Name)

print(file=sys.stderr)
if _failures:
    print(f"FAIL: {len(_failures)} check(s) failed: {', '.join(_failures)}",
          file=sys.stderr)
    sys.exit(1)
print("PASS", file=sys.stderr)
sys.exit(0)
