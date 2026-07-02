# SPDX-License-Identifier: LGPL-2.1-or-later
"""Configuration for the Claude chat session.

Auth is handled by the ``claude`` CLI (the user's own account). This module
also assembles the ``--mcp-config`` that points the CLI at our MCP server
(mcp_server.py), which relays tool calls back to the FreeCAD bridge.
"""

import json
import os

import FreeCAD

DEFAULT_MODEL = "claude-sonnet-5"

#: Reasoning effort passed to the CLI as --effort. Pinning it stops the addon
#: inheriting your global Claude Code effortLevel (which can be xhigh/max and
#: makes turns think for a long time). Override via the "Effort" preference.
DEFAULT_EFFORT = "medium"
_VALID_EFFORT = ("low", "medium", "high", "xhigh", "max")

#: The system prompt lives in system_prompt.md alongside this module so it's
#: easy to read/edit as plain text; loaded once at import time.
_SYSTEM_PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_prompt.md")
with open(_SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as _f:
    SYSTEM_PROMPT = _f.read().strip()

_PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/FreeCADClaude"

#: Addon root = three levels up from this file (.../FreeCADClaude/freecad/freecadclaude).
_ADDON_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

#: A project directory whose .claude/skills hold FreeCAD skills (e.g. the
#: bundled freecad-design-advisor planning skill under this addon's .claude/skills).
#: When set, the CLI runs with this as its cwd so the project skills are
#: discovered, and the Skill + read tools are enabled. Defaults to the addon root
#: so the bundled skills work out of the box; override via the "SkillsProjectDir"
#: preference (e.g. point at an external project), or clear it to disable.
DEFAULT_SKILLS_DIR = _ADDON_ROOT

#: Task/todo tracking tools (always enabled) so the agent can plan and track
#: multi-step modeling work. "Task" is the subagent launcher; the rest are the
#: todo-list family. These have no system side effects.
_TASK_TOOLS = [
    "Task",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskOutput",
    "TaskStop",
    "TaskUpdate",
]

#: Read is always on so the agent can open the PNGs produced by view_sketch_svg
#: and capture_view (and read skill reference files). Read-only.
_READ_TOOLS = ["Read"]

#: Write is always on (like Read) so Claude can author plain-text files
#: directly -- currently used by freecad-lofi-sketch's concept SVGs. Runs
#: inside the claude CLI process itself (not the MCP bridge/GUI thread) and
#: never touches the live FreeCAD document -- Bash and Edit stay OFF; the
#: confirm-gated run_python remains the only path that mutates the document.
_WRITE_TOOLS = ["Write"]

#: Extra built-in tools enabled when a skills project is configured: Skill loads
#: skills; Glob/Grep help a skill find its reference files. Bash/Edit stay
#: OFF -- the only mutation path to the live document is the gated run_python
#: tool.
_SKILL_TOOLS = ["Skill", "Glob", "Grep"]


def get_model():
    params = FreeCAD.ParamGet(_PARAM_PATH)
    return params.GetString("Model", DEFAULT_MODEL) or DEFAULT_MODEL


def get_effort():
    """Reasoning effort (low/medium/high/xhigh/max). Pinned so it doesn't
    inherit the user's global Claude Code effortLevel."""
    params = FreeCAD.ParamGet(_PARAM_PATH)
    effort = (
        (params.GetString("Effort", DEFAULT_EFFORT) or DEFAULT_EFFORT).strip().lower()
    )
    return effort if effort in _VALID_EFFORT else DEFAULT_EFFORT


def get_skills_dir():
    """Return the configured skills project dir if it has .claude/skills, else None."""
    params = FreeCAD.ParamGet(_PARAM_PATH)
    path = (
        params.GetString("SkillsProjectDir", DEFAULT_SKILLS_DIR) or DEFAULT_SKILLS_DIR
    )
    if path and os.path.isdir(os.path.join(path, ".claude", "skills")):
        return path
    return None


def _python_exe():
    """Path to FreeCAD's bundled Python (used to run the stdlib-only MCP server)."""
    home = FreeCAD.getHomePath()
    for name in ("python.exe", "python3", "python"):
        cand = os.path.join(home, "bin", name)
        if os.path.isfile(cand):
            return cand
    return "python"  # last resort: rely on PATH


def build_config(cli_path, bridge_port, bridge_token):
    """Bundle everything the worker needs, including the MCP wiring."""
    from . import freecad_tools

    mcp_config = json.dumps(
        {
            "mcpServers": {
                "freecad": {
                    "command": _python_exe(),
                    "args": [os.path.join(_ADDON_ROOT, "mcp_server.py")],
                    "env": {
                        "FREECAD_BRIDGE_PORT": str(bridge_port),
                        "FREECAD_BRIDGE_TOKEN": bridge_token,
                    },
                }
            }
        }
    )
    allowed_tools = ["mcp__freecad__" + name for name in freecad_tools.TOOLS]

    skills_dir = get_skills_dir()
    builtin_tools = (
        list(_TASK_TOOLS) + list(_READ_TOOLS) + list(_WRITE_TOOLS)
    )  # always available
    if skills_dir:
        builtin_tools += _SKILL_TOOLS
    allowed_tools += builtin_tools
    # The subagent launcher is enabled via "Task" but the CLI reports its use as
    # "Agent"; allow that name too so subagents (e.g. the Plan agent) run without
    # a permission prompt in -p mode.
    allowed_tools.append("Agent")

    return {
        "cli_path": cli_path,
        "model": get_model(),
        "effort": get_effort(),
        "system": SYSTEM_PROMPT,
        "mcp_config": mcp_config,
        "allowed_tools": allowed_tools,
        "builtin_tools": builtin_tools,
        "cwd": skills_dir,  # None -> worker uses a neutral temp dir
        # The active chat conversation's log folder -- see freecad_tools.new_session_id.
        # Must be minted (freecad_tools.new_session_id()) before this call.
        "log_dir": freecad_tools.session_dir(),
    }
