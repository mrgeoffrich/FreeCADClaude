# Device 3D paint — technical design

Send a part from the live document to a tablet as a plain grey solid, let the
user **paint on its actual surface** with a stylus, send it back, and have
FreeCAD re-render the paint from angles chosen to show it.

This is the 3D successor to `docs/device-annotation-{design,plan}.md`. Almost
all of the infrastructure already exists — the LAN server, the token, the QR
pairing, the upload route, the session folders, the offscreen render path — and
this design changes none of it. What is new is one file format going out, a
texture and two annotation types coming back, and a WebGL viewer in the page
that already ships.

The use case that drives it is the one the 2D flow answers badly:

> *"Not that corner — **that** one. And thin this whole area, not that line."*

On a screenshot the user can only circle a region of a projection, and a
projection is ambiguous the moment the part has depth. On the solid itself the
mark lands on a face, and the face has a name.

## What this is not

**It is not just a prettier picture.** The valuable output of this round trip is
a *mark whose position on the solid is known exactly* — as world-space
millimetres and the `FaceN` sub-elements it covers, because that is what
`run_python`, `describe_objects` and a PartDesign dress-up can all act on. The
picture is what the user works in; the coordinates are what Claude works from.
The design carries both and keeps them consistent, and neither is derived from
the other after the fact.

## Scope

**In, for v1:**

- A new tool pair: `send_model_to_device`, `read_device_paint`.
- One new outbound artifact: a tessellated mesh in a small purpose-built binary
  format, carrying **per-triangle BRep-face identity**, **per-vertex UVs into a
  per-face texture atlas**, and the BRep edge polylines.
- One new HTTP route, `GET /api/mesh/<id>`, and one new multipart part on the
  existing upload.
- A WebGL viewer in the existing page: flat grey shaded solid, black BRep edges,
  turntable orbit, pinch zoom.
- **Surface painting**: a brush with a size slider, painting into the atlas.
  A thin brush is ink; a fat brush is a wash; the largest setting fills a whole
  face.
- Annotation document v1 extended with two new annotation types — additive, no
  version bump.
- A contact-sheet render on the way back: N angles chosen from where the paint
  actually landed, composited into one PNG.

**Out, deliberately:**

- **A general triangle-soup UV unwrapper** (xatlas and friends). Priced in
  Decision 2 and rejected — for a BRep the charts are given, not inferred.
- Editing anything on the device. It marks; it does not model.
- Section planes, exploded views, measurement on the device. All are cheap once
  the picker exists — see Deferred — but each needs its own story.
- Perspective camera, lighting controls, material display. The model is grey.
- WebXR / AR. Needs a secure context; plain HTTP rules it out.
- Multiple documents in one shot. One document, one or more objects from it.

## The invariants

Unchanged from the first draft, and none of the new information touches them.

**1. `device_server.py` still imports no FreeCAD and no Qt.** The mesh is
tessellated, unwrapped and written by a tool on the GUI thread and handed to the
server as a *path plus a plain dict*, exactly as the PNG already is. The server
gains one route that opens a file it was told about and one extra multipart part
it stores opaquely. No handler resolves a session folder, reads a preference,
touches a document, or names a face. `eval/test_device_server.py` keeps running
under a bare `python3`.

**2. The round trip is non-mutating at both ends.** The outbound tessellation
and unwrap are pure reads of `Shape`. The inbound render goes through
`render._offscreen_shot` and draws the paint as Coin nodes inserted into the
*throwaway* view — the same mechanism `cutaway` uses for its `SoClipPlane`, and
it dies with the view. No temporary document object, no `ViewObject` property
left changed, no `Modified` flag flipped.

**3. Every FreeCAD name the device sends back is a name FreeCAD gave it.** The
mesh header carries the `(object, FaceN)` table and the atlas rectangle each
face owns; the device echoes indices into it. The device never constructs a
sub-element name and never asks the server for one. This is what lets a wash
come back as *"covers Face7 (68%) and Face11 (12%) of Pad001"* without a single
line of FreeCAD reachability added to the HTTP surface.

## Architecture

```
chat panel (GUI thread)
  └─ AgentWorker ─▶ claude CLI ─▶ MCP ─▶ gui_bridge ─▶ tools_device_paint
                                                        │            ▲
                send_model_to_device ───────────────────┘            │
                  tessellate · unwrap · pack                         │
                  → .fcmesh + thumbnail.png → publish()              │
                read_device_paint  ──────────────────────────────────┘
                  resolve faces · choose angles · Coin texture overlay
                  · _offscreen_shot × N · contact sheet → (text, png)
                                                                     ▼
      iPad / tablet  ◀── HTTP (LAN, token) ──▶  ThreadingHTTPServer
        WebGL viewer                             GET  /api/latest      (+ mesh_url)
        + brush painting into                    GET  /api/image/<id>  (thumbnail)
          the atlas                              GET  /api/mesh/<id>   ← NEW
                                                 POST /api/upload      (+ texture part)
                                                 GET  /api/events      (unchanged)
```

Artifacts land in the existing `<session>/mobile/`, pruned by `_session_subdir`
like everything else: `sent_<label>.fcmesh`, `sent_<label>.png` (thumbnail),
`upload_*.png`, `upload_*_texture.png` and `upload_*.json`. The contact sheet
goes to `<session>/captures/paint_*.png`.

## Measured, not assumed

FreeCAD 1.1.1, `freecadcmd`, the 68-object / 3-body M5 mount eval document.
`MeshPart.meshFromShape(Shape=shape, LinearDeflection=diag/1200,
AngularDeflection=0.35, Relative=False, Segments=True)`:

| object | BRep faces | triangles | mesh ms | segments |
|---|---:|---:|---:|---:|
| SoleFillet | 81 | 8,084 | 44.8 | 81 |
| Scallops | 69 | 7,050 | 18.3 | 69 |
| PuckRecess | 47 | 3,622 | 10.5 | 47 |
| PegChamfer | 44 | 3,264 | 12.1 | 44 |
| Puck | 44 | 3,264 | 12.4 | 44 |
| PlatePad | 6 | 12 | 0.4 | 6 |
| Table (whole) | 81 | 8,084 | 58.6 | 81 |

Three things this settles, and one it does not:

- **`Segments=True` gives exactly one segment per BRep face.**
  `mesh.countSegments() == len(shape.Faces)` on all 34 shapes in the document,
  every row. **Segment *ordering* is still unverified** — that segment *i*
  corresponds to `Face{i+1}` needs the `shape.getElement("Face7").isSame(
  shape.Faces[6])` check. Treat one-per-face as confirmed and the mapping as
  pending.
