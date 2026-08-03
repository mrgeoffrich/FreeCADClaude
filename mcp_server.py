#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Minimal MCP stdio server bridging the Claude Code CLI to a FreeCAD process.

The CLI spawns this script (via --mcp-config) and speaks MCP (JSON-RPC 2.0 over
newline-delimited stdio). This server owns no FreeCAD state itself -- it relays
``tools/list`` and ``tools/call`` over a localhost TCP socket to a bridge
running inside the live FreeCAD process (host/port/token from env), where the
tools actually execute on the GUI thread.

Standard library only -- it runs in whatever Python the CLI launches.
"""

import json
import os
import socket
import sys

PROTOCOL_VERSION = "2024-11-05"
_HOST = "127.0.0.1"
_PORT = int(os.environ.get("FREECAD_BRIDGE_PORT", "0"))
_TOKEN = os.environ.get("FREECAD_BRIDGE_TOKEN", "")

# The bridge runs the tool on FreeCAD's GUI thread and owns the deadline for it
# (gui_bridge._GUI_CALL_TIMEOUT). Our socket timeout must sit ABOVE that one, or
# we abandon a call the bridge is still working on: the reply it eventually
# sends lands on a closed socket and is dropped, so a slow-but-successful call
# is reported to Claude as a failure -- and a *mutating* call reported that way
# still commits, inviting Claude to apply the same change twice. Whoever times
# out first must be the side that can actually describe what's going on.
_CALL_TIMEOUT = 900
# Listing only reads a static schema table on the bridge thread -- it never
# touches the GUI thread, so a slow reply means FreeCAD is wedged, not busy.
_LIST_TIMEOUT = 30


def _bridge(payload, timeout=_CALL_TIMEOUT):
    """Send one JSON request to the FreeCAD bridge, return the JSON reply."""
    with socket.create_connection((_HOST, _PORT), timeout=timeout) as sock:
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.decode("utf-8"))


def _list_tools():
    try:
        return _bridge({"token": _TOKEN, "op": "list"}, _LIST_TIMEOUT).get("tools", [])
    except Exception:  # noqa: BLE001 - never let listing crash the server
        return []


def _call_tool(name, arguments):
    resp = _bridge({"token": _TOKEN, "op": "call", "tool": name, "arguments": arguments})
    if resp.get("ok"):
        content = [{"type": "text", "text": resp.get("text", "Done.")}]
        image = resp.get("image")
        if image:
            content.append({
                "type": "image",
                "data": image["data"],
                "mimeType": image.get("mimeType", "image/png"),
            })
        return {"content": content}
    return {
        "content": [{"type": "text", "text": "Error: " + str(resp.get("error", "unknown"))}],
        "isError": True,
    }


def _send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue

        method = req.get("method")
        mid = req.get("id")

        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "freecad", "version": "0.1.0"},
            }})
        elif method == "notifications/initialized":
            pass  # notification: no response
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": _list_tools()}})
        elif method == "tools/call":
            params = req.get("params", {})
            try:
                result = _call_tool(params.get("name"), params.get("arguments") or {})
            except Exception as exc:  # noqa: BLE001
                result = {"content": [{"type": "text", "text": f"Bridge error: {exc!r}"}],
                          "isError": True}
            _send({"jsonrpc": "2.0", "id": mid, "result": result})
        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif mid is not None:
            _send({"jsonrpc": "2.0", "id": mid,
                   "error": {"code": -32601, "message": f"Method not found: {method}"}})


if __name__ == "__main__":
    main()
