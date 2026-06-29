# SPDX-License-Identifier: LGPL-2.1-or-later
"""The dockable Claude chat panel.

The panel drives the ``claude`` CLI (via :class:`AgentWorker` on a worker
thread) and renders the conversation. The transcript is kept as a Markdown
string and rendered with ``QTextBrowser.setMarkdown`` so Claude's Markdown
replies (headings, lists, **bold**, ``code``, fenced blocks) format properly
and inherit FreeCAD's theme colours.

The panel is a singleton: one dock per FreeCAD session, created lazily and
re-shown on demand. The agent thread is started lazily on the first send.
"""

import html
import time

import FreeCAD
import FreeCADGui

# FreeCAD bundles its own Qt binding under the ``PySide`` name. Always import
# from ``PySide`` so the addon matches the running FreeCAD.
from PySide import QtCore, QtGui, QtWidgets

from . import _deps
from .agent_worker import AgentWorker

#: Role label colours. setMarkdown passes inline HTML spans through, so we
#: colour just the labels while the Markdown body keeps the theme's text colour.
_YOU_COLOR = "#3478c6"      # blue
_CLAUDE_COLOR = "#d97757"   # Claude coral
_MUTED_COLOR = "#888888"    # de-emphasised grey (status text, reasoning)
_CLAUDE_HEADER = f'<span style="color:{_CLAUDE_COLOR}"><b>Claude:</b></span>'

#: Show Claude's streamed reasoning as a muted blockquote. Flip to False to hide
#: it (the agent still thinks; you just won't see it).
SHOW_THINKING = True

#: Object name used both to register the dock and to find it again later.
DOCK_OBJECT_NAME = "FreeCADClaudeDock"

_panel_instance = None


def get_panel():
    """Return the singleton :class:`ChatPanel`, creating it on first use."""
    global _panel_instance
    if _panel_instance is None:
        _panel_instance = ChatPanel()
    return _panel_instance


class ChatPanel:
    """Owns the QDockWidget and its inner chat widget."""

    def __init__(self):
        self._dock = None
        self._build_dock()

    def _build_dock(self):
        main_window = FreeCADGui.getMainWindow()

        # Reuse an existing dock if one survived a workbench reload.
        existing = main_window.findChild(QtWidgets.QDockWidget, DOCK_OBJECT_NAME)
        if existing is not None:
            self._dock = existing
            return

        dock = QtWidgets.QDockWidget(main_window)
        dock.setObjectName(DOCK_OBJECT_NAME)
        dock.setWindowTitle("Claude")
        dock.setWidget(ChatWidget(dock))

        main_window.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
        self._dock = dock

    def show_dock(self):
        if self._dock is None:
            self._build_dock()
        self._dock.show()
        self._dock.raise_()

    def toggle_dock(self):
        if self._dock is None:
            self._build_dock()
        self._dock.setVisible(not self._dock.isVisible())

    @property
    def widget(self):
        return self._dock.widget()