- **Tessellation is cheap.** 58.6 ms worst case, which is *less than the
  offscreen render it sits beside*. The first draft ranked a GUI-thread freeze
  as risk #1; that was wrong by two orders of magnitude and the risk is
  demoted. Still untested: a ~1,500-face assembly, and Windows.
- **Triangle counts are ~10× lower than the first draft assumed.** 8,084, not
  the 40–80k predicted. Every size budget below is re-derived from this, and
  gzip stops being worth the three lines.
- **PlatePad's 6 faces / 12 triangles is the interesting row**, not the big
  one: a planar-faced part tessellates to almost nothing at any deflection. That
  is the fact Decision 2 turns on.

## Decision 1 — what comes out of FreeCAD

**Verified in the installed 1.1.1 bundle:** `Mod/Import/Init.py` registers glTF
export via `ImportGui` (OCCT `libTKDEGLTF` ships); `Mod/Mesh/Init.py` registers
STL, BMS, OBJ, OFF, PLY, AMF, SMF, 3MF; `MeshPart.so` documents
`meshFromShape(..., Segments=False, GroupColors=[])`; `Mesh.so` exports
`countSegments`, `getSegment`, `meshFromSegment`.

**The decision: none of them, and the case is now stronger than in the first
draft.** We need per-triangle BRep-face identity *and* per-vertex UVs into an
atlas whose rectangles are named after BRep faces. No standard mesh format
carries the first; the second is only meaningful together with the first. GLB
would cost a loader (Decision 3) to deliver a file we would still have to ship
a side-car table beside. OBJ is text at ~3× the bytes. PLY has the same identity
gap.

**So: a purpose-built binary, with a section manifest so the format can grow
without a parser change.**

```
magic       "FCM2"                                4 B
header_len  uint32 LE                             4 B
header      JSON, utf-8                           N B
<sections>  raw little-endian buffers, in manifest order
```

The header declares what follows, so phase 1 can ship `positions / indices /
tri_face / edges` and phase 3 can add `uvs` with **no change to the parser and
no format version bump**:

```json
{"units":"mm","bbox":[x0,y0,z0,x1,y1,z1],
 "sections":[{"name":"positions","type":"u16","stride":3,"count":8104},
             {"name":"indices","type":"u16","stride":3,"count":8084},
             {"name":"tri_face","type":"u16","stride":1,"count":8084},
             {"name":"edge_pts","type":"u16","stride":3,"count":2140},
             {"name":"edge_ends","type":"u32","stride":1,"count":214},
             {"name":"uvs","type":"u16","stride":2,"count":8104}],
 "atlas":{"px":1024,"mm_per_texel":0.142},
 "objects":["SoleFillet"],
 "faces":[{"object":"SoleFillet","face":"Face1","hash":91002,"area":642.1,
           "rect":[4,4,180,96],"fill_only":false}, ...],
 "shape_hash":{"SoleFillet":861233}}
```

Four properties worth not undoing:

- **Positions and UVs are quantised to `uint16`.** 16 bits over a 100 mm part is
  1.5 µm; over a 1 m assembly, 15 µm. Both far below what a stylus places. UVs
  quantise into the atlas, so 16 bits is sub-texel at any atlas size we use.
- **Indices are `uint16` when the vertex count allows** (it does on every part
  in the measured document) and `uint32` otherwise. Halves the largest section
  and keeps WebGL1 compatibility for free.
- **No normal buffer.** Flat shading derives the normal with `dFdx`/`dFdy` on
  the interpolated world position — smaller *and* more correct than interpolated
  vertex normals for this look.
- **The BRep edges ship with it.** A flat-grey triangle soup with no edges reads
  as a game asset, not as CAD, and the edge lines are how the user sees where
  one face ends and the next begins. `edge.discretize()` over `Shape.Edges`
  costs a couple of thousand points. Cheapest thing in the design; does the most
  for how the model reads.

**Vertices must be de-shared per face, and this is an implementation trap.**
`meshFromShape` returns one `Mesh` with a shared point array; `getSegment(i)`
returns facet indices into it, so a vertex on a face boundary belongs to two
faces. It cannot carry two UVs or two flat normals. The exporter therefore
reads `mesh.Topology`, then builds each segment's own vertex list, de-duplicated
*within* the segment only. Cost is a dict per face and a modest vertex-count
increase (~8,100 for 8,084 triangles on the measured part).

**Size, from the measured part:**

```
  8,104 verts × 6 B  positions      49 KB
  8,104 verts × 4 B  uvs            32 KB
  8,084 tris  × 6 B  indices (u16)  49 KB
  8,084 tris  × 2 B  tri_face       16 KB
  ~2,140 edge points + offsets      14 KB
  header JSON, 81 faces              6 KB
                                  ─────────
                                  ~166 KB
```

Under 200 ms on any LAN. **Gzip is dropped from v1** — three lines and a header
for 60 ms, at 166 KB. Keep it in mind for the 1,500-face assembly case, which is
also the case where the triangle budget starts to bind.

`detail` is exposed to Claude as an enum (`coarse` / `normal` / `fine`) mapping
to bbox-diagonal fractions (~1/400, 1/1200, 1/3000) with `AngularDeflection`
fixed at 0.35 rad. Hard budget: **150k triangles / 3 MB**, with automatic
coarsening and a warning in the tool result.

## Decision 2 — how the paint is represented (the crux, revised)

> **A `detail` knob moves triangles, not faces.** This is worth stating once and
> plainly, because it is the confusion the whole decision hangs on. `Face7` is
> *topology*: it exists whether the part is meshed at 8,000 triangles or
> 800,000, and the mesher cannot create, split or remove one. On the measured
> part that ratio is **81 BRep faces to 8,084 triangles, about 100:1**. A
> high-detail export does not give you more things to name — it gives you more
> triangles per nameable thing. The naming budget is set by the designer's
> feature tree, not by the mesher.

### The reframe that changes the answer

The first draft rejected texture painting on the grounds that *"UV-unwrapping an
arbitrary FreeCAD solid is not a solved problem"*. That premise is true for a
**triangle soup** and false for a **BRep**, and the difference is the entire
decision.

What makes unwrapping hard is **chart segmentation**: deciding where to cut a
surface so the pieces flatten without unacceptable distortion. That is the
expensive, heuristic half of what xatlas does, and it is heuristic precisely
because a triangle soup has thrown away the information needed to do it well.

A BRep has not thrown it away. **Every BRep face is a chart, given exactly, by
the person who designed the part.** The measurement above confirms the mesher
hands them over one-for-one. And the seams then land on BRep edges — where the
user already perceives a boundary, so a seam artifact is invisible by
construction rather than by tuning.

