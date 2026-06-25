# ClaudeChat (FreeCAD addon)

A FreeCAD workbench that docks a **Claude chat panel** on the right-hand side
of the main window. The long-term goal is to let Claude act on the active
document through curated FreeCAD tools.

> **Status: Milestone 2 — live chat.** The panel drives the `claude` CLI as a
> hidden subprocess, authenticating with your own Claude account (no API key,
> no cost). Replies stream into the UI from a background thread. Chat only: the
> CLI is launched with all tools disabled, so it cannot touch your system.

## What's here

```
ClaudeChat/
├── Init.py                 # App-side init (no-op; no GUI imports)
├── InitGui.py              # Registers the workbench + toolbar/menu command
├── package.xml             # Addon Manager metadata
└── freecad/claudechat/
    ├── __init__.py
    ├── chat_panel.py       # The QDockWidget + chat widget (singleton)
    ├── agent_worker.py     # Drives the claude CLI subprocess; parses stream-json
    ├── agent_config.py     # Model + system prompt
    ├── _deps.py            # Locates the claude CLI
    ├── commands.py         # ClaudeChat_TogglePanel command
    └── resources/icon.svg
```

## Requirements

- **FreeCAD 1.1+** (uses the bundled Python 3.11).
- The **Claude Code CLI** (`npm install -g @anthropic-ai/claude-code`), logged
  in once with your Claude account (`claude` in a terminal). No API key needed
  — this uses your existing Pro/Max subscription. **Personal use:** Anthropic's
  terms don't permit shipping claude.ai login in a distributed product.
- No Python packages.

## Install (for testing)

Copy or symlink this `ClaudeChat` folder into your FreeCAD user `Mod` directory.
Note FreeCAD 1.x uses a **version-namespaced** dir (e.g. `…\FreeCAD\v1-1\Mod\`);
`deploy.ps1` resolves the correct path automatically.

```powershell
pwsh -File deploy.ps1        # copy into the versioned user Mod dir
pwsh -File install_deps.ps1  # verify the claude CLI is present + logged in
```

Then in FreeCAD: pick **Claude Chat** from the workbench selector. The panel
appears on the right; toggle it any time with the toolbar/menu button. Type a
message to start a live Claude session.

> **Dev loop:** `deploy.ps1` preserves `vendor/`, so re-running it after code
> changes is fast and doesn't reinstall dependencies.

## Roadmap

- **M1 (done):** workbench + right-side dock panel + local echo.
- **M2 (done):** drives the `claude` CLI (your account) on a worker thread;
  streamed replies, multi-turn via `--resume`, all tools disabled.
- **M3:** first FreeCAD tool — expose it to the CLI as a custom MCP/tool the
  agent can call, executed on the GUI thread inside an undoable transaction.
- **M4:** expanded toolset / guarded `run_python`, permission UI.
```
