# Slice preview — technical design and plan

Take a collection of objects from the live document, hand them to the Bambu Lab
slicer as a 3MF, and open the resulting G-code in an interactive 3D toolpath
viewer in the user's desktop browser. Three capabilities, one round trip.

The design and the plan are one document rather than the two
`docs/device-annotation-{design,plan}.md` were: the work is smaller, and the
milestone order falls out of the forks.

## What this adds

One extended tool, three new tools, two stdlib-only modules, and a second
committed web-app build output vendored from the `dimensioner` project.

The round trip, end to end:

1. **`slice_model(names=[...])`** resolves the objects the same way `export`
   already does, writes `<session>/slices/<job>/model.3mf` with `Mesh.export`,
   builds the slicer argv from the resolved presets, starts the Bambu Studio CLI
   on a daemon thread, and returns immediately with a job id. FreeCAD keeps
   repainting.
2. **`read_slice_result(job?)`** collects the outcome — `plate_1.gcode`,
   `result.json`'s per-feature times and filament usage, the placed bounding
   boxes, and the log tail on failure.
3. **`view_gcode(job?)`** starts a loopback HTTP server, publishes the G-code
   under an id, and opens the user's desktop browser on the vendored viewer with
   `?gcode=<id>`. The page fetches, parses in a Web Worker, and draws the
   toolpath with per-feature colour toggles and a min/max layer slider.
4. **`export`** gains `.3mf` in its schema, which is most of what capability 1
   needs.

`view_gcode`'s output is for the user, not for Claude, the same shape as
`annotate_view`. Claude cannot see a WebGL canvas. Letting Claude see the
toolpath is a separate capability and it is deferred.

Unlike `device_ui/`, which is mobile-first and reached from a phone over the LAN,
this viewer targets a desktop browser: it is dense and 3D-intensive, and it wants
a mouse, a large canvas and a GPU.

## Scope

**In, for v1:**

- `export` accepts `.3mf`, documented as a multi-object mesh export.
- `slice_model` / `read_slice_result` / `view_gcode`.
- Slicer binary and preset discovery, with preferences for the choices.
- A loopback HTTP server serving the vendored viewer and the G-code.
- The viewer's G-code half, vendored as committed build output.

**Out, for v1:**

- The `dimensioner` Python correction pipeline, and the predicted-edge and
  deviation-surface overlays it feeds. See fork 4.
- Claude seeing the toolpath as an image.
- Choosing among multiple plates in the viewer.
- Removing the slicer's placement offset. See fork 6.

## Verified feasibility

Measured on this machine: FreeCAD 1.1.1, Bambu Studio 02.08.01.55 at
`/Applications/BambuStudio.app/Contents/MacOS/BambuStudio`, macOS. OrcaSlicer is
also installed, at `/Applications/OrcaSlicer.app`.

**FreeCAD already exports 3MF, and `tools_export.py` already reaches it.**
`Mesh.export([box, cylinder], "out.3mf")` produced a valid 3MF whose
`3D/3dmodel.model` holds two `<object>` and two `<item>` elements, so a
multi-object collection survives as distinct objects — which is what the slicer
needs to place them as separate parts. `Part.export` does not handle `.3mf`;
`Mesh.export` does. And `_run_export`'s final `else` branch is already
`Mesh.export(objs, path)`, so `export(path="foo.3mf")` works today. The only
thing stopping Claude from trying it is the schema description, which lists
STEP/IGES/BREP/STL and nothing else. Capability 1 is a description change and a
test.

**Tessellation is controllable.** `Mod/Mesh`'s `MaxDeviationExport` (default 0.1)
is read by `Mesh.export`. A 10 mm-radius cylinder:

| `MaxDeviationExport` | triangles |
|---|---|
| 0.5 | 500 |
| 0.1 | 500 |
| 0.05 | 500 |
| 0.01 | 912 |
| 0.005 | 1672 |
| 0.001 | 5024 |

The knob bites below about 0.05 mm and scales cleanly from there. Coarsening
above that does nothing, because angular deflection sets a floor. So finer
export is available and coarser is not, which is the direction that matters.

