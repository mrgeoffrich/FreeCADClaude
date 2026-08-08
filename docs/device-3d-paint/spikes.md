# Device 3D paint — Phase 0 spike results

Measurements for the four open spikes in `design.md`: T1, U1, U2, S3. Nothing
was built; every number below comes from a throwaway script.

**Environment.** FreeCAD 1.1.1 (Revision 20260414), bundled Python 3.11.14,
macOS on Apple silicon. Primary document
`~/FreeCADClaude/20260804-172047-71a6ac/freecadclaude_eval_m5_mount.FCStd` — the
same 68-object / 3-body file the design doc's table comes from. S3 builds its own
document. No packages were installed.

**Not run.** D1 needs the real devices and phase 1 to exist. U3 is conditional on
U1 failing, and U1 did not fail. U4 needs a painting client.

Three findings do not belong to any one spike and are collected at the end. One
of them touches code that ships today.

---

## T1 — `SoTexture2` through `_save_view_png`

**Verdict: it renders, and alpha blends against the geometry behind it.**
Decision 5 stands as written; the per-face-tint fallback stays a degradation
path rather than the primary one. Risk #3 closes.

### What was run

A throwaway offscreen view over a 10 mm `Part::Box`, top view, `fitAll`. An
`SoSeparator` holding `SoTexture2` + `SoTextureCoordinate2` + `SoCoordinate3` +
`SoIndexedFaceSet` — a four-vertex quad at z = 10.05, just above the box's top
face — inserted with the same camera-search logic as
`tools_cutaway._insert_clip_plane`. Saved through the real recipe:
`SavePicture = FramebufferObject`, `view.saveImage(png, 400, 300, "#3A4A5A", "", 8)`.
Then the PNG read back with `QImage` and the pixels counted.

The texture is a 64² magenta/green checker, so a wrong result is unmistakable
rather than a judgement call.

### Numbers

| case | centre pixel | reading |
|---|---|---|
| baseline, no overlay | (102, 107, 112) | the box's own top face |
| plain `SoMaterial` quad | (201, 10, 200) | insertion works, geometry in world space |
| `SoTexture2` RGB, default MODULATE | (163, 0, 162) / (0, 163, 0) | texture renders, modulated by the material's 0.64 grey |
| `SoTexture2` RGB, REPLACE | (255, 0, 255) / (0, 255, 0) | exact texel colour |
| `SoTexture2` RGBA α=128, MODULATE | (132, 53, 137) / (51, 135, 56) | exactly ½ texel + ½ box — **true blending against what is behind** |
| `SoTexture2` RGBA α=128, DECAL | (209, 81, 209) | blends with the quad's *own* material and stays opaque — not the overlay we want |

The overlay's real shape is a mostly-transparent atlas, so that was measured too:
with α = 0 outside a painted disc, the unpainted region reads (102, 107, 112) —
**pixel-identical to the bare-box baseline** — while the painted region reads
(163, 0, 162).

Cost of a real atlas: 1024² RGBA (4 MB), `image.setValue` **0.1 ms**,
`saveImage` **6.3 ms**. The first `saveImage` in a process costs 75.7 ms; that
is warm-up, not the texture.

Three things that could have made this fragile, and did not:

- **No preference changes the answer.** `RenderCache` 1 and 2 and
  `TransparentObjects` 1 all produce identical pixel histograms. The blend is not
  something a user's settings can turn into stipple.
- **The buffer's lifetime is not load-bearing.** Dropping the Python `bytes`
  reference, `gc.collect()`, and churning the allocator with three same-sized
  allocations before rendering gave a pixel-identical image. (0.1 ms for 4 MB
  looked like NO_COPY; it does not behave like it.)
- **`_insert_after_camera` works for geometry, not just a clip plane.** The
  camera search fell to the `index-0` fallback in every case
  (`path.getLength() != 2`) and world-space coordinates still landed correctly.

### What could not be run, and what it costs

**T1 cannot run under `QT_QPA_PLATFORM=offscreen` on macOS.** Qt's offscreen
platform plugin has no GL at all: *"This plugin does not support
createPlatformOpenGLContext"*, *"QOpenGLWidget is not supported on this
platform"*, *"imageFromFramebuffer failed because no context is active"*. The
available plugins are `cocoa`, `offscreen` and `minimal`, and only `cocoa` has a
context. This is not specific to textures — **no capture of any kind renders
headlessly on this platform**, which is worth knowing for anything else that
wants to test the render path.

