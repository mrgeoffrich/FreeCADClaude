# Workbench Reference Card

Capability reference for the seven workbenches a FreeCAD 1.1 design advisor routes between. One block per workbench: purpose, the named tools a workflow actually uses, strengths, and explicit use-when / avoid-when. This card describes what each workbench **is**; routing logic (matching a design idea to a workbench) lives in the selector doc — cross-reference it rather than re-deciding here.

Quick map:
- **PartDesign + Sketcher** — the default for a single 3D-printable mechanical part (parametric, feature-history).
- **Part** — primitives + Booleans (CSG), repairs, conversions, and the universal "shape" layer everything else produces.
- **Draft** — 2D geometry, working planes, arrays, and modifiers usable on any object.
- **Assembly** — positioning multiple finished parts with joints; never used to model a part.
- **Surface** — NURBS faces for shapes the solid tools can't make; feeds back into a solid.
- **Mesh** — triangle meshes: import STL/OBJ, repair, and the final solid-to-STL export for printing.

---

## PartDesign Workbench

**Purpose:** Parametric, feature-history modeling of a single contiguous solid — the primary workbench for a 3D-printable mechanical part.

**Core container:** Everything happens inside a **Body** (one Body = one contiguous solid, auto-fused). The Body's **Origin** supplies standard planes (XY/XZ/YZ) and axes to attach the first sketch to. The **Tip** is the feature currently exposed; features are an ordered, editable history.

**Key tools by role:**
- *Sketch placement:* **Create Sketch** (on an Origin plane, a Datum Plane, or a planar face), **Attach Sketch**, **Datum Plane / Datum Line / Datum Point / Local Coordinate System** (datum tools) for sketches in non-standard orientations.
- *Additive (base or add material):* **Pad** (extrude a profile), **Revolution** (revolve a closed profile about an axis), **Additive Loft** (transition between profiles), **Additive Pipe** (sweep profile(s) along a path), **Additive Helix** (threads/springs), plus additive primitives (Box, Cylinder, Sphere, Cone, Prism, Wedge, Torus, Ellipsoid).
- *Subtractive (remove material):* **Pocket** (extruded cut), **Hole** (from circle sketches; carries thread/counterbore/countersink parameters), **Groove** (revolved cut), **Subtractive Loft/Pipe/Helix**, and subtractive primitives.
- *Dress-up (apply late):* **Fillet**, **Chamfer**, **Draft** (angular draft on faces — relevant for mold/print pull), **Thickness** (hollow a solid into a shell, opening chosen faces).
- *Transformations:* **Mirrored**, **LinearPattern**, **PolarPattern**, **MultiTransform** (combine transforms, incl. **Scaled**), **Boolean** (bring another Body in for union/cut/common).
- *Built-in profiles:* **Involute gear**, **Sprocket**, **Shaft design wizard** (each generates an editable sketch/feature).

**Strengths:**
- Editable parametric history — change a Pad length or a sketch dimension and downstream features update.
- Guarantees a single solid valid for slicing; warns when an operation would break the solid.
- Tight Sketcher integration; clean intent for "prismatic part with holes, pockets, rounds, ribs, patterns."
- Dress-ups (fillet/chamfer/draft) and shelling (Thickness) as parametric 3D features, kept off the sketch.

**Use when:**
- Designing one manufacturable/printable part (bracket, housing, enclosure, gear, knob, mount).
- The part is fundamentally extrude/revolve/sweep + cuts + rounds, especially with repeated features (patterns) or symmetry (mirror).
- You want later editability and a clean STL export of one solid.

**Avoid when / reach for X instead:**
- Multiple separate solids that move or are fastened together → model each as its own Body, then **Assembly** (or group Bodies in a **Std Part**).
- Pure primitive + Boolean "blocky" CSG where history isn't needed → **Part** is simpler.
- Freeform organic/Class-A surfaces a Pad/Loft can't express → **Surface**, then convert to solid via Part.
- Editing an imported STL → that's a mesh; see **Mesh** (and note solid-to-PartDesign is hard).

---

## Sketcher Workbench

**Purpose:** Create constrained 2D profiles and paths that feed 3D features — the foundation under almost every PartDesign feature and many Part/Draft/Surface operations.