Better still, a BRep face is not just a chart, it is a chart *whose surface type
is known*: `Part.Plane`, `Part.Cylinder`, `Part.Cone` and friends have analytic,
isometric flattenings. Most of a machined part is those three.

**So the unwrapper is about 200 lines of arithmetic, and it is the reason the
answer flips.**

### The algorithm

Per BRep face, over the segment's triangles:

1. Read `face.Surface`. **Plane** → project onto the plane's own frame
   (exactly isometric). **Cylinder** → unroll, `u = θ·R`, `v = axial` (exactly
   isometric; the seam sits at the parametric start, which is already an edge).
   **Cone** → unroll about the apex (exactly isometric).
2. **Anything else** (sphere, torus, B-spline, surface of revolution) →
   planar-project onto the face's area-weighted average normal frame. Isometric
   in the limit of flatness, good enough for a fillet band or a shallow dome.
3. **Check for flips.** If any triangle's winding reverses in UV space, the
   chart self-overlaps and paint would appear in two places. Mark that face
   `fill_only` and skip it. This is an O(triangles) sign test, not a search.
4. Scale each chart so 3D area / UV area is 1, giving uniform texel density
   across the whole atlas.
5. Pack the rectangles (shelf packer, ~50 lines) into the atlas with a 4-texel
   gutter, sized to the target density.

**Nothing here calls `Surface.parameter()`, and that is deliberate.** Using a
face's raw parametric domain is the obvious move and the wrong one twice over:
a B-spline's parameterisation bears no relation to arc length, so the brush
would be fat in one place and thin in another; and the per-vertex projection
solve is exactly the heavy-loop-on-the-GUI-thread hazard the codebase already
has scar tissue about. Geometry instead of parameters avoids both. (Spike U1
times the parametric route anyway, so the decision is on record with a number
next to it.)

The degradation is bounded and legible: a face that will not unwrap becomes
**fill-only** — paint on it lights the whole face rather than a region — and the
tool result says which faces those were. The *coordinates* are unaffected: the
JSON still carries the exact 3D dab centres on that face. Only the picture is
coarse.

### The four options, priced with the new facts

**A. Texture painting — now the recommendation.**

Cost, honestly:

| Piece | Where | Size |
|---|---|---|
| Unwrap + pack | Python, `mesh_export.py` | ~250 lines |
| UV buffer + atlas table | mesh format | +38 KB on the wire |
| Brush → atlas render pass | device, WebGL | ~200 lines |
| Texture display | device | ~10 lines |
| Atlas readback + PNG | device | ~40 lines |
| Third multipart part | `device_server.py` | ~10 lines |
| Coin textured overlay | Python, `tools_device_paint.py` | ~100 lines |

Wire cost: outbound **+38 KB** (the atlas ships empty — there is no outbound
texture at all). Inbound one 1024² RGBA PNG, mostly transparent, so **20–80 KB
typical and ~300 KB heavily painted**. The first draft's *"~2 MB per round
trip"* was simply wrong: it assumed a dense texture, and a paint mask is sparse
and compresses accordingly.

Texel density falls out of surface area, not triangle count:
`atlas_px = clamp(next_pow2(sqrt(total_area / density²)), 512, 2048)`. At a
target of 0.15 mm/texel a typical 100 mm part with ~10,000 mm² of surface lands
at 1024², giving ~0.14 mm per texel — **finer than a stylus places, and the
number belongs in the metadata for the same reason `mm_per_px` does.** "Each
texel is 0.14 mm of the real surface" is this design's honest resolution figure,
and it should travel with the picture.

**B. Vertex colours — still rejected, and the measurement makes it worse.**

The resolution of a vertex-coloured mark is the tessellation density, and the
tessellation density is driven by *curvature*, not by size. **PlatePad in the
table above is 6 faces and 12 triangles** — a planar-faced part, at any
deflection you ask for, because a plane is exactly representable and the mesher
has nothing to refine. A brush stroke on it would have four corners of
resolution. Fixing that means subdividing planar faces to a target edge length,
which is a mesher you now own and which multiplies triangle count by area/target².
Rejected because it fails worst exactly where it is needed most.

**C. Per-face colours — kept, but demoted to a brush mode.**

Free (we have `tri_face` either way), exact, tiny, trivial to render at both
ends. It is the right answer for *"which faces get the fillet"*. But it cannot
say *this corner of the top face*, *roughly this area*, or anything at all about
a revolved body whose whole outside is one face.

The new framing: **"fill this face" is the largest brush setting**, not a
separate mark type. Same gesture, same data model, same JSON — it just happens
to be the setting where the region is the whole chart. That is a genuine
simplification over the first draft, which had it as a parallel concept with its
own annotation type, its own renderer and its own UI mode. It also becomes the
`fill_only` degradation path, so it is load-bearing three times over.

**D. Surface-projected 3D ink — no longer needed as a separate mechanism.**

The first draft's recommendation. It is subsumed: a thin brush painting into a
texture *is* ink. Everything ink had that a texture would not — exact world
coordinates, per-face attribution, a simplified polyline for the JSON — is
retained by keeping the **dab list as the source of truth** (below), so nothing
is lost by folding it in. The one thing that goes is a second renderer at both
ends.

### Why texture wins, in one argument

Not because it has more features. Because of the principle the 2D design already
states:

> *"…which means the user can mark up however they like rather than matching a
> scheme the code knows how to find."*

An ink-only channel makes the user conform: *you may draw lines, not areas.*
The user asked to paint, and a wash over a region is a thing they will reach for
— *"thin this whole area"*, *"the texture goes roughly here"*, *"not this edge,
this patch"* — and having to outline it instead of scrub it is exactly the
scheme-matching the existing design refuses. Texture doesn't ask them to
conform. That is the decisive argument, and it is the codebase's own.

The supporting arguments are that it also gives a **real eraser** — the 2D
design explicitly wanted a pixel eraser and could not have one *"without its own
compositing layer"*, and a texture is that layer — and that it collapses two
mark types into one gesture with a size slider.

### The data model, which is what keeps the coordinates

**The dab list is the source of truth; the atlas is a render of it.** Every pen
sample raycasts onto the mesh and produces a **dab**: a 3D centre in world
millimetres, a radius in millimetres, and the triangle it hit. Dabs are grouped
into **marks**, one per pen-down-to-pen-up.

- Undo drops a mark and re-renders the atlas from the remaining dabs. Fast (it
  is a GPU pass) and exact — no undo snapshots of a 4 MB buffer.
