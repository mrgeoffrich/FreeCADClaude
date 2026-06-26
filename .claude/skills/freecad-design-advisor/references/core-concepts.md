# FreeCAD Mental Model for Design Advice

The conceptual foundation every FreeCAD 1.1 workflow recommendation must respect: how parametric modeling, containers, sketches, datums, and model intent actually work, so the advised feature sequence stays editable and printable.

## Parametric, history-based modeling
- FreeCAD geometry is **parametric**: each object's shape is generated from parameters (dimensions, references, placements), not sculpted. Change a parameter and the object — and everything downstream — regenerates.
- A model is a **directed acyclic graph (DAG)** of objects. Dependencies must flow one direction; circular dependencies are forbidden. A sketch feeds a Pad; the Pad's face can feed another sketch, and so on down a chain.
- **Feature order is the design.** In PartDesign each feature is cumulative — it adds or subtracts material from the result of the feature before it. The same features in a different order can produce a different solid (e.g. a Pocket placed before vs after the Pad it should cut through, or a hole that lands in a different body). Reordering is a real edit, not a cosmetic one.
- Recompute is usually automatic, but heavy operations are deferred — objects get a "touched"/recompute marker and must be refreshed. When advising, assume edits ripple forward through the whole chain.
- The takeaway for advice: **sequence and references are the product.** Recommend an ordered list of features whose dependencies point cleanly backward toward stable references.

## Workbench choice: PartDesign vs Part
- **PartDesign Workbench = feature editing.** The default recommendation for designing a manufacturable/printable mechanical part. Build one cumulative solid by stacking parametric features on a Body. Tools: Pad, Pocket, Revolution, Groove, Additive/Subtractive Loft, Additive/Subtractive Pipe, Helix, additive/subtractive primitives, Hole, dress-ups, and transforms.
- **Part Workbench = constructive solid geometry (CSG).** Independent primitive solids combined with Boolean Fuse/Cut/Common. Each primitive stays a separate object until combined. Reach for it for quick blockouts, importing/operating on external solids, or Boolean combinations that are awkward inside one Body.
- **Sketcher** underlies both: it produces the 2D constrained profiles that PartDesign (and Part Extrude/Revolve) turn into solids. All Sketcher tools are available inside PartDesign, so you rarely switch workbenches mid-part.
- **Mixing is possible but discouraged for newcomers.** A Body can be exported (via its Tip) into a Part Boolean, but the result becomes a Part object that PartDesign tools can no longer edit. To resume feature editing, drag that result into a new Body as a Base Feature. Prefer staying within one paradigm per part unless there's a clear reason.

## Containers — and the rule that defines them
Two container types, easily confused, with opposite purposes:

- **PartDesign Body** — models **one single contiguous solid**. This is the core rule: a Body must resolve to one connected lump of material (a casting/machined-from-one-block analogy). Disconnected solids are not allowed; each new additive feature must touch or intersect the existing solid so the Body auto-fuses into one piece. If your design has separate pieces, screws, glue, or moving parts, that's multiple Bodies, not one. (v1.0+ has an experimental option to permit temporarily non-contiguous solids, intended only for features that later features reconnect — not for housing multiple parts.)
- **Std Part** — a general **assembly/grouping container**. It holds Bodies (and other shaped objects) and moves/positions them together as a unit via its Placement and Origin. It does no modeling; you cannot Boolean two Std Parts. Std Parts nest to build sub-assemblies. Use it to arrange the separate Bodies of a multi-part design in space.
- Each container provides an **Origin**: the standard XY/XZ/YZ planes and X/Y/Z axes. Everything inside is referenced to that local Origin, so the whole Body or Part can be repositioned without disturbing internal geometry. These standard planes/axes are the preferred attachment references (see below).
- **Tip**: the Body's exposed final feature — the shape other workbenches see, and where new features get inserted. Normally the last feature; you can temporarily Set Tip to an earlier feature to insert work mid-history, then must restore it to the last feature.
- **Base Feature**: an external/imported solid (e.g. a STEP import or a Part result) dragged into a Body to become its first feature, so PartDesign features can build on it.
- **One Body per physical part.** Recommend the part count up front: a single contiguous component → one Body; an assembly of several components → one Body each, grouped under a Std Part.

