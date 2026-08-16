# SPDX-License-Identifier: LGPL-2.1-or-later
"""Artifact folders: the per-conversation session dir, and what gets saved into it.

Everything a chat writes to disk lands under ``<artifacts_dir>/<session-id>/``
-- captures, exports, the run_python script archive, optional per-step .FCStd
snapshots, and (written by agent_worker, not here) the CLI's raw JSON stream.
That folder is also the CLI's working directory, so
``prepare_session_workspace`` copies the skills and the scripting references
into it.

Also the one spelling of the paths that aren't artifacts but that several
modules need to agree on: ``PARAM_PATH`` (the preferences root), ``REFS_DIR``
(the bundled scripting references) and ``ref_path`` (how they are cited).
"""

import json
import os
import tempfile


#: Default working-files folder: a "FreeCADClaude" subfolder of the user's home
#: (profile) directory, so captures/exports are easy to find -- not buried in
#: FreeCAD's hidden app-data dir. Override with the "ArtifactsDir" preference.
_DEFAULT_ARTIFACTS_DIR = os.path.join(os.path.expanduser("~"), "FreeCADClaude")

#: Root of every FreeCADClaude preference (ArtifactsDir/SaveSteps here,
#: Model/Effort/SkillsProjectDir in agent_config, which imports this). One
#: spelling: a typo'd copy would read a silently-empty branch of the parameter
#: tree, so every preference under it would just look unset.
PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/FreeCADClaude"

#: The bundled run_python scripting references (read-only assets, not artifacts).
#: This is where prepare_session_workspace copies them FROM; nothing shows this
#: path to Claude, which sees the copy under the session folder.
REFS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "references"
)

#: Where that copy lands in the session folder, and therefore how a reference is
#: cited: the CLI's cwd is the session folder, so a relative path is the same
#: string in every conversation -- which is what lets the system prompt, built
#: once per worker, name the same files as a tool note built mid-conversation.
#: Forward slash on every platform; the CLI resolves it.
REFS_REL = "references"


def ref_path(name):
    """A bundled scripting reference as Claude sees it: relative to the CLI's cwd.

    agent_config substitutes REFS_REL into the system prompt's {REFS_DIR} and the
    tools cite paths through here in their just-in-time notes, so the two cannot
    name the same file differently.
    """
    return REFS_REL + "/" + name


def artifacts_dir():
    """The browsable folder where captures/exports are written.

    Defaults to ``~/FreeCADClaude`` (captures/ and exports/ live beneath it).
    Override via the FreeCADClaude ``ArtifactsDir`` preference (an absolute path).
    """
    import FreeCAD

    configured = FreeCAD.ParamGet(PARAM_PATH).GetString("ArtifactsDir", "").strip()
    path = os.path.expanduser(configured) if configured else _DEFAULT_ARTIFACTS_DIR
    os.makedirs(path, exist_ok=True)
    return path


#: Filename of the bridge discovery file (see bridge_file() for why the path
#: is fixed rather than following ArtifactsDir).
BRIDGE_FILE_NAME = "bridge.json"


def bridge_file():
    """Absolute path to the bridge discovery file, always under the DEFAULT
    artifacts folder -- ``~/FreeCADClaude/bridge.json`` -- never under a
    user-configured ``ArtifactsDir``.

    The reader is mcp_server.py, a stdlib-only script running in a separate
    process spawned by an external MCP client (e.g. `claude mcp add`). It has
    no FreeCAD to ask for the ArtifactsDir preference, so a preference-relative
    location would be undiscoverable -- there would be nothing to tell it where
    to look. This path is spelled once here (the writer) and once, knowingly,
    in mcp_server.py (the reader); each spelling names the other in a comment.
    If you have moved ArtifactsDir, the discovery file still lands in
    ~/FreeCADClaude, not at your configured location.

    Pure: no directory creation, no preference read.
    """
    return os.path.join(_DEFAULT_ARTIFACTS_DIR, BRIDGE_FILE_NAME)


def write_bridge_file(port, token):
    """Publish the bridge's connection details for an external MCP client.

    The token is a credential -- run_python is arbitrary Python inside the
    FreeCAD process, so anyone who can read this file owns the user's session.
    ``tempfile.mkstemp`` creates the file mode 0600 before any secret is
    written to it (umask-independent), and ``os.replace`` is an atomic rename,
    so a reader never observes a partially-written file. Mirrors
    ``gcode_server._write_settings``. Returns the path written.
    """
    path = bridge_file()
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    import time as _time

    body = json.dumps({
        "version": 1,
        "host": "127.0.0.1",
        "port": int(port),
        "token": str(token),
        "pid": os.getpid(),
        "started": _time.time(),
    })
    fd, tmp = tempfile.mkstemp(prefix=".bridge-", suffix=".json", dir=folder)
    try:
        os.chmod(tmp, 0o600)  # belt-and-braces: mkstemp already creates 0600
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return path


