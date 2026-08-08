# SPDX-License-Identifier: LGPL-2.1-or-later
"""Write parts out the way up they are printed.

``print_meta`` records which part-local axis points up once a part is on the
plate; this is what acts on it. Nothing did before, so a part recorded as
"-Z up" still left the document as modelled and a slice of it bore no relation
to how the part gets made.

The rotation must not reach the user's document. Rotating the real objects would
mutate placements the user owns and recompute everything downstream of them, so
nothing here touches an existing object: each shape is meshed, the mesh is
rotated and dropped onto the plate, and the copies are collected in a scratch
document that is closed again before the call returns.

Meshing explicitly is what makes that possible at all, since ``Mesh.export``
offers no way to move what it tessellates. It also puts the deflection under the
caller's control, and it keeps the as-modelled export on the same path as the
rotated one.

Every part lands with its minimum corner at the origin, stacked on top of each
other. Laying them out is the slicer's job: it packs a plate better than we
would, and it keeps each part's rotation while it does. Orientation is a design
decision the document records; layout is packing.
"""

from .print_meta import NOT_PRINTED, NOT_SET, up_vector

#: Linear deflection when the caller names none, matching Mod/Mesh's own
#: MaxDeviationExport default so an export that asks for nothing tessellates the
#: way the rest of FreeCAD would.
_LINEAR_DEFLECTION = 0.1

#: Angular deflection, in radians. It floors how coarse a curved face can get,
#: which is why loosening the linear deflection past about 0.05 mm stops having
#: any effect at all: a small bore stays round instead of degenerating to a
#: polygon.
_ANGULAR_DEFLECTION = 0.35

#: Internal name asked for the throwaway document. FreeCAD uniquifies it, so the
#: real name is read back off the document before anything closes it.
_SCRATCH_DOC = "FreeCADClaudePrintExport"

#: How close to world +Z counts as already standing up. A part that already is
#: gets no rotation applied and is reported as unrotated.
_ALIGNED_TOLERANCE = 1e-9


def _mesh_of(shape, deflection):
    """``shape`` tessellated into a standalone Mesh -- a copy, so everything
    after this point is free to move it."""
    import MeshPart

    return MeshPart.meshFromShape(Shape=shape, LinearDeflection=deflection,
                                  AngularDeflection=_ANGULAR_DEFLECTION,
                                  Relative=False)


def _plate_rotation(obj, up):
    """The rotation standing ``obj`` up on ``up``, or None if it already is.

    ``up`` is stated in the part's local frame, while the mesh comes off
    ``obj.Shape`` -- which already carries the object's Placement. So the axis
    has to be carried through that same Placement rotation before it names a
    direction in the frame the mesh is actually in. Skip that and "+Z up" stops
    meaning "as modelled" for any part whose Placement is rotated.
    """
    import FreeCAD

    axis = FreeCAD.Vector(*up)
    placement = getattr(obj, "Placement", None)
    if placement is not None:
        axis = placement.Rotation.multVec(axis)
    world_up = FreeCAD.Vector(0, 0, 1)
    if (axis - world_up).Length <= _ALIGNED_TOLERANCE:
        return None
    return FreeCAD.Rotation(axis, world_up)


def _close_scratch(scratch, previous):
    """Close the scratch document and hand the active document back.

    Called from a ``finally``, so it runs on every exit path including an
    exception mid-export: a leaked scratch document would appear in the user's
    tree, and creating one makes it active, so the document they were working in
    has to be reasserted.
    """
    import FreeCAD

    try:
        FreeCAD.closeDocument(scratch.Name)
    except Exception:  # noqa: BLE001
        pass
    if previous is None:
        return
    try:
        FreeCAD.setActiveDocument(previous.Name)
    except Exception:  # noqa: BLE001
        pass


def oriented_export(objs, path, deviation=None, orient=True):
    """Mesh ``objs``, stand each one up the way it prints, and write them all to
    one multi-object 3MF at ``path``. Returns a report dict.

    ``path``'s extension chooses the format ``Mesh.export`` writes, so it has to
    be ``.3mf`` for the parts to survive as separate objects; the caller
    validates that. ``deviation`` is the linear deflection in mm, defaulting to
    FreeCAD's own 0.1; anything non-positive falls back to it. ``orient=False``
    exports every part exactly as modelled, coordinates included -- which is
    what to reach for when comparing against a slice made by hand.

    Each recorded direction is treated as ``print_meta`` describes it:

        +Z up               exported as modelled, no rotation
        -Z up, +-X, +-Y     rotated so that local axis points at world +Z
        Custom              rotated the same way, off PrintDirectionCustom
        Not set             exported as modelled, and named in the report
        Not printed         left off the plate entirely

    The report is structured data rather than a sentence -- the tool that calls
    this owns the wording:

        path        where it was written
        deviation   the linear deflection actually used
        oriented    whether rotation was applied at all
        exported    one entry per part on the plate, in the 3MF's own object
                    order: {name, label, direction, rotated, size_mm}. Index is
                    the only way to match the two, because FreeCAD writes no
                    name onto a 3MF <object>.
        not_set     names of parts nobody has decided about
        omitted     names left off the plate as "Not printed"
        skipped     [{name, reason}] for a part with no usable shape

    ``exported`` is empty when there was nothing to write, in which case no file
    was created at ``path``.
    """
    import FreeCAD
    import Mesh

    deflection = _LINEAR_DEFLECTION
    if deviation is not None and float(deviation) > 0.0:
        deflection = float(deviation)

    report = {
        "path": path,
        "deviation": deflection,
        "oriented": bool(orient),
        "exported": [],
        "not_set": [],
        "omitted": [],
        "skipped": [],
    }

    previous = FreeCAD.ActiveDocument
    scratch = FreeCAD.newDocument(_SCRATCH_DOC, hidden=True)
    try:
        for obj in objs:
            name = getattr(obj, "Name", "?")
            label = getattr(obj, "Label", name)
            shape = getattr(obj, "Shape", None)
            if shape is None or shape.isNull():
                report["skipped"].append({"name": name, "reason": "no shape"})
                continue

            direction, up = up_vector(obj)
            if direction == NOT_PRINTED:
                report["omitted"].append(name)
                continue
            if direction == NOT_SET:
                report["not_set"].append(name)

            rotation = _plate_rotation(obj, up) if orient and up is not None else None
            # One shape OCCT cannot tessellate must not cost the whole plate. It
            # is named in the report instead.
            try:
                mesh = _mesh_of(shape, deflection)
            except Exception as exc:  # noqa: BLE001
                report["skipped"].append({"name": name,
                                          "reason": f"meshing failed: {exc}"})
                continue

            if rotation is not None:
                origin = FreeCAD.Vector(0, 0, 0)
                mesh.transform(FreeCAD.Placement(origin, rotation).Matrix)
            box = mesh.BoundBox
            if orient:
                mesh.translate(-box.XMin, -box.YMin, -box.ZMin)

            feature = scratch.addObject("Mesh::Feature", name)
            feature.Label = label
            feature.Mesh = mesh
            report["exported"].append({
                "name": name,
                "label": label,
                "direction": direction,
                "rotated": rotation is not None,
                "size_mm": [round(box.XLength, 3), round(box.YLength, 3),
                            round(box.ZLength, 3)],
            })

        if report["exported"]:
            Mesh.export(scratch.Objects, path)
    finally:
        _close_scratch(scratch, previous)
    return report