## Sketch-based features and the base sketch
- Most PartDesign features start from a **Sketch**: a 2D profile of lines/arcs/curves plus constraints, living on a plane or face. Pad/Pocket extrude it; Revolution/Groove spin it about an axis; Loft transitions between sketches; Pipe sweeps a profile along a path sketch; Helix sweeps along a helix.
- **What makes a good profile sketch:**
  - **Closed contour(s).** Endpoints must actually coincide (use Coincident); gaps, however small, fail to make a solid. Nested contours create voids but must not self-intersect, share edges, or form T-junctions. Use construction geometry for reference lines you don't want in the solid.
  - **Fully constrained** (all geometry green, zero DoF). A Body can be padded from an under-constrained sketch, but parametric stability requires full constraint so edits behave predictably and nothing drifts. Apply geometric constraints first, then dimensional, and lock position last.
  - **Anchored to the Origin.** Center the profile on the Origin (symmetry constraints) or lock a point to it, so the feature has a stable, predictable location independent of other geometry.
  - **Keep sketches simple and split work across several.** A series of simple sketches (base profile in one, cutouts/holes in another) is far easier to manage and edit than one complex sketch. Leave fillets/chamfers out of sketches — add them as 3D dress-up features.
- **Where to place a sketch — this is the key stability decision:**
  - **On a standard Origin plane** (XY/XZ/YZ): most stable; preferred for the base sketch and whenever possible. A non-standard orientation can be achieved with the sketch's own attachment offset, so a sketch doesn't always need a datum.
  - **On a datum plane** attached to the Origin: use when several sketches share the same non-standard orientation, or to pre-visualize stack heights.
  - **On a model face** (a generated face of an existing feature): convenient and intuitive, but the riskiest reference — generated faces get internally renamed when the model changes (see topological naming). Acceptable for simple stacks; avoid as the model grows or when the underlying feature may change.
- **External geometry** projects edges/vertices of existing geometry into a sketch so you can constrain to them. Prefer referencing another **sketch's** geometry over a solid's generated edges/faces.

