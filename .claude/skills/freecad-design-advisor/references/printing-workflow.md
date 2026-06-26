# Preparing Models for 3D Printing: Print-Aware Design and Export

How to fold printability into the modeling approach from the start, then take a finished parametric solid through tessellation, mesh checking/repair, and STL/OBJ export in FreeCAD 1.1 — all at the approach level (which workbench, which feature, in what order), never slicer numbers.

Use this when the user's goal ends in a physical FDM or resin print. The throughline: **model a single watertight solid in PartDesign, design it print-aware up front, then convert to a mesh and verify it in the Mesh workbench before it ever reaches a slicer.**

## Where this sits in the overall workflow

1. **Model** the part as one parametric solid in a **PartDesign Body** (sketch-driven features + dress-up).
2. **Verify the solid** is valid and watertight (Part `Check geometry`).
3. **Tessellate** the solid into a triangle mesh (Mesh `Create mesh from shape`), choosing resolution.
4. **Check/repair** the mesh (Mesh `Analyze` tools, fill holes, fix normals).
5. **Export** to STL/OBJ for the slicer (`File → Export` or Mesh `Export mesh`).

FreeCAD does not slice. A slicer (PrusaSlicer, Cura, OrcaSlicer, etc.) turns the exported mesh into G-code. The advisor's job ends at producing a clean, watertight, correctly-resolved mesh — so design and export decisions should all serve that.

## Print-aware modeling choices (decide these while modeling, not after)

These are STRATEGY calls to make in PartDesign before export. They are far cheaper to bake into the feature tree than to bolt on later.

### Build orientation thinking
- The slicer prints layer by layer along one axis. Orient the part — conceptually — so that **strength runs across layers, not along the layer-bond direction**, since layer adhesion is the weak axis on FDM.
- Favour an orientation that puts the **largest flat face on the bed** for adhesion, and that **minimises overhangs needing support**.
- In PartDesign you don't "set print orientation," but you choose which datum plane a feature's critical faces land on. Sketch the base feature on the plane that makes the intended print-down face natural. The Body's **Placement** and **Origin** let the whole part be reoriented without disturbing internal feature references, so orientation is a late-stage, low-risk adjustment — but the *shape decisions* that suit an orientation (where overhangs fall, where the flat face is) must be made during modeling.

### Wall / shell strategy (Thickness)
- For enclosures, housings, vases, ducts — anything that should be hollow rather than solid — use **PartDesign Thickness** (a dress-up feature) to convert the solid into a shell of uniform wall, selecting the face(s) to leave open.
- Apply Thickness **after** the gross shape exists but typically **before** small dress-up details, so the shell offsets clean faces.
- In 1.1 only the **Skin** mode is implemented (Pipe and Recto Verso are not), so plan for a one-sided uniform-wall shell. Inward offset ("Make thickness inwards") keeps outer dimensions fixed — usually what you want for a part that must fit an external envelope.
- Thickness can **fail on complex shapes** (known OCCT errors, or a silent fail). If so, the approach is to build the hollow form differently — e.g. an **Additive Pipe** or **Additive Loft** for the wall, or model the cavity as an explicit **Pocket/Subtractive** feature — rather than fighting the dress-up.
- Hollowing interacts with print orientation: a shell open at the top prints as an upward-facing cup (good); the same shell needs the opening oriented to avoid a fully enclosed cavity that traps support or uncured resin.

### Strengthening fillets and load paths
- Add **PartDesign Fillet** (rounds) on interior corners that carry load. Sharp internal corners concentrate stress and crack along layer lines; a fillet spreads the load. This is a deliberate strengthening move, not just cosmetics.
- Use **PartDesign Chamfer** on the bottom edge that meets the bed to ease the first-layer "elephant's foot," and on edges where a rounded profile isn't wanted.
- A fillet on a tangentially-connected edge chain propagates along the chain from a single edge selection, so corner-rounding stays cheap to specify.
- **Sequencing rule:** add fillets/chamfers **last**, after the main solid is finalized. They depend on edge naming (topological naming), which shifts if you add geometry afterward — re-running an earlier feature can invalidate a later fillet. `Use all edges` reduces this fragility because it doesn't bind to individual edge names.
- Fillets/chamfers cannot fully consume an adjacent face, and large radii on coincident sharp edges can crash the OCCT kernel — so prefer modest, well-placed rounds over sweeping ones, and keep them late and editable.