- The JSON serialises, per mark: a simplified centre-line polyline in world mm,
  the brush radius, per-face coverage, the 3D bbox and centroid.
- Per-face coverage is accumulated **as the user paints** — every dab already
  knows its triangle, and `tri_face` turns that into a face. No pixel scanning
  anywhere, on either side.

So the wash is in the texture and the millimetres are in the JSON, which is the
existing split applied correctly rather than an exception to it.

### The combination the coordinator asked about: yes, and it costs 16 KB

Keeping `tri_face` alongside a texture is not a compromise, it is what makes the
texture useful to Claude. It costs 2 bytes per triangle — **16 KB on the
measured part** — and it earns that three times: it turns a painted texel into a
named BRep face by a rectangle lookup rather than an atlas rasterization; it is
what phase 2's face fill runs on; and it is the `fill_only` fallback. There is
no version of this design where dropping it saves anything worth having.

## Decision 3 — the engine, the blob, and the budget

### xatlas — considered, rejected

`xatlas-wasm` v0.1.3 (295 KB unpacked, published 2026-05-22) is real,
maintained, and would work. It is rejected on four grounds, in order of weight:

1. **It solves the half of the problem we already have solved.** Chart
   segmentation is the expensive, heuristic part; CAD hands it to us exactly.
   Buying a heuristic to re-derive information we were given is paying for a
   worse answer.
2. **It costs us the face↔chart correspondence.** xatlas charts are built from
   triangles and span BRep faces freely, so texel → face stops being a rectangle
   lookup and becomes an atlas rasterization of triangle ids. It makes
   Decision 7 harder, not easier.
3. **Its seams land wherever the heuristic put them**, not on BRep edges. Ours
   are invisible by construction.
4. **295 KB of opaque binary in the committed tree.** More on this below.

`xatlas-web` is stale (2020). `@agrande/xatlas-web`, `xatlasjs` and
`uv-map-xatlas` exist and are unassessed — they would only matter if the
geometric scheme fails, which is spike-gated below.

**The Python `xatlas` package is separately rejected**, and should be recorded
as such so nobody re-proposes it: it does ship cp311 wheels for macOS arm64,
win_amd64 and manylinux, matching FreeCAD 1.1's bundled Python 3.11, so it
*would* install. It falls to the addon's no-Python-dependencies rule, which is
not negotiable for a thing users install as a plain file copy.

**`potpack`** (8 KB, Mapbox, maintained) is a good packer and is also not needed:
the packing happens in Python, alongside the unwrap, because that is where the
face names and rectangles have to end up anyway. A shelf packer over 81
rectangles is ~50 lines. This keeps `package.json` at **one** runtime dependency.

### The renderer

**three.js**: the full minified module is around 600 KB (~155 KB gzipped);
tree-shaking helps less than people expect because `WebGLRenderer` is
monolithic, so a realistic minimum build lands in the 450–550 KB range.
*(Order-of-magnitude, from the shape of the library; measure it if this is ever
re-opened.)* We need no scene graph, material system, lights, loaders, shadows,
animation or post-processing.

What we do need:

| Piece | Lines |
|---|---|
| mat4/vec3 maths | ~150 |
| GL context, programs, buffers | ~200 |
| Shaders: solid, edges, atlas-paint, textured display | ~140 |
| `.fcmesh` section-manifest parser | ~90 |
| Turntable orbit camera | ~150 |
| GPU id-buffer picking + ray-triangle | ~120 |
| Dab/mark model, simplification, coverage | ~200 |
| Atlas FBO, readback, PNG encode | ~120 |
| UI wiring, brush size, modes | ~150 |

≈ **1,320 lines of TypeScript → roughly 32–40 KB minified.**

**Recommendation: hand-roll, on WebGL2**, unchanged. The added machinery for
texture painting is ~150 lines over the ink design, and the render-to-texture
pass would have been ours to write under three.js too.

### The budget, restated honestly

The first draft asserted a 100 KB ceiling on `device_ui/`. That number was
invented and the reasoning behind it was never stated, so here it is properly.

The user's actual costs are: a one-time file copy at install, and a per-page-load
download over the LAN (`Cache-Control: no-store`, so every load re-fetches).
At 300 KB both are sub-second on wifi. **Bytes are not the binding constraint.**

The binding constraint is **review and diff hygiene.** `device_ui/` is committed
build output, and the build was deliberately configured with fixed asset names
and no content hashes *so that a rebuild changing nothing produces no diff*. A
295 KB opaque WASM blob is a file no reviewer can read, that churns the whole
diff on every upstream bump, and that nobody in the project can debug when a
tablet renders wrong.

So the rule is: **~100 KB of reviewable text output, and a binary artifact needs
its own argument.** xatlas does not win one here. Current 46.8 KB + ~36 KB
lands around 83 KB, comfortably inside — but the number is a consequence of the
architecture, not a constraint that produced it.

### On plain HTTP

WebGL, WebGL2 and `WebAssembly` are **not** secure-context gated. What is, and
therefore out: WebGPU, WebXR, and `DeviceOrientationEvent.requestPermission` —
so "tilt the tablet to orbit" is unavailable. This sits alongside the existing
`getUserMedia` note.

`gl.lineWidth()` is clamped to 1 on essentially every stack. That was a real
trap for the ink design and is now a non-issue: the only GL lines we draw are
the BRep edges, where a 1px black hairline is exactly right.

## Decision 4 — what comes back over the wire

The annotation document gains **two annotation types and one source kind**.
`DOC_VERSION` stays **1**. `web/src/doc.ts` already says *"Unknown annotation
types are dropped rather than failing the document"*, and `parseDoc` refuses a
newer schema outright — new types are precisely the additive change that
discipline was designed for. Bump the version only when an existing field's
meaning changes.

```json
{
  "version": 1,
  "image": "annotation.png",
  "texture": "annotation_texture.png",
  "source": {
    "kind": "freecad_mesh",
    "id": "Yk8_2mQ",
    "document": "M5Mount",
    "mesh": {"units": "mm", "triangles": 8084, "detail": "normal"},
    "atlas": {"px": 1024, "mm_per_texel": 0.142},
    "camera": {"azimuth": 37.5, "elevation": 22.0,
               "target": [12.0, 8.5, 30.0], "ortho_height": 96.4},
    "scale": null
  },
  "annotations": [
    {"id": "m1", "type": "paint",
     "path": [[12.41, 8.02, 30.55], [13.10, 8.02, 31.02]],
     "radius_mm": 1.8,
     "on": [{"face": 6, "coverage": 0.68}, {"face": 10, "coverage": 0.12}],
     "bbox": [10.2, 7.9, 28.1, 21.0, 8.1, 34.6],
     "centroid": [15.4, 8.0, 31.2],
     "note": "thin this whole area", "target_mm": 2.5},

    {"id": "m2", "type": "paint", "fill": true,
     "on": [{"face": 18, "coverage": 1.0}],
     "centroid": [0.0, 12.0, 15.0],
     "note": "counterbore here", "target_mm": null}
  ],
  "caption": "two things"
}
```

