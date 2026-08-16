#!/usr/bin/env python3
"""Phase 1 spike -- FreeCAD side.

Runs under `freecadcmd` (headless; no GUI, no GL needed). Builds the two test
shapes, writes each as a BREP file via obj.Shape.exportBrep(), and records a
JSON per shape with, for every face of obj.Shape.Faces IN ORDER (0-based index
= the order Shape.Faces returns them): the face's exact CenterOfMass, a normal
at a representative parameter, and its area.

Usage: freecadcmd <abs path>/export_faces.py
"""

import json
import os

import FreeCAD as App
import Part

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "out"))
SHAPE_DIR = os.path.normpath(os.path.join(HERE, "..", "shapes"))
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(SHAPE_DIR, exist_ok=True)

# freecadcmd swallows the script's stdout in headless mode (its console is
# /dev/null), so progress and tracebacks go to a log file and stderr instead.
LOG_PATH = os.path.join(OUT_DIR, "freecad_run.log")


def log(msg):
    with open(LOG_PATH, "a") as fh:
        fh.write(msg + "\n")
    import sys

    sys.stderr.write(msg + "\n")


def face_record(face):
    """(centroid, normal, area) for one face, as JSON-safe values.

    Normal at a representative parameter: the midpoint of the face's UV
    parameter range is the cheapest robust representative; fall back to the
    surface parameter of the centroid, then to None, if normalAt fails.
    """
    c = face.CenterOfMass
    n = None
    try:
        u0, u1, v0, v1 = face.ParameterRange
        n = face.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
    except Exception:
        try:
            u, v = face.Surface.param(c)
            n = face.normalAt(u, v)
        except Exception:
            pass
    return {
        "centroid": [c.x, c.y, c.z],
        "normal": None if n is None else [n.x, n.y, n.z],
        "area": face.Area,
    }


def export_shape(name, shape):
    brp_path = os.path.join(SHAPE_DIR, name + ".brp")
    shape.exportBrep(brp_path)
    faces = shape.Faces
    payload = {
        "shape": name,
        "source": "freecad",
        "freecad_version": ".".join(str(x) for x in App.Version()[:3]),
        "face_count": len(faces),
        "faces": [
            {"index": i, **face_record(f)} for i, f in enumerate(faces)
        ],
    }
    # App.ConfigGet("OCCTVersion") is not defined on every FreeCAD build; only
    # record it when the build exposes it.
    occt_ver = App.ConfigGet("OCCTVersion")
    if occt_ver:
        payload["occt_version"] = occt_ver
    out_path = os.path.join(OUT_DIR, name + ".freecad.faces.json")
    with open(out_path, "w") as fh:
        json.dump(payload, fh, indent=2)
    log(
        "[freecad] %s: %d faces -> %s (%d bytes), json %s"
        % (name, len(faces), brp_path, os.path.getsize(brp_path), out_path)
    )


def main():
    doc = App.newDocument("spike")

    # Shape 1: a box 80x50x30 with a central cylindrical hole (r=10) through
    # it -- box + cylinder, boolean cut. The box occupies x in [0,80],
    # y in [0,50], z in [0,30] (Part::Box defaults to the positive octant),
    # so the cylinder is placed at (40, 25) to bore through the middle, and
    # its height (40) exceeds the box height (30) so the hole is a true
    # through-hole: 4 box sides, 2 annular end faces, 1 cylinder side face.
    box = doc.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = 80.0, 50.0, 30.0
    cyl = doc.addObject("Part::Cylinder", "Cylinder")
    cyl.Radius, cyl.Height = 10.0, 40.0
    cyl.Placement = App.Placement(App.Vector(40.0, 25.0, 0.0), App.Rotation())
    doc.recompute()
    cut = doc.addObject("Part::Cut", "Cut")
    cut.Base = box
    cut.Tool = cyl
    doc.recompute()
    export_shape("box_with_hole", cut.Shape)

    # Shape 2: a box 60x40x20 with fillets (r=6) on all 12 edges.
    box2 = Part.makeBox(60.0, 40.0, 20.0)
    filleted = box2.makeFillet(6.0, box2.Edges)
    if not filleted.isValid():
        raise RuntimeError("filleted box shape is invalid")
    export_shape("filleted_box", filleted)

    log("[freecad] done")


# freecadcmd *imports* the script (it does not exec it as __main__), so the
# usual `if __name__ == "__main__":` guard never fires under it. Run directly.
try:
    main()
except Exception:
    import traceback

    log("TRACEBACK:\n" + traceback.format_exc())
    raise
