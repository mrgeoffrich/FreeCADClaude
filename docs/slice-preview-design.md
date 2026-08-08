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
   already does, **rotates each one onto its recorded `PrintDirection`**, writes
   `<session>/slices/<job>/model.3mf` with `Mesh.export`, builds the slicer argv
   from **Bambu Studio's own currently-selected presets**, starts the CLI on a
   daemon thread, and returns immediately with a job id. FreeCAD keeps repainting.
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
- Print-direction rotation: each part exported the way up it is printed.
- Slicer binary and preset discovery, reading Studio's own selection, 0.4 nozzle.
- A slicer settings panel in the served page, backed by `~/FreeCADClaude/slicer.json`.
- A loopback HTTP server serving the vendored viewer and the G-code.
- The viewer's G-code half, vendored as committed build output.

**Out, for v1:**

- The `dimensioner` Python correction pipeline, and the predicted-edge and
  deviation-surface overlays it feeds. See fork 4.
- Claude seeing the toolpath as an image.
- Choosing among multiple plates in the viewer.
- Removing the slicer's placement offset. See fork 6.
- Per-part process overrides (a different layer height for one part).
- Support-material or paint-on-support decisions.

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

**Studio's current selection is readable, so the printer choice does not have to
be configured twice.** `~/Library/Application Support/BambuStudio/BambuStudio.conf`
is JSON, and its `presets` block holds the live GUI selection:

```json
"presets": { "machine":   "Bambu Lab P2S 0.4 nozzle",
             "process":   "0.20mm Standard @BBL P2S",
             "filaments": ["Bambu PLA Matte @BBL P2S", "Bambu PLA Matte @BBL P2S"] }
```

All three names resolved to real preset JSONs by globbing the user roots first
and the system roots second, which is Studio's own precedence. `filaments` is one
entry per extruder or AMS slot, so a single-material slice takes `filaments[0]`.
The same file's `models` array lists the printers the user has added
(`Bambu Lab P1S`, `Bambu Lab P2S` here, each with its nozzle sizes), which is the
list to offer when the selection needs confirming.

Two smaller notes on the same file: it is Studio's live config, so it is
read-only for us and may be mid-write while Studio is running — a parse failure
has to degrade to the preferences rather than raise. And the selection is
whatever the user last had open in Studio, which is why the nozzle is pinned
rather than inherited.

**The nozzle default is 0.4, and the preset JSONs make that exact.** Nozzle size
is a field, not a name pattern, and each machine preset declares its own defaults:

```
Bambu Lab P2S 0.4 nozzle   nozzle_diameter ['0.4']   inherits fdm_bbl_3dp_001_common
    default_print_profile     "0.20mm Standard @BBL P2S"
    default_filament_profile  ["Bambu PLA Basic @BBL P2S"]
Bambu Lab P2S 0.2 nozzle   nozzle_diameter ['0.2']   inherits Bambu Lab P2S 0.4 nozzle
    default_print_profile     "0.10mm Standard @BBL P2S 0.2 nozzle"
```

Three facts follow, all verified:

- **0.4 is the vendor's own canonical base**, not just our preference. The 0.2,
  0.6 and 0.8 presets all `inherit` from the 0.4 one, and the model-level
  `Bambu Lab P2S.json` lists `nozzle_diameter` as `0.4;0.2;0.6;0.8` — 0.4 first.
- **Process and filament compatibility is declared, so it never has to be parsed
  out of a name.** Every process and filament preset carries
  `compatible_printers`, e.g. `0.20mm Standard @BBL P2S` →
  `['Bambu Lab P2S 0.4 nozzle']`. Of the 16 P2S process presets, 7 are compatible
  with the 0.4 machine. The unsuffixed name happening to mean 0.4 is a convention
  that holds today; `compatible_printers` is the ground truth to key off.
- **Changing nozzle is not a one-field change.** A Studio session left on 0.2
  selects a 0.2 machine *and* a 0.2 process *and* a 0.2 filament. Snapping only
  the machine to 0.4 leaves an incompatible process and filament, so all three are
  re-derived together.

So resolving Studio's selection is: take the printer model, pick its machine
preset whose `nozzle_diameter` is 0.4, then keep Studio's process and filament
only if their `compatible_printers` includes that machine — otherwise fall back to
the machine's declared `default_print_profile` and `default_filament_profile`.
Report when a fallback happened, because "I had it on 0.2 in Studio" is then the
obvious next question.

