# SPDX-License-Identifier: LGPL-2.1-or-later
"""The dockable Claude chat panel.

The panel drives the ``claude`` CLI (via :class:`AgentWorker` on a worker
thread) and renders the conversation as a stack of collapsible entry widgets
(see :mod:`transcript_widgets`) -- one per user message, streamed Claude
reply, thinking block, tool-use note, etc. -- each rendered with
``QTextBrowser.setMarkdown`` so Claude's Markdown replies (headings, lists,
**bold**, ``code``, fenced blocks) format properly and inherit FreeCAD's
theme colours.

The panel is a singleton: one dock per FreeCAD session, created lazily and
re-shown on demand. The agent thread is started lazily on the first send.
"""

import html

import FreeCAD
import FreeCADGui

# FreeCAD bundles its own Qt binding under the ``PySide`` name. Always import
# from ``PySide`` so the addon matches the running FreeCAD.
from PySide import QtCore, QtWidgets

from . import _deps, transcript_widgets
from .agent_worker import AgentWorker

#: Show Claude's reasoning entry. Flip to False to hide it (the agent still
#: thinks; you just won't see it). The reasoning *text* is redacted by the CLI,
#: so in practice this surfaces a "💭 thought" marker with the note below rather
#: than the reasoning itself.
SHOW_THINKING = True

#: Body for a reasoning entry whose text the CLI withheld (the common case): the
#: "💭 thought" header records that Claude reasoned; this explains the empty body
#: when someone expands it.
_THINKING_HIDDEN_NOTE = "*Claude's reasoning isn't shown by the CLI.*"

#: Object name used both to register the dock and to find it again later.
DOCK_OBJECT_NAME = "FreeCADClaudeDock"

#: Slash commands that explicitly invoke a bundled skill, mapped to the
#: skill's name (its SKILL.md frontmatter `name`) and a one-line blurb for the
#: /help listing. Skills are explicit-invocation only (see agent_config's
#: SYSTEM_PROMPT and the skills' own descriptions) -- this is the only way a
#: user fires one.
_SKILL_COMMANDS = {
    "lofi-sketch": (
        "freecad-lofi-sketch",
        "Sketch a rough concept SVG (no dimensions) before planning the build",
    ),
    "design-advisor": (
        "freecad-design-advisor",
        "Plan the workbench(es) and feature sequence for a design idea",
    ),
    "run-python": (
        "freecad-run-python",
        "Write/debug the run_python code to build or fix the live document",
    ),
    "hollow-text": (
        "freecad-hollow-text",
        "Turn font text into hollow channel-letter lettering (e.g. LED signage)",
    ),
}

#: Cap how much of a tool's result text gets shown in its (collapsed-by-default)
#: transcript entry -- Claude still sees the full result either way, this is
#: purely a UI-rendering limit.
_MAX_TOOL_RESULT_CHARS = 4000


def _format_tool_input(inp):
    """Render a tool's input args as a Markdown fragment for its detail entry."""
    if not inp:
        return ""
    if "code" in inp:  # run_python -- show the actual code that ran
        parts = []
        if inp.get("description"):
            parts.append(inp["description"])
        parts.append("```python\n" + inp["code"] + "\n```")
        return "\n\n".join(parts)
    lines = [f"- **{k}**: {v}" for k, v in inp.items() if v not in (None, "")]
    return "\n".join(lines)


