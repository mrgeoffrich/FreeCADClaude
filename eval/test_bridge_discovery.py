#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Tests for the bridge discovery file: freecad_tools/session.py's
write_bridge_file/remove_bridge_file/bridge_file, and mcp_server.py's
_endpoint/_bridge/_list_tools fallback onto it.

Real tests on the stdlib halves of this feature (the file's permission bits,
and env-vs-file precedence) rather than hand smoke-testing, because both are
failure modes with no symptom: a wrong mode publishes a credential silently,
and a wrong precedence quietly drives the wrong FreeCAD. The GUI halves
(gui_bridge.start() publishing the file, autostart_if_enabled()) are one-liners
whose failure is immediately visible and are left to hand testing.

Neither module under test imports FreeCAD or Qt -- freecad_tools/session.py
has no relative imports and mcp_server.py is deliberately stdlib-only -- so
this needs no GUI and no running FreeCAD, any Python 3.8+ will do:

    python3 eval/test_bridge_discovery.py
    freecadcmd /abs/path/to/eval/test_bridge_discovery.py

(freecadcmd needs an ABSOLUTE path -- given a relative one it silently runs
nothing and still exits 0.)

Exit: 0 = all passed, 1 = a failure.
"""

import base64
import importlib.util
import io
import json
import os
import socket
import stat
import sys
import tempfile
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Module roots that must not appear because of our import. Snapshotted before
#: it rather than looked for afterwards, so running this inside an interpreter
#: that already has FreeCAD loaded still asks the right question.
_FORBIDDEN_ROOTS = ("FreeCAD", "FreeCADGui", "PySide", "PySide2", "PySide6",
                    "PyQt5", "PyQt6", "shiboken2", "shiboken6", "MeshPart")


def _forbidden(modules):
    return {name for name in modules if name.split(".")[0] in _FORBIDDEN_ROOTS}


_PRELOADED = _forbidden(sys.modules)


def _load(name, path):
    """Import a stdlib-only module by path, under its own module name.

    Neither session.py nor mcp_server.py has a relative import, so unlike
    device_server.py/gcode_server.py this needs no stub parent package.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_failures = []


def check(name, condition, detail=""):
    status = "ok  " if condition else "FAIL"
    print(f"  {status} {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(name)


def _run_mcp(mcp_server, requests):
    """Drive mcp_server.main() over a stdin/stdout pipe pair (StringIO stand-
    ins are enough: main() just iterates sys.stdin line by line until it's
    exhausted, so a StringIO holding every request already ends the loop).
    Returns the parsed JSON-RPC responses, in order, excluding notifications
    (which get none).
    """
    lines = "\n".join(json.dumps(r) for r in requests) + "\n"
    old_stdin, old_stdout = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(lines)
    sys.stdout = io.StringIO()
    try:
        mcp_server.main()
        out = sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = old_stdin, old_stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _start_fake_bridge(tools, call_response):
    """A minimal stand-in for gui_bridge's one-JSON-line-in, one-JSON-line-out
    protocol on an ephemeral loopback port. Returns (port, received_tokens, stop).
    """
    received_tokens = []
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    srv.settimeout(0.5)
    port = srv.getsockname()[1]
    stopping = threading.Event()

    def handle(conn):
        with conn:
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buf += chunk
            try:
                req = json.loads(buf.decode("utf-8"))
            except ValueError:
                return
            received_tokens.append(req.get("token"))
            op = req.get("op")
            if op == "list":
                reply = {"tools": tools}
            elif op == "call":
                reply = call_response
            else:
                reply = {"ok": False, "error": f"unknown op: {op}"}
            conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))

    def serve():
        while not stopping.is_set():
            try:
                conn, _addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    def stop():
        stopping.set()
        srv.close()
        thread.join(timeout=2)

    return port, received_tokens, stop


def _clear_env():
    os.environ.pop("FREECAD_BRIDGE_PORT", None)
    os.environ.pop("FREECAD_BRIDGE_TOKEN", None)


