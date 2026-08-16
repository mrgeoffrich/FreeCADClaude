# Phase 1 spike — face-markup: prove `brep_faces` ↔ `Shape.Faces` order correlation

This is Phase 1 of `docs/face-markup-plan.md`: prove, empirically, that
[`occt-import-js`](https://github.com/kovacsv/occt-import-js) (a WASM build of
OpenCascade, pinned at npm `0.0.23`) reads a BRep file and reports face
ordinals in the **same order** FreeCAD's own `Shape.Faces` does — not just a
matchable order under some permutation, the same order, ordinal `i` in both.

If that holds, a browser pick resolved against `brep_faces` maps to
`"FaceN"` **by construction**, with no translation table that can drift —
which is the claim Phase 3/4 of the plan build on.

## What was run

Three steps, all reproducible from this directory:

1. **FreeCAD side** (`freecad/export_faces.py`, run under `freecadcmd`):
   builds the two test shapes, exports each via `obj.Shape.exportBrep()` to
   `shapes/<name>.brp`, and writes `out/<name>.freecad.faces.json` with, for
   every face of `obj.Shape.Faces` **in order** (0-based index = the order
   `Shape.Faces` returns them): the exact centroid (`face.CenterOfMass`), a
   normal at a representative parameter (midpoint of the face's UV parameter
   range), and the area (`face.Area`).
2. **occt-import-js side** (`node/read_brp.js`, run under Node): reads each
   `.brp` with the library's documented Node entry point (`ReadBrepFile`),
   and writes `out/<name>.occt-import-js.json` with, for each element of the
   returned `brep_faces` array **in array order** (index 0, 1, 2, …), an
   approximate centroid computed by averaging the triangle vertex positions
   of that face's triangle range (`brep_faces[i] = {first, last}` is an
   inclusive range into the mesh's triangle index buffer). The script asserts
   the ranges cover the mesh's triangle count exactly.
3. **Comparison** (`compare.py`, stdlib only): for each shape, for every
   index `i`, finds which `brep_faces[j]` centroid is **nearest** to
   `Shape.Faces[i]`'s centroid. `exact_order_match` is true only when the
   recovered nearest-match assignment is literally the identity permutation
   for every face; any non-identity nearest match is a listed mismatch, not
   an average. Exit code 0 iff `overall_correlation_holds`.

### Test shapes

| Shape | Construction | Faces |
|---|---|---|
| `box_with_hole` | 80×50×30 box minus a central r=10 cylinder (boolean cut, through-hole) | 7 (4 box sides, 2 annular end faces, 1 cylinder side) |
| `filleted_box` | 60×40×20 box, `makeFillet(6, all 12 edges)` | 26 (6 original faces + 12 edge fillets + 8 spherical corner patches where three fillets meet) |

The filleted box is the interesting one: 8 of its 26 faces are identical
quarter-spheres (area π·6²/2 each) — nearly symmetric geometry, which is
exactly where an ordering that is merely "matchable" would need a permutation
to line up. It lines up without one.

## Environment

- **FreeCAD: real, via `freecadcmd`.** The workspace's host image is Alpine
  Linux (aarch64), which has no FreeCAD package in any repo (checked `apk`
  main/community, and the 3.21/edge testing repos) and no `apt-get`. To get a
  real FreeCAD rather than a substitute, a Debian bookworm (arm64) container
  was used: `apt-get install -y freecad` inside it provides
  `/usr/bin/freecadcmd` — FreeCAD **0.20.2** (Debian's build), linked against
  OCCT **7.6.3** (`libocct-7.6` packages). All geometry calls used here
  (`exportBrep`, `Shape.Faces`, `CenterOfMass`, `Area`) are pure OCCT
  topology/geometry reads; nothing rendered. Note the version is older than
  the plan's 1.1.1 — the face-ordering claim rests on
  `TopExp_Explorer(TopAbs_FACE)`, which is the same traversal in both, but
  Phase 4's promoted Vitest test should re-run against the addon's real
  FreeCAD before the correlation is treated as settled for good.
- **occt-import-js:** `0.0.23`, pinned exactly (`npm install
  occt-import-js@0.0.23 --save-exact`; see `node/package.json` +
  `node/package-lock.json`).
- **Node:** v24.19.0, npm 11.17.0.
- freecadcmd swallows script stdout in headless mode, so the FreeCAD script
  logs to `out/freecad_run.log` (and stderr), and it is *imported* rather
  than exec'd as `__main__` — both quirks are noted in the script.

## Result

```
shape: box_with_hole   FreeCAD faces=7   occt-import-js faces=7
  face 0..6: brep_faces[i] nearest to Shape.Faces[i] (d=0.000000 mm)
  exact_order_match: True

shape: filleted_box   FreeCAD faces=26   occt-import-js faces=26
  every face i: brep_faces[i] nearest to Shape.Faces[i]
  nearest-match distance on the identity assignment (max): 1.006253 mm
  exact_order_match: True

overall_correlation_holds: True
```

Full per-face transcript: `out/comparison.txt` (regenerate with
`python3 compare.py > out/comparison.txt`).

Distances are 0.000000 mm on the planar faces (a flat face tessellates
exactly) and up to ~1.01 mm on the curved corner patches — that is the
triangle-mesh approximation of the centroid, not an ordering error: the next
nearest candidate face to any corner patch sits ≥ 14 mm away, so the margin
is comfortable. The verdict does not depend on any distance threshold: it is
purely "is `brep_faces[i]` nearest to `Shape.Faces[i]`, for every `i`".

## What this means for the plan

The plan's load-bearing claim — "`brep_faces` is in the same
`TopExp_Explorer(TopAbs_FACE)` order FreeCAD's own `Shape.Faces` uses, so a
browser pick resolves to `"FaceN"` by construction" — holds on both test
shapes, in the strict identity-permutation sense. Phase 4 promotes this
comparison into the Vitest correlation test; this directory is throwaway and
expected to be deleted then.

## Reproduce

```sh
# 1. FreeCAD side (any machine with freecadcmd; outputs are committed, so
#    this is optional to re-run):
freecadcmd <abs path>/freecad/export_faces.py

# 2. Node side (needs node_modules; npm ci with the pinned lockfile):
cd node && npm ci && node read_brp.js

# 3. Comparison (stdlib only):
python3 compare.py
```
