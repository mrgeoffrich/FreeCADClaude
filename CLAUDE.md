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
| `freecad/freecadclaude/flow_layout.py` | `FlowLayout` — a wrapping row layout, used for the chat panel's control strip (model combo + Files/New/Stop/Send). A `QHBoxLayout`'s minimum width is the *sum* of its children's, and a `QPushButton`'s own minimum is ~80px however short its label — so that strip alone floored the dock at **428px** (measured; the row contributed 420 of it) even though the transcript needs 88 and the input 90. Wrapping makes the layout's minimum the *widest single item* while its `sizeHint` stays the one-row width, so the dock opens wide but can now be dragged to ~98. Rows are flush right, matching where the strip sits. |
| `freecad/freecadclaude/dock_panel.py` | The singleton dock shell both panels subclass (`DockPanel`): lazy creation, reuse-by-`objectName` across a workbench reload, `instance()`/`widget`. Subclasses supply the inner widget and, via `_on_created`, what happens to a fresh dock (chat raises itself; the plan dock tabs in behind it). |
| `freecad/freecadclaude/agent_worker.py` | Drives the `claude` CLI per turn; parses stream-json → Qt signals. |
| `freecad/freecadclaude/agent_config.py` | Model, system prompt (loaded from `system_prompt.md`), CLI flags (tools/mcp/cwd/skills). |
| `freecad/freecadclaude/system_prompt.md` | The system prompt text itself, edited as plain Markdown. Its `{REFS_DIR}` placeholder is replaced by `agent_config` at load with the absolute path of `references/`. |
| `freecad/freecadclaude/references/` | run_python scripting references (sketcher / partdesign / part-draft) the system prompt tells Claude to `Read` on demand — progressive disclosure without a skill gate (the old `freecad-run-python` skill collapsed into these + the prompt's execution-contract section). |
| `freecad/freecadclaude/gui_bridge.py` | In-FreeCAD socket server; runs tools on the GUI thread; run_python arg precheck. |
| `freecad/freecadclaude/freecad_tools/` | The tools, as a package — see its own map below. `__init__.py` holds the `TOOLS` registry and re-exports the facade the rest of the addon imports (`TOOLS`, `list_schemas`, `feature_snapshot`, `post_tool_notes`, the session-dir helpers), so `from . import freecad_tools` still reaches everything. |
| `freecad/freecadclaude/_deps.py` | Locates the `claude` CLI. |
| `freecad/freecadclaude/eval_runner.py` | Unattended end-to-end eval (triggered by env var). |
| `mcp_server.py` | Stdlib-only MCP stdio server the CLI spawns; relays to the bridge. |
| `deploy.ps1` / `install_deps.ps1` / `eval/run.py` | Dev tooling (not deployed). |

## Tools

Registry: `freecad_tools.TOOLS` = name → `{schema, run, precheck?}`. Current set:
`get_objects`, `get_selection`, `get_sketch`, `view_sketch_svg`, `capture_view`,
`capture_user_view`, `crop_view`, `cutaway`, `annotate_view`, `read_annotation`,
`export`, `inspect_api`,
`get_diagnostics`, `run_python` (the sole document-mutating tool —
the general Sketcher/PartDesign/Part path).

**There is no approval gate.** `run_python` used to open a confirmation dialog
per call (with a "Run all this session" button). It was removed — in practice the
first dialog of every session was answered with "Run all", so it cost one click
and bought nothing. Tool calls now execute as soon as they arrive. What still
stands between a bad call and a damaged document is the transaction (a raising
call rolls back whole, newly-created objects removed) and, optionally, the
`SaveSteps` snapshots. Note the scope this gives up: `run_python` is arbitrary
Python in the FreeCAD process, so it can touch the filesystem, not just the
document. That's the deliberate trade for a personal-use addon.

The package is one `tools_*` module per concern over a base of shared
infrastructure. Dependencies run **tools → infra only** — keep it that way; the
infra modules import nothing from `tools_*` (that's why `_ERROR_FLAGS` and
`_solver_constraint_indices` live in `diagnostics`, not next to their callers).

| Module | Role |
|---|---|
| `__init__.py` | The `TOOLS` registry, `list_schemas()`, and the facade re-exports. |
| `tools_document.py` | `get_objects`, `get_selection`. `get_objects` also returns a `bodies` section — each `PartDesign::Body`'s **tip chain**, base-first (see `diagnostics._body_states`). That's the only place a Body's build order is visible, and it's in the tool the model already calls first; a feature missing from the chain contributes nothing however healthy it looks. Costs ~518 bytes of a 15.7 KB payload on a 68-object/3-body document. |
| `tools_python.py` | `run_python` (+ its syntax precheck). |
| `tools_inspect.py` | `inspect_api`. |
| `tools_sketch.py` | `get_sketch`, `view_sketch_svg` (+ the GeoId overlay). |
| `tools_capture.py` | `capture_view`, `capture_user_view`, `crop_view`. |
| `tools_annotate.py` | `annotate_view`, `read_annotation` — the draw-on-the-screenshot round trip. |
| `tools_cutaway.py` | `cutaway` (+ clip-plane resolution). |
| `tools_export.py` | `export`. |
| `session.py` | Artifact folders: the per-conversation session dir, the script/step archives (`_session_subdir`/`_safe_name` build every artifact path), plus the two paths that aren't artifacts but that several modules must agree on: `PARAM_PATH` (the preferences root) and `REFS_DIR` (the bundled `references/`). Both are single-spelled here and imported by `agent_config` rather than re-declared — `agent_config` substitutes `REFS_DIR` into the prompt's `{REFS_DIR}` while the tools cite paths under it in their notes, so a second copy could drift and hand Claude a path that doesn't resolve. `active_session_id()` is how a tool remembers "already said this in this conversation" without inventing its own notion of when one starts. |
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
`references/*.md` scripting references), `Write` (author plain-text
files, e.g. `freecad-lofi-sketch`'s SVGs), and `Glob`/`Grep` (file search — find
files by name/path, search their contents; so Claude can locate a STEP/STL to
import or a previous export before Reading it). All run inside the `claude` CLI
process itself, not the MCP bridge; all are read-only except `Write`, which
authors files on disk but never touches the live document. `Glob`/`Grep` used to
be gated behind a configured skills project — they're now always-on (decoupled
from `_SKILL_TOOLS`, which is now just `Skill`), since file search is a general
capability, not a skill-only one.

