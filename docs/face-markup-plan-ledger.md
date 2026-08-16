# face-markup — autopilot ledger

Started: 2026-08-16
Integration branch: deepseek/face-markup
Clone URL: https://github.com/mrgeoffrich/FreeCADClaude.git
Plan: docs/face-markup-plan.md (not edited during this run)
Scope: docs/face-markup-plan-scope.md (frozen — not edited during this run)
Verification worktree: /private/tmp/claude-501/-Users-geoff-Repos-FreeCADClaude/328500c8-c18d-46b2-baa1-aee72ee86e1a/scratchpad/verify
  — `web/` and `gcode_web/` npm deps installed there; baseline confirmed clean
  (test_device_server.py, test_gcode_server.py, both `npx vitest run` suites)
  before Phase 1 launched.

## Live plan

- [x] P1 spike — proved `occt-import-js`'s `brep_faces` ordinals match
      FreeCAD's `Shape.Faces` order EXACTLY (identity permutation, not just
      matchable) on a 7-face box-with-hole and a 26-face filleted box —
      merged, PR #10
- [x] P2 foundations — `model_export.py`/`model_server.py` built exactly to
      spec, 127 checks passing (79 + 48), merged, PR #11
- [x] P3 viewer — `model_web/` built, real BRep loads and renders shaded,
      orbit confirmed with an actual mouse drag in a real browser (not just
      trusting the agent's report — see phase log) — merged, PR #12
- [x] P4 picking — click-to-mark with a real orange highlight that respects
      face boundaries exactly (verified against fillet edges), Send POSTs
      the correct `ModelMarkupDoc`, marks survive Send, Phase 1's spike
      promoted to a real Vitest correlation test and the throwaway
      directory deleted — merged, PR #13
- [x] P5 tools — `view_model_3d`/`read_model_markup` wired and registered;
      hash-check staleness verified end-to-end on real FreeCAD 1.1.1 (marks
      correctly degrade after a live mutation); confirmation render had a
      real bug (wireframe-only) found and fixed post-merge by independent
      verification — see Load-bearing additions — merged, PR #14 + fix
      commit `35ceaf5`
- [x] P6 hardening — "New" wiring, error paths verified (already correct,
      nothing to fix), CLAUDE.md/RELEASE.md/deploy scripts documented —
      merged, PR #15. **All six phases complete.**

## Budget

Relaunches used: 0 of 3

## Foundations

Model on precedent already in this exact repo — do not invent a second style
for anything below. Quoted into every phase brief verbatim.

- **Errors (Python):** every tool function returns a plain string on failure
  — "a message, not a traceback" — never lets an exception escape to
  `gui_bridge`. Matches `tools_device.py`/`tools_slice.py` exactly.
- **Errors (TS):** `try`/`catch` around every `fetch`, surfaced as user-facing
  text (a toast or status line), never an unhandled rejection that blanks the
  page. Matches `web/src/api.ts`'s `send()` wrapper shape.
- **Config/secrets:** none read by the server itself. `model_server.py`
  takes a token (`secrets.token_urlsafe(16)`, same three-form auth as
  `device_server.py`/`gcode_server.py` — header, query, cookie) and every
  path/preference as a plain argument resolved on the GUI thread by
  `tools_model.py`, exactly the "resolved on the GUI thread, handed in as
  plain values" invariant already stated in the scope document.
- **Logging:** `log_message` overridden to silence per-request stderr noise,
  matching both existing servers. Nothing else — no structured logging, no
  metrics. This addon has none anywhere.
- **Persistence:** none. In-memory only (a module-level `_Feed`-shaped
  object, matching `device_server.py`), cleared by a `reset_session()`
  called from `chat_panel._on_new`.
- **The boundary:** token-gated HTTP only, three-form `_authorized()` check
  copied from `device_server.py`. No further authn/authz — binds
  `127.0.0.1`, per the scope document's non-goal on pairing UI.
- **Lifecycle:** `start()`/`stop()` idempotent; auto-started on
  `view_model_3d` (no button, per `view_gcode`'s precedent, not
  `device_server`'s Connect-Mobile-button precedent); stopped via
  `QApplication.aboutToQuit`, matching `chat_panel._hook_device_shutdown`'s
  guard-flag pattern so a second `view_model_3d` call doesn't double-connect
  the signal.
- **Tests (Python):** `eval/test_model_server.py` — stdlib-only, runnable
  under bare `python3`, structured like `eval/test_device_server.py` (a
  `check(name, condition, detail)` helper, one running server, sequential
  checks). `eval/test_model_tools.py` — `freecadcmd`, structured like
  `eval/test_device_tools.py` (imported via the `freecadclaude` package, not
  by path, so it shares the package's live `model_server` state).
- **Tests (TS):** Vitest, colocated in `model_web/test/`, one file per pure
  module — matches `web/test/`'s and `gcode_web/`'s layout exactly. Nothing
  in CI opens a real browser or needs a GPU (WebGL/raycasting stays a manual
  check, the same carve-out `gcode_web/VENDORED.md` already takes).
- **Build & run:** `model_web/vite.config.ts` copied from
  `gcode_web/vite.config.ts`'s shape — `base: "./"`,
  `outDir: "../freecad/freecadclaude/model_ui"`, fixed asset names, no code
  splitting, deterministic (a rebuild with no source change produces no
  diff).

### Environment: real FreeCAD is not present on the harness host

Learned in P1, load-bearing for every later phase that needs `freecadcmd`
(P2's `model_export.py` tests, P5's hash-check fixtures). Quote this
verbatim into those briefs rather than letting the phase rediscover it:

The harness host container is **Alpine Linux 3.21 aarch64** — no `apt-get`,
and no `freecad` package in `apk` (checked `main`, `community`, and the
`3.21`/`edge` testing repos; `edge` is unreachable from the container
anyway). To get a real `freecadcmd`, bootstrap a nested Debian container with
the repo bind-mounted in:

    docker run -d --name fc-work -v <repo-workspace-path>:/work debian:bookworm sleep infinity
    docker exec fc-work apt-get update
    docker exec fc-work apt-get install -y --no-install-recommends freecad
    docker exec fc-work freecadcmd /work/<script-path>.py

This installs **FreeCAD 0.20.2** (linked against OCCT 7.6.3), not the
addon's real target of 1.1.1 — good enough for the OCCT-level API surface
this plan actually exercises (`Shape.exportBrep`, `Shape.Faces`,
`Shape.hashCode()`, `Part.makeFillet`), all stable across that range, but a
phase must say so plainly rather than imply parity with 1.1.1 if its result
would differ on newer FreeCAD.

Two `freecadcmd` quirks P1 hit and every later phase should expect:
- It does not print a script's stdout when run headless — write output to a
  file or to stderr instead.
- It **imports** the script as a module rather than executing it as
  `__main__` — logic gated behind `if __name__ == "__main__":` silently
  never runs. Put the real work at module level, or call it unconditionally.

### Dependencies, versions resolved 2026-08-16

- `occt-import-js@0.0.23` — checked against the npm registry directly
  (`npm view occt-import-js version` / `dist-tags`); this is still `latest`,
  last published 2024-12-03, no newer release exists. Vendor with a
  `VENDORED.md`-equivalent note in `model_web/`, matching `gcode_web`'s own
  vendoring discipline for `dimensioner`.
- `@react-three/fiber@^9.6.1`, `@react-three/drei@^10.7.7`, `three@^0.184.0`,
  `react@^19.2.6`, `react-dom@^19.2.6` — **not** freshly resolved; copied
  verbatim from `gcode_web/package.json`, which already pins and proves
  these exact versions in this repo. Deliberately not re-resolving "latest"
  here: two different pinned versions of the same libraries living in two
  sibling web apps in one repo would be its own inconsistency, and
  `gcode_web`'s pins are already a known-working combination.
- Dev tooling (`vite@^8.0.12`, `vitest@^4.1.9`, `typescript@~6.0.2`,
  `@vitejs/plugin-react@^6.0.1`, the `@types/*` set, the eslint stack) —
  same reasoning, copied from `gcode_web/package.json` verbatim.
- No other dependency is in scope — see the scope document's non-goal.

## Interfaces P2 actually built (quote verbatim into P3/P4/P5 briefs)

- `freecad_tools/model_export.py`: `export_brep(objs, out_dir) -> dict`.
  `objs`: live document objects with `.Name`/`.Shape`. Writes
  `<out_dir>/<Name>.brp` per object via `exportBrep`. Returns
  `{"objects": {"<Name>": {"path": <abs>, "shape_hash": int, "faces":
  {"<0-based index as string>": {"centroid": [x,y,z], "normal": [nx,ny,nz],
  "area": float}}}}}`. `"FaceN"` = index + 1. Raises `ValueError` naming the
  object if it has no shape. Does not write the dict anywhere.
- `model_server.py` public functions: `start(ui_dir=None, upload_dir=None)
  -> (url, token)` (binds `127.0.0.1`, ephemeral port, idempotent);
  `stop()`; `is_running() -> bool`; `current_url() -> str|None`;
  `set_upload_dir(path)`; `set_upload_hook(callback)` (fires on the HTTP
  worker thread with the stored path); `reset_session(upload_dir=None)`
  (clears state, NOT wired into `chat_panel.py` — P6's job);
  `publish(objects_dict, upload_dir=None) -> record` (`objects_dict` is
  exactly `model_export.export_brep()`'s return value; mints one
  `secrets.token_urlsafe(8)` id per call; `_KEEP_PUBLISHED = 8` cap);
  `published_record(publish_id=None) -> record|None`; `uploads() -> list`.
- Routes: `GET /api/latest` → `{"published": {"id", "objects": {"<Name>":
  {"url": "/api/mesh/<id>/<Name>.brp", "shape_hash", "faces"}},
  "published_at"}}` or `{"published": null}`. `GET
  /api/mesh/<id>/<object_name>.brp` → raw BREP bytes, `Cache-Control:
  no-store` (same as `device_server`'s `/api/image/<id>` precedent — not
  cached, even though immutable per id), 404 JSON for an unknown/pruned
  id or object. `POST /api/upload` → JSON body only (`Content-Type:
  application/json`), stored verbatim, 400 on non-JSON, 413 oversize, 503
  no upload folder configured, 403 without a valid token. `GET /api/events`
  → SSE, one `published` event per publish.
- `_send(status, body, content_type, cacheable=False)` is the one place
  `Cache-Control: no-store` is applied — `cacheable=True` (used only for
  files served from `UI_DIR`/`model_ui/`) skips it. Any future static route
  reuses this flag rather than inventing a second cache policy.
- Chat panel: `_hook_model()` (called once from `_ensure_worker()`, guarded
  by `self._model_hooked`) wires `aboutToQuit` → `_shutdown_model()` →
  `model_server.stop()`. No button, no other `chat_panel.py` surface.
- `web_static.TYPES` gained `.brp`/`.brep` → `"application/octet-stream"`.
- FreeCAD tested against: 0.20.2 (Debian bookworm package, nested container
  — see the environment note above). Addon's real target is 1.1.1; all
  API surface used (`exportBrep`, `Shape.Faces`, `CenterOfMass`, `Area`,
  `normalAt`, `Surface.parameter`, `hashCode`) is stable across that range.

### P3's viewer interface (quote into P4's brief — P4 must EXTEND this, not replace it)

- `model_web/src/worker.ts`: a module Worker, created once per tab
  (`new Worker(new URL("./worker.ts", import.meta.url), {type:"module"})`).
  Message in: `{kind:"parse", id, buffer: ArrayBuffer}` (a `.brp` file's raw
  bytes, transferred). Message out today:
  `{kind:"parsed", id, positions: Float32Array, normals: Float32Array|null,
  indices: Uint32Array}` or `{kind:"error", id, message}`. **P4 must add the
  `brep_faces` triangle-index ranges to this reply** — P3's worker discards
  them after merging into one position/normal/index buffer per object,
  exactly per its own brief (picking was explicitly deferred). Don't touch
  how P3 loads `occt-import-js` itself (`createOCCT({locateFile: () =>
  wasmUrl})`, the `?url`-imported `assets/app.wasm`) — only extend what gets
  posted back.
- `model_web/src/api.ts`: `parseLatest`/`parsePublished`-shaped defensive
  parsing (mirrors `web/src/api.ts`), plus SSE event parsing. P4's upload
  function is new, added alongside these, not a replacement.
- `model_web/src/App.tsx`: owns the `fetch("/api/latest")` on mount, the
  `EventSource` subscription, and one `THREE.Mesh` per object with a plain
  grey `MeshStandardMaterial`, tagged with the object's name. P4 adds
  raycasting against these meshes and a click handler; it does not need to
  change how they're built, only add picking on top and a per-face
  material/highlight capability if the interaction model calls for it.
- Real-browser check performed independently (not just the agent's
  self-report): started `model_server.py` locally, published the two real
  `.brp` fixtures from Phase 1's spike directly (bypassing FreeCAD), loaded
  the built page in a real Playwright/Chromium browser, and confirmed by
  screenshot that real geometry renders shaded (not black, not blank) and
  that a mouse-drag genuinely changes the camera angle (`OrbitControls` is
  wired to real input, not just present in the bundle unused). Worth
  repeating this same technique after P4 (picking) and P5 (the full round
  trip) rather than trusting a flash agent's account of what a browser
  showed it, per the user's own instruction mid-run.

## Amendments

- **Before P2:** the plan document's Phase 2 bullet ("auto-start wiring in
  `chat_panel.py`, mirroring `view_gcode`") is imprecise — verified against
  `tools_slice.py`/`chat_panel.py` directly. `gcode_server.start()` is called
  from *inside* `_run_view_gcode` in `tools_slice.py`, on the GUI thread when
  the tool runs — not from any chat-panel button. `chat_panel.py`'s only
  gcode-related piece is `_hook_slicer()`, which wires `aboutToQuit` →
  shutdown, called from `_ensure_worker()` (so quitting after any chat is
  safe) and again from the Slicer-settings button handler (gcode has a
  button too, for the settings page — model doesn't).
  Corrected division for this plan: **auto-start belongs in Phase 5**
  (`tools_model.py`'s `view_model_3d` calls `model_server.start(...)`
  directly, exactly like `_run_view_gcode` does) — Phase 2 builds
  `model_server.start()`/`stop()` themselves but calls neither. **Phase 2's
  only `chat_panel.py` change** is a `_hook_model()`-shaped method wiring
  `aboutToQuit` → `model_server.stop()`, called once from `_ensure_worker()`
  alongside the existing `self._hook_slicer()` call — one call site, not two,
  since there is no settings button for `model_server` the way there is for
  the slicer.
- Resolved for P2, not stated precisely enough in the plan: `POST
  /api/upload` takes a **plain JSON body** (`Content-Type: application/json`
  — the serialized `ModelMarkupDoc`), NOT multipart like `device_server.py`'s
  upload route. There is no image in this upload — Phase 5's `read_
  model_markup` gets its picture from a fresh **server-side** offscreen
  render (section 6 of the plan), never from a client screenshot — so there
  is nothing for the browser to attach as a second multipart part. The
  plan's Phase 4 bullet ("reusing the multipart shape... already
  established") is superseded by this; Phase 4's brief will say so.
- Resolved for P2: one `view_model_3d` call publishes ONE set of objects
  under a single publish id (mirroring `device_server.publish()` minting one
  id per capture, not one per object). `GET /api/mesh/<id>/<object_name>.brp`
  serves one object's bytes; `GET /api/latest` returns the whole published
  set (`id`, and per object: a mesh URL, `shape_hash`, and the `faces` map)
  in one payload, so the viewer loads every object from one fetch.

## Assumptions

- The scope document's non-goal list includes four items (`doc.ts`
  separation, no auto-mutation from a mark, no hash-validation retrofit onto
  other tools, no Phase 6 cleanup) that the user's own answer batch left
  unselected rather than explicitly confirming. Included anyway — the first
  is already the plan's own stated reasoning, the fourth is mandated by the
  autopilot skill's own late-phase-cleanup rule regardless of user input, and
  the middle two are exactly the "obvious next feature"/"tidy-up for
  consistency" categories the non-goal-generation process exists to catch.
  User was shown this reasoning explicitly and confirmed "commit as written"
  before the document was frozen. If any of the four turns out to be wanted,
  the scope document needs a human edit and a restart from the phase it
  would have affected — not a unilateral reversal mid-run.

## Load-bearing additions

- P1 bootstrapped a nested Debian container (`docker run debian:bookworm` +
  `apt-get install freecad`) to get a real `freecadcmd`, because the harness
  host (Alpine) has none installable. Not written down in the plan or brief,
  but the "preferred path: real FreeCAD" the brief asked for does not exist
  without it — the alternative was settling for the weaker pythonocc-core
  fallback, which the brief itself treats as a degraded result to be labeled
  honestly, not equivalent. See Foundations' new environment note.
- **Post-P5 fix (commit `35ceaf5`, applied directly by me, not a relaunched
  phase): `_write_face_colors` rewritten.** P5's own report was explicit and
  honest that its render path was verified only on the harness container's
  FreeCAD 0.20.2 (via a substitute harness, since `Gui.Document.createView`
  doesn't exist there) and that the real 1.1 `ShapeAppearance` path "needs a
  human check on real FreeCAD 1.1" — exactly the gap flagged before P5
  launched. Checking it (real local FreeCAD 1.1.1,
  `/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd` +
  `FreeCADGui.showMainWindow()`, this repo's own documented recipe) found a
  real, load-bearing bug: `read_model_markup`'s confirmation PNG rendered as
  edges only — every face unfilled — the moment any per-face colour write
  happened, even a no-op rewrite of the same values. Isolated by a control
  (the existing, unrelated `capture_view` rendered perfectly in the
  identical harness) and then by elimination (reading only: fine; writing
  via the classic `DiffuseColor` property, or via
  `ShapeAppearance.setDiffuseColors`: both broke it, identically, regardless
  of timing relative to `_offscreen_shot`). The fix: reassign the WHOLE
  `ShapeAppearance` tuple as fresh `Material` instances per face — the same
  pattern `render._set_diffuse` already uses (proven working, since
  `_shot_appearance`/`capture_view` depend on it) fanned out from one
  material to N. Verified directly afterward: a properly shaded solid with
  one face visibly, correctly highlighted, matching every other face's
  shading. Only `_write_face_colors` changed; its signature and every call
  site are untouched, so this is a drop-in fix, not a redesign.
  This is exactly the scenario flagged in P5's own pre-launch ledger note —
  the container's older FreeCAD couldn't exercise the 1.1-only code path at
  all, so this was never going to be caught without an independent check
  against the real target version. Recorded here as load-bearing, not
  adjacent: the scope document's own "what this delivers" paragraph
  promises a rendered PNG, and it did not work before this fix.

## Adjacent — recorded, not built

(none yet)

## Phase log

### P1 spike — merged, PR #10

Launched: 2026-08-16 · request `mcp-4c822d921ca12c005f43118d9af521ee` ·
session `sess-c4d8bb20e676010f6f4fe4a9fc6b9b87` · finished `ok` in ~11m (71
sub-turns, $0.0008)
Check: base correct (`deepseek/face-markup`), diff confined to the new
`spike/face-markup-correlation/` directory (no fence crossings), dependency
pinned exactly `occt-import-js@0.0.23` (`--save-exact`, verified in the
diff), comparison logic verified by reading `compare.py` directly — it does
the identity-permutation check the brief asked for, not a looser "close
enough." Independently re-ran `compare.py` against the merged integration
branch after merge: `overall_correlation_holds: True`, matches the report.
Baseline `eval/test_device_server.py` still passes on the integration
branch.
One process fix needed: the brief copied `deepseek-flash-task`'s generic
`--draft` PR instruction instead of this run's own "normal PR, you're
merging it in minutes" — `gh pr ready` fixed it in one command, no relaunch.
Left behind: `spike/face-markup-correlation/{freecad/export_faces.py,
node/read_brp.js, compare.py, README.md, shapes/*.brp, out/*.json}` —
throwaway per the plan, superseded by P4's real Vitest correlation test.
Confirms the plan's core geometry bet (section 02) on 7- and 26-face shapes,
including the adversarial case (8 near-identical spherical corner patches on
the filleted box) that a merely-permutable order would have failed.
Deferred by the agent: none.

### P2 foundations — merged, PR #11

Launched: 2026-08-16 · request `mcp-704145282064b9739538f05b9c105f05` ·
session `sess-4dc942a47e881ed81b7e4cfa88f9dc82` · finished `ok` in ~9.5m (41
sub-turns, $0.0013)
Check: base correct, files confined to exactly the six named in the brief
(model_export.py, model_server.py, web_static.py +5/-0, chat_panel.py
+29/-0, two new eval/ test files) — no fence crossings, no dependency
manifest touched. Read `model_export.py`, `model_server.py`'s Cache-Control
logic, `chat_panel.py`'s diff, and `web_static.py`'s diff directly — all
match the brief precisely, well-reasoned docstrings, correct precedent-
following (e.g. `/api/mesh/<id>/...` kept at `no-store` matching
`device_server`'s `/api/image/<id>`, not "improved" to a longer cache).
Independently re-ran on the merged integration branch (not just trusting
the report): `test_device_server.py` PASS, `test_gcode_server.py` PASS,
`test_model_server.py` PASS.
Left behind: see "Interfaces P2 actually built" above — this is now the
authoritative contract, not the plan document's rough sketch.
Deferred by the agent: none. No full-suite escalation needed — nothing
ambiguous, no existing test modified, no shared file beyond the two tiny
additive touches already reviewed line-by-line.

### P3 viewer — merged, PR #12

Launched: 2026-08-16 · request `mcp-5537f2d50cfc600c517ecfaba95c2249` ·
session `sess-614e9e51c7821845359d0a675f6f0ac4` · finished `ok` in ~8m (43
sub-turns, $0.0012)
Check: base correct, files confined to `model_web/`, the new `model_ui/`
build output, and two `.gitignore` lines — no fence crossings. Dependencies
matched the pinned set exactly (`occt-import-js` exact `0.0.23`, everything
else copied verbatim from `gcode_web/package.json`). Build determinism
verified independently by the agent (built twice, `git diff --exit-code`
clean) — not just claimed.
**Escalated beyond the cheap check**: the agent's own report flagged the
WebGL render as unverified (a legitimate, honest carve-out per its brief —
mocking a real GPU render would prove nothing). Rather than accept that gap,
independently started `model_server.py`, published Phase 1's two real
`.brp` fixtures directly (no FreeCAD needed — the spike's own output files
were enough), loaded the built page in headless Chromium via Playwright,
and screenshotted it: real geometry rendered, grey and properly lit (not
black — proves normals + `MeshStandardMaterial` + lights all actually
work), then dragged the mouse and re-screenshotted — the camera angle
genuinely changed, proving `OrbitControls` is wired to real input. No
console errors beyond harmless GPU-perf warnings.
Left behind: see "P3's viewer interface" above.
Deferred by the agent: `brep_faces` extraction (explicitly, per its own
brief — this is P4's job, not a gap).

### P4 picking — merged, PR #13

Launched: 2026-08-16 · request `mcp-f12dc9aace6f940218eec6cfc05bae14` ·
session `sess-59aee8d268df17b80167538ef469b2e8` · finished `ok` in ~30m (161
sub-turns, $0.0013) — the biggest phase so far, as expected.
Check: base correct, files confined to `model_web/` (extended, not
rewritten — `App.tsx`/`worker.ts` diffs only, not full rewrites) plus
`spike/face-markup-correlation/` deleted in full. No new npm dependencies
(package.json untouched). Read `picking.ts` and `doc.ts` directly — clean,
well-reasoned, matches the brief's schema exactly. The agent's own report
included a "sabotage run" (reversed range order) that made its promoted
correlation test fail loudly, which is good independent evidence the test
is real rather than a tautology — still re-ran it myself rather than take
that on faith (see below).
**Escalated beyond the cheap check, same as P3**: independently re-ran
`npx vitest run` (34/34, matches the report). Then pulled the real branch,
started `model_server.py`, published the `filleted_box` fixture the phase
itself copied into `model_web/tests/fixtures/`, and drove real mouse
events through Playwright against the actual built page: hovered a face
(confirmed a visible light tint), clicked it (confirmed a saturated orange
highlight that respects the fillet boundary EXACTLY — proves the
triangle-range face isolation is pixel-correct, not approximate), filled
in a note and caption and pressed Send, and read the file `model_server.py`
actually wrote to disk: `{"version":1,"source":{"publish_id":"W1k00H2-fFk"},
"marks":[{"id":"m1","object":"FilletedBox","face_index":10,"color":null,
"note":"..."}],"caption":""}` — exactly the schema, with the right
`face_index` for the clicked face (Face11) and the right `publish_id`.
Also confirmed visually that the mark stays highlighted and listed AFTER
Send (the "don't clear on send" requirement — checked by eye, not just by
reading the diff).
Left behind: `model_web/src/{worker.ts (extended), picking.ts (new),
doc.ts (new), App.tsx (extended)}`; `model_web/tests/{doc,picking,
correlation}.test.ts` + `fixtures/`; `spike/face-markup-correlation/`
gone.
Deferred by the agent: none. Amendment: none needed — the interface P3 left
was extended exactly as specified, nothing about it needed to change.

### P5 tools — merged, PR #14 + a direct post-merge fix (commit `35ceaf5`)

Launched: 2026-08-16 · request `mcp-46d3b1a4f8a8b68cff81bdd06ae94f99` ·
session `sess-0260aaf58e3d795b982d877be2c67c8c` · finished `ok` in ~17.5m
(78 sub-turns, $0.0014)
Check: base correct, files confined to exactly the three named
(`tools_model.py`, `__init__.py` +11/-0 additive, `eval/test_model_tools.py`)
— no fence crossings. Read `_write_face_colors`, `_face_highlight`, and
`_run_read_model_markup` directly: correct nesting inside
`_offscreen_shot`'s own restore, graceful degradation ("the face
references above still stand" if the render itself fails), dual-API
`ShapeAppearance`/`DiffuseColor` handling with an honest fallback comment.
The agent's own render verification was unusually thorough for what its
container could reach — a real `xvfb` GL context, a real inspected PNG,
even a "sabotage" cross-check — but it explicitly and correctly flagged
that the 1.1-only `ShapeAppearance` path and the addon's real
`_offscreen_shot` scaffolding (blocked by `Gui.Document.createView` not
existing on the container's 0.20.2) both "need a human check on real
FreeCAD 1.1."
**That check found a real bug** — see "Post-P5 fix" under Load-bearing
additions for the full root-cause. Short version: the confirmation PNG
rendered as edges only, every face unfilled, the instant any per-face
colour was written (even a no-op rewrite), because writing the color data
through either FreeCAD API updates the stored values but not Coin's
material binding, and an offscreen FBO grab renders that as wireframe
while the live interactive view never would have shown the problem.
Isolated with `capture_view` as a control (unaffected, proving this was
`tools_model.py`-specific, not my test setup), fixed by reassigning the
whole `ShapeAppearance` tuple as fresh `Material` instances (the pattern
`render._set_diffuse` already uses, fanned out from one material to N),
and re-verified with a real properly-shaded, correctly-highlighted render.
Applied directly as commit `35ceaf5` on the integration branch — a
surgical, single-function fix, not a relaunched phase — since I had the
real target FreeCAD locally and the harness's container structurally could
not have caught this (no `ShapeAppearance` on 0.20.2 at all).
Also independently verified on real FreeCAD 1.1.1, beyond what either the
agent's container tests or my render-bug-hunting touched: the full
`view_model_3d` → (simulate a browser upload) → `read_model_markup` round
trip end to end, including the hash-check genuinely degrading a mark to a
warning (with the correct recorded centroid) after mutating the marked
object and recomputing — the actual "hard part" of the whole plan, and it
worked exactly as designed on the first real try.
Left behind: `tools_model.py` (`view_model_3d`, `read_model_markup`, the
per-face highlight helpers now fixed), both tools registered.
Deferred by the agent: none. Amendment: none needed beyond the fix above —
the interfaces from P2/P4 were consumed exactly as recorded.

### P6 hardening — merged, PR #15 (final phase)

Launched: 2026-08-16 · request `mcp-0219f25afc3df06a9191fd33e2499bab` ·
session `sess-6ee3ed5d834dea95bfbe946e1edc264a` · finished `ok` in ~7.5m
(sub-turns not separately logged in the tail I captured; cost negligible,
consistent with the rest of the run)
Briefed the no-cleanup non-goal unusually explicitly, per the skill's own
mandate to always rehearse this for the last phase — it held. The agent's
own report named five specific, plausible improvements it declined
("update deploy comments to say three", "have `_run_view_model_3d` call
the new `model_upload_dir()` instead of its own inline
`_session_subdir`", "add a start-failure test when no gap was found",
"match `_note`-on-error like the other two resets do, when the brief said
`except: pass`", "mention `model_ui` in the Slice preview section's
build-output bullet") — exactly the "obviously better, not asked for"
temptations this phase exists to test.
Check: base correct, files confined to exactly the seven named
(`tools_model.py` +16/-0, `__init__.py` +2/-1, `chat_panel.py` +24/-0,
`CLAUDE.md` +85/-0, `RELEASE.md` +20/-16, `deploy.ps1` +1/-1, `deploy.sh`
+1/-0) — no fence crossings, nothing else touched. Read the `CLAUDE.md`
addition in full: matches the existing "Device annotation"/"Slice preview"
sections' voice and structure closely, correctly documents the P5 render
fix, correctly cross-references `diagnostics.py`'s naming discipline, and
gets every technical fact right against what was actually built. Read
`_reset_model_session`/`model_upload_dir` against their
`_reset_device_session`/`device_upload_dir` precedents — exact match.
Part B (the one error-path check in scope) found `view_model_3d` already
handles a `model_server.start()` failure correctly and made no change —
confirmed independently by reading `_run_view_model_3d`'s `try/except
RuntimeError`/`except OSError` directly in the merged file.
Independently re-ran, after merge, the full suite: `test_device_server.py`,
`test_gcode_server.py`, `test_model_server.py`, `test_slicer_runner.py`,
`test_qr.py` (bare `python3`, all PASS); `web`, `gcode_web`, `model_web`
vitest suites (117 + 42/1-skipped + 34, all passing); and — on REAL local
FreeCAD 1.1.1, not just the container's 0.20.2 — `eval/test_model_tools.py`
and `eval/test_device_tools.py` (both PASS). This is the scope document's
full definition-of-done, satisfied on the actual target platform.
Left behind: the complete, working feature. Deferred by the agent: none.

## Run complete

All six phases merged into `deepseek/face-markup`. One additional
load-bearing fix (`35ceaf5`) applied directly between P5 and P6 after
independent verification surfaced a real rendering bug the harness's older
container FreeCAD structurally could not have caught. Nothing deferred by
any phase. Full suite passes on the integration branch, confirmed
independently on real FreeCAD 1.1.1. Proceeding to the PR against `main`.
Recorded before launch, for my own re-check afterward: `model_server.
uploads()` records are `{"path", "doc" (raw JSON text), "bytes",
"received_at"}`; `published_record(id)` is the INTERNAL shape
`{"id", "objects": {name: {"path","shape_hash","faces"}}, "published_at"}`
(not the HTTP-facing `_public_record` view). Briefed the render-verification
honesty requirement explicitly — expect this phase's report to plausibly
say the offscreen render itself needs a human check on real FreeCAD 1.1; if
so, I'll do that myself locally (this machine has a real FreeCAD 1.1.1 at
`/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd`) rather than
accept the container's degraded 0.20.2 as the final word, same as the
browser-side checks for P3/P4.

Result: exactly that gap materialised, and checking it found a real,
load-bearing bug (the confirmation render was wireframe-only). Fixed
directly — see "Post-P5 fix" under Load-bearing additions and the phase log
entry below. Note for anyone reading this later: local verification via
`freecadcmd` + `FreeCADGui.showMainWindow()` reliably SIGSEGVs on interpreter
teardown on this Mac, exactly as this repo's own `CLAUDE.md` gotcha section
already documents ("Expect a SIGSEGV during interpreter teardown... flush as
you go") — each crash is a scratch/throwaway document process exiting after
already writing its result, not data loss, but it does pop a macOS crash
dialog per run. Worth knowing before running many of these back to back.