def check_file_lifecycle(session):
    print("write_bridge_file / remove_bridge_file")
    path = session.write_bridge_file(4321, "tok-abc")
    check("write_bridge_file returns bridge_file()'s path", path == session.bridge_file())
    check("the file exists", os.path.isfile(path))
    if os.name != "nt":
        mode = stat.S_IMODE(os.stat(path).st_mode)
        check("file mode is 0600", mode == 0o600, oct(mode))
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    check("port/token/pid/started are all present", (
        data.get("port") == 4321 and data.get("token") == "tok-abc"
        and data.get("pid") == os.getpid()
        and isinstance(data.get("started"), (int, float))
    ), data)
    folder = os.path.dirname(path)
    leftover = [f for f in os.listdir(folder) if f.startswith(".bridge-")]
    check("no .bridge-* temp file left behind", leftover == [], leftover)

    # A second write replaces cleanly.
    path2 = session.write_bridge_file(4322, "tok-def")
    check("a second write reuses the same path", path2 == path)
    if os.name != "nt":
        mode = stat.S_IMODE(os.stat(path2).st_mode)
        check("still 0600 after replace", mode == 0o600, oct(mode))
    with open(path2, encoding="utf-8") as fh:
        data2 = json.load(fh)
    check("replaced with the new values", data2["port"] == 4322 and data2["token"] == "tok-def", data2)
    leftover = [f for f in os.listdir(folder) if f.startswith(".bridge-")]
    check("still no leftover temp file", leftover == [], leftover)

    # remove_bridge_file is a no-op on a file that isn't ours.
    with open(path, encoding="utf-8") as fh:
        foreign = json.load(fh)
    foreign["pid"] = foreign["pid"] + 999999  # not this process
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(foreign, fh)
    session.remove_bridge_file()
    check("remove_bridge_file leaves a file with a different pid alone", os.path.isfile(path))

    # It removes a file that IS ours.
    session.write_bridge_file(4323, "tok-ghi")
    session.remove_bridge_file()
    check("remove_bridge_file removes our own file", not os.path.isfile(path))

    # And is a no-op when the file is simply absent.
    session.remove_bridge_file()
    check("remove_bridge_file is a no-op when the file is absent (no raise)", not os.path.isfile(path))


def check_endpoint_resolution(session, mcp_server):
    print("mcp_server._endpoint() resolution order")
    path = session.bridge_file()

    # Env present + a decoy file with a different port -> env wins. This is
    # the chat panel's own non-regression, in unit form: its turns must not
    # be steered by a bridge.json an external client (or a leftover autostart)
    # happens to have written.
    session.write_bridge_file(9999, "decoy-token")
    os.environ["FREECAD_BRIDGE_PORT"] = "1234"
    os.environ["FREECAD_BRIDGE_TOKEN"] = "env-token"
    try:
        port, token, source = mcp_server._endpoint()
        check("env vars win over a present file", (port, token) == (1234, "env-token"), (port, token))
        check("source reports env", source == "env", source)
    finally:
        _clear_env()

    # Env absent + a valid file -> file wins.
    session.write_bridge_file(5555, "file-token")
    port, token, source = mcp_server._endpoint()
    check("the file is used once env is absent", (port, token) == (5555, "file-token"), (port, token))
    check("source names the file", path in source, source)

    # Absent / malformed / port 0 / missing token, each raising BridgeUnavailable.
    os.remove(path)
    try:
        mcp_server._endpoint()
        check("a missing file raises", False)
    except mcp_server.BridgeUnavailable as exc:
        check("a missing file raises BridgeUnavailable", True)
        check("...and says how to start the bridge", "BridgeAutoStart" in str(exc), str(exc))

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("not { valid json")
    try:
        mcp_server._endpoint()
        check("malformed JSON raises", False)
    except mcp_server.BridgeUnavailable:
        check("malformed JSON raises BridgeUnavailable", True)

    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"port": 0, "token": "x"}, fh)
    try:
        mcp_server._endpoint()
        check("port 0 raises", False)
    except mcp_server.BridgeUnavailable:
        check("port 0 raises BridgeUnavailable", True)

    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"port": 4321}, fh)  # no token
    try:
        mcp_server._endpoint()
        check("a missing token raises", False)
    except mcp_server.BridgeUnavailable:
        check("a missing token raises BridgeUnavailable", True)

    # D8: tools/list on a broken bridge is a JSON-RPC error, not {"tools": []}.
    responses = _run_mcp(mcp_server, [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
    resp = responses[0]
    check("tools/list surfaces a JSON-RPC error, not an empty tool list",
          "error" in resp and "result" not in resp, resp)
    check("error code is -32603", resp.get("error", {}).get("code") == -32603, resp)

    os.remove(path)


def check_end_to_end(session, mcp_server):
    print("end-to-end through a fake bridge (the image-block path)")
    tool_table = [{"name": "get_objects", "description": "list objects",
                   "inputSchema": {"type": "object", "properties": {}}}]
    fake_png = base64.b64encode(b"not real png bytes, just a payload").decode("ascii")
    call_response = {"ok": True, "text": "did the thing",
                      "image": {"mimeType": "image/png", "data": fake_png}}

    port, received_tokens, stop = _start_fake_bridge(tool_table, call_response)
    token = "bridge-token-xyz"
    session.write_bridge_file(port, token)
    _clear_env()
    try:
        responses = _run_mcp(mcp_server, [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "get_objects", "arguments": {}}},
        ])
        check("three responses (the notification gets none)", len(responses) == 3, responses)
        init_resp, list_resp, call_resp = responses if len(responses) == 3 else (None, None, None)
        check("initialize succeeds", bool(init_resp) and "result" in init_resp, init_resp)
        check("tools/list returns the fake bridge's table",
              bool(list_resp) and list_resp.get("result", {}).get("tools") == tool_table, list_resp)
        content = call_resp.get("result", {}).get("content", []) if call_resp else []
        check("the text content block survives",
              any(b.get("type") == "text" and b.get("text") == "did the thing" for b in content),
              content)
        image_blocks = [b for b in content if b.get("type") == "image"]
        check("the image content block survives into the MCP result",
              len(image_blocks) == 1 and image_blocks[0].get("data") == fake_png
              and image_blocks[0].get("mimeType") == "image/png", content)
        check("the fake bridge received the token FROM THE FILE",
              received_tokens and all(t == token for t in received_tokens), received_tokens)
    finally:
        stop()
        try:
            os.remove(session.bridge_file())
        except OSError:
            pass