It does not need a human at a GUI, though. `freecadcmd` +
`FreeCADGui.showMainWindow()` with the default (cocoa) platform gets a real GL
context and runs the whole thing unattended. That is how these numbers were
taken.

Two caveats on the result: it is **one GL stack** (Apple silicon, macOS), so
Windows and Linux are unverified; and the process exits with SIGSEGV during
interpreter teardown *after* all output is written — a
`freecadcmd` + `showMainWindow` shutdown artifact, not a result, but it means
any future script has to flush as it goes rather than at the end.

---

## U1 — unwrap cost and surface-type access

**Verdict: the geometric scheme works — 97.5% of faces get a real chart — but
only with two additions the design does not currently have.** Without them the
rate is 76.7%, which would not have been good enough.

### Surface types, over all 885 faces

Excluding the nine `App::Plane` origin pseudo-faces, whose area is ±1e100.

| surface | faces | share | path |
|---|---:|---:|---|
| `Part::GeomCylinder` | 298 | 33.7% | analytic |
| `Part::GeomBSplineSurface` | 209 | 23.6% | **generic** |
| `Part::GeomPlane` | 179 | 20.2% | analytic |
| `Part::GeomToroid` | 113 | 12.8% | **generic, as designed** |
| `Part::GeomCone` | 86 | 9.7% | analytic |

`.Axis`, `.Position`, `.Center`, `.Radius`, `.Apex`, `.SemiAngle`,
`.MajorRadius` and `.MinorRadius` are all present and readable on every face of
the matching kind, with no failures. One trap: **`.Axis` does not exist on a
`BSplineSurface`** and raises `AttributeError`, so the generic path must branch
on `TypeId` before touching it rather than trying and catching.

The design says *"Most of a machined part is those three"* (plane, cylinder,
cone). On this document that is **63.6%** — true, but by less margin than the
sentence implies. Adding a toroid path takes it to **76.4%**, and B-splines at
23.6% are the whole of the residual risk.

### Cost

| | |
|---|---|
| analytic unwrap, SoleFillet (81 faces, 8,084 triangles) | **7.1 ms** |
| the same with seam handling | **12–15 ms** |
| `mesh.Topology` + 81 `.Surface` reads | 3.0 ms |
| whole document (885 faces, 74,956 triangles) — mesh | 267 ms |
| whole document — unwrap | **117 ms** |

Comfortably inside the design's "well under 30 ms" for a single part, and it sits
next to a tessellation costing 4–7× more.

**The route not taken, now with a number.** `Surface.parameter(v)` over 5,000
calls on SoleFillet's faces: 528.7 ms, **105.7 µs per call**. Per surface kind:

| surface | µs/call |
|---|---:|
| `BSplineSurface` | 77.8 |
| `Plane` | 9.1 |
| `Cylinder` | 5.4 |
| `Toroid` | 5.2 |

SoleFillet's charts hold ~5,500 vertices, so the parametric route is **0.3–0.6 s
against 7 ms** — 40–85× — on the GUI thread. Decision 2's refusal is no longer an
assertion.

### The flip rate, which is the actual result

| | fill_only faces | rate |
|---|---:|---:|
| as the design describes the algorithm | 206 / 885 | **23.3%** |
| with the two additions below | 22 / 885 | **2.49%** |

With the additions, every remaining fill_only face is a **B-spline**; planes,
cylinders, cones and toroids produce **zero**. The 22 are real folds, not
borderline — the worst carry 51.65% of their face area reversed, which is exactly
what step 3 exists to catch. A further 28 B-splines unwrap without flipping but
with a texel-density spread over 3×, which is Risk 2's "distorted but not
flipped" case; it is detectable with the same pass.

### What the design has to change

1. **Add a toroid path.** 113 faces (12.8%) are toroids, and the design routes
   them to the generic planar projection, where **38.1% of them flip**. Unrolled
   as a tube — `u = θ_major · R`, `v = θ_minor · r` — **none** do. This matters
   more than the share suggests: fillets are toroids, and a fillet band wrapping
   a corner is precisely the narrow feature a user paints on. Not isometric (a
   torus has Gaussian curvature) but the distortion is `(R + r·cos φ)/R`, which
   is small wherever `r ≪ R`.

