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
| `freecad/freecadclaude/agent_config.py` | Model, system prompt, CLI flags (tools/mcp/cwd/skills). |
| `freecad/freecadclaude/gui_bridge.py` | In-FreeCAD socket server; runs tools on the GUI thread; run_python confirm dialog. |
| `freecad/freecadclaude/freecad_tools.py` | The tool registry (`TOOLS`) + implementations + SVG/raster/export helpers. |
| `freecad/freecadclaude/_deps.py` | Locates the `claude` CLI. |
| `freecad/freecadclaude/eval_runner.py` | Unattended end-to-end eval (triggered by env var). |
| `mcp_server.py` | Stdlib-only MCP stdio server the CLI spawns; relays to the bridge. |
| `deploy.ps1` / `install_deps.ps1` / `eval/run.ps1` | Dev tooling (not deployed). |

## Tools

Registry: `freecad_tools.TOOLS` = name → `{schema, run, confirm?}`. Current set:
`create_box`, `get_objects`, `get_selection`, `view_sketch_svg`, `capture_view`,
`export`, `run_python` (confirm-gated; the general Sketcher/PartDesign/Part path).

**Adding a tool** is purely additive: add a `{schema, run}` entry. `run(args)`
executes on the GUI thread and returns a string; the MCP allow-list and the
bridge wiring derive automatically. Set `"confirm": True` to require user
approval. Image tools write a PNG under the artifacts dir and return the path;
Claude opens it with the built-in `Read` tool (verified to render images).

Visual perception: prefer `view_sketch_svg` (exact SVG; for 3D pass
`view=front/top/...` → `TechDraw.projectToSVG` orthographic) over `capture_view`
(raster screenshot). Artifacts go to `<UserAppData>/FreeCADClaude/{captures,exports}`.

## CLI invocation (built in `agent_config`/`agent_worker`)

`claude -p <text> --output-format stream-json --verbose --include-partial-messages
--model claude-opus-4-8 --tools <builtins...> --strict-mcp-config
--mcp-config <json> --allowed-tools "<list>"` plus `--append-system-prompt`
(turn 1) or `--resume <id>` (later). cwd = the skills project dir (so its
`.claude/skills` load) else a temp dir.

- `--tools ""` disables ALL built-ins (incl. `Skill`). We enable a safe set:
  `Read` (always, for image viewing), the `Task*` family (todo + Plan subagent),
  and `Skill/Glob/Grep` when a skills project is configured. Bash/Write/Edit stay
  OFF — the only mutation path is the confirm-gated `run_python`.
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
- **End-to-end eval:** `pwsh -File eval/run.ps1 [-Prompt ... -Expect <regex>]` —
  launches FreeCAD, runs a prompt, snapshots the doc to JSON, exits 0/1/2.

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