**The slicer CLI works headless, first try, with no GUI and no window:**

```
BambuStudio --load-settings "<P>/machine/Bambu Lab P2S 0.4 nozzle.json;<P>/process/0.20mm Standard @BBL P2S.json" \
            --load-filaments "<P>/filament/Generic PLA @BBL P2S.json" \
            --slice 0 --outputdir ./sliceout probe.3mf
```

with `<P>` = `/Applications/BambuStudio.app/Contents/Resources/profiles/BBL`. It
wrote `plate_1.gcode` (208 KB) and `result.json` (`"error_string": "Success."`,
`return_code: 0`, per-plate feature-type times, filament usage, per-object placed
bboxes) and exited clean in about 20 s for a trivial two-object part. There is no
`--export-gcode`; `--slice N` writes into `--outputdir`, with 0 meaning all
plates.

**The slicer auto-arranged and centred the parts**, landing them at x≈100/y≈90
though they were modelled at the origin. A document modelled anywhere gets placed
sanely, and `result.json` reports where.

**The generated G-code parses in dimensioner's real parser**, not by tag matching
alone. `parseAndBuild` on `plate_1.gcode`:

```
layerCount 40 · 6117 segments · unknownFeatureStrings [] · warnings []
retractions 130 · layerZ[0..4] 0.2 0.4 0.6 0.8 1.0
bounds min [87.823, 0.2, -112.179]  max [112.177, 8, -87.821]
```

Every `; FEATURE:` string the slicer emitted maps through `FEATURE_ALIASES`,
including `floating vertical shell`. No parser work is needed. The chain
FreeCAD → `Mesh.export` 3MF → Bambu CLI → `plate_1.gcode` → viewer parser is
proven end to end.

Those bounds are plate-absolute: world Y is print Z, and X/Z sit near plate
centre because the slicer moved the part. The viewer frames correctly off
`bounds`, but a user comparing the toolpath to their FreeCAD model finds every
coordinate offset. That is fork 6.

**Presets live in two places** — system at
`<slicer resources>/profiles/BBL/{machine,process,filament}/` and the user's own
at `~/Library/Application Support/BambuStudio/user/<id>/{machine,process,filament}/`.
The printer here is a Bambu Lab P2S printing PLA.

One cosmetic gap: `result.json` reported `filament_id: ""` and `main_used_g: 0.0`
with a bare system filament preset, so filament mass did not resolve. A user
filament preset is the likely fix.

**Measured cost of vendoring the viewer** (its own `npm run build`):
`index.js` 1,132 KB (gzip 311 KB), CSS 5.7 KB, three workers 24 KB combined —
1.15 MB of app assets, built in 495 ms from 585 modules. Its `dist/` measures
5.3 MB, but 4.1 MB of that is `dist/samples/` backing the "Load sample" button.
Today's `device_ui/` is 52 KB; the repo is 56 MB.

## Architecture

```
chat panel (GUI thread)
  └─ AgentWorker ─▶ claude CLI ─▶ MCP ─▶ gui_bridge ─▶ tools_slice
                                                        │      │      │
                     slice_model ────────────────────────┘      │      │
                       Mesh.export 3MF · resolve presets        │      │
                       · start_job() ──▶ slicer_runner ── daemon thread ──▶ BambuStudio CLI
                                             (job table)        │             (subprocess)
                     read_slice_result ─────────────────────────┘      │
                       poll / bounded nested QEventLoop               │
                     view_gcode ────────────────────────────────────────┘
                       publish() ─▶ gcode_server (127.0.0.1) ─▶ open browser
                                          serves gcode_ui/
                                          GET /api/gcode/<id>

  desktop browser ◀── HTTP (loopback, token) ──▶ ThreadingHTTPServer
    vendored viewer: workers parse, r3f renders, feature toggles + layer slider
```

Artifacts go to `<session>/slices/<HHMMSS>_<label>/`, holding `model.3mf`,
`plate_1.gcode`, `result.json`, `slicer.log` and `job.json`. A per-job directory
rather than flat files under `exports/`, because `--outputdir` writes
slicer-chosen names that would collide across jobs, and the log belongs beside
what it explains.

