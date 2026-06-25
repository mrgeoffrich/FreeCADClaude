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
    "panel on the right side of the FreeCAD window. "
    "You can act on the active FreeCAD document using the provided tools (for "
    "example, create_box). Use a tool when the user asks you to create or "
    "change geometry; otherwise just converse. Keep answers concise and "
    "well-suited to a narrow panel."
)

_PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/ClaudeChat"

#: Addon root = three levels up from this file (.../ClaudeChat/freecad/claudechat).
_ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_model():
    params = FreeCAD.ParamGet(_PARAM_PATH)
    return params.GetString("Model", DEFAULT_MODEL) or DEFAULT_MODEL


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

    return {
        "cli_path": cli_path,
        "model": get_model(),
        "system": SYSTEM_PROMPT,
        "mcp_config": mcp_config,
        "allowed_tools": allowed_tools,
    }
