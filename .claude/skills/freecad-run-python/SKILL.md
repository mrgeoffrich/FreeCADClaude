---
name: freecad-run-python
description: >-
  Write and debug the actual FreeCAD 1.1 Python code for the run_python tool
  -- Sketcher geometry/constraints, PartDesign features (Body, Pad, Pocket,
  Revolution, Loft, Hole, patterns, Fillet/Chamfer, Thickness), Part workbench
  booleans, and Draft -- to build or modify the live document in this addon.
  Use whenever
  the user wants something actually built/implemented/scripted in FreeCAD
  (not just planned), wants a run_python call written or fixed, is debugging
  a run_python traceback or an Invalid/Error feature, is continuing from a
  freecad-design-advisor plan into execution, or asks a "how do I script X /
  what's the Python API for Y" question about Sketcher/PartDesign/Part/Draft.
  Do NOT use for approach/workflow advice with no code involved (use
  freecad-design-advisor instead) or for generic FreeCAD addon/macro
  authoring unrelated to this addon's run_python tool (custom workbenches,
  GuiCommands, Coin3D, persisted macro files) -- this skill is scoped to
  scripting the live document through run_python specifically.
---

# FreeCAD run_python Scripting

You write the **actual Python** that goes into this addon's `run_python`
tool call — real dimensions, real `Sketcher`/`PartDesign`/`Part`/`Draft` API
calls, ready to execute against the user's live document. Where
`freecad-design-advisor` stops at named features and approach,
this skill is the **build** step: turn a plan (or a direct request) into
working code, run it, verify it, and recover from errors.

Scope: scripting through this addon's `run_python` tool against an
already-open document. **Not** general FreeCAD macro/addon authoring —
custom workbenches, `GuiCommand` registration, Coin3D scenegraph work, and
persisted macro files don't apply here (`references/execution-model.md`
explains why). Not GUI-workflow advice — if the user just wants to know
*how to approach* a design with no code yet, that's
`freecad-design-advisor`.

## How to script

1. **Read `references/execution-model.md` first if you haven't this
   session.** It governs everything below: the pre-bound namespace, the
   one-call-one-transaction contract, `inspect_api`, the Quantity gotcha, and
   what's out of scope. Every example in the other reference files assumes
   it.
2. **If there's no plan yet**, get one. For "how should I build X" questions
   with no concrete dimensions, that's `freecad-design-advisor`'s job, not
   this skill's — hand off rather than improvising an approach here. If the
   user already has dimensions and just wants it built, proceed directly.
3. **Look up what you're unsure of before writing code.** Pull the relevant
   reference file(s) below for the feature family involved, and call
   `inspect_api` for anything those don't cover or that this FreeCAD install
   might differ on. Don't guess a property name and hope.
4. **Size each `run_python` call to one coherent step**, not the whole part —
   a call is the unit of rollback (`execution-model.md`). Roughly: container
   setup → base feature → each additive/subtractive feature or small group →
   patterns → dress-ups last, mirroring the model-intent order
   `freecad-design-advisor` already advises.
5. **Verify after each step** with `get_objects`, `get_diagnostics`, or
   `view_sketch_svg` rather than chaining several blind steps — a clean
   commit doesn't mean the feature recomputed cleanly.
6. **On failure, read the traceback, fix, and resend** — the failed call
   already rolled back cleanly, so there's no cleanup needed before retrying.

## Reference files — read the ones you need

- **`references/execution-model.md`** — the harness contract: pre-bound
  names, transaction/rollback, `inspect_api`, the verification loop,
  Quantity/units, GUI-thread constraints, what's out of scope. **Read this
  first, always.**
- **`references/sketcher-scripting.md`** — scripting sketch geometry and
  constraints: `addGeometry`, `addConstraint` forms (with the point-position
  addressing scheme), attachment to planes, closed-profile recipes
  (rectangle/polygon/slot), checking constraint/closure state from code.
- **`references/partdesign-scripting.md`** — scripting a Body's feature
  tree: `body.newObject(...)` for Pad/Pocket/Revolution/Groove/Loft/Pipe/Hole,
  Tip management, datum scripting, patterns (Linear/Polar/Mirrored/
  MultiTransform), PartDesign Boolean, Fillet/Chamfer/Thickness edge/face
  selection. The densest file — most builds live here.
- **`references/part-draft-recipes.md`** — Part workbench primitives/
  booleans (raw shapes vs parametric objects), Placement-based positioning
  for multi-body layouts, Draft arrays, export-via-script, and short
  archetype-to-code skeletons translating `freecad-design-advisor`'s named
  recipes (prismatic plate, enclosure, revolved, multi-body boolean,
  patterned) into a first script.

## Shape of a good build

- **Confirm the plan** (dimensions, which parts/features) before writing
  code if it isn't already pinned down — don't silently invent numbers.
- **One coherent step per `run_python` call**, in model-intent order (base
  sketch/feature → additive/subtractive → patterns → dress-ups last).
- **Look up, don't guess** — reference file or `inspect_api` before any
  unfamiliar property/signature.
- **Verify between steps** — `get_objects`/`get_diagnostics`/
  `view_sketch_svg`, not five steps stacked blind.
- **Track multi-step builds as tasks** so progress is visible, same as any
  multi-step build the user is following along with.

## Principles that hold across every script

- **A call is the unit of rollback** — size it to one coherent step so a
  failure doesn't take already-good work down with it.
- **Look before you guess.** `inspect_api` exists because FreeCAD's C++-backed
  methods don't always show a usable signature any other way.
- **A clean commit isn't a healthy feature** — PartDesign/Sketcher failures
  can mark an object Invalid without raising. Check `get_objects`/
  `get_diagnostics`, don't assume.
- **Same model-intent discipline as the GUI**: sketch on stable references,
  dress-ups (fillet/chamfer) last, one Body per physical part — the
  topological-naming pitfall doesn't go away just because it's scripted.
- **Nothing here persists outside the document.** No workbenches, no
  toolbar commands, no macro files — if the user wants one of those, say so
  rather than forcing it through `run_python`.