`session._prune_folder` filters on `os.path.isfile`, so it will not prune
directories. `slices/` needs its own keep-N, as a named addition to `session.py`
(`_session_job_dir(name, keep=20)`).

### How the slicer avoids blocking the GUI thread

This is the central problem. A slice took about 20 s for a trivial two-object
part and will be much worse for a real model. `run_python`'s `_heavy_loop_note`
exists to refuse work of exactly that shape, and the bridge's 600 s
`GuiBusyTimeout` reports rather than cancels.

The answer is the two-halves split `annotate_view` already uses, with one
addition. `slice_model` does its FreeCAD work — resolve objects, `Mesh.export`,
read the world bbox — hands `slicer_runner` a fully-resolved argv and a job
directory, and returns. The subprocess runs on a daemon thread that touches no
FreeCAD and no Qt.

What differs from `annotate_view` is that no human signals completion, so polling
has to work. `read_slice_result` takes an optional bounded wait: a
`QtCore.QEventLoop` plus a `QTimer` polling `job_status` every 250 ms, with a
deadline `QTimer.singleShot(..., loop.quit)`. `eval_runner.py:114` already runs
that loop, and the bridge marshals tool calls into the GUI thread during it,
which is why the nested loop is the required shape rather than `sleep`.

Three constraints on the wait:

- Total wait stays well under `_GUI_CALL_TIMEOUT` (600 s): default 120 s, cap
  480 s. On expiry the tool reports the job as still slicing and asks for another
  `read_slice_result`, so the response is not "resend the slice".
- The nested loop is reentrant in principle, since a second `_CallEvent` could
  arrive while the first is on the stack. There is one client and it is
  sequential within a turn. `read_slice_result` holds no document state across
  the wait either way, only a job handle.
- The Qt import lives inside the function in `tools_slice.py`, as `render.py`
  does. `slicer_runner.py` stays Qt-free.

The subprocess gets a hard timeout (default 900 s) and is killed on expiry.
`chat_panel` connects `aboutToQuit` to a terminate hook, mirroring
`_shutdown_device` at `chat_panel.py:737`; quitting FreeCAD mid-slice would
otherwise orphan the child.

`slicer_runner.set_done_hook(callback)` mirrors `device_server.set_upload_hook`.
`chat_panel` wires it to a `slice_finished` signal and writes a transcript note,
so the user learns the slice landed even while Claude is between calls.

### Modules

| New file | Role |
|---|---|
| `freecad/freecadclaude/freecad_tools/tools_slice.py` | `slice_model`, `read_slice_result`, `view_gcode`. GUI-thread work: object resolution, `Mesh.export`, preference reads, preset resolution, `result.json` summarising, the bounded wait. |
| `freecad/freecadclaude/slicer_runner.py` | Stdlib only, no FreeCAD, no Qt. Job table, daemon thread, subprocess, argv builder, and discovery as pure functions over paths handed in. Sibling of `device_server.py`, testable under a bare `python3`. |
| `freecad/freecadclaude/gcode_server.py` | Stdlib only. `127.0.0.1` static server for `gcode_ui/` plus `GET /api/gcode/<id>`. Token-gated. |
| `freecad/freecadclaude/gcode_ui/` | Committed build output — the vendored viewer. |
| `gcode_web/` | Its Vite source: the viewer half of dimensioner, upstream layout preserved, replaced `vite.config.ts`, plus `VENDORED.md` recording the upstream commit and every local patch. Dev-only, excluded from deploy. |

`tools_export.py` gains a shared `_resolve_export_objects(args, doc)` lifted out
of `_run_export`, so `slice_model` resolves objects through the same code rather
than a second copy. `session.py` gains `_session_job_dir`.

### Tool schemas

**`export`** — description gains 3MF: a mesh export that keeps each named object
as a separate 3MF object, which is what a slicer needs to place them as separate
parts. Plus a `deviation` (number, mm) property setting `MaxDeviationExport`
around the export.

**`slice_model`**

