# PartDesign Feature Scripting (FreeCAD 1.1)

Scripting a Body's feature tree through `body.newObject(...)`: Pad/Pocket, Revolution/Groove, Loft/Pipe, datums, patterns, PartDesign Boolean, Fillet/Chamfer/Thickness, Hole. Sketch geometry/constraints are in `sketcher-scripting.md`; Part-workbench CSG, Draft, Placement and export are in `part-draft-recipes.md`. Property names below are exact Python attributes (no spaces, unlike the GUI labels), verified against FreeCAD source at tag `1.1.1`.

## Body and Tip

- `doc.addObject('PartDesign::Body', 'Body')` creates the Body.
- Add every feature with **`body.newObject('<Type>', '<Name>')`** — one call both creates the object and inserts it into the Body (the standard `GroupExtension.newObject(type, name)` method; Body is a `GeoFeatureGroupExtension`).
- **Tip auto-advances, but only for solid features.** Internally, `newObject` calls `Body.addObject()`, which does `if isSolidFeature(feature): Tip = feature`. Sketches and datum objects (`PartDesign::Plane/Line/Point/CoordinateSystem`) are not solid features, so adding one never moves `Tip` — the ordinary Sketch → Pad → Sketch → Pocket → ... flow needs no manual `Tip` handling at all.
- You only need to touch `body.Tip` yourself when: **inserting** a feature mid-tree (`body.insertObject(feature, target, after=True/False)`, then optionally re-point `Tip`), reordering/removing features, or building with plain `doc.addObject(...)` + `body.addObject(obj)` instead of `newObject` (same auto-Tip rule applies either way).
- Every solid feature implicitly chains off the *previous* Tip via its own `BaseFeature` property — that link is managed by `insertObject`, never set directly.

```python
body = doc.addObject('PartDesign::Body', 'Body')
sketch = body.newObject('Sketcher::SketchObject', 'Sketch')
sketch.AttachmentSupport = [(doc.getObject('XY_Plane'), '')]
sketch.MapMode = 'FlatFace'
# ... addGeometry/addConstraint -- see sketcher-scripting.md ...
pad = body.newObject('PartDesign::Pad', 'Pad')
pad.Profile = sketch
pad.Length = 20.0
doc.recompute()
print(body.Tip.Name)   # 'Pad' -- moved automatically; the Sketch never touched Tip
```

**Origin planes in 1.1**: `body.Origin` is an `App::Origin` (a `LocalCoordinateSystem`); its 6 datum elements + origin point live in `body.Origin.Group`, each tagged with a `Role` string (`'X_Axis'/'Y_Axis'/'Z_Axis'`, `'XY_Plane'/'XZ_Plane'/'YZ_Plane'`, `'Origin'`). `doc.getObject('XY_Plane')` (above) resolves fine for the *first* Body in a document, since that's also the auto-assigned object Name — but a second Body's planes get suffixed names (`XY_Plane001`, ...). For multi-Body scripts, look up by Role instead of guessing the Name:
```python
xy_plane = next(o for o in body2.Origin.Group if o.Role == 'XY_Plane')
```
The pre-1.1 `body.Origin.OriginFeatures[i]` indexed list is gone in 1.1 — don't reach for it.

## Pad / Pocket

Both subclass `FeatureExtrude` → `ProfileBased`.