### Avoiding unsupported overhangs and fragile thin features
- **Overhangs:** geometry that leans out past what the layer below can support needs slicer-generated support (wasteful, leaves scars). Reshape during modeling to keep overhanging faces within a self-supporting slope, or add a built-in support rib/gusset as an additive feature.
- **Draft for moldability/printability:** **PartDesign Draft** applies an angular taper to selected faces about a neutral plane. Originally for casting/injection draft, it's also useful to turn a vertical-or-overhanging wall into a self-supporting sloped wall. Note Draft only works on faces **not tangentially connected** to others — a common mistake is drafting a face that already has a fillet; the fix is to remove the fillet, draft, then re-apply the fillet (reinforcing the "dress-up last" ordering).
- **Thin features:** thin walls, fins, posts, and text that are fine in CAD can be **below a single extrusion width / nozzle diameter**, so they vanish or print as fragile threads. Keep wall/feature thickness a deliberate design parameter (driven by the printing process, not by what looks right on screen) — express it as "robust enough for the chosen process," and let the user set the actual value. Resin tolerates finer features than FDM; advise accordingly.

### Designing clearances for fit (mating parts and assemblies)
- Printed parts come out slightly oversized at holes and undersized at pegs; mating features printed to nominal CAD size **will not fit**. Design an intentional **gap/clearance** between parts that must assemble (press-fit vs. slip-fit vs. clearance-fit are all *gaps*, sized to the process).
- Model the clearance parametrically: cut the receiving feature (Pocket/Hole) slightly larger than, or offset from, the inserted feature, ideally driven by a single clearance parameter or spreadsheet value so it can be tuned per printer without re-modeling.
- For threaded or snap-fit interfaces, the same principle holds — leave process-appropriate slack; don't model metal-precision contact.
- Each mating part is its **own PartDesign Body** (one contiguous solid each); the gap lives between Bodies in an assembly, not inside a single Body.

### Splitting a part too big for the bed
- When a part exceeds the printer's build volume, plan to print it in pieces and rejoin — choose the split planes *while modeling*, along faces that hide the seam and leave each piece self-supporting.
- **Approach:** cut the finished solid with a tool Body or a datum plane (PartDesign **Boolean → Cut**, or Part **Slice / Slice apart**), making each resulting piece its **own Body** so it exports as a separate printable solid.
- Add **registration features** across the seam — mating peg-and-hole or tab-and-slot pairs modeled as a clearance fit (see the fit guidance above) — so the pieces self-align for gluing; leave room for adhesive.
- Orient each piece for its own best print direction; a seam is also an opportunity to put a fresh flat face on the bed.

## The single-watertight-solid requirement

A slicer needs a closed, manifold surface with a well-defined inside and outside. The cleanest way to guarantee that is to start from a **valid solid B-rep** and tessellate it.

