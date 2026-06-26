# Design Archetypes: Idea-to-Recipe Catalog (FreeCAD 1.1)

Pattern-match a user's design idea against these mechanical / 3D-print archetypes; each gives the lead workbench and an ordered, feature-named recipe to build it. GUI workflows only — no scripting.

## How to use this catalog

- Identify the dominant geometry of the idea, then pick the closest archetype below. Real parts often combine several: build the bulk shape with one archetype, then layer features from others.
- Steps are **ordered** and named with real FreeCAD 1.1 features. Follow them top-to-bottom.
- Speak to the user in features and relationships, not dimensions. This doc deliberately avoids specific numbers.

## Universal Part Design scaffold (applies to most archetypes)

Almost every single-piece part follows this skeleton; the archetypes below are specializations of it.

- **Create Body** — every Part Design part lives in one Body, which auto-fuses its additive features into a single contiguous solid. One physical part = one Body.
- **Create Sketch** on an Origin plane (XY/XZ/YZ) — choose the plane so the part's natural "footprint" or "profile" lies on it and symmetry axes fall on sketch axes.
- **Base feature** — turn the first sketch into a solid (Pad, Revolution, Additive Loft, or Additive Pipe). This is the bulk of the part.
- **Additive/subtractive features** — add Pads/bosses and cut Pockets/Holes/Grooves, each on a sketch mapped to a face or datum plane of the growing solid.
- **Transformations** — replicate features with LinearPattern, PolarPattern, Mirrored, or MultiTransform instead of re-drawing them.
- **Dress-up LAST** — Fillet, Chamfer, Draft, Thickness. Apply these after the main geometry is stable to avoid topological-naming breakage.

**Cross-cutting intent rules**
- Keep the part a single connected solid: every new additive feature must touch/overlap the previous material, or the Body errors. Disconnected lumps are not allowed in one Body.
- Model symmetric parts as a quadrant/half and Mirror/MultiTransform — fully parametric and far simpler than drawing the whole outline.
- Capture key design dimensions in **datum planes/axes** (skeleton geometry) and reference machining/secondary features to those datums, so the part updates coherently when a driving dimension changes.
- Sketch on **flat datum planes**, not on slanted/curved faces, when you want a feature's direction controlled and stable.
- Leave fillets/chamfers/draft and shelling to the end; they are fragile to edit-order changes and OCCT can crash on overlapping/over-large rounds.

---

## Prismatic part / mounting plate / bracket

- **Matches:** flat plates, base plates, motor/sensor mounts, gussetless brackets, angle brackets, anything that is essentially "an extruded 2D outline with holes." The PartDesign Pad gallery (L/C/Z/T/H/U profiles, slabs) is exactly this family.
- **Lead workbench:** Part Design.
- **Feature sequence:**
  - **Sketch** the plate outline on an Origin plane (rectangle, rounded-rect, or an L/U/angle profile). Put symmetry on the sketch axes.
  - **Pad** the outline to plate thickness (use *Symmetric to plane* if the plate should straddle the sketch plane).
  - **Sketch + Pocket** any windows/cut-outs/lightening openings, mapped to a face.
  - **Sketch + Hole** for fastener holes (gives counterbore/countersink, clearance/tapped sizing). Use a Hole, not a plain Pocketed circle, whenever a real screw goes through.
  - **LinearPattern / Mirrored / PolarPattern** to replicate hole groups and slots instead of redrawing.
  - **Pad** local bosses/standoffs where fasteners land; **Chamfer/Fillet** outer corners and edges last.
- **Model intent:** the outline sketch is the single source of truth for the footprint; constrain holes to edges or to datums so they track if the plate is resized. For a right-angle bracket, either Pad an L-profile in one shot, or Pad a flat plate then Pad a second wall on a perpendicular face — the L-profile is cleaner and stays one solid.
- **Pitfalls:** open/unclosed sketch profile → "failed to validate broken face." Using Pocket-circles where Hole features belong loses fit/thread metadata. Fillet/chamfer added before holes exist can invalidate edges later.

## Enclosure: box + lid, shelled/hollowed, with registration lip

