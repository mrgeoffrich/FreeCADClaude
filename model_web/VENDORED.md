# Vendored: occt-import-js (WASM OpenCascade)

This project's viewer does not ship meshes: `view_model_3d` (a later phase)
exports FreeCAD's native `.brp` BREP files and the browser tessellates them
itself, client-side, with a real WASM build of OpenCascade. That build is
`occt-import-js`, pinned here at exactly `0.0.23` — the version Phase 1 and
Phase 2 of the face-markup plan already verified (its `ReadBrepFile` is the
same `BRepMesh_IncrementalMesh` algorithm FreeCAD itself calls, and its
`brep_faces` triangle ranges correlate with `Shape.Faces` order, which the
later picking phase depends on).

| | |
|---|---|
| Upstream | <https://github.com/kovacsv/occt-import-js> |
| Version | `0.0.23` (exact pin, `--save-exact`; do not resolve "latest") |
| Licence | LGPL-2.1 |
| Package | `occt-import-js` on npm |
| WASM kernel | `occt-import-js.wasm`, 7,604,031 bytes, no `-pthread` flag |

It is an ordinary npm dependency, not a fork: nothing in this folder patches
its source. What is vendored in the repo is the *build output* — the WASM
kernel and glue land in `freecad/freecadclaude/model_ui/assets/`, committed
like `gcode_ui/` and `device_ui/`, because users install the addon from the
`main` branch as a plain file copy with no Node toolchain.

## How it is used here

`src/worker.ts` loads and drives it **inside a Web Worker**
(`new Worker(new URL("./worker.ts", import.meta.url), { type: "module" })`),
the library's own recommended pattern: parse + tessellate is genuine CPU
work and must not contend with orbiting the loaded model on the UI thread.
No COOP/COEP headers are needed — the build has no `-pthread` flag, so it is
plain single-threaded WASM.

- The glue is told where the kernel lives via `locateFile` (the `?url`-imported
  `assets/app.wasm`), and the kernel loads **once per tab**, not once per tool
  call — a ~7 MB download is exactly what the face-markup plan's invariant 4
  exists to avoid re-paying.
- The worker receives a `.brp` file's raw bytes (`ArrayBuffer`, transferred
  zero-copy), calls `ReadBrepFile(content, null)`, merges the returned meshes
  into one position/normal/index set (one mesh per FreeCAD *object*; the
  `brep_faces` triangle ranges are deliberately not carried back — face
  picking is a later phase), and posts fresh typed arrays back, transferred
  zero-copy.

The package ships no TypeScript types, so `src/occt-import-js.d.ts` declares
the subset this app uses, written against the shape documented in the
package's own README.

## Rebuilding

```
cd model_web && npm ci && npm run build
```

writes `../freecad/freecadclaude/model_ui/`. Commit that output in the same
commit as any change here. The build is deterministic — fixed asset names
(`index.html`, `assets/app.js`, `assets/app.css`, `assets/app.wasm`,
`assets/worker.js`), no content hashes, no code splitting — so a rebuild that
changes nothing produces no diff at all. Verify with two consecutive builds
and `git diff --exit-code freecad/freecadclaude/model_ui`.

## What is not automatically tested

The worker's real `ReadBrepFile` call and the WebGL render are a **manual
browser check**, deliberately not faked with mocks: a mock-heavy test of
either would prove nothing about the real WASM kernel or the real GPU path.
What `npx vitest run` does cover is the pure wire parsing around it (the
`/api/latest` and SSE payloads) — the same carve-out `gcode_web/VENDORED.md`
takes for its own `Viewer.tsx`. To check by hand: start the addon's model
server, open the page with a model published, and confirm the actual solid
appears and orbits.
