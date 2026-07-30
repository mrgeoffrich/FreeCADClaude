---
name: freecad-lofi-sketch
description: >-
  Sketches a low-fidelity concept for a design idea as a single SVG "sheet" --
  a graph-paper grid with three orthographic view panels (Top/Front/Left) --
  before any dimensions, workbench choices, or code are decided. Runs BEFORE
  freecad-design-advisor in the design arc: draft and iterate on rough
  proportions and feature layout visually, then hand off to
  freecad-design-advisor for the workbench/feature plan. Touches no FreeCAD
  document -- it only writes a plain SVG file. Not for workbench/feature
  workflow (see freecad-design-advisor) or for the actual run_python code.
  EXPLICIT INVOCATION ONLY -- invoke only when a message directly asks for
  this skill by name (the /lofi-sketch slash command in the FreeCAD chat
  panel, or "use the lofi-sketch skill"). A new part or object idea is not, by
  itself, a reason to invoke it.
---

# FreeCAD Low-Fidelity Concept Sketch

You help someone **rough out a design idea visually** before committing to
dimensions or a build plan. The deliverable is one hand-authored SVG "sheet":
a graph-paper grid carrying three orthographic view panels (Top, Front,
Left) with simple flat shapes standing in for the major masses and features.
No dimensions, no workbench names, no code -- just proportions and layout the
user can eyeball and redirect before anything gets concrete.

Scope: one SVG file, iterated in place, authored directly with the `Write`
tool. This is the **first** step of the design arc, ahead of
`freecad-design-advisor` (which turns a settled idea into a workbench/feature
workflow) and the `run_python` build itself (which turns the plan into code).
It never touches the live FreeCAD document -- there's nothing to undo, and no
`run_python` call is involved.

## How to sketch

1. **Check this conversation for a prior lo-fi sketch** (a file path and/or a
   summary of one already produced this session). If one exists, revise it --
   don't start cold.
2. **Read the idea for rough proportions and major features.** You need a
   width:depth:height feel (roughly boxy? long and thin? tall?) and 2-6
   features worth calling out (a lid, a lip, a boss, a slot, a handle...).
   Same "dominant geometry" read `freecad-design-advisor` does -- just drawn,
   and no numbers needed yet.
3. **Build the sheet** following `references/svg-template.md`: a graph-paper
   grid background, three panels (Top above Front, Left beside Front) sharing
   one coordinate system so proportions stay consistent across views, flat
   silhouette shapes per feature, text labels naming each feature. **Never a
   dimension number** -- this stays at the same approach-altitude the later
   skills hold to, just expressed visually instead of as a feature list.
4. **Write the file** with the `Write` tool. Use the absolute sketches folder
   given in the slash command's instruction text as the target directory, and
   a short kebab-case slug for the filename (e.g. `cable-tray.svg`). If no
   folder was given, ask rather than guessing at a path.
5. **Report back in one short paragraph**: what you drew and the file path.
   Don't paste the SVG source into chat, and don't try to open the file back
   with `Read` as a self-check -- see *Limitation* below.
6. **Iterate on feedback** by rewriting the *same file* in full (same path).
   Only pick a new slug if the user wants a genuinely different alternative
   concept alongside the first, not a revision of it.
7. **Once the layout and proportions feel right, hand off.** Tell the user to
   run `/design-advisor` next, in this same conversation, and restate the
   shape/features in one sentence so `freecad-design-advisor` has grounding
   even though it can't see the SVG itself.

## Reference files

- **`references/svg-template.md`** — the concrete template: the grid
  pattern, the panel-layout formulas that keep Top/Front/Left aligned, the
  style/label conventions, and a full worked example. Read this before
  drawing the first shape.

## Limitation: you can't see your own sketch

SVG is not one of the raster image types (png/jpeg/gif/webp) that can be
returned to you as a picture, so `Read` on your own sheet gives you back the
markup you just wrote, not the drawing. This is a **human-reviews-the-file
loop**: draw carefully from the template's formulas, describe what you drew in
chat, and let the user's eyes catch misalignment. Don't reach for
rasterization tooling to work around it -- that's out of scope here.

## Shape of a good sketch

- **One sheet, one coordinate system** — Top/Front/Left panels share axes
  with the Front panel so proportions can't drift apart the way they could
  across separate files.
- **2-6 features per view**, each a simple flat shape with a text label
  naming it — enough to convey layout, not a technical drawing.
- **No dimension numbers, ever.** Relative proportion (this boss is about a
  third of the plate width) is fine to reason about while placing shapes;
  showing a number on the sheet is not.
- **Revise in place.** A lo-fi sketch is meant to be redrawn quickly on
  feedback, not accumulated as a pile of near-duplicate files.

## Principles

- **Approach altitude, drawn instead of written.** Same discipline
  `freecad-design-advisor` holds to in text -- named shapes and relationships,
  not measurements -- applied to a picture instead of a feature list.
- **The document stays untouched.** This skill only ever writes a plain file
  with `Write`; if the user wants something built, that's
  `freecad-design-advisor` then `run_python`, not here.
- **Fast iteration over polish.** The sheet is disposable and cheap to
  redraw -- lean into quick revisions over trying to get it right in one
  pass.