The model-level JSON also carries `default_materials`, an ordered preference list
(`Bambu PLA Matte @BBL P2S` first here), which is the right order for a filament
dropdown.

One cosmetic gap: `result.json` reported `filament_id: ""` and `main_used_g: 0.0`
with a bare system filament preset, so filament mass did not resolve. Reading the
selection above uses the same system JSONs, so this is unlikely to be what fixes
it.

**`result.json`'s object bounding boxes are not trustworthy as dimensions.** For a
16 mm cylinder whose exported 3MF vertices measure exactly 16.000 mm, the slicer
reported 17.2 mm; the two boxes on the same plate reported exactly. Whatever
inflates a curved object's reported footprint is unconfirmed, and it is not our
export — the 3MF geometry was checked vertex by vertex. Placement offset is
computed from the bbox **centre**, which a symmetric inflation leaves intact, so
fork 6 survives this; treating the reported sizes as measurements would not.

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

### Print-direction rotation

The addon already records which way up each part is printed.
`print_meta.AXIS_VECTORS` maps `+Z up` / `-X up` / ... to the part-local axis that
points up in world space once the part is on the plate, `set_print_direction`
writes it, and `get_objects` reports it. Nothing acted on it until now: a part
recorded as `-Z up` still exported the way it was modelled, so the slice bore no
relation to how the part gets printed.

It lives in `freecad_tools/print_export.py`, infra beside `print_meta.py`: that
module records the direction, this one acts on it. Infra rather than a `tools_*`
module because the slice tools import it, and dependencies run tools → infra only.
`print_meta.up_vector(obj)` is the accessor it needs — the enum value plus the
part-local unit up vector, `None` when there is no axis to rotate onto.

**The local axis has to be carried through the object's own Placement.** The mesh
comes off `obj.Shape`, which already has the Placement baked in, so the recorded
axis names a direction in the part's frame and not the mesh's:

```python
axis = obj.Placement.Rotation.multVec(FreeCAD.Vector(*up))   # into the mesh's frame
rotation = FreeCAD.Rotation(axis, FreeCAD.Vector(0, 0, 1))
```

Skip the `multVec` and `+Z up` stops meaning "as modelled" for any part whose
Placement is rotated — a bar placed on its side exports still lying down. Identity
placements are unaffected, which is why a spike on freshly-created primitives
agrees either way.

**The rotation must not touch the user's document.** Rotating the real objects
would mutate placements the user owns, and it would recompute anything downstream.
So the export runs through a scratch document: mesh each shape with
`MeshPart.meshFromShape`, apply the rotation to the **mesh** with
`mesh.transform(placement.Matrix)`, translate it down onto the plate, add it as a
`Mesh::Feature` in a hidden scratch document, `Mesh.export` all of them to one
3MF, and close the scratch document. This is also the fork 7(c) escape hatch
arriving early: once the meshing is explicit, per-object deflection comes free.

Verified end to end. Three parts — a wedge at `+Z up`, a 16×16×30 cylinder at
`-Z up`, and a 60×10×10 bar at `+X up` — were rotated, stacked deliberately at the
origin, exported as one 3MF and sliced:

```
Overhang  +Z up   modelled 10x10x10 -> printed 10x10x10
Bore      -Z up   modelled 16x16x30 -> printed 16x16x30
Bar       +X up   modelled 60x10x10 -> printed 10x10x60   <- stood on its end
```

The G-code has **300 layers topping out at `; Z_HEIGHT: 60`**, which is the bar's
rotated height, so the rotation survived to the toolpath. The slicer separated all
three from the origin onto distinct plate positions, so **rotating ourselves and
letting the slicer arrange is a clean division**: orientation is a design decision
the document already records, and layout is packing the slicer does better.

**A bounding box does not catch a sign error.** A rotation and its inverse both
stand a part's axis vertical, so a 60x10x10 bar at `+X up` measures 10x10x60 either
way — and so does a symmetric part at `-Z up`, both directions being 180 degree
flips. Distinguishing them needs a part that is asymmetric *along* the rotated
axis, e.g. a taper, asserted on which end is at the bottom. Confirmed by mutation:
inverting the rotation passes every bbox assertion and fails only the taper.

How each enum value is treated:

