# SPDX-License-Identifier: LGPL-2.1-or-later
"""Artifact folders: the per-conversation session dir, and what gets saved into it.

Everything a chat writes to disk lands under ``<artifacts_dir>/<session-id>/``
-- captures, exports, the run_python script archive, optional per-step .FCStd
snapshots, and (written by agent_worker, not here) the CLI's raw JSON stream.
"""

import os
import tempfile


#: Default working-files folder: a "FreeCADClaude" subfolder of the user's home
#: (profile) directory, so captures/exports are easy to find -- not buried in
#: FreeCAD's hidden app-data dir. Override with the "ArtifactsDir" preference.
_DEFAULT_ARTIFACTS_DIR = os.path.join(os.path.expanduser("~"), "FreeCADClaude")
_PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/FreeCADClaude"


def artifacts_dir():
    """The browsable folder where captures/exports are written.

    Defaults to ``~/FreeCADClaude`` (captures/ and exports/ live beneath it).
    Override via the FreeCADClaude ``ArtifactsDir`` preference (an absolute path).
    """
    import FreeCAD

    configured = FreeCAD.ParamGet(_PARAM_PATH).GetString("ArtifactsDir", "").strip()
    path = os.path.expanduser(configured) if configured else _DEFAULT_ARTIFACTS_DIR
    os.makedirs(path, exist_ok=True)
    return path


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


def session_dir():
    """Absolute path to the active chat conversation's log folder.

    Falls back to a shared "unsaved" folder if called before new_session_id()
    -- shouldn't happen via the bridge, which only runs during a live turn.
    """
    path = os.path.join(artifacts_dir(), _active_session["id"] or "unsaved")
    os.makedirs(path, exist_ok=True)
    return path


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


def _artifact_path(subdir, base, suffix):
    """A unique, readably-named file under <session_dir>/<subdir>/."""
    folder = os.path.join(session_dir(), subdir)
    os.makedirs(folder, exist_ok=True)
    _prune_folder(folder, keep=60)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in base) or "item"
    fd, path = tempfile.mkstemp(prefix=safe + "_", suffix=suffix, dir=folder)
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


def _save_run_python_script(code, description):
    """Archive an approved run_python call under <session_dir>/scripts/.

    Named "<HHMMSS>_<description>.py" -- just the time, not the date, so
    names stay short but a plain alphabetical directory listing still sorts
    chronologically. Mirrors the captures/exports artifact pattern (pruned to
    the most recent 60) so past runs stay browsable/diffable. Best effort --
    a write failure shouldn't block the actual code execution.
    """
    import time

    try:
        folder = os.path.join(session_dir(), "scripts")
        os.makedirs(folder, exist_ok=True)
        _prune_folder(folder, keep=60)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in description) or "run_python"
        name = time.strftime("%H%M%S") + "_" + safe
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

        return bool(FreeCAD.ParamGet(_PARAM_PATH).GetBool("SaveSteps", False))
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
        folder = os.path.join(session_dir(), "steps")
        os.makedirs(folder, exist_ok=True)
        _prune_folder(folder, keep=60)
        n = 0
        for f in os.listdir(folder):
            head = f.split("_", 1)[0]
            if head.isdigit():
                n = max(n, int(head))
        safe = "".join(c if c.isalnum() or c in "-_" else "_"
                       for c in (description or "")) or "step"
        path = os.path.join(folder, f"{n + 1:03d}_{safe}.FCStd")
        doc.saveCopy(path)
        return path
    except Exception:  # noqa: BLE001
        return None