Decisions worth not undoing:

- **Coordinates are world millimetres, not normalized.** The one place the 3D
  document deliberately departs from the 2D one, and the reason is that the
  coordinate is already canonical: there is no image to be relative to, and
  `run_python` can use `[12.41, 8.02, 30.55]` as-is.
- **The paint path IS serialized, and the 2D rule is unchanged.** In 2D the ink
  is not serialized because Claude can see it in the flattened image. Here the
  path is the only thing that carries millimetres, and it is what
  `read_device_paint` reports on. The picture still carries what JSON can't —
  the wash's actual shape — and the JSON still carries what the picture can't.
- **`face` is an index into the mesh header's table, not a name.** Invariant 3,
  in one field.
- **`coverage` is accumulated during painting**, never by scanning pixels.
  Neither end reads the texture to find out which faces were marked.
- **There is no `measured_mm` / `confidence` pair, and `scale` is null.** In 3D
  every length is exact; the entire projection-plane apparatus of the 2D design
  — `axis_aligned`, `_measurement_note`, the `approximate` downgrade — simply
  does not apply. `mm_per_texel` is the honest resolution figure that replaces
  it, and it describes the *picture*, not the numbers.
- **Caps:** 64 points per path after RDP simplification (at ~0.3% of the bbox
  diagonal), 48 marks per document. A full document stays under ~40 KB.

**Upload transport:** one new multipart part, `texture`, alongside the existing
`image` and `doc`. `_parse_multipart` already returns every part by name;
`_handle_upload` gains the same magic-byte check and stores it as
`<stem>_texture.png`. Ten lines, and it touches no invariant. The 12 MB
`_MAX_UPLOAD` covers the view PNG (~400 KB) plus the atlas (~80 KB) plus the doc
with three orders of magnitude to spare.

**`read_device_paint`'s text half is a summary, not the document verbatim.** The
2D tool quotes verbatim because a dimension is four numbers. Per mark this
reports: which faces and at what coverage, the world bbox and centroid, the
brush radius, the note and target. The full document stays on disk beside the
PNGs for `Read`. Quoting it whole would be the same discipline producing the
opposite of its intent.

## Decision 5 — how FreeCAD renders the painted result

Through `render._offscreen_shot`, with the paint **overlaid on the real solid**
rather than replacing it.

```python
sep = coin.SoSeparator()
sep.addChild(_atlas_texture(png_path))      # SoTexture2, from the uploaded PNG
sep.addChild(_texture_coords(uvs))          # SoTextureCoordinate2, from .fcmesh
sep.addChild(_coords(positions_offset))     # ε along normals
sep.addChild(_indexed_face_set(indices))
_insert_after_camera(view, sep)             # shared with tools_cutaway
```

- **Overlay, don't replace.** The real object renders through its own
  ViewProvider with `_shot_appearance`'s pale body and dark edges, exactly as
  every other capture Claude sees, and our textured mesh sits over it,
  alpha-blended, offset ~0.15% of the bbox diagonal along the surface normals.
  So the painted render is a *normal FreeCAD capture with paint on it* — same
  shading, same edges, same background as `capture_view`. Consistency in what
  Claude looks at is worth having, and this costs nothing over replacing the
  solid.
- **`_insert_after_camera(view, node)` is factored out of
  `tools_cutaway._insert_clip_plane` into `render.py`.** That function already
  documents the camera-order nuance and both callers need the same placement.
  One spelling, per the rule that infra owns anything two tools would otherwise
  describe differently.
- **UVs and geometry come from the published `.fcmesh` on disk**, not from a
  fresh tessellation. Same discipline as `_last_annotation["context"]`: it
  describes the model the user marked, not whatever the document has become.
- **`fill_only` faces are rendered as flat tints** from the same overlay mesh,
  using `tri_face` — the phase-2 mechanism, reused as the degradation path.
- **`flatten_colors` is no longer needed.** The first draft wanted to force the
  solid grey so the marks were the only colour; overlaying instead of replacing
  makes that unnecessary, and `_shot_appearance` keeps its existing behaviour of
  preserving the user's colour-coding. One fewer change to shared infra.

**Unverified and important: `SoTexture2` in the offscreen FBO save path.** If
textures don't render through `_save_view_png`'s forced `FramebufferObject`
method, the render-back degrades to per-face tints derived from `coverage` —
which is already built, so the fallback is free. Spike T1.

## Decision 6 — which angles, and how many

Unchanged from the first draft, and the cheap tessellation makes the extra
renders even less of a concern.

**Nothing is guessed. The device reports where the paint landed, so the angles
are computed.**

1. **Replay the device camera.** `source.camera` carries azimuth / elevation /
   target / ortho height — the view the user was looking at when they painted.
2. **The paint-normal view.** Average the marked faces' normals, weighted by
   coverage × area, and look straight at it.
3. **Cluster the rest.** If the marked normals fall into more than one cluster
   (angular k-means, k ≤ 3), one view per cluster.

Then — the part that answers *"how does Claude avoid missing a mark on a face no
chosen angle shows"* — **coverage is verified, not hoped for.** For every mark,
dot its normal with each chosen view direction. Any mark whose best dot is ≤ 0
is invisible in every view so far and gets its own view appended. The tool
**reports the coverage table in its text**:

> 3 marks. `m1` (Face7, Face11) shown in views 1 and 2; `m2` (Face19) in view 2;
> `m3` (Face31) in view 4 only — it faces away in the other three.

That sentence is worth more than a fourth image, because it says what Claude is
*not* looking at.

**Only one image can come back per tool call.** `gui_bridge._dispatch` reads a
single `png_path`; `mcp_server.py` ships one inline block. Two ways forward:

- **(a) Contact sheet.** Composite N renders into one 1568 × 1176 PNG, 2 × 2,
  with azimuth/elevation and mark ids painted into each panel (QImage/QPainter —
  PySide is already imported in `render._looks_blank`). Quarter resolution per
  panel. **Zero changes to the bridge or the MCP server.**
- **(b) Multi-image results.** Let a tool return `(text, [paths])`. Full
  resolution, more tokens, and it changes the one convention every image tool
  depends on.

