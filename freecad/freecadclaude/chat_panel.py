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
import os
import re

# FreeCAD bundles its own Qt binding under the ``PySide`` name. Always import
# from ``PySide`` so the addon matches the running FreeCAD.
from PySide import QtCore, QtGui, QtWidgets

from . import _deps, dock_panel, flow_layout, transcript_widgets
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
    "hollow-text": (
        "freecad-hollow-text",
        "Turn font text into hollow channel-letter lettering (e.g. LED signage)",
    ),
}

#: Cap how much of a tool's result text gets shown in its (collapsed-by-default)
#: transcript entry -- Claude still sees the full result either way, this is
#: purely a UI-rendering limit.
_MAX_TOOL_RESULT_CHARS = 4000

#: Opens every conversation (a fresh panel and the "New" button). run_python used
#: to raise a confirmation dialog per call, which told the user what was about to
#: happen and let them stop it; with that gone, this is the only place the addon
#: states its own reach. Shown per conversation rather than once ever, because the
#: risk is per conversation -- a new chat is where someone hands Claude a new
#: document.
_CAPABILITY_NOTICE = """### What Claude can do here

⚠️ **Claude acts on your document immediately — there is no approval prompt.**

- **Change the document** — it writes and runs Python inside FreeCAD, so it can
  add, edit or delete any object in whatever file you have open.
- **Reach your files** — that code is ordinary Python in the FreeCAD process, so
  it can read, write and delete files on this computer too, not just the model.
- **Look at your work** — screenshots, section views and exports of the document.

Each call is one undoable transaction (a failed one rolls itself back), and a copy
of every script is kept under `~/FreeCADClaude/`. **Save your work before a long
build**, and don't point it at a file you can't afford to lose."""


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


#: Capture tools whose result carries a PNG we surface as a thumbnail.
#: send_to_device is in here for a slightly different reason from the rest: the
#: image goes to a tablet, so without the thumbnail the transcript is the one
#: place the user can't see what Claude actually pushed at them.
_CAPTURE_TOOLS = {"capture_view", "capture_user_view", "crop_view", "cutaway",
                  "send_to_device"}
#: Pull the saved PNG path out of a capture tool's result text. Non-greedy so it
#: tolerates spaces in the home path and stops at the first ".png"; "." never
#: crosses the line, and the path always sits on one line.
_SAVED_PNG_RE = re.compile(r"saved to (.+?\.png)")


def _extract_capture_png(text):
    """Absolute path of the capture PNG named in `text`, if it exists on disk."""
    match = _SAVED_PNG_RE.search(text or "")
    if not match:
        return None
    path = match.group(1)
    return path if os.path.isfile(path) else None


#: Minutes of nothing connected before the device server stops itself, when the
#: ``DeviceIdleMinutes`` preference under PARAM_PATH is unset. The number itself
#: lives in device_server (``_DEFAULT_IDLE_TIMEOUT``, in seconds, with the
#: reasoning); this is only the unit the preference is written in, because
#: "1800" is not a thing anyone wants to type into a parameter editor. 0 turns
#: the auto-stop off.
_DEVICE_IDLE_PREF = "DeviceIdleMinutes"


def _device_idle_timeout():
    """Seconds of idleness before the device server stops itself, or None for
    its own default.

    Read HERE, on the GUI thread, and handed to ``start()`` as a number --
    device_server may not read a FreeCAD preference itself (that is the same
    invariant as the upload folder, and for the same reason). An unset integer
    preference reads back as 0, which is why "off" is spelled as a negative
    rather than as 0: the two are indistinguishable otherwise.
    """
    import FreeCAD

    from . import freecad_tools

    minutes = FreeCAD.ParamGet(freecad_tools.PARAM_PATH).GetInt(_DEVICE_IDLE_PREF, 0)
    return None if minutes == 0 else max(0, minutes) * 60


#: How the pairing QR is drawn. At 8px a module, the largest code this encoder
#: makes (version 6, 41 modules) is 392px square including the quiet zone --
#: big enough to scan from across a desk without the dialog needing to scroll.
_QR_MODULE_PX = 8
#: The spec's minimum quiet zone, and it is not decoration: a reader finds the
#: finder patterns by their light surround, so a code butted up against the
#: dialog's edge is a code that does not scan.
_QR_QUIET_MODULES = 4


