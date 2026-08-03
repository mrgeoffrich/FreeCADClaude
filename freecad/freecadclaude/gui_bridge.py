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

import base64
import json
import secrets
import socket
import threading

from PySide import QtCore

from . import freecad_tools

# A private Qt event type for "run this callable on the GUI thread".
_EVENT_TYPE = QtCore.QEvent.Type(QtCore.QEvent.registerEventType())

# How long we wait for the GUI thread before reporting the call as still-running.
# mcp_server's socket timeout must stay ABOVE this so the bridge, not the socket,
# is the side that gives up first -- it's the only one that knows what happened.
_GUI_CALL_TIMEOUT = 600


class GuiBusyTimeout(Exception):
    """A GUI-thread call outlasted the timeout -- and is STILL running.

    Nothing here can preempt Python already executing on the GUI thread, so this
    is a report, not a cancellation: the call keeps going and, if it succeeds,
    still commits its transaction. The message has to say that, because the
    obvious reading of a timeout -- "it failed, send it again" -- is the one
    response that makes things worse: the resend queues behind the first call
    and can apply the same change twice.
    """


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


def _run_on_gui(fn, label="call", timeout=_GUI_CALL_TIMEOUT):
    ev = _CallEvent(fn)
    QtCore.QCoreApplication.postEvent(_state["invoker"], ev)
    if not ev.done.wait(timeout):
        raise GuiBusyTimeout(
            f"'{label}' has been running on FreeCAD's GUI thread for {timeout}s "
            "and has NOT returned. It was not cancelled -- it is still running, "
            "FreeCAD is frozen until it finishes, and if it succeeds it will "
            "still commit its changes.\n\n"
            "Do NOT resend this call: a second copy queues behind the first and "
            "could apply the same change twice. Tell the user FreeCAD is busy "
            "with this call and wait for it. Once it frees up, call get_objects "
            "to see what actually landed before doing anything else.\n\n"
            "The usual cause is a loop of Part shape operations (slice, cut, "
            "fuse, recompute, ...): each costs tens to hundreds of milliseconds "
            "on a real solid, and run_python holds the GUI thread for the whole "
            "call. Restructure it to use the one-shot form rather than retrying."
        )
    if ev.error is not None:
        raise ev.error
    return ev.result


def _warn(msg):
    """Surface a bridge-level problem in FreeCAD's report view.

    The user is looking at a frozen or misbehaving FreeCAD, not at the chat
    transcript, so the places where we drop something on the floor say so where
    they'll actually see it. Imported lazily and failure-tolerantly -- this must
    never be the reason a reply doesn't get sent."""
    try:
        import FreeCAD

        FreeCAD.Console.PrintWarning(msg + "\n")
    except Exception:  # noqa: BLE001 - diagnostics must not raise
        pass


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
            # The caller gave up and closed the socket, so this reply has nowhere
            # to go. Worth saying out loud: the call itself still ran to
            # completion, and a mutating one still committed, even though Claude
            # was told it failed.
            _warn(
                "FreeCADClaude: dropped the reply to a "
                f"'{req.get('tool', '?')}' call -- the caller had already timed "
                "out. The call itself still ran and its changes stand."
            )


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
        # Pre-flight check (e.g. a syntax compile for run_python) BEFORE the tool
        # runs -- code that can't compile shouldn't reach the GUI thread or open a
        # transaction. Pure Python, no FreeCAD access, so it's fine off the GUI
        # thread. A non-empty result is relayed to Claude as the tool's output so
        # it fixes and retries.
        precheck = tool.get("precheck")
        if precheck is not None:
            try:
                problem = precheck(args)
            except Exception as exc:  # noqa: BLE001
                problem = f"precheck error: {exc!r}"
            if problem:
                return {"ok": True, "text": problem}
        def _call():
            # Snapshot PartDesign feature volumes/solid-counts BEFORE a mutating
            # tool runs, so post_tool_notes can diff and report what this operation
            # added/removed (None for read-only tools -> nothing to diff).
            before = freecad_tools.feature_snapshot(name)
            out = tool["run"](args)
            # Image tools (view_sketch_svg, capture_view) return (text, png_path);
            # everything else returns a plain string.
            text, png_path = out if isinstance(out, tuple) else (out, None)
            # Fold post-call diagnostics into the reply: features that failed to
            # recompute, plus a per-operation volume/solid-count delta with the
            # empty-cut and disconnected-solid escalations. get_diagnostics is
            # skipped (it reports failures itself) -- the composer handles that.
            note = freecad_tools.post_tool_notes(name, before)
            return text, png_path, note

        try:
            text, png_path, note = _run_on_gui(_call, label=name)
            if note:
                text = f"{text}\n\n{note}"
            reply = {"ok": True, "text": text}
            if png_path:
                try:
                    with open(png_path, "rb") as fh:
                        reply["image"] = {
                            "mimeType": "image/png",
                            "data": base64.b64encode(fh.read()).decode("ascii"),
                        }
                except OSError as exc:
                    reply["text"] += f"\n\n(failed to read rendered image: {exc!r})"
            return reply
        except GuiBusyTimeout as exc:
            # Already written for Claude to read -- don't repr() it into noise.
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": repr(exc)}
    return {"ok": False, "error": f"unknown op: {op}"}