| `PrintDirection` | Behaviour |
|---|---|
| `+Z up` | Exported as modelled. No rotation. |
| `-Z up`, `±X up`, `±Y up` | Rotated so that local axis points to world +Z. |
| `Custom` | Rotated using `PrintDirectionCustom`, via the same `Rotation`. |
| `Not set` | Exported as modelled, and **named in the returned text**. |
| `Not printed` | Left off the plate entirely. |

`Not set` reporting matters more than it looks: silently printing an unset part as
modelled is right about as often as it is wrong, and the tool result is where the
user finds out which parts nobody has decided about. `Not printed` is the other
half of the same argument — a jig or a reference body should not consume plate, and
that holds at `orient=False` too, since whether a part is printed at all is a
different decision from which way up.

Two further behaviours the enum table does not cover. Rotation implies the drop to
the plate, so every rotated part lands with its minimum corner at the origin and
the slicer separates them. `orient=False` leaves modelled coordinates completely
alone, including position — which is what comparing against a hand-made Studio
slice wants. And a `Custom` direction whose vector is missing or zero-length gets
no rotation; it shows in the report as `Custom` with `rotated: false` rather than
being silently treated as `+Z up`.

**A 3MF `<object>` carries an `id` and no name.** Report order is therefore the
only mapping from parts to file objects, which is what fork 6's placement-offset
arithmetic has to index against in `result.json`.

**`Mesh.export` to an unwritable path aborts the process, not the call.** The
failure escapes Python entirely — neither `except Exception` nor `except
BaseException` sees it, and under `freecadcmd` the script stops dead with
`[No write permission for file]`. A `finally` still runs, so the scratch document
is still closed, but no error message can be produced at that site. **The write
path must be checked before the call**, since inside FreeCAD this runs on the GUI
thread.

A rotation invalidates the `set_print_direction` payload's `print_plate_side` only
in the sense that it is now literally true: after rotation the plate side is world
−Z for every part, which is what `DIRECTION_NOTE` describes.

### Slicer configuration in the page

Presets resolve without configuration on a machine with Studio set up, but three
things still need a place to be chosen: which printer, when the user has more than
one; which process, since "0.20mm Standard" is a default and not a decision; and
whether to orient and arrange. A settings panel in the served page is the natural
home — it is already open on a desktop, it can show the real preset lists, and it
needs no FreeCAD dialog.

**A JSON config file is the seam, not a hook.** `gcode_server.py` imports no
FreeCAD and no Qt, so it cannot write a FreeCAD preference. The device server
solves its equivalent problem with `set_upload_hook` firing onto the GUI thread,
but a write-back hook is the wrong shape here: the settings are not per-session,
FreeCAD is not necessarily mid-turn when the user changes them, and the value has
to survive a restart. So the page reads and writes `~/FreeCADClaude/slicer.json`
directly, and `slicer_runner` — already stdlib-only — reads the same file when
building the argv. No FreeCAD involvement on either side, and the invariant holds
without a new mechanism.

```json
{ "printer":  "Bambu Lab P2S",
  "nozzle":   "0.4",
  "machine":  "Bambu Lab P2S 0.4 nozzle",
  "process":  "0.20mm Standard @BBL P2S",
  "filament": "Bambu PLA Matte @BBL P2S",
  "orient": true, "arrange": true, "deviation": 0.1 }
```

It sits outside the session folders, beside `sketches/`, because a printer choice
outlives a conversation and must not be pruned. `machine` is stored resolved as
well as by printer-and-nozzle so a Studio rename shows up as a stale-name warning
rather than a silent substitution.

This inserts one level into the resolution order, above Studio's ambient
selection: **explicit tool argument → `slicer.json` → Studio's selection snapped
to 0.4 → `Slicer*` preferences.** An explicit choice in the page should beat
whatever Studio happens to be set to; the preferences stay as the last resort.

Two HTTP routes, both token-gated like everything else on the server:

| Route | Returns |
|---|---|
| `GET /api/slicer/options` | The user's printers from `BambuStudio.conf`'s `models`, the nozzle sizes each supports, and — filtered by `compatible_printers` for the currently chosen machine — the process and filament names, with the machine's declared defaults marked. Internal base profiles are excluded by `instantiation: "false"`: `compatible_printers` alone offers 92 processes on this install, 45 of them scaffolding like `fdm_process_common`. |
| `GET`/`PUT /api/slicer/config` | Read and replace `slicer.json`. `PUT` validates every name against the discovered lists and rejects an unknown one, so a stale page cannot write a preset that will fail at slice time. |