def check_stale_file(session, mcp_server):
    print("stale discovery file (a bound-then-closed port)")
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    stale_port = probe.getsockname()[1]
    probe.close()  # nothing is listening on stale_port any more

    path = session.write_bridge_file(stale_port, "stale-token")
    _clear_env()
    try:
        start = time.monotonic()
        responses = _run_mcp(mcp_server, [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "get_objects", "arguments": {}}},
        ])
        elapsed = time.monotonic() - start
        check("a stale port is reported quickly, not after the 900s call timeout",
              elapsed < 15.0, elapsed)
        result = responses[0].get("result", {}) if responses else {}
        check("tools/call reports isError rather than hanging or crashing",
              result.get("isError") is True, responses)
        text = (result.get("content") or [{}])[0].get("text", "")
        check("the message names the discovery file's path", path in text, text)
    finally:
        try:
            os.remove(session.bridge_file())
        except OSError:
            pass


def main():
    print("bridge discovery: session.py + mcp_server.py")

    home_dir = tempfile.mkdtemp(prefix="fcc-bridge-test-home-")
    old_home = os.environ.get("HOME")
    old_userprofile = os.environ.get("USERPROFILE")
    old_bridge_port = os.environ.get("FREECAD_BRIDGE_PORT")
    old_bridge_token = os.environ.get("FREECAD_BRIDGE_TOKEN")
    # Set BEFORE loading session.py: its _DEFAULT_ARTIFACTS_DIR is a module-
    # level constant computed at import time from expanduser("~"). mcp_server's
    # own resolution is call-time (see its _bridge_file_path), so it agrees
    # with whatever HOME/USERPROFILE says at every call regardless of when it
    # was imported -- but pointing both at the same home here keeps every
    # check working from one shared, disposable folder.
    os.environ["HOME"] = home_dir
    os.environ["USERPROFILE"] = home_dir
    _clear_env()

    try:
        session = _load(
            "_fcc_test_session",
            os.path.join(_ROOT, "freecad", "freecadclaude", "freecad_tools", "session.py"),
        )
        mcp_server = _load("_fcc_test_mcp_server", os.path.join(_ROOT, "mcp_server.py"))

        pulled_in = _forbidden(sys.modules) - _PRELOADED
        check("importing session.py/mcp_server.py pulls in no FreeCAD/Qt",
              not pulled_in, pulled_in)

        check("bridge_file() sits under this fake home",
              session.bridge_file() == os.path.join(home_dir, "FreeCADClaude", "bridge.json"),
              session.bridge_file())

        check_file_lifecycle(session)
        check_endpoint_resolution(session, mcp_server)
        check_end_to_end(session, mcp_server)
        check_stale_file(session, mcp_server)
    finally:
        _clear_env()
        if old_bridge_port is not None:
            os.environ["FREECAD_BRIDGE_PORT"] = old_bridge_port
        if old_bridge_token is not None:
            os.environ["FREECAD_BRIDGE_TOKEN"] = old_bridge_token
        if old_home is not None:
            os.environ["HOME"] = old_home
        else:
            os.environ.pop("HOME", None)
        if old_userprofile is not None:
            os.environ["USERPROFILE"] = old_userprofile
        else:
            os.environ.pop("USERPROFILE", None)
        import shutil

        shutil.rmtree(home_dir, ignore_errors=True)

    print()
    if _failures:
        print(f"FAIL: {len(_failures)} check(s) failed: {', '.join(_failures)}")
        return 1
    print("PASS")
    return 0


# No __main__ guard: freecadcmd *imports* the script under a module name taken
# from the filename, so a guarded body would silently never run there -- which
# is the one interpreter this most needs to work under. It also tears the
# process down on SystemExit without flushing, so do that ourselves or the
# entire report is discarded and the run looks like it printed nothing.
_status = main()
sys.stdout.flush()
sys.exit(_status)
