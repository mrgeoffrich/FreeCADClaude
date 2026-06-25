# SPDX-License-Identifier: LGPL-2.1-or-later
"""Drives the ``claude`` CLI as a subprocess and bridges to Qt via signals.

Each turn spawns ``claude -p ... --output-format stream-json`` with all tools
disabled (pure chat) and parses the newline-delimited JSON it emits. Crucially
we spawn it ourselves with CREATE_NO_WINDOW + piped stdio, so the console
window that the Agent SDK popped (and the hang that came with it) never occurs.

    GUI thread  --submit(text)-->  queue.Queue  -->  run() loop (worker thread)
    worker loop --Qt signals (auto-queued)-->  GUI thread (transcript update)

Conversation context is kept by the CLI itself: turn 1 starts a session, and
later turns pass ``--resume <session-id>``. The FreeCAD API is NOT touched
here -- that comes in milestone 3.
"""

import json
import queue
import subprocess
import tempfile

from PySide import QtCore

# Hide the child console window on Windows; 0 (no-op) elsewhere.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class AgentWorker(QtCore.QObject):
    """Runs the claude CLI per turn, streaming replies out as signals."""

    text_received = QtCore.Signal(str)      # assistant text
    tool_used = QtCore.Signal(str)          # name of a FreeCAD tool the agent invoked
    task_event = QtCore.Signal(dict)        # {op:create,num,subject} | {op:update,num,status}
    plan_received = QtCore.Signal(str)      # full text of a Plan subagent's output
    turn_finished = QtCore.Signal()         # current response complete
    status_changed = QtCore.Signal(str)     # "ready" / "closed"
    failed = QtCore.Signal(str)             # error message

    def __init__(self, config):
        super().__init__()
        # config: {cli_path, model, system, ...} built on the GUI thread.
        self._config = config
        self._queue = queue.Queue()
        self._session_id = None  # set from the first turn, reused via --resume
        self._pending_tasks = {}  # TaskCreate tool_use_id -> subject (awaiting its #)
        self._plan_ids = set()    # Agent(Plan) tool_use_ids (awaiting result text)

    # -- runs on the worker thread ---------------------------------------

    @QtCore.Slot()
    def run(self):
        self.status_changed.emit("ready")
        while True:
            text = self._queue.get()
            if text is None:  # shutdown sentinel
                break
            self._handle_prompt(text)
        self.status_changed.emit("closed")

    def _build_argv(self, text):
        cfg = self._config
        argv = [
            cfg["cli_path"],
            "-p", text,
            "--output-format", "stream-json",
            "--verbose",                 # required for stream-json in -p mode
            "--model", cfg["model"],
        ]
        # Built-in tools: a safe allowlist (Skill + read-only) when a skills
        # project is configured, otherwise none. Bash/Write/Edit stay off either
        # way -- the only mutation path is the gated run_python MCP tool.
        builtin = cfg.get("builtin_tools") or []
        if builtin:
            argv += ["--tools", *builtin]
        else:
            argv += ["--tools", ""]
        argv += [
            "--strict-mcp-config",       # ignore the user's own MCP servers
            "--mcp-config", cfg["mcp_config"],   # expose our FreeCAD tools
        ]
        if cfg["allowed_tools"]:
            # auto-approve our tools so -p mode never blocks on a permission prompt
            argv += ["--allowed-tools", " ".join(cfg["allowed_tools"])]
        if self._session_id:
            argv += ["--resume", self._session_id]
        else:
            argv += ["--append-system-prompt", cfg["system"]]
        return argv

    def _handle_prompt(self, text):
        argv = self._build_argv(text)
        emitted = False
        stray = []  # non-JSON lines (e.g. stderr merged in) -> error context
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # merge so a full stderr buffer can't deadlock us
                text=True,
                encoding="utf-8",
                errors="replace",
                # cwd = the skills project (so its .claude/skills load), else a
                # neutral temp dir that loads no project context.
                cwd=self._config.get("cwd") or tempfile.gettempdir(),
                creationflags=_NO_WINDOW,
            )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Could not launch claude CLI: {exc!r}")
            self.turn_finished.emit()
            return

        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    stray.append(line)
                    continue
                emitted = self._dispatch(obj) or emitted
            proc.wait()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Error reading claude output: {exc!r}")
        finally:
            if not emitted and proc.returncode not in (0, None):
                detail = "\n".join(stray[-5:]) or f"claude exited with code {proc.returncode}"
                self.failed.emit(detail)
            self.turn_finished.emit()

    def _dispatch(self, obj):
        """Handle one parsed JSON object. Returns True if it produced text."""
        kind = obj.get("type")
        if kind == "system":
            sid = obj.get("session_id")
            if sid:
                self._session_id = sid
            return False
        if kind == "assistant":
            produced = False
            for block in obj.get("message", {}).get("content", []):
                btype = block.get("type")
                if btype == "text" and block.get("text"):
                    self.text_received.emit(block["text"])
                    produced = True
                elif btype == "tool_use":
                    self._handle_tool_use(block)
            return produced
        if kind == "user":
            for block in obj.get("message", {}).get("content", []):
                if block.get("type") == "tool_result":
                    self._handle_tool_result(block)
            return False
        if kind == "result":
            if obj.get("is_error"):
                self.failed.emit(str(obj.get("result") or "claude reported an error"))
            return False
        return False

    def _handle_tool_use(self, block):
        name = block.get("name") or ""
        inp = block.get("input") or {}
        if name == "TaskCreate":
            self._pending_tasks[block.get("id")] = inp.get("subject") or inp.get("description") or "Task"
        elif name == "TaskUpdate":
            self.task_event.emit(
                {"op": "update", "num": str(inp.get("taskId")), "status": inp.get("status") or ""}
            )
        elif name == "Agent" and inp.get("subagent_type") == "Plan":
            if block.get("id"):
                self._plan_ids.add(block["id"])
        elif name.startswith("mcp__freecad__"):
            # Only surface our FreeCAD actions in the chat; built-ins stay quiet.
            self.tool_used.emit(name.replace("mcp__freecad__", ""))

    def _handle_tool_result(self, block):
        import re

        tid = block.get("tool_use_id")
        if tid in self._plan_ids:
            self._plan_ids.discard(tid)
            text = self._extract_text(block.get("content"))
            if text:
                self.plan_received.emit(text)
        elif tid in self._pending_tasks:
            subject = self._pending_tasks.pop(tid)
            content = self._extract_text(block.get("content"))
            match = re.search(r"#(\d+)", content or "")
            num = match.group(1) if match else str(len(self._pending_tasks) + 1)
            self.task_event.emit({"op": "create", "num": num, "subject": subject})

    @staticmethod
    def _extract_text(content):
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            return "\n".join(p for p in parts if p)
        return ""

    # -- callable from the GUI thread ------------------------------------

    def submit(self, text):
        self._queue.put(text)

    def shutdown(self):
        self._queue.put(None)