| Arg | Type | Meaning |
|---|---|---|
| `names` | array[string] | Objects to export and slice (default: selection, else all solids) |
| `path` | string | An existing `.3mf`/`.stl` to slice instead of exporting |
| `machine` | string | Machine preset name or absolute path (default: `SlicerMachine`) |
| `process` | string | Process preset (default: `SlicerProcess`) |
| `filament` | string | Filament preset (default: `SlicerFilament`) |
| `arrange` | boolean | Let the slicer arrange the plate (default true) |
| `copies` | integer | `--repetitions` (default 1) |
| `note` | string | Free text stored in `job.json` |

Returns immediately with the job id, the job folder, the argv actually used, and
an instruction to call `read_slice_result`. Returning the argv is what makes a
failure diagnosable without re-running. `precheck` validates argument shape only:
not both `names` and `path`, `copies >= 1`, `path` extension in `{3mf, stl}`.

With no preset configured and none passed, it refuses and returns the discovered
lists — binary found, machine/process/filament preset names — plus the preference
names to set. The note is then useful without a follow-up call.

**`read_slice_result`**

| Arg | Type | Meaning |
|---|---|---|
| `job` | string | Default: the most recent job |
| `wait_seconds` | integer | 0 = poll only; default 120, max 480 |

On success: G-code paths, layer count, estimated time and per-feature breakdown,
filament usage, and the placement-offset line. On failure: `result.json`'s
`error_string` plus the tail of `slicer.log`.

**`view_gcode`**

| Arg | Type | Meaning |
|---|---|---|
| `job` | string | Default: the most recent successful job |
| `path` | string | A specific `.gcode`/`.gcode.3mf` to view instead |

Starts `gcode_server` if it is not running, publishes, opens the browser, returns
the URL, and states that the picture is for the user.

### Preferences

Under `session.PARAM_PATH`, following `SaveSteps` / `AnnotateEditor` /
`DeviceIdleMinutes`. None hardcode a machine; all are discoverable when unset.

| Key | Type | Meaning |
|---|---|---|
| `SlicerPath` | string | The slicer binary. Empty → auto-discover. |
| `SlicerProfileDirs` | string | Extra profile roots, `os.pathsep`-joined. Empty → auto-discover. |
| `SlicerMachine` / `SlicerProcess` / `SlicerFilament` | string | Chosen preset names. |
| `SlicerArrange` | bool | Default for `arrange` (default true). |
| `GcodeUiDir` | string | Override `gcode_ui/` — the dev hook for pointing at a Vite build. |

Discovery candidates, all read-only filesystem probes handed in to
`slicer_runner`: macOS `/Applications/{BambuStudio,OrcaSlicer}.app/Contents/MacOS/*`;
Windows `%PROGRAMFILES%\Bambu Studio\bambu-studio.exe` and the Orca equivalent;
Linux the binary on `PATH` plus the common AppImage locations. User presets:
`~/Library/Application Support/BambuStudio/user/*/{machine,process,filament}/*.json`,
`%APPDATA%\BambuStudio\user\...`, `~/.config/BambuStudio/user/...`.

## The forks, priced

### 1. How the viewer is served and opened

**(a) `file://` straight off disk.** Zero server, and blocked rather than merely
worse: the viewer constructs its workers as
`new Worker(new URL('../parser/parser.worker.ts', import.meta.url), {type:'module'})`,
and browsers refuse module workers from a `file://` origin, along with `fetch` of
a sibling file. The workers keep a large parse off the UI thread and transfer
typed buffers zero-copy, so taking this option means rewriting the parser to run
inline.

**(b) Extend `device_server.py`** with a second static root and a
`/api/gcode/<id>` route. The LAN listener would have to be running for a
local-only feature, or `device_server` grows a bind parameter, a second static
root and a second token. That spends the module's one-sentence security story,
which is its main asset. Reversibility is high — routes are additive — but the
invariant is what is being spent.

**(c) A new sibling `gcode_server.py` on `127.0.0.1`, started by the tool.**
About 150 lines, most of it static serving and auth that should be shared rather
than copied: `device_server._resolve_static` is hardcoded to module-level
`UI_DIR` at line 615, so it needs a `root=UI_DIR` parameter, imported along with
`_TYPES`/`_content_type` or lifted into a small `web_static.py`. Reversibility
high. What it proves: a loopback listener needs no button, no QR and no LAN
exposure, so a tool may start it without making a security decision for the user.

