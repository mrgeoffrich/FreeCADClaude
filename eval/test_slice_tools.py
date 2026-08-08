#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""The parts of slice_model / read_slice_result that can be checked without a
slicer: the report reader, the placement arithmetic, and the two refusals.

**No slicer is ever executed here, and none is discovered either.** Bambu Studio
and OrcaSlicer are GUI applications: a flag one of them does not accept opens a
modal dialog on the user's desktop. So ``_preferences`` is replaced with a
synthetic install whose "binary" is a shell stub named OrcaSlicer -- which
``build_argv`` refuses outright, so the refusal path is exercised with nothing
spawned at all. The artifacts folder is redirected into a temp directory for the
same class of reason: the real one holds the user's own sessions.

Four things are worth pinning here.

The report reader. Everything read_slice_result says on success comes out of the
slicer's own ``result.json`` and the G-code header, and neither is ours -- so the
reader is driven over a fixture and has to tolerate both spellings of the fields
that have two, and report a missing field as missing rather than as zero.

The placement arithmetic. The slicer arranges the plate, so every toolpath
coordinate is offset from the model. The offset is plate centre minus model
centre, in that order and matched to the right part: a sign error or an index
slip produces a plausible number pointing at the wrong place. The slicer's boxes
are also not dimensions -- a 16 mm cylinder came back at 17.2 -- which is why only
the centre is used and why a box whose spans contradict the sizes beside it is
refused rather than averaged.

The unwritable path. ``Mesh.export`` to a path it cannot write aborts the
process, so the guard has to run before the export and not around it.

``_resolve_export_objects``, lifted out of ``_run_export``, against an
independent copy of the block it replaced.

    /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd /abs/path/to/eval/test_slice_tools.py

(freecadcmd needs an ABSOLUTE path -- given a relative one it silently runs
nothing and still exits 0.)

The bounded nested-loop wait is not here: it needs a Qt event loop and a live
turn, so it is a manual check in real FreeCAD.

Exit: 0 = all passed, 1 = a failure.
"""

import json
import os
import shutil
import stat
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# `freecadclaude`, not `freecad.freecadclaude`: FreeCAD owns the `freecad`
# namespace package in its own installation and a checkout can't shadow it. The
# package (not a by-path load) because tools_slice reaches its sibling
# slicer_runner through a relative import.
sys.path.insert(0, os.path.join(_ROOT, "freecad"))

import FreeCAD  # noqa: E402

from freecadclaude import slicer_runner  # noqa: E402
from freecadclaude.freecad_tools import (  # noqa: E402
    session,
    tools_export,
    tools_slice,
)
from freecadclaude.freecad_tools.print_meta import NOT_SET  # noqa: E402
from freecadclaude.freecad_tools.visibility import _expand_containers  # noqa: E402

#: 3MF core spec namespace, as FreeCAD writes it into 3D/3dmodel.model.
_NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"

_failures = []


def check(name, condition, detail=""):
    status = "ok  " if condition else "FAIL"
    print(f"  {status} {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(name)


def _near(actual, expected, tol=0.01):
    return len(actual) == len(expected) and \
        all(abs(a - b) <= tol for a, b in zip(actual, expected))


# -- the recorded slicer reports --------------------------------------------
# Genuine Bambu Studio 02.08.01.55 output, kept verbatim: one plate of two
# parts, and one of the three rotated parts from the print-direction spike.
# Nothing here is reconstructed, which matters -- the shape of an object's
# bounding box and the order of the objects array are both things a plausible
# guess gets wrong, and both are load-bearing.

_FIXTURES = os.path.join(_ROOT, "eval", "fixtures")


def _fixture(name):
    with open(os.path.join(_FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


TWO_BOXES = _fixture("result_two_boxes.json")
ROTATED_THREE = _fixture("result_rotated_three.json")

#: NOT slicer output. The spellings the reader tolerates but no real file here
#: uses: a single plate written at the top level, the feature times as a list of
#: records, and a bounding box given as a min/max pair.
_OTHER_SPELLINGS = {
    "return_code": 0,
    "error_string": "Success.",
    "id": 2,
    "total_predication": 65.0,
    "feature_type_times": [{"name": "Outer wall", "time": 40.0},
                           {"name": "Travel", "time": 25.0}],
    "objects": [{"name": "Object_1",
                 "bbox": {"min": [90.0, 90.0, 0.0], "max": [110.0, 110.0, 8.0]}}],
    "filaments": [{"filament_id": "GFA00", "total_used_g": 24.7,
                   "main_used_g": 23.1}],
}

#: A Bambu G-code header. The thumbnail block that follows it in a real file is
#: what makes the header worth reading by a bounded prefix rather than a line at
#: a time, and the comments are the only thing read out of it.
_GCODE_HEADER = """; HEADER_BLOCK_START
; BambuStudio 02.08.01.55
; model printing time: 1h 2m 22s; total estimated time: 1h 5m 10s
; total layer number: 125
; total filament weight [g] : 24.70
; HEADER_BLOCK_END
; CONFIG_BLOCK_START
; layer_height = 0.2
; CONFIG_BLOCK_END
; FEATURE: Outer wall
G1 X100 Y100 E1
"""


# -- the synthetic slicer install -------------------------------------------
# Deliberately OrcaSlicer: its command line is unverified, so build_argv refuses
# to produce one for it. That makes this the one install that exercises the whole
# of slice_model without any possibility of a process being started.


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    return path


def _stub_binary(path):
    """A file that only has to exist and be executable. Nothing runs it: the
    refusal it is here to trigger happens while the command line is being
    built."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


MODEL = "Bambu Lab P2S"
MACHINE = "Bambu Lab P2S 0.4 nozzle"
PROCESS = "0.20mm Standard @BBL P2S"
FILAMENT = "Bambu PLA Basic @BBL P2S"


def _build_install(root):
    """An install whose presets all resolve, behind a binary that gets no command
    line."""
    app = os.path.join(root, "OrcaSlicer.app", "Contents")
    binary = _stub_binary(os.path.join(app, "MacOS", "OrcaSlicer"))
    system = os.path.join(app, "Resources", "profiles", "BBL")
    _write_json(os.path.join(system, "machine", MODEL + ".json"),
                {"type": "machine_model", "name": MODEL, "nozzle_diameter": "0.4"})
    _write_json(os.path.join(system, "machine", MACHINE + ".json"),
                {"type": "machine", "name": MACHINE, "printer_model": MODEL,
                 "printer_variant": "0.4", "nozzle_diameter": ["0.4"],
                 "default_print_profile": PROCESS,
                 "default_filament_profile": [FILAMENT]})
    _write_json(os.path.join(system, "process", PROCESS + ".json"),
                {"type": "process", "name": PROCESS,
                 "compatible_printers": [MACHINE]})
    _write_json(os.path.join(system, "filament", FILAMENT + ".json"),
                {"type": "filament", "name": FILAMENT,
                 "compatible_printers": [MACHINE]})
    conf = _write_json(os.path.join(root, "config", "BambuStudio", "BambuStudio.conf"),
                       {"models": [{"model": MODEL, "vendor": "BBL",
                                    "nozzle_diameter": "0.4"}],
                        "presets": {"machine": MACHINE, "process": PROCESS,
                                    "filaments": [FILAMENT]}})
    return {"binary": binary, "conf": conf}