- **Matches:** project boxes, electronics enclosures, battery cases, any hollow housing — especially "box with a removable lid that locates onto the base."
- **Lead workbench:** Part Design (model base and lid as **separate Bodies**, one per printed piece).
- **Feature sequence (base shell):**
  - **Sketch** the outer footprint; **Pad** to full outer height to get a solid block.
  - **Fillet** the vertical corners (and base edges) now, while faces are simple — fillets propagate into the shelled walls cleanly.
  - **Thickness** with the *top face selected as the open face* → hollows the block to a uniform-wall open box (Skin mode, offset inward).
  - **Sketch on the top rim + Pad** a thin upstand around the wall to form the **registration lip** (the tongue that the lid's groove captures); or **Pocket** a recess inboard of the rim for the lid to drop into.
  - **Sketch + Hole / Pad bosses** for screw posts, cable glands, mounting feet, vents.
- **Feature sequence (lid, new Body):**
  - **Sketch** the lid footprint matching the box outer profile; **Pad** the lid plate.
  - **Pocket / Sketch+Pad** a mating groove or rim that registers against the base lip (model with a clearance gap — see the fit archetype).
  - **Thickness** if the lid itself should be a shallow shell; **Hole** for lid fasteners aligned to the base posts.
- **Model intent:** uniform wall thickness via Thickness is what makes it printable and light; do corner fillets *before* Thickness so the inner cavity inherits rounded corners. The lip/groove pair is a deliberate clearance fit, not a coincident contact. Keep base and lid as two Bodies so each exports as its own printable part.
- **Pitfalls:** Thickness silently fails or throws OCCT errors on complex/over-thick shells — keep walls well under the smallest interior dimension and shell before adding lots of small bosses. Don't try to model base+lid in one Body (they're separate parts). A lid with zero clearance to the lip won't assemble after printing.

## Revolved part: pulley, knob, spacer, bushing, wheel

- **Matches:** anything with rotational symmetry about an axis — pulleys, knobs, spacers, bushings, nozzles, flanged collars, hand-wheels, turned feet.
- **Lead workbench:** Part Design.
- **Feature sequence:**
  - **Sketch** a closed **half-profile** on a plane containing the axis: draw the cross-section on one side of a centerline. Add a construction line on the axis if you want an explicit revolve axis.
  - **Revolution** the profile a full turn about the chosen axis (vertical/horizontal sketch axis, a construction line, or a Base axis) → the solid of revolution.
  - **Groove** for circumferential features cut by revolving a small profile: V-belt grooves on a pulley, seal/O-ring grooves, retaining-ring grooves, knurl-relief.
  - **Hole** on axis for a bore/shaft hole; add a **Pocket** flat or keyway if it must key to a shaft.
  - **PolarPattern** any repeated radial features (finger grips on a knob, spokes, lightening holes) about the axis.
  - **Fillet / Chamfer** edges last (lead-in chamfer on bores, rounded rims).
- **Model intent:** the entire silhouette is captured in one profile sketch, so the part is trivially re-proportioned by editing that sketch. Build belt/seal grooves as Grooves (revolved cuts) rather than many pockets. Put the bore on the same axis as the Revolution so everything stays concentric.
- **Pitfalls:** the revolve profile must be closed and must not cross the axis. Angles over a full turn aren't allowed. For a near-spherical/torus piece, a swept profile (Additive Pipe along a circle) can be more robust than a single Revolution.

## Swept part: handle, tube, conduit, trim, gasket

- **Matches:** constant-or-varying cross-section dragged along a path — grab handles, hoses/tubes/conduit, cable raceways, bent pipes, wire guides, gaskets, edge trims, ergonomic grips.
- **Lead workbench:** Part Design (Additive Pipe). Surface/Sketcher for tricky 3D paths.
- **Feature sequence:**
  - **Sketch the path (spine)** as a single continuous open or closed curve — lines and arcs/splines, no branches or T-junctions (loops allowed).
  - **Sketch the cross-section** on a plane **orthogonal to the path start**, with the profile's origin sitting on the path. Use a datum plane / Map Mode attachment to align it to a path endpoint.
  - **Additive Pipe** — select the cross-section, set the path as the spine → solid tube/handle. For a hollow tube, draw the cross-section as two concentric loops (outer + inner).
  - For varying section, set **Multisection** and **Add Section** for each extra profile along the path (keep all sections with matching segment counts).
  - **Pad/Hole** end bosses, mounting tabs, or end caps where the swept body meets flat mounting features; **Fillet** transitions.