**Sketch editing** (`get_sketch`, read-only): every Sketcher mutation is addressed
by **GeoId** (`moveGeometry`, `addConstraint`) or **constraint index** (`setDatum`,
`delConstraint`), and nothing else in the tool set exposes either — `get_objects`
gives a bbox, and `view_sketch_svg`'s exported paths *fuse* connected edges into
unlabelled wires and omit construction/external geometry entirely. So `get_sketch`
is the only way to edit an existing sketch without guessing: it returns every
geometry element with its GeoId/coords/construction flag, every constraint with its
index/operands/datum, a `constraints_by_geoId` reverse index, the solver state, and
external geometry. `view_sketch_svg` now overlays GeoId labels + the omitted
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
not a filled cross-section — Coin's clip doesn't cap); a capped section would mean
a geometry Boolean cut on temporary objects, deliberately not done to keep the
tool non-mutating like `capture_view`. Both share `_resolve_camera_args`/
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

**Draw style** (`style` on both `capture_view` and `cutaway`, schema shared via
`render._STYLE_SCHEMA_PROPS`): `shaded` (default), `xray`, `wireframe`.
`_force_draw_style` is the single place the viewer's override mode is set — and
that override is *why* a ViewObject's `DisplayMode` has no effect on a capture
(it deliberately outranks per-object modes so a shot can't inherit e.g. `Points`
from one object). Measured the confusing way first: `DisplayMode` read back as
`Wireframe` while the render came out shaded. `wireframe` is therefore a per-view
override — no document mutation, dies with the throwaway view. `xray` has no
draw-style equivalent, so it goes through `Transparency` (60%; at 80 the form
dissolves into the background) and must be restored.

