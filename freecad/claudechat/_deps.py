# SPDX-License-Identifier: LGPL-2.1-or-later
"""Locates the Claude Code CLI.

ClaudeChat drives the ``claude`` command-line tool directly (as a subprocess),
which authenticates with the user's own Claude account (Pro/Max subscription
or whatever the CLI is logged in with). No Python package and no API key are
needed -- the only dependency is the ``claude`` binary, installed and logged
in once by the user.
"""

import os
import shutil


def find_cli():
    """Return the path to the ``claude`` executable, or None if not found."""
    found = shutil.which("claude")
    if found:
        return found

    # Common install locations, in case FreeCAD's PATH is narrower than a shell's.
    appdata = os.environ.get("APPDATA", "")
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".local", "bin", "claude.exe"),
        os.path.join(home, ".local", "bin", "claude"),
        os.path.join(appdata, "npm", "claude.cmd"),
        os.path.join(appdata, "npm", "claude.exe"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def cli_available():
    """Return (ok: bool, detail: str). Never raises."""
    path = find_cli()
    if not path:
        return False, "claude CLI not found on PATH"
    return True, path


#: Human-readable hint shown in the panel when the CLI is missing (Markdown).
INSTALL_HINT = (
    "The Claude Code CLI isn't available. Install it with "
    "`npm install -g @anthropic-ai/claude-code`, then run `claude` once in a "
    "terminal to log in with your Claude account, and restart FreeCAD. "
    "No API key needed."
)