def _fake_preferences(install):
    """What _preferences would return with SlicerPath and SlicerConfPath set."""
    return {"binary": install["binary"], "conf": install["conf"],
            "profile_dirs": [], "presets": {"machine": "", "process": "",
                                            "filament": ""},
            "nozzle": None, "arrange": True, "orient": True, "gcode_ui": ""}


# -- session_job_dir --------------------------------------------------------


def _check_job_dirs(temp_root):
    print("  -- _session_job_dir")
    parent = os.path.join(session.session_dir(), "slices")
    os.makedirs(parent, exist_ok=True)
    # Twelve jobs, oldest first, with mtimes far enough apart to order.
    now = time.time()
    for index in range(12):
        folder = os.path.join(parent, "job%02d" % index)
        os.makedirs(folder, exist_ok=True)
        os.utime(folder, (now - (12 - index) * 60, now - (12 - index) * 60))
    kept_file = os.path.join(parent, "notes.txt")
    with open(kept_file, "w", encoding="utf-8") as fh:
        fh.write("not a job")

    fresh = session._session_job_dir("103000_bracket", keep=5)
    remaining = sorted(name for name in os.listdir(parent)
                       if os.path.isdir(os.path.join(parent, name)))
    check("the new job folder exists", os.path.isdir(fresh), fresh)
    check("it sits under the session's slices/",
          os.path.dirname(fresh) == parent, fresh)
    check("only the newest `keep` older jobs survive",
          remaining == ["103000_bracket", "job07", "job08", "job09", "job10",
                        "job11"], remaining)
    # The half a reversed sort would pass: name the ones that must be GONE.
    check("...so the oldest are the ones removed",
          not any(name in remaining for name in ("job00", "job01", "job06")),
          remaining)
    check("a plain file in the folder is left alone", os.path.isfile(kept_file))

    again = session._session_job_dir("103000_bracket", keep=20)
    check("a name already taken gets a folder of its own", again != fresh,
          (fresh, again))
    check("...and that folder is real and empty",
          os.path.isdir(again) and not os.listdir(again), again)
    # The id a caller quotes is the folder's basename, so the two have to differ.
    check("...so the two jobs cannot share an id",
          os.path.basename(again) != os.path.basename(fresh),
          (os.path.basename(fresh), os.path.basename(again)))
    shutil.rmtree(parent, ignore_errors=True)


# -- _resolve_export_objects ------------------------------------------------


def _resolve_as_before(args, doc):
    """The block that used to sit inline in _run_export, copied verbatim.

    An independent copy rather than a call: what is under test is that the lifted
    function answers identically, and asserting it against itself would prove
    nothing.
    """
    from freecadclaude.freecad_tools.gui_state import _selected_objects

    names = args.get("names")
    objs = []
    if names:
        for n in names:
            obj = doc.getObject(n)
            if obj is None:
                return None, f"No object named '{n}'."
            objs.append(obj)
    else:
        objs = _selected_objects()
        if not objs:
            objs = [o for o in doc.Objects
                    if getattr(o, "Shape", None) is not None and not o.Shape.isNull()]
    objs = _expand_containers(objs)
    objs = [o for o in objs if getattr(o, "Shape", None) is not None]
    if not objs:
        return None, "No objects with a shape to export."
    return objs, None


def _check_resolution(doc, empty_doc):
    print("  -- _resolve_export_objects matches the block it replaced")
    cases = [
        ("no names at all", {}),
        ("one name", {"names": ["Bracket"]}),
        ("two names, in the order given", {"names": ["Cylinder", "Bracket"]}),
        ("a name that is not there", {"names": ["Nope"]}),
        ("a container", {"names": ["Assembly"]}),
        ("a name and a container", {"names": ["Bracket", "Assembly"]}),
    ]
    for label, args in cases:
        got, got_err = tools_export._resolve_export_objects(args, doc)
        want, want_err = _resolve_as_before(args, doc)
        names = None if got is None else [o.Name for o in got]
        wanted = None if want is None else [o.Name for o in want]
        check(f"{label}: same objects", names == wanted, (names, wanted))
        check(f"{label}: same error", got_err == want_err, (got_err, want_err))

    got, got_err = tools_export._resolve_export_objects({}, empty_doc)
    want, want_err = _resolve_as_before({}, empty_doc)
    check("an empty document: same refusal",
          got is None and want is None and got_err == want_err, (got_err, want_err))
    check("...and it is the sentence it always was",
          got_err == "No objects with a shape to export.", got_err)
    # The container has to actually expand, or the case above compares two
    # identical no-ops and proves nothing.
    expanded, _err = tools_export._resolve_export_objects({"names": ["Assembly"]}, doc)
    check("the container case really expands to its children",
          [o.Name for o in expanded] == ["Inner"], [o.Name for o in expanded])


def _check_export_still_works(doc, out_dir):
    print("  -- _run_export after the lift")
    step = os.path.join(out_dir, "parts.step")
    text = tools_export._run_export({"names": ["Bracket", "Cylinder"], "path": step})
    check("a .step export still succeeds", "STEP" in text and "2 object" in text, text)
    check("...and wrote the file", os.path.isfile(step) and os.path.getsize(step) > 0)

    three = os.path.join(out_dir, "parts.3mf")
    text = tools_export._run_export({"names": ["Bracket", "Cylinder"], "path": three})
    check("a .3mf export still succeeds", "3MF" in text, text)
    with zipfile.ZipFile(three) as archive:
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
    check("...with both objects kept separate",
          len(list(root.iter(_NS + "object"))) == 2
          and len(list(root.iter(_NS + "item"))) == 2,
          len(list(root.iter(_NS + "object"))))

    missing = tools_export._run_export({"names": ["Nope"], "path": step})
    check("a bad name is still a sentence", missing == "No object named 'Nope'.",
          missing)


# -- the unwritable path ----------------------------------------------------