def remove_bridge_file():
    """Best-effort removal of the bridge discovery file -- but only if it is
    ours (``pid`` matches this process).

    With two FreeCAD instances running, the second overwrites the first's
    file; without the pid check, whichever quits first would delete the
    survivor's file out from under it. The instance that lost the race stays
    running but undiscoverable until it restarts -- accepted, not fixed.
    Swallows every failure: this runs from ``aboutToQuit``, where there is
    nothing useful left to do about an error.
    """
    path = bridge_file()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("pid") == os.getpid():
            os.remove(path)
    except (OSError, ValueError):
        pass


def ensure_sketches_dir():
    """Absolute path to the lo-fi sketch folder (freecad-lofi-sketch), created
    up front so Write -- used directly by Claude, outside the MCP bridge --
    always has somewhere to write."""
    path = os.path.join(artifacts_dir(), "sketches")
    os.makedirs(path, exist_ok=True)
    return path


#: Folder name of the chat conversation currently being logged, set by
#: new_session_id() (called from chat_panel on the GUI thread when a chat
#: starts or "New" resets it).
_active_session = {"id": None}

#: Top-level folders under artifacts_dir() that are NOT per-session and must
#: be skipped by session-folder pruning.
_NON_SESSION_DIRS = {"sketches"}


def new_session_id():
    """Mint a fresh id for the current chat conversation and make it active.

    Everything logged for this conversation -- captures, run_python scripts,
    and the CLI's raw JSON stream -- lands under
    <artifacts_dir>/<session_id>/ (see session_dir). Prunes old session
    folders first so a long history of chats doesn't grow the folder forever.
    """
    import secrets
    import time

    _prune_session_dirs()
    session_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    _active_session["id"] = session_id
    return session_id


def active_session_id():
    """The active conversation's id, or None before new_session_id() has run.

    Lets a tool remember "already said this in this conversation" without keeping
    its own notion of when a conversation starts: "New" in the chat panel mints a
    fresh id, so anything keyed on this resets with it.
    """
    return _active_session["id"]


def session_dir():
    """Absolute path to the active chat conversation's log folder.

    Falls back to a shared "unsaved" folder if called before new_session_id()
    -- shouldn't happen via the bridge, which only runs during a live turn.
    """
    path = os.path.join(artifacts_dir(), _active_session["id"] or "unsaved")
    os.makedirs(path, exist_ok=True)
    return path


def prepare_session_workspace(skills_project=None):
    """Populate the active session folder with what the CLI reads from its cwd,
    and return the folder.

    The CLI runs each turn with this folder as its working directory, so the
    assets have to be inside it: the bundled scripting references as
    :data:`REFS_REL`, and the skills project's skills as ``.claude/skills/``
    (the only path the CLI discovers them on). Copies rather than links --
    a symlink needs privileges on Windows.

    Best effort, but the references are cited relative to this folder, so a
    failed copy leaves those citations pointing at nothing. Everything else the
    conversation writes here fails with it, so treat it as the folder being
    unusable rather than as a case to fall back for.
    """
    folder = session_dir()
    _copy_assets(REFS_DIR, os.path.join(folder, REFS_REL))
    if skills_project:
        _copy_assets(
            os.path.join(skills_project, ".claude", "skills"),
            os.path.join(folder, ".claude", "skills"),
        )
    return folder


def _copy_assets(src, dst):
    """Copy a read-only asset tree into the session folder (best effort)."""
    import shutil

    try:
        shutil.copytree(src, dst, dirs_exist_ok=True)
    except OSError:
        pass


def _prune_session_dirs(keep=40):
    """Keep only the most recent `keep` session folders (best effort)."""
    import shutil

    base = artifacts_dir()
    try:
        entries = [os.path.join(base, d) for d in os.listdir(base)]
    except OSError:
        return
    dirs = [d for d in entries
            if os.path.isdir(d) and os.path.basename(d) not in _NON_SESSION_DIRS]
    dirs.sort(key=os.path.getmtime, reverse=True)
    for old in dirs[keep:]:
        try:
            shutil.rmtree(old)
        except OSError:
            pass


def _safe_name(text, fallback):
    """`text` reduced to a filesystem-safe stem, or `fallback` if nothing survives.

    Every artifact name here is built from model-authored text (a run_python
    description, a document label), so it can hold anything -- path separators
    included.
    """
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in (text or "")) or fallback


def _session_subdir(name, keep=60):
    """<session_dir>/<name>/, created and pruned to its most recent `keep` files
    -- the opening move of every artifact write (captures, exports, scripts, steps)."""
    folder = os.path.join(session_dir(), name)
    os.makedirs(folder, exist_ok=True)
    _prune_folder(folder, keep=keep)
    return folder


def _artifact_path(subdir, base, suffix):
    """A unique, readably-named file under <session_dir>/<subdir>/."""
    folder = _session_subdir(subdir)
    fd, path = tempfile.mkstemp(prefix=_safe_name(base, "item") + "_", suffix=suffix, dir=folder)
    os.close(fd)
    return path


