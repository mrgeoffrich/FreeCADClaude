#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Discovery, preset resolution, the argv and the job table, for
freecad/freecadclaude/slicer_runner.py.

Four things worth pinning, in the order they matter:

The nozzle pin. A slicer session left on a 0.2 nozzle has a 0.2 machine, a 0.2
process and a 0.2 filament selected, so the check is that all three come back
re-derived at 0.4 -- snapping the machine alone would leave a command line the
slicer refuses, several minutes after the user asked for a slice.

Compatibility read as a field. ``compatible_printers`` is what says whether a
process may be used with a machine, and the tests make it disagree with the
naming convention so a name-parsing shortcut cannot pass them.

Precedence. A user preset shadows a system one of the same name, and it sits a
folder deeper than the system tree does; the four resolution levels each have to
report which one they were.

The job table. A slice runs on a daemon thread against a real subprocess, so
success, the timeout kill and the completion hook are all exercised against a
fake binary that is only a Python one-liner. No real slicer is ever started,
here or anywhere else: they are GUI applications, and a flag one of them does not
take opens a dialog on the user's desktop.

The module under test imports no Qt and no FreeCAD -- that invariant is what
keeps a slicer subprocess off the GUI thread and it is asserted here rather than
described -- so this needs no GUI, no running FreeCAD, no slicer installed and no
network:

    python3 eval/test_slicer_runner.py

A bare interpreter, not freecadcmd: the fake slicer is spawned as a real child
process, and under freecadcmd there is no interpreter to spawn it with -- the one
in ``sys.executable`` is FreeCAD's launcher, which does not take ``-c``, and any
other inherits an environment it will not start in.

Exit: 0 = all passed, 1 = a failure.
"""

import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Module roots that must not appear because of our import. Snapshotted before
#: the import rather than simply looked for afterwards, so that running this
#: inside an interpreter that already has FreeCAD loaded still asks the right
#: question.
_FORBIDDEN_ROOTS = ("FreeCAD", "FreeCADGui", "PySide", "PySide2", "PySide6",
                    "PyQt5", "PyQt6", "shiboken2", "shiboken6", "MeshPart")


def _forbidden(modules):
    return {name for name in modules if name.split(".")[0] in _FORBIDDEN_ROOTS}


_PRELOADED = _forbidden(sys.modules)


def _load_slicer_runner():
    """Import slicer_runner.py directly by path.

    Not ``from freecad.freecadclaude import slicer_runner``: importing the
    package pulls in modules that expect a FreeCAD process. The module is
    standalone, so load it that way and keep the test honest about that.
    """
    path = os.path.join(_ROOT, "freecad", "freecadclaude", "slicer_runner.py")
    spec = importlib.util.spec_from_file_location("_fcc_slicer_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load_slicer_runner()

_failures = []


def check(name, condition, detail=""):
    status = "ok  " if condition else "FAIL"
    print(f"  {status} {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(name)


# -- the synthetic slicer install -------------------------------------------
# One printer with two nozzles, and presets whose names say less than their
# compatible_printers does: "0.16mm Optimal @BBL P2S" names no nozzle, and the
# user's own filament copy declares no compatibility at all and inherits it.

MODEL = "Bambu Lab P2S"
OTHER_MODEL = "Bambu Lab P1S"
M04 = "Bambu Lab P2S 0.4 nozzle"
M02 = "Bambu Lab P2S 0.2 nozzle"
P04 = "0.20mm Standard @BBL P2S"
P04_ALT = "0.16mm Optimal @BBL P2S"
P02 = "0.10mm Standard @BBL P2S 0.2 nozzle"
BASE_PROCESS = "fdm_process_common"
F04 = "Bambu PLA Basic @BBL P2S"
F04_MATTE = "Bambu PLA Matte @BBL P2S"
F02 = "Bambu PLA Basic @BBL P2S 0.2 nozzle"


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    return path


def _write_stub_binary(path):
    """A file that only has to exist and be executable -- discovery probes the
    filesystem and never runs anything."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


