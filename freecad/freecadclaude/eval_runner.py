# SPDX-License-Identifier: LGPL-2.1-or-later
"""Unattended end-to-end evaluation of the FreeCADClaude agent.

Triggered from InitGui.py when the FREECADCLAUDE_EVAL env var is set. It opens the
chat panel, submits a prompt (FREECADCLAUDE_EVAL_PROMPT), auto-approves run_python,
waits for the turn to finish via a nested Qt event loop (so the GUI thread stays
free to marshal tool calls), writes a JSON report (FREECADCLAUDE_EVAL_RESULT), and
quits FreeCAD. Launch it with eval/run.ps1.
"""

import json
import os
import traceback

import FreeCAD
import FreeCADGui

from PySide import QtCore, QtWidgets

_DEFAULT_PROMPT = "Create a box exactly 20 x 20 x 20 mm. Do not ask questions."


def _result_path():
    return os.environ.get("FREECADCLAUDE_EVAL_RESULT") or os.path.join(
        os.path.expanduser("~"), "freecadclaude_eval_result.json"
    )


def _snapshot_document():
    doc = FreeCAD.ActiveDocument
    if doc is None:
        return {"document": None, "object_count": 0, "objects": []}
    objects = []
    for obj in doc.Objects:
        info = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
        dims = {}
        for prop in ("Length", "Width", "Height", "Radius", "Radius1", "Radius2"):
            if hasattr(obj, prop):
                value = getattr(obj, prop)
                dims[prop] = getattr(value, "Value", value)
        if dims:
            info["dimensions"] = dims
        objects.append(info)
    return {"document": doc.Label, "object_count": len(objects), "objects": objects}


def run():
    prompt = os.environ.get("FREECADCLAUDE_EVAL_PROMPT", _DEFAULT_PROMPT)
    timeout_ms = int(os.environ.get("FREECADCLAUDE_EVAL_TIMEOUT", "240")) * 1000
    report = {"prompt": prompt, "completed": False, "error": None,
              "transcript": "", "result": {}}

    try:
        from . import chat_panel, gui_bridge

        gui_bridge.start()
        gui_bridge._auto_approve["on"] = True  # unattended: skip the confirm dialog

        if FreeCAD.ActiveDocument is None:
            FreeCAD.newDocument("Eval")

        panel = chat_panel.get_panel()
        panel.show_dock()
        widget = panel.widget
        widget.input.setPlainText(prompt)
        widget.on_send()

        worker = widget._worker
        if worker is None:
            report["error"] = "agent did not start (claude CLI missing or not logged in?)"
        else:
            loop = QtCore.QEventLoop()
            state = {"done": False}

            def _on_done():
                if not state["done"]:
                    state["done"] = True
                    loop.quit()

            worker.turn_finished.connect(_on_done)
            QtCore.QTimer.singleShot(timeout_ms, loop.quit)
            loop.exec()

            report["completed"] = state["done"]
            if not state["done"]:
                report["error"] = "timeout"
            report["transcript"] = widget._md
    except Exception:
        report["error"] = traceback.format_exc()

    report["result"] = _snapshot_document()
    try:
        with open(_result_path(), "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
    except OSError:
        pass

    # Close documents without save prompts, then quit FreeCAD.
    try:
        for name in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(name)
    except Exception:  # noqa: BLE001
        pass
    app = QtWidgets.QApplication.instance()
    if app is not None:
        QtCore.QTimer.singleShot(300, app.quit)
