#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""3MF export, and the one property of it that a slicer depends on.

``export(path="foo.3mf")`` falls through ``_run_export``'s final branch to
``Mesh.export``, which writes each object handed to it as its own ``<object>``
with a matching ``<item>`` on the build plate. That separation is the whole
reason 3MF is worth naming in the schema: a slicer places the items as distinct
parts, where an STL of the same two objects would fuse them into one shape.

It is FreeCAD's behaviour rather than ours, which is exactly why it is pinned
here -- it can change under us, silently, and a slice of a fused plate looks
plausible until someone tries to move one part. The schema wording is checked
alongside it, since a description that never mentions 3MF is the same as not
supporting it: Claude has no other way to learn the format is available.

    /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd /abs/path/to/eval/test_export_3mf.py

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

from freecad_tools import tools_export  # noqa: E402

#: 3MF core spec namespace, as FreeCAD writes it into 3D/3dmodel.model.
_NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"

#: The one part of the container the geometry lives in.
_MODEL_PART = "3D/3dmodel.model"

_failures = []


def check(name, condition, detail=""):
    status = "ok  " if condition else "FAIL"
    print(f"  {status} {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(name)


def _model_root(path):
    """The parsed 3D/3dmodel.model out of the 3MF at `path`."""
    with zipfile.ZipFile(path) as archive:
        return ET.fromstring(archive.read(_MODEL_PART))


# -- the schema, which is the whole of the M1 change ------------------------
print("export schema")

description = tools_export._EXPORT_SCHEMA["description"]
check("names 3MF", "3MF" in description and ".3mf" in description, description)
check(
    "says it keeps each object separate",
    "separate" in description.lower(),
    description,
)
check("says which formats 'format' takes", "3mf" in
      tools_export._EXPORT_SCHEMA["inputSchema"]["properties"]["format"]["description"])

# -- two objects out, two objects in the file -------------------------------
print("two objects to one 3MF")

doc = FreeCAD.newDocument("Export3mf")
box = doc.addObject("Part::Box", "Box")
box.Length, box.Width, box.Height = 20, 10, 5
cylinder = doc.addObject("Part::Cylinder", "Cylinder")
cylinder.Radius, cylinder.Height = 6, 12
doc.recompute()

out_dir = tempfile.mkdtemp(prefix="fcc-3mf-")
try:
    path = os.path.join(out_dir, "two.3mf")
    # Through the tool, not through Mesh.export directly: that the .3mf
    # extension reaches the mesh branch at all is half of what is being pinned.
    result = tools_export._run_export({"path": path, "names": ["Box", "Cylinder"]})
    check("the tool reports a 3MF export", "3MF" in result, result)
    check("...naming the path it wrote", path in result, result)
    check("the file exists and is not empty",
          os.path.isfile(path) and os.path.getsize(path) > 0)

    with zipfile.ZipFile(path) as archive:
        entries = archive.namelist()
    check(f"the container holds {_MODEL_PART}", _MODEL_PART in entries, str(entries))

    root = _model_root(path)
    objects = list(root.iter(_NS + "object"))
    items = list(root.iter(_NS + "item"))
    check("two <object> elements", len(objects) == 2, f"{len(objects)}")
    check("two <item> elements", len(items) == 2, f"{len(items)}")

    # The pair is what makes them separate parts and not two meshes inside one
    # object: each item is a distinct placement of a distinct object resource.
    object_ids = [o.get("id") for o in objects]
    item_ids = [i.get("objectid") for i in items]
    check("every object has an id", all(object_ids), str(object_ids))
    check("the two items reference different objects",
          len(set(item_ids)) == 2, str(item_ids))
    check("...and both of those objects exist",
          set(item_ids) <= set(object_ids), f"{item_ids} vs {object_ids}")

    # Each object carries its own mesh, so neither is an empty resource the
    # element count alone would let through.
    facets = [len(list(o.iter(_NS + "triangle"))) for o in objects]
    check("both objects carry triangles", all(f > 0 for f in facets), str(facets))
finally:
    shutil.rmtree(out_dir, ignore_errors=True)
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
