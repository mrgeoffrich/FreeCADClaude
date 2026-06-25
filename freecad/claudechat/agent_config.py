# SPDX-License-Identifier: LGPL-2.1-or-later
"""Configuration for the Claude chat session.

Auth is handled by the ``claude`` CLI (the user's own account). This module
also assembles the ``--mcp-config`` that points the CLI at our MCP server
(mcp_server.py), which relays tool calls back to the FreeCAD bridge.
"""

import json
import os

import FreeCAD

DEFAULT_MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = (
    "You are Claude, embedded as an assistant inside FreeCAD, the open-source "
    "parametric CAD program. You speak to the user through a narrow dockable "
    "panel on the right side of the FreeCAD window.\n\n"
    "Tools:\n"
    "- get_objects: inspect the active document (names, types, dimensions). Call "
    "it before modifying or referring to existing geometry.\n"
    "- get_selection: what the user currently has selected (objects + "
    "sub-elements like Edge3/Face2). Use it to act on what they clicked.\n"
    "- view_sketch_svg: SEE geometry as crisp SVG lines. Flat/2D (sketches, "
    "profiles) export directly; for 3D solids pass a 'view' "
    "(front/rear/top/bottom/left/right/iso) to get a clean orthographic "
    "projection. PREFER SVG for flat geometry AND for DIAGNOSING 3D parts from "
    "standard views -- profiles, alignment, and holes read clearly as exact "
    "lines.\n"
    "- capture_view: PNG screenshot of the 3D view -- use when you need a "
    "shaded/realistic look rather than line drawings. Pass a 'view' for angles.\n"
    "  Both view tools return a file path -- open it with the Read tool to see "
    "the image. When diagnosing a 3D issue, reach for orthographic SVG views "
    "first; fall back to capture_view for shading.\n"
    "- create_box: quick rectangular box.\n"
    "- run_python: execute FreeCAD Python in the live instance. This is your "
    "general capability for Sketcher (geometry + constraints), PartDesign "
    "(Body, Pad, Pocket, Revolution, Loft, Fillet, Chamfer...), Part booleans, "
    "Draft, arrays, and modifying or deleting existing objects. The user must "
    "approve each run_python call, so make each step purposeful.\n\n"
    "Working style: prefer small, verifiable steps; after a change, use "
    "get_objects (or print from run_python) to confirm the result before "
    "continuing. For PartDesign, create a PartDesign::Body and add features "
    "inside it; reference existing objects by their internal Name. If "
    "run_python returns a traceback, fix the code and retry. Keep chat answers "
    "concise and suited to a narrow panel.\n\n"
    "When the user asks how to model/design/build something, use your "
    "freecad-design-advisor skill (if available) to plan the workbench and "
    "feature sequence, then offer to build it with run_python. For any "
    "multi-step build, track the steps with the task tools (TaskCreate, then "
    "TaskUpdate to mark each in_progress/completed) so the user can follow "
    "progress in the Plan & Tasks panel."
)

_PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/ClaudeChat"

#: Addon root = three levels up from this file (.../ClaudeChat/freecad/claudechat).
_ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: A project directory whose .claude/skills hold FreeCAD skills (e.g. the
#: freecad-design-advisor planning skill). When set, the CLI runs with this as
#: its cwd so the project skills are discovered, and the Skill + read tools are
#: enabled. Override via the "SkillsProjectDir" preference; clear it to disable.
DEFAULT_SKILLS_DIR = r"C:\Repos\freecad-advisor"

#: Task/todo tracking tools (always enabled) so the agent can plan and track
#: multi-step modeling work. "Task" is the subagent launcher; the rest are the
#: todo-list family. These have no system side effects.
_TASK_TOOLS = ["Task", "TaskCreate", "TaskGet", "TaskList", "TaskOutput", "TaskStop", "TaskUpdate"]

#: Read is always on so the agent can open the PNGs produced by view_sketch_svg
#: and capture_view (and read skill reference files). Read-only.
_READ_TOOLS = ["Read"]

#: Extra built-in tools enabled when a skills project is configured: Skill loads
#: skills; Glob/Grep help a skill find its reference files. Bash/Write/Edit stay
#: OFF -- the only mutation path is the gated run_python tool.
_SKILL_TOOLS = ["Skill", "Glob", "Grep"]


def get_model():
    params = FreeCAD.ParamGet(_PARAM_PATH)
    return params.GetString("Model", DEFAULT_MODEL) or DEFAULT_MODEL


def get_skills_dir():
    """Return the configured skills project dir if it has .claude/skills, else None."""
    params = FreeCAD.ParamGet(_PARAM_PATH)
    path = params.GetString("SkillsProjectDir", DEFAULT_SKILLS_DIR) or DEFAULT_SKILLS_DIR
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

    mcp_config = json.dumps({
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
    })
    allowed_tools = ["mcp__freecad__" + name for name in freecad_tools.TOOLS]

    skills_dir = get_skills_dir()
    builtin_tools = list(_TASK_TOOLS) + list(_READ_TOOLS)  # always available
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
        "system": SYSTEM_PROMPT,
        "mcp_config": mcp_config,
        "allowed_tools": allowed_tools,
        "builtin_tools": builtin_tools,
        "cwd": skills_dir,  # None -> worker uses a neutral temp dir
    }