def _check_write_guard(temp_root):
    print("  -- the write guard that has to run BEFORE Mesh.export")
    good = os.path.join(temp_root, "writable")
    os.makedirs(good, exist_ok=True)
    target = os.path.join(good, "model.3mf")
    check("a writable target passes", tools_slice._writable_target(target) is None)
    check("...leaving no probe file behind", not os.path.exists(target))

    nowhere = os.path.join(temp_root, "does", "not", "exist", "model.3mf")
    message = tools_slice._writable_target(nowhere)
    check("a path with no folder is refused", isinstance(message, str) and message)
    check("...saying nothing was exported or sliced",
          "Nothing was exported or sliced" in (message or ""), message)
    check("...and naming the path", nowhere in (message or ""), message)

    locked = os.path.join(temp_root, "locked")
    os.makedirs(locked, exist_ok=True)
    os.chmod(locked, stat.S_IRUSR | stat.S_IXUSR)
    try:
        blocked = tools_slice._writable_target(os.path.join(locked, "model.3mf"))
        if blocked is None:
            print("       (skipped: this filesystem let a read-only folder be "
                  "written to)")
        else:
            check("an unwritable folder is refused", isinstance(blocked, str))
            check("...before anything is exported",
                  "Nothing was exported or sliced" in blocked, blocked)
    finally:
        os.chmod(locked, stat.S_IRWXU)


# -- the refusal for a slicer with no known command line --------------------


def _check_recorded_boxes(doc):
    print("  -- the world boxes recorded before anything moves")
    boxes = tools_slice._recorded_boxes([doc.getObject("Bracket"),
                                        doc.getObject("Offset")])
    check("one entry per object, keyed by internal Name",
          sorted(boxes) == ["Bracket", "Offset"], sorted(boxes))
    # A 20x30x10 box at the origin: the centre is the middle of it, not a corner.
    # Everything the placement offset says rests on this being the centre.
    check("the recorded centre is the middle of the box",
          _near(boxes["Bracket"]["centre"], [10.0, 15.0, 5.0]),
          boxes["Bracket"]["centre"])
    check("...and the size its lengths", _near(boxes["Bracket"]["size"],
                                              [20.0, 30.0, 10.0]),
          boxes["Bracket"]["size"])
    # Placed away from the origin, so a corner and a centre cannot coincide and
    # the two cannot be confused for each other.
    check("a part placed away from the origin records its own centre",
          _near(boxes["Offset"]["centre"], [105.0, -47.0, 3.0]),
          boxes["Offset"]["centre"])
    check("...with the world extents alongside it",
          boxes["Offset"]["extents"]["x_min"] == 100.0
          and boxes["Offset"]["extents"]["x_max"] == 110.0,
          boxes["Offset"]["extents"])
    shapeless = tools_slice._recorded_boxes([doc.getObject("Notes")])
    check("an object with no shape is left out entirely", shapeless == {}, shapeless)


def _check_plate_lines():
    print("  -- what slice_model says went on the plate")
    report = {"deviation": 0.1, "exported": _EXPORTED_THREE, "omitted": ["Jig"],
              "not_set": ["Undecided"],
              "skipped": [{"name": "Broken", "reason": "meshing failed"}]}
    lines = "\n".join(tools_slice._plate_lines(report))
    check("each part is listed with the way up it went",
          "Overhang (+Z up) -- as modelled" in lines
          and "Bar (+X up) -- rotated onto its print direction" in lines, lines)
    check("...and its exported size", "10x10x60 mm" in lines, lines)
    check("a Not printed part is named as left off the plate",
          "Left off the plate as Not printed: Jig." in lines, lines)
    check("an undecided part is named rather than passed over",
          "Undecided" in lines and "Nobody has decided" in lines, lines)
    check("...quoting the enum value it actually carries", f"'{NOT_SET}'" in lines,
          lines)
    check("a part that could not be meshed is named too",
          "Skipped Broken: meshing failed." in lines, lines)
    quiet = "\n".join(tools_slice._plate_lines(
        {"deviation": 0.1, "exported": _EXPORTED_THREE[:1], "omitted": [],
         "not_set": [], "skipped": []}))
    check("with nothing to warn about, nothing is warned about",
          "Left off" not in quiet and "Nobody" not in quiet, quiet)


def _check_no_presets(doc, install):
    print("  -- nothing to slice with")
    # The config is stubbed away rather than left empty. An empty preference means
    # "discover", and discovery would find the real slicer's own config on this
    # machine -- the presets would then resolve against the user's own installed
    # ones and this would be testing the wrong thing entirely.
    prefs = dict(_fake_preferences(install), conf="")
    real = tools_slice._preferences
    real_conf = slicer_runner.discover_conf_path
    tools_slice._preferences = lambda: prefs
    slicer_runner.discover_conf_path = lambda candidates=None: None
    try:
        text = tools_slice._run_slice_model({"names": ["Bracket"]})
    finally:
        tools_slice._preferences = real
        slicer_runner.discover_conf_path = real_conf

    check("it refuses rather than slicing with a guess",
          "no machine, process, filament preset could be resolved" in text, text)
    check("...naming the slicer it did find", install["binary"] in text, text)
    check("...saying its config could not be read",
          "config file was not found" in text, text)
    check("...and listing what IS installed, so the fix needs no second call",
          MACHINE in text and PROCESS in text and FILAMENT in text, text)
    check("...plus the preferences to set",
          "SlicerMachine" in text and "SlicerProcess" in text
          and "SlicerFilament" in text, text)
    check("no job was created", slicer_runner.latest_job() is None)
    # The refusal arrives before the export, which is the point of resolving the
    # presets first: there is no mesh to spend on a plate nothing can slice.
    jobs = os.path.join(session.session_dir(), "slices")
    check("...and nothing was exported", not os.path.isdir(jobs)
          or not [name for base, _dirs, files in os.walk(jobs) for name in files],
          jobs)
    shutil.rmtree(jobs, ignore_errors=True)


def _check_stale_settings(doc, install):
    """A preset the settings page stored that nothing installed answers to.

    ``resolve_presets`` refuses rather than substituting something that would
    slice, so this arrives as the same no-preset refusal -- but the fix is on
    the settings page and not in a FreeCAD preference, and the refusal has to
    say so or the reader goes looking in the wrong place.
    """
    print("  -- a stored preset name that no longer exists")
    settings = tools_slice._settings_path()
    gone = "Bambu Lab X9 0.4 nozzle"
    prefs = dict(_fake_preferences(install), conf="")
    real = tools_slice._preferences
    real_conf = slicer_runner.discover_conf_path
    tools_slice._preferences = lambda: prefs
    slicer_runner.discover_conf_path = lambda candidates=None: None
    _write_json(settings, {"machine": gone})
    try:
        text = tools_slice._run_slice_model({"names": ["Bracket"]})
    finally:
        tools_slice._preferences = real
        slicer_runner.discover_conf_path = real_conf
        os.remove(settings)

    check("it refuses rather than slicing with something else",
          "Nothing was sliced" in text, text)
    check("...naming the stored preset that is gone", gone in text, text)
    check("...and the file holding it", settings in text, text)
    check("...pointing at the settings page, not only at a preference",
          "Slicer button" in text and "view_gcode" in text, text)
    check("no job was created", slicer_runner.latest_job() is None)
    shutil.rmtree(os.path.join(session.session_dir(), "slices"), ignore_errors=True)