**(d) A Qt WebEngine panel inside FreeCAD.** FreeCAD 1.1 ships `Mod/Web`, so a
WebEngine view is probably available — but availability varies by build and
platform, WebGL support in the embedded view is unverified, and a crash there
takes FreeCAD down with any unsaved work. It also puts a 3D-intensive canvas in
the same process as FreeCAD's own OpenGL context. Additive later; the server
stays the transport either way.

**Recommend (c).** Keep a token even on loopback, as `gui_bridge` does. No idle
timeout in v1; stop on `aboutToQuit`. Open the browser with `Popen`, never `run`,
following `tools_annotate._open_for_editing`.

### 2. How the viewer comes along

**(a) Vendored as committed build output** in `gcode_ui/`, source in
`gcode_web/`. About 1.15 MB of committed minified assets: 20× today's
`device_ui/`, about 2% of the repo. The real cost is review rather than bytes,
since nobody reads a 1.1 MB minified diff where the current 52 KB is at least
skimmable. Two things make that acceptable: the no-hash fixed-name contract means
an unchanged rebuild produces no diff at all, and `gcode_web/VENDORED.md` records
the upstream commit so the source diff is the reviewable artifact.

**(b) Reference a separate checkout** via a preference pointing at the user's own
`dist/`. Near-zero repo size, but the feature does not exist for anyone
installing from `main` — the argument `device_ui/` already settled.

**(c) Qt WebEngine** — see fork 1(d).

**(d) Rewrite a minimal viewer** with three.js only, no React and no r3f. Would
save perhaps 400 KB, at the cost of throwing away a working project, and the
request is explicitly not a redesign. The cheap trim inside it does not exist
either: `@react-three/drei` supplies `OrbitControls`, `Grid`, both cameras and
`Line` across four components, so dropping it is a scene rewrite.

**Recommend (a), and drop `public/samples/`** — 4.1 MB backing a "Load sample"
button with no job here, since the addon always has real G-code. That costs the
only way to smoke-test the page with no FreeCAD, so remove the button in a
three-line patch recorded in `VENDORED.md` rather than leave one that errors.
**Also ship (b) as the dev hook:** `GcodeUiDir` overriding `gcode_ui/` is about
five lines and makes iterating against a Vite dev build pleasant.

The build config is real, named work. `web/vite.config.ts` specifies
`base: "./"`, `inlineDynamicImports`, `cssCodeSplit: false`, `assetsInlineLimit`
1 MB and fixed asset names with no content hashes. The upstream build violates
the last two: every name carries a hash, and the three workers are separate
Rollup entry points. Vite builds workers in their own pass configured by
`config.worker`, so the shape is:

```ts
build: { rollupOptions: { output: { inlineDynamicImports: true,
         entryFileNames: "assets/app.js", chunkFileNames: "assets/app.js",
         assetFileNames: "assets/app.[ext]" } } },
worker: { format: "es", rollupOptions: { output: {
         entryFileNames: "assets/[name].js", chunkFileNames: "assets/[name].js" } } },
```

giving `assets/app.js` plus `assets/parser.worker.js`,
`assets/prediction.worker.js` and `assets/mesh.worker.js`. Whether
`inlineDynamicImports` survives alongside the worker pass is what M4's test
checks. `worker.format: "iife"` is the fallback if module workers misbehave;
every current desktop browser supports them and this viewer is desktop-only.

### 3. Sync vs async slicing

**(a) Blocking `subprocess.run` on the GUI thread.** 20 s minimum on a trivial
part, unbounded on a real one — the failure `_heavy_loop_note` exists to prevent.
Reject.

**(b) One `slice_model` call waiting in a nested `QEventLoop`.** Nothing extra to
build, and FreeCAD stays alive. But a slice outrunning 600 s trips
`GuiBusyTimeout`, whose message would then be false, and there is nothing to
resume from.