`PUT` validating against the discovered lists is the part worth not skipping. The
alternative is discovering the mistake as a slicer failure several minutes later,
with the argv as the only clue.

**The panel has to work with no G-code loaded**, since configuring the printer
comes before the first slice. So the page renders the settings drawer whether or
not `?gcode=` was supplied, and `view_gcode` may be called with no job to open it
for configuration alone. That is also the honest answer to "how do I change this
without asking Claude": the button in the chat panel opens the same page.

Whether the panel is a drawer over the viewer or a separate route is a UI decision
for when it is built. A drawer keeps one page and one bundle; a separate route
loads faster when there is nothing to render. Lean drawer, since the bundle is
loaded either way.

### Modules

| New file | Role |
|---|---|
| `freecad/freecadclaude/freecad_tools/print_export.py` | Infra beside `print_meta.py`. `oriented_export(objs, path, deviation, orient)` — mesh, rotate onto the recorded direction, drop to the plate, one multi-object 3MF via a scratch document, and a structured report. |
| `freecad/freecadclaude/freecad_tools/tools_slice.py` | `slice_model`, `read_slice_result`, `view_gcode`. GUI-thread work: object resolution, the oriented export, preference reads, preset resolution, `result.json` summarising, the bounded wait. |
| `freecad/freecadclaude/slicer_runner.py` | Stdlib only, no FreeCAD, no Qt. Job table, daemon thread, subprocess, argv builder, and discovery as pure functions over paths handed in. Sibling of `device_server.py`, testable under a bare `python3`. |
| `freecad/freecadclaude/gcode_server.py` | Stdlib only. `127.0.0.1` static server for `gcode_ui/`, plus `GET /api/gcode/<id>` and the `/api/slicer/{options,config}` routes. Token-gated. |
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
| `machine` | string | Machine preset name or absolute path (default: Studio's selection, else `SlicerMachine`) |
| `process` | string | Process preset (same resolution order) |
| `filament` | string | Filament preset (same resolution order) |
| `orient` | boolean | Rotate each part onto its recorded `PrintDirection` (default true) |
| `arrange` | boolean | Let the slicer arrange the plate (default true) |
| `deviation` | number | Mesh deviation in mm for the export (default: FreeCAD's 0.1) |
| `copies` | integer | `--repetitions` (default 1) |
| `note` | string | Free text stored in `job.json` |

Returns immediately with the job id, the job folder, the presets and where they
came from, the per-part rotation applied, any part left off the plate, and the argv
actually used. Returning the argv is what makes a failure diagnosable without
re-running. `precheck` validates argument shape only: not both `names` and `path`,
`copies >= 1`, `path` extension in `{3mf, stl}`.

`orient: false` exports as modelled, which is what to reach for when comparing
against a slice made by hand in Studio.

Preset resolution order, most specific first: an explicit argument, then Studio's
`BambuStudio.conf` selection, then the `Slicer*` preferences. If all three miss, it
refuses and returns the discovered lists — binary found, the `models` array, and
the available preset names — plus the preference names to set, so the note is
useful without a follow-up call.

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
the URL, and states that the picture is for the user. With no job and no successful
slice to fall back on, it opens the page anyway for the settings panel rather than
refusing.

### Preferences

Under `session.PARAM_PATH`, following `SaveSteps` / `AnnotateEditor` /
`DeviceIdleMinutes`. None hardcode a machine, and on a system with Studio set up
none needs setting at all: the preset keys are the fallback for when
`BambuStudio.conf` is missing or unreadable.

| Key | Type | Meaning |
|---|---|---|
| `SlicerPath` | string | The slicer binary. Empty → auto-discover. |
| `SlicerConfPath` | string | Studio's `BambuStudio.conf`. Empty → auto-discover. |
| `SlicerNozzle` | string | Nozzle to pin when Studio's selection is used (default `0.4`). |
| `SlicerProfileDirs` | string | Extra profile roots, `os.pathsep`-joined. Empty → auto-discover. |
| `SlicerMachine` / `SlicerProcess` / `SlicerFilament` | string | Preset names, used when Studio's selection is unreadable. |
| `SlicerArrange` | bool | Default for `arrange` (default true). |
| `SlicerOrient` | bool | Default for `orient` (default true). |
| `GcodeUiDir` | string | Override `gcode_ui/` — the dev hook for pointing at a Vite build. |

**`discover_profile_dirs` needs the binary path**, since the system root is
derived from it (`<resources>/profiles/<vendor>`). Omit it and the call returns the
user roots alone, which indexes a plausible-looking subset — user presets only, no
system machine presets — and resolution then fails with the wrong printer rather
than an error. Callers pass it. This install has three roots: `user/<id>`,
`user/default`, and the system vendor tree.

Discovery candidates, all read-only filesystem probes handed in to
`slicer_runner`: macOS `/Applications/{BambuStudio,OrcaSlicer}.app/Contents/MacOS/*`;
Windows `%PROGRAMFILES%\Bambu Studio\bambu-studio.exe` and the Orca equivalent;
Linux the binary on `PATH` plus the common AppImage locations. Config and user
presets, per platform:

| | Config | User presets |
|---|---|---|
| macOS | `~/Library/Application Support/BambuStudio/BambuStudio.conf` | `.../user/*/{machine,process,filament}/**/*.json` |
| Windows | `%APPDATA%\BambuStudio\BambuStudio.conf` | `%APPDATA%\BambuStudio\user\...` |
| Linux | `~/.config/BambuStudio/BambuStudio.conf` | `~/.config/BambuStudio/user/...` |

Only the macOS paths are verified. The user preset glob needs `recursive=True` and
a `**`, because Studio nests them a level deeper (`user/<id>/filament/base/...`).

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

**(b) Discover and select by name**, storing the choice in a preference. A
directory walk plus filename parsing, all pure over paths handed in. Works, but
asks the user to say in FreeCAD what they have already said in Studio, and the two
then drift — change the nozzle in Studio and the addon keeps slicing 0.4.

**(c) Require full JSON paths per call.** Friction every call, but it is the escape
hatch when discovery misses — a custom profile root, or a preset the naming scheme
does not match.

**(d) Read Studio's own current selection** from `BambuStudio.conf`'s `presets`
block and resolve those names against the profile roots. Verified: all three
resolved. Zero configuration for the common case, and it tracks Studio, so a
nozzle change in the GUI is picked up on the next slice. The cost is a dependency
on an undocumented config layout that a Studio update could rename, and a
selection that reflects whatever the user last had open rather than what they want
here.

**(e) A settings panel in the served page**, writing `~/FreeCADClaude/slicer.json`.
Needs the server and the page, both of which are being built anyway, and it is the
only option that lets the user change the printer without going through Claude or
FreeCAD's preference editor. See the configuration section above.

**Recommend (e) over (d) over (b), with (c) as the escape hatch** — the four-level
order under the configuration section. Nothing needs configuring on a machine with
Studio set up, an explicit choice in the page beats Studio's ambient one, the
preferences still work when everything else is missing, and an absolute path always
wins. Report which level supplied each preset in the tool result, because "why did
it slice at 0.2mm" is otherwise unanswerable.

**Whatever level supplies them, the nozzle is pinned to 0.4** unless the page says
otherwise, and process and filament are re-derived against it. See the verified
rule under Verified feasibility.

Resolution is `slicer_runner.resolve_presets(conf_path, profile_dirs, overrides)`,
a pure function over paths handed in — the same discipline as
`device_server.start(upload_dir=...)`, and for the same reason: the runner's thread
must not read a FreeCAD preference. A malformed or half-written `conf` degrades to
the next level rather than raising, since Studio may be running.

**Anything that is not Bambu Studio gets no command line at all.** `build_argv`
raises for an unrecognised binary and for OrcaSlicer alike, naming the path and
pointing at the `SlicerPath` preference; `start_job` propagates the refusal before
it creates a job folder. These are GUI applications, so a flag they do not accept
opens a modal dialog on the user's screen and no error text comes back — there is
nothing to report and nothing to retry against, which makes emitting a guess worse
than refusing. `slicer_flavour` still identifies Orca, and a `flavour="bambu"`
override is the opt-in for a binary someone has verified. `--load-settings` joins machine and
process with `;`, which is verified on macOS and unverified on Windows, where `;`
is also the path separator.

No `--datadir` in v1: the verified command did not need one, and a scratch
datadir may break preset `inherits` resolution. That leaves the risk of a
concurrently running Bambu Studio holding config state.

Deferred: a `slicer_presets` probe tool. `slice_model`'s no-preset failure path
carries the same lists, so a registry slot buys only the standalone question.

### 6. Auto-arrange, and the coordinate offset

Rotation settles the half of this that matters. Orientation is a design decision
the document records, so we apply it; layout is packing, which the slicer does
better. The verified run confirms the division holds — three parts rotated by us
and stacked at the origin came out separated across the plate with their rotations
intact. What remains is only where the parts ended up.

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
`capture_user_view` uses for `SavePicture` would give a `deviation` argument for
about ten lines. It only refines; coarsening past about 0.05 mm has no effect
because angular deflection floors it.

But **print-direction rotation already forces explicit meshing**, so the
`MeshPart.meshFromShape(LinearDeflection=..., AngularDeflection=...)` path is being
built regardless and the preference-juggling is redundant. `deviation` maps
straight to `LinearDeflection`, per-object control comes free, and angular control
is available if it ever matters. Default stays 0.1 to match FreeCAD.

That leaves one case the rotation path does not cover: `orient: false` with no
`deviation`, where nothing needs a scratch document. Meshing explicitly anyway is
the simpler code — one export path, not two — and worth the slightly slower
as-modelled export.

## Milestones

Ordered by dependency, then size. Each leaves the addon working.

```
  M1 export 3mf ── M1b rotation ──┐
                                  ├── M3 tools ─────────────┐
  M2 runner ──────────────────────┘                         ├── M6 autoload ── M7 docs
                                                            │
  M4 vendored build ── M0 parser guard ── M5 server ── M5b settings
```

M0 comes after M4, not before it: the parser guard imports `parseAndBuild` from the
vendored source, so it cannot run until `gcode_web/` exists.

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

Schema description gains 3MF and the separate-objects property.

**Test:** `eval/test_export_3mf.py` under `freecadcmd` — build a Box and a
Cylinder, export `.3mf`, assert the zip's `3D/3dmodel.model` holds two
`<object>` and two `<item>` elements. Permanent: that is a property of FreeCAD's
exporter, not of our code, so it can change under us.

### M1b — Print-direction rotation · size M · permanent

`print_meta.up_vector(obj)`, plus `print_export.oriented_export(objs, path,
deviation, orient)`: mesh, rotate onto the recorded direction through the object's
Placement, drop to Z=0, scratch document, one 3MF, close. Handles `Custom` off
`PrintDirectionCustom`, reports `Not set`, omits `Not printed`.

**Test:** `eval/test_oriented_export.py` under `freecadcmd`, reading per-object
vertex bboxes back out of the 3MF zip. A 60×10×10 bar at `+X up` asserting 10×10×60;
the same bar with a rotated Placement, asserting the carry-through; **a taper
asserting which end is at the bottom**, since the bbox alone passes an inverted
rotation; the user's document unchanged, same `Placement` on every object and none
added; `Not printed` producing no 3MF object; and the scratch document closed on the
exception path. **Permanent** — this is where a wrong rotation silently prints a part
on the wrong face.

### M2 — `slicer_runner.py`, no tools yet · size M · permanent

Binary discovery, preset resolution, the argv builder, the job table, the daemon
thread, the subprocess timeout and kill, `set_done_hook`.

**Test:** `eval/test_slicer_runner.py` under a bare `python3` — resolution over a
synthetic profiles tree and a synthetic `BambuStudio.conf`, including the
three-level order, a user preset beating a system one of the same name, and a
truncated conf degrading rather than raising; the argv pinned, especially the
`;`-joined `--load-settings`; job lifecycle against a fake binary
(`python3 -c "..."`) that writes a `result.json`; the timeout path killing a
sleeper. Permanent, mirroring `eval/test_device_server.py`.

### M3 — The tools · size L · mixed

`tools_slice.py` with `slice_model` and `read_slice_result`, registered in
`freecad_tools/__init__.py`; `_resolve_export_objects` factored out of
`tools_export`; `session._session_job_dir`; the `aboutToQuit` terminate hook and
the `slice_finished` transcript note in `chat_panel`.

**Test:** the pure parts get a `freecadcmd` script — the `result.json` summariser
and the placement-offset arithmetic, as functions over a recorded `result.json`.
The nested-loop wait needs Qt and a live turn, so it is a throwaway manual check in
real FreeCAD: slice a real part, watch the application stay responsive, confirm
`read_slice_result` returns without a `GuiBusyTimeout`.

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

### M5b — The slicer settings panel · size M · mixed

`slicer_runner.discover_options(conf, profile_dirs)` returning printers, nozzles
and the `compatible_printers`-filtered process and filament lists with the declared
defaults marked; the `/api/slicer/{options,config}` routes; `slicer.json` read and
validated write; the drawer in the page.

**Test:** the discovery and validation halves get `eval/test_slicer_runner.py`
cases under a bare `python3` — the 0.4 pin against a synthetic conf set to 0.2, the
`compatible_printers` filter, and a `PUT` of an unknown preset name rejected.
**Permanent**, because the pin and the filter are what stop a slice failing minutes
later. The drawer itself is a throwaway manual check in the browser.

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

1. **Decided: silent, with the nozzle pinned to 0.4.** Studio's selection is used
   without a confirmation prompt, but the nozzle is not inherited from it — the
   machine preset is snapped to 0.4 and process and filament re-derived against it.
   The settings panel is where a different nozzle gets chosen deliberately, and
   every result states which level supplied each preset.
2. **Is a part with `PrintDirection` unset worth refusing over?** Proposed no:
   export as modelled and name it, so a first slice needs no setup. Refusing would
   push the user to record directions before they can see anything.
3. **One plate or all?** `--slice 0` writes every plate. v1 publishes plate 1 and
   reports the others' paths; loading a chosen one is a `?gcode=<id>` away.
4. **Should `view_gcode` accept `.gcode.3mf`?** The viewer handles it via fflate,
   and `--export-3mf` can emit an arranged project, which doubles as an "open this
   in Bambu Studio" artifact. That flag's ordering relative to `--slice` is
   unverified.
5. **Report the placement offset, or remove it** (fork 6c)? Depends on whether
   coordinates will be cross-referenced.
6. **Decided for now: Bambu Studio only.** Orca is identified but refused, since
   its command line is unverified and we will not probe a GUI app to find out.
   Adding it is one entry in the flag table plus someone checking the flags.

## Risks, with what would falsify each

1. **A Bambu Studio update renames or moves the `presets` block in
   `BambuStudio.conf`.** Preset reading then silently falls through to the
   preferences, and a user who never set those gets a refusal instead of a slice.
   Falsified by the next Studio update; the test in M2 pins the layout we rely on,
   so it fails loudly rather than degrading quietly.
2. **`MeshPart.meshFromShape` is slow on a real multi-body document.** It runs on
   the GUI thread, before the job is handed off, so it is the one part of the round
   trip that can still freeze FreeCAD. Falsified by meshing the 68-object eval
   document and timing it. If it is slow, the meshing moves into the job — which
   means the scratch document has to be built on the GUI thread and only the write
   deferred, or the mesh data handed to the thread as plain arrays.
3. **`slicer.json` drifts from Studio and nobody notices.** A preset chosen in the
   page outranks Studio, so changing the printer in Studio then slicing here uses
   the old one. Falsified by doing exactly that. Mitigated rather than solved: the
   result states which level supplied each preset, and a stored `machine` name that
   no longer resolves is reported as stale rather than silently replaced.
4. **A large model makes the export or the slice much slower than measured.** The
   export is on the GUI thread; the slice is not. Falsified by exporting and
   slicing the 68-object eval document and timing the `Mesh.export` call alone. A
   slow export needs the same async treatment as the slice, which changes
   `slice_model`'s shape.
5. **`inlineDynamicImports` and the worker pass do not coexist**, so the no-hash
   contract and the workers cannot both be had. Falsified by M4's build. Fallback:
   drop `inlineDynamicImports` and pin `chunkFileNames`, accepting a handful of
   stable chunk files.
6. **A running Bambu Studio conflicts with the CLI** over config state or a lock.
   Falsified by running a slice with Studio open. Fallback is `--datadir`, which
   then has to be shown not to break preset inheritance.
7. **The `;` preset separator differs on Windows.** Falsified by one slice on
   Windows. Cheap to fix, easy not to notice until a Windows user reports a slice
   that ignored the process preset.
8. **Module workers or WebGL misbehave in the user's default browser.** Falsified
   by opening the page in each installed browser. Fallbacks are
   `worker.format: "iife"` and, at the far end, fork 1(d).
9. **The committed 1.1 MB bundle is judged too heavy after all.** Falsified by
   the first diff. The reversal is fork 2(b) plus a documented build step, which
   demotes the feature rather than rewriting it.