def _prune_folder(folder, keep):
    """Keep only the most recent `keep` files in a folder (best effort)."""
    try:
        files = [os.path.join(folder, f) for f in os.listdir(folder)]
        files = [f for f in files if os.path.isfile(f)]
        files.sort(key=os.path.getmtime, reverse=True)
        for old in files[keep:]:
            try:
                os.remove(old)
            except OSError:
                pass
    except OSError:
        pass


#: Parent of the per-job slice folders. One artifact subdir whose entries are
#: directories rather than files, which is why it needs its own pruner.
_JOBS_SUBDIR = "slices"


def _session_job_dir(name, keep=20):
    """A fresh ``<session_dir>/slices/<name>/`` for one slice job.

    A slice leaves a folder rather than a file -- the 3MF handed to the slicer,
    the G-code it wrote, its ``result.json``, its log and the job record -- so
    the artifact pruners above do not apply: ``_prune_folder`` filters on
    ``os.path.isfile`` and would skip every one of these. This keeps the most
    recent `keep` job folders instead.

    Uniquified when `name` is already taken, because the folder's own basename is
    the job id the caller quotes; two jobs sharing a folder would overwrite each
    other's G-code and leave one id naming both.
    """
    parent = os.path.join(session_dir(), _JOBS_SUBDIR)
    os.makedirs(parent, exist_ok=True)
    _prune_dirs(parent, keep=keep)
    folder = os.path.join(parent, _safe_name(name, "job"))
    suffix = 2
    while os.path.isdir(folder):
        folder = os.path.join(parent, f"{_safe_name(name, 'job')}-{suffix}")
        suffix += 1
    os.makedirs(folder, exist_ok=True)
    return folder


def _prune_dirs(folder, keep):
    """Keep only the most recent `keep` subdirectories of a folder (best effort)."""
    import shutil

    try:
        entries = [os.path.join(folder, f) for f in os.listdir(folder)]
        dirs = [d for d in entries if os.path.isdir(d)]
        dirs.sort(key=os.path.getmtime, reverse=True)
        for old in dirs[keep:]:
            try:
                shutil.rmtree(old)
            except OSError:
                pass
    except OSError:
        pass


def _save_run_python_script(code, description):
    """Archive a run_python call under <session_dir>/scripts/.

    Named "<HHMMSS>_<description>.py" -- just the time, not the date, so
    names stay short but a plain alphabetical directory listing still sorts
    chronologically. Mirrors the captures/exports artifact pattern (pruned to
    the most recent 60) so past runs stay browsable/diffable. Best effort --
    a write failure shouldn't block the actual code execution.
    """
    import time

    try:
        folder = _session_subdir("scripts")
        name = time.strftime("%H%M%S") + "_" + _safe_name(description, "run_python")
        path = os.path.join(folder, name + ".py")
        n = 2
        while os.path.exists(path):  # two runs in the same second
            path = os.path.join(folder, f"{name}-{n}.py")
            n += 1
        with open(path, "w", encoding="utf-8") as f:
            if description:
                f.write(f"# {description}\n")
            f.write(code)
    except OSError:
        pass


#: When on, _run_python saves a numbered .FCStd snapshot of the document after
#: every successful commit, under <session_dir>/steps/, so the model can be
#: opened at each step of a build. Off by default; the eval turns it on
#: (eval_runner), and interactive sessions can enable it via the "SaveSteps"
#: FreeCADClaude preference or the FREECADCLAUDE_SAVE_STEPS=1 env var.
_save_steps = {"on": os.environ.get("FREECADCLAUDE_SAVE_STEPS") == "1"}


def _save_steps_enabled():
    """Whether per-step .FCStd snapshots are on (in-process flag OR preference)."""
    if _save_steps["on"]:
        return True
    try:
        import FreeCAD

        return bool(FreeCAD.ParamGet(PARAM_PATH).GetBool("SaveSteps", False))
    except Exception:  # noqa: BLE001
        return False


def _save_step_snapshot(doc, description):
    """Save a numbered .FCStd snapshot of `doc` under <session_dir>/steps/.

    Uses doc.saveCopy so the document's own FileName / modified flag is left
    untouched -- an interactive user's real save location is never hijacked.
    Named "<NNN>_<description>.FCStd" (zero-padded so a plain listing sorts in
    build order); the number is max-existing + 1, staying monotonic even after
    pruning removes early steps. Best effort -- a save failure must not block the
    run_python result. Returns the path or None.
    """
    try:
        folder = _session_subdir("steps")
        n = 0
        for f in os.listdir(folder):
            head = f.split("_", 1)[0]
            if head.isdigit():
                n = max(n, int(head))
        path = os.path.join(folder, f"{n + 1:03d}_{_safe_name(description, 'step')}.FCStd")
        doc.saveCopy(path)
        return path
    except Exception:  # noqa: BLE001
        return None