def _build_install(root):
    """A slicer install, a config folder and a user preset tree under `root`."""
    app = os.path.join(root, "BambuStudio.app", "Contents")
    binary = _write_stub_binary(os.path.join(app, "MacOS", "BambuStudio"))
    orca = _write_stub_binary(os.path.join(
        root, "OrcaSlicer.app", "Contents", "MacOS", "OrcaSlicer"))
    system = os.path.join(app, "Resources", "profiles", "BBL")

    # The model-level file, in the same folder as the machine presets and told
    # apart from them only by its type. Its nozzle list covers every variant.
    _write_json(os.path.join(system, "machine", MODEL + ".json"),
                {"type": "machine_model", "name": MODEL,
                 "nozzle_diameter": "0.4;0.2",
                 "default_materials": F04_MATTE + ";" + F04})
    _write_json(os.path.join(system, "machine", M04 + ".json"),
                {"type": "machine", "name": M04, "printer_model": MODEL,
                 "printer_variant": "0.4", "nozzle_diameter": ["0.4"],
                 "default_print_profile": P04,
                 "default_filament_profile": [F04]})
    # No printer_model of its own: it inherits one, like the real 0.2 preset.
    _write_json(os.path.join(system, "machine", M02 + ".json"),
                {"type": "machine", "name": M02, "inherits": M04,
                 "printer_variant": "0.2", "nozzle_diameter": ["0.2"],
                 "default_print_profile": P02,
                 "default_filament_profile": [F02]})
    for name, printer in ((P04, M04), (P04_ALT, M04), (P02, M02)):
        _write_json(os.path.join(system, "process", name + ".json"),
                    {"type": "process", "name": name,
                     "compatible_printers": [printer]})
    # A base profile the vendor tree is built out of: compatible with everything
    # because it constrains nothing, and not a preset anyone may choose.
    _write_json(os.path.join(system, "process", BASE_PROCESS + ".json"),
                {"type": "process", "name": BASE_PROCESS,
                 "instantiation": "false"})
    for name, printer in ((F04, M04), (F04_MATTE, M04), (F02, M02)):
        _write_json(os.path.join(system, "filament", name + ".json"),
                    {"type": "filament", "name": name, "which": "system",
                     "compatible_printers": [printer]})

    # The user's own copy of a system preset: same name, one folder deeper, and
    # carrying only the fields it changed -- so its compatibility is only
    # reachable through inherits.
    user = os.path.join(root, "config", "BambuStudio", "user", "1925491949")
    _write_json(os.path.join(user, "filament", "base", F04_MATTE + ".json"),
                {"type": "filament", "name": F04_MATTE, "inherits": F04,
                 "which": "user"})

    models = [{"model": MODEL, "nozzle_diameter": "0.2;0.4", "vendor": "BBL"},
              {"model": OTHER_MODEL, "nozzle_diameter": "0.4", "vendor": "BBL"}]
    conf_dir = os.path.join(root, "config", "BambuStudio")
    conf_02 = _write_json(os.path.join(conf_dir, "BambuStudio.conf"),
                          {"models": models,
                           "presets": {"machine": M02, "process": P02,
                                       "filaments": [F02, F02]}})
    conf_04 = _write_json(os.path.join(conf_dir, "Studio-0.4.conf"),
                          {"models": models,
                           "presets": {"machine": M04, "process": P04_ALT,
                                       "filaments": [F04_MATTE, F04]}})
    # Studio writing its config while we read it.
    truncated = os.path.join(conf_dir, "Studio-truncated.conf")
    with open(truncated, "w", encoding="utf-8") as fh:
        fh.write('{"models": [{"model": "Bambu Lab P2S", "nozz')
    # Valid JSON, wrong shape.
    shaped = _write_json(os.path.join(conf_dir, "Studio-shape.conf"),
                         {"presets": "not a mapping", "models": 7})

    # A preset outside every profile root, reachable only as a path.
    custom = _write_json(os.path.join(root, "custom", "my-machine.json"),
                         {"type": "machine", "name": "My P2S",
                          "printer_model": MODEL, "printer_variant": "0.4",
                          "nozzle_diameter": ["0.4"],
                          "default_print_profile": P04,
                          "default_filament_profile": [F04]})
    return {"binary": binary, "orca": orca, "system": system, "user": user,
            "conf_02": conf_02, "conf_04": conf_04, "truncated": truncated,
            "shaped": shaped, "missing": os.path.join(conf_dir, "nope.conf"),
            "custom": custom}


def _sources(resolved):
    """``{kind: source}`` for a resolve_presets result, None where unresolved."""
    return {kind: (resolved[kind] or {}).get("source")
            for kind in ("machine", "process", "filament")}


def _names(resolved):
    return {kind: (resolved[kind] or {}).get("name")
            for kind in ("machine", "process", "filament")}


# -- the fake slicer --------------------------------------------------------
# A Python one-liner standing in for the binary: it writes the two files a real
# slice leaves behind and prints on both streams, which is what the log check
# needs. Started through build_argv like the real one, so it also proves the
# argv is something a process can actually be spawned with.