**`_shot_appearance` saves in one pass and applies in a second, and that split is
load-bearing.** Setting `Transparency` on a Body **propagates to the features
inside it**, so a save-then-set-as-you-go loop reads an already-propagated value
for objects it reaches later and "restores" them to the shot's value. That leaked
60% transparency onto 4 objects of a real document before the split was added.
The same hazard applies to any ViewObject property that propagates — record
everything before changing anything. Note also what the override does *not* cover:
`Hidden Line` renders identically to `Shaded` on FreeCAD 1.1 (no edge lines), so
it isn't offered.

**A crop defaults its omitted axes to the SHOWN objects, and the image is shaped
to the geometry** — both learned from one session where Claude could not get a
usable picture of a 122×6.6mm door and gave up on looking at it entirely
(*"raster crops aren't helping on an 18:1 strip; I'll analyse the geometry
numerically instead"*), which is what led to the ~1,700-`slice()` call that froze
the GUI for 2m46s. Two separate causes:
- `_apply_extent_crop` called `_document_bbox(doc)` with no `names`, so an axis
  the caller didn't specify defaulted to the **whole document** — cropping x on
  one object out of 36 blew y and z out to everything else in the file. The door
  (Y 114..120.6) got framed against the document's Y −6.6..120.6, landing at 4.9%
  of frame height jammed on the top edge: a *narrower* crop rendered worse than
  no crop. Predicted-vs-observed framing matched to within 1% on both axes, and
  `_shown_extents_note` had been reporting the right box while the camera framed
  a different one. `_framed_box(doc, keep_names, extents)` is now the single
  definition of "the box a capture frames", used by both the framing and the
  auto-size so they cannot disagree.
- Even framed correctly, an 18:1 part in a fixed 4:3 image is 6.8% geometry and
  ~90% black. `render._fit_render_size` now shapes the *image* to the box's
  on-screen aspect (`_screen_half_extents` off the live camera basis — it must
  run after `_apply_camera_plan`, since how wide a box looks depends on where the
  camera ended up). Same 1.23 MP budget and 1568px long-edge ceiling, clamped to
  `_MAX_AUTO_ASPECT` 4:1; 4:3 input still yields exactly 1280×960, so ordinary
  parts are untouched. An explicit `width`/`height` disables it, and every
  unclear case (no ortho camera, degenerate box, framing refused) falls back to
  the old size — it can only improve a shot or leave it alone.
`view_sketch_svg` (exact SVG; for
3D pass `view=front/top/...` → `TechDraw.projectToSVG` orthographic) is for
reasoning about exact coordinates as text, not for looking at the shape — its
3D-projection path data is tessellated into many small segments and isn't
meant to be read directly either. Artifacts go to `~/FreeCADClaude/<session-id>/{captures,exports,scripts}`
(the user's home directory, **not** FreeCAD's `UserAppData`) — `<session-id>` is a
readable id (`YYYYMMDD-HHMMSS-<6 hex>`) minted by `freecad_tools.new_session_id()`
when a chat starts and again on "New" (`chat_panel._ensure_worker`/`_on_new`), so
every conversation gets its own folder; `session_dir()` resolves the active one
(older session folders are pruned, keeping the most recent 40). `captures`/
`exports`/`scripts` are written by FreeCAD tools via `_artifact_path` (auto-pruned, kept
≤60 files each); `scripts` holds a `.py` copy of every `run_python` call
(written by `_save_run_python_script`, right before `exec`, so both successful and
failed runs are archived); the same session folder also holds `stream.jsonl` — the
raw newline-delimited JSON `agent_worker` reads from the `claude` CLI, appended
turn-by-turn (`AgentWorker._open_log`) — handy for diagnosing a turn after the fact.
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
`--append-system-prompt` (turn 1) or `--resume <id>` (later). cwd = the skills
project dir (so its `.claude/skills` load) else a temp dir.

- `--tools ""` disables ALL built-ins (incl. `Skill`). We enable a safe set:
  `Read` and `Write` (always — skill reference files and plain-text file
  authoring), `Glob`/`Grep` (always — file search), the `Task*` family (todo +
  Plan subagent), and `Skill` when a skills project is configured. `Bash`/`Edit`
  stay OFF — the only path that mutates the *live FreeCAD document* is
  `run_python`; `Write` can create/overwrite arbitrary files on
  disk but never touches the document.
- The subagent launcher is reported as `Agent` in tool_use even though enabled via
  `Task`; `Agent` is in the allow-list so subagents (e.g. `Plan`) don't prompt.

## Dev workflow

- **Deploy:** `pwsh -File deploy.ps1` copies into the **version-namespaced** user
  Mod dir (`%APPDATA%\FreeCAD\v1-1\Mod\FreeCADClaude`), resolved via
  `freecadcmd -c "import FreeCAD; print(FreeCAD.getUserAppDataDir())"`. Restart
  FreeCAD after deploying.
- **No Python deps** to install (we drive the CLI). `install_deps.ps1` just
  verifies the `claude` CLI is present/logged in.
- **Headless testing:** `freecadcmd <script.py>` for App-side logic (tool
  functions, parsing). GUI-only bits (FreeCADGui, QApplication) need
  `QT_QPA_PLATFORM=offscreen` and may lack fonts/`activeView`.
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
  success or failure), and `captures/`/`exports/` (images/exported files). See
  the "Tools" section above for how `<session-id>` is chosen. The "Files"
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
a no-op, and this was measured, not assumed.** Across the 33 logged sessions that
ran `run_python` since the `references/` files landed (12 Jul 2026 — the same
commit that told Claude to read them), `sketcher-scripting.md` was read **zero
times** despite ~16 of those sessions doing sketch work, `part-draft-recipes.md`
zero times, and `partdesign-scripting.md` 4 times against ~14 sessions doing
PartDesign work. `inspect_api` was called 46 times over the same period. Two
reasons, and only one is a wording problem:

1. The trigger fires on a moment the model can't observe — "before writing
   unfamiliar code" asks it to notice it's about to do something it doesn't know
   well, which is exactly the judgement an over-confident model skips.
2. `inspect_api` is a genuinely better answer for a signature (ground truth from
   the running install, and it can't drift), so it wins. That means a reference
   only earns its keep for what `inspect_api` *can't* return — pitfalls, the
   both-required property pairs, the recipes. Chasing a 100% read rate would be
   chasing the wrong number.

So the pointer is attached to a **detected condition** instead, arriving with the
evidence in a tool result — the same channel that makes the `⚠` notes work.

**That fixed when the pointer arrives, not whether the file gets opened — also
measured.** Over the 13 logged sessions since the triggers landed (30 Jul,
`a2e79f9`), the pointer notes fired **11 times across 9 sessions** — the trigger
mechanism works — but were followed by a `Read` of the cited file **once**, with
**171 `run_python` calls** running past an unopened reference. That is the same
near-zero read rate the prompt-based instruction got. Two conclusions, and the
second is the design rule now:

1. A pointer's read rate is roughly independent of how well-timed it is. Don't
   spend another round of wording on it.
2. **Carry the payload, cite the file only for the tail.** `_EDITING_RULES` was
   already built this way and is the one that works: it fired in 6 sessions and
   needed zero reads, because the four rules are *in* the note and only the API
   forms/external-geometry residue is behind the link. `_PARTDESIGN_ESSENTIALS`
   was rewritten to match (it used to be a bare "read this file", ignored 10
   times out of 11). A note that has to be followed to be useful mostly isn't.

**The snapshot is a shape FINGERPRINT, and it is cached by OCCT shape hash.**
`_feature_states` records `(contribution, solids, faces, edges, vertexes, bbox,
valid)` per feature, not just volume+solids — because volume alone is a lossy
way to ask "did this change": a feature re-topologised, moved, or left invalid at
constant volume was invisible to the old diff. `_shape_metrics` caches those
measurements under `(object name, Shape.hashCode())`, which is sound because they
are a pure function of the shape — a rebuilt shape gets a new hash and misses.
Measured on a 10-feature chain: hashing the chain is **0.03 ms**, reading
`Volume`/`Solids` is **99 ms**, `isValid()` is **51 ms** — so validity was never
the expensive part, `Volume` was, and we were already paying it. Net effect on a
23-feature document, per `run_python` call (two passes): **741 ms → 0.4 ms** when
nothing rebuilt, ~140 ms when a mid-chain feature does. The richer snapshot is
several times *cheaper* than the thin one it replaced, and that matters because
this runs on the GUI thread.

The five notes, and what each one is:

- `diagnostics.summarize_chain_changes(before)` fires when a call changed **which
  features reach a Body's Tip**. This is the one structural break nothing else
  catches: a dropped feature keeps a valid shape and recomputes without error, it
  just stops contributing — so neither the Invalid scan nor the volume diff says
  a word. Measured on the session that prompted it: two features left the chain
  on `run_python` call **3 of 40** and it wasn't noticed until call **30**. The
  note echoes the **pre-call chain** back, because that's what's most expensive
  to recover later — once the break is spotted, the only chain still in context
  is the broken one, and the repair then rebuilds toward a guess (that session's
  repair invented an order the document had never had). Three shrink causes are
  *not* breakage and are classified apart, each found by the pristine-document
  tests rather than reasoned about: a feature the call deleted, the Tip
  deliberately moved back (plain line, no ⚠ — those features are still linked,
  just past the Tip), and a Tip left dangling by `doc.removeObject` (its own ⚠,
  since "re-link via BaseFeature" would be the wrong fix). It also subsumes the
  tip-cycle gotcha below: a scripted `newObject` that wires a cycle now trips
  this on the very call that does it.

- `diagnostics.summarize_validity_changes(before)` fires when a feature's shape
  stops being **geometrically valid**. The failure the volume diff was blind to by
  construction: an invalid solid recomputes without error, keeps its solid count
  and reports a plausible volume, so every other gate passes it. Found by the
  eval that followed the tip-chain work — the agent signed off *"document
  recomputes clean, one solid"* on a tip shape that fails OCCT validation, and
  `isValid()` was called **nowhere** in the package. Three things make it usable
  rather than a nag: it reports the **first** such feature in the tip chain, not
  the set (invalidity is inherited downstream, so a list names innocents);
  `_invalid_subshapes` localises it to specific faces/edges with world extents,
  since a bare `False` is unactionable; and `_operation_region` **cross-checks
  before naming a cause** — on the replay it caught a `Fillet` working at Z 48..55
  whose bad faces were all at Z 0..14, and said so instead of asserting "your
  radius is too big". Base rate is low enough to be worth saying: **1 of 57**
  bodies across real documents has an invalid tip shape. Deliberately does *not*
  call `Shape.check()` — the BOP check can run for seconds, and this is the GUI
  thread.

- `diagnostics.summarize_new_failures(before)` escalates when
  `_broke_an_existing_feature` holds (a previously-fine feature went Invalid) and
  hands off to `_pre_existing_failure_note`, which **works out which cause it is
  before naming one**. It used to assert the tip cycle outright from that
  symptom, and that was wrong: measured, the note fired in 2 sessions since it
  landed and *neither* was a cycle (no `must be a DAG` in either log), while in
  one Claude had to overrule it — *"its `Base` pins literal edge names
  (`Edge4…Edge18`)… Not a BaseFeature [cycle]"*. The symptom is shared with
  **topological naming**, which is much the more common cause. So now:
  `_on_basefeature_cycle` follows `BaseFeature` and reports the cycle only when
  the chain actually repeats; `_pinned_subelements` detects a dress-up
  hardcoding `EdgeNN` names and names topological naming instead, with the
  offending edges; and when neither is confirmable the note says both are
  possible rather than picking. **Confidently naming the wrong cause is worse
  than naming none** — it costs the model a turn to argue with.
- `tools_sketch._editing_rules_note()` appends the rules for changing an existing
  sketch to the **first `get_sketch` of each conversation** (keyed on
  `active_session_id()`, so "New" re-arms it). A `get_sketch` call *is* "I am
  about to edit a sketch", so it's the one moment those rules are relevant; they
  used to cost 700 always-loaded words in the prompt and then, briefly, sat in the
  file with the 0% read rate.
- `diagnostics._partdesign_reference_note(before)` delivers
  `_PARTDESIGN_ESSENTIALS` the first time a conversation leaves a
  `PartDesign::Body` in the document. The trigger is the **Body**, not a feature,
  because a Body is usually created in its own call — which puts the note ahead
  of the first feature rather than after it. Its contents were picked from the
  errors actually logged (`KeyError: 'XY_Plane'`, a feature object passed where
  `newObject` wants a type string, `Tip` read as `None`, `Transformed`/
  `SubElementNames` guessed on a pattern, `Body: object is not allowed`), not
  from what the reference happens to cover; `partdesign-scripting.md` is cited at
  the end for Revolution/Groove, Loft/Pipe, Hole, datums and MultiTransform.

**Both the trigger and the decision not to add one were measured**, over the same
logged sessions (33 with `run_python`, 325 calls, 9% of calls hitting a traceback):

| Reference | Sessions | First call at | More followed | Error rate |
|---|---|---|---|---|
| `partdesign-scripting.md` | 14/33 | run_python #2 (median) | 11/14 sessions | **17%** — ~2× baseline |
| `part-draft-recipes.md` | 10/33 | #4 (median) | 3/10 sessions | **3%** — below baseline |

So PartDesign work is where wrong-property guesses actually cost something, and a
pointer there front-runs more work in 11 of 14 sessions. Part-primitive/Draft work
errored once in 37 calls and in 7 of 10 sessions nothing followed the first call —
a pointer there would be noise, so `part-draft-recipes.md` deliberately has no
trigger and is reachable only from the prompt's reference list. Don't add one "for
symmetry" without re-running the numbers.

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
  window pops up (and historically hung) under the windowed FreeCAD process.
- `run_python` runs inside an `openTransaction`/`commit` (undoable); on error it
  aborts AND removes newly-added objects (undo may be off in some contexts).
- **A loop of Part shape operations doesn't run slowly — it freezes FreeCAD, and
  nothing can preempt it.** `run_python` executes on the GUI thread, so a call
  that takes minutes is minutes of a dead application. Measured: a session that
  ran `while x <= 151.5: ymax(x); x += 0.1` — ~1,700 `Shape.slice()` calls on a
  725-face solid at ~96 ms each — held the GUI thread for **2m46s**. Three
  things follow, and all three are now handled:
  - `tools_python._heavy_loop_note` (a `run_python` precheck) refuses the call
    statically, before it reaches the GUI thread, when a heavy op runs inside a
    loop of unknown or >50 trip count. It resolves calls through locally-defined
    helpers, because the expensive call is usually one level down — in the
    measured case the `slice()` was inside `def ymax`, not in the loop body, so
    a lexical check would have missed the exact thing worth catching. The
    threshold is sized off the logged sessions (the loops that read fine sampled
    7 and 11 Z-heights; the ones that froze ran 183/245/1,206), and `# slow-ok`
    anywhere in the code skips it — a loop that must run costs one round-trip,
    which is the point.
  - **`isInside` belongs on that heavy list even though it looks like a cheap
    point test**, and it was missed once for exactly that reason (a session
    froze the GUI for >5 min sampling a wall profile with it). FreeCAD's
    `TopoShapePy::isInside` builds a fresh `BRepClass3d_SolidClassifier` on
    *every* call, so it walks the whole solid per point: sampled under the
    freeze, **1462 of 2206 stacks were in the classifier's constructor** before
    any classifying happened. Measured cost is linear in face count —
    0.06 ms @ 10 faces, 0.54 @ 130, **2.80 @ 568** — so it degrades silently as
    the model gets real. The one-shot form is a boolean against a line:
    `shape.common(Part.makeLine(a, b)).Edges` returns the material intervals
    along the whole line in one call (**210×** faster than the 1,071-point scan
    it replaced, and exact rather than quantised to the step). For a height or
    width profile, `shape.slices(dir, [d1, …])` is one call for all planes and
    gives every coordinate, not the few that got sampled (**9×**, and all 65
    probed heights matched the point scan exactly).
  - The bridge's `GuiBusyTimeout` (`_GUI_CALL_TIMEOUT`, 600 s) reports the call
    as **still running**, not failed. It is not a cancellation — the call keeps
    going and still commits — so the message's job is to stop the obvious
    response ("resend it"), which would queue a second copy behind the first and
    apply the same change twice.
  - `mcp_server._CALL_TIMEOUT` (900 s) must stay **above** that 600 s. It was
    120 s, which is how the incident got worse than slow: the socket gave up 46 s
    before the work finished, the reply landed on a closed socket and was
    dropped by `_handle`'s `except OSError`, and Claude was told a call had
    failed that in fact succeeded. Whoever times out first has to be the side
    that can describe what's happening — that's the bridge. That dropped-reply
    path now warns into the report view via `gui_bridge._warn`.
- Box `Length`/etc. are `Quantity` objects (`str` → "20.0 mm"); use the numeric
  input or `.Value`.
- **Assigning `sketch.Geometry` to move constrained geometry silently mangles it.**
  It doesn't raise — the solver just drags the geometry back to satisfy the old
  constraints (overwrite a line to 6mm while a `DistanceX=10` holds it and FreeCAD
  keeps it 10mm, flinging the start point to -3.08). `moveGeometry` only shifts
  *underconstrained* geometry, by its own contract. The only correct way to move
  constrained geometry is `setDatum(constraintIndex, value)`. This burned a whole
  real session (undo → retry → mangle → undo) before `get_sketch` existed.
- `inspect_api` on a document-object *instance* used to hide its methods: it took
  the `PropertiesList` branch and the `elif` meant `dir()` was never walked, so a
  sketch's 201 methods and its non-property attributes (`DoF`, `ConflictingConstraints`)
  were undiscoverable — the model could only guess names, and guessed wrong
  (`movePoint`, `setGeometry`, `getDoF()` — none exist). Both branches now run.
  `Sketcher.SketchObject` also isn't an importable class; `_describe_by_type_id`
  resolves that (and any `Type::String`) to a live instance in the document.
- **Assigning a dress-up's `Base` a DIFFERENT feature silently re-parents it,
  cutting every feature in between out of the Body.** `DressUp::onChanged`
  (`FeatureDressUp.cpp:276`) sets `BaseFeature` to whatever `Base` was just
  pointed at — so `fillet.Base = (other_feature, [...])`, which reads as an
  edge-list edit, is a chain edit. One such line (`SoleFillet.Base` moved from
  `HandlingFillet` to `FootFlareL`, while re-deriving stale `EdgeNN` names) took
  two features out of a real document's tip chain; they kept recomputing cleanly
  on their own branch, so nothing errored, and the damage went unnoticed for 27
  more `run_python` calls. **A dress-up's `Base[0]` must always be its own
  immediate predecessor.** To fix stale edge names, keep `Base` on the same
  feature: recompute *that* feature first, then read the names off its `Shape` —
  reading them off a not-yet-recomputed shape is what made the correct fix look
  like it had failed and prompted the object swap. Now detected by
  `summarize_chain_changes`, and stated inline in `_PARTDESIGN_ESSENTIALS`.
- **`doc.removeObject()` on the feature a Body's `Tip` points at leaves `Tip`
  dangling**, and the Body then builds no shape at all while every feature in it
  still recomputes fine. Set `body.Tip` to whichever feature should now be last.
- **Scripted `body.newObject(...)` onto a Body that has a datum feature (e.g. a
  `PartDesign::Plane`) sitting in `Group` between the current Tip and its
  predecessor can wire a circular `BaseFeature`.** (`Body::setBaseProperty` picks
  the new feature's neighbours with `getPrevSolidFeature`/`getNextSolidFeature`,
  which walk **`Group` order**, not the chain — so a Body whose `Group` order and
  chain disagree is the precondition. That disagreement is common and normally
  harmless: 34% of 58 bodies measured across real documents, which is why it is
  *not* worth flagging on its own.) Adding a `PartDesign::Fillet`
  this way left the *previous* tip (`MirroredTopCut`) with `BaseFeature` rewired
  to point at the *new* `Fillet`, while `Fillet.BaseFeature` correctly pointed
  back at `MirroredTopCut` — a two-node cycle. Symptom: the older feature reports
  `Invalid` after a successful-looking `run_python` call, and a forced recompute
  on it throws `RuntimeError: The graph must be a DAG`. Fix by reassigning the
  older feature's `BaseFeature` directly back to its true predecessor
  (`doc.MirroredTopCut.BaseFeature = doc.Pocket001`) — do **not** try to fix it via
  `Body.insertObject`/`Group` reordering, which reproduces the same cycle (and can
  duplicate the `Group` entry). See
  `freecad/freecadclaude/references/partdesign-body-tip-cycle-gotcha.md` for the
  full incident writeup.