def _check_settings_page(install, temp_root):
    """The page the chat panel's Slicer button opens, and its two refusals.

    The browser launch is stubbed. Everything else is real, including the
    listener, which is stopped again -- the point of the check is that the
    failures come back as sentences rather than as exceptions into Qt, since
    this one is called from a button and not through the bridge.
    """
    print("  -- the settings page, and what it says when it cannot open")
    from freecadclaude import gcode_server

    opened = []
    real_prefs = tools_slice._preferences
    real_open = tools_slice._open_in_browser
    real_server = gcode_server._Server
    tools_slice._open_in_browser = lambda url: (opened.append(url), "Opened it.")[1]

    def _will_not_bind(*_args, **_kwargs):
        raise OSError(48, "Address already in use")

    def _open_page():
        """``open_settings_page()``, with a raise reported rather than thrown.

        A button handler is where an escaping exception costs most, so the
        difference between "returned a sentence" and "raised" is the check being
        made -- and a raise here would otherwise end the run instead of failing
        one line of it.
        """
        try:
            return tools_slice.open_settings_page()
        except Exception as exc:  # noqa: BLE001
            return None, repr(exc)

    missing = os.path.join(temp_root, "never-built")
    try:
        tools_slice._preferences = lambda: dict(_fake_preferences(install),
                                                gcode_ui=missing)
        url, note = _open_page()
        check("a viewer directory that is not there refuses in a sentence",
              url is None and "could not be started" in note
              and "GcodeUiDir" in note, (url, note))
        check("...and no browser was opened", opened == [], opened)
        check("...and nothing is listening", not gcode_server.is_running())
        # view_gcode reaches the same failure through the same call, so it has
        # to read the same way: the tool and the button share one sentence.
        text = tools_slice._run_view_gcode({})
        check("view_gcode says the same thing rather than raising",
              isinstance(text, str) and "could not be started" in text
              and "GcodeUiDir" in text, text)
        check("...and still opened nothing", opened == [], opened)

        tools_slice._preferences = lambda: _fake_preferences(install)
        gcode_server._Server = _will_not_bind
        url, note = _open_page()
        check("a listener that will not bind refuses in a sentence too",
              url is None and "could not be started" in note
              and "Nothing is listening" in note, (url, note))
        gcode_server._Server = real_server

        url, note = _open_page()
        check("with the viewer built it serves the page",
              url is not None and url.startswith("http://127.0.0.1:"), (url, note))
        check("...carrying the token, so the page authenticates",
              "?t=" in (url or ""), url)
        check("...and no job, since this is the configuration way in",
              "gcode=" not in (url or ""), url)
        check("...and the browser was opened on exactly that URL",
              opened == [url], opened)
    finally:
        tools_slice._preferences = real_prefs
        tools_slice._open_in_browser = real_open
        gcode_server._Server = real_server
        gcode_server.stop()
        gcode_server._config["ui_dir"] = None


def _check_unknown_slicer(doc, install, temp_root):
    print("  -- a slicer whose command line is not known")
    real = tools_slice._preferences
    tools_slice._preferences = lambda: _fake_preferences(install)
    try:
        text = tools_slice._run_slice_model({"names": ["Bracket"],
                                            "note": "refusal probe"})
    finally:
        tools_slice._preferences = real

    check("it comes back as text", isinstance(text, str), type(text))
    check("...not a traceback",
          "Traceback" not in text and "UnknownSlicer(" not in text, text)
    check("...naming the binary", install["binary"] in text, text)
    check("...and the preference to point somewhere else",
          "SlicerPath" in text, text)
    check("...and saying nothing was sliced", "Nothing was sliced" in text, text)
    check("no job was created", slicer_runner.latest_job() is None,
          slicer_runner.latest_job())
    # The refusal happens while the command line is built, so the plate was
    # already written. That is stated rather than assumed: the 3MF is still
    # useful, and a reader of this test should know the order.
    jobs = os.path.join(session.session_dir(), "slices")
    wrote = [os.path.join(base, name)
             for base, _dirs, files in os.walk(jobs) for name in files
             if name == "model.3mf"]
    check("the exported plate is still on disk", len(wrote) == 1, wrote)
    shutil.rmtree(jobs, ignore_errors=True)


# -- the result.json reader -------------------------------------------------