2. **Seam-crossing triangles must be split, and step 1's parenthetical is
   wrong.** The design says of the cylinder that *"the seam sits at the
   parametric start, which is already an edge"*. A closed face's mesh **shares
   its seam vertices** between the two sides of that edge, so no choice of cut
   angle separates them and the seam triangles flip wherever the seam is put.
   This is not a corner case: **46.3% of cylinders and 38.1% of toroids** fail
   this way, and it is the whole of the difference between 23.3% and 2.5%. The
   fix is O(triangles) — a triangle whose vertex angles span more than π
   straddles the seam and its trailing vertices get a duplicate at +2π. Note the
   consequence for the de-share step: **de-sharing is two-level**, once per face
   and again at the seam. Cost measured at 1,222 extra vertices over 885 faces
   and 74,956 triangles, under 2% on top of the per-face de-share.

3. **Step 3's flip test must be area-weighted.** A bare winding-sign test flags
   degenerate slivers carrying ~1e-7 mm². On B-splines it reported 11 of 83 faces
   flipped where only 5 carried more than 0.1% of their face's area — so an
   unweighted test roughly doubles the B-spline fill_only rate for nothing. The
   threshold used throughout above is 0.1% of the face's 3D area.

4. **Restate the "most of a machined part" claim** as 63.6% analytic, 76.4% with
   the toroid path, and name B-splines as where the whole residual risk lives.

---

## U2 — segment ordering

**Verdict: ordered. Segment `i` is `shape.Faces[i]` is `Face{i+1}`.**

### What was run

The design names one `isSame` check. That answers half the question — it relates
`getElement("FaceN")` to `Faces[N-1]`, and says nothing about which face a
*segment* covers, which is what everything downstream assumes. So both.

**(a) Naming.** `shape.getElement(f"Face{i+1}").isSame(shape.Faces[i])` over
every face of every shape in the document: **894 / 894 match, zero mismatches.**

**(b) Pairing.** Four tests, because the obvious one is not discriminating:

- `mesh.countSegments() == len(shape.Faces)` on every shape — confirms the
  design's S1 again.
- **The segments partition the mesh exactly.** SoleFillet: 8,084 facets, 8,084
  assigned, 0 duplicated, 0 unassigned, and the sum of segment areas equals the
  total mesh area to the last digit. Same on PegChamfer and Scallops.
- **Facet centroids of segment `i` sit on face `i`.** Worst distance across the
  whole document 0.187 mm (LegFillet), most under 0.05 mm.
- **Argmin over every face, not just the neighbour.** The first attempt compared
  segment `i` against face `i+1` as a control, and that control is worthless:
  adjacent BRep faces touch, so a centroid near a shared edge is close to both.
  Testing instead which of *all* the shape's faces minimises the distance:
  **79 / 79 sampled segments across six objects picked their own index.**
- Segment area against `face.Area`: within 0.83% on every face but one.

### The one face, and it is not an ordering problem

**`SoleFillet.Face81`** — a 735.1 mm² plane — gets 10 facets totalling
**1.84 mm²**. 99.75% of its area is simply absent from the mesh.

The cause is the document, not the mesher: **`SoleFillet.Shape.isValid()` is
`False`**, with 19 of its 81 faces invalid and Face81 among them. It shows up in
the totals as a 3.1% shortfall — mesh area 22,982.9 against `shape.Area`
23,724.1 — which is almost exactly Face81's contribution.

Two things follow. The smaller one is that **the design's primary measurement
document has an invalid tip shape**; the numbers in the design doc's table are
still what that document produces, but it is not a clean specimen.

The one that changes the design: **a face can ship in the header with an atlas
rectangle and no triangles to paint on.** The user taps it, nothing happens, and
nothing anywhere says why. The exporter should compare each segment's area to
`face.Area` and mark a face that lost most of its tessellation — `fill_only` is
the wrong label (nothing will render), so it wants either its own flag or an
exclusion plus a line in the tool result. Cheap: the areas are already summed for
the chart-scaling step.

(Nine segments were empty across the document; all nine are origin-plane
pseudo-faces on `App::Plane` objects, which the exporter excludes anyway. Also
noted without chasing: 35 shapes carry faces here, where the design doc's table
says 34.)

---

## S3 — nested placements

**Verdict: `mesh.Topology` is in the object's own local frame.
`getGlobalPlacement()` is needed, and the design does not currently mention it at
all.**

### What was run

A fresh document in the scratchpad: an `App::Part` "Assembly" at (100, 50, 25)
rotated 30° about Z; a `Part::Box` inside it at (5, 0, 0) rotated 15° about X; a
`PartDesign::Body` inside it at (0, 40, 0) rotated 20° about Y carrying a Pad; and
a top-level box with the same placement as the nested one, as a control.