**Ship (a).** If a mark needs detail a panel can't carry, `capture_view` and
`crop_view` already exist and can be aimed at the world bbox the tool just
reported. Revisit (b) only if panel resolution is measurably binding.

## Decision 7 — naming faces, and where that work lives

The expensive branch — *"the paint covers Face7 and Face11 of Pad001"* — is a
byproduct of work we do anyway, provided it happens on the **outbound** leg.

Done on the **return** leg it is both expensive and invariant-breaking: the
device would send a bare world point and something would have to find its face
by `distToShape` or `BRepClass_FaceClassifier` per face per mark — a loop of Part
shape operations on the GUI thread, the exact hazard
`tools_python._heavy_loop_note` exists to refuse. In an HTTP handler it would
also be FreeCAD reachability in the server.

Done on the **outbound** leg it costs 2 bytes per triangle plus a table in the
header, and the segments that produce it are free from `meshFromShape`.

**With per-face charts it gets cheaper still.** Because each BRep face owns its
own atlas rectangle, a painted texel resolves to a face by looking up which
rectangle contains it — no rasterization of a triangle-id atlas, which is what
a general unwrapper would have forced. And in practice we never even do that
lookup, because the device accumulates coverage per face as it paints.

**Where the code lives:** the face table, the charts and the atlas rectangles
are built in `mesh_export.py`, called from `tools_device_paint.py` on the GUI
thread. The device echoes indices. `read_device_paint` resolves index →
`(object, FaceN)` and re-checks the hash, on the GUI thread. `device_server.py`
opens files it was told about. No handler ever calls into FreeCAD.

## Decision 8 — the tablet, and the gesture collision

**Rendering.** 8,000 flat-shaded triangles is nothing. Even the 150k budget is
trivial for any phone GPU made this decade. The binding costs are now the
**atlas FBO pass** and the **readback at send time**, not the geometry.

**Painting.** For each dab, render the affected triangles into the atlas FBO
with `gl_Position = vec4(uv * 2 - 1, 0, 1)` and a fragment shader that computes
`distance(worldPos, dabCentre)` for the falloff. Driven by 3D distance rather
than 2D, so it paints across chart seams correctly and needs no special case.
~60 lines of shader plus setup, and it is the standard technique.

**Seam bleed** from bilinear filtering across chart boundaries is handled by the
4-texel gutter plus a dilation pass before readback. `NEAREST` filtering is the
cheap fallback and, at 0.14 mm/texel, barely distinguishable.

**Readback at send time:** `gl.readPixels` a 1024² RGBA buffer (4 MB) into a
canvas and `toBlob`. Expect a few hundred milliseconds, once, on Send. Spike D1.

**Picking.** Naive ray-triangle per pen sample is out. Render triangle id into
RGB in a second pass, `readPixels` a 1 × 1 rect at the pointer, then one exact
ray-triangle test against that triangle for the world point. **Once per
animation frame, not per coalesced sample** — 60 picks/second is ample for a
brush and it caps the GPU→CPU stall at one per frame. Fallback is a median-split
BVH (~150 lines). Spike D1.

**The gesture collision.** `web/src/input.ts` reserves two fingers for zoom/pan
and gives one finger to ink on a device that has never seen a stylus. 3D wants
one finger for orbit.

The existing policy resolves it almost for free on the target device:

> *once a stylus has been seen, touch never draws.*

So on a pen tablet: **pen paints, one finger orbits, two fingers pan and zoom**
— and `shouldDraw` does not change at all. The 3D mode routes single-touch to
orbit instead of to ink, which is a caller decision, not a policy change.

On a **penless phone** one finger cannot be both. There the toolbar gains an
explicit Orbit / Paint toggle, defaulting to **Orbit**. Stated honestly: a phone
with no stylus is not the device this feature is for, and a mode toggle is the
correct cost to pay there rather than degrading the tablet experience to match.

`gestureOf` gains a single-touch orbit case alongside the two-touch one, and
stays a pure function over plain state so the pen-then-palm-then-orbit sequence
is a unit test rather than something discovered on a tablet.

**iOS Safari drops the WebGL context when the tab is backgrounded**, and with a
texture atlas that now means losing the user's paint as well as the view. The
dab list is CPU-side, so `webglcontextrestored` re-uploads the buffers and
re-renders the atlas from the dabs — which is exactly the reason the dab list is
the source of truth. Cheap, but it has to be written.

## Python side

### New module: `freecad_tools/tools_device_paint.py`

**`send_model_to_device(objects, note?, detail?)`** — mirrors `send_to_device`:
refuses with `_NOT_RUNNING` (reused verbatim) if the server is down, renders a
thumbnail through the ordinary `_offscreen_shot` path so
`chat_panel._extract_capture_png` puts it in the transcript, tessellates,
unwraps, packs, writes the `.fcmesh`, publishes, **returns immediately.** Same
two-halves rule and same reason. No new rendering code.

**`read_device_paint(index?, views?)`** — returns `(text, png_path)` where the
PNG is the contact sheet. Same `index` semantics as `read_device_image`.

### New module: `freecad_tools/mesh_export.py`

Infra: tessellation, de-sharing, per-face unwrap, the shelf packer, the
`.fcmesh` writer and reader, the face table. Imports nothing from `tools_*`, per
the package's dependency rule; no `import FreeCAD` at module level, per the
"importable from any thread for its schema data alone" contract. The unwrap
maths is pure functions over vertex arrays and a surface descriptor, so most of
it is unit-testable with no FreeCAD at all.

### `render.py`

`_insert_after_camera(view, node)`, lifted from `tools_cutaway` and now shared.
Nothing else changes.

### `device_server.py`

- `publish(path, meta, upload_dir=None, mesh_path=None)`.
- `_public_record` gains `mesh_url` when a record has one, so `/api/latest` and
  the SSE payload carry it.
- `GET /api/mesh/<id>` — the id is a dictionary key, never a path fragment,
  exactly as `_send_published` already is. `application/octet-stream`.
- `_handle_upload` stores an optional third part, `texture`, through the same
  magic-byte check.

**An older client meeting a newer server still works:** the thumbnail is
published as the record's `path`, so `url` and `/api/image/<id>` behave exactly
as before and a 2D-only page shows the thumbnail rather than erroring.

### Cost on the GUI thread

Measured at 10–60 ms for the mesh. The unwrap adds one `.Surface` read per face
plus pure arithmetic over the triangles — expected well under 30 ms, confirmed
by spike U1. That puts the whole outbound path at roughly the cost of one
`capture_view`, which is what it sits next to.