def _check_reader(temp_root):
    print("  -- reading the slicer's own result.json")
    plates = tools_slice._plates(TWO_BOXES)
    check("the plate list is found", len(plates) == 1, plates)
    # The plate's own id, not the top level's plate_index -- that is 0, the
    # --slice argument, meaning every plate.
    check("...and its number read off the plate, not off --slice 0",
          tools_slice._plate_index(plates[0], 9) == 1
          and TWO_BOXES["plate_index"] == 0)
    other = tools_slice._plates(_OTHER_SPELLINGS)
    check("a single plate written at the top level is found too", len(other) == 1,
          other)
    other = other or [{}]
    check("...with its own number", tools_slice._plate_index(other[0], 9) == 2)
    check("a plate's own id is what numbers it",
          tools_slice._plate_index({"id": 7}, 1) == 7)
    check("a plate with no number falls back to its position",
          tools_slice._plate_index({}, 3) == 3)
    check("a result with no plates at all reads as none",
          tools_slice._plates({"return_code": 0}) == []
          and tools_slice._plates(None) == [])

    times = tools_slice._feature_times(plates[0])
    check("the feature times come back longest first",
          [name for name, _s in times[:3]] ==
          ["Sparse infill", "Outer wall", "Inner wall"], times[:3])
    check("...all of them", len(times) == 11, len(times))
    other_times = tools_slice._feature_times(other[0])
    check("...from the list spelling as well as the mapping",
          other_times == [("Outer wall", 40.0), ("Travel", 25.0)], other_times)
    check("a feature time that is not a number is dropped",
          tools_slice._feature_times({"feature_type_times": {"Bogus": "soon",
                                                            "Travel": 5}}) ==
          [("Travel", 5.0)])

    check("the print estimate is the slicer's own prediction",
          round(tools_slice._print_seconds(plates[0]), 1) == 242.1,
          tools_slice._print_seconds(plates[0]))
    check("...and is None when it reported none",
          tools_slice._print_seconds({}) is None)

    usage = tools_slice._filament_usage(other[0])
    check("filament usage is read", usage == [{"id": "GFA00", "total_g": 24.7,
                                              "main_g": 23.1}], usage)
    # Real, and the known gap: a bare system filament preset resolves no mass, so
    # every fixture here reports 0.0 g on a slice that otherwise succeeded.
    unresolved = tools_slice._filament_usage(plates[0])
    check("a filament with no mass is kept, not dropped",
          len(unresolved) == 1 and not unresolved[0]["total_g"], unresolved)

    check("seconds read as a print time",
          (tools_slice._format_seconds(3742), tools_slice._format_seconds(125),
           tools_slice._format_seconds(9)) == ("1h 2m", "2m 5s", "9s"))

    summary = "\n".join(tools_slice._summarise_plate(plates[0], 1))
    check("the plate summary states the estimate", "4m 2s" in summary, summary)
    check("...where the time goes, biggest share first",
          "Sparse infill" in summary
          and summary.index("Sparse infill") < summary.index("Outer wall"), summary)
    check("...with a percentage", "%" in summary, summary)
    check("...cut short rather than listing all eleven",
          "and 3 smaller" in summary and "Custom" not in summary, summary)
    check("a plate with no filament mass says so rather than printing 0 g",
          "no mass" in summary and "0 g" not in summary, summary)
    resolved_summary = "\n".join(tools_slice._summarise_plate(other[0], 1))
    check("...and a resolved mass is quoted", "24.7 g" in resolved_summary,
          resolved_summary)
    warned = "\n".join(tools_slice._summarise_plate(
        dict(plates[0], warning_message="Some objects are too close"), 1))
    check("a slicer warning is surfaced",
          "Some objects are too close" in warned, warned)

    # The settings the slice actually ran with, off the top level rather than
    # off a preset name.
    settings = tools_slice._settings_line(TWO_BOXES)
    check("the layer height is reported, rounded off its float noise",
          "0.2 mm layers" in settings, settings)
    check("...with the wall count and the infill density",
          "2 wall loops" in settings and "20% sparse infill" in settings, settings)
    check("nothing to report gives no line",
          tools_slice._settings_line({}) is None)

    gcode = os.path.join(temp_root, "plate_1.gcode")
    with open(gcode, "w", encoding="utf-8") as fh:
        fh.write(_GCODE_HEADER)
    layers, printing, total = tools_slice._header_facts(gcode)
    check("the layer count comes off the G-code header", layers == 125, layers)
    check("...along with the printing time", printing == "1h 2m 22s", printing)
    check("...and the total estimate", total == "1h 5m 10s", total)
    bare = os.path.join(temp_root, "bare.gcode")
    with open(bare, "w", encoding="utf-8") as fh:
        fh.write("G1 X1 Y1\n")
    check("a G-code with no header reports nothing rather than guessing",
          tools_slice._header_facts(bare) == (None, None, None),
          tools_slice._header_facts(bare))
    check("a G-code that is not there either",
          tools_slice._header_facts(os.path.join(temp_root, "nope.gcode")) ==
          (None, None, None))


# -- the placement arithmetic ----------------------------------------------


def _check_boxes():
    print("  -- reading a slicer bounding box")
    # The real shape: a minimum corner and a size in one dict. Read as a centre
    # it would put a 60 mm part 30 mm below the plate, since z is 0.0 on
    # everything the export dropped onto it.
    box = ROTATED_THREE["sliced_plates"][0]["objects"][0]["bbox"]
    centre, size = tools_slice._box_geometry(box)
    check("x/y/z is the minimum corner, so the centre is half a size along",
          _near(centre, [108.975, 106.0, 30.0]), centre)
    check("...and the size comes back as the size", _near(size, [10.0, 10.0, 60.0]),
          size)
    check("the corner really is at z=0, which is what says it is a corner",
          box["z"] == 0.0 and box["height"] == 60.0, box)

    centre, size = tools_slice._box_geometry({"min": [0.0, 0.0, 0.0],
                                             "max": [10.0, 20.0, 30.0]})
    check("a min/max pair is read too", _near(centre, [5.0, 10.0, 15.0]), centre)
    check("...with its size", _near(size, [10.0, 20.0, 30.0]), size)
    # A flat list is deliberately NOT read: min-then-max and axis-pair-by-axis-
    # pair are both plausible orderings and half the readings are then wrong.
    for bogus in (None, "0 0 0", [1, 2, 3], {}, [118.0, 113.0, 0.0, 138.0, 143.0, 10.0],
                  {"width": 10.0, "depth": 10.0}, {"x": 1.0, "y": 2.0, "z": 3.0}):
        check(f"{bogus!r} is not a box this reads",
              tools_slice._box_geometry(bogus) == (None, None),
              tools_slice._box_geometry(bogus))

    check("a size matching the part it was matched to agrees",
          tools_slice._sizes_agree([10.0, 10.0, 60.0], [10.0, 10.0, 60.0]) is True)
    check("...whatever order the axes come in",
          tools_slice._sizes_agree([60.0, 10.0, 10.0], [10.0, 10.0, 60.0]) is True)
    # The real inflation: a 16 mm cylinder reported at 17.206, about 8 percent,
    # or about 1.2 mm of absolute margin. The tolerance has to swallow that and
    # still separate a 10 mm cube from a 60 mm bar.
    check("an inflated curved part still agrees",
          tools_slice._sizes_agree([17.206, 17.206, 30.0], [16.0, 16.0, 30.0]) is True)
    check("a different part does not",
          tools_slice._sizes_agree([10.0, 10.0, 60.0], [10.0, 10.0, 10.0]) is False)
    check("nothing to compare reads as neither",
          tools_slice._sizes_agree([], [10.0, 10.0, 10.0]) is None)


#: The three parts of the rotated fixture, as oriented_export reported them --
#: file order, which is the order the slicer's Object_N names count in.
_EXPORTED_THREE = [
    {"name": "Overhang", "direction": "+Z up", "rotated": False,
     "size_mm": [10.0, 10.0, 10.0]},
    {"name": "Bore", "direction": "-Z up", "rotated": True,
     "size_mm": [16.0, 16.0, 30.0]},
    {"name": "Bar", "direction": "+X up", "rotated": True,
     "size_mm": [10.0, 10.0, 60.0]},
]

_RECORDED_THREE = {
    "Overhang": {"centre": [5.0, 5.0, 5.0], "size": [10.0, 10.0, 10.0]},
    "Bore": {"centre": [0.0, 0.0, 15.0], "size": [16.0, 16.0, 30.0]},
    "Bar": {"centre": [30.0, 5.0, 5.0], "size": [60.0, 10.0, 10.0]},
}

#: The two-part fixture's own parts. The export report that went with it was not
#: kept, so these are reconstructed from the slicer's sizes less its inflation
#: (about 1.2 mm on a curved footprint, the same margin the 16 mm cylinder shows).
_EXPORTED_TWO = [
    {"name": "Plate", "direction": "+Z up", "rotated": False,
     "size_mm": [10.0, 20.0, 5.0]},
    {"name": "Post", "direction": "+Z up", "rotated": False,
     "size_mm": [10.0, 10.0, 8.0]},
]

