# SPDX-License-Identifier: LGPL-2.1-or-later
"""Export live document objects as native BREP files, plus a per-face map.

``view_model_3d`` (a later phase) sends real geometry to a browser so a user
can click exact faces of the actual solid. That geometry is the native BREP,
not a converted mesh: ``TopoShape.exportBrep`` writes OCCT's own format --
the same writer FreeCAD uses when it saves an object into a ``.FCStd`` -- and
the browser tessellates it client-side with a WASM OpenCascade build, so the
face ordinals the user picks resolve back to ``Shape.Faces`` by construction.

This module is the export half of that round trip, and it is deliberately
small: pure BRep export, no meshing, no rotation, no scratch document (there
is nothing to rotate or recompute -- ``exportBrep`` is a read of ``Shape`` and
mutates nothing). What it adds over a bare loop of ``exportBrep`` calls is the
sidecar map a later phase validates against: per object, ``hashCode()`` --
the same ``(name, hashCode())`` cache-key idiom ``diagnostics._shape_metrics``
uses -- plus, per face, its centroid, a representative normal and its area,
keyed by the 0-based index ``Shape.Faces`` returns them in. ``FaceN``
(1-based: ``N = index + 1``) is built from exactly those indices, so the map
and the BREP can never disagree about which face is which.

No FreeCAD import at module level, per the repo-wide rule: importing this
module must not require FreeCAD to be running, so its schema and helpers are
reachable from any thread. The objects handed in are live document objects;
``exportBrep`` and ``Shape.Faces`` are plain methods on them, so nothing here
needs importing from FreeCAD at all.
"""

import os


def export_brep(objs, out_dir):
    """Write ``<out_dir>/<ObjectName>.brp`` for each of `objs`, and return the
    per-object map a later phase's server publishes:

        {"objects": {
            "<ObjectName>": {
                "path": "<abs path to the .brp file>",
                "shape_hash": 1234567,
                "faces": {
                    "0": {"centroid": [x, y, z],
                          "normal": [nx, ny, nz],
                          "area": 12.3},
                    "1": {...},
                },
            },
        }}

    ``faces`` is keyed by the 0-based index ``obj.Shape.Faces`` returns faces
    in -- the browser's picked ``FaceN`` is ``N = index + 1``, so the caller
    can resolve a mark back to this map without any reordering. ``centroid``
    is ``face.CenterOfMass``, ``area`` is ``face.Area``, and ``normal`` is
    evaluated at the surface parameter of that centroid -- one representative
    point is enough, since the map exists to re-locate a face after a
    recompute, not to carry a load-bearing geometric guarantee.

    `objs` are live FreeCAD document objects, each with a ``Name`` and a
    ``Shape``; an object with no shape raises ``ValueError`` naming it, rather
    than silently dropping a name the caller explicitly asked for. The
    returned dict is not written anywhere -- the caller decides that.
    """
    os.makedirs(out_dir, exist_ok=True)
    objects = {}
    for obj in objs:
        name = obj.Name
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            raise ValueError(f"object '{name}' has no shape to export")
        path = os.path.join(out_dir, name + ".brp")
        shape.exportBrep(path)
        objects[name] = {
            "path": os.path.abspath(path),
            "shape_hash": shape.hashCode(),
            "faces": _face_map(shape),
        }
    return {"objects": objects}


def _face_map(shape):
    """``{index: {centroid, normal, area}}`` for ``shape.Faces``, by its own
    order. Index is a string key -- the dict is destined for JSON, where a
    number key would come back as a string anyway."""
    faces = {}
    for index, face in enumerate(shape.Faces):
        centroid = face.CenterOfMass
        normal = _representative_normal(face)
        faces[str(index)] = {
            "centroid": [centroid.x, centroid.y, centroid.z],
            "normal": [normal.x, normal.y, normal.z],
            "area": face.Area,
        }
    return faces


def _representative_normal(face):
    """The normal of `face` at the parameter of its centre of mass.

    ``normalAt`` needs the surface parameter, and ``Surface.parameter`` is the
    inverse of the surface's own parametrisation -- exact for the analytic
    surfaces (plane, cylinder, sphere, ...) that dominate real parts. A
    trimmed face whose centre of mass lands outside its parameter range falls
    back to the middle of the range instead, which is still a point on the
    surface.
    """
    try:
        u, v = face.Surface.parameter(face.CenterOfMass)
        return face.normalAt(u, v)
    except Exception:  # noqa: BLE001 - a representative point, not a contract
        return face.normalAt(0.5, 0.5)
