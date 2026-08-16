#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Minimal MCP stdio server bridging the Claude Code CLI to a FreeCAD process.

The CLI spawns this script (via --mcp-config) and speaks MCP (JSON-RPC 2.0 over
newline-delimited stdio). This server owns no FreeCAD state itself -- it relays
``tools/list`` and ``tools/call`` over a localhost TCP socket to a bridge
running inside the live FreeCAD process, where the tools actually execute on
the GUI thread.

The bridge's host/port/token come from one of two places, tried in order on
EVERY call (see _endpoint): the FREECAD_BRIDGE_PORT/FREECAD_BRIDGE_TOKEN env
vars the chat panel sets when it spawns this script for its own turns, or --
when those are absent, e.g. an external MCP client started with
`claude mcp add` -- the discovery file gui_bridge.start() writes to
~/FreeCADClaude/bridge.json (see freecad_tools.session.bridge_file; the path
is spelled again here, deliberately, because this stdlib-only process has no
FreeCAD to ask for the ArtifactsDir preference).

Standard library only -- it runs in whatever Python the CLI launches.
"""

import json
import os
import socket
import sys

PROTOCOL_VERSION = "2024-11-05"

# The bridge only ever binds loopback (see gui_bridge.start), so this is a
# constant, not read from the discovery file -- honouring a "host" field from
# disk could only ever send the token somewhere other than this machine.
_HOST = "127.0.0.1"

# The bridge runs the tool on FreeCAD's GUI thread and owns the deadline for it
# (gui_bridge._GUI_CALL_TIMEOUT, 600s). Our socket timeout for the send/recv
# exchange must sit ABOVE that one, or we abandon a call the bridge is still
# working on: the reply it eventually sends lands on a closed socket and is
# dropped, so a slow-but-successful call is reported to Claude as a failure --
# and a *mutating* call reported that way still commits, inviting Claude to
# apply the same change twice. Whoever times out first must be the side that
# can actually describe what's going on -- that's the bridge, so this stays
# strictly above it.
_CALL_TIMEOUT = 900
# Listing only reads a static schema table on the bridge thread -- it never
# touches the GUI thread, so a slow reply means FreeCAD is wedged, not busy.
_LIST_TIMEOUT = 30
# Separate, short bound on the TCP connect step alone. A stale discovery file
# (FreeCAD restarted, the port got re-bound to something else) refuses or
# hangs at connect, not at the reply -- and without a short connect deadline
# that failure would otherwise wait out the full 900s call timeout, since
# socket.create_connection(timeout=...) applies one deadline to both connect
# and recv.
_CONNECT_TIMEOUT = 5

#: Name of the fallback discovery file, one directory level under the user's
#: home -- see freecad_tools.session.bridge_file(), the other spelling of this
#: path. Kept as two literals (folder, filename) rather than importing
#: session.py: this script must stay runnable by any bare python3 the CLI
#: happens to launch, with no FreeCAD package on its path.
_BRIDGE_DIR_NAME = "FreeCADClaude"
_BRIDGE_FILE_NAME = "bridge.json"


class BridgeUnavailable(Exception):
    """No usable bridge endpoint was found -- env vars absent/incomplete and
    the discovery file missing, unreadable, or structurally wrong."""


def _bridge_file_path():
    # Evaluated at call time, not import time, so a test can point HOME (or
    # USERPROFILE on Windows) at a temp dir before calling _endpoint().
    home = os.path.expanduser("~")
    return os.path.join(home, _BRIDGE_DIR_NAME, _BRIDGE_FILE_NAME)


def _endpoint():
    """(port, token, source) for the bridge to talk to. Raises BridgeUnavailable.

    Two sources, tried in order, re-resolved on every call (no caching): the
    env vars the chat panel sets for its own turns, must stay byte-for-byte
    equivalent to the pre-discovery-file behaviour since that path is the one
    the chat panel's own conversations depend on; then the discovery file, so
    an external client left open across a FreeCAD restart picks up the new
    port/token on its very next call.
    """
    env_port = os.environ.get("FREECAD_BRIDGE_PORT", "0")
    env_token = os.environ.get("FREECAD_BRIDGE_TOKEN", "")
    try:
        port = int(env_port)
    except ValueError:
        port = 0
    if port and env_token:
        return port, env_token, "env"

    path = _bridge_file_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise BridgeUnavailable(
            "No FreeCAD bridge is available: FREECAD_BRIDGE_PORT/TOKEN are not "
            f"set and there is no discovery file at {path}. FreeCAD is not "
            "running, or the bridge has not started. Either switch to the "
            "Claude Chat workbench with the BridgeAutoStart preference set, "
            "or send one message in the chat panel to start the bridge."
        )
    except (OSError, ValueError) as exc:
        raise BridgeUnavailable(f"Could not read the bridge discovery file at {path}: {exc}")

    if not isinstance(data, dict):
        raise BridgeUnavailable(f"Bridge discovery file at {path} is not a JSON object.")
    port = data.get("port")
    token = data.get("token")
    if not isinstance(port, int) or port <= 0:
        raise BridgeUnavailable(f"Bridge discovery file at {path} has no valid 'port'.")
    if not isinstance(token, str) or not token:
        raise BridgeUnavailable(f"Bridge discovery file at {path} has no valid 'token'.")
    pid = data.get("pid")
    return port, token, f"discovery file at {path} (pid {pid})"


def _bridge(payload, timeout=_CALL_TIMEOUT):
    """Send one JSON request to the FreeCAD bridge, return the JSON reply.

    A connect failure most often means the discovery file is stale -- FreeCAD
    quit or crashed since it was written, and the port it names now refuses or
    belongs to something else. The refusal is the staleness detector (see
    _endpoint's docstring on why this isn't a pid liveness check), so the
    resulting message names the source it came from, path and pid included
    when that source was the file.
    """
    port, token, source = _endpoint()
    payload = dict(payload, token=token)
    try:
        with socket.create_connection((_HOST, port), timeout=_CONNECT_TIMEOUT) as sock:
            sock.settimeout(timeout)
            sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
    except OSError as exc:
        raise BridgeUnavailable(
            f"Could not reach the FreeCAD bridge via {source}: {exc}. If "
            "FreeCAD was restarted or crashed, this is a stale discovery "
            "file -- a running bridge rewrites it on its next start."
        )
    return json.loads(buf.decode("utf-8"))


def _list_tools():
    """(tools, error_message) -- error_message is None on success.

    Never returns ([], None) on failure: an empty list tells the client "this
    server has no tools", which the client then caches for the session with no
    way for the user to act on it. A failure has to be reported as a protocol
    error instead (see main()'s tools/list branch).
    """
    try:
        port, token, source = _endpoint()
    except BridgeUnavailable as exc:
        return [], str(exc)
    try:
        resp = _bridge({"op": "list"}, _LIST_TIMEOUT)
    except BridgeUnavailable as exc:
        return [], str(exc)
    except Exception as exc:  # noqa: BLE001
        return [], f"Could not reach the FreeCAD bridge ({source}): {exc!r}"
    return resp.get("tools", []), None


def _call_tool(name, arguments):
    try:
        resp = _bridge({"op": "call", "tool": name, "arguments": arguments})
    except BridgeUnavailable as exc:
        return {"content": [{"type": "text", "text": "Error: " + str(exc)}], "isError": True}
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
            tools, error = _list_tools()
            if error is not None:
                # A tools/call failure stays as isError content, because a
                # failed call is something the model should read and relay.
                # This is different: a client with no tools has nothing to
                # relay through, so it has to be a protocol-level error, loud
                # enough that the user (not the model) sees it.
                sys.stderr.write("freecad mcp_server: tools/list failed: "
                                  + error + "\n")
                _send({"jsonrpc": "2.0", "id": mid,
                       "error": {"code": -32603, "message": error}})
            else:
                _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}})
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