## Datum geometry and attachment
- **Datum objects** — Datum Plane, Datum Line, Datum Point, and local Coordinate System (LCS) — are auxiliary reference geometry. They never become part of the final shape; they exist to support and position sketches and features. They are also useful purely as visual reference markers. *(In v1.1 these datum tools moved from PartDesign into the base/Std system, so they're usable across workbenches, e.g. for assemblies; conceptually they behave as before.)*
- **Use datums instead of model faces when:**
  - you're placing sketches in **non-standard (offset/rotated) orientations**, especially when **multiple** sketches or features share that orientation — adjusting one datum then repositions all of them at once;
  - you want stable references for a feature that would otherwise have to attach to a generated face/edge;
  - you want to pre-lay reference geometry before any solids exist, to plan the part.
- **Attachment** links an object's placement to one or more references; if the reference moves, the attached object follows. Driven by the **Map Mode** property (and the attacher engine). Pick references in the 3D view, choose a mode (the likely one is bold), and refine with **Attachment Offset** (translations and rotations *in the attachment's local coordinate system* — note the local Z is along a datum plane's normal). The selecting-a-plane dialog when creating a sketch is just a simplified attachment with zero offsets.
- The attached object's **origin is the "hook"** that lands on the reference — how the sketch is constrained relative to its own origin determines where attachment places it. Common modes: FlatFace/plane, Normal-to-edge, Plane-by-3-points, Inertia (center of mass), Concentric, the O-axis "Align" family. Map Path Parameter slides an edge-based attachment along the edge — handy for positioning Loft/Sweep sections.
- A **temporary-attachment trick**: to align to a generated face without the fragility, attach to that face, then re-map the object to a coordinate plane — FreeCAD keeps the position but now references a stable plane (you lose the parametric link to the face, so re-do it if the model changes).
- Datums can chain off other datums; this is lower-risk than referencing solid faces because datums are simple objects, but long chains still propagate change.

## The topological naming problem (TNP)
- **What it is:** after a modeling operation, the kernel may internally **rename** faces/edges/vertices (e.g. Face13 → Face14). Any feature or sketch that referenced the old name now points at the wrong element — sketches flip to odd orientations, downstream features break, dimensions measure the wrong edge. It's most visible in PartDesign (features on faces) and TechDraw.
- **FreeCAD 1.1 mitigates but does not eliminate it.** The naming algorithm's job is mainly to **identify** the breaking operation and flag it, sometimes **suggest** a fix (common for fillets/chamfers — you accept or re-pick), and occasionally **auto-repair** with high confidence (e.g. sketch-on-face under simple parametric change). Structural changes (inserting/deleting mid-tree features) are least likely to auto-heal. So the good GUI habits still matter.
- **Habits that prevent breakage (advise these by default):**
  - **Attach sketches and datums to standard Origin planes/axes** (or to datums attached to those), not to generated faces/edges/vertices. Use attachment offsets for position.
  - Prefer **referencing sketches or sketch geometry** over generated geometry. If you must reference generated geometry, reference it at the **earliest feature where that element first appears**, so later edits don't disturb it.
  - Use a **master/base sketch** done first, holding the part's core geometry — since it precedes everything, it can only reference the stable Origin, and later features reference it (directly, or via a ShapeBinder/SubShapeBinder if it lives outside the Body). Don't build (Sub)ShapeBinders from generated geometry.
  - **Apply dress-ups (Fillet, Chamfer, Draft, Thickness) as late as possible** in the tree, and don't reference fillet/chamfer-generated edges from later features.
  - Cost/benefit: datums and explicit attachment are more setup work but yield models that survive parameter changes; this trade is almost always worth it for parts meant to be revised.

## Model intent / designing for change
- **Model intent** means structuring the tree so that the edits you anticipate propagate cleanly. Decide what's likely to change (a width, a hole pattern, a wall) and make those parameters drive everything else.
- **Expressions** let one dimension drive others (e.g. a feature length tied to a named sketch constraint, or a sketch positioned on a Pad's top via a Pad-length offset expression), so a single edit updates the whole part consistently.
- **Order for stability:** base/master sketch on a standard plane → primary additive feature (Pad/Revolution) → further additive/subtractive features referencing sketches and datums → patterns/mirrors of features (LinearPattern, PolarPattern, Mirrored, MultiTransform) → dress-ups (fillets/chamfers/draft/thickness) last.
- **Patterns reference features, not the whole solid** — Mirrored/LinearPattern/PolarPattern operate on the selected feature(s); mirroring a subtractive feature across a datum reproduces the cut, and the result must still form one valid contiguous solid. Pattern the generating feature, and mirror across stable planes/datums.
- **The history is an asset**: keep it editable to retune dimensions and pattern counts later. When you specifically want to discard history (e.g. to reuse a finished part many times), make a simple copy or round-trip through STEP — a deliberate choice, not the default.
- **For 3D printing (FDM/resin):** PartDesign's single-contiguous-solid discipline directly serves printability — it keeps the model solid and watertight, which is exactly what a slicer needs. Keep the part a valid solid throughout, then export to STL (mesh, with a deviation/resolution choice) for slicing, or STEP for downstream CAD/CAM. Print-oriented intent (wall continuity, overhang/orientation, hole/clearance fit) should be expressed as parametric features and expressions so they're easy to tune per printer/material — without baking specific values into the mental model here.

## Sources
- Feature_editing.md
- Part_and_PartDesign.md
- PartDesign_Body.md
- Topological_naming_problem.md
- Std_Part.md
- PartDesign_Workbench.md
- Manual_Modeling_for_product_design.md
- Manual_Parametric_objects.md
- Datum.md
- Sketcher_requirement_for_a_sketch.md
- PartDesign_Feature.md
- Part_EditAttachment.md
- PartDesign_CoordinateSystem.md
- Sketcher_MapSketch.md
- PartDesign_NewSketch.md
- Document_structure.md
- Sketcher_Workbench.md
- PartDesign_Plane.md
- Sketch.md
- Body.md
- PartDesign_MoveFeatureInTree.md
- PartDesign_MoveTip.md
- Manual_Preparing_models_for_3D_printing.md
- Constructive_solid_geometry.md
- Property_editor.md
- Basic_Attachment_Tutorial.md