**Key tools by role:**
- *Geometry:* **Line / Polyline**, **Arc** (by center / 3-point), **Circle** (center / 3-point), **Ellipse**, **Rectangle / Centered rectangle / Rounded rectangle**, **Regular polygon** (triangle…octagon or N-sided), **Slot / Arc slot**, **B-spline**, **Point**, **Toggle construction geometry** (reference-only geometry excluded from the profile).
- *Geometric constraints:* **Coincident (unified)**, **Point on object**, **Horizontal/Vertical**, **Parallel**, **Perpendicular**, **Tangent/collinear**, **Equal**, **Symmetric**, **Block**.
- *Dimensional constraints:* **Dimension** (context-sensitive, the main tool), **Horizontal/Vertical distance**, **Distance**, **Radius / Diameter / Auto radius-diameter**, **Angle**, **Lock**. Any dimension can be toggled to **reference** mode or driven by an **expression**.
- *Edit/productivity:* **Fillet / Chamfer**, **Trim / Split / Extend**, **Offset geometry**, **Symmetry**, **Array transform / Polar transform / Scale transform**, **Carbon copy**, and **Create external projection geometry** / **Create external intersection geometry** (pull edges/vertices from outside the sketch onto the sketch plane).
- *Diagnostics:* **Select unconstrained DoF**, **Select redundant/conflicting constraints**, **Validate sketch**.

**Strengths:**
- A constraint solver turns rough geometry into precise, fully-determined profiles; degrees-of-freedom feedback (green = fully constrained).
- Auto-constraints, snapping, and On-View-Parameters speed exact input.
- Expressions and named constraints make sketches parametric and spreadsheet-drivable.

**Profile rules (load-bearing for whether a Pad/Pocket succeeds):**
- Profiles for solids must be **closed** contours, no gaps (use Coincident to actually join endpoints), no self-intersections, no shared/duplicate edges, no T-junctions. Construction geometry is exempt.
- A single closed profile is enough to create a feature; full constraint is best practice, not a requirement.
- Best practice: prefer several simple sketches over one complex sketch; apply geometric constraints first, then dimensions; anchor to the Origin.

**Use when:**
- Defining any profile to Pad/Pocket/Revolve, or a path to sweep, or a section for a loft.
- You need precise, relational 2D geometry (symmetry, tangency, equal features) that must stay editable.

**Avoid when / reach for X instead:**
- Standalone 2D drawings, annotations, or DXF output, or geometry placed freely in 3D space → **Draft**.
- You don't actually need constraints/precision and just want quick blocks → primitives in **Part** / additive primitives in PartDesign.
- Sketcher is rarely entered on its own — it is normally driven from inside PartDesign (which contains all Sketcher tools).

---

## Part Workbench

**Purpose:** Traditional constructive solid geometry (CSG) — independent solids built from primitives and combined with Boolean operations — plus a broad toolbox for geometry creation, repair, and conversion.

**Key tools by role:**
- *Primitives:* **Box**, **Cylinder**, **Sphere**, **Cone**, **Torus**, **Tube**, and **Create primitives…** (Prism, Wedge, Ellipsoid, Helix, Spiral, Plane, regular polygon, line/point). **Shape builder** assembles shapes from edges/faces.
- *Sketch-based solids:* **Extrude**, **Revolve**, **Loft**, **Sweep**, **Ruled Surface**, **Make face from wires**.
- *Booleans:* **Union (Fuse)**, **Cut**, **Intersection (Common)**, **Boolean** (chooses op), plus **Boolean fragments**, **Slice / Slice apart**, **XOR**, and **Compound / Explode compound**.
- *Modify:* **Fillet**, **Chamfer**, **Mirror**, **Scale**, **2D/3D Offset**, **Thickness** (hollow), **Projection on surface** (project logo/text/wire onto a face), **Defeaturing**.
- *Utility / interop:* **Check geometry** (validate for printing), **Refine shape** (remove redundant edges), **Convert to solid**, **Create shape from mesh**, **Import/Export CAD file** (STEP/IGES/BREP), **Attachment**, simple/transformed copies.

**Strengths:**
- Direct, predictable primitive + Boolean modeling without managing a feature history or a Body.
- The underlying OpenCascade "Part" layer that nearly all other workbenches produce, so it's the common ground for fixing, validating, converting, and combining geometry from anywhere.
- Best home for STEP/IGES import/export, mesh-to-solid conversion, and geometry checks before export.

**Use when:**
- Blocky/CSG parts that are essentially primitives fused and cut, where parametric editability isn't important.
- Repairing, refining, validating, or converting shapes (incl. results from other workbenches or imports).
- Combining whole solids that PartDesign's single-Body model makes awkward; STEP/IGES interchange.