The guards stay anyway, because the untested cases are the ones that would hurt:

- **`objects` is required**, as for every capture tool.
- **A face-count gate** (`len(shape.Faces)`, cheap) before meshing, refusing
  above ~1,500 with a message asking Claude to narrow `objects` rather than
  starting something that might freeze FreeCAD on Windows or on hardware slower
  than the machine this was measured on.
- **Coarsen, don't retry** if the triangle budget is blown, and say so.

A `precheck` can only validate argument *shape* — it runs in pure Python with no
document — so it covers `detail` and a non-empty `objects` and nothing more.

## Frontend

```
web/src/
  gl/context.ts  gl/program.ts  gl/fbo.ts        GL plumbing
  mesh.ts        .fcmesh section-manifest parser  ← unit tested
  orbit.ts       turntable camera, az/el ↔ matrix ← unit tested
  pick.ts        id-buffer pick + ray-triangle    ← unit tested (the maths)
  paint.ts       dabs, marks, simplification,     ← unit tested
                 per-face coverage, doc types
  atlas.ts       the FBO paint pass, readback
  view3d.ts      render loop and mode wiring
```

`main.ts`, `ui.ts` and `input.ts` gain a mode; `api.ts` gains `mesh()`, one field
on `Published` and a third upload part; `canvas.ts`, `strokes.ts`,
`dimensions.ts`, `scale.ts`, `doc.ts` and `token.ts` are essentially untouched.

**One bundle, one page, mode switched by what is published.** Vite's config
forces `inlineDynamicImports` and fixed asset names precisely so the committed
tree stays a handful of stable files; a second entry point would give that up
and gain shared chunks in the diff. ~36 KB downloaded once is not worth
restructuring the build for.

**`orbit.ts` is pinned against `render._orbit_rotation`.** The Python function
documents that its cardinal (azimuth, elevation) pairs reproduce the
front/right/back/left/top/bottom presets. The TypeScript turntable implements
the same formula so the device camera round-trips into FreeCAD's with no
conversion, and its unit test pins the same six cases. A cross-language pinned
test is the only thing that stops the two drifting into a subtly rotated replay.

**Orthographic on the device**, matching the capture camera. Simpler picking, no
projection mismatch in the replay, and what CAD users expect.

## Testing

Vitest, on the pure logic:

- `mesh` — the section manifest (a file with and without `uvs`, proving the
  format grows without a parser change), malformed magic, truncated header,
  quantisation round-trip, an empty edge section.
- `orbit` — az/el ↔ matrix round-trip, the six cardinal cases pinned against the
  Python docstring, no roll accumulating over a long drag.
- `pick` — ray-triangle against known triangles, including edge-on and
  behind-camera.
- `paint` — dab → mark grouping, RDP preserving endpoints, per-face coverage
  accumulation, the caps, and undo restoring the exact prior coverage.

Python, under `freecadcmd`:

- `mesh_export` unwrap — **planes, cylinders and cones are isometric to within a
  tolerance**, checked by comparing 3D triangle areas to UV triangle areas
  scaled by the chart factor. This is the test that catches an unwrap that looks
  plausible and paints wrong.
- `mesh_export` flip detection — a synthetic 360° face must come out
  `fill_only` under planar projection and *not* under the cylinder path.
- The packer — no overlaps, gutters respected, achieved density within 10% of
  target.
- The byte layout and the face table.
- `device_server` — the mesh route (unknown id, pruned file, token gate) and the
  three-part upload. Still runs under a bare `python3`.
- `tools_device_paint` — angle selection and the coverage check as pure
  functions over a synthetic document, no FreeCAD needed.

Brush feel, orbit feel and GL correctness are verified on the two real devices,
which is the only place they can be.

## Risks, re-ranked

1. **Topological naming — a face index is only valid against the shape as
   published.** Now #1, because it is the only failure here that is *silent and
   confidently wrong*, which the codebase already documents as costing a turn to
   argue with. If the model is edited between send and read, `Face7` may be a
   different face. Mitigated by the per-face `hashCode` in the header: on read,
   re-resolve by hash first, fall back to index, and **say which happened.** When
   the hash no longer matches, degrade to *"the paint is at world point
   (x, y, z), on a face that has changed since"* rather than naming a face.
2. **Unwrap quality on freeform faces.** The risk texture introduces. Bounded
   and legible — a face that flips becomes `fill_only`, the coordinates survive,
   and the tool says which faces degraded. The residual worry is a face that
   *doesn't* flip but is badly distorted, where the brush changes apparent size
   across it. Detectable (per-triangle area ratio variance) and worth reporting
   rather than hiding.
3. **`SoTexture2` in the offscreen FBO save path (T1).** If it doesn't render,
   the whole faithful-picture half of the design falls back to per-face tints.
   The fallback is already built, so this is a quality risk, not a schedule one —
   but it should be the first thing spiked, because it is the only unknown that
   could change what phase 3 is worth building.
4. **Mobile GL: the atlas FBO pass, the readback, and the pick stall (D1).**
   Three related unknowns on hardware we don't have. Fallbacks exist for each
   (smaller atlas, chunked readback, BVH picking) but they are not free.
5. **Nested placements (S3).** Still open, still nasty because a container
   transform produces a *silent constant offset* on every mark. Test
   deliberately on a part inside a placed `App::Part`.
6. **Segment ordering.** One `isSame` check away from settled; everything
   downstream assumes it.
7. **Tessellation cost on the GUI thread.** **Demoted from #1 to here.**
   Measured at 10–60 ms on an 81-face part — less than the offscreen render
   beside it, and not in the same league as the `Shape.slice()` loops the
   codebase has scar tissue about. Remaining unknowns are a ~1,500-face
   assembly and Windows, and the face-count gate covers both conservatively.
8. **Bundle growth.** Bounded by rejecting the WASM blob; ~83 KB projected.
9. **Contact-sheet resolution.** Accepted; escape hatch is `crop_view` aimed at
   a reported world bbox, and Decision 6(b) if that proves insufficient.

## Spikes

**Closed by measurement:** S1 (one segment per BRep face — confirmed on 34
shapes), S2 (tessellation cost — 10–60 ms). **S4 (glTF tessellation control) is
dropped as moot**: Decision 1 no longer considers glTF.

**Open, in the order they should be run:**

- **T1 — `SoTexture2` through `_save_view_png`.** Build a throwaway offscreen
  view, add an `SoSeparator` with an `SoTexture2` (image set from a QImage's
  bits), an `SoTextureCoordinate2` and an `SoIndexedFaceSet` over four
  vertices, force `SavePicture = FramebufferObject` and save. Check the texture
  appears. Also check alpha blending, since the overlay depends on it.
  *Highest-value spike: it is the only one that can change what phase 3 is
  worth.*