def _format_tool_result(text):
    if len(text) > _MAX_TOOL_RESULT_CHARS:
        text = text[:_MAX_TOOL_RESULT_CHARS] + "\n… (truncated)"
    return f"**Result:**\n\n```\n{text}\n```"


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
        self._live_entry = None       # the in-progress Claude entry being streamed
        self._live_think_entry = None  # the in-progress reasoning entry being streamed
        self._think_has_text = False   # did the current reasoning burst stream real text (vs. redacted)?
        self._tool_entries = {}       # tool_use_id -> CollapsibleSection, awaiting its result
        self._status_text = ""        # last worker status ("ready"/"closed"), shown when idle
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

        self.transcript_view = transcript_widgets.TranscriptView(self)
        layout.addWidget(self.transcript_view, stretch=1)

        self.input = _InputBox(self)
        self.input.setPlaceholderText(
            "Ask Claude…  (Enter to send, Shift+Enter for newline; /help for skills)"
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

        self._note('*Type a message to start a Claude session. '
                    'Try: "create a 20×40×10 box", or `/help` to see the available skills.*')

    # -- worker lifecycle ------------------------------------------------

    def _ensure_worker(self):
        """Start the agent thread on first use. Returns True if usable."""
        if self._worker is not None:
            return True

        ok, detail = _deps.cli_available()
        if not ok:
            self._note(_deps.INSTALL_HINT)
            self._note(f"*({detail})*")
            return False

        from . import agent_config, freecad_tools, gui_bridge

        try:
            port, token = gui_bridge.start()  # runs on the GUI thread
        except Exception as exc:  # noqa: BLE001
            self._note(f"*Could not start the FreeCAD tool bridge: {exc!r}*")
            return False

        # Mint this conversation's log folder BEFORE build_config, which reads
        # it (see freecad_tools.new_session_id / session_dir).
        freecad_tools.new_session_id()
        self._thread = QtCore.QThread(self)
        self._worker = AgentWorker(agent_config.build_config(detail, port, token))
        self._worker.moveToThread(self._thread)

        # Worker -> GUI (queued automatically across threads).
        self._worker.text_received.connect(self._on_text)
        if SHOW_THINKING:
            self._worker.thinking_received.connect(self._on_thinking)
            self._worker.thinking_started.connect(self._on_thinking_started)
        self._worker.tool_used.connect(self._on_tool)
        self._worker.tool_result.connect(self._on_tool_result)
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

        cli_text = text
        if text.startswith("/"):
            expanded = self._expand_slash_command(text)
            if expanded is None:
                self.input.clear()
                return  # handled locally (help, or an unknown command)
            text, cli_text = expanded

        if self._busy:
            self._note("*Still working on the previous message…*")
            return
        if not self._ensure_worker():
            return

        self.input.clear()
        self._add_entry("you", html.escape(text))
        self._set_busy(True)
        self._worker.submit(cli_text)

    def _expand_slash_command(self, text):
        """Parse a leading "/command rest..." input.

        Returns ``(display_text, cli_text)`` to send onward for a recognized
        skill command, or ``None`` if the command was handled locally (help,
        or an unknown command) and nothing should go to the CLI.
        """
        cmd, _, rest = text[1:].partition(" ")
        cmd = cmd.strip().lower()
        rest = rest.strip()

        if cmd in ("", "help", "skills"):
            lines = ["**Available skills** (explicit-invocation only):"]
            for name, (_, blurb) in _SKILL_COMMANDS.items():
                lines.append(f"- `/{name}` — {blurb}")
            self._note("\n".join(lines))
            return None

        if cmd not in _SKILL_COMMANDS:
            known = ", ".join(f"`/{name}`" for name in _SKILL_COMMANDS)
            self._note(f"*Unknown command `/{cmd}`. Available: {known}, `/help`.*")
            return None

        skill_name, _ = _SKILL_COMMANDS[cmd]
        instruction = f"Use the Skill tool to invoke the '{skill_name}' skill now"
        if cmd == "lofi-sketch":
            # freecad-lofi-sketch writes plain files with the Write tool, outside
            # the MCP bridge -- it has no way to resolve the (possibly
            # user-overridden) artifacts dir itself, so hand it the resolved path.
            from . import freecad_tools

            sketches_dir = freecad_tools.ensure_sketches_dir()
            instruction += f" (write concept SVGs under this exact absolute folder: {sketches_dir})"
        instruction += f", then use it to address this: {rest}" if rest else "."
        return text, instruction

    # -- worker signal handlers (run on the GUI thread) ------------------

    @QtCore.Slot(str)
    def _on_text(self, chunk):
        # We now know this phase produced no reasoning -- drop the empty
        # placeholder thinking entry instead of leaving it stranded above
        # the reply until the next commit.
        if self._live_think_entry is not None and not self._live_think_entry.raw_text.strip():
            self.transcript_view.remove_entry(self._live_think_entry)
            self._live_think_entry = None
        if self._live_entry is None:
            self._live_entry = self.transcript_view.start_live_entry("claude")
        self._live_entry.append_text(chunk)
        self._render_soon()

    @QtCore.Slot()
    def _on_thinking_started(self):
        # Claude reasoned this round but the CLI redacts the text. Surface a
        # muted "💭 thinking…" marker (committed as "💭 thought") so the turn
        # doesn't look stalled; the note gives the body something on expand.
        self._ensure_live_think_entry()
        if not self._think_has_text and not self._live_think_entry.raw_text.strip():
            self._live_think_entry.update_content_markdown(_THINKING_HIDDEN_NOTE)
            self._render_soon()

    @QtCore.Slot(str)
    def _on_thinking(self, chunk):
        self._ensure_live_think_entry()
        if not self._think_has_text:
            # Real reasoning text arrived (rare) -- drop the redacted-note
            # placeholder so it doesn't sit above the actual reasoning.
            self._think_has_text = True
            self._live_think_entry.update_content_markdown("")
        self._live_think_entry.append_text(chunk)
        self._render_soon()

    @QtCore.Slot(str, str, dict)
    def _on_tool(self, tool_id, label, tool_input):
        self._commit_live()
        entry = self._add_entry("tool", _format_tool_input(tool_input), collapsed=True, tool_name=label)
        if tool_id:
            self._tool_entries[tool_id] = entry

    @QtCore.Slot(str, str)
    def _on_tool_result(self, tool_id, result_text):
        entry = self._tool_entries.pop(tool_id, None)
        if entry is None:
            return
        body = entry.raw_text
        if body.strip():
            body += "\n\n---\n\n"
        entry.update_content_markdown(body + _format_tool_result(result_text))

    @QtCore.Slot()
    def _on_turn_finished(self):
        self._commit_live()
        self._set_busy(False)

    @QtCore.Slot(str)
    def _on_status(self, status):
        self._status_text = status
        if not self._busy:
            self.status_label.setText(status)

    @QtCore.Slot(str)
    def _on_failed(self, message):
        self._commit_live()
        self._add_entry("warning", message)
        self._set_busy(False)

    # -- transcript ------------------------------------------------------

    @property
    def _md(self):
        """Plain-Markdown reconstruction of the committed transcript, for eval_runner.py."""
        return self.transcript_view.to_markdown()

    def _note(self, text):
        return self._add_entry("note", text)

    def _add_entry(self, kind, text, **kw):
        return self.transcript_view.add_entry(kind, text, **kw)

    def _ensure_live_think_entry(self):
        """Lazily create the live thinking entry the moment real reasoning
        starts streaming in (called from _on_thinking only) -- unlike the old
        eager placeholder, the transcript never holds an entry with no
        content behind it. "Is Claude working?" is answered by the status
        label instead (see _set_busy)."""
        if not SHOW_THINKING:
            return
        if self._live_think_entry is None:
            self._live_think_entry = self.transcript_view.start_live_entry("thinking")

    def _commit_live(self):
        """Finalize the streamed reasoning + Claude entries into the transcript."""
        for attr in ("_live_think_entry", "_live_entry"):
            entry = getattr(self, attr)
            if entry is None:
                continue
            entry.flush()  # ensure the last (possibly still-throttled) chunk is shown
            entry.commit()
            if not entry.raw_text.strip():
                self.transcript_view.remove_entry(entry)
            setattr(self, attr, None)
        self._think_has_text = False  # next reasoning burst starts fresh

    def _render(self):
        """Render now (cancels any pending throttled render)."""
        self._render_timer.stop()
        self._do_render()

    def _render_soon(self):
        """Render within ~80ms, coalescing rapid streaming deltas."""
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _do_render(self):
        """Flush the live streamed entries."""
        self._render_timer.stop()
        if self._live_think_entry is not None:
            self._live_think_entry.flush()
        if self._live_entry is not None:
            self._live_entry.flush()

    def _on_new(self):
        """Start a fresh conversation: reset the agent's session and clear panels."""
        if self._worker is not None:
            if self._busy:
                self._worker.cancel()
            self._worker.reset_session()
            from . import freecad_tools

            freecad_tools.new_session_id()
            self._worker.set_log_dir(freecad_tools.session_dir())
        self.transcript_view.clear()
        self._live_entry = None
        self._live_think_entry = None
        self._think_has_text = False
        self._tool_entries.clear()
        self._set_busy(False)
        try:
            from . import plan_panel

            plan_panel.get_panel().widget.clear()
        except Exception:  # noqa: BLE001
            pass
        self._note("*New conversation started.*")

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
            self._note("*(stopped)*")

    def _set_busy(self, busy):
        self._busy = busy
        self.send_button.setEnabled(not busy)
        self.send_button.setText("…" if busy else "Send")
        self.stop_button.setEnabled(busy)
        if busy:
            color = transcript_widgets.CLAUDE_COLOR
            self.status_label.setText(f'<span style="color:{color}">Processing</span>')
        else:
            self.status_label.setText(self._status_text)


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