def _python():
    """A plain interpreter to stand the fake slicer up on.

    Never a discovered slicer, at any point in this file: an unrecognised flag
    opens a dialog on the user's screen rather than printing to stderr.
    """
    if os.path.basename(sys.executable).lower().startswith("python"):
        return sys.executable
    return shutil.which("python3") or shutil.which("python") or sys.executable


_FAKE_SLICER = """
import json, os, sys
argv = sys.argv[1:]
out = argv[argv.index('--outputdir') + 1]
with open(os.path.join(out, 'plate_1.gcode'), 'w') as fh:
    fh.write('; FEATURE: Outer wall\\nG1 X10 Y10\\n')
with open(os.path.join(out, 'result.json'), 'w') as fh:
    json.dump({'return_code': 0, 'error_string': 'Success.',
               'plates': [{'index': 1, 'gcode': 'plate_1.gcode'}]}, fh)
sys.stdout.write('slicing plate 1\\n')
sys.stderr.write('a warning from the slicer\\n')
"""

#: A child that will not finish. It touches a file first, so a test can wait
#: until the process really exists rather than until the job was queued.
_FAKE_SLEEPER = """
import os, sys, time
argv = sys.argv[1:]
out = argv[argv.index('--outputdir') + 1]
open(os.path.join(out, 'started'), 'w').close()
time.sleep(30)
"""

_FAKE_FAILER = """
import json, os, sys
argv = sys.argv[1:]
out = argv[argv.index('--outputdir') + 1]
with open(os.path.join(out, 'result.json'), 'w') as fh:
    json.dump({'return_code': 1, 'error_string': 'Failed to generate G-code'}, fh)
sys.exit(1)
"""


