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
> API, exports files, and runs Python against the document inside an undoable
> transaction (`run_python`).
>
> **What it can touch:** the only path that changes your document is
> `run_python`, and on error the transaction is rolled back. `Write` can create
> or overwrite files on disk (but never the live document); every other tool is
> read-only. `Bash` and `Edit` are disabled.
>
> **On a phone or tablet:** press **Connect Mobile** and the panel starts a small web
> server *on your local network* and shows a QR code. Scan it and you get a pen
> canvas: Claude can push a view of the model to it, you circle the boss and
> write "30mm", and it comes back with the dimensions as real numbers. Off
> until you press the button, and it stops itself when idle — see
> [Annotating on a phone or tablet](#annotating-on-a-phone-or-tablet).
>
> **Slicing:** Claude can hand your parts to **Bambu Studio** — each one stood up
> the way it prints — and open the resulting toolpath in your desktop browser.
> That launches the slicer as a background process and serves the viewer on
> `127.0.0.1`; see [Slicing and the toolpath viewer](#slicing-and-the-toolpath-viewer).
>
> ⚠️ **Tool calls are not gated by a confirmation prompt.** `run_python` executes
> as soon as Claude asks for it, and it is ordinary Python inside the FreeCAD
> process — so it can reach the filesystem, not just your document. This is a
> deliberate choice for a personal-use addon. Don't point it at work you can't
> afford to lose, and save (or enable the `SaveSteps` preference) before long
> builds.

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
    ├── device_server.py    # LAN HTTP server for the phone/tablet annotation page
    ├── device_ui/          # BUILT web app (committed; source is web/, see RELEASE.md)
    ├── gcode_server.py     # Loopback HTTP server for the G-code viewer + slicer settings
    ├── gcode_ui/           # BUILT viewer (committed; source is gcode_web/, see RELEASE.md)
    ├── slicer_runner.py    # Drives Bambu Studio as a subprocess; preset discovery
    ├── web_static.py       # Static-file resolution shared by both servers
    ├── qr.py               # Stdlib QR encoder for the pairing code
    ├── freecad_tools/      # The tools: registry (__init__.py), one tools_*.py per
    │                       #   concern, over shared infra (session/geometry/svg/
    │                       #   gui_state/visibility/render/diagnostics)
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

### 6. Optional — connect an external MCP client

The FreeCAD tools are also reachable from any other MCP client (e.g. a `claude`
CLI session running outside this addon), not just the built-in chat panel:

```bash
claude mcp add freecad -- python3 <Mod dir>/FreeCADClaude/mcp_server.py
```

Any `python3` works — the script is stdlib-only. Two things have to be true
first: set the integer/bool preference `BridgeAutoStart` under
`User parameter:BaseApp/Preferences/Mod/FreeCADClaude` to `true` (it's off by
default — publishing a credential to disk on every launch is opt-in), and start
FreeCAD *before* the external client, so the bridge has already written
`~/FreeCADClaude/bridge.json` for it to discover.

**Known gap:** an external client gets the raw FreeCAD tools, but not this
addon's system prompt — the execution contract and scripting references that
the chat panel's own turns carry are not (yet) exposed over MCP `initialize`.
Expect to guide it the way you'd guide any MCP client new to a tool set.

## Annotating on a phone or tablet

A picture tells Claude *where*; only a dimension with a number tells it *how
much*. The **Connect Mobile** button serves a small pen-and-canvas web
app to a phone or tablet on the same wifi, so you can point at the model with a
stylus instead of describing it.

1. Press **Connect Mobile**. A dialog shows a QR code and the URL under it.
2. Scan it (or type the URL). The page opens already authenticated.
3. Claude calls `send_to_device` and a rendered view appears on the device;
   or pick a photo/camera shot on the device instead — a napkin sketch, a part
   you're copying.
4. Draw on it. Two taps place a **dimension**: it shows the measured length in
   millimetres (derived exactly from the capture's orthographic camera) and you
   can type the target you actually want.
5. Press **Send**, tell Claude, and it calls `read_device_image` — it sees the
   marked-up picture inline, plus the dimensions as structured numbers with the
   camera angle and world extents the shot was taken at.

Images land in `~/FreeCADClaude/<session-id>/mobile/`, alongside the rest of the
conversation's artifacts.

### What it exposes, precisely

This is the one part of the addon that listens on your **local network** rather
than on localhost, so it's worth being exact about what that means.

- **Off by default.** Nothing listens until you press Connect Mobile — not on startup,
  and not because Claude asked. `send_to_device` with the server down returns a
  message telling Claude to ask *you* to press the button.
- **A fresh token per start**, carried in the URL the QR encodes. Every request
  must have it. Pressing **Stop server** (or quitting FreeCAD) both stops the
  listener and revokes the token — the next start mints a different one.
- **It stops itself when nothing is connected.** Default 30 minutes; the clock
  only runs while no device has the page open, so it won't cut you off
  mid-drawing. Change it with the integer preference `DeviceIdleMinutes` under
  `User parameter:BaseApp/Preferences/Mod/FreeCADClaude` (a negative value turns
  the auto-stop off).
- **Plain HTTP.** There is no TLS, so **the token crosses your LAN in clear**,
  and anyone who can watch that traffic — or who guesses a 22-character random
  secret — can use it. On a home network that is an accepted trade for a
  personal-use addon; on a shared or public network, don't turn it on.
- **The server cannot reach FreeCAD.** `device_server.py` imports no FreeCAD and
  no Qt, and no request handler calls into either. Captures are *pushed* to it by
  a tool running on FreeCAD's GUI thread. So the worst a token-holder can do is
  read the captures you have pushed and write image files into the session
  folder — not run `run_python`, not read your document, not touch the model.

### Hacking on it

The page's source is `web/` (Vite + TypeScript + Vitest); the built output in
`freecad/freecadclaude/device_ui/` is **committed to git**, because installs are
a plain file copy with no Node at the far end. If you change anything under
`web/`, rebuild and commit the output in the same commit:

```bash
cd web && npm ci && npx vitest run && npm run build
```

## Slicing and the toolpath viewer

Ask Claude to slice, and it exports the parts you name as one multi-object 3MF —
each **stood up the way it prints**, from the build direction recorded on it —
hands that to **Bambu Studio**, and reports the layer count, the print estimate,
where the time goes by feature type and the filament used. Then `view_gcode`
opens the toolpath in your own browser: an interactive 3D view with per-feature
colours and a layer slider.

Presets come from Bambu Studio's own current selection with the nozzle pinned to
0.4, so on a machine with Studio set up there is nothing to configure. To change
the printer, nozzle, process or filament, press **🖨 Slicer** in the chat panel —
that opens the same page, with the settings drawer, and needs no slice and no
message to Claude. Your choice is stored in `~/FreeCADClaude/slicer.json` and
outranks Studio's selection on the next slice.

Each job's artifacts land in `~/FreeCADClaude/<session-id>/slices/<job>/`: the
3MF that was handed over, the G-code and `result.json` that came back, the
slicer's log, and a `job.json` recording the exact command line used.

### What it exposes, precisely

- **It launches an external program.** `slice_model` starts Bambu Studio as a
  hidden background subprocess with a command line the addon built. It runs on a
  worker thread, so FreeCAD stays usable, and it is killed when you quit FreeCAD
  mid-slice.
- **Only Bambu Studio.** A binary the addon does not recognise — OrcaSlicer
  included — gets no command line at all, and the tool says so. These are GUI
  applications: a flag one doesn't accept would open a dialog on your desktop
  and report nothing back.
- **The viewer server binds `127.0.0.1`, not the LAN.** Nothing off this machine
  can reach it. It still carries a per-start token, because a loopback listener
  is reachable by every other process on the machine and this one serves files
  and rewrites your printer configuration. There is no button and no QR — it
  starts when a tool or the Slicer button needs it, and stops with FreeCAD.
- **It cannot reach FreeCAD.** `gcode_server.py` imports no FreeCAD and no Qt,
  and no request handler calls into either — the same invariant as the device
  server. G-code files are *pushed* to it by a tool running on the GUI thread.

### Hacking on the viewer

The viewer's source is `gcode_web/` (Vite + TypeScript + Vitest), vendored from
the [dimensioner](https://github.com/mrgeoffrich/dimensioner) project; the built
output in `freecad/freecadclaude/gcode_ui/` is **committed to git** for the same
reason `device_ui/` is. Rebuild and commit it in the same commit as any source
change, and record the change in `gcode_web/VENDORED.md`:

```bash
cd gcode_web && npm ci && npx vitest run && npm run build
```

## Evaluation

`eval/run.py` (cross-platform: Windows / macOS / Linux) runs an end-to-end
test: launch FreeCAD, open the chat panel, submit a prompt through the real
agent, wait for the turn, snapshot the resulting
document to JSON, and quit. It's stdlib-only — no venv or `pip install`; run it
with any Python 3.8+.

```bash
python3 eval/run.py                                      # default box prompt
python3 eval/run.py -p "Create a cylinder r5 h30 named C" \
                    -e '"type":\s*"Part::Cylinder"'      # with a PASS/FAIL check
```

`-e`/`--expect` is a regex matched against the result JSON; the script exits
0 (PASS), 1 (FAIL), or 2 (eval didn't complete). Other flags: `-t` timeout
seconds, `-c` a named in-tree case (`-l` lists them). The trigger is the
`FREECADCLAUDE_EVAL` environment variable, handled in `InitGui.py` →
`freecad/freecadclaude/eval_runner.py`.

### Judging *behaviour*, not just the snapshot

The result JSON only records object names, types and dimensions — enough for a
regex like "did a Cylinder get created", but not for *how* the agent got there.
For a behaviour or prompt change (did it cut in the right direction, review the
sketch before pocketing, recover from a warning, and in how many steps), the
signal is in the run's own session folder — `run.py` prints its path, and it's
the newest directory under `~/FreeCADClaude/`:

- **`stream.jsonl`** — the tool calls in order, plus the per-operation
  volume/solid-count delta and `⚠` notes folded into each tool result. Read it
  for the *tool-call ordering* (e.g. did it review the sketch before pocketing?),
  whether a `⚠` note fired, and whether it then recovered.
- **`scripts/`** — every `run_python`, in order. The count and content
  show whether it went straight to the answer or flailed through dead ends.

Some advice from using it:

- **It's a live agent run**, not a headless unit test — each eval drives the
  real `claude` CLI on your subscription and briefly opens a FreeCAD window. Keep
  prompts pointed and use `-e`/`--expect` so a run is self-checking.
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