def _qr_pixmap(url, module_px=_QR_MODULE_PX, quiet=_QR_QUIET_MODULES):
    """The QR code for `url` as a pixmap, or ``None`` if it cannot be encoded.

    ``None`` rather than an exception because the popup degrades to the URL as
    text perfectly well, and the one way this fails -- a payload over 134 bytes,
    which would take a hostname nobody has -- should not cost the user the
    pairing dialog entirely.
    """
    from . import qr

    try:
        matrix = qr.encode(url)
    except ValueError:
        return None

    n = len(matrix)
    side = (n + 2 * quiet) * module_px
    pixmap = QtGui.QPixmap(side, side)
    # Black on white explicitly, never the palette: under FreeCAD's dark theme a
    # themed code would come out light-on-dark, which readers are entitled to
    # refuse (and some do).
    pixmap.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(pixmap)
    try:
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("black"))
        for row in range(n):
            for col in range(n):
                if matrix[row][col]:
                    painter.drawRect(
                        (col + quiet) * module_px,
                        (row + quiet) * module_px,
                        module_px,
                        module_px,
                    )
    finally:
        # A QPixmap left with an active painter cannot be drawn, and the failure
        # is a warning on stderr rather than an exception -- so end() in a
        # finally, not after the loop.
        painter.end()
    return pixmap


def get_panel():
    """Return the singleton :class:`ChatPanel`, creating it on first use."""
    return ChatPanel.instance()


class ChatPanel(dock_panel.DockPanel):
    """Owns the QDockWidget and its inner chat widget."""

    OBJECT_NAME = DOCK_OBJECT_NAME
    TITLE = "Claude"

    def _make_widget(self, dock):
        return ChatWidget(dock)

    def show_dock(self):
        super().show_dock()
        self._dock.raise_()  # chat takes the front tab


