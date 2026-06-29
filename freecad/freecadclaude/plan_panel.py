# SPDX-License-Identifier: LGPL-2.1-or-later
"""A dock panel showing the agent's Plan and its task list, separate from chat.

Populated by signals from the AgentWorker:
- ``task_event`` -> the task checklist (TaskCreate/TaskUpdate from the CLI's
  task tools).
- ``plan_received`` -> the Markdown output of the Plan subagent.

ChatWidget connects the worker's signals to this panel's slots when it starts
the agent.
"""

import FreeCAD
import FreeCADGui

from PySide import QtCore, QtGui, QtWidgets

DOCK_OBJECT_NAME = "FreeCADClaudePlanDock"
CHAT_DOCK_OBJECT_NAME = "FreeCADClaudeDock"

#: status -> (glyph, color)
_STATUS = {
    "pending": ("○", "#888888"),
    "in_progress": ("◐", "#3478c6"),
    "completed": ("✓", "#3a9d4a"),
    "cancelled": ("✗", "#c0392b"),
}

_panel_instance = None


def get_panel():
    global _panel_instance
    if _panel_instance is None:
        _panel_instance = PlanPanel()
    return _panel_instance


class PlanPanel:
    """Owns the QDockWidget for the plan/tasks view."""

    def __init__(self):
        self._dock = None
        self._build_dock()

    def _build_dock(self):
        main_window = FreeCADGui.getMainWindow()
        existing = main_window.findChild(QtWidgets.QDockWidget, DOCK_OBJECT_NAME)
        if existing is not None:
            self._dock = existing
            return

        dock = QtWidgets.QDockWidget(main_window)
        dock.setObjectName(DOCK_OBJECT_NAME)
        dock.setWindowTitle("Claude · Plan & Tasks")
        dock.setWidget(PlanTasksWidget(dock))
        main_window.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)

        # Tab it together with the chat dock if that exists.
        chat = main_window.findChild(QtWidgets.QDockWidget, CHAT_DOCK_OBJECT_NAME)
        if chat is not None:
            main_window.tabifyDockWidget(chat, dock)
        self._dock = dock

    def show_dock(self):
        if self._dock is None:
            self._build_dock()
        self._dock.show()  # don't raise -- let chat stay in front

    @property
    def widget(self):
        return self._dock.widget()


class PlanTasksWidget(QtWidgets.QWidget):
    """Top: the Plan (Markdown). Bottom: a live task checklist."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._task_state = {}   # num(str) -> {"subject", "status"}
        self._task_items = {}   # num(str) -> QListWidgetItem
        self._plan_md = ""
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical, self)

        plan_box = QtWidgets.QWidget()
        plan_layout = QtWidgets.QVBoxLayout(plan_box)
        plan_layout.setContentsMargins(0, 0, 0, 0)
        plan_layout.addWidget(QtWidgets.QLabel("<b>Plan</b>"))
        self.plan_view = QtWidgets.QTextBrowser()
        self.plan_view.setOpenExternalLinks(True)
        self.plan_view.setPlaceholderText("The Plan subagent's output appears here.")
        plan_layout.addWidget(self.plan_view)
        splitter.addWidget(plan_box)

        task_box = QtWidgets.QWidget()
        task_layout = QtWidgets.QVBoxLayout(task_box)
        task_layout.setContentsMargins(0, 0, 0, 0)
        task_layout.addWidget(QtWidgets.QLabel("<b>Tasks</b>"))
        self.task_list = QtWidgets.QListWidget()
        task_layout.addWidget(self.task_list)
        splitter.addWidget(task_box)

        splitter.setSizes([300, 200])
        layout.addWidget(splitter, stretch=1)

        clear = QtWidgets.QPushButton("Clear", self)
        clear.clicked.connect(self.clear)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(clear)
        layout.addLayout(row)

    # -- slots (connected to worker signals; run on the GUI thread) ------

    @QtCore.Slot(dict)
    def on_task_event(self, event):
        num = str(event.get("num"))
        if event.get("op") == "create":
            self._task_state[num] = {"subject": event.get("subject", "Task"), "status": "pending"}
            item = self._task_items.get(num)
            if item is None:
                item = QtWidgets.QListWidgetItem()
                self.task_list.addItem(item)
                self._task_items[num] = item
            self._render_item(num)
        elif event.get("op") == "update":
            state = self._task_state.get(num)
            if state is None:  # update for a task we never saw create -> add a stub
                state = self._task_state[num] = {"subject": f"Task #{num}", "status": "pending"}
                item = QtWidgets.QListWidgetItem()
                self.task_list.addItem(item)
                self._task_items[num] = item
            state["status"] = event.get("status") or state["status"]
            self._render_item(num)

    @QtCore.Slot(str)
    def on_plan(self, text):
        self._plan_md = (self._plan_md + "\n\n---\n\n" + text) if self._plan_md else text
        self.plan_view.setMarkdown(self._plan_md)
        bar = self.plan_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear(self):
        self._task_state.clear()
        self._task_items.clear()
        self.task_list.clear()
        self._plan_md = ""
        self.plan_view.setMarkdown("")

    # -- helpers ---------------------------------------------------------

    def _render_item(self, num):
        state = self._task_state[num]
        glyph, color = _STATUS.get(state["status"], _STATUS["pending"])
        item = self._task_items[num]
        item.setText(f"{glyph}  {state['subject']}")
        item.setForeground(QtGui.QBrush(QtGui.QColor(color)))
