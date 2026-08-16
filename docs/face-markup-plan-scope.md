# Plan scope — face markup

Companion to `docs/face-markup-plan.md` (six phases). Written before the
`deepseek-flash-autopilot` run starts and frozen from that point — not edited
by the orchestrator or by any phase agent, regardless of what a phase's diff
seems to argue for.

## What this delivers

From the FreeCAD chat panel, Claude calls `view_model_3d` naming one or more
objects in the active document; a desktop browser tab opens (or an
already-open one updates over SSE) showing the real, orbitable BRep geometry
of those objects, tessellated client-side by a WASM OpenCascade build
(`occt-import-js`) reading FreeCAD's own native `.brp` export — no
server-baked mesh anywhere in the path. The user clicks a face, types a note,
and presses Send. Claude then calls `read_model_markup` and gets back both a
rendered PNG confirming what was marked and a list of exact
`(ObjectName, "FaceN")` references it can pass straight to `run_python` — each
one re-validated against the live document's current shape hash at read-back
time, so a face name is only ever reported with confidence when the geometry
it names hasn't changed since export. Calling `read_model_markup` before
anything has been marked up returns a plain message, not a traceback.

## Non-goals

Confirmed with the user against the plan's six phases, category by category
(generalization, the adjacent members of a set, the obvious next feature,
tidy-up, second consumer, compatibility, dependencies). Absolute — a phase
that appears to need one of these to make the delivered system work has found
a plan/scope contradiction, not a justified exception, and the run stops
rather than overriding it.

- **No standalone `export_brep`-style tool.** The `exportBrep` step stays
  private to `model_export.py`, called only from `tools_model.py`. It is not
  a general-purpose export tool alongside `export`/`slice_model`.
- **No STEP export.** `.brp` only, both directions. Not a fallback, not an
  alternative format.
- **No auth/pairing UI beyond what a loopback server needs.** No QR pairing,
  no persistent token store — `model_server.py` binds `127.0.0.1` and uses
  the same lightweight scheme `gcode_server.py` already does, not
  `device_server.py`'s LAN-facing one.
- **No loading of arbitrary external BREP/STEP files.** The viewer only ever
  loads what `view_model_3d` published. Dragging in a file from elsewhere is
  not part of this round trip.
- **No multi-face grouping.** One mark = one face for v1. `FaceMark` names a
  single face; "these three faces are one region" is not a structured
  concept the schema carries.
- **No edge- or vertex-picking.** Faces only, in the viewer and in the
  schema.
- **No phone/tablet transport.** Desktop loopback browser only — this is
  Open Decision #1 in the plan, not yet resolved either way, so it defaults
  to not-built until it is.
- **`model_web/src/doc.ts` is not a reuse of `web/src/doc.ts`.** A new,
  separate schema. Mixing flat 2D pixel coordinates and 3D face ordinals into
  one shared type is exactly the conflation the 2D schema's inert
  `snapped_to: null` field was left there to avoid — this is already the
  plan's own stated reasoning, restated here so it can't be quietly reversed
  mid-run for convenience.
- **`read_model_markup` does not mutate the document.** It returns face
  references and a picture. It does not call a fillet, a pocket, or any
  other operation itself — acting on a mark is a separate `run_python` call
  Claude makes afterward, same as every other read-only capture tool in this
  addon.
- **No retrofitting the shape-hash validation onto other tools.** Phase 5's
  staleness check is scoped to `tools_model.py` alone. `annotate_view` and
  `send_to_device` are not touched to add it, however consistent that might
  look.
- **No general cleanup of existing code.** Not in Phase 6, not in any phase.
  `render.py`, `diagnostics.py`, `session.py`, and everything else this
  feature reads as precedent get the minimum edit each needs to support this
  feature and nothing else — no dedup, no rename-for-consistency, no
  extracting a shared helper "while in there."
- **No new dependencies beyond the ones the plan already names.**
  `occt-import-js@0.0.23`, `@react-three/fiber`, `@react-three/drei`,
  `three` — all pinned in `docs/face-markup-plan.md`. Swapping any of them
  for "a better fit" mid-run is a non-goal, not a judgement call for the
  phase that gets there.
- **No changes to existing tests.** Add to a test file if a phase's own
  surface needs coverage; a change to an existing test's assertions or setup
  to make something pass is a signal to stop the run, not a fix to apply.
- **No second consumer.** The two tools are reached only through the
  existing MCP tool-call surface (`gui_bridge` → `mcp_server.py` → the
  `claude` CLI). No standalone CLI entry point, no second binary, nothing
  outside `freecad_tools/__init__.py`'s `TOOLS` registry.

## Fences