class ChatWidget(QtWidgets.QWidget):
    """Transcript + input box + Send button, wired to the agent worker."""

    #: An image landed from the paired device, with its stored path.
    #:
    #: Emitted from device_server's HTTP worker thread, which is why it is a
    #: signal at all: the transcript is a Qt widget tree with GUI-thread
    #: affinity, and touching it from the server thread is the one thing that
    #: absolutely must not happen here. A cross-thread emit to a receiver on the
    #: GUI thread is queued by Qt automatically, so the slot runs there.
    device_upload_received = QtCore.Signal(str)

    #: The device server stopped itself after sitting idle, with the timeout it
    #: was given (seconds). Emitted from the watchdog thread -- same reason as
    #: above, and the same queued hop onto the GUI thread.
    device_idle_stopped = QtCore.Signal(float)

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
        self.device_upload_received.connect(self._on_device_upload)
        self.device_idle_stopped.connect(self._on_device_idle_stopped)
        self._device_quit_hooked = False
        self._build_ui()
        self._note(_CAPABILITY_NOTICE)

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

        # Status on the left, controls flush right. The controls live in a
        # FlowLayout so they wrap onto a second line in a narrow dock instead of
        # summing into a ~430px floor the dock can't be dragged below (a
        # QPushButton's own minimum is ~80px whatever its label reads).
        button_row = QtWidgets.QHBoxLayout()
        self.status_label = _StatusLabel(self)
        button_row.addWidget(self.status_label, stretch=1)

        controls = QtWidgets.QWidget(self)
        control_row = flow_layout.FlowLayout(controls, spacing=layout.spacing())
        from . import agent_config

        self.model_combo = QtWidgets.QComboBox(controls)
        self.model_combo.setToolTip(
            "Model for the conversation (locks once a chat starts; use New to change)"
        )
        for label, model_id in agent_config.MODELS:
            self.model_combo.addItem(label, model_id)  # model id stored as itemData
        idx = self.model_combo.findData(agent_config.get_model())
        self.model_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        control_row.addWidget(self.model_combo)
        self.effort_combo = QtWidgets.QComboBox(controls)
        self.effort_combo.setToolTip(
            "Reasoning effort: how long Claude thinks before acting "
            "(changeable mid-chat; applies from the next message)"
        )
        for label, effort_id in agent_config.EFFORTS:
            self.effort_combo.addItem(label, effort_id)  # effort id stored as itemData
        idx = self.effort_combo.findData(agent_config.get_effort())
        self.effort_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.effort_combo.currentIndexChanged.connect(self._on_effort_changed)
        control_row.addWidget(self.effort_combo)
        self.files_button = QtWidgets.QPushButton("Files", controls)
        self.files_button.setToolTip("Open the FreeCADClaude captures/exports folder")
        self.files_button.clicked.connect(self._open_artifacts)
        control_row.addWidget(self.files_button)
        self.device_button = QtWidgets.QPushButton("Device", controls)
        self.device_button.setToolTip(
            "Serve the annotation page to a phone or tablet on this network"
        )
        self.device_button.clicked.connect(self._on_device)
        control_row.addWidget(self.device_button)
        self.new_button = QtWidgets.QPushButton("New", controls)
        self.new_button.setToolTip("Start a new conversation (clears history and the agent's memory)")
        self.new_button.clicked.connect(self._on_new)
        control_row.addWidget(self.new_button)
        self.stop_button = QtWidgets.QPushButton("Stop", controls)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._on_stop)
        control_row.addWidget(self.stop_button)
        self.send_button = QtWidgets.QPushButton("Send", controls)
        self.send_button.setDefault(True)
        self.send_button.clicked.connect(self.on_send)
        control_row.addWidget(self.send_button)
        button_row.addWidget(controls)
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

        # First message of this conversation: apply the selected model and lock
        # the selector for the rest of the chat (the CLI ties a resumed session
        # to its original model, so it can't change mid-conversation). "New"
        # re-enables it. The combo's enabled state is our "chat has begun" flag.
        if self.model_combo.isEnabled():
            self._worker.set_model(self.model_combo.currentData())
            self.model_combo.setEnabled(False)

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

        # For a capture tool, show the rendered PNG as a click-to-open thumbnail
        # and auto-expand the (otherwise collapsed) entry so it's visible.
        if entry.tool_name in _CAPTURE_TOOLS:
            png = _extract_capture_png(result_text)
            if png:
                entry.set_image(png)
                entry.set_collapsed(False)

    @QtCore.Slot()
    def _on_turn_finished(self):
        self._commit_live()
        self._set_busy(False)

    @QtCore.Slot(str)
    def _on_status(self, status):
        self._status_text = status
        if not self._busy:
            self.status_label.set_status(status)

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
            from . import agent_config, freecad_tools

            freecad_tools.new_session_id()
            self._worker.set_log_dir(freecad_tools.session_dir())
            # The CLI's cwd is the session folder, so it moves with the session.
            self._worker.set_cwd(agent_config.session_workspace())
        self.model_combo.setEnabled(True)  # a fresh conversation can pick a model again
        self.transcript_view.clear()
        self._live_entry = None
        self._live_think_entry = None
        self._think_has_text = False
        self._tool_entries.clear()
        self._set_busy(False)
        # After the clear, not before: its only output is an error note, and a
        # note written above would be wiped by the very next line.
        self._reset_device_session()
        try:
            from . import plan_panel

            plan_panel.get_panel().widget.clear()
        except Exception:  # noqa: BLE001
            pass
        self._note(_CAPABILITY_NOTICE)

    def _reset_device_session(self):
        """Point the device server at the new conversation and forget the old
        one's images.

        Called from "New", AFTER the fresh session id has been minted, because
        the upload folder is derived from it: without this the tablet's next
        upload would land in the previous chat's ``mobile/`` folder, and
        ``read_device_image`` would answer the new conversation with the
        previous one's marked-up capture.

        Unconditional, even with the server stopped: the published/upload state
        deliberately outlives a stop (see device_server._Feed), so it is exactly
        the state that would leak.
        """
        from . import device_server, freecad_tools

        try:
            upload_dir = (
                freecad_tools.device_upload_dir() if device_server.is_running() else None
            )
            device_server.reset_session(upload_dir)
        except Exception as exc:  # noqa: BLE001 - "New" must clear the panel regardless
            self._note(f"*Could not reset the device server: {exc}*")

    def _on_model_changed(self, _index):
        """Persist the selected model (only reachable between conversations, since
        the combo is disabled for the duration of a chat)."""
        from . import agent_config

        agent_config.save_model(self.model_combo.currentData())

    def _on_effort_changed(self, _index):
        """Persist the selected effort and push it to a running worker. The CLI
        takes --effort on every invocation, so this is not locked for the
        conversation the way the model is."""
        from . import agent_config

        effort = self.effort_combo.currentData()
        agent_config.save_effort(effort)
        if self._worker is not None:
            self._worker.set_effort(effort)

    def _open_artifacts(self):
        """Open the FreeCADClaude captures/exports folder in the file manager."""
        from . import freecad_tools

        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(freecad_tools.artifacts_dir())
        )

    def _on_device(self):
        """Start the LAN device server and show the user how to reach it.

        Explicit, and only from here: the server binds a LAN interface, so
        nothing but a button press should ever be able to turn it on (see
        device_server's module docstring). ``start()`` is idempotent, so
        pressing this again just re-shows the pairing details.
        """
        from . import device_server, freecad_tools

        try:
            # The upload folder and the idle timeout are resolved HERE, on the
            # GUI thread, and handed over as plain values. The server must never
            # work either out itself -- both walks end in a FreeCAD preference
            # read, and an HTTP worker thread calling into FreeCAD is the
            # invariant the whole feature rests on.
            url, _token = device_server.start(
                upload_dir=freecad_tools.device_upload_dir(),
                idle_timeout=_device_idle_timeout(),
            )
        except Exception as exc:  # noqa: BLE001 - report, never raise into Qt
            self._note(f"*Could not start the device server: {exc}*")
            return
        # Bound signal emits, not methods: the hooks fire on the server's own
        # threads and Qt queues them onto ours (see device_upload_received).
        device_server.set_upload_hook(self.device_upload_received.emit)
        device_server.set_idle_hook(self.device_idle_stopped.emit)
        self._hook_device_shutdown()
        self._show_pairing(url)

    def _hook_device_shutdown(self):
        """Make sure quitting FreeCAD takes the LAN listener down with it.

        Connected on first start rather than in ``__init__`` because there is
        nothing to stop until then -- and guarded by a flag because pressing
        Device again would otherwise connect a second copy (Qt allows duplicate
        connections, and this would then stop an already-stopped server).

        This is separate from ``_shutdown_worker``'s connection on purpose: that
        one is made when a *chat* starts, and the server can be running with no
        conversation ever having been sent.
        """
        if self._device_quit_hooked:
            return
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown_device)
            self._device_quit_hooked = True

    def _shutdown_device(self):
        """Stop the server on the way out. No transcript note: the window this
        would be written into is already closing."""
        from . import device_server

        try:
            device_server.stop()
        except Exception:  # noqa: BLE001 - nothing useful to do while quitting
            pass

    def _on_device_idle_stopped(self, timeout):
        """The watchdog stopped the server. Say so, and say why.

        Runs on the GUI thread (queued). Without this the tablet simply goes
        dead, which from over there is indistinguishable from a wifi problem --
        and the fix (press Device again, re-scan) is not one anybody would guess
        at from the device.
        """
        minutes = max(1, int(round(float(timeout) / 60.0)))
        self._note(
            f"*Device server stopped after {minutes} idle minute(s) with nothing "
            "connected. Press **Device** to start it again -- it mints a new "
            "code, so the device has to re-scan.*"
        )

    def _on_device_upload(self, path):
        """An image arrived from the device -- say so in the transcript.

        Runs on the GUI thread (queued from the server thread). Deliberately
        just a note: auto-injecting "the user sent an image" into the next
        prompt is a separate, deferred decision, and until it's made the user
        seeing that it landed is what stops them wondering whether Send worked.
        """
        self._note(
            "📱 **image received** from the device.\n\n"
            f"`{path}`\n\nAsk Claude to look at it (it reads it with "
            "`read_device_image`)."
        )

    def _show_pairing(self, url):
        """Show the pairing QR for `url`, the URL as text, and a way to stop.

        The QR is the point: the URL carries a 22-character case-sensitive
        token, and typing that on a tablet once per session is the kind of
        friction that decides whether a feature gets used. The text under it is
        the fallback for when the camera won't cooperate (or when the device is
        this machine) -- selectable so it can be copied and messaged over.

        Modal, and deliberately: pairing is a few seconds of scanning, and a
        modeless window would go on floating over FreeCAD long after. Closing
        leaves the server running; "Stop server" is how you turn it off, and
        since :func:`device_server.start` mints a fresh token each time, that
        also deauthorises whatever device is already paired.
        """
        self._note(
            "📱 **Device server running.** Scan the code, or open this on your "
            f"phone or tablet (same wifi):\n\n`{url}`"
        )

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Device")
        layout = QtWidgets.QVBoxLayout(dialog)

        pixmap = _qr_pixmap(url)
        layout.addWidget(
            QtWidgets.QLabel(
                "Scan this with a phone or tablet on the same network:"
                if pixmap is not None
                else "Open this address on a phone or tablet on the same network:",
                dialog,
            )
        )
        if pixmap is not None:
            code = QtWidgets.QLabel(dialog)
            code.setPixmap(pixmap)
            code.setAlignment(QtCore.Qt.AlignCenter)
            layout.addWidget(code)

        address = QtWidgets.QLabel(url, dialog)
        address.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        address.setAlignment(QtCore.Qt.AlignCenter)
        address.setWordWrap(True)
        layout.addWidget(address)

        buttons = QtWidgets.QHBoxLayout()
        stop_button = QtWidgets.QPushButton("Stop server", dialog)
        stop_button.setToolTip(
            "Stop serving the page. The next start mints a new token, so this "
            "also revokes any device already paired."
        )
        stop_button.clicked.connect(lambda: self._stop_device(dialog))
        buttons.addWidget(stop_button)
        buttons.addStretch(1)
        close_button = QtWidgets.QPushButton("Close", dialog)
        close_button.setDefault(True)
        close_button.clicked.connect(dialog.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        dialog.exec()

    def _stop_device(self, dialog):
        """Stop the LAN server and close the pairing popup, whose code is now dead."""
        from . import device_server

        device_server.stop()
        self._note("*Device server stopped.*")
        dialog.accept()

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
            self.status_label.set_status(f'<span style="color:{color}">Processing</span>')
        else:
            self.status_label.set_status(self._status_text)


class _StatusLabel(QtWidgets.QLabel):
    """The worker-status text -- the one thing in the control row that gives up
    its width rather than claiming any.

    Horizontally ``Ignored``, so it adds nothing to the panel's minimum width
    (that floor is what lets the dock be dragged narrow at all -- see
    :mod:`flow_layout`) and so the controls beside it still reach the right
    edge. When the row does squeeze it below the width its text needs, it blanks
    itself instead of painting a clipped fragment ("re" for "ready", which reads
    as a bug); the status stays available as the tooltip, and a busy turn is
    also visible in the Send button's "…".
    """

    def __init__(self, parent=None):
        super().__init__("", parent)
        self.setStyleSheet("color:#888888")
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred
        )
        self._status = ""

    def set_status(self, text):
        """Set the status (plain or rich text). Shown only if it fits."""
        self._status = text
        self.setToolTip(_plain_text(text))
        self._sync()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync()

    def _sync(self):
        # Measure the *full* status, not what's currently displayed (sizeHint
        # would report the latter, so a blanked label would measure as empty
        # and never come back).
        fits = self.width() >= _text_width(self._status, self.font())
        self.setText(self._status if fits else "")


def _plain_text(markup):
    """`markup` -- which may be rich text, as the busy status is -- as plain text."""
    if not markup:
        return ""
    doc = QtGui.QTextDocument()
    doc.setHtml(markup)
    return doc.toPlainText()


def _text_width(markup, font):
    """Width `markup` needs on one line in `font` (rich text included)."""
    if not markup:
        return 0
    doc = QtGui.QTextDocument()
    doc.setDefaultFont(font)
    doc.setHtml(markup)
    return int(doc.idealWidth()) + 2


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