**(c) Async start plus a read tool.** A job table and a second tool.
Reversibility high. What it proves: the same two-halves shape the codebase
already uses whenever a human or an external process sits in the middle.

**Recommend (c), with (b)'s mechanism inside the read half** — the hybrid under
Architecture. Async start is the contract; the bounded nested loop is a
convenience in `read_slice_result` that saves a polling chatter loop, and it
degrades to a plain poll at `wait_seconds: 0`.

### 4. The Python correction pipeline

**Out of scope for v1.** Including it means numpy, scipy, shapely, trimesh and
rtree inside FreeCAD's bundled interpreter. The addon's premise is that there is
nothing to install — `install_deps.ps1` only verifies the `claude` CLI — and
shapely and rtree are C extensions, so this is the `vendor/` problem at a larger
scale. Beyond packaging, the correction model is only meaningful with a
`CalibrationProfile` fitted per (printer, nozzle, filament, layer height) from
physical measurements, which this workflow does not have.

What it would buy is the predicted-real-edge and deviation-coloured-surface
overlays, which are the interesting part of the upstream project. The viewer's
overlay components come along in the bundle for free and are simply never fed, so
a later phase only has to produce `*.geojson[.gz]` and `*.mesh.json[.gz]` and
hand them to `view_gcode`. Do not delete them from the vendored copy.

### 5. Preset selection and discovery

**(a) Hardcode the P2S/PLA paths.** Reject; works on one machine.

**(b) Discover and select by name.** Scan the system and user profile roots,
match on preset name, store the choice in a preference. A directory walk plus
filename parsing, all pure over paths handed in.

**(c) Require full JSON paths per call.** Friction every call, but it is the
escape hatch when discovery misses — a custom profile root, or a preset the
naming scheme does not match.

**Recommend (b) with (c) as an escape hatch**: `machine`/`process`/`filament`
accept either a discovered name or an absolute path. Discovery is
`slicer_runner.discover_presets(profile_dirs)`, a pure function; `tools_slice`
reads `SlicerProfileDirs` and hands the directories in, the same discipline as
`device_server.start(upload_dir=...)` and for the same reason — the runner's
thread must not read a FreeCAD preference.

The argv builder keys off the binary's self-identified name so an OrcaSlicer path
does not silently get Bambu-only flags. `--load-settings` joins machine and
process with `;`, which is verified on macOS and unverified on Windows, where `;`
is also the path separator.

No `--datadir` in v1: the verified command did not need one, and a scratch
datadir may break preset `inherits` resolution. That leaves the risk of a
concurrently running Bambu Studio holding config state.

Deferred: a `slicer_presets` probe tool. `slice_model`'s no-preset failure path
carries the same lists, so a registry slot buys only the standalone question.

### 6. Auto-arrange, and the coordinate offset

**(a) Accept auto-arrange** (`--arrange 1`, which is what happened implicitly).
The viewer frames correctly off `bounds`; every toolpath coordinate is offset from
the FreeCAD model by wherever the slicer put the part. The offset has to be
reported, or a user comparing the two quietly concludes something is wrong.
`result.json`'s placed per-object bboxes minus the world bbox `slice_model`
recorded at export time gives the delta — the same record-it-when-you-took-it
discipline as `_last_annotation["context"]`. Reversibility is total; it is one
flag.

**(b) `--arrange 0`.** Coordinates match FreeCAD exactly. But the Bambu plate
origin is a corner, so a part modelled at the FreeCAD origin lands at the plate
corner and may sit partly off the bed; `--ensure-on-bed` would then move it
anyway, reintroducing an offset without the arrange step's sanity.

**(c) Pre-translate to plate centre in FreeCAD, then `--arrange 0`.** The offset
becomes known by construction. Needs `printable_area` read out of the machine
preset JSON and a translation applied to the exported mesh rather than the
document. Real work, worth it only once someone wants to cross-reference
coordinates.

**Recommend (a) for v1**, with `arrange` as an argument so (b) is one call away,
and the offset stated in `read_slice_result`'s text: the slicer placed the parts
at plate centre, so toolpath X/Y are plate coordinates offset about
(+100, +100) mm from the model. Note (c) as the upgrade.

