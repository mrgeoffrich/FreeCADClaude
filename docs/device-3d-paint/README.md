# Device 3D paint — parked

Send a part from the live document to a tablet as a plain grey solid, let the
user paint on its actual surface with a stylus, send it back, and have FreeCAD
re-render the paint from angles chosen to show it.

**Status: parked 2026-08-08, after Phase 0.** Design done, spikes run, no code
written. Nothing in the addon depends on any of this.

| File | What it is |
|---|---|
| `design.md` | The design. Opens with the five claims the spikes falsified. |
| `spikes.md` | Phase 0 measurements — T1, U1, U2, S3 — and three findings that belong to no single spike. |

## Where it got to

The four spikes that gate Phase 1 all ran, and none of them killed the design:

- **T1 passed**, which was the one that mattered. `SoTexture2` renders through
  the forced-FBO save path and alpha-blends against the geometry behind it, so
  the textured render-back is real and the per-face-tint fallback stays a
  degradation path. One GL stack only — macOS on Apple silicon. Windows and
  Linux are unverified.
- **U1** showed the geometric unwrap works at 2.49% fill-only, but only with two
  additions the design lacks (a toroid path, and splitting seam-crossing
  triangles). As written it is 23.3%.
- **U2** settled segment ordering: 894/894 on `isSame`, and an argmin over every
  face picked its own index 79/79.
- **S3** confirmed the silent-offset risk is real. `mesh.Topology` is
  object-local.

Still unrun: **D1** (the atlas paint pass, readback and pick stall on real
devices) needs Phase 1 to exist, and **U4** (round-trip texture size) needs a
painting client. **U3** was conditional on U1 failing, and U1 did not fail.

## Picking it up

Phase 1 is unblocked. Before writing `mesh_export.py`, fold the five corrections
at the top of `design.md` into the algorithm — they are algorithm changes, not
notes, and the seam-splitting one changes the de-share step from one level to
two.

Two measurements were reproduced independently of the spike run, on the same
document: `SoleFillet.Shape.isValid()` is `False` with 19 of 81 faces invalid,
and the bounding-box diagonal moves −1.032% under tessellation while
`hashCode()` stays identical. **The design's measurement table therefore comes
from a part that does not pass OCCT validation.** The ratios are believable but
the specimen is not clean; re-base the table on a valid part before quoting it
anywhere that matters.

## The one finding that outlives this project

`diagnostics._shape_metrics` caches bbox keyed on `(object name,
Shape.hashCode())`, and both its docstring and `CLAUDE.md` justify that on the
grounds the metrics are a pure function of the shape. **For bbox that is false**:
tessellating a shape shrinks its bounding box about 1% and leaves the hash
unchanged, so a cached bbox can outlive the value it describes.

It does not bite today. `TechDraw.projectToSVG` — the only call in the shipped
tool set that could trigger tessellation, used by `view_sketch_svg` for 3D
projection — leaves the bounding box untouched, verified on the same document.
So this is latent, and Phase 1 is what makes it real, because Phase 1 tessellates
routinely.

## Re-running the measurements

Scripts were throwaway and are not kept; the numbers and methods are in
`spikes.md`. Two things cost time and are worth knowing before you write another:

- **`freecadcmd` takes the script as its only argument.** A second path makes it
  try to open both as project files, and the script silently does not run. Pass
  data in through environment variables. Absolute paths only — a relative one
  runs nothing and still exits 0.
- **Nothing renders under `QT_QPA_PLATFORM=offscreen` on macOS.** Qt's offscreen
  plugin has no GL context at all, so this is not specific to textures — no
  capture of any kind can be tested that way. `freecadcmd` plus
  `FreeCADGui.showMainWindow()` on the default cocoa platform does get a real
  context and runs unattended, which is how T1 was measured. Expect a SIGSEGV
  during interpreter teardown after the output is written, so flush as you go.