_RECORDED_TWO = {
    "Plate": {"centre": [5.0, 10.0, 2.5], "size": [10.0, 20.0, 5.0]},
    "Post": {"centre": [0.0, 0.0, 4.0], "size": [10.0, 10.0, 8.0]},
}


def _check_offsets():
    print("  -- where the slicer put each part, on the real plates")
    plate = TWO_BOXES["sliced_plates"][0]
    offsets = tools_slice._placement_offsets(_RECORDED_TWO, _EXPORTED_TWO, plate)
    check("one entry per part", [entry["name"] for entry in offsets] ==
          ["Plate", "Post"], offsets)
    check("every part on this plate got a position -- none was refused",
          all(entry["plate_centre"] is not None for entry in offsets), offsets)
    check("...and every one of them a real offset",
          all(entry["offset"] is not None for entry in offsets), offsets)
    # Plate centre (104.999, 100.0, 2.5) minus model centre (5, 10, 2.5). The
    # signs are the whole point: this is what a user ADDS to a model coordinate.
    check("the offset is plate centre minus model centre, in that order",
          _near(offsets[0]["offset"], [99.999, 90.0, 0.0]), offsets[0]["offset"])
    check("...off a centre derived from the corner and the size",
          _near(offsets[0]["plate_centre"], [104.999, 100.0, 2.5]), offsets[0])
    check("...and the model centre recorded before the export",
          _near(offsets[0]["model_centre"], [5.0, 10.0, 2.5]), offsets[0])
    # The curved one: reported 11.29 wide for a 10 mm part, and the centre is
    # still exactly where the part is, because the inflation is symmetric.
    check("an inflated curved part's centre survives it",
          _near(offsets[1]["plate_centre"], [94.0, 100.0, 4.0]), offsets[1])

    print("  -- the objects array is not in file order")
    three = ROTATED_THREE["sliced_plates"][0]
    names = [entry["name"] for entry in three["objects"]]
    check("this real plate lists its objects backwards",
          names == ["Object_3", "Object_2", "Object_1"], names)
    ordered = tools_slice._placed_objects(three)
    check("...and they are put back into the file's own order",
          [round(record["size"][2]) for record in ordered] == [10, 30, 60],
          [record["size"] for record in ordered])
    offsets = tools_slice._placement_offsets(_RECORDED_THREE, _EXPORTED_THREE, three)
    check("so each part is matched to its own object",
          all(entry["plate_centre"] is not None for entry in offsets),
          [(entry["name"], entry["reason"]) for entry in offsets])
    check("the unrotated part gets a real offset",
          _near(offsets[0]["offset"], [101.211, 89.0, 0.0]), offsets[0])
    check("...and the 60 mm bar is placed where the 60 mm object is",
          _near(offsets[2]["plate_centre"], [108.975, 106.0, 30.0]), offsets[2])
    for entry in offsets[1:]:
        check(f"{entry['name']} was rotated, so it gets no offset",
              entry["offset"] is None
              and "model axes do not map" in (entry["reason"] or ""), entry)

    # Reading the array positionally -- what the order LOOKS like it means --
    # lines the 10 mm cube up with the 60 mm bar. The size check is what stops
    # that becoming a plausible number pointing at the wrong place.
    ascending = dict(three, objects=[dict(entry, name="")
                                     for entry in three["objects"]])
    slipped = tools_slice._placement_offsets(_RECORDED_THREE, _EXPORTED_THREE,
                                             ascending)
    check("a plate whose objects cannot be ordered falls back to their ids",
          all(entry["plate_centre"] is not None for entry in slipped),
          [(entry["name"], entry["reason"]) for entry in slipped])
    unordered = dict(three, objects=[{"bbox": entry["bbox"]}
                                     for entry in three["objects"]])
    wrong = tools_slice._placement_offsets(_RECORDED_THREE, _EXPORTED_THREE,
                                           unordered)
    check("with neither name nor id, the mismatch is caught rather than quoted",
          wrong[0]["offset"] is None and wrong[0]["plate_centre"] is None, wrong[0])
    check("...saying the parts could not be lined up",
          "could not be lined up" in (wrong[0]["reason"] or ""), wrong[0])

    unreadable = dict(plate, objects=[{"name": "Object_1", "bbox": "somewhere"}])
    none_box = tools_slice._placement_offsets(_RECORDED_TWO, _EXPORTED_TWO[:1],
                                              unreadable)
    check("no readable box means no offset and a reason",
          none_box[0]["offset"] is None
          and "no readable bounding box" in (none_box[0]["reason"] or ""), none_box)

    unrecorded = tools_slice._placement_offsets({}, _EXPORTED_TWO[:1], plate)
    check("a part whose world box was never recorded says so",
          unrecorded[0]["offset"] is None
          and "not recorded" in (unrecorded[0]["reason"] or ""), unrecorded)

    fewer = tools_slice._placement_offsets(_RECORDED_TWO, _EXPORTED_TWO, dict(
        plate, objects=plate["objects"][:1]))
    check("fewer objects on the plate than parts exported truncates",
          len(fewer) == 1, fewer)

    lines = "\n".join(tools_slice._offset_lines(
        tools_slice._placement_offsets(_RECORDED_TWO, _EXPORTED_TWO, plate), True))
    check("the offset lines quote the delta to add",
          "+99.999" in lines and "+90" in lines, lines)
    check("...and warn that the reported sizes are not measurements",
          "not measurements" in lines, lines)
    check("nothing to report gives no lines",
          tools_slice._offset_lines([], True) == [])


# -- the three shapes of read_slice_result ---------------------------------


def _job_record(temp_root, status, label=None, **extra):
    label = label or status
    job_dir = os.path.join(temp_root, "jobs", label)
    os.makedirs(job_dir, exist_ok=True)
    record = {"id": "104500_" + label, "status": status, "job_dir": job_dir,
              "log_path": os.path.join(job_dir, slicer_runner.LOG_NAME),
              "argv": ["/Applications/BambuStudio.app/Contents/MacOS/BambuStudio",
                       "--load-settings", "/a/machine.json;/a/process.json",
                       "--slice", "0", "--outputdir", job_dir, "model.3mf"],
              "elapsed": 22.5, "returncode": 0, "result": None, "error": None,
              "timeout": 900.0, "presets": {}, "options": {}, "note": None,
              "model_path": os.path.join(job_dir, "model.3mf"),
              "started_at": time.time(), "finished_at": time.time()}
    record.update(extra)
    return record


