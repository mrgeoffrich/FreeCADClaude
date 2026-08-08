# CLAUDE.md

Guidance for Claude Code working on **FreeCADClaude**, a FreeCAD addon.

## What this is

A FreeCAD 1.1 workbench that docks a **Claude chat panel** and lets Claude act
on the active document. It does **not** call the Anthropic API directly and uses
**no API key**: it drives the user's installed **`claude` CLI** (Claude Code) as
a subprocess, authenticating with the user's own Claude subscription. Personal
use only — Anthropic's terms don't permit shipping claude.ai login in a
distributed product.

## Architecture (how a turn flows)

```
chat panel (GUI thread)
  └─ AgentWorker (QThread)  ── spawns per turn ──▶  claude -p ... (subprocess, hidden)
                                                       └─ MCP stdio server (mcp_server.py, child)
                                                            └─ TCP (localhost+token) ──▶ gui_bridge (in FreeCAD)
                                                                                            └─ runs tool on the GUI thread
```

- **One `claude -p` process per turn**, streaming newline-delimited JSON
  (`--output-format stream-json`). Multi-turn continuity via `--resume <session_id>`
  (session id captured from the first turn's `system` event).
- FreeCAD's API is **not thread-safe** → all tool execution is marshalled onto
  the **GUI main thread** by `gui_bridge` (a posted `QEvent` + `threading.Event`).
- Tools reach the live document because the bridge runs **inside** FreeCAD; the
  MCP server child only relays over a localhost socket (shared-secret token).

## Module map

| File | Role |
|---|---|
| `Init.py` / `InitGui.py` | Workbench registration (App/GUI). InitGui also has the eval hook. |
| `freecad/freecadclaude/chat_panel.py` | The dock, Markdown transcript (streamed), buttons, worker wiring. |
| `freecad/freecadclaude/plan_panel.py` | Second dock: Plan (subagent output) + live task checklist. |
| `freecad/freecadclaude/flow_layout.py` | `FlowLayout` — a wrapping row layout, used for the chat panel's control strip (Send/Stop/New, model + effort combos, Open Files/Connect Mobile/Slicer). A `QHBoxLayout`'s minimum width is the *sum* of its children's, and a `QPushButton`'s own minimum is ~80px however short its label, so a single non-wrapping strip sets the dock's floor far above what the transcript and input need. Wrapping makes the layout's minimum the *widest single item* while its `sizeHint` stays the one-row width, so the dock opens wide and still drags narrow. Rows are flush right, matching where the strip sits. |
| `freecad/freecadclaude/dock_panel.py` | The singleton dock shell both panels subclass (`DockPanel`): lazy creation, reuse-by-`objectName` across a workbench reload, `instance()`/`widget`. Subclasses supply the inner widget and, via `_on_created`, what happens to a fresh dock (chat raises itself; the plan dock tabs in behind it). |
| `freecad/freecadclaude/agent_worker.py` | Drives the `claude` CLI per turn; parses stream-json → Qt signals. `_handle_tool_use` **surfaces a tool call by default** and special-cases only the exceptions, so a tool added to the allow-list appears in the transcript without a change here — the old allow-list shape let `Write`/`Glob`/`Grep` run invisibly. `_tool_label` picks the detail arg (path basename, pattern, subagent type); an unmapped name falls back to its own lowercased name. `TaskCreate`/`TaskUpdate` stay out of the transcript because the plan dock's checklist already shows them call for call. A `Plan` subagent is the one id in **both** `_plan_ids` and `_chat_tool_ids` — `_handle_tool_result` must not `elif` those two branches, or the transcript entry never gets its result. |
| `freecad/freecadclaude/agent_config.py` | Model, system prompt (loaded from `system_prompt.md`), CLI flags (tools/mcp/cwd/skills). |
| `freecad/freecadclaude/system_prompt.md` | The system prompt text itself, edited as plain Markdown. Its `{REFS_DIR}` placeholder is replaced by `agent_config` at load with `REFS_REL` — the references folder relative to the CLI's cwd. |
| `freecad/freecadclaude/references/` | run_python scripting references (sketcher / partdesign / part-draft) the system prompt tells Claude to `Read` on demand — progressive disclosure without a skill gate. The prompt's execution-contract section covers the rest. |
| `freecad/freecadclaude/gui_bridge.py` | In-FreeCAD socket server; runs tools on the GUI thread; run_python arg precheck. |
| `freecad/freecadclaude/freecad_tools/` | The tools, as a package — see its own map below. `__init__.py` holds the `TOOLS` registry and re-exports the facade the rest of the addon imports (`TOOLS`, `list_schemas`, `feature_snapshot`, `post_tool_notes`, the session-dir helpers), so `from . import freecad_tools` still reaches everything. |
| `freecad/freecadclaude/device_server.py` | The LAN HTTP server behind the chat panel's **Connect Mobile** button: serves `device_ui/` to a phone or tablet and takes marked-up images back. Stdlib only — no Qt, no FreeCAD, not even indirectly; see "Device annotation" below for what that import list buys. |
| `freecad/freecadclaude/device_ui/` | The built web app — committed build output, whose source is `web/`. See "Device annotation" before touching either. |
| `freecad/freecadclaude/gcode_server.py` | The **loopback** HTTP server behind `view_gcode` and the chat panel's **Slicer** button: serves `gcode_ui/` to the desktop browser, plus `GET /api/gcode/<id>` and `GET`/`PUT /api/slicer/{options,config}`. Binds `127.0.0.1`, keeps a token anyway. Stdlib only, same rule as `device_server.py`; see "Slice preview" below. |
| `freecad/freecadclaude/gcode_ui/` | The built G-code viewer — the **second** committed build output, whose source is `gcode_web/`. See "Slice preview" before touching either. |
| `freecad/freecadclaude/slicer_runner.py` | Drives Bambu Studio as a subprocess on a daemon thread, and resolves the presets it needs off the slicer's own files. Job table, argv builder, discovery. Stdlib only, and testable under a bare `python3`. |
| `freecad/freecadclaude/web_static.py` | A URL path turned into a file inside one fixed directory, and a content type for it — shared by both servers. `resolve()` is the whole of the containment (realpath on both ends, against a root the caller passes); a second copy of it is a second place for that check to be subtly weaker. |
| `freecad/freecadclaude/qr.py` | The pairing QR: byte mode, EC level L, versions 3–6, fixed mask 0. Imports no Qt (returns a boolean matrix; `chat_panel._qr_pixmap` paints it), so it is unit-testable headlessly. |
| `freecad/freecadclaude/_deps.py` | Locates the `claude` CLI. |
| `freecad/freecadclaude/eval_runner.py` | Unattended end-to-end eval (triggered by env var). |
| `mcp_server.py` | Stdlib-only MCP stdio server the CLI spawns; relays to the bridge. |
| `web/` | Vite + TypeScript + Vitest source of `device_ui/`. Dev-time only — excluded from `deploy.ps1`/`deploy.sh` and never shipped. |
| `gcode_web/` | Vite + TypeScript + Vitest source of `gcode_ui/`, vendored from the `dimensioner` project. `gcode_web/VENDORED.md` records the upstream commit and every local patch, and is the reviewable artifact for a 1.1 MB minified build nobody reads. Dev-time only, same exclusions as `web/`. |
| `deploy.ps1` / `install_deps.ps1` / `eval/run.py` | Dev tooling (not deployed). |

## Tools

Registry: `freecad_tools.TOOLS` = name → `{schema, run, precheck?}`. Current set:
`get_objects`, `describe_objects`, `get_selection`, `document_notes`,
`set_print_direction`, `get_sketch`,
`view_sketch_svg`, `capture_view`,
`capture_user_view`, `crop_view`, `cutaway`, `annotate_view`, `read_annotation`,
`send_to_device`, `read_device_image`,
`export`, `slice_model`, `read_slice_result`, `view_gcode`, `inspect_api`,
`get_diagnostics`, `run_python` (the only tool that touches geometry —
the general Sketcher/PartDesign/Part path; `document_notes` mutates the
document too, but only its own notes object).

The three slice tools reach outside FreeCAD rather than into the document:
`slice_model` starts the user's own slicer as a subprocess, and `view_gcode`
starts a loopback listener and launches their browser. See "Slice preview".

**There is no approval gate.** Tool calls execute as soon as they arrive. What
stands between a bad call and a damaged document is the transaction (a raising
call rolls back whole, newly-created objects removed) and, optionally, the
`SaveSteps` snapshots. Note the scope this gives up: `run_python` is arbitrary
Python in the FreeCAD process, so it can touch the filesystem, not just the
document. That is the trade for a personal-use addon.

The package is one `tools_*` module per concern over a base of shared
infrastructure. Dependencies run **tools → infra only** — keep it that way; the
infra modules import nothing from `tools_*` (that's why `_ERROR_FLAGS` and
`_solver_constraint_indices` live in `diagnostics`, not next to their callers).

| Module | Role |
|---|---|
| `__init__.py` | The `TOOLS` registry, `list_schemas()`, and the facade re-exports. |
| `tools_document.py` | `get_objects` (the shallow survey), `describe_objects` (the deep read on named objects), `get_selection` — see "The survey/detail split" below. `get_objects` also returns a `bodies` section — each `PartDesign::Body`'s **tip chain**, base-first (see `diagnostics._body_states`). That's the only place a Body's build order is visible, and it's in the tool the model already calls first; a feature missing from the chain contributes nothing however healthy it looks. Costs ~475 bytes of a 7.3 KB payload on a 68-object/3-body document. |
| `tools_python.py` | `run_python` (+ its syntax precheck). |
| `tools_notes.py` | The design-context tools: `document_notes` (read with no args, replace with `text`) and `set_print_direction` (batch). |
| `doc_notes.py` | Where those notes live and how staleness is judged — see "Document notes" below. |
| `print_meta.py` | Per-part build direction — the enumeration, the plate-side derivation, `DIRECTION_NOTE`, `up_vector()`. |
| `print_export.py` | `oriented_export` — mesh each part, stand it up on its recorded `PrintDirection`, drop it onto the plate, and write them all as one multi-object 3MF through a scratch document that is closed again. Infra beside `print_meta.py`: that one records the direction, this one acts on it. The rotation reaches the mesh, never the user's objects. |
| `tools_slice.py` | `slice_model`, `read_slice_result`, `view_gcode`, plus `open_settings_page()` re-exported for the chat panel's **Slicer** button. Everything environmental — the binary, the slicer's config, the profile roots, the preset names, the viewer directory — is read here on the GUI thread and handed to `slicer_runner`/`gcode_server` as plain values. |
| `tools_inspect.py` | `inspect_api`. |
| `tools_sketch.py` | `get_sketch`, `view_sketch_svg` (+ the GeoId overlay). |
| `tools_capture.py` | `capture_view`, `capture_user_view`, `crop_view`. |
| `tools_annotate.py` | `annotate_view`, `read_annotation` — the draw-on-the-screenshot round trip. |
| `tools_device.py` | `send_to_device`, `read_device_image` — the same round trip through a phone or tablet on the LAN. Also `device_upload_dir()`, re-exported for `chat_panel`; it is the whole of the no-FreeCAD seam described under "Device annotation". No rendering code of its own — it enters `render._offscreen_shot` like the capture tools do. |
| `tools_cutaway.py` | `cutaway` (+ clip-plane resolution). |
| `tools_export.py` | `export`. |
| `session.py` | Artifact folders: the per-conversation session dir (also the CLI's cwd — `prepare_session_workspace` copies the skills and references into it), the script/step archives (`_session_subdir`/`_safe_name` build every artifact path), plus the paths that aren't artifacts but that several modules must agree on: `PARAM_PATH` (the preferences root), `REFS_DIR` (the bundled `references/`, only ever the copy *source*) and `ref_path()` (how a reference is cited: `REFS_REL` + the name, relative to the CLI's cwd). All single-spelled here — `agent_config` substitutes `REFS_REL` into the prompt's `{REFS_DIR}` and the tools cite through `ref_path()` in their notes, so the prompt (built once per worker) and a note (built mid-conversation) name the same file the same way however many sessions have come and gone. `active_session_id()` is how a tool remembers "already said this in this conversation" without inventing its own notion of when one starts. |
| `namespace.py` | `scripting_namespace()` — the names `run_python` binds. `inspect_api` resolves against the *same* function, so the two cannot drift: inspect_api exists to tell Claude what run_python will have bound, and a name resolving in one but not the other is the exact guess it's there to prevent. |
| `geometry.py` | Bounding boxes, world-space crop extents. |
| `svg.py` | Framing/cropping an SVG projection; `_SVG_XFORM_RE` — the one pattern for importSVG's wrapper `translate()/scale()` group, shared by its writer (`_flat_crop_svg` regenerates it when cropping) and its reader (`tools_sketch._annotate_sketch_svg` positions the GeoId overlay off whatever ends up in the file). |
| `gui_state.py` | The user's current GUI context: what they have open in an editor (`_active_edit_object` & co) and what they have selected (`_selected_objects`, used by `get_sketch`/`view_sketch_svg`/`export` for their no-`name` fallback). |
| `visibility.py` | Show only the captured objects, then restore. |
| `render.py` | The offscreen view, its camera, the PNG grab, `_last_capture`. `_offscreen_shot` is the context manager all three raster tools enter — it owns the setup/teardown (isolate visibility, suspend selection, size the view; restore all of it plus the GUI doc's `Modified` flag on *every* exit path, early `return` included). That restore is what keeps a capture read-only, so it lives here once rather than in three copied `finally` blocks. It also holds everything `capture_view` and `cutaway` share *around* that view — they differ only in what happens inside it, so their whole front half (`_capture_setup`: active doc, the required `objects` list → visibility keep-set, camera plan, size, crop extents) and back half (`_camera_angle_note`/`_shown_extents_note`) live here, as do the schema properties describing those args (`_objects_schema_prop`, `_CAMERA_SCHEMA_PROPS`, `_SIZE_SCHEMA_PROPS`). Keep it that way: the two tools must not describe or handle the same knob differently. |
| `diagnostics.py` | What a mutating call changed, and what it broke (`post_tool_notes`). Also where a scripting reference gets **cited**, when the condition it documents is the one just detected — see "Just-in-time reference pointers" below. `_broke_an_existing_feature` is that trigger for the tip-cycle gotcha: a newly-Invalid feature that already existed in the `before` snapshot (a feature the call *created* failing is ordinary and the traceback covers it). `_body_states`/`_walk_base_chain` are the **structural** view — which features actually reach a Body's Tip — used by both `summarize_chain_changes` (the before/after diff) and `get_objects`. They live here, not in `tools_document`, because infra must not import from `tools_*`. `_shape_metrics` is the hash-keyed measurement cache every snapshot goes through; see the fingerprint note under "Just-in-time reference pointers" before adding a field to it. |

Importing the package imports every submodule, so **no submodule may `import
FreeCAD` at module level** — that would break the "importable from any thread for
its schema data alone" contract. Keep FreeCAD imports inside the functions.

**Adding a tool** is purely additive: add a `{schema, run}` entry to the registry
in `__init__.py`, with the implementation in the matching `tools_*` module (a new
one if it's a new concern). `run(args)` executes on the GUI thread and returns a
string; the MCP allow-list and the bridge wiring derive automatically. Set
`"precheck": fn` to validate the args in pure Python first (a non-empty return
goes to Claude instead of running the tool). `capture_view` returns a `(text, png_path)` tuple instead of a plain
string; `gui_bridge` reads and base64-encodes `png_path` and `mcp_server.py`
ships it back as an inline MCP `image` content block in the same tool result —
Claude sees the picture directly, no separate file-open step. (The Claude API's
image content blocks only accept raster media types — png/jpeg/gif/webp, not
svg+xml — so this only applies to `capture_view`'s screenshot.) `view_sketch_svg`
writes an SVG file and returns just its path as plain text; Claude opens it with
the built-in `Read` tool to read the raw vector source, since it's text Claude
reasons about, not something it can visually see.

Besides this MCP registry, a few of the CLI's own built-in tools are always
enabled (`agent_config.build_config`'s `builtin_tools`): `Read` (the SVG file
from `view_sketch_svg`, skill reference files, and the bundled
`references/*.md` scripting references), `Write`/`Edit` (author plain-text
files whole or patch them in place, e.g. `freecad-lofi-sketch`'s SVGs), and
`Glob`/`Grep` (file search — find
files by name/path, search their contents; so Claude can locate a STEP/STL to
import or a previous export before Reading it). All run inside the `claude` CLI
process itself, not the MCP bridge; all are read-only except `Write`/`Edit`,
which reach the filesystem but never the live document. `Glob`/`Grep` are
always on, independent of `_SKILL_TOOLS` (which covers only `Skill`), since file
search is a general capability, not a skill-only one.

**The survey/detail split** (`get_objects` → `describe_objects`): `get_objects` is
the cheap survey, called about once per session, so its cost is one large payload
rather than a frequency problem. Each trim below is checked against what the
logged sessions actually referred to again afterwards, not guessed:

- **Origin planes and axes are a large share of the payload and carry no
  information.** The `App::Origin`/`App::Plane`/`App::Line`/`App::Point` set
  every Body and Std Part gets is identical boilerplate whose bounding box is
  literally **±1e100**. But the *names* matter —
  a multi-body document numbers them (`XY_Plane002`) and picking the wrong suffix
  is a logged error (`KeyError: 'XY_Plane'`) — and a flat list doesn't say which
  Body owns which. So they're collapsed onto their owner's entry as `origin`
  (`_origin_owners` walks each `App::Origin`'s `OriginFeatures` rather than
  guessing from names): fewer bytes *and* the ownership mapping.
  `_ORIGIN_NOTE` explains the resulting gap between `object_count` and the
  entries, because an unexplained gap reads as missing objects and costs exactly
  the exploratory `run_python` this tool exists to prevent.
- **Per-feature bounding boxes are dead weight; sketch ones are not.** A bbox
  only an in-body feature carries is almost never referred to again, and the ones
  that are come mostly from sketches. In-body feature boxes
  are also largely duplicates of each other (a chain all bounding the same
  growing solid, which the Body's own entry reports). So `_reported_bbox` skips
  anything derived from `PartDesign::Feature` and keeps everything else.
- `position` is `[0,0,0]` on most objects, `label` usually repeats `name`,
  and `visible` only matters when true — all three are conditional.
- Output is **compact JSON** (`separators=(",", ":")`), not `indent=2`: the
  indentation alone costs a third of the payload, and this is a bulk list Claude
  reads rather than parses.

`describe_objects(names=[...])` is where the detail lives, and it is allowed to be
slow and large — you name the handful you care about. It answers the
object-level half of what `run_python` is otherwise used for: a large share of
logged `run_python` calls are pure read-only inspection with no mutation at all,
asking mostly for bbox, volume, chain/tip, validity and properties. So it returns
full placement *including rotation*, the world bbox, shape
metrics (solids/faces/edges/vertexes, volume, area, centre of mass, `isValid`),
dimensions, attachment (`AttachmentSupport`/`MapMode`/`AttachmentOffset`),
dependency links **both ways** (`depends_on` walked generically off
`PropertiesList`+`getTypeIdOfProperty`, so an unforeseen feature type still
reports its inputs; `used_by` from `InList`), a Body's tip and chain, and a
sketch's DoF/solver state/wire closure. The face- and edge-level querying that
made up the other half of those calls stays in `run_python` — it's query-shaped,
not enumerable.

Two things worth not breaking. `_dependency_links` skips `Hidden` properties and
anything starting with `_`: `_Body` is FreeCAD's own bookkeeping and would report
a feature's containing Body as one of its *inputs*, which reads as a cycle.
And `_extra_metrics` (area + centre of mass) is a **separate** hash-keyed cache
from `diagnostics._shape_metrics` on purpose — that one feeds the fingerprint diff
running twice on every `run_python` call on the GUI thread, and two more GProp
reads there would make every mutating call pay for something only this tool wants.
Caching them here makes a repeat describe near-free; the cold call still pays the
honest price of `Volume`/`Area`/`isValid` on real solids.

**Document notes** (`document_notes`, storage in `doc_notes.py`): free text carried
inside the FCStd saying what the model is for, how its parts relate and how it is
to be printed — the context no geometry states. It lives in a `ClaudeNotes`
`App::TextDocument` so the user can open and edit it in FreeCAD's own text tab.
`get_objects` returns it, which is why the read needs no prompt instruction; the
write is prompted, backed by a staleness flag raised when a top-level part has
been added or removed since the notes were stamped. Feature-level churn inside a
Body does not count, since notes about purpose survive it. The stamp is a pair of
dynamic properties created with `Prop_Output` (attr `8`): a plain property write
marks its object touched, and the next recompute then rebuilds that object and
everything downstream of it. `_top_level_parts` excludes `PartDesign::Feature`
even when one sits at top level — `removeObject` on a Body leaves its features
behind, and an orphan would otherwise read as a new part.

**Print direction** (`set_print_direction`, storage in `print_meta.py`): per part,
the part-local axis that points UP in world space when it is printed. `+Z up` is
the part as modelled; `-Z up` prints it upside-down with the local `+Z` face on
the plate. An `App::PropertyEnumeration` rather than free text or a bare vector —
FreeCAD renders it as a dropdown and rejects an off-list value with `ValueError`,
and both the value and the list survive a reload. `App::PropertyDirection` holds
the tilted case and is read only when the enum says `Custom`. Both carry
`Prop_Output` for the same reason the notes fingerprint does.

Every report pairs the direction with `print_plate_side`, and any payload
carrying one appends `DIRECTION_NOTE`. The plate side is one negation away from
the direction, and that is the step that gets reversed under load — reversing it
puts supports and bridging on the wrong face.

**Sketch editing** (`get_sketch`, read-only): every Sketcher mutation is addressed
by **GeoId** (`moveGeometry`, `addConstraint`) or **constraint index** (`setDatum`,
`delConstraint`), and nothing else in the tool set exposes either — `get_objects`
gives a bbox, and `view_sketch_svg`'s exported paths *fuse* connected edges into
unlabelled wires and omit construction/external geometry entirely. So `get_sketch`
is the only way to edit an existing sketch without guessing: it returns every
geometry element with its GeoId/coords/construction flag, every constraint with its
index/operands/datum, a `constraints_by_geoId` reverse index, the solver state, and
external geometry. `view_sketch_svg` overlays GeoId labels + the omitted
construction/external geometry + the origin on top of importSVG's exact paths
(`_annotate_sketch_svg`, positioned from the wrapper `<g transform>` it parses out,
so it composes with `_flat_crop_svg`). Three verified facts the code depends on:
external geometry starts at GeoId **-4** (not -3 — the origin point holds -3);
the solver's `ConflictingConstraints`/`RedundantConstraints`/`MalformedConstraints`
are **1-based** while `setDatum`/`delConstraint` are 0-based (`_solver_constraint_indices`
normalises them, else a "drop the redundant constraint" fix deletes the wrong one);
and `DoF`/those conflict lists are plain attributes, **not** in `PropertiesList`.

**GUI edit state** (`_active_edit_object`, in `gui_state.py`): what the user
has *open in an editor* — as opposed to selected — comes from exactly one place,
the in-edit ViewProvider (`FreeCADGui.ActiveDocument.getInEdit().Object`). It is
not derivable from the document, so without it "this sketch" is a guess. Three
consumers: `get_selection` reports it as an `editing` field (name/label/type/
`is_sketch`, else `null`) — that tool is the *current GUI context* probe, not just
a selection dump, and it's how Claude resolves "this sketch"/"here" without a
screenshot or a full `get_sketch` dump; `_resolve_sketch` prefers the open sketch
for a no-`name` `get_sketch`; and `_sketch_report` carries `open_in_editor` so a
returned sketch says whether the user is actually sitting in it (a no-`name` call
on a multi-sketch doc with nothing open still falls back to the *first* sketch —
that flag is what tells you it was a guess). All three degrade to `None`/`False`
under no GUI, no active GUI document, or a dead ViewProvider.

Visual perception: `capture_view` (raster screenshot, returned inline as an
image) is the way Claude actually *sees* geometry — reach for it whenever
shape needs visual inspection, especially 3D. `cutaway` is its sibling: the same
offscreen-render + inline-PNG path, but with a Coin `SoClipPlane` inserted at the
root of the throwaway view's scene graph (world coords; discarded with the view,
so the document and the user's real view are untouched) to slice the model open
and reveal internal features. The cut is *hollow* (the exposed interior surfaces,
not a filled cross-section — Coin's clip doesn't cap); a capped section would need
a geometry Boolean cut on temporary objects, which would stop the tool being
non-mutating like `capture_view`. Both share `_resolve_camera_args`/
`_apply_camera_plan` for the `view`/`azimuth`/`elevation` angle handling.
`capture_user_view` is the other sibling, for the opposite situation: instead of
an auto-framed offscreen camera Claude controls, it screenshots the user's *own*
active 3D view exactly as painted on screen (their real camera angle, zoom, draw
style, background) — useful when the user is pointing at something in front of
them rather than asking Claude to go find an angle. It temporarily flips the
`SavePicture` preference to `GrabFramebuffer` (reads the already-rendered
widget — only valid because, unlike the other two, this view is actually
visible) and restores it in a `finally`; no offscreen view, no camera move, no
draw-style override — genuinely read-only. Fails with a plain-text message if
the active tab isn't a 3D view. `capture_view`/`cutaway`/`crop_view` all enter
`render._offscreen_shot` and differ only in what they do inside it (aim the
camera / insert the clip plane / replay the last camera and zoom); the shared
`x_min..z_max` framing is `render._apply_extent_crop`.

**Annotation round trip** (`tools_annotate.py`): `annotate_view` grabs the user's
own view (same `GrabFramebuffer` path as `capture_user_view`), saves it under
`<session>/annotations/`, and opens it in their image editor — then **returns
immediately**. It must: the call runs on the GUI thread, so waiting for the
editor to close would freeze FreeCAD for as long as the user spent drawing. The
user draws, saves in place, says so, and `read_annotation` re-reads the file and
returns it as an inline image.

**There is deliberately no pixel diffing or colour detection.** Claude *sees* the
returned image, so a circled boss or an arrow at an edge is read straight off the
picture — which means the user can mark up however they like rather than matching
a scheme the code knows how to find. What the code contributes is what a picture
can't carry: the document, the visible objects, their world extents and the
camera angle, recorded **at grab time** (`_last_annotation["context"]`) and
replayed by `read_annotation`, since those facts describe the image and not
wherever the user has since orbited to. Editor is `open -a Preview` (macOS) /
`mspaint` (Windows) / `xdg-open` (Linux), overridable with the `AnnotateEditor`
preference under `PARAM_PATH`.

**Device annotation** (`device_server.py` + `qr.py` + `tools_device.py` +
`device_ui/`, source in `web/`): the same round trip, with a phone or tablet on
the LAN as the editor. `send_to_device` renders a capture and publishes it; the
paired page shows it over SSE, the user draws with a stylus and places
**dimensions**; `read_device_image` returns the flattened PNG inline plus the
annotation document verbatim. The server runs only while the chat panel's
**Connect Mobile** button says so. Design docs: `docs/device-annotation-{design,plan}.md`.

- **The HTTP server never calls into FreeCAD, and its import list is what
  enforces that.** `device_server.py` imports no FreeCAD and no Qt, not even
  indirectly, so the module is testable under a bare interpreter, a LAN request
  cannot freeze the GUI thread, and the worst a token-holder can do is read
  pushed captures and write image files into one folder. The cost is that every
  path and preference is passed *in*: `<session>/mobile/` is resolved on the GUI
  thread (`tools_device.device_upload_dir()`, from `chat_panel._on_device` on
  start and from `send_to_device` on every publish) and handed over as a string,
  and `idle_timeout` the same way. A handler that "just looks something up"
  crosses that line.
- **`device_ui/` is committed build output.** Users install from `main` as a
  plain file copy — no Node, no build step — so whatever is in that folder is the
  app they get. Rebuild it (`npm ci && npm run build`) and commit it in the same
  commit as any `web/` change; `RELEASE.md` repeats this as a release step. The
  build is deterministic (fixed asset names, no hashes, no code splitting), so a
  rebuild that changes nothing produces no diff.
- **Plain HTTP, so the token crosses the LAN in clear.** Accepted for a
  personal-use addon and stated in `README.md`/`SECURITY.md`. It also caps the
  client: `getUserMedia` needs a secure context, so there is no in-page
  viewfinder, but `<input type="file" capture="environment">` opens the camera
  app over plain HTTP.
- **Idle auto-stop counts only time with no request in flight**, and an
  `/api/events` stream is in flight for as long as the page is open — so the
  timeout means "the tablet went away", not a deadline the user has to beat while
  drawing. `_serving()` brackets the *dispatch*, never `handle_one_request`: with
  keep-alive a handler spends most of its life blocked reading the next request,
  and counting that would keep the server up for ever. Default 30 min,
  `DeviceIdleMinutes` preference, negative to disable.
- **"New" clears the server's state** (`reset_session`, from `chat_panel._on_new`
  after the new session id is minted). The feed outlives `stop()` on purpose,
  since `read_device_image` is routinely called after the user has stopped the
  server; without the reset the next chat's `read_device_image` would answer with
  the previous chat's capture and its uploads would land in the previous
  session's folder.
- **Measurement is a division, and the caveat travels with it.**
  `mm_per_px = cam.height / rendered_height_px` off the ortho camera, read in
  `render._capture_optics` inside the offscreen view and last — `_fit_render_size`
  can re-frame the camera and change the pixel height in the same call. An
  oblique camera downgrades confidence rather than dropping the number, so Claude
  can tell "no measurement" from "a measurement you shouldn't machine to"; an
  absent field makes those identical. `measured_mm` and `target_mm` stay separate
  facts, and with no scale a dimension reads in pixels. Hence `send_to_device`
  defaults face-on where `capture_view` defaults iso.

**Slice preview** (`slicer_runner.py` + `gcode_server.py` + `web_static.py` +
`freecad_tools/{tools_slice,print_export}.py` + `gcode_ui/`, source in
`gcode_web/`): `slice_model` writes the chosen parts as one multi-object 3MF,
each stood up the way it prints, and hands Bambu Studio a finished command line
on a daemon thread; `read_slice_result` collects the outcome; `view_gcode` opens
the toolpath in the user's own desktop browser, for them and not for Claude.
Design doc: `docs/slice-preview-design.md`.

- **There are now two committed build outputs**, and this is the fact most
  likely to trip someone: `device_ui/` from `web/`, and `gcode_ui/` from
  `gcode_web/`. Each is rebuilt with `npm ci && npm run build` in its own source
  folder and committed in the same commit as the source change; `RELEASE.md`
  lists both. Both builds are deterministic (fixed asset names, no hashes, no
  code splitting), so a rebuild that changes nothing produces no diff.
- **`gcode_web/` is vendored, so `VENDORED.md` is the review.** Every local
  change to the vendored source is one numbered entry there — add yours when you
  touch `gcode_web/src`, because the built diff is unreadable.
- **Neither stdlib module may import FreeCAD or Qt**, for the reason
  `device_server.py` may not: `slicer_runner` drives the slicer off the GUI
  thread and `gcode_server` answers HTTP on its own threads. So every path,
  preference and preset name is resolved in `tools_slice._preferences` and
  handed over as a plain value; a function there that looked one up itself would
  put a preference read on a worker thread.
- **The slice is async because a slice takes minutes.** `slice_model` returns a
  job id at once. `read_slice_result` waits in a nested `QEventLoop` with a
  polling `QTimer` (default 120 s, cap 480), never a sleep, so FreeCAD repaints
  and the bridge keeps marshalling calls; both bounds sit under the bridge's
  600 s `GuiBusyTimeout`, which reports rather than cancels.
- **A binary that is not Bambu Studio gets no command line at all.**
  `build_argv` raises `UnknownSlicer` and `start_job` propagates it before a job
  folder exists. These are GUI applications: a flag one does not accept opens a
  modal dialog on the user's desktop and returns no text anyone can read, so a
  guess is worse than a refusal. OrcaSlicer is identified and refused.
- **Preset resolution is four levels, most specific first** — an explicit tool
  argument, `~/FreeCADClaude/slicer.json` (what the settings page stored), the
  slicer's own live selection, then the `Slicer*` preferences — and every result
  says which level supplied each preset. A name from the top two levels that
  resolves to nothing is refused, never replaced. The nozzle is pinned (0.4
  unless asked) and all three presets are re-derived together against it: a
  Studio session left on 0.2 has a 0.2 machine *and* process *and* filament, so
  snapping the machine alone leaves the other two incompatible. Compatibility is
  read from each preset's own `compatible_printers`, never parsed from its name.
- **Every slicer-owned file is read through `slicer_runner._read_json`, which
  returns a dict or None.** Its callers say `_read_json(path) or {}` and then
  `.get(...)`: a JSON array is truthy, so a non-object file would survive that
  guard and raise `AttributeError` frames away from the file that caused it.
- **The settings page is how a printer gets changed without Claude.**
  `view_gcode` and the chat panel's **Slicer** button open the same page, and
  the drawer renders with no G-code loaded because configuring the printer comes
  before the first slice. `PUT /api/slicer/config` validates every name against
  what is installed before writing; the alternative surfaces minutes later as a
  failed slice with the argv as the only clue.
- **"New" clears the job table** (`slicer_runner.reset_session` plus
  `freecad_tools.reset_slice_session`, from `chat_panel._on_new`), for the reason
  `device_server.reset_session` exists: without it `read_slice_result` with no
  argument answers a new conversation with the previous one's job. A slice still
  running keeps running — what is dropped is the handle, not the child, which
  `terminate_all` still reaches on `aboutToQuit`.
- **The slicer's reported object sizes are not dimensions** — a 16 mm cylinder
  came back at 17.2 — so only the box CENTRE is used and the sizes are never
  quoted as measurements. A 3MF `<object>` carries an id and no name, so
  `oriented_export`'s report ORDER is the only mapping from parts to file
  objects; `result.json`'s `objects` array is *not* in that order, so match on
  the slicer's `Object_N` name, fall back to the numeric id, and cross-check the
  pair's size before quoting a position.

Preferences, all under `PARAM_PATH` and all "empty means discover" — on a
machine with Bambu Studio set up, none needs setting:

| Key | Type | Meaning |
|---|---|---|
| `SlicerPath` | string | The slicer binary. |
| `SlicerConfPath` | string | The slicer's own `BambuStudio.conf`. |
| `SlicerProfileDirs` | string | Extra profile roots, `os.pathsep`-joined. |
| `SlicerNozzle` | string | Nozzle to pin when the slicer's selection is used (default `0.4`). |
| `SlicerMachine` / `SlicerProcess` / `SlicerFilament` | string | Preset names, the last resort when everything above is missing. |
| `SlicerArrange` / `SlicerOrient` | bool | Defaults for those two arguments (both true). |
| `GcodeUiDir` | string | Override `gcode_ui/` — the dev hook for pointing at a Vite build. |

**Draw style** (`style` on both `capture_view` and `cutaway`, schema shared via
`render._STYLE_SCHEMA_PROPS`): `shaded` (default), `xray`, `wireframe`.
`_force_draw_style` is the single place the viewer's override mode is set — and
that override is *why* a ViewObject's `DisplayMode` has no effect on a capture: it
outranks per-object modes so a shot can't inherit e.g. `Points` from one object.
Expect `DisplayMode` to read back as something the render doesn't show.
`wireframe` is therefore a per-view
override — no document mutation, dies with the throwaway view. `xray` has no
draw-style equivalent, so it goes through `Transparency` (60%; at 80 the form
dissolves into the background) and must be restored.

**`_shot_appearance` saves in one pass and applies in a second, and that split is
load-bearing.** Setting `Transparency` on a Body **propagates to the features
inside it**, so a save-then-set-as-you-go loop reads an already-propagated value
for objects it reaches later and "restores" them to the shot's value, leaking the
shot's transparency into the document.
The same hazard applies to any ViewObject property that propagates — record
everything before changing anything. Note also what the override does *not* cover:
`Hidden Line` renders identically to `Shaded` on FreeCAD 1.1 (no edge lines), so
it isn't offered.

**A crop defaults its omitted axes to the SHOWN objects, and the image is shaped
to the geometry.** Both matter most on long thin parts, which are otherwise
unphotographable:
- An axis the caller doesn't specify must default to the shown objects, not the
  whole document. Defaulting to the document lets a crop on one axis blow the
  other two out to everything else in the file, so a *narrower* crop renders
  worse than no crop. `_framed_box(doc, keep_names, extents)` is the single
  definition of "the box a capture frames", used by both the framing and the
  auto-size so they cannot disagree.
- Even framed correctly, a long thin part in a fixed 4:3 image is mostly black.
  `render._fit_render_size` shapes the *image* to the box's
  on-screen aspect (`_screen_half_extents` off the live camera basis — it must
  run after `_apply_camera_plan`, since how wide a box looks depends on where the
  camera ended up). Same 1.23 MP budget and 1568px long-edge ceiling, clamped to
  `_MAX_AUTO_ASPECT` 4:1; 4:3 input still yields exactly 1280×960, so ordinary
  parts are untouched. An explicit `width`/`height` disables it, and every
  unclear case (no ortho camera, degenerate box, framing refused) falls back to
  the fixed size — it can only improve a shot or leave it alone.
`view_sketch_svg` (exact SVG; for
3D pass `view=front/top/...` → `TechDraw.projectToSVG` orthographic) is for
reasoning about exact coordinates as text, not for looking at the shape — its
3D-projection path data is tessellated into many small segments and isn't
meant to be read directly either. Artifacts go to `~/FreeCADClaude/<session-id>/{captures,exports,scripts,mobile,slices}`
(the user's home directory, **not** FreeCAD's `UserAppData`) — `<session-id>` is a
readable id (`YYYYMMDD-HHMMSS-<6 hex>`) minted by `freecad_tools.new_session_id()`
when a chat starts and again on "New" (`chat_panel._ensure_worker`/`_on_new`), so
every conversation gets its own folder; `session_dir()` resolves the active one
(older session folders are pruned, keeping the most recent 40). `captures`/
`exports`/`scripts`/`mobile` are written by FreeCAD tools via `_artifact_path`
(auto-pruned, kept ≤60 files each); `mobile` holds both directions of the device
round trip (`sent_*` out, `upload_*` back, each upload's annotation JSON beside
its PNG); `scripts` holds a `.py` copy of every `run_python` call
(written by `_save_run_python_script`, right before `exec`, so both successful and
failed runs are archived); `slices` is the exception to the flat-files rule —
one folder per slice job (`<HHMMSS>_<label>/`, holding `model.3mf`, the
slicer's `plate_1.gcode` and `result.json`, `slicer.log` and `job.json`),
because `--outputdir` writes slicer-chosen names that would collide across jobs
and the log belongs beside what it explains. `_prune_folder` filters on
`isfile`, so it cannot prune directories; `_session_job_dir(name, keep=20)` is
their own keep-N. `~/FreeCADClaude/slicer.json` sits outside every session,
beside `sketches/`, because a printer choice outlives a conversation. The same
session folder also holds `stream.jsonl` — the
raw newline-delimited JSON `agent_worker` reads from the `claude` CLI, appended
turn-by-turn (`AgentWorker._open_log`) — handy for diagnosing a turn after the fact.
The folder is also the CLI's cwd, so `prepare_session_workspace` copies the
skills into `.claude/skills/` and the bundled scripting references into
`references/` when it is created (see "CLI invocation"); those two are copies of
read-only assets, not artifacts, and are not pruned.
Optionally, `steps/` holds a numbered `.FCStd` snapshot of the document after each
successful `run_python` (via `_save_step_snapshot`, using `doc.saveCopy` so the doc's
own FileName is untouched) — off by default; enabled by the `SaveSteps` FreeCADClaude
preference, the `FREECADCLAUDE_SAVE_STEPS=1` env var, or `freecad_tools._save_steps["on"]`
(the eval sets this). Lets you open the model at each build step; parallels `scripts/`
(one `.FCStd` per successful call ↔ one `.py` per call). The end-to-end eval also drops
a final `<DocLabel>.FCStd` in the session root (`eval_runner._save_final_documents`).
`~/FreeCADClaude/sketches` sits outside any session: it holds `freecad-lofi-sketch`'s
concept SVGs, written directly by Claude via `Write` (not auto-pruned, since they
bypass `_artifact_path`, and not session-scoped since a sketch can precede any chat
turn that would mint one).

## CLI invocation (built in `agent_config`/`agent_worker`)

`claude -p <text> --output-format stream-json --verbose --include-partial-messages
--model claude-opus-5 --tools <builtins...>
--strict-mcp-config --mcp-config <json> --allowed-tools "<list>"` plus
`--append-system-prompt` (turn 1) or `--resume <id>` (later).

- **cwd = this conversation's session folder**, so the CLI's project context is
  the conversation's own artifacts rather than this repo's source (which is what
  it used to be, CLAUDE.md and all). Skills only load from `<cwd>/.claude/skills`,
  so `session.prepare_session_workspace` copies the skills project's skills and
  the bundled `references/` into the folder when it is created;
  `agent_config.session_workspace()` is the one call that does both and returns
  the path. "New" mints a fresh folder, so `chat_panel._on_new` repoints the
  running worker with `set_cwd` alongside `set_log_dir` — without that the CLI
  keeps running in the previous conversation's folder. A temp dir is the
  fallback only if the session folder can't be made. Everything shown to Claude
  cites the references relative to that cwd (`session.ref_path`), which is what
  lets one substitution in the prompt stay correct across a "New".
- `--tools ""` disables ALL built-ins (incl. `Skill`). We enable a safe set:
  `Read`, `Write` and `Edit` (always — skill reference files and plain-text file
  authoring), `Glob`/`Grep` (always — file search), the `Task*` family (todo +
  Plan subagent), `Skill` when a skills project is configured, and `PowerShell`
  on Windows only (`_SHELL_TOOLS`, empty elsewhere — the name doesn't resolve on
  macOS and needs an opt-in on Linux). The only path that mutates the *live
  FreeCAD document* is `run_python`; the rest reach the filesystem but never the
  document. A shell is not extra reach — `run_python` is already arbitrary
  Python in the FreeCAD process — but it runs in the CLI subprocess, so unlike
  `run_python` it can't block the GUI thread. `Bash` stays off: one shell is
  enough and PowerShell is the one matching the deploy target.
- An unrecognised name in `--tools` is **dropped silently**, with no warning and
  no error — a renamed or misspelt tool degrades capability invisibly. The
  `system` init event in `stream.jsonl` lists what the CLI actually resolved;
  that is the only place a drop shows up.
- The subagent launcher is reported as `Agent` in tool_use even though enabled via
  `Task`; `Agent` is in the allow-list so subagents (e.g. `Plan`) don't prompt.
  The CLI treats the two names as aliases, so listing both in `--tools` is
  redundant, not additive.

## Dev workflow

- **Deploy:** `pwsh -File deploy.ps1` copies into the **version-namespaced** user
  Mod dir (`%APPDATA%\FreeCAD\v1-1\Mod\FreeCADClaude`), resolved via
  `freecadcmd -c "import FreeCAD; print(FreeCAD.getUserAppDataDir())"`. Restart
  FreeCAD after deploying.
- **No Python deps** to install (we drive the CLI). `install_deps.ps1` just
  verifies the `claude` CLI is present/logged in.
- **Headless testing:** `freecadcmd <script.py>` for App-side logic (tool
  functions, parsing). GUI-only bits (FreeCADGui, QApplication) need
  `QT_QPA_PLATFORM=offscreen` and may lack fonts/`activeView`. Give `freecadcmd`
  an **absolute** path — with a relative one it runs nothing and still exits 0.
  After touching the device feature, re-run `eval/test_device_server.py`,
  `test_device_tools.py`, `test_qr.py` and `test_capture_scale.py`; the first
  three also run under a plain `python3`. After touching the slice feature,
  re-run `test_slicer_runner.py` and `test_gcode_server.py` (both under a plain
  `python3`) plus `test_slice_tools.py`, `test_export_3mf.py` and
  `test_oriented_export.py` under `freecadcmd`. **No test may execute a real
  slicer or spawn `sys.executable` under FreeCAD** — both slicers are GUI
  applications, so an unaccepted flag opens a modal dialog on the user's
  desktop. `test_slicer_runner.py` drives a `shutil.which("python3")` fake;
  everything else refuses before anything is spawned.
- **The two web apps:** `cd web && npm ci` once, then `npx vitest run` and
  `npm run build`; and the same in `gcode_web`. Each build writes a committed
  folder — `freecad/freecadclaude/device_ui/` and `.../gcode_ui/` respectively —
  so rebuild and commit the output alongside any source change in either.
- **End-to-end eval:** `python3 eval/run.py [-p ... -e <regex>]` (cross-platform
  — one stdlib-only script, no venv needed) — launches FreeCAD, runs a prompt
  through the real agent, snapshots the doc to
  JSON, exits 0/1/2. Sets the `FREECADCLAUDE_EVAL*` env vars that `InitGui.py` →
  `eval_runner.py` acts on. On Windows it kills a runaway FreeCAD via
  `taskkill /IM freecad.exe` (the exe detaches, so there's no PID to track);
  on macOS/Linux the spawned PID *is* FreeCAD, so it kills only that PID.
  - **The result JSON is a shallow snapshot** (object names/types/dims) — fine
    for an `-e`/`--expect` regex ("did object X get made"), but it can't tell you
    *how* the agent behaved. To judge a behaviour/prompt change (tool-call
    order, cut direction, whether a `⚠` note fired, how many steps it took),
    read the run's own session folder — `stream.jsonl` for the tool calls and
    the per-op volume/solid delta + `⚠` notes in each tool result, and
    `scripts/` for the ordered `run_python` calls (see "Diagnosing a past
    conversation" below). That trace, not the snapshot, is the real signal.
    `run.py` prints the session path on exit.
- **Diagnosing a past conversation:** everything for it lives in
  `~/FreeCADClaude/<session-id>/` — `stream.jsonl` (the raw JSON the `claude`
  CLI streamed, turn by turn), `scripts/` (every `run_python` call,
  success or failure), `captures/`/`exports/` (images/exported files), and
  `slices/<job>/` (the 3MF handed over, the G-code and `result.json` that came
  back, `slicer.log`, and `job.json` with the argv actually used). See
  the "Tools" section above for how `<session-id>` is chosen. The "Open Files"
  button in the chat panel opens `~/FreeCADClaude` itself (all sessions).
- **Releasing:** see `RELEASE.md`. Short version — users install from the `main`
  **branch** (the Addon Manager custom-repo entry and `package.xml` both pin
  `branch="main"`), so a tag ships nothing and the GitHub release is just the
  changelog. The step that isn't optional is bumping `<version>`/`<date>` in
  `package.xml` in the same commit you tag: that number is what the Addon
  Manager shows users, so a tag without it is invisible to them.

## Conventions

- **PySide:** always `from PySide import ...` (FreeCAD's bundled binding), never a
  pip `PySide6`.
- **No asyncio.** The worker is a plain `QThread` + `queue.Queue`; the CLI's
  streaming call is synchronous.
- **Lazy GUI imports:** `InitGui.py` and tool `run` functions import
  `FreeCAD`/`FreeCADGui` inside functions where it matters.
- Keep the App/GUI split clean; tool execution always on the GUI thread.
- Commits: **committing and pushing straight to `main` is fine on this project** —
  no branch/PR needed unless asked. End messages with the `Co-Authored-By` trailer
  used in this repo's history.

## Just-in-time reference pointers

**A "read this file before you do X" instruction in the system prompt is close to
a no-op.** Measured against the logged sessions, reference files told about in the
prompt go essentially unread however much work their territory sees. Two reasons,
and only one is a wording problem:

1. The trigger fires on a moment the model can't observe — "before writing
   unfamiliar code" asks it to notice it's about to do something it doesn't know
   well, which is exactly the judgement an over-confident model skips.
2. `inspect_api` is a genuinely better answer for a signature (ground truth from
   the running install, and it can't drift), so it wins. A reference only earns
   its keep for what `inspect_api` *can't* return — pitfalls, the both-required
   property pairs, the recipes. Chasing a 100% read rate would be chasing the
   wrong number.

So a pointer is attached to a **detected condition** instead, arriving with the
evidence in a tool result — the same channel that makes the `⚠` notes work.

**Better timing changes when a pointer arrives, not whether the file gets
opened.** The condition triggers fire reliably, and are still almost never
followed by a `Read` of the cited file. Two rules follow:

1. A pointer's read rate is roughly independent of how well-timed it is. Don't
   spend another round of wording on it.
2. **Carry the payload, cite the file only for the tail.** `_EDITING_RULES` is
   the model: it needs no reads at all, because the four rules are *in* the note
   and only the API forms/external-geometry residue sits behind the link.
   `_PARTDESIGN_ESSENTIALS` follows the same shape. A note that has to be
   followed to be useful mostly isn't.

**The snapshot is a shape FINGERPRINT, and it is cached by OCCT shape hash.**
`_feature_states` records `(contribution, solids, faces, edges, vertexes, bbox,
valid)` per feature, not just volume+solids — volume alone is a lossy way to ask
"did this change", since a feature can re-topologise, move, or go invalid at
constant volume. `_shape_metrics` caches those
measurements under `(object name, Shape.hashCode())`, which is sound because they
are a pure function of the shape — a rebuilt shape gets a new hash and misses.
Hashing a chain is orders of magnitude cheaper than reading `Volume`/`Solids`, so
validity is not the expensive part; `Volume` is, and the diff pays it anyway.
With the cache, a call that rebuilds nothing costs almost nothing, which matters
because this runs on the GUI thread.

The five notes, and what each one is:

- `diagnostics.summarize_chain_changes(before)` fires when a call changed **which
  features reach a Body's Tip**. This is the one structural break nothing else
  catches: a dropped feature keeps a valid shape and recomputes without error, it
  just stops contributing — so neither the Invalid scan nor the volume diff says
  a word, and a break can go unnoticed for dozens of calls. The
  note echoes the **pre-call chain** back, because that's what's most expensive
  to recover later: once the break is spotted, the only chain still in context is
  the broken one, and a repair then rebuilds toward a guessed order. Three shrink
  causes are *not* breakage and are classified apart: a feature the call deleted,
  the Tip moved back on purpose (plain line, no ⚠ — those features are still
  linked, just past the Tip), and a Tip left dangling by `doc.removeObject` (its
  own ⚠, since "re-link via BaseFeature" would be the wrong fix). It also
  subsumes the tip-cycle gotcha below: a scripted `newObject` that wires a cycle
  trips this on the very call that does it.

- `diagnostics.summarize_validity_changes(before)` fires when a feature's shape
  stops being **geometrically valid**. The failure the volume diff is blind to by
  construction: an invalid solid recomputes without error, keeps its solid count
  and reports a plausible volume, so every other gate passes it and the model
  signs off on a document that "recomputes clean, one solid" while its tip shape
  fails OCCT validation. Three things make it usable
  rather than a nag: it reports the **first** such feature in the tip chain, not
  the set (invalidity is inherited downstream, so a list names innocents);
  `_invalid_subshapes` localises it to specific faces/edges with world extents,
  since a bare `False` is unactionable; and `_operation_region` **cross-checks
  before naming a cause**, so a `Fillet` whose bad faces sit nowhere near the
  region it worked on is reported as such rather than blamed for its radius.
  An invalid tip shape is rare across real documents, so treat the note as a real
  finding rather than routine noise. It does *not*
  call `Shape.check()` — the BOP check can run for seconds, and this is the GUI
  thread.

- `diagnostics.summarize_new_failures(before)` escalates when
  `_broke_an_existing_feature` holds (a previously-fine feature went Invalid) and
  hands off to `_pre_existing_failure_note`, which **works out which cause it is
  before naming one**. The symptom does not identify the cause: it is shared
  between a tip cycle and **topological naming**, and topological naming is much
  the more common of the two. So:
  `_on_basefeature_cycle` follows `BaseFeature` and reports the cycle only when
  the chain actually repeats; `_pinned_subelements` detects a dress-up
  hardcoding `EdgeNN` names and names topological naming instead, with the
  offending edges; and when neither is confirmable the note says both are
  possible rather than picking. **Confidently naming the wrong cause is worse
  than naming none** — it costs the model a turn to argue with.
- `tools_sketch._editing_rules_note()` appends the rules for changing an existing
  sketch to the **first `get_sketch` of each conversation** (keyed on
  `active_session_id()`, so "New" re-arms it). A `get_sketch` call *is* "I am
  about to edit a sketch", so it's the one moment those rules are relevant.
- `diagnostics._partdesign_reference_note(before)` delivers
  `_PARTDESIGN_ESSENTIALS` the first time a conversation leaves a
  `PartDesign::Body` in the document. The trigger is the **Body**, not a feature,
  because a Body is usually created in its own call — which puts the note ahead
  of the first feature rather than after it. Its contents track the errors the
  logs actually show (`KeyError: 'XY_Plane'`, a feature object passed where
  `newObject` wants a type string, `Tip` read as `None`, `Transformed`/
  `SubElementNames` guessed on a pattern, `Body: object is not allowed`, link
  properties read back as objects rather than tuples), not what the reference
  happens to cover; `partdesign-scripting.md` is cited at
  the end for Revolution/Groove, Loft/Pipe, Hole, datums and MultiTransform.

Which references get a trigger is decided on measurement, comparing each one's
territory on how early its work starts, how much work follows, and how often that
work errors.

PartDesign is where wrong-property guesses actually cost something: it errors at
well above the baseline rate, starts early in a session, and most sessions that
create one feature go on to create more, so a pointer there front-runs real work.
Part-primitive/Draft work errors below baseline and usually stops after the first
call — a pointer there would be noise, so `part-draft-recipes.md` has
no trigger and is reachable only from the prompt's reference list. Don't add one
"for symmetry" without re-running the numbers.

When you add a reference file, ask which observable condition should cite it, and
whether the work it covers actually fails. If neither, expect it not to be read —
and that may be fine.

## Gotchas (learned the hard way)

- FreeCAD 1.x uses a **version-namespaced** user dir (`…\FreeCAD\v1-1\`). Deploying
  to the unversioned path is silently ignored.
- `InitGui.py` is run via `exec()` **without `__file__`**, and module-level names
  in it are **not visible to methods called later** (they resolve against
  FreeCAD's loader globals). Reference resources via the importable package
  (`from freecad import freecadclaude; …__file__`), and import names **inside**
  workbench methods.
- `package.xml` workbench needs `<subdirectory>.</subdirectory>` or FreeCAD looks
  for `InitGui.py` in a phantom subfolder named after the workbench.
- `freecad.exe file.py` does **not** run a startup macro, and the exe **detaches**
  (returns immediately). Trigger startup logic from `InitGui.py`; for unattended
  runs, **poll for an output file**, don't wait on the process.
- Waiting for a turn on the GUI thread must use a **nested `QEventLoop`**, never
  `sleep` — otherwise the bridge can't marshal tool calls and it deadlocks.
- Spawn the CLI with `creationflags=CREATE_NO_WINDOW` + piped stdio, or a console
  window pops up under the windowed FreeCAD process and can hang it.
- `run_python` runs inside an `openTransaction`/`commit` (undoable); on error it
  aborts AND removes newly-added objects (undo may be off in some contexts).
- **A loop of Part shape operations doesn't run slowly — it freezes FreeCAD, and
  nothing can preempt it.** `run_python` executes on the GUI thread, so a call
  that takes minutes is minutes of a dead application — a sampling loop of
  `Shape.slice()` over a real solid will hold it for minutes. Three things follow,
  and all three are handled:
  - `tools_python._heavy_loop_note` (a `run_python` precheck) refuses the call
    statically, before it reaches the GUI thread, when a heavy op runs inside a
    loop of unknown or >50 trip count. It resolves calls through locally-defined
    helpers, because the expensive call is usually one level down: the `slice()`
    sits inside a helper rather than in the loop body, where a lexical check
    would miss it. The
    threshold separates the sampling loops that read fine from the ones that
    froze, and `# slow-ok`
    anywhere in the code skips it — a loop that must run costs one round-trip,
    which is the point.
  - **`isInside` belongs on that heavy list even though it looks like a cheap
    point test.** FreeCAD's
    `TopoShapePy::isInside` builds a fresh `BRepClass3d_SolidClassifier` on
    *every* call, so it walks the whole solid per point; under a sampled freeze
    most stacks sit in the classifier's constructor before any classifying
    happens. Cost is linear in face count, so it degrades silently as
    the model gets real. The one-shot form is a boolean against a line:
    `shape.common(Part.makeLine(a, b)).Edges` returns the material intervals
    along the whole line in one call, orders of magnitude faster than a point
    scan and exact rather than quantised to the step. For a height or
    width profile, `shape.slices(dir, [d1, …])` is one call for all planes and
    gives every coordinate, not the few that got sampled.
  - Those one-shot forms return a `Part.Compound` — a `Part.Shape` subclass, not
    a Python sequence, so no iteration and no `len()`; contents come from
    `.childShapes()`/`.SubShapes` (one level) or `.Solids`/`.Wires`/`.Edges`
    (flattened). `slices()` returns every wire from every plane in one flat
    compound, not one per distance, so `zip(distances, result)` fails twice over.
    `_heavy_loop_note` states both, since it is what recommends these calls.
  - The bridge's `GuiBusyTimeout` (`_GUI_CALL_TIMEOUT`, 600 s) reports the call
    as **still running**, not failed. It is not a cancellation — the call keeps
    going and still commits — so the message's job is to stop the obvious
    response ("resend it"), which would queue a second copy behind the first and
    apply the same change twice.
  - `mcp_server._CALL_TIMEOUT` (900 s) must stay **above** that 600 s. If the
    socket gives up first, the reply lands on a closed socket, gets dropped by
    `_handle`'s `except OSError`, and Claude is told a call failed that in fact
    succeeded. Whoever times out first has to be the side that can describe
    what's happening — that's the bridge. The dropped-reply path warns into the
    report view via `gui_bridge._warn`.
- Box `Length`/etc. are `Quantity` objects (`str` → "20.0 mm"); use the numeric
  input or `.Value`.
- **Assigning `sketch.Geometry` to move constrained geometry silently mangles it.**
  It doesn't raise — the solver just drags the geometry back to satisfy the old
  constraints (overwrite a line to 6mm while a `DistanceX=10` holds it and FreeCAD
  keeps it 10mm, flinging the start point to -3.08). `moveGeometry` only shifts
  *underconstrained* geometry, by its own contract. The only correct way to move
  constrained geometry is `setDatum(constraintIndex, value)`.
- `inspect_api` on a document-object *instance* must walk **both** branches: the
  `PropertiesList` one and `dir()`. A sketch's methods and its non-property
  attributes (`DoF`, `ConflictingConstraints`) live only in the latter, and
  without them the model can only guess names — `movePoint`, `setGeometry`,
  `getDoF()` do not exist.
  `Sketcher.SketchObject` also isn't an importable class; `_describe_by_type_id`
  resolves that (and any `Type::String`) to a live instance in the document.
- **Assigning a dress-up's `Base` a DIFFERENT feature silently re-parents it,
  cutting every feature in between out of the Body.** `DressUp::onChanged`
  (`FeatureDressUp.cpp:276`) sets `BaseFeature` to whatever `Base` was just
  pointed at — so `fillet.Base = (other_feature, [...])`, which reads as an
  edge-list edit, is a chain edit. The features it cuts out keep recomputing
  cleanly on their own branch, so nothing errors and the damage stays invisible.
  **A dress-up's `Base[0]` must always be its own
  immediate predecessor.** To fix stale edge names, keep `Base` on the same
  feature: recompute *that* feature first, then read the names off its `Shape`.
  Reading them off a not-yet-recomputed shape makes the correct fix look like it
  failed, which is what tempts the object swap. Detected by
  `summarize_chain_changes`, and stated inline in `_PARTDESIGN_ESSENTIALS`.
- **`doc.removeObject()` on the feature a Body's `Tip` points at leaves `Tip`
  dangling**, and the Body then builds no shape at all while every feature in it
  still recomputes fine. Set `body.Tip` to whichever feature should now be last.
- **Scripted `body.newObject(...)` onto a Body that has a datum feature (e.g. a
  `PartDesign::Plane`) sitting in `Group` between the current Tip and its
  predecessor can wire a circular `BaseFeature`.** (`Body::setBaseProperty` picks
  the new feature's neighbours with `getPrevSolidFeature`/`getNextSolidFeature`,
  which walk **`Group` order**, not the chain — so a Body whose `Group` order and
  chain disagree is the precondition. That disagreement is common across real
  documents and normally harmless, so it is *not* worth flagging on its own.)
  The new feature's `BaseFeature` points back correctly while the *previous* tip
  gets rewired forward to the new feature — a two-node cycle. Symptom: the older
  feature reports `Invalid` after a successful-looking `run_python` call, and a
  forced recompute on it throws `RuntimeError: The graph must be a DAG`. Fix by
  reassigning the older feature's `BaseFeature` directly back to its true
  predecessor — do **not** try to fix it via
  `Body.insertObject`/`Group` reordering, which reproduces the same cycle (and can
  duplicate the `Group` entry). Worked example in
  `freecad/freecadclaude/references/partdesign-body-tip-cycle-gotcha.md`.
- **QR version 6 at EC level L is two error-correction blocks, not one**, and a
  single-block version 6 produces a structurally perfect symbol that decoders
  read as nothing at all. `eval/test_qr.py` pins full reference matrices from
  `qrcode` and decodes them back with `zxing-cpp` — neither a dependency of the
  addon — which is the only reason this surfaced. Blocks are equal-sized across
  versions 3–6, so `_interleave` is a `zip` with no group-1/group-2 split.
