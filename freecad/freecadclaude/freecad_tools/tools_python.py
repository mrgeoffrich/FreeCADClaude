# SPDX-License-Identifier: LGPL-2.1-or-later
"""run_python -- the sole document-mutating tool, gated on user approval."""

from .namespace import scripting_namespace
from .session import _save_run_python_script, _save_step_snapshot, _save_steps_enabled

_RUN_PYTHON_SCHEMA = {
    "name": "run_python",
    "description": (
        "Execute FreeCAD Python in the running FreeCAD instance. This is how you "
        "do Sketcher work (geometry + constraints), PartDesign features "
        "(Body, Pad, Pocket, Revolution, Loft, Fillet, Chamfer, ...), Part "
        "booleans, Draft, arrays, and anything else in the API. "
        "Pre-bound names: FreeCAD, App, FreeCADGui, Gui, Part, Sketcher, "
        "PartDesign, Draft, and doc (the active document, created if none). "
        "The code runs on the GUI thread inside ONE undoable transaction. "
        "Return data by printing or by assigning to a variable named `result` "
        "(both are returned to you). On error you get the full traceback and the "
        "transaction is rolled back -- fix it and try again. If you're unsure of "
        "a method's parameters, call inspect_api first rather than guessing. "
        "Work in small steps and verify with get_objects. For PartDesign, create "
        "a PartDesign::Body first and add features inside it. The user is shown "
        "your code and must approve it before it runs."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "FreeCAD Python source to execute"},
            "description": {
                "type": "string",
                "description": "One-line summary of what the code does (shown to the user for approval)",
            },
        },
        "required": ["code"],
    },
}


def _precheck_python(args):
    """Reject code that won't even compile, BEFORE the user is asked to approve it.

    Run by the bridge ahead of the confirmation dialog: there's no point making
    the user approve code that can't run, and Claude gets the error a turn
    sooner. Returns an error string to relay to Claude, or "" when the code is
    syntactically fine. NB this only catches Python-level syntax errors -- a
    linter can't validate FreeCAD's C++ call signatures, so for *parameter*
    mistakes the agent should reach for inspect_api instead.
    """
    code = args.get("code", "")
    if not code.strip():
        return "No code provided."
    try:
        compile(code, "<run_python>", "exec")
    except SyntaxError as exc:
        where = f"line {exc.lineno}" + (f", col {exc.offset}" if exc.offset else "")
        lines = [f"SyntaxError at {where}: {exc.msg}. Nothing ran -- fix and resend."]
        detail = (exc.text or "").rstrip("\n")
        if detail:
            lines.append(detail)
            lines.append(" " * (max(1, exc.offset or 1) - 1) + "^")
        return "\n".join(lines)
    return ""


def _doc_alive(doc):
    """True while `doc` still references a live document. Running code can close
    the document out from under us (e.g. App.closeDocument); the handle then
    becomes a deleted C++ object and ANY attribute access on it raises, so this
    is how we detect that before touching the stale transaction. Checks the
    handle, not the name -- a closed document's name can be reused by a new one."""
    try:
        doc.Name
        return True
    except Exception:  # noqa: BLE001 - ReferenceError on a deleted document
        return False


def _document_closed_msg(doc_name, stdout_text, tb=None):
    """Reply for when run_python code closed the document it was operating on.

    The transaction we opened went with the document, so there's nothing to
    commit or roll back on our now-deleted handle -- steer back to the supported
    pattern instead of surfacing a bare 'deleted object' ReferenceError."""
    import FreeCAD

    active = FreeCAD.ActiveDocument
    parts = [
        f"run_python closed the active document '{doc_name}' mid-call. Avoid "
        "closing or recreating the document from inside run_python: each call "
        "runs in an undoable transaction on that document, so closing it leaves "
        "nothing to commit and undo can't cover the change. To redo a document's "
        "contents, remove the objects with doc.removeObject(name) and rebuild "
        "them in place instead.",
        f"The active document is now '{active.Name}'." if active is not None
        else "There is no active document now.",
    ]
    if tb:
        parts.append(
            "The code also raised before finishing (no rollback was possible -- "
            "the document was already gone):\n" + tb
        )
    if stdout_text:
        parts.append("stdout:\n" + stdout_text)
    return "\n".join(parts)


def _run_python(args):
    import contextlib
    import io
    import traceback

    import FreeCAD

    code = args.get("code", "")
    doc = FreeCAD.ActiveDocument or FreeCAD.newDocument()

    _save_run_python_script(code, args.get("description") or "")

    namespace = scripting_namespace(doc)

    existing = {obj.Name for obj in doc.Objects}
    doc_name = doc.Name  # remember it now -- the handle dies if the code closes it
    doc.openTransaction("FreeCADClaude: run_python")
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            exec(code, namespace)  # noqa: S102 - intentional, user-approved
        if not _doc_alive(doc):
            # The code closed the document mid-run; the transaction went with it,
            # so don't touch the stale handle -- report it and bail cleanly.
            return _document_closed_msg(doc_name, stdout.getvalue())
        doc.recompute()
        doc.commitTransaction()
    except Exception:
        tb = traceback.format_exc()
        captured = stdout.getvalue()
        if not _doc_alive(doc):
            # Same case, but the code also raised: no rollback is possible on a
            # document that no longer exists, so don't crash trying to abort it.
            return _document_closed_msg(doc_name, captured, tb)
        doc.abortTransaction()
        # Safety net: if undo is disabled (so abort didn't roll back), remove any
        # objects this failed run added. No-op when abort already removed them.
        for obj in list(doc.Objects):
            if obj.Name not in existing:
                try:
                    doc.removeObject(obj.Name)
                except Exception:  # noqa: BLE001
                    pass
        msg = "Execution failed (rolled back):\n" + tb
        if captured:
            msg += "\n--- stdout before error ---\n" + captured
        return msg

    # Optional: snapshot the committed document so the build can be reviewed step
    # by step (off by default; see _save_steps_enabled). Kept out of the reply so
    # it stays a purely on-disk artifact and doesn't nudge the model.
    if _save_steps_enabled():
        _save_step_snapshot(doc, args.get("description") or "")

    parts = ["OK (committed)."]
    captured = stdout.getvalue()
    if captured:
        parts.append("stdout:\n" + captured)
    if namespace.get("result") is not None:
        parts.append("result: " + repr(namespace["result"]))
    return "\n".join(parts)