**Avoid when / reach for X instead:**
- A part you'll iterate on with editable named features, holes, patterns, dress-ups → **PartDesign**.
- The profile needs precise constraints → draw it in **Sketcher** first, then Extrude/Revolve here.
- Freeform NURBS faces → **Surface**. Triangle meshes → **Mesh**.

---

## Draft Workbench

**Purpose:** Create and modify 2D objects positioned anywhere in 3D space, define working planes, and apply general-purpose modifiers (move, array, mirror) to 2D or 3D objects from any workbench.

**Key tools by role:**
- *Drafting:* **Line**, **Polyline (Wire)**, **Arc** (and 3-point), **Circle**, **Ellipse**, **Rectangle**, **Polygon**, **B-spline / Bézier**, **Point**, **Facebinder** (face from selected faces), **Shape from text (ShapeString)**, **Hatch**.
- *Annotation:* **Text**, **Dimension**, **Label**, **Annotation styles**.
- *Modification (work on Draft and non-Draft objects):* **Move**, **Rotate**, **Scale**, **Mirror**, **Offset**, **Trimex** (trim/extend), **Stretch**, **Clone**, **Join / Split**, **Upgrade / Downgrade**, **Edit**.
- *Arrays:* **Array (Ortho)**, **Polar array**, **Circular array**, **Path array**, **Point array** (each optionally a Link array).
- *Setup / conversion:* **Select plane** (working plane), the **Draft snap** system and grid, **Draft to sketch** (convert Draft objects ↔ Sketches), **Shape 2D view** (flatten 3D to 2D).

**Strengths:**
- Free placement on any working plane in 3D — not locked to one sketch plane.
- Its modifiers and especially its **arrays** apply to objects from any workbench (e.g. array a Part solid or a Body along a path).
- Quick wires/polygons that can become the base/path for 3D operations (extrude a Draft Polygon, drive a sweep with a Draft Wire).

**Use when:**
- Producing 2D drawings/layouts, profiles, or paths without the constraint overhead of Sketcher.
- You need an orthogonal/polar/path array of an existing 3D part, or to move/rotate/mirror objects precisely.
- Generating a flattened 2D view, or converting between Draft wires and Sketches.

**Avoid when / reach for X instead:**
- A precise, constraint-driven, fully-parametric profile for a solid feature → **Sketcher**.
- Printable production drawings on a titled/scaled sheet (PDF) → **TechDraw** (out of scope here; note only).
- Building the actual 3D solid → **PartDesign** / **Part**.

---

## Assembly Workbench

**Purpose:** Position and constrain multiple already-modeled parts relative to one another with mechanical joints (FreeCAD 1.0+ built-in, Ondsel solver). It arranges parts; it does not model them.

**Key tools by role:**
- *Structure:* **Create Assembly** (root or sub-assembly), **Insert Component** / **Insert a new part**, **Solve Assembly**.
- *Grounding:* **Toggle Grounded** (fix one part as the fixed reference — typically the first part).
- *Joints:* **Fixed**, **Revolute** (hinge), **Slider** (prismatic), **Cylindrical**, **Ball**, **Distance**, **Parallel**, **Perpendicular**, **Angle**. Joints can take limits/offsets and be driven by changing the offset value or via expressions.
- *Motion coupling:* **Rack and Pinion**, **Screw**, **Gears**, **Belt** joints.
- *Outputs:* **Create Exploded View**, **Create Simulation**, **Create Bill of Materials**, **Export ASMT**.

**Typical flow:** model each part as its own Body/solid → Create Assembly → drag parts in (or Insert Component) → Toggle Grounded on the base part → add joints between mating faces/edges/axes → Solve.

**Strengths:**
- Real kinematic joints with a constraint solver; parts retain individual identity and can be dragged to verify motion and check clashes.
- Exploded views, BOM, and simulation for multi-part products.

**Use when:**
- You have several finished parts (each a Body/solid) and need to position them, define how they mate or move, check fit/interference, or produce a BOM/exploded view.

**Avoid when / reach for X instead:**
- Designing the individual parts → **PartDesign** (one Body each).
- A single contiguous object with no moving parts — that's not an assembly; it's one **PartDesign** Body. Multiple non-moving Bodies that just travel together can be grouped in a **Std Part** instead of a full assembly.
- Fusing solids into one new solid (not positioning separate ones) → Boolean in **Part** / PartDesign.

