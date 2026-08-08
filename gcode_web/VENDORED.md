# Vendored: the dimensioner G-code viewer

This folder is a vendored copy of the **viewer half** of the `dimensioner`
project. It is the Vite source for `freecad/freecadclaude/gcode_ui/`, which is
committed build output: users install FreeCADClaude from the `main` branch as a
plain file copy, with no Node toolchain to build it with.

| | |
|---|---|
| Upstream | <https://github.com/mrgeoffrich/dimensioner> |
| Commit | `e2b9be15c08b16f33bfc3299bf6e7e688c7e42da` |
| Upstream commit date | 2026-06-26 |
| Vendored on | 2026-08-08 |
| Licence | same project, same author as this addon |

Nobody reads a 1.1 MB minified diff, so this file is the reviewable artifact:
the upstream commit above plus every local patch below is the whole delta
between what the author wrote and what this repo ships.

## Rebuilding

```
cd gcode_web && npm ci && npm run build
```

writes `../freecad/freecadclaude/gcode_ui/`. Commit that output in the same
commit as any change here. The build is deterministic — fixed asset names, no
content hashes, no code splitting — so a rebuild that changes nothing produces
no diff at all. Verify with two consecutive builds and
`git diff --exit-code freecad/freecadclaude/gcode_ui`.

## What came across

`src/`, `index.html`, `public/` (minus `samples/`), `tests/`, `package.json`,
`package-lock.json`, `tsconfig.json`, `tsconfig.app.json`, `tsconfig.node.json`,
`eslint.config.js`. The lockfile is not optional: the rebuild command above is
`npm ci`, which refuses to run without one.

## What was deliberately left behind

| Left behind | Why |
|---|---|
| `pipeline/` | The Python correction model. Bringing it means numpy, scipy, shapely, trimesh and rtree inside FreeCAD's bundled interpreter, and the model is only meaningful with a calibration profile fitted from physical measurements. Out of scope — see fork 4 in `docs/slice-preview-design.md`. |
| `calibration/` | Fixtures and notebooks for that pipeline. |
| `measurement-tooling.md`, `spec.md`, `README.md`, `CLAUDE.md` | Upstream's own docs, describing the whole project including the half that did not come. This file replaces them for our purposes. |
| `base.gcode.3mf`, `print-1-frame-2.stl` (repo root) | Sample print data, 1.6 MB, unused by the viewer build. |
| `public/samples/` | 4.1 MB (`base.gcode.3mf` 1.28 MB, `base.mesh.json.gz` 2.03 MB) backing the "Load sample" button. The addon always has real G-code, so the samples buy nothing and cost four times the built bundle. |
| `vite.config.ts` | Replaced — see the patches below. |
| `.gitignore` | This repo's root `.gitignore` covers `gcode_web/node_modules/` and `gcode_web/dist/`, so the ignore rules stay in one place. |

The pipeline's **viewer-side** overlay components (`PredictionOverlay`,
`MeshOverlay`, `StlOverlay`, `PredictionTopHighlight`, `deviationColor.ts`, the
prediction and mesh workers) **did** come across and are simply never fed. Do
not delete them: a later phase only has to produce `*.geojson[.gz]` and
`*.mesh.json[.gz]` and hand them to `view_gcode` for the overlays to light up.

## Local patches

Every difference from the upstream commit, one line each.

1. **`vite.config.ts` replaced wholesale.** Upstream's is a four-line dev config
   whose build emits content-hashed names into `dist/`. Ours sets
   `base: "./"`, `outDir: "../freecad/freecadclaude/gcode_ui"`, `emptyOutDir`,
   `target: "es2022"`, `assetsInlineLimit: 1 MB`, `cssCodeSplit: false`,
   `modulePreload: {polyfill: false}`, `inlineDynamicImports: true` and fixed
   `entry/chunk/assetFileNames`, plus a `worker` block pinning the three Web
   Workers to `assets/[name].js`. That is the committed-output contract: a
   rebuild that changes nothing must produce no diff. The `test` block is
   carried over from upstream unchanged.
2. **`public/samples/` deleted** (see the table above).
3. **"Load sample" removed** — the button 404s once the samples are gone, and a
   button that errors is worse than no button:
   - `src/hooks/useGcodeFile.ts`: dropped the four `SAMPLE_*` constants and the
     `loadSample` callback; the hook now returns `{ loadFile }` alone.
   - `src/App.tsx`: destructures `loadFile` only, and drops the `onSample` props.
   - `src/components/FileDrop.tsx`: dropped the `onSample` prop and the
     "Load sample" toolbar button.
   - `src/components/StatusOverlay.tsx`: dropped the `onSample` prop and the
     "Load the sample print" button; the idle card's subtitle now ends
     "drag it anywhere, or use Open file…" instead of trailing into the removed
     button.
4. **`tests/freecad-roundtrip.test.ts` added.** The parser guard: parses a real
   recorded `plate_1.gcode` from this addon's own round trip and asserts no
   unknown `; FEATURE:` strings. See below.
5. **`tests/fixtures/plate_two_boxes.gcode.gz` added.** That test's fixture,
   moved here from `eval/fixtures/` so it sits beside the test it guards.

Nothing else differs. `index.html`, `package.json`, `package-lock.json`, the
three `tsconfig*.json` and `eslint.config.js` are byte-identical to upstream, as
is every `src/` file not listed above.

### Not patched, on purpose

- **`tests/sample.smoke.test.ts` kept as is.** It already
  `describe.skipIf`s on `public/samples/base.gcode.3mf` being absent, and with
  the samples dropped it skips cleanly (`1 skipped`) rather than failing. Keeping
  it costs one skipped line and keeps the divergence from upstream smaller; drop
  a sample back into `public/samples/` and it runs again.
- **The page title is still "Dimensioner — G-code Viewer"**, and the toolbar
  still reads "Dimensioner · G-code viewer". Honest attribution, and one less
  patch to carry.
- **Upstream's two `react-hooks/immutability` lint errors and six unused
  `eslint-disable` warnings are pre-existing.** `npm run lint` reports the same
  8 problems here as it does on a clean upstream checkout, in
  `src/components/StlOverlay.tsx`, `src/components/Viewer.tsx` and
  `tests/sample.smoke.test.ts` — none of them files we patched. Left alone.

## The parser guard

`tests/freecad-roundtrip.test.ts` is a permanent contract, not a smoke test. It
guards the seam most likely to rot: an unmapped `; FEATURE:` string does not
fail anything at runtime, it buckets as `unknown` and draws magenta, and nobody
notices for months. So a Bambu Studio release that renames or adds a feature
string has to fail here instead, and the fix is a new entry in `FEATURE_ALIASES`
in `src/featureColors.ts`.

The fixture is genuine recorded output — Bambu Studio 02.08.01.55 slicing a 3MF
this addon exported from FreeCAD, two boxes on one plate, 40 layers, 6117
segments. It is gzipped (32 KB, 208 KB raw) purely to keep the repo small; the
test gunzips it in `node:zlib`. Its ten feature strings are Bottom surface,
Bridge, Custom, Floating vertical shell, Inner wall, Internal solid infill,
Outer wall, Skirt, Sparse infill and Top surface — Custom and Skirt both map to
`custom`, so nine feature types.

Run it with `cd gcode_web && npx vitest run`. No FreeCAD, no slicer, no browser.