def _check_reports(temp_root):
    print("  -- what read_slice_result says")
    record = _job_record(temp_root, "succeeded", result=ROTATED_THREE)
    with open(os.path.join(record["job_dir"], "plate_1.gcode"), "w",
              encoding="utf-8") as fh:
        fh.write(_GCODE_HEADER)
    tools_slice._remember_export(record["id"], {
        "boxes": _RECORDED_THREE,
        "report": {"exported": _EXPORTED_THREE, "omitted": [], "not_set": [],
                   "skipped": [], "deviation": 0.1},
        "orient": True, "model_path": record["model_path"]})
    text = tools_slice._success_report(record)
    check("a success says so", "succeeded" in text, text)
    check("...and names the G-code file", "plate_1.gcode" in text, text)
    check("...with its layer count", "125 layers" in text, text)
    check("...the print estimate", "30m 13s" in text, text)
    check("...the settings it actually used", "0.2 mm layers" in text, text)
    check("...the feature breakdown", "Sparse infill" in text, text)
    check("...the filament, which this real slice could not weigh",
          "no mass" in text, text)
    check("...where the slicer put the parts", "+101.211" in text, text)
    check("...and the job folder", record["job_dir"] in text, text)

    empty = _job_record(temp_root, "succeeded", label="noresult", result=None)
    text = tools_slice._success_report(empty)
    check("a success with no result.json says what is missing",
          "no readable result.json" in text, text)
    check("...and that no G-code was written",
          "No G-code file was written" in text, text)
    check("...saying there is nothing for view_gcode either",
          "view_gcode has nothing to show" in text, text)
    check("...and, with no log, saying that too",
          "No log was written" in text, text)

    # A success that wrote nothing is the case with no error string anywhere, so
    # the log is the only evidence there is. Sending the reader off to open it
    # costs a turn on a file this call already has the path of.
    with open(empty["log_path"], "w", encoding="utf-8") as fh:
        fh.write("\n".join("line %d" % n for n in range(60))
                 + "\nplate 1 is empty: no object is on the bed\n")
    text = tools_slice._success_report(empty)
    check("a success that wrote no G-code quotes the log rather than citing it",
          "no object is on the bed" in text, text)
    check("...only the tail of it", "line 0" not in text, text)

    failed = _job_record(temp_root, "failed", returncode=1,
                         error="Failed to generate G-code",
                         result={"return_code": 1,
                                 "error_string": "Failed to generate G-code"})
    with open(failed["log_path"], "w", encoding="utf-8") as fh:
        fh.write("\n".join("line %d" % n for n in range(60))
                 + "\nslicing failed: bed is too small\n")
    text = tools_slice._failure_report(failed)
    check("a failure says it failed", "FAILED" in text, text)
    check("...with the slicer's own reason", "Failed to generate G-code" in text, text)
    check("...the tail of the log", "bed is too small" in text, text)
    check("...only the tail of it", "line 0" not in text, text)
    check("...the exit code", "Exit code: 1" in text, text)
    check("...and the command line, so it is diagnosable without re-running",
          "--load-settings" in text, text)

    running = _job_record(temp_root, "running", returncode=None, elapsed=95.0)
    text = tools_slice._still_running(running, 120)
    check("a running job is reported as still slicing",
          "STILL SLICING" in text, text)
    check("...explicitly not as a failure", "Nothing has failed" in text, text)
    check("...and nothing to resend", "nothing needs resending" in text, text)
    check("...telling the caller to read again rather than slice again",
          "read_slice_result again" in text, text)
    check("...and when the slicer will be given up on", "15m" in text, text)

    check("no job at all is a sentence, not an exception",
          "No slice has been started" in tools_slice._run_read_slice_result({}))
    named = tools_slice._run_read_slice_result({"job": "nope"})
    check("an unknown job id names it and what to do",
          "nope" in named and "no argument" in named, named)


def _check_wait_and_precheck():
    print("  -- the wait bounds and the precheck")
    check("no wait_seconds means the default",
          tools_slice._wait_seconds({}) == 120)
    check("zero means do not wait", tools_slice._wait_seconds({"wait_seconds": 0}) == 0)
    check("a wait over the cap is clamped, not honoured",
          tools_slice._wait_seconds({"wait_seconds": 9999}) == 480)
    check("...and the cap stays well under the bridge's 600 s",
          tools_slice._WAIT_MAX <= 480)
    check("a negative wait is zero",
          tools_slice._wait_seconds({"wait_seconds": -5}) == 0)
    check("nonsense falls back to the default",
          tools_slice._wait_seconds({"wait_seconds": "soon"}) == 120)
    # An unknown job must not sit in a loop: there is nothing to wait for.
    started = time.monotonic()
    check("waiting on a job that does not exist returns at once",
          tools_slice._wait_for_job("no-such-job", 30) is None
          and time.monotonic() - started < 5.0)

    check("names and path together are refused",
          "not both" in (tools_slice._precheck_slice_model(
              {"names": ["Box"], "path": "/tmp/a.3mf"}) or ""))
    check("a format the slicer cannot read is refused",
          ".3mf" in (tools_slice._precheck_slice_model({"path": "/tmp/a.step"}) or ""))
    check("a .3mf path is accepted",
          tools_slice._precheck_slice_model({"path": "/tmp/a.3mf"}) is None)
    check("an .stl path is accepted too",
          tools_slice._precheck_slice_model({"path": "/tmp/a.STL"}) is None)
    check("copies below one is refused",
          "below 1" in (tools_slice._precheck_slice_model({"copies": 0}) or ""))
    check("copies that is not a number is refused",
          "whole number" in (tools_slice._precheck_slice_model(
              {"copies": "two"}) or ""))
    check("an ordinary call passes the precheck",
          tools_slice._precheck_slice_model({"names": ["Box"], "copies": 2}) is None)


def _check_preferences():
    print("  -- the preferences, read on this thread and handed over")
    prefs = tools_slice._preferences()
    # Pinned as a set: everything the two stdlib-only modules are handed is read
    # on this thread, and a preference added without appearing here is one they
    # would have to look up themselves.
    check("every value slicer_runner and gcode_server are handed is resolved here",
          set(prefs) == {"binary", "conf", "profile_dirs", "presets", "nozzle",
                         "arrange", "orient", "gcode_ui"}, sorted(prefs))
    check("the viewer directory is a preference too, empty meaning the built one",
          prefs["gcode_ui"] == "", prefs["gcode_ui"])
    check("orient and arrange default on",
          prefs["orient"] is True and prefs["arrange"] is True, prefs)
    check("the nozzle is unset rather than 0.4, so slicer.json can still choose",
          prefs["nozzle"] is None, prefs["nozzle"])
    check("...and slicer_runner's own default is the 0.4 that then applies",
          slicer_runner.DEFAULT_NOZZLE == "0.4")
    check("the preset fallbacks are a mapping of the three kinds",
          set(prefs["presets"]) == {"machine", "process", "filament"},
          prefs["presets"])