class ChatWidget(QtWidgets.QWidget):
    """Transcript + input box + Send button, wired to the agent worker."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self._worker = None
        self._busy = False
        self._md = ""         # the committed transcript, as Markdown
        self._live = None     # the in-progress Claude block being streamed
        self._live_think = None  # the in-progress reasoning block being streamed
        self._thinking = False
        self._think_dots = 0
        self._think_start = 0.0
        self._think_timer = QtCore.QTimer(self)
        self._think_timer.setInterval(450)
        self._think_timer.timeout.connect(self._tick_thinking)
        # Coalesces rapid streaming deltas into ~12 renders/sec instead of one
        # full setMarkdown per token.
        self._render_timer = QtCore.QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(80)
        self._render_timer.timeout.connect(self._do_render)
        self._build_ui()

    # -- UI construction -------------------------------------------------

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.transcript = QtWidgets.QTextBrowser(self)
        self.transcript.setOpenExternalLinks(True)
        layout.addWidget(self.transcript, stretch=1)

        self.input = _InputBox(self)
        self.input.setPlaceholderText(
            "Ask Claude…  (Enter to send, Shift+Enter for newline)"
        )
        self.input.setFixedHeight(64)
        self.input.submitted.connect(self.on_send)
        layout.addWidget(self.input)

        button_row = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel("", self)
        self.status_label.setStyleSheet("color:#888888")
        button_row.addWidget(self.status_label)
        button_row.addStretch(1)
        self.files_button = QtWidgets.QPushButton("Files", self)
        self.files_button.setToolTip("Open the FreeCADClaude captures/exports folder")
        self.files_button.clicked.connect(self._open_artifacts)
        button_row.addWidget(self.files_button)
        self.new_button = QtWidgets.QPushButton("New", self)
        self.new_button.setToolTip("Start a new conversation (clears history and the agent's memory)")
        self.new_button.clicked.connect(self._on_new)
        button_row.addWidget(self.new_button)
        self.stop_button = QtWidgets.QPushButton("Stop", self)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._on_stop)
        button_row.addWidget(self.stop_button)
        self.send_button = QtWidgets.QPushButton("Send", self)
        self.send_button.setDefault(True)
        self.send_button.clicked.connect(self.on_send)
        button_row.addWidget(self.send_button)
        layout.addLayout(button_row)

        self._add_md('*Type a message to start a Claude session. '
                     'Try: "create a 20×40×10 box".*')

    # -- worker lifecycle ------------------------------------------------

    def _ensure_worker(self):
        """Start the agent thread on first use. Returns True if usable."""
        if self._worker is not None:
            return True

        ok, detail = _deps.cli_available()
        if not ok:
            self._add_md(_deps.INSTALL_HINT)
            self._add_md(f"*({detail})*")
            return False

        from . import agent_config, gui_bridge

        try:
            port, token = gui_bridge.start()  # runs on the GUI thread
        except Exception as exc:  # noqa: BLE001
            self._add_md(f"*Could not start the FreeCAD tool bridge: {exc!r}*")
            return False

        self._thread = QtCore.QThread(self)
        self._worker = AgentWorker(agent_config.build_config(detail, port, token))
        self._worker.moveToThread(self._thread)

        # Worker -> GUI (queued automatically across threads).
        self._worker.text_received.connect(self._on_text)
        if SHOW_THINKING:
            self._worker.thinking_received.connect(self._on_thinking)
        self._worker.tool_used.connect(self._on_tool)
        self._worker.turn_finished.connect(self._on_turn_finished)
        self._worker.status_changed.connect(self._on_status)
        self._worker.failed.connect(self._on_failed)

        # Route plan/task events to the separate Plan & Tasks panel.
        from . import plan_panel

        plan_widget = plan_panel.get_panel().widget
        self._worker.task_event.connect(plan_widget.on_task_event)
        self._worker.plan_received.connect(plan_widget.on_plan)

        self._thread.started.connect(self._worker.run)
        self._thread.start()

        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown_worker)
        return True

    def _shutdown_worker(self):
        if self._worker is not None:
            self._worker.shutdown()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)

    # -- sending ---------------------------------------------------------

    def on_send(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        if self._busy:
            self._add_md("*Still working on the previous message…*")
            return
        if not self._ensure_worker():
            return

        self.input.clear()
        self._add_md(
            f'<span style="color:{_YOU_COLOR}"><b>You:</b></span> {html.escape(text)}'
        )
        self._set_busy(True)
        self._set_thinking(True)
        self._worker.submit(text)

    # -- worker signal handlers (run on the GUI thread) ------------------

    @QtCore.Slot(str)
    def _on_text(self, chunk):
        if self._live is None:
            self._live = ""
        self._live += chunk
        self._render_soon()

    @QtCore.Slot(str)
    def _on_thinking(self, chunk):
        if self._live_think is None:
            self._live_think = ""
        self._live_think += chunk
        self._render_soon()

    @QtCore.Slot(str)
    def _on_tool(self, tool_name):
        self._commit_live()
        self._add_md(f"*↪ used tool: {tool_name}*")

    @QtCore.Slot()
    def _on_turn_finished(self):
        self._commit_live()
        self._set_thinking(False)
        self._set_busy(False)

    @QtCore.Slot(str)
    def _on_status(self, status):
        self.status_label.setText(status)

    @QtCore.Slot(str)
    def _on_failed(self, message):
        self._commit_live()
        self._set_thinking(False)
        self._add_md(f"**⚠ {message}**")
        self._set_busy(False)

    # -- transcript ------------------------------------------------------

    def _add_md(self, fragment):
        """Append a Markdown fragment to the committed transcript and render."""
        self._md += fragment.rstrip() + "\n\n"
        self._render()

    def _commit_live(self):
        """Finalize the streamed reasoning + Claude block into the transcript."""
        if self._live_think is not None:
            think = self._live_think.strip()
            if think:
                self._md += self._format_thinking(think) + "\n\n"
            self._live_think = None
        if self._live is not None:
            text = self._live.strip()
            if text:
                self._md += f"{_CLAUDE_HEADER}\n\n{text}\n\n"
            self._live = None

    @staticmethod
    def _format_thinking(text, live=False):
        """Render reasoning as a muted, de-emphasised blockquote."""
        label = "💭 <i>thinking…</i>" if live else "💭 <i>thought</i>"
        header = f'<span style="color:{_MUTED_COLOR}">{label}</span>'
        quoted = "\n".join("> " + line for line in text.splitlines())
        return f"{header}\n\n{quoted}"

    def _render(self):
        """Render now (cancels any pending throttled render)."""
        self._render_timer.stop()
        self._do_render()

    def _render_soon(self):
        """Render within ~80ms, coalescing rapid streaming deltas."""
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _do_render(self):
        """Render committed transcript + the live streamed block + thinking line."""
        body = self._md
        if self._live_think is not None:
            body += self._format_thinking(self._live_think, live=True) + "\n\n"
        if self._live is not None:
            body += f"{_CLAUDE_HEADER}\n\n{self._live}\n\n"
        # Animated placeholder only until reasoning or text starts streaming.
        if self._thinking and self._live is None and self._live_think is None:
            elapsed = int(time.monotonic() - self._think_start)
            body += (
                f'<span style="color:{_CLAUDE_COLOR}"><i>Thinking'
                f'{"." * self._think_dots} ({elapsed}s)</i></span>'
            )
        bar = self.transcript.verticalScrollBar()
        # Was the view pinned to (near) the bottom *before* we replace the doc?
        # Only then do we re-pin -- otherwise leave the user where they scrolled.
        stick = bar.value() >= bar.maximum() - 8
        prev = bar.value()
        self.transcript.setMarkdown(body)
        if stick:
            # ensureCursorVisible forces layout to the end and scrolls there,
            # which is robust to the async relayout of a long document. Reading
            # bar.maximum() right after setMarkdown can be stale -> the jitter.
            self.transcript.moveCursor(QtGui.QTextCursor.End)
            self.transcript.ensureCursorVisible()
        else:
            bar.setValue(min(prev, bar.maximum()))

    def _set_thinking(self, on):
        self._thinking = on
        self._think_dots = 0
        if on:
            self._think_start = time.monotonic()
            self._think_timer.start()
        else:
            self._think_timer.stop()
        self._render()

    def _tick_thinking(self):
        self._think_dots = (self._think_dots + 1) % 4
        self._render()

    def _on_new(self):
        """Start a fresh conversation: reset the agent's session and clear panels."""
        if self._worker is not None:
            if self._busy:
                self._worker.cancel()
            self._worker.reset_session()
        self._md = ""
        self._live = None
        self._live_think = None
        self._set_busy(False)
        self._set_thinking(False)
        try:
            from . import plan_panel

            plan_panel.get_panel().widget.clear()
        except Exception:  # noqa: BLE001
            pass
        self._add_md("*New conversation started.*")

    def _open_artifacts(self):
        """Open the FreeCADClaude captures/exports folder in the file manager."""
        from . import freecad_tools
        from PySide import QtGui

        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(freecad_tools.artifacts_dir())
        )

    def _on_stop(self):
        if self._worker is not None and self._busy:
            self._worker.cancel()
            self._commit_live()
            self._add_md("*(stopped)*")

    def _set_busy(self, busy):
        self._busy = busy
        self.send_button.setEnabled(not busy)
        self.send_button.setText("…" if busy else "Send")
        self.stop_button.setEnabled(busy)


class _InputBox(QtWidgets.QPlainTextEdit):
    """Plain-text input that submits on Enter (Shift+Enter inserts a newline)."""

    submitted = QtCore.Signal()

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and not (
            event.modifiers() & QtCore.Qt.ShiftModifier
        ):
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)