- **`Profile`** (LinkSub) — sketch or face.
- **`Length`** / **`Length2`** — plain float in (mm); reading back gives a `Quantity` (`.Value` for math — see `execution-model.md`).
- **`Type`** / **`Type2`** — per-side enum. Pad: `'Length'|'UpToLast'|'UpToFirst'|'UpToFace'|'UpToShape'`. Pocket: `'Length'|'ThroughAll'|'UpToFirst'|'UpToFace'|'UpToShape'`.
- **`SideType`** — `'One side'|'Two sides'|'Symmetric'`. **Use this, not `Midplane`** — `Midplane` (bool, inherited from `ProfileBased`) still works in 1.1 but is deprecated in favor of `SideType = 'Symmetric'`; a script that sets it gets a deprecation warning.
- **`Reversed`** (bool, inherited) — flip direction. **Direction matters and the default trips people up.** A Pad extrudes ALONG the sketch normal; a Pocket removes material OPPOSITE the sketch normal. A sketch attached to a datum plane (`AttachmentSupport = (doc.XY_Plane, [''])`) takes that plane's fixed normal (+Z for XY), **not** the normal of whatever solid face happens to sit there — so a hole sketched on `XY_Plane` to cut a solid that was padded UP (+Z) from that same plane pockets DOWNWARD into empty space and removes nothing (valid shape, no error, unchanged volume). Get the direction right: attach the cut sketch to the solid's actual top/bottom **face** (a face normal points out of the material, so the default opposite-normal pocket cuts into it) — the robust habit; or, if you sketch on a datum plane, determine which side the solid sits on (compare its `BoundBox.Center` to the sketch plane along the normal) and set `Reversed` so the cut aims into it. You don't have to check by hand: after any `run_python` that changes a PartDesign feature, the tool result reports per feature how much material it added/removed and how the solid count changed (e.g. `HolePocket (Pocket): removed 7069 mm³ · solids 1→1`). A cut that reports `no volume change` is auto-escalated with the profile normal, which side the solid is on, and the exact `Reversed` value to fix it; a feature that leaves `>1` disconnected solids (didn't touch the solid, or split it) is flagged too — a Body must stay one contiguous lump.
- **`UpToFace`** / **`UpToShape`** (LinkSub / LinkSubList) — termination target, only meaningful when `Type` selects it.
- **`UseCustomVector`** (bool) + **`Direction`** (Vector) for a custom direction, or **`ReferenceAxis`** (LinkSub) to derive one from an edge/datum line; **`AlongSketchNormal`** (bool) picks whether `Length` is measured along that axis or the sketch normal.
- **`Offset`**, **`TaperAngle`** / **`TaperAngle2`** — also per-side.

```python
pad = body.newObject('PartDesign::Pad', 'Pad')
pad.Profile = sketch
pad.Length = 20.0
pad.SideType = 'Symmetric'
doc.recompute()

pocket = body.newObject('PartDesign::Pocket', 'Pocket')
pocket.Profile = sketch2
pocket.Type = 'ThroughAll'
doc.recompute()
```

## Revolution / Groove

Subclass `ProfileBased` directly (not `FeatureExtrude`) — no `SideType`; `Midplane`/`Reversed` are the live (non-deprecated) symmetry controls here.

- **`Profile`**, **`ReferenceAxis`** (LinkSub tuple) — `(sketch, ['V_Axis'])`, or `(sketch, ['Edge1'])` / an external datum line.
- **`Angle`** (deg, 0–360, no negatives — use `Reversed`) / **`Angle2`** when `Type = 'TwoAngles'`.
- **`Type`** — Revolution: `'Angle'|'UpToLast'|'UpToFirst'|'UpToFace'|'TwoAngles'`. Groove: same but `'ThroughAll'` instead of `'UpToLast'`.

```python
rev = body.newObject('PartDesign::Revolution', 'Revolution')
rev.Profile = sketch
rev.ReferenceAxis = (sketch, ['V_Axis'])
rev.Angle = 360.0
doc.recompute()
```

## Additive/Subtractive Loft and Pipe

The registered types are **`PartDesign::AdditiveLoft`/`SubtractiveLoft`** and **`PartDesign::AdditivePipe`/`SubtractivePipe`** — `Loft`/`Pipe` alone are abstract base classes, don't pass those strings to `newObject`.