### 7. Tessellation control on the 3MF

Resolved by measurement — see the table under Verified feasibility.
`MaxDeviationExport` is read by `Mesh.export`, so the save/set/restore shape
`capture_user_view` uses for `SavePicture` gives a `deviation` argument for about
ten lines. It only refines; coarsening past about 0.05 mm has no effect because
angular deflection floors it. Default stays FreeCAD's 0.1.

`MeshPart.meshFromShape(LinearDeflection=..., AngularDeflection=...)` into a
hidden scratch document remains the escape hatch if per-object control or
angular control turns out to matter. It is more code and needs the scratch
document precisely so the user's document is not mutated.

## Milestones

Ordered by dependency, then size. Each leaves the addon working.

```
  M1 export 3mf ──┐
                  ├── M3 tools ── M6 autoload ── M7 hardening
  M2 runner ──────┘        │            │
                           │      M5 gcode_server
                     M0 parser guard    │
                                   M4 vendored build
```

### M0 — The parser guard · size S · permanent

Copy the verified rig into `gcode_web/tests/freecad-roundtrip.test.ts`: parse a
committed fixture G-code with `parseAndBuild`, assert `unknownFeatureStrings` is
empty and `layerCount > 0`. Fixture is a trimmed or gzipped `plate_1.gcode`,
roughly 30–60 KB.

A permanent contract deserving a full selftest: it guards the seam most likely to
rot when Bambu Studio changes its dialect, and a new `; FEATURE:` string renders
magenta and otherwise gets noticed months later. Runs under a plain
`npx vitest run` in `gcode_web/` with no FreeCAD.

**Test:** the test is the milestone.

### M1 — `export` handles 3MF, on the record · size S · permanent

Schema description gains 3MF and the separate-objects property, plus the
`deviation` argument.

**Test:** `eval/test_export_3mf.py` under `freecadcmd` — build a Box and a
Cylinder, export `.3mf`, assert the zip's `3D/3dmodel.model` holds two
`<object>` and two `<item>` elements, and assert a finer `deviation` raises the
triangle count. Permanent: those are properties of FreeCAD's exporter, not of our
code, so they can change under us.

### M2 — `slicer_runner.py`, no tools yet · size M · permanent

Binary and preset discovery, the argv builder, the job table, the daemon thread,
the subprocess timeout and kill, `set_done_hook`.

**Test:** `eval/test_slicer_runner.py` under a bare `python3` — discovery over a
synthetic profiles tree; the argv pinned, especially the `;`-joined
`--load-settings`; job lifecycle against a fake binary (`python3 -c "..."`) that
writes a `result.json`; the timeout path killing a sleeper. Permanent, mirroring
`eval/test_device_server.py`.

### M3 — The tools · size L · mixed

`tools_slice.py` with `slice_model` and `read_slice_result`, registered in
`freecad_tools/__init__.py`; `_resolve_export_objects` factored out of
`tools_export`; `session._session_job_dir`; the `aboutToQuit` terminate hook and
the `slice_finished` transcript note in `chat_panel`.

**Test:** the pure parts get a `freecadcmd` script — preset resolution, the
`result.json` summariser and the placement-offset arithmetic, as functions over a
recorded `result.json`. The nested-loop wait needs Qt and a live turn, so it is a
throwaway manual check in real FreeCAD: slice a real part, watch the application
stay responsive, confirm `read_slice_result` returns without a `GuiBusyTimeout`.

### M4 — The vendored build, under the no-hash contract · size M · permanent

`gcode_web/` populated from the upstream repo at a recorded commit,
`public/samples/` dropped, "Load sample" removed, `vite.config.ts` replaced per
fork 2. Deploy and ignore wiring: the `deploy.sh`/`deploy.ps1` exclude lists gain
`gcode_web`; `.gitignore` gains `gcode_web/node_modules/` and `gcode_web/dist/`
and must not ignore `freecad/freecadclaude/gcode_ui/`.