- **Model intent:** the spine encodes the routing; the section encodes the shape. Keep the section truly perpendicular to the spine in 3D or the sweep distorts. Use *Frenet* or *Auxiliary* orientation if a circular path twists the profile unexpectedly. For threads/coils, sweep along a Part Helix instead of a sketched path.
- **Pitfalls:** non-perpendicular section, branched paths, or a section coplanar with the preceding one all break the pipe. Sharp path corners may need the *Round corner* transition. Multisection profiles with mismatched vertex counts produce twisted/invalid walls.

## Lofted transition / adapter between two profiles

- **Matches:** shape adapters — round-to-square duct, fan shroud, hopper/funnel, speaker horn, transition fittings, a boss that morphs from one footprint to another.
- **Lead workbench:** Part Design (Additive Loft).
- **Feature sequence:**
  - **Sketch profile A** on one plane (e.g. the round end) and **Sketch profile B** on a parallel **datum plane** offset away (e.g. the square end). Add intermediate section sketches on more datum planes if the transition needs guiding.
  - **Additive Loft** — pick the base profile, **Add Section** for each subsequent profile in order; toggle *Ruled* for straight blends or leave smooth.
  - **Thickness** to hollow it into a duct/funnel wall (open both ends by selecting the two end faces), or draw both profiles as closed rings for a built-in wall.
  - **Pad** flanges at each end (sketch on the end face), then **Hole/Pocket** their bolt patterns; **Fillet** the flange-to-body junctions.
