# Face markup — implementation plan

Send the real geometry of the active document to a browser — the native BREP,
not a converted mesh — so the user can mark exact faces of the actual solid
with a mouse, and get that back as data Claude can act on directly: an
`(ObjectName, "FaceN")` reference, not a pixel coordinate on a picture.

This is not a 3D version of `send_to_device`/`annotate_view`. Those exist so
Claude can *see* ink on a flattened screenshot. The whole point here is the
opposite: a mark must resolve to an exact FreeCAD subelement. The round trip
still returns **both** a rendered PNG (so Claude can visually confirm "yes,
that's the boss on the left") and the structured face references — see
Phase 5.

**Geometry format, settled:** `obj.Shape.exportBrep(path)` — confirmed in
`/Users/geoff/Repos/freecad/src/Mod/Part/App/TopoShape.pyi` — writes OCCT's
native BREP format, and it is not merely compatible with FreeCAD's own file
format, it *is* it: `PropertyTopoShape.cpp:389` (`SaveDocFile`) writes every
object into the `.FCStd` zip with the same writer. No mesh, no glTF, no lossy
intermediate. The browser tessellates it itself, client-side, with
[`occt-import-js`](https://github.com/kovacsv/occt-import-js) — a real WASM
build of actual OCCT (`BRepMesh_IncrementalMesh`, the same algorithm FreeCAD
calls server-side), pinned at the verified npm version `0.0.23`
(`occt-import-js.wasm` = 7,604,031 bytes, `occt-import-js.js` glue = 96,871
bytes). Each mesh it returns carries a `brep_faces` array of triangle-index
ranges, one per source BRep face, in the same `TopExp_Explorer(TopAbs_FACE)`
order FreeCAD's own `Shape.Faces` uses — so a browser pick resolves to
`"FaceN"` by construction, not via a translation table that can drift.

## Scope

**In, for v1:**

- A new tool pair: `view_model_3d`, `read_model_markup`.
- A new loopback (`127.0.0.1`) HTTP server, `model_server.py`, auto-starting
  like `view_gcode` — no button, no QR, no LAN exposure.
- A new web app, `model_web/` → committed `model_ui/`, vendoring
  `occt-import-js` plus the `@react-three/fiber`/`drei`/`three` stack already
  proven in this repo by `gcode_web`.
- Client-side face picking (mouse hover-highlight, click/shift-click) against
  the real tessellated BRep, with per-face notes.
- Face-identity validation on read-back: a shape-hash check so a mark is never
  handed to Claude as a confident `"FaceN"` if the document changed underneath
  it since export.

**Out, deliberately, for v1:**

- The phone/tablet LAN flow. Desktop only — see "Open decisions" below for
  why, and when that might change.
- Multi-face grouping as a first-class concept ("these three faces are one
  fillet region"). One mark = one face; the schema doesn't foreclose grouping
  later.
- Anything that edits the model from the browser. It marks; it does not model.
- `measured_mm`/`target_mm` — that pair exists for the 2D dimension tool
  because a screen distance is ambiguous between what it measures and what's
  wanted. A face pick has no such ambiguity; it names a face.

## The invariants

Same shape as every round trip this addon already has, restated for this one:

1. **`model_server.py` imports no FreeCAD and no Qt, not even indirectly.**
   Every path and the hash/centroid map are resolved on the GUI thread in
   `tools_model.py` and handed to the server as plain values — a `.brp` path
   plus a dict — exactly like `device_server.set_upload_dir` /
   `gcode_server._config`. No handler resolves a session folder, reads a
   preference, or touches a document.
2. **The export is non-mutating.** `exportBrep` is a pure read of `Shape`.
   Nothing in `view_model_3d` opens a transaction.
3. **Every FreeCAD name the browser sends back is a name FreeCAD gave it.**
   The browser only ever claims a `(object, face_ordinal)` pair; the server
   resolves that to `"FaceN"` and re-validates it against the live document
   (Phase 5). The browser never constructs a subelement name itself.
4. **The WASM kernel loads once per browser tab, not once per tool call.**
   `view_model_3d` publishes over SSE (the `device_server.py` pattern) rather
   than a one-shot query-string load (the `gcode_server.py` pattern) — a
   markup conversation plausibly involves several round trips, and re-paying
   a ~7 MB download each time would make the feature painful to use.

## Architecture

```
chat panel (GUI thread)
  └─ AgentWorker ─▶ claude CLI ─▶ MCP ─▶ gui_bridge ─▶ tools_model
                                                        │            ▲
                view_model_3d ─────────────────────────┘            │
                  exportBrep × N + hash/centroid map                │
                  → publish() over SSE                               │
                read_model_markup ────────────────────────────────────┘
                  resolve (object, face_ordinal) → "FaceN"
                  hash-check · highlight render · (text, png)
                                                                     ▼
      desktop browser  ◀── HTTP (loopback, token) ──▶  ThreadingHTTPServer
        occt-import-js (WASM, in a Worker)               GET  /api/latest
        + three.js viewer, raycast pick                  GET  /api/mesh/<id>.brp  ← NEW
                                                          POST /api/upload
                                                          GET  /api/events (unchanged shape)
```

Artifacts land in a new `<session>/models/` folder, pruned by
`_session_subdir` like `mobile`/`captures`/`exports`.

Ordering below is **mostly linear** — each phase depends on the one before it,
because this feature is genuinely one pipe end to end (export → view → pick →
read back), not several independent pieces. The one leaf is Phase 1: it blocks
nothing in the addon and can run as a throwaway script before any addon code
exists.

---

## Phase 1 — Spike: prove the correlation · size S

Gates everything downstream, and — unlike the rejected glTF approach's spike —
this one is automatable, not manual GLB inspection. Do it first, alone, before
writing any addon code.

- Export a known multi-face primitive (a box with a hole, a filleted box) via
  `freecadcmd`: `obj.Shape.exportBrep(path)`.
- Read it back with `occt-import-js` under Node (it has a documented Node
  entry point) via `ReadBrepFile`.
- Assert the returned `brep_faces` ordinals correlate to the same shape's
  `Shape.Faces` order — match by centroid, since that's a fact both sides can
  independently compute and compare.

**Done:** a script, kept as the seed of Phase 4's Vitest correlation test, that
proves `brep_faces[i]` and `Shape.Faces[i]` name the same face on a handful of
real shapes.

---

## Phase 2 — Foundations: export + server · size M

The plumbing everything else sits on. No viewer yet — this phase proves the
bytes can leave FreeCAD and be fetched over HTTP.

- `freecad/freecadclaude/freecad_tools/model_export.py` (new, sibling to
  `print_export.py`, but *smaller* — no meshing code at all): per requested
  object, `obj.Shape.exportBrep(path)` into `<session>/models/`, plus a plain
  dict of `{"shape_hash": obj.Shape.hashCode(), "faces": {i: {centroid,
  normal, area}}}` per object — the same `(name, hashCode())` cache-key idiom
  `diagnostics._shape_metrics` already uses.
- `freecad/freecadclaude/model_server.py` (new, stdlib-only, modeled on
  `device_server.py`'s SSE shape per invariant 4): `GET /api/latest`,
  `GET /api/mesh/<id>` (serves the `.brp` bytes), `POST /api/upload`,
  `GET /api/events`. Reuses `web_static.resolve()` for containment;
  `web_static.TYPES` already maps `.wasm` — add `.brp`/`.brep`.
- `Cache-Control` note: this pair of servers normally sends `no-store` on
  every response (their own JS/CSS has no content hash). The vendored
  `occt-import-js` WASM/JS in Phase 3 is the deliberate exception — it only
  changes on a reviewed version bump — so `model_server.py` should NOT apply
  `no-store` to `model_ui/`'s vendored assets. Flagged again in Phase 3;
  implement the exception here.
- Auto-start wiring in `chat_panel.py`, mirroring `view_gcode` (no button).

**Tests:** `eval/test_model_server.py`, stdlib-only under bare `python3`,
mirroring `test_device_server.py` — publish/fetch round trip, upload parsing,
token auth. `model_export.py` under `freecadcmd`: face count round-trips,
`shape_hash` changes after a mutation.

**Done:** a `.brp` file, exported from a live FreeCAD object, is fetchable
over `GET /api/mesh/<id>` with the right bytes and content type.

---

## Phase 3 — Viewer: load and orbit · size M

Get real geometry rendering in the browser. No picking yet — this phase is
"can the user see the actual solid," nothing more.

- `model_web/` — new Vite + TypeScript + Vitest project, `@react-three/fiber`
  + `@react-three/drei` (`OrbitControls`) + `three`, the exact stack already
  proven clean and deterministic in this repo by `gcode_web`.
- Vendor `occt-import-js@0.0.23` with the same discipline `gcode_web` vendors
  `dimensioner` — pinned version, a `VENDORED.md`-equivalent note recording
  which release and why. Its `.wasm`/`.js` land in the committed `model_ui/`
  build output.
- Load `.brp` bytes via `ReadBrepFile` inside a Worker (the library's own
  recommended pattern — no COOP/COEP headers needed, its build has no
  `-pthread` flag), one mesh per requested object, tagged with the FreeCAD
  object name.
- Render with `OrbitControls`; flat shaded, one light, grey material — no
  texture, no material display (matches the "the model is grey" simplicity of
  every other capture tool in this addon).
- `vite.config.ts`: `base: "./"`, `outDir: "../freecad/freecadclaude/model_ui"`,
  fixed asset names, no code splitting — matching `web/` and `gcode_web/`'s
  existing build config exactly.

**Tests:** `npx vitest run` for anything pure (asset/URL resolution); the
actual WebGL render is a manual browser check, the same carve-out
`gcode_web/VENDORED.md` already takes for `Viewer.tsx`.

**Done:** `view_model_3d` on a real object opens a browser tab showing the
actual solid, orbitable, at a size a WASM-illiterate user would recognize as
"the part."

---

## Phase 4 — Picking and the markup document · size L

Turn the viewer into something that produces data.

- `model_web/src/doc.ts` — `ModelMarkupDoc`: `version`, `source` (ties back to
  which export this was drawn on), `marks: FaceMark[]`, `caption`. Each
  `FaceMark`: `id`, `object`, `face_index` (the ordinal resolved from
  `brep_faces` — see below), `color | null`, `note`. Deliberately a new
  sibling to `web/src/doc.ts`, not a reuse of it — mixing flat 2D pixel
  coordinates and 3D face ordinals into one schema is exactly the conflation
  `web/src/doc.ts`'s inert `snapped_to: null` field was left there to avoid.
- Pick resolution: `raycaster.intersectObject(mesh)` → a triangle index → a
  search over that object's `brep_faces` ranges → a face ordinal. Entirely
  client-side; nothing is sent to FreeCAD until Send is pressed.
- Interaction: click a face to select it, type a note, mark it (see "Open
  decisions" — paint-a-color vs. click-and-annotate is not settled; build the
  simpler click-and-annotate first, since a color picker is additive to the
  same pick-resolution code either way).
- `POST /api/upload` with the flattened doc, reusing the multipart shape
  `device_server.py`'s upload route already established.

**Tests:** `npx vitest run` — the doc schema (parse/serialize round-trip,
mirroring `web/`'s `doc.ts` tests) and, promoting Phase 1's script into a real
test, the `brep_faces` ↔ `Shape.Faces` correlation check, run headless under
Node with no browser and no GPU.

**Done:** click a face in the browser, type a note, press Send — a
`ModelMarkupDoc` naming that exact face arrives at the server.

---

## Phase 5 — Tools: `view_model_3d` / `read_model_markup` · size M

Wire the round trip into what Claude actually calls, and close the
face-identity-rot gap.

- `freecad/freecadclaude/freecad_tools/tools_model.py` (new). `view_model_3d`:
  same `_objects_schema_prop` convention as every other capture tool, calls
  `model_export.py`, starts `model_server.py` if needed, publishes over SSE,
  opens the browser only if a tab isn't already open, returns immediately —
  the same "two halves, never block the GUI thread" shape as `send_to_device`.
- `read_model_markup`: reads the newest (or `index`-back) markup document.
  For each `FaceMark`, re-fetch the *live* object's `Shape.hashCode()`:
  - **Unchanged** → hand Claude `"Pad.Face7"` with full confidence.
  - **Changed** → do not claim the name is still valid. Report the recorded
    centroid/normal/area plus an explicit warning to re-locate the face
    before scripting against it — the same "confidently naming the wrong
    cause is worse than naming none" discipline `diagnostics.py`'s
    `_pre_existing_failure_note` already applies to the topological-naming
    gotcha. Check is per-object, matching `_shape_metrics`'s granularity.
  - Returns a `(text, png_path)` tuple like `capture_view` — the PNG is a
    fresh **server-side** offscreen render (`render._offscreen_shot`, not a
    client screenshot) with marked faces highlighted via
    `ViewObject.DiffuseColor` (confirmed live in FreeCAD 1.1 via
    `ViewProviderPartExtPyImp.cpp`), using the same save-then-mutate-then-
    restore discipline `render.py`'s `_shot_appearance` already uses for
    `Transparency`.
- Both registered in `freecad_tools/__init__.py`'s `TOOLS` registry — purely
  additive, per the existing convention.

**Tests:** `freecadcmd` fixtures for the hash-check path — export, mutate the
object, read back, assert the warning fires; assert it does *not* fire when
nothing changed.

**Done:** ask Claude to look at what was marked — it gets the picture, the
exact face names it can hand to `run_python`, and an honest warning on the
(rare, but real) occasions the model moved out from under the mark.

---

## Phase 6 — Hardening and docs · size S–M

Depends on everything.

- "New" clears `model_server.py`'s session state (mirroring
  `device_server.reset_session`/`slicer_runner.reset_session`) so one chat's
  exports don't leak into the next.
- Error paths as messages, not tracebacks: server not running, no active
  document, a requested object that doesn't exist, a face pick on a mesh the
  server has since pruned.
- `CLAUDE.md`: a new section in the voice of "Device annotation"/"Slice
  preview" — including *why* BREP was chosen over a server-baked mesh (the
  payload-size table and the WASM-cost tradeoff are exactly the kind of "why,"
  not "what," this repo's docs consistently keep) — plus the module-map rows
  for `model_server.py`, `model_export.py`, `tools_model.py`, `model_web/` →
  `model_ui/`.
- `RELEASE.md`: `model_web/` joins `web/`/`gcode_web/` in the "rebuild and
  commit alongside any source change" list.
- `deploy.ps1`/`deploy.sh`: add `model_web` to the exclude lists (dev-time
  only, same as `web/`/`gcode_web/`).

---

## Open decisions

Not blocking Phase 1–2, but worth settling before Phase 3–4 lock in a shape:

1. **Transport.** Loopback desktop (this plan) vs. extending the LAN
   phone/tablet flow. Client-side BRep tessellation is genuine CPU work, and
   nobody has measured a 7 MB WASM module plus a real tessellation pass on a
   tablet. If "tablet in hand at the printer" is the actual priority use
   case, that needs a real device test before this plan's desktop-first
   default is treated as settled.
2. **Interaction model.** Click-to-select-and-annotate (Phase 4's default,
   simpler to build first) vs. a paint tool (colour-per-face, like a
   highlighter). Purely a `model_web` UI decision; doesn't touch Phases 1–3
   or the tools in Phase 5.
3. **Multi-face grouping.** Out of scope for v1 (see Scope) — revisit once
   real use shows whether "these three faces are one fillet region" comes up
   often enough to need first-class support in `FaceMark`.
4. **Cache policy for the vendored WASM kernel.** Phase 2 carves out an
   exception to this pair of servers' `Cache-Control: no-store` convention —
   confirm that's the right call before it ships, since it departs from a
   rule stated elsewhere for a specific, load-bearing reason.
5. **Default tessellation quality.** Deflection is set per request from the
   browser now, unlike a server-baked mesh. Does Claude get a say in it
   (e.g. "render this coarser, it's a big assembly"), or is it a fixed
   client-side default the user tunes in the viewer itself?
