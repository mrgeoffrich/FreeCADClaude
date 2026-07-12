# Sketcher Scripting (FreeCAD 1.1)

Scripting `Sketcher::SketchObject` geometry and constraints from `run_python`. Covers the mechanics of building and fully constraining a 2D profile — `addGeometry`, `addConstraint`/`Sketcher.Constraint()` argument forms, plane attachment, and closure/constraint checks. PartDesign-Body-specific sketch mechanics (Tip handling, datum scripting) are in `partdesign-scripting.md`; this file only covers the Sketcher API itself, which is identical whether the sketch lives in a Body or stands alone.

## Creating a sketch

```python
# Standalone, in the document root
sketch = doc.addObject('Sketcher::SketchObject', 'Sketch')

# Inside a PartDesign Body — creates AND adds to the body in one call
body = doc.addObject('PartDesign::Body', 'Body')
sketch = body.newObject('Sketcher::SketchObject', 'Sketch')
```

A freshly created sketch already sits on the **global XY plane** via its default identity `Placement` — no attachment is required just to get geometry onto a plane. Use `body.newObject` (not `doc.addObject` + manual `body.addObject`) when the sketch belongs to a Body, so it's correctly inserted into the feature tree.

## Attaching to a plane

The current (1.0/1.1) property is **`AttachmentSupport`** (`PropertyLinkSubList`) — the older `Support` name is a deprecated alias kept only for loading old files. Pair it with **`MapMode`**:

```python
sketch.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
sketch.MapMode = 'FlatFace'
```

- A Body or `Std::Part` auto-creates an Origin with sub-objects named `XY_Plane`/`XZ_Plane`/`YZ_Plane` — `doc.getObject('XY_Plane')` resolves to it as long as the name is unique. A bare document has no Origin unless you add one.
- Same pattern for a datum plane: `sketch.AttachmentSupport = [(datumPlane, '')]`.
- **Both properties must be set together.** `MapMode` defaults to `'Deactivated'`; setting only `AttachmentSupport` does nothing until `MapMode` is also set, and vice versa.

## addGeometry

```python
idx = sketch.addGeometry(Part.LineSegment(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(40, 0, 0)), False)
ids = sketch.addGeometry([geo1, geo2, geo3], False)   # batch form -> tuple of indices, same order
```

`addGeometry(geo, construction=False)` returns the new **geoId** (int, 0-based — the GUI status bar shows 1-based numbers, subtract 1). Passing a list returns a **tuple** of indices, not a list.

Geometry classes used inside a sketch:

```python
Part.LineSegment(FreeCAD.Vector(x1, y1, 0), FreeCAD.Vector(x2, y2, 0))
Part.Circle(FreeCAD.Vector(cx, cy, 0), FreeCAD.Vector(0, 0, 1), radius)          # normal is always +Z in-sketch
Part.ArcOfCircle(Part.Circle(FreeCAD.Vector(cx, cy, 0), FreeCAD.Vector(0, 0, 1), radius),
                  startAngleRad, endAngleRad)                            # sweeps CCW, start < end
Part.ArcOfEllipse(Part.Ellipse(FreeCAD.Vector(cx, cy, 0), majorRadius, minorRadius),
                   startParam, endParam)
```

`Part.BSplineCurve()` + `setPoles(...)` exists for free-form curves but is rarely needed for ordinary profiles — reach for `inspect_api` if you need it.