- **Model intent:** each cross-section lives on its own datum plane, so spacing/scale of the transition is parametric. Matching the number of sketch segments between profiles (e.g. break a circle into arcs to match a rectangle's sides) controls how the surface twists. Loft for *transitions between different profiles*; reach for Additive Pipe when one profile follows a path.
- **Pitfalls:** wildly different vertex counts between sections cause puckering or a black (failed) solid. A section may not lie on the same plane as the one before it. Thickness on a complex loft can fail — consider lofting the wall directly with ring profiles instead.

## Ribbed / gusseted reinforced part

- **Matches:** parts that need stiffening without bulk — ribbed brackets, gusseted L-mounts, webbed bosses, reinforced thin walls, cantilevered shelves.
- **Lead workbench:** Part Design.
- **Feature sequence:**
  - Build the base part first via the **prismatic** or **enclosure** recipe (Pad / Thickness) so the faces a rib will tie into already exist.
  - **Sketch the rib profile** on a plane that cuts through where the rib runs (often an Origin plane or a datum plane through the joint), referencing the existing faces as **external geometry** so the rib snaps to them.
  - **Pad** the rib — typically *Symmetric to plane* or *Up to face* so it spans cleanly between the two surfaces it braces. The rib must intersect both members to fuse in.
  - **LinearPattern / Mirrored** to repeat ribs along a wall or mirror a gusset across a symmetry plane.
  - **Fillet** the rib roots into the parent faces last (improves print strength and stress flow); apply **Draft** to rib sides if it's a molded/cast part.
- **Model intent:** ribs are just thin Pads that bridge existing geometry — anchor their sketches to the host faces via external geometry so they follow if the part resizes. Triangular gussets are a triangle sketch padded between a vertical and horizontal face. Keep rib thickness proportionate to the wall it stiffens for clean FDM printing.
- **Pitfalls:** a rib that doesn't actually touch the parts it braces leaves a disconnected solid (Body error). Filleting before the rib is placed, or trying to draft a face that already carries a fillet, fails — draft first, fillet after. Over-thick ribs cause FDM sink/warp.

## Multi-body part assembled with Booleans

- **Matches:** a single physical part whose shape is most naturally described as primitives combined/subtracted — "cylinder through a block," "two intersecting bosses," "block minus a sculpted cavity," or merging a separately-modeled feature body into the main part.
- **Lead workbench:** Part Design (PartDesign Boolean) for parametric history; Part Workbench CSG for quick primitive-based blocking.
- **Feature sequence (Part Design Boolean):**
  - **Model each chunk as its own Body** (e.g. a base Body, and a tool Body shaped like the cavity or the add-on). Each Body is a normal Part Design solid in its own right.
  - **Activate the receiving Body**, run **PartDesign Boolean**, **Add body** for each tool Body, and choose **Fuse** (merge), **Cut** (subtract), or **Common** (intersection).
  - Continue with ordinary **Pad/Pocket/Hole/Fillet** features on the now-combined Body.
- **Feature sequence (Part CSG alternative):** create primitives (Box, Cylinder, …), set their Placement, then **Cut / Fuse / Common**; the operands stay nested under the result for later edits. Good for fast massing; lacks the sketch-driven parametrics of Part Design.
- **Model intent:** prefer keeping everything inside one Part Design Body with additive/subtractive features when you can — it's simpler and stays one solid. Reach for Booleans when a sub-shape is far easier to author as its own body, or to reuse one tool body for several cuts. Make boolean operand faces slightly overlap rather than exactly coincident to dodge face-on-face failures.
- **Pitfalls:** *Common* needs all tool bodies to mutually intersect the active body. Tool bodies inherit the active Body's local origin — keep the active Body at the global origin to avoid placement surprises. Exact coincident faces in CSG cuts are flaky; oversize the cutting solid so it pokes through.

## Patterned features: bolt circle, vent slots, repeated bosses

- **Matches:** any regularly repeated feature — bolt circles/flange holes, ventilation slot arrays, cooling fins, perforation grids, repeated standoffs/clips, gear-like teeth.
- **Lead workbench:** Part Design (LinearPattern, PolarPattern, Mirrored, MultiTransform).
- **Feature sequence:**
  - Build **one** instance of the feature first (a single **Hole**, **Pocket** slot, or **Pad** boss) and constrain it correctly relative to a center/edge.
  - **Bolt circle / radial array:** **PolarPattern** that feature about the part's axis; set occurrences and overall/offset angle (full turn distributes evenly around the circle).
  - **Row / grid of slots or bosses:** **LinearPattern** along a Base axis or sketch axis; for a 2-axis grid or a quadrant-symmetric plate, use **MultiTransform** (e.g. mirror about one axis, then the other) so the whole part is driven by one quadrant.
  - **Symmetric pair:** **Mirrored** about a sketch axis or a Base plane — ideal for left/right hole pairs and mirrored bosses.
  - **Pattern-of-a-pattern:** wrap multiple transforms in a single **MultiTransform** (a pattern can't be applied directly on top of another pattern).
- **Model intent:** model the prototype feature once and let the transform own the count/spacing — editing the original updates every copy. Choose the pattern axis/plane from the Body Origin or a construction line so it's stable. For huge instance counts, patterns get slow; a Draft array fused with a Part boolean is the escape hatch (but leaves Part Design).
- **Pitfalls:** any patterned instance that doesn't overlap the parent solid is silently dropped (keeps the Body single-solid) — watch arrays that run off the edge. Patterns of *Up to face*/*To first* pockets misbehave; prefer **Dimension** or **Through all** depth for features you intend to pattern. Mixed additive+subtractive features in one pattern are order-sensitive — reorder them in the list.

## Text, labels, and embossed / engraved markings

- **Matches:** raised or recessed lettering, part numbers, logos, branding, knob graduations / dial markings, icons on a face — a very common 3D-print request.
- **Lead workbench:** Draft (to generate the text outline) → Part Design (to give it depth); curved faces additionally use Part **Projection on surface**.
- **Feature sequence (flat face):**
  - **Draft → Shape from text (ShapeString)** to generate the lettering as wire outlines (pick the font), placed on the target face's working plane.
  - **Draft to Sketch** to convert those outlines into a sketch the solid tools accept.
  - **Pad** the text for **raised** lettering, or **Pocket** it for **recessed / engraved** lettering, on the chosen face.
- **Feature sequence (curved face):** generate the ShapeString, use Part **Projection on surface** to wrap the outline onto the curved face, then **Pad / Pocket** from the projected wires.
- **Model intent:** keep text as its own late feature so it never complicates the main solid; anchor the ShapeString to the face/datum it sits on so it tracks if the part is resized.
- **Pitfalls:** for FDM, **prefer raised text to engraved** — fine recessed strokes can fall below one extrusion width and vanish. Thin/serif fonts print poorly; favour bold, simple letterforms. The counters of letters (the holes in A, O, R) must stay as separate closed contours so they pad/pocket correctly.

## Parts designed to fit together (clearance / press-fit / snap-fit)

- **Matches:** mating parts — pegs in holes, lids on boxes, shafts in bushings, press-fit bearings/pins, snap-fit clips/latches, sliding rails, threaded interfaces. This is an *approach* concern layered onto the other archetypes.
- **Lead workbench:** Part Design (model each mating part as its own Body).
- **Approach (not a single feature list):**
  - **Decide the fit class up front:** *clearance* (free slip — lids, removable pins), *transition/press* (interference — bearings, dowels, structural pins), or *snap* (elastic engagement — clips, hooks, living hinges).
  - **Drive both halves from shared intent:** capture the nominal mating dimension once (a datum plane/axis, or matching sketch constraints) and apply the gap/overlap as a deliberate offset on one part. The hole and the peg should *not* both be drawn at the same nominal size.
  - **Build the gap parametrically:** clearance = the female feature slightly larger / male slightly smaller via the sketch dimension or an Attachment Offset; press = the male feature slightly larger than the female so the print interferes; snap = model the hook/undercut and the catching recess as separate Pads/Pockets with a small engaging overhang.
  - **Use Hole features for round mating bores** so clearance/thread classes are explicit; add a **Chamfer** lead-in on bores and pin ends to ease assembly and printing.
  - **Snap-fit specifics:** Pad the cantilever beam, Pocket the gap that lets it flex, and Pad the catching lip; orient the flexing beam so FDM layer lines run *across* the bend, not along it.
- **Model intent:** fit is a relationship, expressed as an intentional offset between two solids — never coincident geometry. Keep mating parts as separate Bodies/files so each prints and tolerances independently. Tune the offset for the printer/material (FDM and resin behave differently); making it a single parameter lets the user dial it in after a test print.
- **Pitfalls:** modeling male and female at identical nominal size yields a part that won't go together (or won't hold). Sharp-edged pins/bores without lead-in chamfers jam. Snap features printed with the flex axis along layer lines snap off. Threads modeled as real geometry (Hole *Model Thread*) are heavy and slow — add them near the very end, or rely on the thread's clearance sizing and skip modeled threads for FDM.

## Assembly of several printed parts (Assembly workbench + joints)

- **Matches:** a product made of multiple parts that move or bolt together — hinged lids, linkages, mechanisms, a base + bracket + cover stack, anything you want to verify fits/moves before printing.
- **Lead workbench:** Assembly (with each part authored separately in Part Design).
- **Feature sequence:**
  - **Model each part as its own Body** (its own file, or all in one document), each a clean single solid.
  - **Create Assembly** to add an assembly container to the document.
  - **Insert Component** (or drag the Bodies/Parts into the assembly) so the solver can move them.
  - **Toggle Grounded** on the base/reference part to lock it to the assembly origin — a normally-inserted part is *not* grounded automatically, so ground exactly one part yourself.
  - **Apply joints** between geometric elements of two different parts, choosing by intended motion: **Fixed** (bolted/bonded, no motion), **Revolute** (hinge, one rotation axis), **Slider** (linear only), **Cylindrical** (rotate + slide on one axis), **Ball** (pivot), plus **Distance/Parallel/Perpendicular/Angle** constraints and coupling joints (**Screw/RackPinion/Gears/Belt**) for mechanisms.
  - **Solve Assembly**, drag parts to confirm the motion, then add limits (min/max angle or length) where travel is bounded; optionally **Create Exploded View** or a **Bill of Materials**.
- **Model intent:** the Assembly workbench arranges and constrains finished parts — it is *not* for modeling the parts themselves (that's Part Design, one Body each). Select clean reference elements (a circular edge, a flat face, an axis) for each joint so the solver places parts predictably. Ground exactly one part as the datum; every other part's pose comes from its joint chain.
- **Pitfalls:** don't try to model the individual parts inside the assembly. Over-constraining (redundant joints) confuses the solver — pick the least-constraining joint that captures the real motion (e.g. Cylindrical instead of Slider when one rotation should stay free). If a joint won't solve, drag the parts near their solved pose first to help the solver.

## When the idea is organic / freeform — step outside Part Design

- **Matches:** sculpted, flowing, or aesthetic surfaces — toy/figurine bodies, ergonomic shells, blended compound curves, boat-hull-like forms, anything where "smooth surface" matters more than "parametric prism."
- **Lead workbench:** Surface (NURBS faces from boundary curves), with sketches on Part Design datum planes. *(Optional: the third-party **Curves** addon — installed via the Addon Manager, not part of a stock FreeCAD 1.1 install — adds advanced spline/surface tools; only route the user there if they already have it.)*
- **Approach:**
  - **Sketch boundary curves** (splines) on a set of **PartDesign datum planes** positioned in space to cage the form; keep them parametric so the shape stays editable.
  - **Surface Filling / Sections / Fill boundary curves** to skin those edges into NURBS faces; **Blend Curve** and **Extend face** to control continuity and stitch patches together.
  - Assemble the patches into a closed **shell**, then a **solid**, using Part **Shape builder** (the Surface result can't live inside a Part Design Body — keep surface, Body of datums/sketches, and the solid together inside a **Std Part** container).
  - Return to Part Design only for the *manufacturable* features (mounting holes, bosses, flat mating faces) added to the resulting solid via a Base Feature.
- **Model intent:** Part Design excels at prismatic/revolved/swept engineering geometry; freeform aesthetic surfaces are Surface (or the optional Curves addon) territory. Most real freeform parts are a smooth Surface body for the look plus Part Design features for the function — combine them, don't force everything into one workbench.
- **Pitfalls:** Surface output won't insert into a Part Design Body (use Std Part to group them). Freeform NURBS detail meshes heavily for STL export — check the mesh deviation when slicing. Don't reach for Surface when a Loft or Pipe in Part Design already gives the needed shape; it's more work and less parametric.

## Quick selector

- Flat outline + holes → **Prismatic / mounting plate / bracket** (Pad → Pocket/Hole → Pattern → Fillet)
- Hollow housing with lid → **Enclosure** (Pad → Fillet → Thickness → lip Pad → Holes)
- Spun about an axis → **Revolved** (Revolution → Groove → Hole → PolarPattern)
- Section dragged along a route → **Swept** (Additive Pipe along a spine)
- Morph between two profiles → **Lofted transition** (Additive Loft across datum-plane sketches)
- Needs stiffening → **Ribbed/gusseted** (Pad ribs between existing faces → Mirror/Fillet)
- Easiest as combined primitives → **Multi-body Boolean** (per-Body solids → PartDesign Boolean)
- Repeated feature → **Patterned** (build one → Linear/Polar/Mirror/MultiTransform)
- Text/logo/markings on a face → **Text/markings** (ShapeString → Draft to Sketch → Pad raised / Pocket engraved)
- Two parts must mate → **Fit** (deliberate clearance/interference offset, separate Bodies)
- Several parts move/bolt together → **Assembly** (Part Design parts → Assembly joints)
- Smooth/sculpted form → **Surface** (optionally the Curves addon), then back to Part Design for functional features

## Sources

- Basic_Part_Design_Tutorial.md
- PartDesign_Workbench.md
- PartDesign_Examples.md
- PartDesign_Body.md
- PartDesign_Pad.md
- PartDesign_Pocket.md
- PartDesign_Hole.md
- PartDesign_Revolution.md
- PartDesign_Groove.md
- PartDesign_AdditivePipe.md
- PartDesign_SubtractivePipe.md
- PartDesign_AdditiveLoft.md
- PartDesign_SubtractiveLoft.md
- PartDesign_Thickness.md
- PartDesign_Draft.md
- PartDesign_Fillet.md
- PartDesign_Chamfer.md
- PartDesign_LinearPattern.md
- PartDesign_PolarPattern.md
- PartDesign_Mirrored.md
- PartDesign_MultiTransform.md
- PartDesign_Boolean.md
- Manual_Traditional_modeling,_the_CSG_way.md
- PartDesign_Plane.md
- Assembly_Workbench.md
- Assembly_CreateJointFixed.md
- Assembly_CreateJointRevolute.md
- Manual_Preparing_models_for_3D_printing.md
- PartDesign_Bearingholder_Tutorial_I.md
- Surface_Workbench.md
- Draft_ShapeString.md
- Draft_Draft2Sketch.md
- Part_ProjectionOnSurface.md
