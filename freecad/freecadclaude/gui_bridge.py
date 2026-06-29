# SPDX-License-Identifier: LGPL-2.1-or-later
"""In-FreeCAD bridge that executes tool calls on the GUI main thread.

The MCP server (a child of the claude CLI) connects here over a localhost
socket and asks us to list or run FreeCAD tools. Tool execution must happen on
the Qt GUI thread (the FreeCAD API is not thread-safe), so the socket handler
posts a Qt event to an invoker living on the GUI thread and blocks until it
completes.

    MCP child  --TCP json line-->  _handle() (bridge thread)
    bridge thread --postEvent-->  _Invoker.customEvent() (GUI thread)  --> result

``start()`` must be called from the GUI thread (so the invoker has GUI-thread
affinity). It is idempotent and returns (port, token).
"""

import json
import secrets
import socket
import threading

from PySide import QtCore

from . import freecad_tools

# A private Qt event type for "run this callable on the GUI thread".
_EVENT_TYPE = QtCore.QEvent.Type(QtCore.QEvent.registerEventType())


class _CallEvent(QtCore.QEvent):
    def __init__(self, fn):
        super().__init__(_EVENT_TYPE)
        self.fn = fn
        self.result = None
        self.error = None
        self.done = threading.Event()


class _Invoker(QtCore.QObject):
    """Lives on the GUI thread; runs queued callables there."""

    def customEvent(self, event):
        try:
            event.result = event.fn()
        except Exception as exc:  # noqa: BLE001 - relay to caller
            event.error = exc
        finally:
            event.done.set()


_state = {"port": None, "token": None, "invoker": None}


def start():
    """Start the bridge (idempotent). Call from the GUI thread. Returns (port, token)."""
    if _state["port"] is not None:
        return _state["port"], _state["token"]

    _state["invoker"] = _Invoker()  # created here -> GUI-thread affinity
    token = secrets.token_hex(16)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]

    threading.Thread(target=_serve, args=(srv, token), daemon=True).start()
    _state["port"], _state["token"] = port, token
    return port, token


def _run_on_gui(fn, timeout=600):
    ev = _CallEvent(fn)
    QtCore.QCoreApplication.postEvent(_state["invoker"], ev)
    if not ev.done.wait(timeout):
        raise TimeoutError("FreeCAD GUI thread did not respond in time")
    if ev.error is not None:
        raise ev.error
    return ev.result


#: Session-wide "approve all" toggle, set from the confirmation dialog.
_auto_approve = {"on": False}


def _confirm_dialog(tool_name, args):
    """Ask the user to approve a tool call. Runs on the GUI thread."""
    if _auto_approve["on"]:
        return True

    from PySide import QtWidgets

    code = args.get("code", "")
    desc = args.get("description") or ""

    box = QtWidgets.QMessageBox()
    box.setWindowTitle("FreeCADClaude — approve action?")
    box.setIcon(QtWidgets.QMessageBox.Question)
    box.setText(f"Claude wants to run <b>{tool_name}</b>" + (f": {desc}" if desc else "."))
    if code:
        box.setInformativeText("Review the code under “Show Details”.")
        box.setDetailedText(code)
    run_btn = box.addButton("Run", QtWidgets.QMessageBox.AcceptRole)
    all_btn = box.addButton("Run all this session", QtWidgets.QMessageBox.YesRole)
    box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
    box.setDefaultButton(run_btn)
    box.exec()

    clicked = box.clickedButton()
    if clicked is all_btn:
        _auto_approve["on"] = True
        return True
    return clicked is run_btn


def _serve(srv, token):
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        threading.Thread(target=_handle, args=(conn, token), daemon=True).start()


def _handle(conn, token):
    with conn:
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
        try:
            req = json.loads(buf.decode("utf-8"))
        except ValueError:
            return
        reply = _dispatch(req, token)
        try:
            conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
        except OSError:
            pass


def _dispatch(req, token):
    if req.get("token") != token:
        return {"ok": False, "error": "unauthorized"}

    op = req.get("op")
    if op == "list":
        return {"tools": freecad_tools.list_schemas()}
    if op == "call":
        name = req.get("tool")
        args = req.get("arguments") or {}
        tool = freecad_tools.TOOLS.get(name)
        if tool is None:
            return {"ok": False, "error": f"unknown tool: {name}"}
        # Pre-flight check (e.g. a syntax compile for run_python) BEFORE the confirm
        # dialog -- no point asking the user to approve code that can't run. Pure
        # Python, no FreeCAD access, so it's fine off the GUI thread. A non-empty
        # result is relayed to Claude as the tool's output so it fixes and retries.
        precheck = tool.get("precheck")
        if precheck is not None:
            try:
                problem = precheck(args)
            except Exception as exc:  # noqa: BLE001
                problem = f"precheck error: {exc!r}"
            if problem:
                return {"ok": True, "text": problem}
        if tool.get("confirm"):
            try:
                approved = _run_on_gui(lambda: _confirm_dialog(name, args))
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"confirmation failed: {exc!r}"}
            if not approved:
                return {"ok": False, "error": "The user declined to run this code."}
        def _call():
            out = tool["run"](args)
            # Scan for features that failed to recompute during this call and fold
            # a one-line summary into the reply (get_diagnostics reports its own).
            note = "" if name == "get_diagnostics" else freecad_tools.summarize_new_failures()
            return out, note

        try:
            text, note = _run_on_gui(_call)
            if note:
                text = f"{text}\n\n{note}"
            return {"ok": True, "text": text}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": repr(exc)}
    return {"ok": False, "error": f"unknown op: {op}"}