**Test:** `npm ci && npm run build` twice, then
`git diff --exit-code freecad/freecadclaude/gcode_ui` — an empty diff. Also
confirm the three workers emitted at fixed names and that the page loads them.
A permanent contract, but it belongs in `RELEASE.md` as a documented command
rather than a test file, as the `device_ui/` rebuild step already is.

### M5 — `gcode_server.py` and `view_gcode` · size M · permanent

Loopback server, token, `publish(path)` minting an id, `GET /api/gcode/<id>`,
static serving from `gcode_ui/` or `GcodeUiDir`, `aboutToQuit` stop. The
`view_gcode` tool opens the browser.

**Test:** `eval/test_gcode_server.py` under a bare `python3` — token rejection,
path-traversal rejection, a known asset served, unknown-id 404. Permanent, same
shape and reasons as `eval/test_device_server.py`.

### M6 — The autoload seam · size S · throwaway rig

The single patch to the vendored source: `useGcodeFile` gains
`loadUrl(url, name)`, which is `loadSample` with the URL parameterised, and
`App.tsx` reads `gcode` from `location.search` on mount and calls it. Same-origin
`fetch` carries the cookie the `?t=` page load set, so no token header is needed.
About fifteen lines, recorded in `VENDORED.md`.

**Test:** one vitest on the query-parameter helper; the rest is a throwaway
manual check in the browser, which is where a render can be judged.

### M7 — Hardening and docs · size M

Error paths as messages rather than tracebacks: no slicer binary, no preset,
slicer missing a profile, slice failed, no G-code written, port unavailable,
viewer folder absent.

`RELEASE.md` gains the `gcode_web` rebuild step beside the `web` one and says
there are now two committed build outputs. `README.md` and `SECURITY.md` gain the
loopback listener and the fact that the addon launches an external slicer
subprocess. `CLAUDE.md` gains the module-map rows, the three tools, the `slices/`
artifact subdir, the preference list, and the `gcode_web/` → `gcode_ui/`
relationship.

## Open questions

1. **Which presets are the default when discovery finds several?** Proposed: none
   — refuse and report, so the first slice is an explicit choice. Guessing the
   single P2S machine when exactly one is found is friendlier and occasionally
   wrong.
2. **One plate or all?** `--slice 0` writes every plate. v1 publishes plate 1 and
   reports the others' paths; loading a chosen one is a `?gcode=<id>` away.
3. **Should `view_gcode` accept `.gcode.3mf`?** The viewer handles it via fflate,
   and `--export-3mf` can emit an arranged project, which doubles as an "open this
   in Bambu Studio" artifact. That flag's ordering relative to `--slice` is
   unverified.
4. **Report the placement offset, or remove it** (fork 6c)? Depends on whether
   coordinates will be cross-referenced.
5. **Does OrcaSlicer need to work too**, or is Bambu Studio the target? It sets
   how defensive the argv builder must be.

## Risks, with what would falsify each

1. **A large model makes the export or the slice much slower than measured.** The
   export is on the GUI thread; the slice is not. Falsified by exporting and
   slicing the 68-object eval document and timing the `Mesh.export` call alone. A
   slow export needs the same async treatment as the slice, which changes
   `slice_model`'s shape.
2. **`inlineDynamicImports` and the worker pass do not coexist**, so the no-hash
   contract and the workers cannot both be had. Falsified by M4's build. Fallback:
   drop `inlineDynamicImports` and pin `chunkFileNames`, accepting a handful of
   stable chunk files.
3. **A running Bambu Studio conflicts with the CLI** over config state or a lock.
   Falsified by running a slice with Studio open. Fallback is `--datadir`, which
   then has to be shown not to break preset inheritance.
4. **The `;` preset separator differs on Windows.** Falsified by one slice on
   Windows. Cheap to fix, easy not to notice until a Windows user reports a slice
   that ignored the process preset.
5. **Module workers or WebGL misbehave in the user's default browser.** Falsified
   by opening the page in each installed browser. Fallbacks are
   `worker.format: "iife"` and, at the far end, fork 1(d).
6. **The committed 1.1 MB bundle is judged too heavy after all.** Falsified by
   the first diff. The reversal is fork 2(b) plus a documented build step, which
   demotes the feature rather than rewriting it.