---

## Surface Workbench

**Purpose:** Create and modify simple NURBS surfaces (faces) for shapes the standard solid tools can't produce — bounded/blended/filled faces — that are then stitched into a solid elsewhere.

**Key tools by role:**
- **Filling** — fill a set of boundary curves with a surface (can match neighboring curvature, add constraint curves/points).
- **Fill boundary curves (GeomFillSurface)** — surface from two to four boundary edges.
- **Sections** — surface through edges representing transversal sections.
- **Extend face** — extrapolate a face beyond its boundary.
- **Blend Curve** — a Bézier curve between two edges with chosen continuity.
- **Curve on mesh** — approximate spline curves on top of a mesh (bridge from scanned/mesh data to surfaces).

**Strengths:**
- Parametric, option-rich face creation (curvature continuity, boundary constraints) beyond the non-parametric Part **Shape builder** "face from edges."
- Integrates with PartDesign: build boundary **Sketches** on **Datum Planes** inside a Body, then surface them — fully parametric if datums/sketches are defined accordingly.

**Use when:**
- Freeform or sculpted faces (smooth blended transitions, organic shells, aesthetic Class-A-style surfaces) that Pad/Loft/Revolve can't express.
- You need precise control of surface boundary continuity, or to build faces from a mesh template.

**Avoid when / reach for X instead:**
- Standard prismatic/round mechanical geometry → **PartDesign** (Loft/Pipe already cover many transitions).
- Surfaces don't go inside a Body. To get a printable solid: contain the surfaces (with the source Body of datums/sketches) in a **Std Part**, then stitch into a shell and **Convert to solid** with **Part** (Shape builder / Part tools) before export.

---

## Mesh Workbench

**Purpose:** Handle triangle meshes — import/export STL/OBJ/3MF, analyze and repair, and perform the solid-to-mesh conversion required to send a model to a slicer.

**Key tools by role:**
- *Conversion:* **Create mesh from shape** (solid → mesh; choose mesher and surface/angular **deviation** to balance accuracy vs. file size — the standard last step before STL export), **Import mesh**, **Export mesh**.
- *Analyze/repair:* **Evaluate and repair mesh** (non-manifold edges, holes, defects), **Check solid mesh**, **Harmonize / Flip normals**, **Fill holes / Close hole**, **Add triangle**, **Boundings info**, curvature info.
- *Modify:* **Refinement (remesh)**, **Smooth**, **Decimation** (reduce triangle count), **Scale**, **Regular solid**, **Segmentation**.
- *Combine/cut:* mesh **Union / Intersection / Difference** (require OpenSCAD), **Cut / Trim / Trim by plane**, **Cross-sections**, **Merge**, **Split by components**, **Unwrap mesh/face**.
- *Back to solid:* pair with Part's **Create shape from mesh** → **Convert to solid** (note: mesh→solid is approximate and tedious).

**Strengths:**
- The required bridge between FreeCAD's precise BREP solids and the mesh formats slicers consume (STL is the de-facto 3D-printing format).
- Robust mesh evaluation/repair to catch issues (holes, flipped normals, non-manifold geometry) before slicing.

**Use when:**
- Final export of a finished solid to STL/OBJ/3MF for an FDM/resin slicer (Create mesh from shape → Export).
- Importing a mesh (downloaded model, 3D scan) to inspect, repair, decimate, cut, or reorient.
- Diagnosing/fixing mesh defects before printing.

**Avoid when / reach for X instead:**
- Precise parametric or curved CAD modeling — meshes can't accurately represent curves; do the design in **PartDesign** / **Part** and mesh only at export.
- Editing an imported mesh as if it were a parametric solid → reconstruct geometry in **PartDesign/Part**; reserve Mesh for cleanup/conversion.
- Curves on a mesh for surfacing → **Surface** (Curve on mesh).

## Sources
- PartDesign_Workbench.md
- Sketcher_Workbench.md
- Part_Workbench.md
- Draft_Workbench.md
- Assembly_Workbench.md
- Surface_Workbench.md
- Mesh_Workbench.md
- Which_workbench_should_I_choose.md
- Manual_All_workbenches_at_a_glance.md
- Feature_editing.md
- PartDesign_Body.md
- Sketcher_requirement_for_a_sketch.md
- Manual_Preparing_models_for_3D_printing.md