| reading | Box | Pad |
|---|---|---|
| `Shape.BoundBox` | [5.00, −1.29, 0.00] .. [25.00, 9.66, 7.42] | [0, 0, 0] .. [8, 6, 4] |
| `mesh.Topology` bounds | identical | identical |
| `Part.getShape(obj, transform=True)` | identical | identical |
| `Part.getShape(container, "<Name>.")` | [99.50, 51.38, 25.00] .. [122.30, 70.87, 32.42] | **null shape** |
| `getGlobalPlacement() · Placement⁻¹` applied | [99.50, 51.38, 25.00] .. [122.30, 70.87, 32.42] | [77.00, 84.64, 22.26] .. [87.70, 94.28, 28.76] |

The control box, identical placement but no container, comes out the same by
every route — so the discrepancy is the container transform and nothing else.

Four specifics worth not rediscovering:

- **The mesher adds no transform of its own.** `mesh.Topology`'s bounds equal
  `Shape.BoundBox` exactly, so the whole question is what frame `Shape` is in.
- **`Part.getShape(obj, transform=True)` does not apply the container
  transform.** It applies the object's own `Placement`, which `Shape` already
  carries, so it returns the same thing as `obj.Shape`. The argument name reads
  like it would do more.
- **`Part.getShape(container, "Box.")` does return the global shape** — but only
  for an object directly under that container. The same call for the `Pad`, which
  sits inside the Body inside the Assembly, returns a null shape (a ±1e308
  bounding box), because the subname would have to be `"Body.Pad."`. Building
  that path correctly is exactly the fiddly part, which is the argument for not
  using this route.
- **A PartDesign feature is nested twice over.** `pad.Placement` is identity,
  `pad.Shape` is in **body-local** coordinates, and `pad.getGlobalPlacement()`
  returns the composed container ∘ body placement. One call covers both levels,
  which is why it is the right call.

The transform to apply is
`obj.getGlobalPlacement().multiply(obj.Placement.inverse())` — `Shape` already
carries `obj.Placement`, so composing the global placement alone would apply it
twice. On the top-level control this composes to identity, as it must.

### What the design has to change

`mesh_export.py` must apply that transform, and the design should say so
explicitly. Decision 4 already commits to world millimetres for every coordinate
the device sends back, so the mesh has to be in the same frame or **every mark
is offset by the container transform, silently and constantly** — Risk 5's exact
failure. `send_model_to_device` must do it per object, since two objects in one
export can live in different containers with different transforms.

---

## Found in passing

### `Shape.BoundBox` changes after tessellation, and `hashCode()` does not

| | diagonal | `diag / 1200` |
|---|---:|---:|
| cold | 160.537254243 | 0.133781045 |
| after one `meshFromShape` | 158.880460401 | 0.132400384 |

A 1.03% shrink: `BoundBox` is computed from the triangulation once one exists.
`obj.Shape.copy()` returns the cold value; re-reading `obj.Shape` does not.
`hashCode()` is **1102451014 before and after**.

**For this design:** `LinearDeflection = diag/1200` is therefore not a stable
function of the shape, and the mesh is not reproducible unless the deflection is
pinned. Measured on SoleFillet, reproducibly and across processes:

| | triangles |
|---|---:|
| one held `Shape`, diagonal hoisted, meshed 5× | 8,084 every time |
| `obj.Shape` re-read before each of 5 meshes | 8,126 every time |
| a fresh `.copy()` before each of 5 meshes | 8,084 every time |

So: compute the diagonal once from a known state (a `.copy()` is the cheapest
way to guarantee it), or accept that two exports of the same unedited part
differ. Nothing in the header may key on the triangle count. The topology is
unaffected throughout — 81 faces, 81 segments, every time.

**Beyond this design, and worth checking before phase 1:**
`diagnostics._shape_metrics` caches `(contribution, solids, faces, edges,
vertexes, bbox, valid)` under `(object name, Shape.hashCode())`, on the stated
grounds that *"they are a pure function of the shape — a rebuilt shape gets a new
hash and misses"*. The **bbox is not**: it moves ~1% once the shape has been
tessellated, with no hash change. Nothing in the addon tessellates today, which
is why this has never bitten. This feature would tessellate routinely.

### `meshFromShape` does not dirty the document

`doc.isTouched()` stayed `False` and both objects stayed `Up-to-date` across
meshing. So invariant 2 holds where it matters, even though the tessellation is
written back into the shape — the design's phrase *"pure reads of `Shape`"* is
not literally true, but nothing observable at the document level changes.

### The primary measurement document is not a clean specimen

`SoleFillet.Shape.isValid()` is `False`, 19 of 81 faces invalid. See U2.