### How the Body / feature workflow supports this
- A **PartDesign Body** is purpose-built to produce a *single contiguous solid*: one piece, no disconnected lumps, no internal gaps. It **auto-fuses** additive features, so overlapping/touching adds become one solid automatically.
- Constraints that keep you watertight: features must touch/intersect the existing material (disconnected solids aren't allowed in a Body), and the software flags operations that would break the solid. Build the part as base feature (Pad/primitive/Revolution/etc.) → additive/subtractive features → dress-up, all inside one Body.
- **One printable part = one Body.** Multiple separate printed parts = multiple Bodies, arranged (not fused) in a **Std Part** / assembly. Do not try to model two disconnected solids in one Body.
- The Body's **Tip** is the final shape exported to other tools and to meshing. Select the **Body itself (or its Tip)** for export — not an intermediate feature, not a stray face.

### Verify the solid before meshing (Part Check geometry)
- Switch to the Part workbench and run **`Part → Check geometry`** on the whole part (select the Body/solid, not a single face). It validates the B-rep and can additionally run a **BOP (Boolean operation) check**.
- Clicking a reported error highlights the offending edge/face in the 3D view. It also reports shape content — including the **solid count** (you want exactly one solid) and volume (a valid printable body has non-zero volume).
- **FreeCAD cannot auto-repair B-rep geometry.** If Check geometry finds faults, fix them at the source — revisit the feature(s) that created them (a self-intersecting sketch, a failed boolean, a degenerate fillet) and correct the model. Meshing a faulty solid just bakes the fault into the mesh.

## Solid → mesh: tessellation

Meshing converts the exact curved B-rep into flat triangles. This is lossy by nature; the goal is to keep the deviation invisible at print resolution without bloating the file.

### Creating the mesh (Mesh workbench)
- Load the **Mesh Workbench** (not loaded by default), select the Body, and run **`Meshes → Create mesh from shape`**. The **Tessellation** task panel opens.
- Pick a mesher tab. The **Standard** mesher is always available and is sufficient for parts that fit a typical print bed. **Mefisto / Netgen / Gmsh** may or may not be present depending on how the build was compiled — don't assume them.
- This works on any object with a shape, including PartDesign Bodies, not just Part objects.

### Choosing mesh quality (conceptually, no numbers)
- **Surface deviation** is the main lever: the maximum linear gap between a triangle and the true surface. **Smaller deviation = finer mesh = closer to the CAD surface, but larger files.** Coarse settings leave visibly faceted curves; over-fine settings explode triangle count for no print-visible gain.
- **Angular deviation** controls how finely *curved* surfaces are split as the surface turns — important for cylinders, holes, and rounded faces so circles don't print as polygons.
- **Relative surface deviation** scales the allowed deviation by feature size — useful when the model mixes large and small features.
- Practical approach: **start with the default deviation; tighten it only if curves look faceted; loosen it if the triangle count is excessive.** Flat-faced mechanical parts mesh perfectly at coarse settings; parts dominated by curves and small holes want a finer deviation. Resin (high-res) rewards finer meshes; FDM rarely benefits past a point.
- Optional flags: **apply face colors to mesh** and **define segments by face colors** (the latter groups regions for formats that support it, e.g. OBJ) — generally irrelevant for plain FDM/STL printing.

### Inspect before trusting it
- Set the new mesh object's **Display Mode** to **Flat Lines** to see the actual triangulation, and compare against the hidden solid. Re-mesh with a different deviation if curves are too coarse. This is the quickest visual quality check.

## Checking and repairing the mesh before slicing

Even a valid solid can produce a mesh a slicer dislikes. Run these in the Mesh workbench before export.

### Is it solid/watertight?
- **`Meshes → Analyze → Check solid mesh`** (Mesh EvaluateSolid) reports whether the mesh is solid — i.e. **has no holes**. A non-solid mesh is the classic cause of slicer failures or missing walls.

### Full evaluate & repair
- **`Meshes → Analyze → Evaluate and repair mesh`** (Mesh Evaluation) is the main tool. It runs a battery of tests — **non-manifold points/edges, self-intersections, degenerate (zero-area) faces, duplicated faces/points, orientation, indices, and optionally folds on the surface**.
- Workflow: press **Analyze** on an individual test, or **Analyze "All above tests together"**. Errors are listed and marked in the 3D view (yellow/red). Then press the matching **Repair** button to fix found problems. Don't tick the checkboxes yourself — they auto-tick when an error is found.
- **Caveat:** repairing often works by **deleting** the offending faces, which can leave **holes**. So re-check for holes after repairing.

### Closing holes
- **`Meshes → Fill holes`** (Mesh FillupHoles) closes holes up to a chosen complexity (max edges per hole). For stubborn or specific holes, **Close hole** (FillInteractiveHole) and **Add triangle** (AddFacet) let you patch by hand.

### Normals (inside vs. outside)
- Every triangle has a normal that must point **outward** so the slicer knows which side is solid. Inconsistent normals cause inverted shells and weird slices.
- **`Meshes → Harmonize normals`** makes all face normals consistent with each other. If the result ends up consistently *inward*, **`Meshes → Flip normals`** reverses them. (Harmonize can sometimes flip the whole mesh inward — flip to correct.)
- To *see* orientation, set the mesh's **Lighting** property to **One side**; back-faces then render in the backlight color, making wrong-facing triangles obvious.

### Re-check, then export
Loop Evaluate → Repair → Fill holes → Harmonize/Flip until **Check solid mesh** reports solid and Evaluate finds nothing. Only then export.

## Exporting STL / OBJ

Two equivalent routes; the difference is whether you mesh explicitly first.

### Method A — direct export from the solid (`File → Export`)
- Select the Body (or its Tip) and choose **`File → Export`** (Ctrl+E), file type **STL** (or OBJ/3MF). FreeCAD tessellates on the fly and writes the mesh.
- The resolution here is governed by a **separate preference**: **`Edit → Preferences → Import-Export → Mesh Formats → Maximum mesh deviation`** (lower = finer). This page only appears once the Mesh workbench has been loaded, and **only `Std Export` uses it** — the Mesh workbench's own export does not.
- Fast and fine for most parts, but you **can't inspect or repair** the mesh before it's written.

### Method B — explicit mesh, then `Export mesh` (recommended for printing)
- Mesh the solid with **Create mesh from shape** (full control over deviation/angular settings), **check and repair** it as above, then right-click the mesh → **`Export mesh`** (or `Meshes → Export mesh`).
- This is the method to use when you want to **verify** the mesh, tune resolution precisely, or **combine multiple solids** (mesh each, then Mesh boolean/`Merge`) into one file.

### Format choice
- **STL** is the de-facto 3D-printing format: triangles only, no color/material, universally accepted by slicers. Default for FDM/resin.
- **OBJ / 3MF** carry extra data (colors, materials, segments); use only if a downstream tool needs them — they add nothing for a plain monochrome print and can complicate slicing.
- (For CNC/machining instead of printing, prefer **STEP/IGES** to keep exact geometry — but that's a different pipeline.)

### Units
- Mesh formats are **unitless**; FreeCAD writes assuming the model is in **millimetres**. If the model was built in other units, **scale it before export** (e.g. Draft Scale) or the slicer will import it at the wrong size.

## Common gotchas that produce unprintable exports

- **Exporting the wrong selection.** `Ctrl+A` / Select-all grabs invisible objects and Body sub-elements; this exports duplicate/hidden junk. Select only the **Body or its Tip**. Don't use Select-All for export.
- **Exporting a non-solid / open shell.** A surface or a Body that never closed gives a mesh with holes. Run **Check geometry** (one solid, non-zero volume) and **Check solid mesh** first.
- **Faceted curves.** Default deviation too coarse for a curve-heavy part → polygonal holes and cylinders. Tighten **surface/angular deviation** and re-mesh.
- **Bloated files.** Deviation set needlessly fine → millions of triangles that slow or crash the slicer with no print-visible benefit. Loosen it.
- **Inverted/inconsistent normals** → slicer reads inside-out. **Harmonize**, then **Flip** if needed; verify with One-side lighting.
- **Holes left after auto-repair.** Evaluate's repair deletes bad faces; always re-run hole-filling and re-check solidity afterward.
- **Wrong scale.** Model not in mm exported to a unitless format → part comes out off by a whole unit-conversion factor (e.g. a mm/inch or cm/mm mismatch). Scale before export.
- **Dress-up applied too early / topological-naming breakage.** Fillets and chamfers added before the solid was finalized can silently invalidate or vanish on recompute, leaving sharp stress risers (or a broken feature) in the exported shape. Keep dress-up last; prefer `Use all edges` where appropriate.
- **Thin features below process resolution.** Walls/fins/text thinner than one extrusion width disappear or print fragile. Make minimum thickness a deliberate, process-driven parameter.
- **Zero-clearance mating parts.** Nominal-fit mates won't assemble after printing. Design intentional gaps between Bodies.
- **Thickness silently failing on complex shapes.** If the shell looks wrong or empty, switch strategy (Additive Pipe/Loft, or explicit Pocket cavity) rather than trusting a failed dress-up.

## Sources

- Manual_Preparing_models_for_3D_printing.md
- Export_to_STL_or_OBJ.md
- Mesh_FromPartShape.md
- Mesh_Evaluation.md
- Mesh_EvaluateSolid.md
- Mesh_FillupHoles.md
- Mesh_HarmonizeNormals.md
- Mesh_FlipNormals.md
- Mesh_Export.md
- Mesh_Workbench.md
- Std_Export.md
- Import_Export_Preferences.md
- Part_CheckGeometry.md
- Part_Slice.md
- PartDesign_Body.md
- PartDesign_Thickness.md
- PartDesign_Fillet.md
- PartDesign_Chamfer.md
- PartDesign_Draft.md
