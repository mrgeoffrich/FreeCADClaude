# Part / Draft Scripting, Placement & Export (FreeCAD 1.1)

What's left after Sketcher and PartDesign: **Part workbench CSG**, **Draft
2D/array scripting**, **Placement-based positioning**, and **export**.
Single-Body parametric feature work (Pad/Pocket/Hole/Revolution/patterns/
fillets) is covered in `partdesign-scripting.md` — don't look for it here.

## Part primitives: raw shapes vs parametric objects

**Raw OCC shapes** — quick blockout/CSG math, throwaway geometry. No document
object until `Part.show`; no editable properties after (a static
`Part::Feature`, not parametric).

```python
shape = Part.makeBox(40, 20, 10)                       # length, width, height
cyl   = Part.makeCylinder(5, 20, FreeCAD.Vector(20,10,0), FreeCAD.Vector(0,0,1))
sph   = Part.makeSphere(15, FreeCAD.Vector(0,0,10))
cone  = Part.makeCone(10, 0, 20)
torus = Part.makeTorus(10, 2)
Part.show(shape, "Block")                               # adds a Part::Feature to doc
```

**Parametric primitives** — when the user might want to tweak a dimension
later; properties stay live and recompute on change.

```python
box = doc.addObject('Part::Box', 'Box')
box.Length, box.Width, box.Height = 40, 20, 10           # X, Y, Z
cyl = doc.addObject('Part::Cylinder', 'Cylinder')
cyl.Radius, cyl.Height = 5, 20          # also Part::Cone (Radius1/Radius2/Height), Part::Sphere (Radius)
doc.recompute()
```

Pick raw shapes for one-off massing you'll throw away; pick parametric
objects as inputs to anything the user will iterate on, including as
Base/Tool/Shapes inputs to the parametric booleans below.

## Part booleans: raw shape ops vs parametric objects

**Raw shape ops** — chain freely, cheap, no tree clutter. `removeSplitter()`
merges coplanar seam faces left by the boolean (cosmetic cleanup); it can
lose a shape's `Placement`, so re-apply that after if needed.

```python
fused  = shape1.fuse(shape2)
cut    = shape1.cut(shape2)
common = shape1.common(shape2)
clean  = fused.removeSplitter()                          # drop redundant seam edges/faces
Part.show(clean, "Result")
```

**Parametric boolean objects** — editable tree, can re-point inputs later.
Property names differ by op:

```python
cut = doc.addObject('Part::Cut', 'Cut')
cut.Base, cut.Tool = box, cyl                # binary only: Base minus Tool
cut.Refine = True                            # auto-clean seams, like removeSplitter()

fuse = doc.addObject('Part::MultiFuse', 'Fusion')
fuse.Shapes = [box, cyl, sph]                 # list, 2+ objects -- GUI Union always makes MultiFuse

common = doc.addObject('Part::Common', 'Common')   # binary: Base AND Tool
common.Base, common.Tool = box, cyl
# 3+-way intersection: Part::MultiCommon, with a Shapes list instead of Base/Tool
doc.recompute()
```

## Placement: positioning and simple multi-body layout

`FreeCAD.Placement(position, rotation)` arranges several Bodies/Parts
relative to each other from a script — the lightweight alternative to the
Assembly workbench for "just put these where they belong."

```python
pos  = FreeCAD.Vector(50, 0, 0)
rot  = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), 90)   # axis + angle (degrees)
rot2 = FreeCAD.Rotation(10, 20, 30)                    # yaw(Z), pitch(Y), roll(X)
obj.Placement = FreeCAD.Placement(pos, rot)

# Compose: parent placement * child's local placement -> child's world placement
child.Placement = parent.Placement.multiply(child_local_placement)  # or parent.Placement * child_local_placement
```

`obj.Placement.Base` / `.Rotation.Angle` / `.Rotation.Axis` read components
back. This is plain positioning, not a solver — full kinematic joints
(Revolute/Slider/gears) are built interactively in the Assembly workbench,
not via `run_python`; say so if the user wants live mechanism motion.

## Draft workbench scripting

Modern API is **snake_case** (`make_*`); the old camelCase names
(`makeRectangle`, `makeCircle`, `makeWire`, `makeArray`) are kept as
deprecated aliases in 1.x but may warn — write the snake_case form. `Draft`
is pre-bound, don't `import` it.

```python
rect = Draft.make_rectangle(40, 20)   # length (X), height (Y) -- "height" means Y here, not Z
circ = Draft.make_circle(10)
wire = Draft.make_wire([FreeCAD.Vector(0,0,0), FreeCAD.Vector(10,0,0),
                         FreeCAD.Vector(10,10,0)], closed=True)
hexagon = Draft.make_polygon(6, radius=15)
```

**Arrays repeat whole separate objects**, not features inside one Body —
reach for `PartDesign::LinearPattern`/`PolarPattern` (`partdesign-scripting.md`)
when staying inside a single solid; use Draft arrays to repeat an entire
Body/Part/Feature in space.

```python
grid = Draft.make_ortho_array(base_obj, v_x=FreeCAD.Vector(20,0,0),
            v_y=FreeCAD.Vector(0,20,0), v_z=FreeCAD.Vector(0,0,10), n_x=4, n_y=3, n_z=1)
ring = Draft.make_polar_array(base_obj, number=6, angle=360, center=FreeCAD.Vector(0,0,0))
```

## Export scripting

This addon already has a dedicated `export` tool for the common formats —
reach for `run_python` export mainly for formats it doesn't cover (e.g. BREP),
or to export a raw shape/sub-selection never added to the document.