- **`docs/device-3d-paint/**`** — a separate, parked project (texture-atlas
  painting on a UV-unwrapped mesh, not face-picking on a BRep) from before
  this plan existed. Different technical approach to an adjacent problem.
  Must not be read, referenced, modified, or deleted by any phase.
- **`web/**`, `gcode_web/**`, and their committed build output
  `freecad/freecadclaude/device_ui/**` / `freecad/freecadclaude/gcode_ui/**`.**
  Read as pattern precedent only (Phase 3 names the exact stack and build
  config to mirror). Never modified — this plan's own file list never
  touches them.
- **`freecad/freecadclaude/device_server.py`, `gcode_server.py`,
  `freecad_tools/tools_device.py`, `freecad_tools/tools_slice.py`,
  `freecad_tools/print_export.py`.** Read as pattern precedent (the SSE
  shape, the async-tool shape, the meshing precedent). Not modified.
- **`freecad/freecadclaude/freecad_tools/__init__.py`'s `TOOLS` registry —
  additive only.** New entries for `view_model_3d`/`read_model_markup`;
  every existing entry's schema and `run`/`precheck` wiring is unchanged.
- **`freecad/freecadclaude/web_static.py` — additive only.** One new
  `.brp`/`.brep` entry in `TYPES`. The existing entries, `content_type()`,
  and `resolve()` are unchanged.
- **`docs/device-annotation-design.md`, `docs/device-annotation-plan.md`,
  `docs/device-annotation-mockup.html`, `docs/slice-preview-design.md`.**
  Existing design docs for other features. Not modified.
- **`gcode_web/package.json` and its lockfile.** Two Dependabot PRs are
  currently open against them (`postcss`, `brace-expansion`). This plan
  never has a reason to touch `gcode_web/` at all (see the fence above), so
  this is redundant with it in practice — named anyway because a manifest
  edit is exactly the kind of thing that looks harmless in a diff.

## In scope

The surfaces this plan may create or change — the complement of the fences.

- `freecad/freecadclaude/model_server.py` (new)
- `freecad/freecadclaude/freecad_tools/model_export.py` (new)
- `freecad/freecadclaude/freecad_tools/tools_model.py` (new)
- `freecad/freecadclaude/freecad_tools/__init__.py` (additive registration
  only, per the fence above)
- `freecad/freecadclaude/web_static.py` (additive `.brp`/`.brep` entry only,
  per the fence above)
- `freecad/freecadclaude/model_ui/` (new, committed build output)
- `model_web/` (new Vite + TypeScript + Vitest project, source of the above)
- `freecad/freecadclaude/chat_panel.py` (additive: auto-start wiring for
  `view_model_3d`, "New" clears `model_server.py` session state)
- `CLAUDE.md` (additive: a new section plus module-map rows)
- `RELEASE.md`, `deploy.ps1`, `deploy.sh` (additive: `model_web` joins the
  existing `web`/`gcode_web` entries in each list)
- `.gitignore` (additive: `model_web/node_modules/`, `model_web/dist/`)
- New `eval/test_model_server.py`, `eval/test_model_tools.py`

## Definition of done

Run on the integration branch before the final pull request to `main`.
Everything below except the three new-file lines already passes on `main`
today, confirmed at the time this document was written (FreeCAD 1.1.1,
`freecadcmd` at `/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd`).

- `python3 eval/test_device_server.py` — unmodified, still passes.
- `python3 eval/test_gcode_server.py` — unmodified, still passes.
- `python3 eval/test_slicer_runner.py` — unmodified, still passes.
- `freecadcmd <abs path>/eval/test_device_tools.py` — unmodified, still
  passes (needs an **absolute** path or it silently runs nothing).
- `cd web && npx vitest run` — unmodified, still passes (117 tests today).
- `cd gcode_web && npx vitest run` — unmodified, still passes (42 passed, 1
  skipped today).
- `python3 eval/test_model_server.py` — new, stdlib-only, no FreeCAD
  required.
- `freecadcmd <abs path>/eval/test_model_tools.py` — new; includes the
  staleness-warning fixture below.
- `cd model_web && npx vitest run` — new.
- **Behavioural, the golden path:** `view_model_3d` on a real object opens a
  browser tab showing the actual solid, orbitable; clicking a face, typing a
  note, and pressing Send makes `read_model_markup` return that exact
  `"FaceN"` with no warning attached.
- **Behavioural, the absent path:** `read_model_markup` called before
  anything has been marked up returns a plain message saying so, not a
  traceback and not an empty/malformed result.
- **Behavioural, the stale path:** export, mutate the object's shape, then
  read back a mark made against the pre-mutation export — `read_model_markup`
  must NOT report a confident `"FaceN"` for that object; it must return the
  recorded centroid/normal/area and an explicit warning instead.