- **U1 — unwrap cost and surface-type access.** Confirm `face.Surface` on the
  measured document yields `Part.Plane` / `Part.Cylinder` / `Part.Cone` objects
  with usable `.Axis` / `.Position` / `.Radius`, and count how many of the 81
  faces fall to the generic projection path. Time the pure-Python UV loop over
  8,084 triangles. **Also time `face.Surface.parameter(v)` × 5,000**, so the
  route we are *not* taking has a number next to it rather than an assertion.
  Expected: analytic path < 30 ms; `parameter()` unknown and possibly 100×
  worse.
- **U2 — segment ordering.** `shape.getElement("Face7").isSame(shape.Faces[6])`
  across the measured document.
- **D1 — the device, on a mid-range Android and on the iPad.** Atlas FBO paint
  pass frame time; 1024² readback + PNG encode time at Send; `readPixels` pick
  stall per frame. Any one of these being bad has a known fallback; all three
  being bad means reconsidering the atlas size.
- **S3 — nested placements.** Does `mesh.Topology` come out in global
  coordinates for an object inside an `App::Part` with a non-identity placement,
  or is `obj.getGlobalPlacement()` needed?
- **U3 — conditional, only if U1 shows the geometric scheme failing on real
  parts.** Whether `xatlas-wasm`'s binding exposes chart seeding from
  pre-existing groups, and unwrap time for 8k triangles on a mid-range phone. If
  the geometric scheme works, this is never run.
- **U4 — round-trip texture size.** Paint a real part at three coverage levels
  and measure the PNG. Expected 20–80 KB sparse, ~300 KB heavy.

## Phased plan

Each phase is a commit that leaves the addon working, and each is useful on its
own.

```
  0 Spikes ─── 1 Mesh out + viewer ─── 2 Face fill ─── 3 Texture paint ─┐
                                              └────────────────────────┴─ 4 Angles ── 5 Hardening
```

### Phase 0 — Spikes · half a day, ships nothing

T1, U1, U2, S3. D1 needs phase 1 to exist, so it runs inside phase 1.

### Phase 1 — Mesh out, viewer in · size L

`mesh_export.py` (tessellate, de-share, edges, the section manifest — **no UVs
yet**), `send_model_to_device`, `GET /api/mesh/<id>`, the WebGL viewer with
turntable orbit, pinch zoom, BRep edges and flat grey. The face table ships from
day one even though nothing reads it. Run D1 here.

**Done:** *"show me the bracket on my tablet"* — Claude sends it, the user spins
it in their hands. No painting. Retires every unknown in Decisions 1, 3 and 8 at
once, on the real devices.

### Phase 2 — Face fill · size M

The picker. Tap a face, it tints (from `tri_face` — no UVs, no atlas); tap again
to clear. `read_device_paint` resolving indices to `(object, FaceN)` with hash
verification, and a single-angle render replaying the device camera. The `paint`
annotation type in its `fill: true` form.

**Done:** *"tap the faces you want filleted"* — and Claude gets exact
sub-element names it can put straight into a `PartDesign::Fillet`. The highest
value per line of code in the whole design, and it needs none of phase 3.

### Phase 3 — Texture paint · size L

The unwrap and packer in Python, the `uvs` section (added to the manifest, no
parser change), the atlas FBO paint pass, brush size, the real eraser, readback
and the `texture` upload part, and the Coin textured overlay on the FreeCAD
side. `_insert_after_camera` factored out of `tools_cutaway`. `fill_only` faces
degrade to phase 2's renderer.

**Done:** the user scrubs a wash over half the top face of the actual solid and
Claude sees it re-rendered on the model, with the faces it covers named and the
coverage quantified. **This is the phase that answers what was originally
asked.**

### Phase 4 — Angles and coverage · size M

The three angle sources, the coverage check, the extra-view rule, the contact
sheet and the coverage table in the tool text.

**Done:** Claude stops missing marks on faces the first angle happened not to
show, and knows when it has.

### Phase 5 — Hardening and docs · size M

Triangle and atlas budgets with warnings, the face-count gate, WebGL context
loss re-rendering the atlas from the dab list, error paths with a message rather
than a stack trace (no active document, no GL, mesh pruned off disk, device
dropped mid-upload, unwrap degraded), `README.md`/`SECURITY.md` on what the new
route exposes, and `CLAUDE.md`: module map gains `tools_device_paint.py` and
`mesh_export.py`, the tools table gains the two tools, and the Device annotation
section gains a sibling paragraph.

## Deferred

1. **3D measurement.** Two picks give an exact world-space distance in
   millimetres, with *no* projection caveat at all. Nearly free once the picker
   exists, and it inverts the most awkward part of the 2D design. Almost
   certainly the next thing after phase 5.
2. **A section plane on the device.** One clip plane in the shader; `cutaway`
   already proves the FreeCAD half. Lets the user paint something *inside* the
   part.
3. **Multiple brush colours as semantics** — red "remove", green "add", blue
   "question". Free in the atlas (it is RGBA), and the JSON already has a place
   for it. Deliberately not in v1: the 2D design's lesson is that Claude reads
   marks off the picture rather than decoding a scheme, and a colour convention
   is a scheme the user has to remember.
4. **Full-resolution multi-image results** — Decision 6(b), if panel resolution
   binds.
5. **Edge selection.** `EdgeN` is as nameable as `FaceN` and the edge polylines
   already ship; the only missing piece is a screen-space pick tolerance.
   Directly useful for chamfers.
6. **Gzip on the mesh route**, if a large assembly makes 166 KB become 3 MB.
7. **Auto-inject on upload** — still open from the 2D design, and it applies
   identically here.

## Open questions

1. **Should a published model replace a published capture, or coexist?** The
   feed has one `latest`. Proposed: one `latest` with a `kind`, and the page
   switches mode — notify, don't clobber, exactly as the 2D banner already does.
2. **What is the right target texel density?** 0.15 mm/texel is chosen from
   "finer than a stylus places". It could be a `detail`-linked knob, but a second
   resolution knob is a second thing to explain. Start fixed, report the
   achieved number, and revisit if anyone asks for it.
3. **Does the mesh survive a "New"?** `reset_session` clears the feed and the
   `.fcmesh` is pruned with the rest of `mobile/` — consistent, but it means a
   tablet holding a model from the previous conversation holds something FreeCAD
   no longer knows about. Proposed: `read_device_paint` detects the missing
   publish record and says so, as it already does for a pruned upload.