**Construction geometry** — two equivalent ways:
```python
sketch.addGeometry(geo, True)        # construction at creation time
sketch.toggleConstruction(idx)       # flip an existing geoId after the fact
sketch.setConstruction(idx, True)    # explicit on/off instead of toggle
```
Construction geometry is solved like normal geometry but excluded from the profile `Pad`/`Pocket` see — use it for reference lines/circles (e.g. a polygon's circumscribing circle), never for edges meant to form the solid boundary.

## addConstraint and the point-position scheme

```python
sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))
```

**Point-position (`pos`) integers**, used wherever a constraint addresses a specific point of an edge:
- `0` — the entire edge (used by edge-level constraints: `Horizontal`, `Distance` on a line, `Block`)
- `1` — start point
- `2` — end point
- `3` — center point (circles, arcs, ellipses only)
- `n` — the n-th pole, for a B-spline

**GeoId integers**: positive = sketch geometry index (creation order). Negative values address axes/external geometry: `-1` = the sketch's X axis, `-2` = Y axis, `-3, -4, ...` = external geometry in the flattened order of `sketch.ExternalGeometry`. The idiom `(-1, 1)` — "geoId -1, point 1" — addresses the **sketch origin point** itself; it's the standard way to pin a point to the origin: `Sketcher.Constraint('Coincident', geoId, pos, -1, 1)`.

Verified `Sketcher.Constraint(...)` forms for the constraints actually needed to build real profiles:

```python
# Geometric
Sketcher.Constraint('Coincident', geoId1, pos1, geoId2, pos2)
Sketcher.Constraint('Horizontal', geoId)                                  # edge horizontal
Sketcher.Constraint('Horizontal', geoId1, pos1, geoId2, pos2)             # two points level
Sketcher.Constraint('Vertical', geoId)                                    # (same two forms as Horizontal)
Sketcher.Constraint('Parallel', geoId1, geoId2)
Sketcher.Constraint('Perpendicular', geoId1, geoId2)                      # direct, edge-to-edge
Sketcher.Constraint('Perpendicular', geoId1, pos1, geoId2, pos2)          # point-to-point (implies coincident)
Sketcher.Constraint('Perpendicular', geoId1, pos1, geoId2)                # point-to-curve
Sketcher.Constraint('Tangent', geoId1, geoId2)                            # (same 3 forms as Perpendicular)
Sketcher.Constraint('Equal', geoId1, geoId2)
Sketcher.Constraint('Symmetric', geoId1, pos1, geoId2, pos2, lineGeoId)        # symmetric about a line
Sketcher.Constraint('Symmetric', geoId1, pos1, geoId2, pos2, geoId3, pos3)     # symmetric about a point
Sketcher.Constraint('PointOnObject', geoId, pos, onGeoId)
Sketcher.Constraint('Block', geoId)

# Dimensional (last arg is the value, mm or radians; FreeCAD.Units.Quantity('45 deg') also accepted for Angle)
Sketcher.Constraint('Distance', geoId, value)                             # line length
Sketcher.Constraint('Distance', geoId1, geoId2, value)                    # edge-to-edge distance
Sketcher.Constraint('Distance', geoId1, pos1, geoId2, value)              # point-to-edge (perpendicular) distance
Sketcher.Constraint('Distance', geoId1, pos1, geoId2, pos2, value)        # point-to-point distance
Sketcher.Constraint('DistanceX', geoId, value)                            # (DistanceY analogous)
Sketcher.Constraint('DistanceX', geoId, pos, value)                       # single point from sketch origin
Sketcher.Constraint('DistanceX', geoId1, pos1, geoId2, pos2, value)       # point-to-point, X component only
Sketcher.Constraint('Radius', geoId, value)
Sketcher.Constraint('Diameter', geoId, value)
Sketcher.Constraint('Angle', geoId, value)                                # line slope, or arc's angular span
Sketcher.Constraint('Angle', geoId1, pos1, geoId2, pos2, value)           # angle between two lines
```

A point-to-point `Tangent`/`Perpendicular` already implies coincidence at that point — don't add a separate `Coincident` on the same pair, it's redundant. Every numeric form also accepts two optional trailing booleans, `(activated, driving)`: `Sketcher.Constraint('Distance', 0, 40.0, True, False)` adds a non-driving (reference) dimension.

## Closed-profile recipes

**Rectangle**, anchored at one corner, fully constrained (4 geometric + 2 dimensional + origin pin):

```python
p = [FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(40, 0, 0), FreeCAD.Vector(40, 20, 0), FreeCAD.Vector(0, 20, 0)]
lines = [Part.LineSegment(p[i], p[(i + 1) % 4]) for i in range(4)]
sketch.addGeometry(lines, False)                                  # geoIds 0..3

for i in range(4):
    sketch.addConstraint(Sketcher.Constraint('Coincident', i, 2, (i + 1) % 4, 1))
sketch.addConstraint(Sketcher.Constraint('Horizontal', 0))
sketch.addConstraint(Sketcher.Constraint('Vertical', 1))
sketch.addConstraint(Sketcher.Constraint('Horizontal', 2))
sketch.addConstraint(Sketcher.Constraint('Vertical', 3))
sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 1, -1, 1))     # pin corner to origin
sketch.addConstraint(Sketcher.Constraint('DistanceX', 0, 40.0))          # width, driven by edge0
sketch.addConstraint(Sketcher.Constraint('DistanceY', 1, 20.0))          # height, driven by edge1
doc.recompute()
```

**Regular polygon** (N-sided, equal sides, one length dimension):

```python
import math
n, r = 6, 15.0
pts = [FreeCAD.Vector(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), 0) for i in range(n)]
sketch.addGeometry([Part.LineSegment(pts[i], pts[(i + 1) % n]) for i in range(n)], False)

for i in range(n):
    sketch.addConstraint(Sketcher.Constraint('Coincident', i, 2, (i + 1) % n, 1))
for i in range(1, n):
    sketch.addConstraint(Sketcher.Constraint('Equal', 0, i))
sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 1, -1, 1))     # pin vertex0 to origin
sketch.addConstraint(Sketcher.Constraint('Horizontal', 0))               # fix rotation
sketch.addConstraint(Sketcher.Constraint('Distance', 0, 15.0))           # side length
doc.recompute()
```

**Slot** (two arcs + two tangent-coincident lines — the closure pattern FreeCAD's own Slot tool generates):

```python
r, L = 5.0, 30.0
c1, c2, n = FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(L, 0, 0), FreeCAD.Vector(0, 0, 1)
arc1 = Part.ArcOfCircle(Part.Circle(c1, n, r), math.pi / 2, 3 * math.pi / 2)   # bulges left
arc2 = Part.ArcOfCircle(Part.Circle(c2, n, r), -math.pi / 2, math.pi / 2)     # bulges right
line1 = Part.LineSegment(FreeCAD.Vector(0, -r, 0), FreeCAD.Vector(L, -r, 0))
line2 = Part.LineSegment(FreeCAD.Vector(0, r, 0), FreeCAD.Vector(L, r, 0))
sketch.addGeometry([arc1, arc2, line1, line2], False)                  # geoIds 0=arc1 1=arc2 2=line1 3=line2

sketch.addConstraint(Sketcher.Constraint('Tangent', 0, 1, 3, 1))   # arc1.start <-> line2.start
sketch.addConstraint(Sketcher.Constraint('Tangent', 0, 2, 2, 1))   # arc1.end   <-> line1.start
sketch.addConstraint(Sketcher.Constraint('Tangent', 2, 2, 1, 1))   # line1.end  <-> arc2.start
sketch.addConstraint(Sketcher.Constraint('Tangent', 3, 2, 1, 2))   # line2.end  <-> arc2.end
sketch.addConstraint(Sketcher.Constraint('Equal', 0, 1))
sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 3, -1, 1))            # arc1 center to origin
sketch.addConstraint(Sketcher.Constraint('Horizontal', 0, 3, 1, 3))             # centers level
sketch.addConstraint(Sketcher.Constraint('Radius', 0, r))
sketch.addConstraint(Sketcher.Constraint('DistanceX', 0, 3, 1, 3, L))           # center distance
doc.recompute()
```

## Checking constraint state from code

```python
doc.recompute()                      # always before reading anything derived
sketch.FullyConstrained               # bool — all geometry fully determined (DoF == 0)
sketch.DoF                            # int — HOW loose it still is (0 == fully constrained)
sketch.ConflictingConstraints         # constraints that contradict each other
sketch.RedundantConstraints           # harmless to the shape, but they break later edits
sketch.MalformedConstraints           # the solver can't even evaluate these
sketch.getOpenVertices()              # list of Base.Vector3d — endpoints NOT closed; [] means closed wire(s)
len(sketch.Geometry), len(sketch.Constraints)   # sanity counts after a batch of adds
```

`getOpenVertices()` is populated by the sketch's internal analyser during recompute, so it's only meaningful after `doc.recompute()` runs clean. `FullyConstrained` is a hidden read-only property but still readable from Python.

**These are plain Python attributes, not App properties** — they don't appear in `PropertiesList`, and there is no `getDoF()`/`getLastSolverStatus()` method (those don't exist; nor do `setGeometry`, `getGeometry`, `movePoint`, or `rebuildExternalGeometry`). Read the properties above.

**A conflicting or malformed sketch still "recomputes clean."** Nothing raises, `get_diagnostics` reports nothing, and the geometry you read back is *not* what the constraints say. Check these lists explicitly — or just use this addon's **`get_sketch`** tool, which returns all of it (plus every GeoId and constraint index) in one read-only call and is almost always the right first move. Constraint indices in `get_sketch` are 0-based; the raw `ConflictingConstraints`/`RedundantConstraints` properties report them **1-based**, so subtract 1 before passing one to `delConstraint`/`setDatum`.

## Modifying an existing sketch

Editing someone else's sketch is a different job from drawing a new one, and the intuitive approach silently produces garbage.

**In a constrained sketch, the constraints drive the geometry — not the coordinates.** So to change a dimension, change the *datum*:

```python
sketch.setDatum(constraintIndex, 6.0)                       # mm
sketch.setDatum(constraintIndex, App.Units.Quantity('45 deg'))   # angles — Value is RADIANS
sketch.renameConstraint(i, 'Width')                          # then setDatum('Width', 25.0) by name
```

`setDatum` re-solves the sketch and moves everything the constraint governs, keeping the design intent intact. This is the normal way to resize or reposition anything in a well-constrained sketch.

**Do NOT assign to `sketch.Geometry` to move something.** It does not raise. The solver just drags the geometry back to satisfy the constraints that are still there, and you get a mangled, often self-intersecting profile with no error anywhere:

```python
# A line held by DistanceX = 10, "shortened" to 6:
g = sketch.Geometry
g[0] = Part.LineSegment(App.Vector(0,0,0), App.Vector(6,0,0))
sketch.Geometry = g
doc.recompute()
# -> Line (-3.08, 0, 0) → (6.92, 0, 0):  still 10mm long, and the start point
#    has been flung off to -3.08. Nothing warned you. Use setDatum instead.
```

**`moveGeometry` only moves UNDERCONSTRAINED geometry** — that's its documented contract ("it works only for underconstrained portions of the sketch"). Applied to a constrained point it quietly does nothing, or moves it and not its neighbours, which is how you get a sketch that is broken in a *different* way after each attempt.

```python
sketch.moveGeometry(geoId, pos, App.Vector(x, y, 0), relative=False)
```

Before trying to move anything, look at what pins it (`get_sketch`'s `constraints_by_geoId`, or scan `sketch.Constraints` for the GeoId). If a datum holds it → `setDatum`. If nothing holds it → moving it is legitimate, but prefer *adding the constraint that should have been there*, since unconstrained geometry is why it drifted in the first place.

**Rescaling a sketch means changing every dimension that drives it.** Scaling only the `Distance` constraints leaves unconstrained geometry exactly where it was, tearing the sketch in half — part at the new size, part at the old. Check `DoF` first: if a sketch is largely unconstrained, say so rather than nudging points one at a time.

Other edit primitives, all GeoId-addressed and 0-based:

```python
sketch.delConstraint(i)               # drop a constraint (indices SHIFT — delete high→low)
sketch.delGeometry(geoId)             # drop geometry (its constraints go too; GeoIds shift)
sketch.toggleConstruction(geoId)      # normal <-> construction
sketch.setConstruction(geoId, True)
sketch.autoRemoveRedundants()         # clear the redundant set
sketch.solve()                         # returns 0 on success
```

Deleting shifts every later index/GeoId down by one. Delete in **descending** order, or re-read the sketch between deletions.

## External geometry

```python
geoId = sketch.addExternal(otherSketchOrObject, 'Edge1')   # returns a negative geoId
```

`addExternal(obj, subName, defining=False, intersection=False)` projects an edge/vertex from elsewhere into the sketch so it can be constrained against. Use sparingly and prefer referencing another **sketch's** geometry over a solid's generated face/edge — referencing generated topology ties the sketch to names that can shift under the topological-naming problem (full explanation in the design-advisor's `core-concepts.md`, not repeated here).

**Negative GeoIds** (verified against FreeCAD 1.1): `sketch.ExternalGeo[i]` has GeoId `-(i+1)`, and the first three slots are the sketch's own reference geometry:

| GeoId | What it is |
|---|---|
| `-1` | X axis (H_Axis) |
| `-2` | Y axis (V_Axis) |
| `-3` | origin point (RootPoint) — `sketch.getPoint(-1, 1)` also returns it |
| `-4` and below | actual external geometry, in the order added |

So **external geometry starts at `-4`**, not `-3` as widely repeated (the origin point occupies `-3` in 1.x). Don't hardcode either — read the GeoIds from `get_sketch`.

## Common mistakes

- **Off-by-one `pos`**: `1`/`2` are start/end, not `0`/`1` — `0` means "the whole edge," not "the start point."
- **Reading derived state before `doc.recompute()`** — `getOpenVertices()`, `FullyConstrained`, and `.Shape` all reflect the *last solve*, not the geometry you just added.
- **Numerically-coincident endpoints aren't a closed wire.** If two line endpoints happen to land on the same coordinates but there's no `Coincident` constraint between them, the sketch isn't robustly closed — a later dimension edit can pull them apart silently. Always constrain closure explicitly.
- **Construction-mode confusion**: the `addGeometry(geo, True)` flag is `construction`, not "hidden" or "auxiliary" — construction edges are excluded from `Pad`/`Pocket` profiles entirely, so accidentally passing `True` (or `False`) silently changes what gets built.
- **Setting `AttachmentSupport` or `MapMode` alone**: without both set together the sketch is configured but not actually attached — it stays at its prior `Placement`.
- **GeoId numbering**: scripts are 0-based; the GUI status bar / numbering shown when hovering geometry is 1-based — subtract 1 when translating what you see on screen into code.
- **Treating `addGeometry`'s batch return as a list** — it's a tuple; index into it (`ids[0]`), don't expect `.append`/list mutation semantics.
- **Overwriting `sketch.Geometry` to move constrained geometry** — the single most destructive mistake here. It never errors; the solver silently drags everything back and mangles the profile. Use `setDatum`. See "Modifying an existing sketch".
- **Guessing method names.** `setGeometry`, `getGeometry`, `movePoint`, `getDoF()`, `getLastSolverStatus()`, `rebuildExternalGeometry` **do not exist**. The real ones are `moveGeometry`, the `DoF` *property*, and `sketch.Geometry[i]` for reads. `inspect_api` on a sketch **instance** (e.g. `doc.Sketch001`) lists every real method — `Sketcher.SketchObject` is not an importable class, though `inspect_api` will now resolve it to a live instance for you.
- **Passing the solver's constraint indices straight to `delConstraint`** — `ConflictingConstraints`/`RedundantConstraints` are 1-based, `delConstraint`/`setDatum` are 0-based. Off by one deletes the wrong constraint. (`get_sketch` already normalises them to 0-based.)
- **Deleting constraints/geometry in ascending order** — each deletion shifts every later index down. Delete descending.

## Sources
- [Sketcher scripting](https://wiki.freecad.org/Sketcher_scripting)
- [Sketcher ConstrainCoincident](https://wiki.freecad.org/Sketcher_ConstrainCoincident)
- [Sketcher ConstrainPointOnObject](https://wiki.freecad.org/Sketcher_ConstrainPointOnObject)
- [Sketcher ConstrainTangent](https://wiki.freecad.org/Sketcher_ConstrainTangent)
- [Sketcher ConstrainPerpendicular](https://wiki.freecad.org/Sketcher_ConstrainPerpendicular)
- [Sketcher ConstrainSymmetric](https://wiki.freecad.org/Sketcher_ConstrainSymmetric)
- [Sketcher ConstrainAngle](https://wiki.freecad.org/Sketcher_ConstrainAngle)
- [Sketcher ConstrainDistance](https://wiki.freecad.org/Sketcher_ConstrainDistance), [ConstrainDistanceX](https://wiki.freecad.org/Sketcher_ConstrainDistanceX), [ConstrainRadius](https://wiki.freecad.org/Sketcher_ConstrainRadius), [ConstrainDiameter](https://wiki.freecad.org/Sketcher_ConstrainDiameter), [ConstrainHorizontal](https://wiki.freecad.org/Sketcher_ConstrainHorizontal), [ConstrainEqual](https://wiki.freecad.org/Sketcher_ConstrainEqual), [ConstrainParallel](https://wiki.freecad.org/Sketcher_ConstrainParallel), [ConstrainBlock](https://wiki.freecad.org/Sketcher_ConstrainBlock)
- [Sketcher SketchObject](https://wiki.freecad.org/Sketcher_SketchObject)
- [Topological data scripting](https://wiki.freecad.org/Topological_data_scripting)
- [Scripted objects with attachment](https://wiki.freecad.org/Scripted_objects_with_attachment)
- [Code snippets](https://wiki.freecad.org/Code_snippets)
- FreeCAD source (github.com/FreeCAD/FreeCAD, `src/Mod/Sketcher/App/ConstraintPyImp.cpp`, `SketchObject.h`/`.cpp`, `SketchObjectPyImp.cpp`; `src/Mod/Part/App/AttachExtension.cpp`) — used to verify exact `Constraint()` overloads, `addGeometry`/`addExternal` signatures and return values, and the `AttachmentSupport` vs. deprecated `Support` property name, where the wiki feature pages reference but don't reproduce the scripting forms; also the Slot tool's own constraint-generation commit, used as the verified source for the slot closure pattern.