def _wait_for(predicate, timeout):
    """Poll `predicate` until it holds or `timeout` elapses. The job runs on a
    daemon thread, so there is nothing to join."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return bool(predicate())


def _finished(job_id):
    status = runner.job_status(job_id)
    return status is not None and status["status"] != "running"


def _build_or_refuse(binary, presets, model_path, outputdir, start=False, **kwargs):
    """``(result, error)`` -- whichever of the two came back.

    Both halves are returned so a check can assert on the error AND on what was
    produced anyway: an argv built for the wrong slicer is the failure this
    guards, so "it raised" is not on its own enough to look at.
    """
    call = runner.start_job if start else runner.build_argv
    try:
        return call(binary, presets, model_path, outputdir, **kwargs), None
    except Exception as exc:  # noqa: BLE001 - which exception it is, is the point
        return None, exc


# -- the checks -------------------------------------------------------------


def _check_import_invariant():
    print("  -- the no-FreeCAD, no-Qt invariant")
    now = _forbidden(sys.modules)
    check("importing it loads no FreeCAD and no Qt module",
          now <= _PRELOADED, sorted(now - _PRELOADED))
    if _PRELOADED:
        print("       (this interpreter had %s loaded already)"
              % ", ".join(sorted(_PRELOADED)))


def _check_discovery(install):
    print("  -- discovery")
    order = [runner.slicer_flavour(path) for path in runner._binary_candidates()]
    check("Bambu Studio is preferred over OrcaSlicer",
          runner.ORCA not in order
          or order.index(runner.BAMBU) < order.index(runner.ORCA), order)

    missing = os.path.join(os.path.dirname(install["binary"]), "NotInstalled")
    plain = os.path.join(os.path.dirname(install["binary"]), "notes.txt")
    with open(plain, "w", encoding="utf-8") as fh:
        fh.write("not a binary")
    found = runner.discover_binary([missing, install["binary"]])
    check("discover_binary skips what is not there",
          found and found["path"] == install["binary"], found)
    check("...and reports which slicer it is",
          found and found["flavour"] == runner.BAMBU, found)
    orca = runner.discover_binary([install["orca"]])
    check("an OrcaSlicer path is reported as OrcaSlicer",
          orca and orca["flavour"] == runner.ORCA, orca)
    check("nothing installed -> None", runner.discover_binary([]) is None)
    check("discover_conf_path finds the config file",
          runner.discover_conf_path([install["missing"], install["conf_02"]])
          == install["conf_02"])

    dirs = runner.discover_profile_dirs(install["binary"], install["conf_02"])
    check("the user root comes before the system one",
          dirs == [install["user"], install["system"]], dirs)

    index = runner.index_presets(dirs)
    check("the flat system tree is indexed",
          index["process"].get(P04)
          == os.path.join(install["system"], "process", P04 + ".json"),
          index["process"].get(P04))
    check("...and the deeper user tree too",
          index["filament"].get(F04_MATTE)
          == os.path.join(install["user"], "filament", "base", F04_MATTE + ".json"),
          index["filament"].get(F04_MATTE))
    check("the model-level file is indexed beside the machine presets",
          set(index["machine"]) == {MODEL, M04, M02}, sorted(index["machine"]))
    return dirs, index


def _check_nozzle_pin(install, dirs):
    print("  -- the 0.4 nozzle pin, against a config left on 0.2")
    resolved = runner.resolve_presets(conf_path=install["conf_02"], profile_dirs=dirs)
    names = _names(resolved)
    check("the machine is snapped to 0.4", names["machine"] == M04, names)
    check("the process is re-derived, not kept at 0.2", names["process"] == P04, names)
    check("the filament is re-derived too", names["filament"] == F04, names)
    check("nothing is left unresolved", resolved["missing"] == [], resolved["missing"])
    check("the nozzle it pinned to is reported", resolved["nozzle"] == "0.4")
    check("so is the printer it resolved for", resolved["printer"] == MODEL,
          resolved["printer"])
    check("the substituted presets say they came from the machine's defaults",
          _sources(resolved) == {"machine": runner.SOURCE_STUDIO,
                                "process": runner.SOURCE_MACHINE_DEFAULT,
                                "filament": runner.SOURCE_MACHINE_DEFAULT},
          _sources(resolved))
    notes = " | ".join(resolved["notes"])
    check("the pin is reported", M02 in notes and "0.4 mm nozzle" in notes, notes)
    check("...and so is each substitution it forced",
          P02 in notes and F02 in notes and "not compatible" in notes, notes)

    # The pin is a default, not a rule: a nozzle asked for explicitly is honoured
    # and all three presets follow it, which is the same derivation in reverse.
    at_02 = runner.resolve_presets(conf_path=install["conf_02"], profile_dirs=dirs,
                                   nozzle="0.2")
    check("an explicit 0.2 keeps all three at 0.2",
          _names(at_02) == {"machine": M02, "process": P02, "filament": F02},
          _names(at_02))
    check("...with nothing substituted",
          _sources(at_02) == {"machine": runner.SOURCE_STUDIO,
                              "process": runner.SOURCE_STUDIO,
                              "filament": runner.SOURCE_STUDIO}
          and at_02["notes"] == [], at_02["notes"])


def _check_kept_selection(install, dirs):
    print("  -- a compatible selection is kept, and user presets win")
    resolved = runner.resolve_presets(conf_path=install["conf_04"], profile_dirs=dirs)
    check("the process the config chose is kept",
          _names(resolved)["process"] == P04_ALT, _names(resolved))
    check("...on its compatible_printers and not on its name",
          _sources(resolved)["process"] == runner.SOURCE_STUDIO, _sources(resolved))
    check("nothing was substituted, so nothing is reported",
          resolved["notes"] == [], resolved["notes"])

    filament = resolved["filament"]
    check("the user's copy of a preset beats the system one of that name",
          filament["path"].startswith(install["user"]), filament)
    check("...and its compatibility is followed through inherits",
          (_read(filament["path"]) or {}).get("which") == "user", filament)


def _check_levels(install, dirs):
    print("  -- the four resolution levels")
    settings = os.path.join(os.path.dirname(install["conf_04"]), "slicer.json")
    _write_json(settings, {"process": P04, "filament": F02})
    resolved = runner.resolve_presets(
        conf_path=install["conf_04"], profile_dirs=dirs,
        overrides={"process": P04_ALT}, config_path=settings,
        fallbacks={"machine": M02, "process": P02, "filament": F04})
    check("an argument beats slicer.json",
          _names(resolved)["process"] == P04_ALT
          and _sources(resolved)["process"] == runner.SOURCE_ARGUMENT,
          (_names(resolved), _sources(resolved)))
    check("slicer.json beats the config's own selection",
          _names(resolved)["filament"] == F02
          and _sources(resolved)["filament"] == runner.SOURCE_CONFIG,
          (_names(resolved), _sources(resolved)))
    check("the config's selection beats the fallbacks",
          _names(resolved)["machine"] == M04
          and _sources(resolved)["machine"] == runner.SOURCE_STUDIO,
          (_names(resolved), _sources(resolved)))

    # slicer.json can also move the nozzle, which is the settings panel's job.
    _write_json(settings, {"nozzle": "0.2"})
    pinned = runner.resolve_presets(conf_path=install["conf_04"],
                                   profile_dirs=dirs, config_path=settings)
    check("slicer.json's nozzle re-derives all three",
          _names(pinned) == {"machine": M02, "process": P02, "filament": F02},
          _names(pinned))

    # A path always wins, and it does not have to be under any profile root.
    _write_json(settings, {"machine": M02})
    override = runner.resolve_presets(
        conf_path=install["conf_02"], profile_dirs=dirs, config_path=settings,
        overrides={"machine": install["custom"]},
        fallbacks={"machine": M02})
    check("a path to a JSON wins over every name",
          (override["machine"] or {}).get("path") == install["custom"], override)
    check("...and is reported as the argument it was",
          _sources(override)["machine"] == runner.SOURCE_ARGUMENT, override)
    check("...and names itself from the file, not the filename",
          (override["machine"] or {}).get("name") == "My P2S", override)

    # A stored name that no longer resolves is a stale name, not a licence to
    # slice with something else.
    _write_json(settings, {"machine": "Bambu Lab X9 0.4 nozzle"})
    stale = runner.resolve_presets(conf_path=install["conf_04"], profile_dirs=dirs,
                                   config_path=settings,
                                   fallbacks={"machine": M02})
    check("a stale stored preset name leaves it unresolved",
          stale["machine"] is None and "machine" in stale["missing"], stale)
    check("...and is named in the report",
          any("Bambu Lab X9 0.4 nozzle" in note for note in stale["notes"]),
          stale["notes"])
    os.remove(settings)


def _check_degrading(install, dirs):
    print("  -- an unreadable config degrades to the next level")
    fallbacks = {"machine": M04, "process": P04, "filament": F04}
    for label, conf in (("truncated", install["truncated"]),
                        ("missing", install["missing"]),
                        ("wrong shape", install["shaped"])):
        try:
            resolved = runner.resolve_presets(conf_path=conf, profile_dirs=dirs,
                                              fallbacks=fallbacks)
        except Exception as exc:  # noqa: BLE001 - a raise here is the failure
            check(f"a {label} config resolves rather than raising", False, repr(exc))
            continue
        check(f"a {label} config falls through to the fallbacks",
              _sources(resolved) == {"machine": runner.SOURCE_FALLBACK,
                                     "process": runner.SOURCE_FALLBACK,
                                     "filament": runner.SOURCE_FALLBACK},
              _sources(resolved))
        check(f"...and says the {label} config could not be read",
              any("could not be read" in note for note in resolved["notes"]),
              resolved["notes"])
    check("studio_selection on a truncated config returns nothing",
          runner.studio_selection(install["truncated"]) == {})
    empty = runner.resolve_presets(conf_path=install["missing"], profile_dirs=dirs)
    check("with no fallbacks either, every kind is reported missing",
          empty["missing"] == ["machine", "process", "filament"], empty["missing"])


def _check_options(install, dirs):
    print("  -- discover_options for the settings panel")
    options = runner.discover_options(install["conf_04"], dirs, machine=M04)
    printers = {entry["model"]: entry for entry in options["printers"]}
    check("the printers the user has added are offered",
          sorted(printers) == sorted([MODEL, OTHER_MODEL]), sorted(printers))
    check("with the nozzles each supports",
          printers[MODEL]["nozzles"] == ["0.2", "0.4"], printers[MODEL])
    check("...mapped to the machine preset for each",
          printers[MODEL]["machines"] == {"0.2": M02, "0.4": M04},
          printers[MODEL]["machines"])
    check("a printer with no presets installed offers no machine",
          printers[OTHER_MODEL]["machines"] == {}, printers[OTHER_MODEL])
    check("the model's own material order comes along",
          printers[MODEL]["materials"] == [F04_MATTE, F04],
          printers[MODEL]["materials"])

    processes = [entry["name"] for entry in options["processes"]]
    filaments = [entry["name"] for entry in options["filaments"]]
    check("only the processes compatible with that machine are listed",
          processes == sorted([P04, P04_ALT]), processes)
    check("the vendor's own base profiles are not offered as choices",
          BASE_PROCESS not in processes, processes)
    check("only the compatible filaments are listed",
          filaments == sorted([F04, F04_MATTE]), filaments)
    marked = {kind: [entry["name"] for entry in options[kind] if entry["default"]]
              for kind in ("processes", "filaments")}
    check("the machine's declared default process is marked",
          marked["processes"] == [P04], options["processes"])
    check("...and its default filament", marked["filaments"] == [F04],
          options["filaments"])
    check("the config's own selection is reported alongside",
          options["selected"] == {"machine": M04, "process": P04_ALT,
                                 "filament": F04_MATTE}, options["selected"])

    other = runner.discover_options(install["conf_04"], dirs, machine=M02)
    check("the same filter run for the 0.2 machine lists only its presets",
          [entry["name"] for entry in other["processes"]] == [P02]
          and [entry["name"] for entry in other["filaments"]] == [F02],
          (other["processes"], other["filaments"]))
    default = runner.discover_options(install["conf_04"], dirs)
    check("with no machine named, the config's selection is filtered for",
          default["machine"] == M04, default["machine"])


def _check_argv(install, dirs):
    print("  -- the command line")
    resolved = runner.resolve_presets(conf_path=install["conf_04"], profile_dirs=dirs)
    machine = resolved["machine"]["path"]
    process = resolved["process"]["path"]
    filament = resolved["filament"]["path"]

    argv = runner.build_argv(install["binary"], resolved, "/tmp/model.3mf",
                             "/tmp/out", copies=3)
    check("the argv is exactly the verified invocation",
          argv == [install["binary"],
                   "--load-settings", machine + ";" + process,
                   "--load-filaments", filament,
                   "--arrange", "1", "--ensure-on-bed", "--repetitions", "3",
                   "--slice", "0", "--outputdir", "/tmp/out", "/tmp/model.3mf"],
          argv)
    check("machine and process are joined with a semicolon",
          argv[argv.index("--load-settings") + 1] == machine + ";" + process,
          argv[argv.index("--load-settings") + 1])
    check("...as one argument, so neither is passed on its own",
          machine not in argv and process not in argv, argv)

    plain = runner.build_argv(install["binary"], resolved, "m.3mf", "out",
                              arrange=False, ensure_on_bed=False)
    check("arrange=False is spelled out rather than dropped",
          "--arrange" in plain and plain[plain.index("--arrange") + 1] == "0", plain)
    check("ensure_on_bed=False leaves the flag off", "--ensure-on-bed" not in plain)
    check("one copy needs no --repetitions", "--repetitions" not in plain, plain)

    # A flag the other slicer does not take does not fail quietly: it opens a
    # dialog on the user's desktop and returns nothing to read. So every flag in
    # the verified invocation is Bambu-derived, and none of them may reach a
    # binary that has not been identified as Bambu Studio.
    derived = [arg for arg in argv if arg.startswith("--")]
    check("every flag in the verified argv is under test",
          set(derived) == {"--load-settings", "--load-filaments", "--arrange",
                           "--ensure-on-bed", "--repetitions", "--slice",
                           "--outputdir"}, derived)
    for label, binary in (("OrcaSlicer", install["orca"]),
                          ("a binary whose name says no slicer", "/opt/slicer/run")):
        built, error = _build_or_refuse(binary, resolved, "m.3mf", "out", copies=3)
        check(f"{label} gets no command line at all",
              built is None and isinstance(error, runner.UnknownSlicer),
              (built, repr(error)))
        check(f"...so not one Bambu-derived flag reaches {label}",
              not [flag for flag in derived if flag in (built or [])], built)
        check(f"...and the refusal for {label} names it and what to point at",
              error is not None and str(binary) in str(error)
              and "SlicerPath" in str(error), str(error))
    check("the flavour can be stated when the name does not say it",
          "--ensure-on-bed" in runner.build_argv(
              "/opt/slicer/run", resolved, "m.3mf", "out", flavour=runner.BAMBU))
    # The refusal has to arrive before any of the job machinery moves, since a
    # job folder and a table entry for a slice that cannot be built are litter.
    never = os.path.join(os.path.dirname(install["orca"]), "never-a-job")
    started, error = _build_or_refuse(install["orca"], resolved, "m.3mf", never,
                                      start=True)
    check("start_job refuses an unidentified binary too",
          started is None and isinstance(error, runner.UnknownSlicer), repr(error))
    check("...before creating a job folder or a job at all",
          not os.path.exists(never) and runner.latest_job() is None,
          runner.latest_job())
    found = runner.discover_binary([install["binary"]])
    check("discover_binary reports the flavour build_argv keys off",
          found["flavour"] == runner.BAMBU
          and runner.discover_binary([install["orca"]])["flavour"] == runner.ORCA)

    # A mapping of bare paths works too, which is the escape hatch for a caller
    # that resolved the presets some other way.
    bare = runner.build_argv(install["binary"], {"machine": machine}, "m.3mf", "out")
    check("a preset with nothing resolved for it is left off",
          "--load-filaments" not in bare
          and bare[bare.index("--load-settings") + 1] == machine, bare)


def _check_job_success(install, dirs, jobs_root):
    print("  -- a job that succeeds")
    resolved = runner.resolve_presets(conf_path=install["conf_04"], profile_dirs=dirs)
    job_dir = os.path.join(jobs_root, "143002_bracket")
    done = []
    runner.set_done_hook(done.append)
    job_id = runner.start_job([_python(), "-c", _FAKE_SLICER], resolved,
                              os.path.join(jobs_root, "model.3mf"), job_dir,
                              {"arrange": True, "copies": 1,
                               "flavour": runner.BAMBU}, note="the bracket")
    check("start_job returns immediately with a handle", bool(job_id), job_id)
    check("the id is the job folder's own name", job_id == "143002_bracket", job_id)
    running = runner.job_status(job_id)
    check("job.json exists before the slice finishes",
          os.path.isfile(os.path.join(job_dir, runner.JOB_NAME)))
    check("a poller sees it running or finished, never nothing",
          running is not None and running["status"] in
          ("running", "succeeded", "failed"), running)

    check("it finishes", _wait_for(lambda: _finished(job_id), 30.0))
    status = runner.job_status(job_id)
    check("...as a success", status["status"] == "succeeded", status)
    check("with the child's exit code", status["returncode"] == 0, status)
    check("and no error", status["error"] is None, status)
    check("elapsed time is reported", status["elapsed"] >= 0.0, status)
    check("the slicer's result.json is parsed",
          (status["result"] or {}).get("error_string") == "Success.", status["result"])
    check("the G-code landed in the job folder",
          os.path.isfile(os.path.join(job_dir, "plate_1.gcode")))

    with open(status["log_path"], "rb") as fh:
        log = fh.read()
    check("slicer.log holds the child's output",
          b"slicing plate 1" in log, log[:120])
    check("...both streams of it", b"a warning from the slicer" in log, log[:200])

    with open(os.path.join(job_dir, runner.JOB_NAME), encoding="utf-8") as fh:
        recorded = json.load(fh)
    check("job.json holds the argv actually used",
          recorded["argv"] == status["argv"], recorded.get("argv"))
    check("...the note", recorded["note"] == "the bracket", recorded.get("note"))
    check("...the options", recorded["options"]["copies"] == 1, recorded.get("options"))
    check("...the presets and where each came from",
          recorded["presets"]["machine"]["source"] == runner.SOURCE_STUDIO,
          recorded.get("presets"))
    check("...and the timestamps",
          recorded["started_at"] > 0 and recorded["finished_at"] > 0, recorded)
    check("latest_job() is this one", (runner.latest_job() or {}).get("id") == job_id)
    # Wait on the hook rather than on the status: the hook runs on the worker
    # thread once the record is final, so it lands a moment after the status a
    # poller sees flip.
    check("the done hook fired, with the finished record",
          _wait_for(lambda: len(done) == 1, 10.0) and done[0]["id"] == job_id
          and done[0]["status"] == "succeeded", done)
    check("job_status on an unknown id -> None",
          runner.job_status("no-such-job") is None)
    runner.set_done_hook(None)


def _check_job_failure(install, dirs, jobs_root):
    print("  -- a job that fails, and one that runs too long")
    resolved = runner.resolve_presets(conf_path=install["conf_04"], profile_dirs=dirs)
    job_dir = os.path.join(jobs_root, "143500_failing")
    job_id = runner.start_job([_python(), "-c", _FAKE_FAILER], resolved,
                              "model.3mf", job_dir, {"flavour": runner.BAMBU})
    check("it finishes", _wait_for(lambda: _finished(job_id), 30.0))
    status = runner.job_status(job_id)
    check("a non-zero exit is a failure", status["status"] == "failed", status)
    check("...reported with the slicer's own words",
          status["error"] == "Failed to generate G-code", status["error"])

    # The timeout, at a fraction of a second against a child that would sit for
    # thirty. What is under test is the rule, not the number.
    done = []
    runner.set_done_hook(done.append)
    slow_dir = os.path.join(jobs_root, "144000_slow")
    started = time.monotonic()
    slow_id = runner.start_job([_python(), "-c", _FAKE_SLEEPER], resolved,
                               "model.3mf", slow_dir,
                               {"timeout": 0.6, "flavour": runner.BAMBU})
    check("the sleeper is given up on", _wait_for(lambda: _finished(slow_id), 10.0))
    elapsed = time.monotonic() - started
    slow = runner.job_status(slow_id)
    check("...well before it would have ended on its own", elapsed < 10.0,
          f"{elapsed:.1f}s")
    check("a timeout is a failure, not a success",
          slow["status"] == "failed", slow)
    check("...and says it was killed on the timeout",
          "was killed" in (slow["error"] or ""), slow["error"])
    check("the child is gone, not merely abandoned",
          slow["returncode"] is not None and slow["returncode"] != 0, slow)
    check("the hook fires for a failure too",
          _wait_for(lambda: len(done) == 1, 10.0)
          and done[0]["status"] == "failed", done)
    runner.set_done_hook(None)


def _check_reset_session(install, dirs, jobs_root):
    print("  -- reset_session, for the chat panel's New")
    resolved = runner.resolve_presets(conf_path=install["conf_04"], profile_dirs=dirs)
    # From a clear table: the checks above left several jobs in it, and the
    # count below is about this one.
    runner.reset_session()
    job_dir = os.path.join(jobs_root, "151500_previous_chat")
    job_id = runner.start_job([_python(), "-c", _FAKE_SLICER], resolved,
                              "model.3mf", job_dir, {"flavour": runner.BAMBU})
    check("it finishes", _wait_for(lambda: _finished(job_id), 30.0))
    check("there is a job to forget", runner.latest_job() is not None)

    check("reset_session reports what it dropped", runner.reset_session() == 1)
    # The failure this exists to prevent: read_slice_result with no argument
    # answering a new conversation with the previous one's job.
    check("latest_job() is None afterwards", runner.latest_job() is None,
          runner.latest_job())
    check("...and a known job id no longer resolves",
          runner.job_status(job_id) is None, runner.job_status(job_id))
    check("the job's own folder is untouched -- only the handle went",
          os.path.isfile(os.path.join(job_dir, runner.JOB_NAME))
          and os.path.isfile(os.path.join(job_dir, "plate_1.gcode")))
    check("reset_session with nothing to forget is a no-op",
          runner.reset_session() == 0)

    # A slice still running is dropped from the table but not from
    # terminate_all's reach: forgetting the handle must not orphan the child.
    slow_dir = os.path.join(jobs_root, "151800_still_running")
    slow_id = runner.start_job([_python(), "-c", _FAKE_SLEEPER], resolved,
                               "model.3mf", slow_dir,
                               {"timeout": 60, "flavour": runner.BAMBU})
    check("the child is really running",
          _wait_for(lambda: os.path.exists(os.path.join(slow_dir, "started")), 15.0))
    check("resetting drops a running job's handle too", runner.reset_session() == 1
          and runner.job_status(slow_id) is None, runner.job_status(slow_id))
    check("...but quitting can still stop its child", runner.terminate_all() == 1)
    check("...and the child really ended, rather than being merely signalled",
          _wait_for(lambda: runner.terminate_all() == 0, 15.0))


def _check_terminate_all(install, dirs, jobs_root):
    print("  -- terminate_all, for quitting mid-slice")
    resolved = runner.resolve_presets(conf_path=install["conf_04"], profile_dirs=dirs)
    job_dir = os.path.join(jobs_root, "150000_quitting")
    job_id = runner.start_job([_python(), "-c", _FAKE_SLEEPER], resolved,
                              "model.3mf", job_dir,
                              {"timeout": 60, "flavour": runner.BAMBU})
    started = os.path.join(job_dir, "started")
    check("the child is really running", _wait_for(
        lambda: os.path.exists(started), 15.0)
        and (runner.job_status(job_id) or {}).get("status") == "running")
    check("terminate_all reports what it stopped", runner.terminate_all() == 1)
    check("the job ends", _wait_for(lambda: _finished(job_id), 10.0))
    status = runner.job_status(job_id)
    check("...as a failure that says it was stopped",
          status["status"] == "failed" and "stopped" in (status["error"] or ""),
          status)
    check("terminate_all with nothing running is a no-op",
          runner.terminate_all() == 0)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main():
    print("slicer_runner")
    _check_import_invariant()

    root = tempfile.mkdtemp(prefix="fcc-slicer-test-")
    try:
        install = _build_install(root)
        dirs, _index = _check_discovery(install)
        _check_nozzle_pin(install, dirs)
        _check_kept_selection(install, dirs)
        _check_levels(install, dirs)
        _check_degrading(install, dirs)
        _check_options(install, dirs)
        _check_argv(install, dirs)
        jobs_root = os.path.join(root, "session", "slices")
        _check_job_success(install, dirs, jobs_root)
        _check_job_failure(install, dirs, jobs_root)
        _check_reset_session(install, dirs, jobs_root)
        _check_terminate_all(install, dirs, jobs_root)
    finally:
        runner.set_done_hook(None)
        runner.terminate_all()
        shutil.rmtree(root, ignore_errors=True)

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