- **Loft**: `Profile` (inherited) is the first section; **`Sections`** (`PropertyLinkSubList`) is the list of additional cross-section sketches; **`Ruled`** (bool, straight-line lofting vs. smoothed) and **`Closed`** (bool, wraps last section back to the profile) round it out.
- **Pipe** ("Sweep" group internally): **`Spine`** (LinkSub) — the path; `Profile`/**`Sections`** — the swept cross-section(s); **`SpineTangent`** (bool); **`AuxiliarySpine`** (LinkSub) + `AuxiliarySpineTangent`/`AuxiliaryCurvilinear` to control orientation with a second curve; **`Mode`** — `'Standard'|'Fixed'|'Frenet'|'Auxiliary'|'Binormal'`; **`Transition`** — `'Transformed'|'Right corner'|'Round corner'`.

```python
loft = body.newObject('PartDesign::AdditiveLoft', 'Loft')
loft.Profile = sketch1
loft.Sections = [(sketch2, []), (sketch3, [])]
loft.Ruled = False
doc.recompute()

pipe = body.newObject('PartDesign::AdditivePipe', 'Pipe')
pipe.Profile = profile_sketch
pipe.Spine = (path_sketch, [])
pipe.Mode = 'Frenet'
doc.recompute()
```

## Datum features

Still **`PartDesign::Plane`/`Line`/`Point`/`CoordinateSystem`** in 1.1 — the GUI menu now favors a more generic `Std_DatumPlane` command, but the type added inside a Body's tree is unchanged (verified in source: `DatumPlane.cpp` still registers `PartDesign::Plane`). All subclass `Part::Datum` → `Part::AttachExtension`, so they take the same attachment properties as a Sketch:

- **`AttachmentSupport`** (`PropertyLinkSubList`) — `[(obj, [subnames])]` tuples, e.g. `[(pad, ['Face1'])]`.
- **`MapMode`** (enum) — e.g. `'FlatFace'`, `'ObjectXY'`, `'Plane3Points'`, `'ParallelPlane'` — `inspect_api` for the full `AttachEngine` list.
- **`AttachmentOffset`** (`PropertyPlacement`) — a `FreeCAD.Placement` stacked on top of the attachment.

```python
plane = body.newObject('PartDesign::Plane', 'Plane')
plane.AttachmentSupport = [(pad, ['Face1'])]
plane.MapMode = 'FlatFace'
plane.AttachmentOffset = FreeCAD.Placement(FreeCAD.Vector(0, 0, 5), FreeCAD.Rotation())
doc.recompute()
```

## Patterns

`LinearPattern` / `PolarPattern` / `Mirrored` / `MultiTransform` all subclass `Transformed`, which holds **`Originals`** (`PropertyLinkList`) — the feature(s) being repeated.

- **LinearPattern**: `Direction` (LinkSub), `Reversed` (bool), `Mode` — `'Extent'|'Spacing'`, `Length`, `Occurrences` (int). A parallel `Direction2`/`Length2`/`Occurrences2`/... set exists for a second direction.
- **PolarPattern**: `Axis` (LinkSub), `Angle`, `Occurrences`, `Reversed`, `Mode` (same enum as above).
- **Mirrored**: `MirrorPlane` (LinkSub) — a planar face or datum plane.
- **MultiTransform**: `Transformations` (`PropertyLinkList`) — a chain of Linear/Polar/Mirrored feature objects created but **not** added to the Body (don't `newObject` them — build with plain constructors via `doc.addObject` and never insert), composed in order.

```python
pattern = body.newObject('PartDesign::PolarPattern', 'PolarPattern')
pattern.Originals = [hole]
pattern.Axis = (sketch, ['V_Axis'])
pattern.Angle = 360.0
pattern.Occurrences = 6
doc.recompute()
```

## PartDesign Boolean

`PartDesign::Boolean` combines other **Bodies** into the active one — different from the Part-workbench booleans in `part-draft-recipes.md`, which combine raw shapes/`Part::Feature` objects. `Type` is `'Fuse'|'Cut'|'Common'`; the tool bodies go in **`Group`** (`PropertyLinkList`, inherited from `App::GeoFeatureGroupExtension` — historically named `Bodies` in old files, renamed to `Group`).

```python
boolean = body.newObject('PartDesign::Boolean', 'Boolean')
boolean.Type = 'Cut'
boolean.Group = [tool_body]
doc.recompute()
```

## Fillet / Chamfer / Thickness

All three subclass `DressUp`, which holds **`Base`** (`PropertyLinkSub`, a *single* link with a sub-element list — not a `PropertyLinkSubList`) — `(feature, ['Edge3', 'Edge7'])`: the Tip-chain feature plus the edges (Fillet/Chamfer) or faces (Thickness) to dress. For "whole object, no sub-element" on a `PropertyLinkSub` like this `Base`, pass the bare object or `(obj, [])` — *not* an empty string; that empty-string idiom (`(obj, '')`) is specific to `PropertyLinkSubList` properties like `AttachmentSupport` above, a different property type.

- **Fillet**: **`Radius`**, `UseAllEdges` (bool).
- **Chamfer**: **`Size`** / `Size2`, **`Angle`**, **`ChamferType`** — `'Equal distance'|'Two distances'|'Distance and Angle'`, `FlipDirection` (bool).
- **Thickness**: **`Value`** (wall thickness), **`Mode`** — `'Skin'|'Pipe'|'RectoVerso'`, **`Reversed`** (bool, **defaults to `True`** — thickens toward the solid's interior, the opposite default sense from Fillet/Chamfer/Pad's `Reversed`), `Join` — `'Arc'|'Intersection'`, `Intersection` (bool). `Base`'s sub-elements are the faces to *remove* (open up); leaving it empty just copies the input shape unchanged, no error.

```python
fillet = body.newObject('PartDesign::Fillet', 'Fillet')
fillet.Base = (pad, ['Edge3', 'Edge7'])
fillet.Radius = 2.0
doc.recompute()

thickness = body.newObject('PartDesign::Thickness', 'Thickness')
thickness.Base = (pad, ['Face6'])   # the face(s) to open -- confirm the name via get_objects/inspect_api
thickness.Value = 2.0
thickness.Mode = 'Skin'
doc.recompute()
```

## Hole

`PartDesign::Hole` (subclasses `ProfileBased`) has ~25 properties — not worth memorizing. The essentials: `Profile` (a sketch with point(s) marking hole centers), `Diameter` (default 6.0mm, used when not threaded), `DepthType` (`'Dimension'|'ThroughAll'`), `Depth`, `Threaded`/`ModelThread` (bool), `ThreadType`/`ThreadSize`/`ThreadClass`/`ThreadFit` (enums), `HoleCutType` (countersink/counterbore), `DrillPoint`/`DrillPointAngle`. **Call `inspect_api` on the live `Hole` object before guessing enum strings** — a wrong value fails on recompute, not at assignment.

```python
hole = body.newObject('PartDesign::Hole', 'Hole')
hole.Profile = point_sketch
hole.Diameter = 5.0
hole.DepthType = 'ThroughAll'
doc.recompute()
```

## Common mistakes

- **Skipping `doc.recompute()` between dependent steps** — a feature referencing a not-yet-recomputed Profile/Base can fail, or build against stale topology.
- **Creating with `doc.addObject(...)` and forgetting `body.addObject(feature)`** — the object exists in the document but isn't in the Body's tree, never becomes `Tip`, and a later `Profile = feature` reference won't behave like a proper PartDesign feature.
- **Using deprecated `Midplane = True` on Pad/Pocket** — still works, but logs a warning; use `SideType = 'Symmetric'`.
- **Assigning `MirrorPlane`/`ReferenceAxis`/`Direction`-style LinkSub properties as a bare object** instead of a `(object, [subnames])` tuple — works for some (whole-object references tolerate `[]`), but face/edge-specific ones need the sub-element name.
- **Reaching for `body.Origin.OriginFeatures`** — removed in 1.1; use `body.Origin.Group` filtered by `.Role`, or `doc.getObject('XY_Plane')` for the first Body only.
- **Referencing `body.Tip` before any solid feature exists** — it's `None` until the first Pad/Pocket/Revolution/etc.
- **Building a `MultiTransform` sub-transform feature with `body.newObject(...)`** — it must stay un-inserted (created but not added to the Body); inserting it directly makes it a competing Tip-chain feature instead of a step inside the MultiTransform.

## Sources

- FreeCAD source, tag `1.1.1` (github.com/FreeCAD/FreeCAD) — `src/Mod/PartDesign/App/{FeaturePad,FeaturePocket,FeatureExtrude,FeatureSketchBased,FeatureRevolution,FeatureGroove,FeatureLoft,FeaturePipe,FeatureBoolean,FeatureDressUp,FeatureFillet,FeatureChamfer,FeatureHole,FeatureTransformed,FeatureLinearPattern,FeaturePolarPattern,FeatureMirrored,FeatureMultiTransform,DatumPlane,Body}.{cpp,h}`; `src/App/{Datums,Origin,GroupExtension}.{h,pyi}`; `src/Mod/Part/App/{DatumFeature,AttachExtension}.h`
- [PartDesign Pad](https://wiki.freecad.org/PartDesign_Pad), [PartDesign Pocket](https://wiki.freecad.org/PartDesign_Pocket)
- [PartDesign Revolution](https://wiki.freecad.org/PartDesign_Revolution), [PartDesign Groove](https://wiki.freecad.org/PartDesign_Groove)
- [Body](https://wiki.freecad.org/Body), [Part DatumPlane](https://wiki.freecad.org/Part_DatumPlane)