```python
Part.export([obj1, obj2], "/path/to/part.step")    # format inferred from extension
shape.exportStep("/path/to/box.stp")                 # raw shape, no document object needed

import Mesh
Mesh.export([obj1], "/path/to/part.stl")             # tessellates Part solids on the fly, no Mesh::Feature needed
```

## Quantity / units

Same rule as everywhere else in this skill: plain numbers in, `Quantity` out —
see `execution-model.md`'s Quantity section, not repeated here.

## Recipe translation: archetype to first script

Property-by-property detail (Pad/Pocket/Hole/Revolution/pattern fields) lives
in `partdesign-scripting.md`; these are just the bulk-shape starting points.

**Prismatic plate/bracket** (Pad an outline):
```python
body = doc.addObject('PartDesign::Body', 'Body')
sk = body.newObject('Sketcher::SketchObject', 'Sketch')
sk.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
sk.MapMode = 'FlatFace'
# closed rectangle/profile geometry + constraints -- see sketcher-scripting.md
pad = body.newObject('PartDesign::Pad', 'Pad')
pad.Profile, pad.Length = sk, 5
doc.recompute()
```

**Enclosure box + lid** (Pad then Thickness, lid as a second Body):
```python
base = doc.addObject('PartDesign::Body', 'BaseBody')
sk = base.newObject('Sketcher::SketchObject', 'OuterSketch')   # outline -- sketcher-scripting.md
pad = base.newObject('PartDesign::Pad', 'Pad'); pad.Profile, pad.Length = sk, 30
shell = base.newObject('PartDesign::Thickness', 'Shell')
shell.Base = (pad, ['Face6'])             # the open top face -- confirm via get_objects/inspect_api; full property set in partdesign-scripting.md
shell.Value = 2
lid = doc.addObject('PartDesign::Body', 'LidBody')   # repeat sketch+pad for a thin lid plate
doc.recompute()
```

**Revolved part** (half-profile + Revolution):
```python
body = doc.addObject('PartDesign::Body', 'Body')
sk = body.newObject('Sketcher::SketchObject', 'Profile')
sk.AttachmentSupport = [(doc.getObject('XZ_Plane'), '')]
sk.MapMode = 'FlatFace'                    # closed half-profile against the axis -- sketcher-scripting.md
rev = body.newObject('PartDesign::Revolution', 'Revolution')
rev.Profile, rev.ReferenceAxis, rev.Angle = sk, (sk, ['V_Axis']), 360
doc.recompute()
```

**Multi-body boolean** (Part CSG quick massing — see also PartDesign Boolean
across two Bodies in `partdesign-scripting.md`):
```python
box = doc.addObject('Part::Box', 'Box')
box.Length, box.Width, box.Height = 40, 40, 20
hole = doc.addObject('Part::Cylinder', 'Hole')
hole.Radius, hole.Height = 5, 22
hole.Placement = FreeCAD.Placement(FreeCAD.Vector(20, 20, -1), FreeCAD.Rotation())
cut = doc.addObject('Part::Cut', 'Cut')
cut.Base, cut.Tool = box, hole
doc.recompute()
```

**Patterned features** (one feature, then PolarPattern — full property set in
`partdesign-scripting.md`):
```python
polar = body.newObject('PartDesign::PolarPattern', 'PolarPattern')
polar.Originals = [feat]                   # the single Hole/Pocket/Pad to repeat
polar.Axis, polar.Angle, polar.Occurrences = (sk, ['V_Axis']), 360, 6
doc.recompute()
```

## Common mistakes

- **Mixing raw-shape ops with parametric objects** — `box.Shape.fuse(other)`
  returns a detached `Part.Shape`; assigning it back to `box.Shape` makes
  `box` a non-parametric blob that no longer tracks `Length`/`Width`/`Height`.
  Pick one path (raw or `Part::*` boolean objects) and stay in it.
- **Forgetting `doc.recompute()`** after `addObject`/property edits — reads
  via `get_objects` can show stale geometry until it runs.
- **Draft name drift** — old camelCase (`makeRectangle`, `makeArray`, ...)
  still resolves in 1.x but is deprecated; use the snake_case forms here.
- **Assuming a pip package is available** — this addon ships zero Python
  dependencies; only FreeCAD's bundled modules (`Part`, `Draft`, `Mesh`,
  `TechDraw`, ...) and the stdlib exist. `import numpy` will fail.

## Sources

- [Part scripting](https://wiki.freecad.org/Part_scripting)
- [Part Boolean](https://wiki.freecad.org/Part_Boolean), [Part Fuse](https://wiki.freecad.org/Part_Fuse), [Part Cut](https://wiki.freecad.org/Part_Cut), [Part Common](https://wiki.freecad.org/Part_Common)
- [Part Export](https://wiki.freecad.org/Part_Export), [Mesh Export](https://wiki.freecad.org/Mesh_Export)
- [Draft Rectangle](https://wiki.freecad.org/Draft_Rectangle), [Draft Circle](https://wiki.freecad.org/Draft_Circle), [Draft Wire](https://wiki.freecad.org/Draft_Wire), [Draft Polygon](https://wiki.freecad.org/Draft_Polygon)
- [Draft OrthoArray](https://wiki.freecad.org/Draft_OrthoArray), [Draft PolarArray](https://wiki.freecad.org/Draft_PolarArray)
- [Placement](https://wiki.freecad.org/Placement), [Quantity](https://wiki.freecad.org/Quantity)
