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
| `freecad/freecadclaude/agent_worker.py` | Drives the `claude` CLI per turn; parses stream-json → Qt signals. |
| `freecad/freecadclaude/agent_config.py` | Model, system prompt (loaded from `system_prompt.md`), CLI flags (tools/mcp/cwd/skills). |
| `freecad/freecadclaude/system_prompt.md` | The system prompt text itself, edited as plain Markdown. |
| `freecad/freecadclaude/gui_bridge.py` | In-FreeCAD socket server; runs tools on the GUI thread; run_python confirm dialog. |
| `freecad/freecadclaude/freecad_tools.py` | The tool registry (`TOOLS`) + implementations + SVG/raster/export helpers. |
| `freecad/freecadclaude/_deps.py` | Locates the `claude` CLI. |
| `freecad/freecadclaude/eval_runner.py` | Unattended end-to-end eval (triggered by env var). |
| `mcp_server.py` | Stdlib-only MCP stdio server the CLI spawns; relays to the bridge. |
| `deploy.ps1` / `install_deps.ps1` / `eval/run.py` | Dev tooling (not deployed). |

## Tools

Registry: `freecad_tools.TOOLS` = name → `{schema, run, confirm?}`. Current set:
`get_objects`, `get_selection`, `get_sketch`, `view_sketch_svg`, `capture_view`,
`capture_user_view`, `crop_view`, `cutaway`, `export`, `inspect_api`,
`get_diagnostics`, `run_python` (confirm-gated; the sole document-mutating tool —
the general Sketcher/PartDesign/Part path).

**Adding a tool** is purely additive: add a `{schema, run}` entry. `run(args)`
executes on the GUI thread and returns a string; the MCP allow-list and the
bridge wiring derive automatically. Set `"confirm": True` to require user
approval. `capture_view` returns a `(text, png_path)` tuple instead of a plain
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
from `view_sketch_svg`, and skill reference files), `Write` (author plain-text
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
the active tab isn't a 3D view.
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
≤60 files each); `scripts` holds a `.py` copy of every approved `run_python` call
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
--model claude-opus-4-8 --tools <builtins...>
--strict-mcp-config --mcp-config <json> --allowed-tools "<list>"` plus
`--append-system-prompt` (turn 1) or `--resume <id>` (later). cwd = the skills
project dir (so its `.claude/skills` load) else a temp dir.

- `--tools ""` disables ALL built-ins (incl. `Skill`). We enable a safe set:
  `Read` and `Write` (always — skill reference files and plain-text file
  authoring), `Glob`/`Grep` (always — file search), the `Task*` family (todo +
  Plan subagent), and `Skill` when a skills project is configured. `Bash`/`Edit`
  stay OFF — the only path that mutates the *live FreeCAD document* is the
  confirm-gated `run_python`; `Write` can create/overwrite arbitrary files on
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
  through the real agent (auto-approving `run_python`), snapshots the doc to
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
  CLI streamed, turn by turn), `scripts/` (every approved `run_python` call,
  success or failure), and `captures/`/`exports/` (images/exported files). See
  the "Tools" section above for how `<session-id>` is chosen. The "Files"
  button in the chat panel opens `~/FreeCADClaude` itself (all sessions).

## Conventions

- **PySide:** always `from PySide import ...` (FreeCAD's bundled binding), never a
  pip `PySide6`.
- **No asyncio.** The worker is a plain `QThread` + `queue.Queue`; the CLI's
  streaming call is synchronous.
- **Lazy GUI imports:** `InitGui.py` and tool `run` functions import
  `FreeCAD`/`FreeCADGui` inside functions where it matters.
- Keep the App/GUI split clean; tool execution always on the GUI thread.
- Commits: branch off `main` unless told otherwise; end messages with the
  `Co-Authored-By` trailer used in this repo's history.

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
