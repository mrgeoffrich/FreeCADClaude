# FreeCADClaude (FreeCAD addon)

A FreeCAD workbench that docks a **Claude chat panel** on the right-hand side
of the main window and lets Claude act on the active document through a curated
set of FreeCAD tools.

*Unofficial community project — not affiliated with, endorsed by, or sponsored
by Anthropic. "Claude" is a trademark of Anthropic, PBC. It drives your own
[Claude Code](https://www.anthropic.com/claude-code) CLI and Claude
subscription; intended for personal use.*

> **Status: active.** The panel drives the `claude` CLI as a hidden subprocess,
> authenticating with your own Claude account (no API key, no cost). Replies
> stream into the UI from a background thread, and Claude can act on the live
> document through a curated set of tools: it reads objects and selections,
> *sees* your geometry via screenshots and section (cutaway) views, inspects the
> API, exports files, and — behind a **per-call confirmation dialog** — runs
> Python against the document inside an undoable transaction (`run_python`).
>
> **What it can touch:** the only path that changes your document is the
> confirm-gated `run_python` — you approve each call, and on error the
> transaction is rolled back. `Write` can create or overwrite files on disk (but
> never the live document); every other tool is read-only. `Bash` and `Edit`
> are disabled.

## Quick install

**Prerequisites:** [FreeCAD 1.1+](https://www.freecad.org/) and
[Node.js](https://nodejs.org/) (for the Claude Code CLI). You log in once with
your own Claude account — no API key and no extra cost (it uses your existing
Pro/Max plan).

**Windows** (PowerShell):
```powershell
npm install -g @anthropic-ai/claude-code   # install the Claude Code CLI
claude                                      # log in once (opens a browser), then exit
git clone https://github.com/mrgeoffrich/FreeCADClaude `
  "$env:APPDATA\FreeCAD\v1-1\Mod\FreeCADClaude"
```

**macOS** (Terminal):
```bash
npm install -g @anthropic-ai/claude-code   # install the Claude Code CLI
claude                                      # log in once (opens a browser), then exit
git clone https://github.com/mrgeoffrich/FreeCADClaude \
  "$HOME/Library/Application Support/FreeCAD/v1-1/Mod/FreeCADClaude"
```

Then **restart FreeCAD** and choose **Claude Chat** from the workbench selector.
Prefer a GUI with automatic updates? Install through FreeCAD's **Addon Manager**
using this repo's URL instead — see [Installation](#installation) below, which
also covers Linux and verifying the `Mod` path for your build.

## What's here

```
FreeCADClaude/
├── Init.py                 # App-side init (no GUI imports)
├── InitGui.py              # Registers the workbench + command; eval hook
├── package.xml             # Addon Manager metadata
├── mcp_server.py           # Stdlib MCP stdio server the CLI spawns; relays to the bridge
└── freecad/freecadclaude/
    ├── chat_panel.py       # The chat dock: streamed transcript, buttons, worker wiring
    ├── plan_panel.py       # Second dock: Plan (subagent output) + live task checklist
    ├── transcript_widgets.py  # Chat transcript rendering widgets
    ├── agent_worker.py     # Drives the claude CLI per turn; parses stream-json → Qt signals
    ├── agent_config.py     # Model, system prompt, CLI flags (tools/mcp/cwd/skills)
    ├── system_prompt.md    # The system prompt text
    ├── gui_bridge.py       # In-FreeCAD socket server; runs tools on the GUI thread
    ├── freecad_tools.py    # Tool registry + implementations + capture/export helpers
    ├── _deps.py            # Locates the claude CLI
    ├── commands.py         # FreeCADClaude_TogglePanel command
    ├── eval_runner.py      # Unattended end-to-end eval (env-var triggered)
    └── resources/icon.svg
```

## Requirements

- **FreeCAD 1.1+** (uses the bundled Python 3.11).
- The **Claude Code CLI** (`npm install -g @anthropic-ai/claude-code`), logged
  in once with your Claude account (`claude` in a terminal). No API key needed
  — this uses your existing Pro/Max subscription. **Personal use:** Anthropic's
  terms don't permit shipping claude.ai login in a distributed product.
- No Python packages.

## Installation

The easiest way is through FreeCAD's **Addon Manager** — no cloning, and you get
update checks like any indexed addon. It still needs the
[prerequisites](#1-prerequisites) below (the `claude` CLI), so set those up
first, then:

1. In FreeCAD, open **Tools → Addon manager** (accept the third-party notice on
   first use).
2. Open its configuration — the **⚙ gear** icon in the Addon Manager window (or
   **Edit → Preferences → Addon Manager**) — and under **Custom repositories**
   add a new entry:
   - **Repository URL:** `https://github.com/mrgeoffrich/FreeCADClaude`
   - **Branch:** `main`
3. Close preferences; back in the Addon Manager the addon now appears in the
   list. Select **FreeCADClaude** and click **Install**.
4. **Restart FreeCAD**, then pick **Claude Chat** from the workbench selector.

Prefer the command line, or want to hack on the code? The manual steps below
(clone or copy into the `Mod` dir) still work.

### 1. Prerequisites

- **FreeCAD 1.1+**.
- **Node.js** and the **Claude Code CLI**, logged in once with your Claude
  account:
  ```bash
  npm install -g @anthropic-ai/claude-code
  claude            # run once, complete the login (uses your Pro/Max subscription)
  ```
  Make sure `claude` is on your `PATH` (`claude --version` should work). No
  Anthropic API key is required.

### 2. Find your FreeCAD user `Mod` directory

FreeCAD 1.x uses a **version-namespaced** user directory. The addon must live in
its `Mod` folder:

| OS      | User `Mod` directory                                         |
|---------|--------------------------------------------------------------|
| Windows | `%APPDATA%\FreeCAD\v1-1\Mod\`                                 |
| Linux   | `~/.local/share/FreeCAD/v1-1/Mod/` (or `~/.FreeCAD/...`)      |
| macOS   | `~/Library/Application Support/FreeCAD/v1-1/Mod/`             |

The exact path for your build is whatever this prints, with `Mod` appended:
```bash
freecadcmd -c "import FreeCAD; print(FreeCAD.getUserAppDataDir())"
```

### 3. Install the addon

The result should be `…/Mod/FreeCADClaude/` containing `Init.py`, `InitGui.py`,
`package.xml`, and the `freecad/` package. Any of these works:

- **git clone** straight into the Mod dir:
  ```bash
  git clone https://github.com/mrgeoffrich/FreeCADClaude "<Mod dir>/FreeCADClaude"
  ```
- **Copy** the folder into the Mod dir manually.
- **Windows dev** — from a clone, `pwsh -File deploy.ps1` copies it into the
  correct versioned Mod dir automatically (re-run after code changes).

There are **no Python dependencies** to install. On Windows you can run
`pwsh -File install_deps.ps1` to confirm the `claude` CLI is present and
logged in.

### 4. Run it

Restart FreeCAD, then pick **Claude Chat** from the workbench selector. The chat
and **Plan & Tasks** panels dock on the right (toggle the chat any time from the
toolbar/menu). Type a message to start a live session.

### 5. Optional — enable a skills project

To let the agent use FreeCAD skills (e.g. a design-advisor), point it at a
project whose `.claude/skills` holds them, via the preference
`User parameter:BaseApp/Preferences/Mod/FreeCADClaude` → string `SkillsProjectDir`.
When set, the agent runs with that as its working dir and enables the
`Skill`/`Read`/`Glob`/`Grep` tools. Leave it unset to keep things locked down.

## Evaluation

`eval/run.ps1` (Windows) and `eval/run.sh` (macOS/Linux) run an end-to-end
test: launch FreeCAD, open the chat panel, submit a prompt through the real
agent (auto-approving `run_python`), wait for the turn, snapshot the resulting
document to JSON, and quit.

```powershell
pwsh -File eval/run.ps1                                  # default box prompt
pwsh -File eval/run.ps1 -Prompt "Create a cylinder r5 h30 named C" `
     -Expect '"type":\s*"Part::Cylinder"'               # with a PASS/FAIL check
```

```bash
./eval/run.sh                                            # default box prompt
./eval/run.sh -p "Create a cylinder r5 h30 named C" \
              -e '"type":\s*"Part::Cylinder"'            # with a PASS/FAIL check
```

`-Expect`/`-e` is a regex matched against the result JSON; the script exits
0 (PASS), 1 (FAIL), or 2 (eval didn't complete). The trigger is the
`FREECADCLAUDE_EVAL` environment variable, handled in `InitGui.py` →
`freecad/freecadclaude/eval_runner.py`.

### Judging *behaviour*, not just the snapshot

The result JSON only records object names, types and dimensions — enough for a
regex like "did a Cylinder get created", but not for *how* the agent got there.
For a behaviour or prompt change (did it cut in the right direction, review the
sketch before pocketing, recover from a warning, and in how many steps), the
signal is in the run's own session folder — `run.sh` prints its path, and it's
the newest directory under `~/FreeCADClaude/`:

- **`stream.jsonl`** — the tool calls in order, plus the per-operation
  volume/solid-count delta and `⚠` notes folded into each tool result. Read it
  for the *tool-call ordering* (e.g. did it review the sketch before pocketing?),
  whether a `⚠` note fired, and whether it then recovered.
- **`scripts/`** — every approved `run_python`, in order. The count and content
  show whether it went straight to the answer or flailed through dead ends.

Some advice from using it:

- **It's a live agent run**, not a headless unit test — each eval drives the
  real `claude` CLI on your subscription and briefly opens a FreeCAD window. Keep
  prompts pointed and use `-Expect`/`-e` so a run is self-checking.
- **One green run isn't proof.** The agent is non-deterministic, so for a change
  meant to fix an "always fails this way" behaviour, run it a few times before
  trusting it.
- **Reproduce the exact failure *and* a harder variant** that stresses the same
  weakness — e.g. a hole "through" the part *and* one "in the bottom", which puts
  the cut on the opposite face. A fix that only passes the easy phrasing isn't
  really fixed.
- **Diff against the old behaviour.** The failing run's `stream.jsonl`/`scripts/`
  are the baseline; compare step count and tool-call order before vs. after (copy
  the folder out first — session dirs are auto-pruned).
