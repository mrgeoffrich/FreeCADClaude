#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Error paths of send_to_device / read_device_image.

The cases a user actually hits -- the server isn't running, nothing has come
back yet, there is no document open -- and the one property they all share:
each returns a SENTENCE, not a traceback. A tool that raises reaches Claude as
``repr(exc)`` through gui_bridge, which is both unreadable and unactionable;
these say what happened and what to press.

Imported through ``freecadclaude`` rather than by path, which is the difference
from ``eval/test_device_server.py``: ``_run_send_to_device`` asks the *package's*
device_server whether it is running, so a module loaded a second way would have
its own separate state and the test would be asking the wrong object.

    "C:\\Program Files\\FreeCAD 1.1\\bin\\freecadcmd.exe" /abs/path/to/eval/test_device_tools.py
    python3 eval/test_device_tools.py     # skips the one case that needs FreeCAD

(freecadcmd needs an ABSOLUTE path -- given a relative one it silently runs
nothing and still exits 0.)

Exit: 0 = all passed, 1 = a failure.
"""

import os
import shutil
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# `freecadclaude`, not `freecad.freecadclaude`: FreeCAD owns the `freecad`
# namespace package in its own installation and a checkout can't shadow it.
sys.path.insert(0, os.path.join(_ROOT, "freecad"))

from freecadclaude import device_server  # noqa: E402
from freecadclaude.freecad_tools import tools_device  # noqa: E402

_failures = []


def check(name, condition, detail=""):
    status = "ok  " if condition else "FAIL"
    print(f"  {status} {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(name)


def _text(result):
    """A tool returns a string, or (text, png_path) when it has an image."""
    return result[0] if isinstance(result, tuple) else result


def _have_freecad():
    try:
        import FreeCAD  # noqa: F401
    except ImportError:
        return False
    return True


def main():
    print("device tool error paths")
    check("the server starts stopped", not device_server.is_running())

    # -- nothing running -------------------------------------------------
    # Deliberately NOT auto-started by a tool call: this binds a LAN interface,
    # so only a button press may turn it on -- which makes "press the button"
    # the only useful thing the message can say.
    sent = _text(tools_device._run_send_to_device({"objects": ["Box"]}))
    check("send_to_device with no server -> a message", isinstance(sent, str))
    check("...naming the button to press", "Device" in sent and "panel" in sent, sent)
    check("...and not a traceback", "Traceback" not in sent and "Error(" not in sent, sent)

    read = _text(tools_device._run_read_device_image({}))
    check("read_device_image with no server -> a message", isinstance(read, str))
    check("...which says the server is down too", "isn't running" in read, read)

    # -- running, but nothing has come back yet --------------------------
    uploads_dir = tempfile.mkdtemp(prefix="fcc-device-tools-")
    device_server.start(upload_dir=uploads_dir, idle_timeout=0)
    try:
        read = _text(tools_device._run_read_device_image({}))
        check("read_device_image before any upload -> a message",
              "Nothing has come back" in read, read)
        check("...and it says not to poll", "polling" in read, read)

        # -- no active document ------------------------------------------
        # freecadcmd opens none, which is the same state as a FreeCAD sitting on
        # the start page -- and the first thing send_to_device would trip over.
        if _have_freecad():
            sent = _text(tools_device._run_send_to_device({"objects": ["Box"]}))
            check("send_to_device with no document -> a message",
                  sent == "No active document.", sent)
        else:
            print("  skip no-active-document (needs freecadcmd)")

        index = _text(tools_device._run_read_device_image({"index": 4}))
        check("an index past the end -> a message, not an IndexError",
              "Nothing has come back" in index, index)
    finally:
        device_server.stop()
        shutil.rmtree(uploads_dir, ignore_errors=True)

    print()
    if _failures:
        print(f"FAIL: {len(_failures)} check(s) failed: {', '.join(_failures)}")
        return 1
    print("PASS")
    return 0


# No __main__ guard: freecadcmd *imports* the script under a module name taken
# from the filename, so a guarded body would silently never run there.
_status = main()
sys.stdout.flush()
sys.exit(_status)
