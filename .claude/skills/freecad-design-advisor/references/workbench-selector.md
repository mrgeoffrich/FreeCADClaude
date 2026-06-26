# Workbench Selector: Routing a Design Idea to the Right FreeCAD Workbench

Decision guide for choosing which FreeCAD 1.1 workbench leads a mechanical / 3D-printing design, what supports it, and the ordered feature sequence to build it — GUI workflows only.

## TL;DR routing

- **One mechanical part you want to iterate and print** → **Part Design** (it includes all Sketcher tools). This is the default and best-supported path.
- **A blocky shape that is just primitives added/subtracted** → **Part** (CSG) is faster and more forgiving.
- **Several finished parts that move or fit together** → model each as its own **Body**, then assemble in the **Assembly** workbench.
- Everything else (2D inputs, freeform surfaces, imported meshes) is a **supporting** workbench that feeds one of the three leads above.

The substrate under all solid modelling is **Sketcher** (2D profiles + constraints) and the **OpenCASCADE BREP kernel** (precise, curve-accurate solids). Meshes (STL/OBJ) are only the *export* form for slicers, not a modelling form.

## Decision table: design archetype → lead workbench

| Design idea / goal | Lead workbench | Supporting | Why / intent |
|---|---|---|---|
| General mechanical part to 3D print (bracket, enclosure, knob, mount) | **Part Design** | Sketcher | Parametric, stays a single watertight solid, full editable history |
| Part dominated by a 2D profile pushed into 3D (plate, faceplate, gasket, lever) | **Part Design** | Sketcher | Sketch → **Pad**, then **Pocket**/**Hole** for cutouts |
| Rotationally symmetric part (pulley, nozzle, shaft, bottle, spacer) | **Part Design** | Sketcher | **Revolution** of a half-profile; **Groove** for revolved cuts |
| Blocky shape = primitives combined, no sketches needed | **Part** (CSG) | — | **Box/Cylinder/...** + **Cut/Fuse/Common**; quick and tolerant |
| Combine several *independent* solids or several finished **Bodies** | **Part** | — | Booleans (**Cut/Fuse/Common**) act on independent objects |
| Hollow shell / thin-walled enclosure | **Part Design** | Sketcher | Build the solid, then **Thickness** and open a face |
| Repeated features (vents, fins, bolt circle, rib array) | **Part Design** | Sketcher | **LinearPattern** / **PolarPattern** / **MultiTransform** |
| Mirror-symmetric part | **Part Design** | Sketcher | Model one side, then **Mirrored** |
| Threads, springs, augers, coils | **Part Design** | Sketcher | **Additive/Subtractive Helix** (Part **Helix** for CSG) |
| Profile swept along a path (handle, frame, pipe, trim) | **Part Design** | Sketcher | **Additive Pipe** (Part **Sweep** for CSG) |
| Transition between differing profiles (adapter, duct, funnel) | **Part Design** | Sketcher | **Additive Loft** (Part **Loft** for CSG) |
| Gear / sprocket | **Part Design** | Sketcher | **Involute gear** / **Sprocket** profile → **Pad** |
| Organic / freeform faired surface (ergonomic grip, shells, fairing) | **Surface** | PartDesign, Sketcher | NURBS faces from boundary curves; then make a solid |
| Multi-part product, mechanism, or fit/clearance check | **Assembly** | PartDesign per part | One **Body** per part, joined by joints |
| Source geometry is a 2D outline / DXF / flat-profile | **Draft** | Sketcher, PartDesign | Draw/import in 2D → **Draft to Sketch** → **Pad** |
| Imported mesh / 3D-scan STL to edit as a real solid | **Mesh** | Part, PartDesign | Repair mesh → convert to Part shape → use as Base Feature |
| Final hand-off to a slicer (FDM/resin) | **Mesh** | — | **Create mesh from shape** → **Export** STL/OBJ/3MF |

## The key fork: Part Design vs Part (CSG)

Both produce precise BREP solids on the same kernel; they differ in *workflow* and *intent*. FreeCAD historically started with CSG (Part), then added feature editing (Part Design) — the SolidWorks-style approach.

### Part Design — feature editing (the default for mechanical parts)
- **Model = one Body = one single contiguous solid** (a casting / a part milled from one block — no loose or separate pieces). The Body auto-fuses its additive features, so each new feature must touch/intersect the previous one.
- Work is an **ordered, cumulative history**: a base feature, then features that each *add* or *subtract* material, then *dress-up* and *transformation* features on top.
- A Body ships with an **Origin** (XY/XZ/YZ planes, X/Y/Z axes) to anchor sketches and datums.
- **Pick it when:** the part has sketched profiles, holes, pockets, patterns, fillets; you expect to tweak dimensions and want changes to ripple through; you need a guaranteed watertight solid for printing.
- **Strengths:** parametric, intent-preserving, enforces print-ready solids, rich dress-up/pattern toolset.
- **Watch out (topological naming):** prefer attaching sketches/datums to the Origin's **base planes/axes**, not to generated faces/edges; use a simple **master sketch** for driving geometry; add **fillets/chamfers as late as possible** in the tree. This keeps edits from breaking the model.

### Part — constructive solid geometry (CSG)
- **Each object is an independent solid** that can be moved freely; you build complexity by **Boolean** combination: **Cut** (subtract), **Fuse/Union**, **Common/Intersection**.
- No enforced single-solid container; objects live loose in the document (optionally grouped).
- **Pick it when:** the form is naturally a stack of **primitives** (Box, Cylinder, Sphere, Cone, Torus, Tube, Prism, Wedge, Helix); you need to **combine multiple separate solids or multiple finished Bodies**; you're doing kernel-level work (repair, **Defeaturing**, **Refine shape**, **Check Geometry**, **Create shape from mesh**, **Convert to solid**).
- **Strengths:** simple mental model, flexible object positioning, the lowest-level tools that can often fix what higher-level tools choke on.
- **Watch out (coplanar Booleans):** OCCT Booleans can fail when operands share a face/edge. Make the cutting/joining solid clearly **protrude past** the target rather than ending flush.

### They interoperate (don't treat it as either/or)
- A Body's final shape (its **Tip**) can be consumed by Part Booleans and other workbenches — for clean external selection set the Body's **Display Mode Body** to `Tip`.
- A Part solid (or imported STEP) can seed a Body as its **Base Feature** (drag it into an empty Body), then continue parametrically in Part Design.
- **PartDesign Boolean** imports whole Bodies/Clones into the active Body for a parametric combine.
- **Rule of thumb:** lead with Part Design for any *single* mechanical part; reach into Part to *combine independent solids* or to *clean up* geometry.

## Supporting workbenches — when each enters

### Sketcher — the 2D foundation for both leads
- Creates constrained 2D profiles that become Pad/Pocket/Revolution/Loft/Pipe inputs. Lives inside Part Design (no need to switch) and also feeds Part.
- **Intent:** aim for **fully constrained** sketches (geometric constraints first, then dimensional, lock position last) so edits stay predictable.
- **Profile rules for solids:** only **closed**, non-self-intersecting, non-overlapping contours (nested contours make holes); use **construction geometry** for guides that shouldn't become edges.
- Keep sketches **simple and layered** (one for the base profile, another for holes/cutouts) rather than one giant sketch. Use **External geometry** / projection to reference existing model edges into a new sketch.

### Draft — 2D drafting and array source
- Use when the *input* is 2D: lines, wires, rectangles, polygons, splines, or imported **DXF/DWG/SVG** outlines. Convert with **Draft to Sketch**, then **Pad** in Part Design.
- Its **array** tools (**Array (Ortho), Polar array, Circular array, Path array, Point array**) and **modifier** tools (Move/Rotate/Scale/Mirror/Offset) work on 3D objects too — handy for laying out copies that aren't a single Body's pattern.
- Relies on a **working plane**; set it before drawing.

### Assembly — putting finished parts together (FreeCAD 1.0+, Ondsel solver)
- Enter only once parts exist. **Each part is its own Body** (a chair is many Bodies, not one). The built-in Assembly workbench solves real joints.
- Sequence: **Create Assembly** → drag parts in (or **Insert Component**) → **Toggle Grounded** on the fixed base part → add **joints** (Fixed, Revolute, Slider, Cylindrical, Ball, Distance, Parallel, Perpendicular, Angle; plus Screw / Rack-and-Pinion / Gears / Belt for mechanisms) → **Solve**.
- Extras: **Exploded View**, **Bill of Materials**, **Simulation**. Use it to verify fit, clearance, and motion before printing.
- For pure static grouping (no solver, just "move these together"), the general **Std Part** container is enough; **Std Group** is just an organizing folder. Note: Booleans cannot be applied across Std Parts.

### Surface — freeform / organic faces the solid tools can't make
- Use for NURBS faces driven by boundary curves: **Filling**, **Fill boundary curves**, **Sections**, **Extend face**, **Blend Curve**, **Curve on mesh**.
- Typical intent: place **sketches on datum planes** (in Part Design) to frame the form, build faces in Surface, then stitch faces into a **shell → solid** with Part **Shape builder**.
- Constraint: a Surface result **cannot live inside a PartDesign Body**, but the Surface plus its driving Body (datums + sketches) can be grouped in a **Std Part**.

### Mesh — import of meshes and export to slicers
- **Inbound:** repair/clean a downloaded or scanned mesh (**Evaluate and repair mesh**, **Check solid mesh**, fill holes, harmonize normals, **Decimation**), then hand to Part's **Create shape from mesh** / **Convert to solid** to get a BREP you can use as a Body **Base Feature**. (Mesh→solid is lossy and heavy — optimize/decimate first; it is never as clean as native modelling.)
- **Outbound:** the only way to feed a slicer. See the 3D-printing lens below.

## How workbenches combine in one project (pipelines)

A FreeCAD document spans all workbenches — you switch toolsets, not files. Common chains:

- **Single printed part:** Part Design (Sketcher) → Body → Mesh export → STL → slicer.
  - Canonical sequence inside the Body: *Sketch on a base plane → Pad (base solid) → new sketch on a resulting face → Pad/Pocket/Hole → pattern (Linear/Polar) → Thickness if hollow → Fillet/Chamfer last.*
- **Multi-part product:** Part Design per part (one Body each) → **Assembly** (joints, fit/motion check) → export **each Body separately** to STL.
- **2D-driven part:** Draft (or imported DXF) → Draft to Sketch → Part Design Pad/Pocket → export.
- **Freeform part:** Part Design datum planes + sketches → Surface faces → Part Shape builder shell→solid → (Std Part) → export.
- **Reverse-engineering:** Mesh import/repair → Part Create shape from mesh → Part Design Base Feature → add parametric features → export.
- **Combine independent solids:** model each (Part primitives or Bodies) → Part **Cut/Fuse/Common** → optionally wrap as a Body Base Feature for further features.
- **History cleanup:** to stop a reusable component (e.g. one tiled many times) from dragging its whole feature tree, use Part **Create simple copy** (or round-trip through STEP) to flatten it to a non-parametric solid.

Datum geometry note: add a **Datum Plane** (or line/point/local coordinate system) only when several sketches share the *same non-standard* orientation, so adjusting one datum moves them all; otherwise attach sketches directly to the Origin's base planes for stability.

## The mechanical + 3D-printing lens

- **Watertight solids are mandatory.** Slicers reject open/leaky geometry. Part Design guarantees a single closed solid per Body; if you built with Part CSG, run **Check Geometry** before exporting. The kernel's BREP solids carry inside/outside, which is what makes a printable object.
- **Export is a mesh conversion.** Either **File → Export** to STL, or (for control) Mesh **Create mesh from shape**, then export STL/OBJ/3MF. Select the **Body / solid** (or the Body in `Tip` display mode) as the export source.
  - **Curve quality vs file size:** lower **surface deviation** / **angular deviation** = smoother curves but bigger files. Curved printed parts usually need a finer deviation than the default; flat/blocky parts don't.
  - STL/OBJ are **dimensionless and assumed mm** — model in mm or scale before export.
- **FDM/resin form choices:**
  - Hollow with **Thickness** to save material; **open a face** for resin drain/escape or to expose the cavity (FDM walls/infill are otherwise a slicer concern).
  - Use **Fillet/Chamfer** as late dress-up features to soften stress risers and ease printed-edge quality.
  - **PartDesign Draft** (angular draft on faces) aids release/overhang behaviour on faces you want tapered.
  - Tune mating clearances by editing sketch dimensions — parametric history makes fit adjustments trivial; verify the fit in **Assembly** before committing to a print.
- **CNC / non-print hand-off:** prefer **STEP/IGES** (keeps exact BREP) over STL; export via Part. The **CAM** workbench generates G-code for milling (much more manual than slicing). Run a **Mesh** evaluation to catch non-manifold edges if a mesh path is unavoidable.

## Quick anti-patterns

- Don't model a multi-part assembly as one Part Design Body — a Body is *one* contiguous solid; split into Bodies.
- Don't attach Part Design sketches/datums to generated faces/edges when a base plane would do — it invites topological-naming breakage.
- Don't fight coplanar Boolean failures in Part — make operands overlap/protrude instead of ending flush.
- Don't try to model freeform NURBS faces inside a Body — use Surface, then convert to a solid.
- Don't treat STL as an editable format — convert to a Part shape first, and expect to optimize.

## Sources

- Which_workbench_should_I_choose.md
- Manual_All_workbenches_at_a_glance.md
- Manual_Modeling_for_product_design.md
- Manual_Traditional_modeling,_the_CSG_way.md
- PartDesign_Workbench.md
- Part_Workbench.md
- Workbenches.md
- Feature_editing.md
- Constructive_solid_geometry.md
- Manual_Preparing_models_for_3D_printing.md
- Sketcher_Workbench.md
- Assembly_Workbench.md
- Draft_Workbench.md
- Mesh_Workbench.md
- Surface_Workbench.md
- PartDesign_Body.md
- Std_Part.md
- Datum.md
- Document_structure.md
- Part_Boolean.md
- Export_to_STL_or_OBJ.md
- Mesh_to_Part.md
- Migrating_to_FreeCAD_from_SolidWorks.md
- Body.md