def _check_view_gcode_choice(temp_root):
    """Which file view_gcode would show, and what it says when there is none.

    ``_run_view_gcode`` itself is not called: it launches the user's browser.
    What is checked is the half that decides, driven over a stubbed job table --
    no slicer, no subprocess, no window.
    """
    print("  -- what view_gcode picks, and its refusals")
    job_dir = os.path.join(temp_root, "slices", "160000_bracket")
    os.makedirs(job_dir, exist_ok=True)
    plate = os.path.join(job_dir, "plate_1.gcode")
    with open(plate, "w", encoding="utf-8") as fh:
        fh.write("; total layer number: 2\n")

    table = {}
    real_status, real_latest = slicer_runner.job_status, slicer_runner.latest_job
    slicer_runner.job_status = lambda job_id: table.get(job_id)
    slicer_runner.latest_job = lambda: (list(table.values()) or [None])[-1]
    try:
        check("with no slice at all it says so rather than raising",
              tools_slice._gcode_to_show({}) == (None, (
                  "No slice has been run in this FreeCAD session, so there is "
                  "no toolpath to show yet.")),
              tools_slice._gcode_to_show({}))
        check("a job id nobody knows is named back",
              "no slice job called 'nope'" in
              (tools_slice._gcode_to_show({"job": "nope"})[1] or ""),
              tools_slice._gcode_to_show({"job": "nope"}))

        table["160000_bracket"] = {"id": "160000_bracket", "status": "running",
                                   "job_dir": job_dir}
        path, note = tools_slice._gcode_to_show({})
        check("a running job has no G-code yet, and that is not a failure",
              path is None and "still slicing" in note, (path, note))

        table["160000_bracket"]["status"] = "failed"
        path, note = tools_slice._gcode_to_show({})
        check("a failed job points at read_slice_result",
              path is None and "failed" in note and "read_slice_result" in note,
              (path, note))

        table["160000_bracket"]["status"] = "succeeded"
        check("a successful job's G-code is what gets shown",
              tools_slice._gcode_to_show({}) == (plate, "Showing job "
                                                 "'160000_bracket'."),
              tools_slice._gcode_to_show({}))

        second = os.path.join(job_dir, "plate_2.gcode")
        with open(second, "w", encoding="utf-8") as fh:
            fh.write("; total layer number: 3\n")
        path, note = tools_slice._gcode_to_show({})
        check("with several plates it shows the first and says the others exist",
              path == plate and "2 plates" in note, (path, note))
        os.remove(second)

        empty_dir = os.path.join(temp_root, "slices", "161000_empty")
        os.makedirs(empty_dir, exist_ok=True)
        table["161000_empty"] = {"id": "161000_empty", "status": "succeeded",
                                 "job_dir": empty_dir}
        path, note = tools_slice._gcode_to_show({"job": "161000_empty"})
        check("a success that wrote no G-code says that, not nothing",
              path is None and "no G-code file" in note, (path, note))

        check("an explicit path is taken as given",
              tools_slice._gcode_to_show({"path": plate}) == (plate, None),
              tools_slice._gcode_to_show({"path": plate}))
        missing = os.path.join(temp_root, "not-there.gcode")
        path, note = tools_slice._gcode_to_show({"path": missing})
        check("...and a path that is not there is refused by name",
              path is None and missing in note, (path, note))
    finally:
        slicer_runner.job_status, slicer_runner.latest_job = real_status, real_latest

    # "New" in the chat panel. The job table is cleared beside this by
    # slicer_runner.reset_session; this is the half that would otherwise hand a
    # new conversation the previous one's bounding boxes.
    tools_slice._remember_export("160000_bracket", {"boxes": {}, "report": None})
    check("there is an export record to forget", tools_slice._exports)
    tools_slice.reset_session()
    check("reset_session forgets it", tools_slice._exports == {}
          and tools_slice._exports_order == [], tools_slice._exports)


# -- the run ----------------------------------------------------------------


def main():
    print("slice tools")
    temp_root = tempfile.mkdtemp(prefix="fcc-slice-test-")
    artifacts = os.path.join(temp_root, "artifacts")
    os.makedirs(artifacts, exist_ok=True)

    # The real artifacts folder holds the user's own sessions, and the slice
    # tools write into it through here. Redirected for the whole run, in both
    # modules -- tools_slice imported the name, so patching session alone would
    # leave _settings_path pointing at the real one.
    real_artifacts = session.artifacts_dir
    real_session = session._active_session["id"]
    session.artifacts_dir = lambda: artifacts
    tools_slice.artifacts_dir = lambda: artifacts
    session._active_session["id"] = "test-session"

    doc = FreeCAD.newDocument("SliceTools")
    try:
        bracket = doc.addObject("Part::Box", "Bracket")
        bracket.Length, bracket.Width, bracket.Height = 20, 30, 10
        cylinder = doc.addObject("Part::Cylinder", "Cylinder")
        cylinder.Radius, cylinder.Height = 8, 25
        assembly = doc.addObject("App::Part", "Assembly")
        inner = doc.addObject("Part::Box", "Inner")
        assembly.addObject(inner)
        # Modelled well away from the origin, so its bounding box corner and its
        # centre cannot be mistaken for one another.
        placed = doc.addObject("Part::Box", "Offset")
        placed.Length, placed.Width, placed.Height = 10, 10, 6
        placed.Placement = FreeCAD.Placement(FreeCAD.Vector(100, -52, 0),
                                             FreeCAD.Rotation())
        doc.addObject("App::TextDocument", "Notes")  # an object with no shape
        doc.recompute()
        empty_doc = FreeCAD.newDocument("SliceToolsEmpty")
        FreeCAD.setActiveDocument(doc.Name)

        install = _build_install(os.path.join(temp_root, "install"))
        _check_job_dirs(temp_root)
        _check_resolution(doc, empty_doc)
        _check_export_still_works(doc, temp_root)
        _check_write_guard(temp_root)
        _check_recorded_boxes(doc)
        _check_plate_lines()
        _check_no_presets(doc, install)
        _check_stale_settings(doc, install)
        _check_unknown_slicer(doc, install, temp_root)
        _check_settings_page(install, temp_root)
        _check_reader(temp_root)
        _check_boxes()
        _check_offsets()
        _check_reports(temp_root)
        _check_wait_and_precheck()
        _check_view_gcode_choice(temp_root)
        _check_preferences()
    finally:
        session.artifacts_dir = real_artifacts
        tools_slice.artifacts_dir = real_artifacts
        session._active_session["id"] = real_session
        for name in list(FreeCAD.listDocuments()):
            FreeCAD.closeDocument(name)
        shutil.rmtree(temp_root, ignore_errors=True)

    print()
    if _failures:
        print(f"FAIL: {len(_failures)} check(s) failed: {', '.join(_failures)}")
        return 1
    print("PASS")
    return 0


# No __main__ guard: freecadcmd *imports* the script under a module name taken
# from the filename, so a guarded body would silently never run there. It also
# tears the process down on SystemExit without flushing, so do that here or the
# whole report is discarded and the run looks like it printed nothing.
_status = main()
sys.stdout.flush()
sys.exit(_status)
