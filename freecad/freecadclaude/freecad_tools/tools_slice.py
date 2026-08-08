# SPDX-License-Identifier: LGPL-2.1-or-later
"""slice_model / read_slice_result / view_gcode -- hand a plate to the desktop slicer.

Two halves, like ``tools_annotate`` and ``tools_device``, and for a harder
reason than either: nobody is waiting in the middle, but a slice takes about
twenty seconds for a trivial part and minutes for a real one, and every tool
call runs on FreeCAD's GUI thread. So ``slice_model`` does the FreeCAD work --
resolve the objects, record their world boxes, write the oriented 3MF, resolve
the presets -- hands ``slicer_runner`` a finished command line, and returns a job
id while the slicer runs on a daemon thread. ``read_slice_result`` collects the
outcome afterwards, optionally waiting for it inside a nested event loop, so the
application keeps repainting and the bridge keeps marshalling calls while it
waits.

Everything environmental is resolved at this end. ``slicer_runner`` reads no
FreeCAD preference and must not start: it runs the slicer off the GUI thread, and
a FreeCAD call from there is the invariant the whole feature rests on. So the
binary, the slicer's config, the profile roots, the preset names and the
arrange/orient defaults are all read here and handed over as plain values.

Two hazards this module exists to keep away from the GUI thread.

``Mesh.export`` to a path it cannot write aborts the process rather than raising.
No ``except`` clause sees it and nothing can be reported from the call site, so
the write path is probed before ``oriented_export`` is called at all.

The slicer is a GUI application. A flag it does not accept opens a modal dialog
on the user's desktop and returns no text anyone here can read, which is why
``build_argv`` refuses to produce a command line for a binary it does not
recognise -- and why that refusal is caught here and reported as a sentence
rather than left to surface as a traceback.

What the slicer reports back needs reading with some care. Its per-object
bounding boxes are not dimensions: a 16 mm cylinder whose exported vertices
measure exactly 16.000 came back as 17.2, while boxes on the same plate came back
exact. Only the box CENTRE is used, which a symmetric inflation leaves where it
was, and the reported sizes are never quoted as measurements. And a 3MF
``<object>`` carries an id and no name, so ``oriented_export``'s report order is
the only mapping from the user's parts to the objects in the file.

``view_gcode`` is a third half-tool of the same shape: it starts the loopback
viewer server, hands it a G-code path, and opens the user's own browser. What it
returns is for the USER, not for Claude, which cannot see a WebGL canvas.
"""

import os
import re
import subprocess
import sys
import time

from . import print_export
from .geometry import _bbox_dict
from .print_meta import NOT_SET
from .session import PARAM_PATH, _safe_name, _session_job_dir, artifacts_dir
from .tools_export import _resolve_export_objects

#: What the slicer takes as an input model, for a caller slicing a file that
#: already exists instead of exporting one.
_SLICEABLE = ("3mf", "stl")

#: The exported plate's filename inside the job folder. Fixed, because the folder
#: is per job and the name is then one less thing to carry around.
_MODEL_NAME = "model.3mf"

#: Bounds on read_slice_result's optional wait. Both sit well under the bridge's
#: 600 s GuiBusyTimeout, which reports a call as still running rather than
#: cancelling it -- tripping it would put a misleading message in front of Claude
#: while the slice carried on regardless.
_WAIT_DEFAULT = 120
_WAIT_MAX = 480

#: How often the wait looks at the job table. Fine enough that the tool returns
#: promptly once a slice lands, coarse enough to cost nothing while it has not.
_POLL_MS = 250

#: How much of the slicer's log a failure quotes. The useful part is at the end.
_LOG_TAIL_LINES = 25

#: How much of a G-code file is read to find its header comments. The header
#: block comes before the embedded thumbnails, which are the only large thing
#: near the top of one.
_HEADER_BYTES = 262144

_LAYER_COUNT_RE = re.compile(rb"total layer number:\s*(\d+)")
_PRINT_TIME_RE = re.compile(rb"model printing time:\s*([^;\r\n]+);\s*"
                            rb"total estimated time:\s*([^\r\n]+)")

#: How the slicer names an object of the model file it was handed: ``Object_N``,
#: N counting the file's objects from 1. That name is the mapping back to the
#: parts, because the order of its own ``objects`` array is not one -- see
#: :func:`_placed_objects`.
_OBJECT_NAME_RE = re.compile(r"^Object_(\d+)$")

#: FreeCADClaude preferences, every one of them "empty means discover". On a
#: machine with the slicer set up, none needs setting at all.
_PREF_PATH = "SlicerPath"
_PREF_CONF = "SlicerConfPath"
_PREF_PROFILE_DIRS = "SlicerProfileDirs"
_PREF_ARRANGE = "SlicerArrange"
_PREF_ORIENT = "SlicerOrient"
_PREF_NOZZLE = "SlicerNozzle"
#: Where the viewer's built assets are served from. The dev hook for pointing at
#: a Vite build instead of the committed gcode_ui/; empty means the committed one.
_PREF_GCODE_UI = "GcodeUiDir"

#: The preset kinds and the preference each falls back to. One spelling, because
#: a refusal names these to the user and a second copy of the names is how that
#: message goes stale without anything failing.
_PRESET_PREFS = {"machine": "SlicerMachine", "process": "SlicerProcess",
                 "filament": "SlicerFilament"}

#: The file the slicer settings page reads and writes. Outside the session
#: folders, beside sketches/, because a printer choice outlives a conversation
#: and must not be pruned with one.
_SETTINGS_NAME = "slicer.json"

#: What slice_model knew at export time, per job: the world bounding boxes from
#: before anything moved, and oriented_export's report. In memory only, which
#: matches the job table -- slicer_runner's is in memory too, so a job does not
#: survive a FreeCAD restart in the first place.
_exports = {}
_exports_order = []
_KEEP_EXPORTS = 40

#: Cap on the preset names a refusal lists. A full vendor tree carries about
#: ninety processes.
_MAX_LISTED = 20

#: How far the slicer's size for an object may differ from the size FreeCAD
#: measured for the part it is supposed to be. Loose on purpose: this asks
#: whether the two are the same part, not whether the numbers match, and the
#: slicer's sizes are inflated on curved parts (a 16 mm cylinder came back at
#: 17.21, about 8 percent).
_SIZE_TOLERANCE = 0.2

#: How many feature types a plate's time breakdown lists before it is cut short.
#: A real plate reports about thirteen, sorted longest first, so the tail is
#: seconds apiece.
_MAX_FEATURES = 8


# -- preferences and discovery ----------------------------------------------


def _preferences():
    """Every slicer preference, read here on the GUI thread and passed on as
    plain values.

    The nozzle is read as "unset" rather than as 0.4, even though 0.4 is the
    effective default: ``slicer_runner.DEFAULT_NOZZLE`` is that 0.4, and letting
    it supply the value keeps the preference at the bottom of the resolution
    order where it belongs. Reading it as "0.4" would make an unset preference
    outrank a nozzle chosen on the settings page.
    """
    import FreeCAD

    params = FreeCAD.ParamGet(PARAM_PATH)

    def text(key):
        return params.GetString(key, "").strip()

    extra = [os.path.expanduser(part.strip())
             for part in text(_PREF_PROFILE_DIRS).split(os.pathsep) if part.strip()]
    return {
        "binary": os.path.expanduser(text(_PREF_PATH)),
        "conf": os.path.expanduser(text(_PREF_CONF)),
        "profile_dirs": extra,
        "presets": {kind: text(key) for kind, key in _PRESET_PREFS.items()},
        "nozzle": text(_PREF_NOZZLE) or None,
        "arrange": bool(params.GetBool(_PREF_ARRANGE, True)),
        "orient": bool(params.GetBool(_PREF_ORIENT, True)),
        "gcode_ui": os.path.expanduser(text(_PREF_GCODE_UI)),
    }


def _settings_path():
    """``~/FreeCADClaude/slicer.json``, whether or not it exists yet."""
    return os.path.join(artifacts_dir(), _SETTINGS_NAME)


def _discover(prefs):
    """``(discovery, error)`` -- the binary, the slicer's config and the profile
    roots; one of the two is None.

    The order is forced by where the presets live: the system root is derived
    from the executable (``<resources>/profiles/<vendor>``), so the binary has to
    be found first and ``discover_profile_dirs`` has to be given it. Called
    without it that function returns the user roots alone -- an index with no
    system machine presets in it, which resolves to the wrong printer instead of
    failing.
    """
    from .. import slicer_runner

    binary = prefs["binary"]
    if binary and not os.path.isfile(binary):
        return None, (
            f"The {_PREF_PATH} preference points at {binary}, which is not a "
            "file. Point it at the slicer's executable, or clear it to let the "
            "addon look in the usual places. Nothing was exported or sliced."
        )
    if not binary:
        found = slicer_runner.discover_binary()
        if found is None:
            return None, (
                "No slicer was found on this machine. Bambu Studio is the one "
                f"this drives; install it, or set the {_PREF_PATH} preference "
                f"under {PARAM_PATH} to its executable. Nothing was exported."
            )
        binary = found["path"]
    conf = prefs["conf"] or slicer_runner.discover_conf_path() or None
    profile_dirs = slicer_runner.discover_profile_dirs(
        binary=binary, conf_path=conf, extra=prefs["profile_dirs"])
    return {"binary": binary, "conf": conf, "profile_dirs": profile_dirs,
            "index": slicer_runner.index_presets(profile_dirs)}, None


# -- the export ------------------------------------------------------------


def _writable_target(path):
    """None if a file can be created at `path`, else why not.

    ``Mesh.export`` to a path it cannot write ABORTS THE PROCESS. The failure
    escapes Python entirely -- neither ``except Exception`` nor ``except
    BaseException`` sees it -- so nothing can be reported from the call site, and
    this runs on the GUI thread, where an abort takes FreeCAD and any unsaved
    work with it. Finding out first is the only defence there is.
    """
    try:
        with open(path, "wb"):
            pass
    except OSError as exc:
        return (
            f"Nothing was exported or sliced: {path} cannot be written "
            f"({exc.strerror or exc}). Check that the FreeCADClaude artifacts "
            "folder is writable and that the disk is not full."
        )
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return None


def _recorded_boxes(objs):
    """``{name: {"centre", "size", "extents"}}`` in world mm, as things stand.

    Taken before the export moves anything, and kept for read_slice_result. The
    slicer arranges the plate, so the only way to say how far it moved a part is
    to hold the box from before -- the same record-it-when-you-took-it discipline
    as ``_last_annotation["context"]``.
    """
    boxes = {}
    for obj in objs:
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        try:
            box = shape.BoundBox
        except Exception:  # noqa: BLE001 - a box nobody can read is one we skip
            continue
        boxes[obj.Name] = {
            "centre": [round((box.XMin + box.XMax) / 2.0, 3),
                       round((box.YMin + box.YMax) / 2.0, 3),
                       round((box.ZMin + box.ZMax) / 2.0, 3)],
            "size": [round(box.XLength, 3), round(box.YLength, 3),
                     round(box.ZLength, 3)],
            "extents": _bbox_dict(box),
        }
    return boxes


def _remember_export(job_id, record):
    """Keep what slice_model knew at export time, for read_slice_result."""
    _exports[job_id] = record
    _exports_order.append(job_id)
    while len(_exports_order) > _KEEP_EXPORTS:
        _exports.pop(_exports_order.pop(0), None)


def reset_session():
    """Forget the export records of the conversation just ended.

    Called from the chat panel's "New", alongside
    ``slicer_runner.reset_session``, which drops the jobs these describe. This
    table is keyed by job id and would otherwise outlive them, so a new
    conversation whose first job happened to reuse an id would read the previous
    conversation's bounding boxes as its own.
    """
    _exports.clear()
    del _exports_order[:]


def _job_name(label):
    """``<HHMMSS>_<label>``, which is the job folder's name and so the job's id.

    Just the time, not the date, matching the scripts/ archive: names stay short
    and a plain directory listing still sorts chronologically.
    """
    return time.strftime("%H%M%S") + "_" + _safe_name(label, "slice")[:32]


def _argv_text(argv):
    """The command line as one quoted line, for pasting into a terminal."""
    return " ".join(f'"{arg}"' if " " in str(arg) else str(arg) for arg in argv)


# -- slice_model -----------------------------------------------------------


_SLICE_MODEL_SCHEMA = {
    "name": "slice_model",
    "description": (
        "Slice the model with the user's own desktop slicer (Bambu Studio) and "
        "return a job id STRAIGHT AWAY -- this does not wait for the slice. "
        "Each part is exported the way up it is printed (its recorded "
        "PrintDirection), written as one multi-object 3MF so the slicer places "
        "them as separate parts, and handed to the slicer on a background "
        "thread, so FreeCAD stays responsive while it runs.\n"
        "Then call read_slice_result for the outcome: layer count, print time "
        "and where it goes by feature, filament used, or the reason it failed. "
        "A trivial part takes about 20 s and a real one takes minutes, so tell "
        "the user it is running rather than sitting on it.\n"
        "Presets come from the user's own slicer selection with the nozzle "
        "pinned to 0.4, so nothing normally needs naming. Pass 'machine', "
        "'process' or 'filament' -- a preset name, or an absolute path to a "
        "preset JSON -- only when the user asked for something specific; the "
        "result says which level supplied each one.\n"
        "It does not touch the document: the parts are meshed and rotated in a "
        "scratch document, and nothing the user owns moves."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Object internal Names to slice (default: the user's "
                    "selection, else every object in the document with a shape)."
                ),
            },
            "path": {
                "type": "string",
                "description": (
                    "An existing .3mf or .stl to slice as it is, instead of "
                    "exporting from the document. Not to be combined with "
                    "'names'."
                ),
            },
            "machine": {
                "type": "string",
                "description": (
                    "Machine (printer) preset name, or an absolute path to a "
                    "preset JSON. Default: the user's slicer selection, pinned "
                    "to a 0.4 nozzle."
                ),
            },
            "process": {
                "type": "string",
                "description": "Process (print settings) preset name or path.",
            },
            "filament": {
                "type": "string",
                "description": "Filament preset name or path.",
            },
            "orient": {
                "type": "boolean",
                "description": (
                    "Rotate each part onto its recorded PrintDirection before "
                    "exporting (default true). false exports as modelled, "
                    "coordinates included, which is what to use when comparing "
                    "against a slice the user made by hand."
                ),
            },
            "arrange": {
                "type": "boolean",
                "description": (
                    "Let the slicer lay the plate out (default true). It packs "
                    "better than we do, at the cost of toolpath X/Y being plate "
                    "coordinates rather than model coordinates."
                ),
            },
            "deviation": {
                "type": "number",
                "description": (
                    "Mesh deviation in mm for the export (default 0.1, "
                    "FreeCAD's own). Smaller refines curved faces; larger has "
                    "no effect past about 0.05 mm, because angular deflection "
                    "floors it."
                ),
            },
            "copies": {
                "type": "integer",
                "minimum": 1,
                "description": "How many copies of the plate to slice (default 1).",
            },
            "note": {
                "type": "string",
                "description": (
                    "Short line stored with the job, saying what this slice was "
                    "for. It is what a job read back weeks later explains "
                    "itself with."
                ),
            },
        },
        "additionalProperties": False,
    },
}


def _precheck_slice_model(args):
    """Argument shape only -- nothing here reaches the document or the disk."""
    if args.get("names") and args.get("path"):
        return (
            "Pass either 'names' (objects to export and slice) or 'path' (a "
            "file to slice as it is), not both."
        )
    path = str(args.get("path") or "").strip()
    if path:
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        if ext not in _SLICEABLE:
            return (
                f"'{path}' is not something the slicer reads: it takes .3mf and "
                ".stl. Export the geometry as one of those first -- export with "
                "a .3mf path keeps the objects separate, .stl fuses them."
            )
    copies = args.get("copies")
    if copies is not None:
        try:
            if int(copies) < 1:
                return "'copies' is a count of plates, so it cannot be below 1."
        except (TypeError, ValueError):
            return "'copies' must be a whole number, at least 1."
    return None


def _preset_summary(resolved):
    """One line naming each resolved preset and the level that supplied it."""
    parts = []
    for kind in ("machine", "process", "filament"):
        entry = resolved.get(kind)
        parts.append(f"{kind} '{entry['name']}' (from the {entry['source']})"
                     if entry else f"{kind} unresolved")
    return "Presets: " + ", ".join(parts) + f". Nozzle {resolved['nozzle']} mm."


def _no_presets_note(resolved, discovery):
    """Why nothing was sliced, and everything needed to fix it in one turn.

    The discovered lists come along because the alternative is a refusal followed
    by a round trip asking what is installed. Capped, since most of a vendor
    tree's process presets are the vendor's own base profiles.
    """
    from .. import slicer_runner

    missing = ", ".join(resolved["missing"])
    lines = [f"Nothing was sliced: no {missing} preset could be resolved.",
             f"Slicer: {discovery['binary']}."]
    conf = discovery["conf"]
    lines.append(f"Slicer config: {conf}." if conf else
                 "The slicer's own config file was not found, so its current "
                 "selection could not be read.")
    lines.extend("  - " + note for note in resolved["notes"])

    models = slicer_runner.studio_selection(conf).get("models") or []
    if models:
        lines.append("Printers the user has added: " + ", ".join(
            "%s (%s nozzle)" % (entry["model"], "/".join(entry["nozzles"]) or "?")
            for entry in models) + ".")
    for kind in resolved["missing"]:
        names = sorted(discovery["index"].get(kind) or ())
        if not names:
            lines.append(f"No {kind} presets were found under any profile root.")
            continue
        shown = names[:_MAX_LISTED]
        more = "" if len(names) == len(shown) else f" (+{len(names) - len(shown)} more)"
        lines.append(f"Discovered {kind} presets: " + ", ".join(shown) + more + ".")

    # A name the settings page stored is fixed on the settings page, not in a
    # FreeCAD preference -- so say which file holds it and how it is reached.
    stored = slicer_runner.read_settings(_settings_path())
    stale = [kind for kind in resolved["missing"] if stored.get(kind)]
    if stale:
        lines.append(
            "The stored slicer settings name "
            + ", ".join(f"{kind} '{stored[kind]}'" for kind in stale)
            + f", which nothing installed answers to. That file is "
            f"{_settings_path()}, written from the slicer settings page -- the "
            "user opens it with the chat panel's Slicer button, or you can with "
            "view_gcode. It checks a name against what is installed before "
            "storing it, so choosing there cannot leave this state again."
        )
    lines.append(
        "Either name one in this call, or ask the user to set "
        + ", ".join(_PRESET_PREFS[kind] for kind in resolved["missing"])
        + f" under {PARAM_PATH}."
    )
    return "\n".join(lines)


def _plate_lines(report):
    """The per-part half of slice_model's answer: what went on the plate, the way
    up each part went, and what did not go at all.

    "Not set" is named rather than passed over. Exporting an undecided part as
    modelled is right about as often as it is wrong, and this is where the user
    finds out which parts nobody has decided about; "Not printed" is the other
    half of the same argument.
    """
    lines = []
    for entry in report["exported"]:
        how = "rotated onto its print direction" if entry["rotated"] else "as modelled"
        size = "x".join(f"{value:g}" for value in entry["size_mm"])
        lines.append(f"  {entry['name']} ({entry['direction']}) -- {how}, {size} mm")
    if report["omitted"]:
        lines.append("Left off the plate as Not printed: "
                     + ", ".join(report["omitted"]) + ".")
    if report["not_set"]:
        lines.append(
            f"No print direction recorded ('{NOT_SET}'), so exported as modelled: "
            + ", ".join(report["not_set"])
            + ". Nobody has decided which way up these print -- worth asking, "
            "since it decides which faces get supports and which holes bridge."
        )
    for entry in report["skipped"]:
        lines.append(f"Skipped {entry['name']}: {entry['reason']}.")
    return lines


def _run_slice_model(args):
    import FreeCAD

    from .. import slicer_runner

    prefs = _preferences()
    discovery, err = _discover(prefs)
    if err:
        return err

    overrides = {kind: str(args.get(kind) or "").strip()
                 for kind in ("machine", "process", "filament")}
    resolved = slicer_runner.resolve_presets(
        conf_path=discovery["conf"], profile_dirs=discovery["profile_dirs"],
        overrides=overrides, config_path=_settings_path(),
        fallbacks=prefs["presets"], nozzle=prefs["nozzle"],
        index=discovery["index"])
    if resolved["missing"]:
        return _no_presets_note(resolved, discovery)

    given = str(args.get("path") or "").strip()
    doc = FreeCAD.ActiveDocument
    objs = None
    if not given:
        if doc is None:
            return "No active document, and no 'path' to slice instead."
        objs, err = _resolve_export_objects(args, doc)
        if err:
            return err

    label = str(args.get("note") or "").strip() or \
        (os.path.splitext(os.path.basename(given))[0] if given else doc.Label)
    try:
        job_dir = _session_job_dir(_job_name(label))
    except OSError as exc:
        return (f"Could not create a folder for this slice: {exc}. Nothing was "
                "exported or sliced.")

    orient = prefs["orient"] if args.get("orient") is None else bool(args["orient"])
    arrange = prefs["arrange"] if args.get("arrange") is None else bool(args["arrange"])
    copies = max(1, int(args.get("copies") or 1))
    options = {"arrange": arrange, "copies": copies,
               "flavour": slicer_runner.slicer_flavour(discovery["binary"])}

    boxes = {}
    if given:
        if not os.path.isfile(given):
            return f"No file at {given}, so there is nothing to slice."
        model_path, report = given, None
    else:
        model_path = os.path.join(job_dir, _MODEL_NAME)
        err = _writable_target(model_path)
        if err:
            return err
        boxes = _recorded_boxes(objs)
        try:
            report = print_export.oriented_export(
                objs, model_path, deviation=args.get("deviation"), orient=orient)
        except Exception as exc:  # noqa: BLE001 - a failed export is a message
            return (f"The plate could not be exported: {exc!r}. Nothing was "
                    "sliced.")
        if not report["exported"]:
            return (
                "Nothing was written to the plate, so there was nothing to "
                "slice. Every object handed over is either marked 'Not printed' "
                "or has no shape: " + ", ".join(
                    report["omitted"] + [e["name"] for e in report["skipped"]]) + "."
            )

    # The refusal for an unrecognised binary arrives from build_argv, and this is
    # where it is turned into a sentence: the slicer is a GUI application, so a
    # command line built on a guess opens a dialog on the user's desktop instead
    # of reporting anything.
    try:
        job_id = slicer_runner.start_job(discovery["binary"], resolved, model_path,
                                         job_dir, options,
                                         note=str(args.get("note") or "").strip() or None)
    except slicer_runner.UnknownSlicer as exc:
        return f"Nothing was sliced. {exc}"
    except OSError as exc:
        return f"The slice could not be started: {exc}. Nothing is running."

    _remember_export(job_id, {"boxes": boxes, "report": report,
                              "orient": orient, "model_path": model_path})

    record = slicer_runner.job_status(job_id) or {}
    lines = [
        f"Slicing started as job '{job_id}'. This returned straight away -- the "
        "slicer runs in the background and FreeCAD stays usable.",
        f"Job folder: {job_dir}",
    ]
    if report is None:
        lines.append(f"Slicing the file it was given as it is: {model_path}")
    else:
        lines.append(
            f"Plate: {len(report['exported'])} part(s) written to {model_path} at "
            f"{report['deviation']:g} mm deviation"
            + (", each stood up the way it prints." if orient else
               ", exactly as modelled (orient=false).")
        )
        lines.extend(_plate_lines(report))
    lines.append(_preset_summary(resolved))
    lines.extend("  - " + note for note in resolved["notes"])
    if copies > 1:
        lines.append(f"Slicing {copies} copies of the plate.")
    lines.append(
        "The slicer is arranging the plate, so toolpath X/Y will be plate "
        "coordinates rather than model coordinates; read_slice_result reports "
        "how far each part moved."
        if arrange else
        "arrange=false, so the parts keep their exported coordinates -- though "
        "the slicer may still nudge a part that would sit off the bed."
    )
    lines.append("Command line used:\n  " + _argv_text(record.get("argv") or []))
    lines.append(
        "Call read_slice_result for the outcome; it waits up to 120 s for the "
        "slice by default without blocking FreeCAD. Do NOT call slice_model "
        "again while this job is running -- that starts a second slice rather "
        "than checking on this one."
    )
    return "\n".join(lines)


# -- reading the slicer's own report ---------------------------------------


def _number(value):
    """`value` as a float, or None if it is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _numbers(values):
    """`values` as a list of floats, or [] if any of them is not one."""
    if not isinstance(values, (list, tuple)):
        return []
    out = []
    for value in values:
        number = _number(value)
        if number is None:
            return []
        out.append(number)
    return out


def _plates(result):
    """The per-plate records in a ``result.json``.

    Both shapes the slicer writes are accepted -- a list of plates, or a single
    plate's fields at the top level -- because which one appears depends on how
    many plates were sliced, and a summariser that only understood one would
    report an empty result for a file that is perfectly readable.
    """
    if not isinstance(result, dict):
        return []
    for key in ("sliced_plates", "plates"):
        value = result.get(key)
        if isinstance(value, list):
            plates = [entry for entry in value if isinstance(entry, dict)]
            if plates:
                return plates
    if any(key in result for key in ("feature_type_times", "objects", "filaments")):
        return [result]
    return []


def _plate_index(plate, position):
    """The plate's own number, or its position if it does not carry one.

    ``id`` is the spelling a real plate record uses. The top level's own
    ``plate_index`` is not it -- that is 0, the plate ARGUMENT, which means "all
    of them".
    """
    for key in ("plate_index", "index", "plate_id", "id"):
        number = _number(plate.get(key))
        if number is not None:
            return int(number)
    return position


def _feature_times(plate):
    """``[(feature, seconds)]`` longest first.

    Two spellings, since the field is a name-to-seconds mapping in some versions
    and a list of records in others.
    """
    raw = plate.get("feature_type_times")
    items = []
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                items.append((entry.get("name") or entry.get("type"),
                              entry.get("time", entry.get("value"))))
    pairs = []
    for name, raw_seconds in items:
        seconds = _number(raw_seconds)
        if name and seconds is not None:
            pairs.append((str(name), seconds))
    pairs.sort(key=lambda pair: -pair[1])
    return pairs


def _print_seconds(plate):
    """The slicer's estimate for this plate, in seconds, or None."""
    for key in ("total_predication", "main_predication", "prediction"):
        seconds = _number(plate.get(key))
        if seconds is not None and seconds > 0:
            return seconds
    return None


def _filament_usage(plate):
    """``[{"id", "total_g", "main_g"}]`` for the plate's filaments.

    A bare system filament preset has been seen to report an empty id and 0.0 g,
    so an entry with no mass is kept rather than dropped: "the slicer did not
    work out the mass" and "no filament" are different answers.
    """
    entries = []
    raw = plate.get("filaments")
    for entry in raw if isinstance(raw, list) else ():
        if not isinstance(entry, dict):
            continue
        entries.append({"id": str(entry.get("filament_id") or "").strip(),
                        "total_g": _number(entry.get("total_used_g")),
                        "main_g": _number(entry.get("main_used_g"))})
    return entries


def _box_geometry(box):
    """``(centre, size)`` in plate millimetres for one slicer bounding box, or
    ``(None, None)``.

    The slicer writes a box as a corner and a size together --
    ``{x, y, z, width, depth, height}`` -- and ``x``/``y``/``z`` is the MINIMUM
    corner, not the centre: ``z`` is 0.0 on every object of a plate whose parts
    were all dropped onto it.

    A ``{min, max}`` pair is accepted as well, since it costs a line and cannot
    be misread. A flat list of numbers is not: without knowing whether it runs
    min-then-max or axis-pair-by-axis-pair, half the readings put the part
    somewhere it is not, and a centre that is wrong is worse than no centre.
    """
    if not isinstance(box, dict):
        return None, None
    size = _numbers([box.get(key) for key in ("width", "depth", "height")])
    corner = _numbers([box.get(key) for key in ("x", "y", "z")])
    if len(size) == 3 and len(corner) == 3:
        return ([round(low + span / 2.0, 3) for low, span in zip(corner, size)],
                [round(span, 3) for span in size])
    low, high = _numbers(box.get("min")), _numbers(box.get("max"))
    if low and len(low) == len(high):
        return ([round((a + b) / 2.0, 3) for a, b in zip(low, high)],
                [round(abs(b - a), 3) for a, b in zip(low, high)])
    return None, None


def _object_position(entry):
    """Which object of the model file a slicer object record is, 0-based, or
    None if its name does not say."""
    match = _OBJECT_NAME_RE.match(str(entry.get("name") or "").strip())
    return int(match.group(1)) - 1 if match else None


def _sizes_agree(reported, exported):
    """Whether a slicer object is the part we matched it to, or None when there
    is nothing to compare.

    The one independent quantity available: `exported` is FreeCAD's own mesh
    bounding box, measured before the file was written, and nothing the slicer
    reports contributed to it. Compared loosely and as a set of three, because
    the arrange step may turn a part in the plane and because the slicer's own
    sizes are not dimensions -- this asks whether these are the same part, not
    whether the numbers match.
    """
    if len(reported) != 3 or len(exported) != 3:
        return None
    for got, want in zip(sorted(reported), sorted(exported)):
        if want <= 0:
            return None
        if abs(got - want) > _SIZE_TOLERANCE * max(want, 1.0):
            return False
    return True


def _placed_objects(plate):
    """``[{"centre", "size"}]`` for the plate's objects, in the model file's own
    object order.

    **The array's order is not that order.** One real plate lists its objects
    ascending and another lists them descending, so reading them positionally
    matches parts to the wrong boxes on half the plates -- and every number that
    follows is then plausible and wrong. What is reliable is the name: the slicer
    calls the Nth object of the file it was handed ``Object_N``. Its numeric
    ``id`` sorts the same way and stands in when a name is missing; only with
    neither does the array order have to be trusted.
    """
    raw = plate.get("objects")
    records = []
    for entry in raw if isinstance(raw, list) else ():
        if not isinstance(entry, dict):
            continue
        centre, size = _box_geometry(entry.get("bbox"))
        records.append({"centre": centre, "size": size or [],
                        "position": _object_position(entry),
                        "id": _number(entry.get("id"))})
    if records and all(record["position"] is not None for record in records):
        records.sort(key=lambda record: record["position"])
    elif records and all(record["id"] is not None for record in records):
        records.sort(key=lambda record: record["id"])
    return records


def _placement_offsets(recorded, exported, plate):
    """How far the slicer moved each part from where the model sits.

    `recorded` is each object's world bounding box taken at export time, before
    anything moved. `exported` is ``oriented_export``'s report list, and its
    ORDER is the file's object order, since FreeCAD writes no name onto a 3MF
    ``<object>``; :func:`_placed_objects` puts the slicer's own objects into that
    same order, and the two are then matched off it.

    That match is then checked against the sizes, which is the only independent
    quantity there is. A mismatch means the parts and the boxes were lined up
    wrongly, and every offset below it would be a real number pointing at the
    wrong place -- so it is refused rather than reported.

    Only the box centre is used. The slicer's reported box is inflated on curved
    parts, and a symmetric inflation leaves the centre where it was.

    A part that was rotated onto its print direction gets no offset: its axes no
    longer line up with the model's, so a delta between the two centres is a
    distance between two points and not a translation anyone can apply.

    Returns ``[{"name", "model_centre", "plate_centre", "offset", "rotated",
    "reason"}]``, with ``offset`` None and ``reason`` filled in wherever the
    arithmetic would not have meant anything.
    """
    placed = _placed_objects(plate)
    offsets = []
    for index, entry in enumerate(exported or ()):
        if index >= len(placed):
            break
        box = recorded.get(entry["name"]) or {}
        model_centre = box.get("centre")
        record = {"name": entry["name"], "model_centre": model_centre,
                  "plate_centre": placed[index]["centre"],
                  "offset": None, "rotated": bool(entry.get("rotated")),
                  "reason": None}
        offsets.append(record)
        if record["plate_centre"] is None:
            record["reason"] = "the slicer reported no readable bounding box for it"
            continue
        if _sizes_agree(placed[index]["size"], entry.get("size_mm") or []) is False:
            record["reason"] = (
                "the object the slicer reports here measures %s mm, which is not "
                "this part -- the parts and the slicer's objects could not be "
                "lined up, so no position is quoted"
                % "x".join(f"{value:g}" for value in placed[index]["size"])
            )
            record["plate_centre"] = None
            continue
        if model_centre is None:
            record["reason"] = "its world bounding box was not recorded at export time"
            continue
        if record["rotated"]:
            record["reason"] = ("it was rotated onto its print direction first, so "
                                "model axes do not map onto plate axes")
            continue
        record["offset"] = [round(plate_value - model_value, 3) for plate_value,
                            model_value in zip(record["plate_centre"], model_centre)]
    return offsets


# -- the G-code the slicer wrote -------------------------------------------


def _gcode_paths(job_dir):
    """Every G-code file in the job folder, plate order."""
    try:
        names = sorted(os.listdir(job_dir))
    except OSError:
        return []
    return [os.path.join(job_dir, name) for name in names
            if name.lower().endswith((".gcode", ".gcode.3mf"))]


def _gcode_header(path):
    """The first chunk of a G-code file, where its header comments live."""
    try:
        with open(path, "rb") as fh:
            return fh.read(_HEADER_BYTES)
    except OSError:
        return b""


def _header_facts(path):
    """``(layer_count, printing_time, total_time)`` from a G-code header.

    The layer count is the slicer's own ``; total layer number:`` comment rather
    than a count of layer changes: it is what the slicer thinks it wrote, which
    is the number to report when the two could differ. Any of the three is None
    when the comment is not there.
    """
    header = _gcode_header(path)
    layers = _LAYER_COUNT_RE.search(header)
    times = _PRINT_TIME_RE.search(header)
    return (int(layers.group(1)) if layers else None,
            times.group(1).decode("utf-8", "replace").strip() if times else None,
            times.group(2).decode("utf-8", "replace").strip() if times else None)


def _log_tail(path, lines=_LOG_TAIL_LINES):
    """The last `lines` lines of the slicer's log, or None."""
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    tail = [line for line in text.splitlines() if line.strip()][-lines:]
    return "\n".join(tail) or None


def _format_seconds(seconds):
    """Seconds as ``1h 23m`` / ``12m 5s`` / ``40s``."""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _settings_line(result):
    """The print settings the slice actually ran with, or None.

    Reported because these are the numbers, not the names: "why did it slice at
    0.2 mm" is answered by what the slicer used, and a preset name only answers
    it by implication. They sit at the top level of the report, beside the
    outcome, rather than on the plate.

    ``:g`` is doing real work here. The layer height comes back as a float that
    has been through a 32-bit round trip -- 0.20000000298023224 -- and printing
    it as it stands makes a settled number look like a defect.
    """
    parts = []
    height = _number(result.get("layer_height"))
    if height:
        parts.append(f"{height:g} mm layers")
    loops = _number(result.get("wall_loops"))
    if loops:
        parts.append(f"{int(loops)} wall loops")
    density = _number(result.get("sparse_infill_density"))
    if density is not None:
        parts.append(f"{density:g}% sparse infill")
    return "Settings used: " + ", ".join(parts) + "." if parts else None


def _summarise_plate(plate, position):
    """The lines describing one sliced plate: time, where it goes, filament.

    Everything comes out of the slicer's own ``result.json``. Missing fields are
    left out rather than reported as zero -- a plate whose filament mass did not
    resolve has been seen on a working slice, and printing "0.0 g" would read as
    a measurement.
    """
    lines = []
    index = _plate_index(plate, position)
    seconds = _print_seconds(plate)
    head = f"Plate {index}"
    if seconds is not None:
        head += f": estimated {_format_seconds(seconds)} of printing"
    lines.append(head + ".")

    features = _feature_times(plate)
    if features:
        total = sum(seconds for _name, seconds in features)
        parts = []
        for name, value in features[:_MAX_FEATURES]:
            share = f" ({100.0 * value / total:.0f}%)" if total > 0 else ""
            parts.append(f"{name} {_format_seconds(value)}{share}")
        rest = len(features) - len(parts)
        lines.append("  Where the time goes: " + ", ".join(parts)
                     + (f", and {rest} smaller." if rest > 0 else "."))

    for entry in _filament_usage(plate):
        who = f"filament '{entry['id']}'" if entry["id"] else "filament"
        if entry["total_g"]:
            lines.append(f"  Uses {entry['total_g']:g} g of {who}"
                         + (f" ({entry['main_g']:g} g in the part itself)."
                            if entry["main_g"] else "."))
        else:
            lines.append(f"  The slicer reported no mass for {who}, which it does "
                         "when the filament preset carries no density -- the "
                         "slice itself is unaffected.")

    warning = str(plate.get("warning_message") or "").strip()
    if warning:
        lines.append(f"  The slicer warned: {warning}")
    return lines


def _offset_lines(offsets, oriented):
    """The placement-offset half of a successful result.

    Stated because the slicer arranges the plate: every toolpath coordinate is
    offset from the model by wherever it put the part, and a user comparing the
    two without being told quietly concludes something is wrong.
    """
    if not offsets:
        return []
    lines = ["Where the slicer put the parts (plate coordinates, mm):"]
    for entry in offsets:
        plate_centre = entry["plate_centre"]
        where = ("centre " + ", ".join(f"{value:g}" for value in plate_centre)
                 if plate_centre else "not reported")
        line = f"  {entry['name']}: {where}"
        if entry["offset"] is not None:
            delta = ", ".join(f"{value:+g}" for value in entry["offset"][:2])
            line += f" -- add ({delta}) to a model X/Y to find it in the G-code"
            if oriented:
                line += "; its Z was dropped onto the plate"
        elif entry["reason"]:
            line += f" -- no offset: {entry['reason']}"
        lines.append(line)
    lines.append(
        "  The sizes the slicer reports for these boxes are not measurements -- "
        "a 16 mm cylinder came back as 17.2 -- so only the centres are used here."
    )
    return lines


# -- read_slice_result -----------------------------------------------------


_READ_SLICE_RESULT_SCHEMA = {
    "name": "read_slice_result",
    "description": (
        "Collect the outcome of a slice_model job: the G-code files written, the "
        "layer count, the estimated print time and where it goes by feature "
        "type, the filament used, and -- because the slicer arranges the plate "
        "-- how far it moved each part from where the model sits. On a failure "
        "it returns the slicer's own error and the tail of its log.\n"
        "Defaults to the most recent job, so no argument is needed in the normal "
        "flow. It WAITS for the slice by default (up to 120 s, maximum 480) "
        "without blocking FreeCAD; pass wait_seconds: 0 to read the job exactly "
        "as it stands. If it comes back still slicing, tell the user and call "
        "this again -- do NOT call slice_model again, which would start a second "
        "slice rather than checking this one."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "job": {
                "type": "string",
                "description": "Job id from slice_model (default: the most recent).",
            },
            "wait_seconds": {
                "type": "integer",
                "minimum": 0,
                "maximum": _WAIT_MAX,
                "description": (
                    f"How long to wait for a running slice. 0 reads it as it "
                    f"stands; default {_WAIT_DEFAULT}, maximum {_WAIT_MAX}."
                ),
            },
        },
        "additionalProperties": False,
    },
}


def _wait_seconds(args):
    """The wait this call asked for, clamped to the cap."""
    value = args.get("wait_seconds")
    if value is None:
        return _WAIT_DEFAULT
    try:
        return max(0, min(_WAIT_MAX, int(value)))
    except (TypeError, ValueError):
        return _WAIT_DEFAULT


def _wait_for_job(job_id, seconds):
    """Wait up to `seconds` for a job to leave "running". Returns its record.

    A nested ``QEventLoop`` with a polling ``QTimer``, never a sleep: this runs
    on the GUI thread, so an event loop has to be turning for FreeCAD to repaint
    and for the bridge to marshal the next tool call at all. Same shape as
    eval_runner's wait for a turn to finish.

    Qt is imported here rather than at module level, as render.py does, so the
    package stays importable from any thread for its schema data alone. With no
    application to nest inside -- no GUI, or no Qt at all -- the wait degrades to
    a single look at the job table, which reads back as "still slicing" rather
    than as an error.
    """
    from .. import slicer_runner

    record = slicer_runner.job_status(job_id)
    if record is None or record["status"] != "running" or seconds <= 0:
        return record
    try:
        from PySide import QtCore, QtWidgets
    except ImportError:
        return record
    if QtWidgets.QApplication.instance() is None:
        return record

    loop = QtCore.QEventLoop()
    state = {"record": record}
    poll = QtCore.QTimer()
    poll.setInterval(_POLL_MS)

    def _look():
        current = slicer_runner.job_status(job_id)
        state["record"] = current or state["record"]
        if current is None or current["status"] != "running":
            loop.quit()

    poll.timeout.connect(_look)
    poll.start()
    QtCore.QTimer.singleShot(int(seconds * 1000), loop.quit)
    try:
        loop.exec()
    finally:
        poll.stop()
    return slicer_runner.job_status(job_id) or state["record"]


def _still_running(record, waited):
    """The one message that must not read as "the slice failed"."""
    from .. import slicer_runner

    elapsed = _format_seconds(record.get("elapsed") or 0)
    timeout = _format_seconds(record.get("timeout") or slicer_runner.DEFAULT_TIMEOUT)
    waited_text = f" after waiting {_format_seconds(waited)}" if waited else ""
    return (
        f"Job '{record['id']}' is STILL SLICING{waited_text} -- it has been "
        f"running {elapsed}. Nothing has failed and nothing needs resending.\n"
        f"Tell the user it is still going and call read_slice_result again "
        f"(with wait_seconds if you want it to wait longer). The slicer is given "
        f"up on at {timeout}.\n"
        f"Job folder: {record['job_dir']}"
    )


def _failure_report(record):
    """The slicer's own words, then the end of its log.

    The log tail is what makes a failure diagnosable without re-running, and so
    is the command line: a preset that does not suit the machine shows up in the
    argv and nowhere else.
    """
    lines = [f"The slice FAILED (job '{record['id']}', after "
             f"{_format_seconds(record.get('elapsed') or 0)})."]
    error = record.get("error")
    if error:
        lines.append(f"Reason: {error}")
    stated = ((record.get("result") or {}).get("error_string") or "").strip()
    if stated and stated not in (error or ""):
        lines.append(f"The slicer's result.json says: {stated}")
    if record.get("returncode") is not None:
        lines.append(f"Exit code: {record['returncode']}")
    tail = _log_tail(record.get("log_path") or "")
    if tail:
        lines.append(f"The end of {record['log_path']}:\n```\n{tail}\n```")
    else:
        lines.append(f"No log was written at {record.get('log_path')}.")
    lines.append("Command line used:\n  " + _argv_text(record.get("argv") or []))
    lines.append(f"Job folder: {record['job_dir']}")
    return "\n".join(lines)


def _success_report(record):
    """What the slice produced, from the slicer's report and the G-code header."""
    job_dir = record["job_dir"]
    lines = [f"The slice succeeded (job '{record['id']}', "
             f"{_format_seconds(record.get('elapsed') or 0)})."]

    gcode = _gcode_paths(job_dir)
    if gcode:
        lines.append("G-code written:")
        for path in gcode:
            layers, printing, total = _header_facts(path)
            detail = []
            if layers is not None:
                detail.append(f"{layers} layers")
            if printing:
                detail.append(f"{printing} printing")
            if total and total != printing:
                detail.append(f"{total} total")
            size = os.path.getsize(path) / 1024.0 if os.path.isfile(path) else 0.0
            lines.append(f"  {path} ({size:.0f} KB"
                         + (", " + ", ".join(detail) if detail else "") + ")")
    else:
        # The evidence travels with the finding, as it does in _failure_report:
        # "check the log" costs a turn reading a file this already has open.
        lines.append(
            f"No G-code file was written into {job_dir}, even though the slicer "
            "reported success -- so there is nothing to view or to print, and "
            "view_gcode has nothing to show."
        )
        tail = _log_tail(record.get("log_path") or "")
        lines.append(f"The end of {record['log_path']}:\n```\n{tail}\n```"
                     if tail else
                     f"No log was written at {record.get('log_path')} either.")

    result = record.get("result")
    plates = _plates(result)
    settings = _settings_line(result if isinstance(result, dict) else {})
    if settings:
        lines.append(settings)
    for position, plate in enumerate(plates, start=1):
        lines.extend(_summarise_plate(plate, position))
    if not plates:
        lines.append(
            "The slicer wrote no readable result.json, so there is no print "
            "time, feature breakdown or filament figure for this slice -- only "
            "the G-code above."
        )

    export = _exports.get(record["id"]) or {}
    report = export.get("report")
    if plates and report:
        offsets = _placement_offsets(export.get("boxes") or {},
                                     report["exported"], plates[0])
        lines.extend(_offset_lines(offsets, export.get("orient")))
    elif plates and export.get("model_path"):
        lines.append(
            "The plate was a file handed straight to the slicer, so there are no "
            "model coordinates to compare its placement against."
        )

    lines.append(f"Job folder: {job_dir}")
    return "\n".join(lines)


def _run_read_slice_result(args):
    from .. import slicer_runner

    job = str(args.get("job") or "").strip()
    record = slicer_runner.job_status(job) if job else slicer_runner.latest_job()
    if record is None:
        if job:
            return (
                f"There is no slice job called '{job}' in this FreeCAD session. "
                "Job ids are only remembered until FreeCAD restarts; the folder "
                "under the session's slices/ still holds its job.json and log. "
                "Call read_slice_result with no argument for the most recent job."
            )
        return (
            "No slice has been started in this FreeCAD session. Call slice_model "
            "first."
        )

    waited = _wait_seconds(args)
    if record["status"] == "running" and waited > 0:
        record = _wait_for_job(record["id"], waited) or record
    if record["status"] == "running":
        return _still_running(record, waited)
    if record["status"] == "failed":
        return _failure_report(record)
    return _success_report(record)


# -- view_gcode ------------------------------------------------------------


_VIEW_GCODE_SCHEMA = {
    "name": "view_gcode",
    "description": (
        "Open the sliced toolpath in the USER'S OWN desktop browser: an "
        "interactive 3D view of the G-code with per-feature colours and a layer "
        "slider. Defaults to the most recent successful slice, so no argument "
        "is needed after read_slice_result.\n"
        "This is for the user to look at, not for you -- it returns a URL and a "
        "browser window, not a picture, so do not describe what the toolpath "
        "looks like off the back of it. Ask them what they see.\n"
        "The same page carries the slicer settings panel (printer, nozzle, "
        "process, filament), so it is also the answer to 'how do I change which "
        "printer this slices for'. With no slice to show it still opens, for "
        "that panel alone."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "job": {
                "type": "string",
                "description": (
                    "Job id from slice_model (default: the most recent "
                    "successful slice)."
                ),
            },
            "path": {
                "type": "string",
                "description": (
                    "A specific .gcode or .gcode.3mf file to view instead of a "
                    "job's output."
                ),
            },
        },
        "additionalProperties": False,
    },
}


def _viewer_paths(prefs):
    """``(conf_path, profile_dirs)`` for the settings panel's discovery.

    Best effort, and not ``_discover``: that one refuses when no
    slicer is installed, which is right before a slice and wrong here -- the
    panel is where a user with nothing set up finds out what is missing, so the
    page has to open either way.

    The binary is still resolved first, because the system profile root is
    derived from it. Without it ``discover_profile_dirs`` returns the user roots
    alone, and the panel would then offer a plausible-looking subset with no
    system machine presets in it.
    """
    from .. import slicer_runner

    binary = prefs["binary"] if prefs["binary"] and os.path.isfile(prefs["binary"]) \
        else None
    if binary is None:
        found = slicer_runner.discover_binary()
        binary = found["path"] if found else None
    conf = prefs["conf"] or slicer_runner.discover_conf_path() or None
    dirs = slicer_runner.discover_profile_dirs(binary=binary, conf_path=conf,
                                               extra=prefs["profile_dirs"])
    return conf, dirs


def _open_in_browser(url):
    """Launch the user's default browser on `url`, without blocking.

    Popen, never run: this executes on FreeCAD's GUI thread, so waiting on the
    browser would freeze the application for as long as the user left the tab
    open. Same shape and same reason as ``tools_annotate._open_for_editing``.
    Returns a note, since a failure to open is something the user has to be told
    rather than a silent no-op -- the URL is in the result either way.
    """
    kwargs = {}
    if sys.platform == "win32":  # don't flash a console window under the GUI
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if sys.platform == "darwin":
        command = ["open", url]
    elif sys.platform == "win32":
        command = ["cmd", "/c", "start", "", url]
    else:
        command = ["xdg-open", url]
    try:
        subprocess.Popen(command, **kwargs)  # noqa: S603 - our own loopback URL
        return f"Opened it with {command[0]}."
    except Exception:  # noqa: BLE001 - fall through to the Windows shell open
        pass
    if sys.platform == "win32":
        try:
            os.startfile(url)  # noqa: S606 - documented Windows shell open
            return "Opened it with the default Windows handler."
        except Exception as exc:  # noqa: BLE001
            return f"Could not open a browser automatically ({exc!r})."
    return "Could not open a browser automatically."


def _gcode_to_show(args):
    """``(path, note)`` -- the G-code file to publish, and what to say about it.

    Either may be None. A missing file is a note rather than a refusal: the page
    also carries the settings panel, and configuring the printer is what comes
    BEFORE the first slice -- refusing here would mean the one way to reach that
    panel is to have already succeeded at the thing it configures.
    """
    from .. import slicer_runner

    given = str(args.get("path") or "").strip()
    if given:
        path = os.path.abspath(os.path.expanduser(given))
        if not os.path.isfile(path):
            return None, f"There is no file at {path}, so nothing was loaded."
        return path, None

    job = str(args.get("job") or "").strip()
    record = slicer_runner.job_status(job) if job else slicer_runner.latest_job()
    if record is None:
        return None, (
            f"There is no slice job called '{job}' in this FreeCAD session."
            if job else
            "No slice has been run in this FreeCAD session, so there is no "
            "toolpath to show yet."
        )
    if record["status"] == "running":
        return None, (f"Job '{record['id']}' is still slicing, so there is no "
                      "G-code to show yet -- call read_slice_result for it.")
    if record["status"] != "succeeded":
        return None, (f"Job '{record['id']}' failed, so it wrote no G-code -- "
                      "call read_slice_result for the reason.")
    paths = _gcode_paths(record["job_dir"])
    if not paths:
        return None, (f"Job '{record['id']}' succeeded but left no G-code file "
                      f"in {record['job_dir']}.")
    extra = ""
    if len(paths) > 1:
        extra = (" It sliced %d plates; the others are in the job folder and "
                 "can be opened with view_gcode's 'path'." % len(paths))
    return paths[0], f"Showing job '{record['id']}'." + extra


def _start_viewer_server(prefs):
    """Start the loopback viewer server. Returns an error sentence, or None.

    The two ways in -- the ``view_gcode`` tool and the chat panel's button --
    share this so a failure reads the same either way. Both failures it can
    report are ones the user has to act on: an unbuilt viewer folder, and a
    listener that would not bind.
    """
    from .. import gcode_server

    conf, profile_dirs = _viewer_paths(prefs)
    try:
        gcode_server.start(ui_dir=prefs["gcode_ui"] or None,
                           settings_path=_settings_path(),
                           conf_path=conf, profile_dirs=profile_dirs)
    except RuntimeError as exc:
        return f"The G-code viewer could not be started: {exc}"
    except OSError as exc:
        return (f"The G-code viewer could not be started: {exc}. Nothing is "
                "listening and nothing was opened.")
    return None


def open_settings_page():
    """Open the viewer page with nothing loaded, for the slicer settings panel.

    The chat panel's own way in, beside ``view_gcode``'s. The panel is where the
    printer, nozzle, process and filament are chosen, and that choice comes
    before the first slice -- so reaching it must not require a slice, or a
    conversation.

    Returns ``(url, note)``. A None `url` means the page could not be served at
    all and `note` says why; otherwise `note` says how the browser was opened,
    or that it could not be and the URL has to be pasted.
    """
    from .. import gcode_server

    error = _start_viewer_server(_preferences())
    if error:
        return None, error
    url = gcode_server.page_url()
    if not url:
        return None, ("The G-code viewer server reported no address, so there "
                      "is nothing to open.")
    return url, _open_in_browser(url)


def _run_view_gcode(args):
    from .. import gcode_server

    prefs = _preferences()
    path, note = _gcode_to_show(args)
    if path is None and str(args.get("path") or "").strip():
        # An explicitly named file that is not there is the one refusal here:
        # opening a different view than the one asked for would be worse than
        # saying so.
        return note

    error = _start_viewer_server(prefs)
    if error:
        return error

    record = gcode_server.publish(path) if path else None
    url = gcode_server.page_url(record["id"] if record else None)
    opened = _open_in_browser(url)

    lines = []
    if path:
        size = os.path.getsize(path) / 1024.0 if os.path.isfile(path) else 0.0
        lines.append(f"Opened the toolpath viewer on {path} ({size:.0f} KB).")
    else:
        lines.append("Opened the G-code viewer with nothing loaded.")
    if note:
        lines.append(note)
    lines.append(f"{opened} If it did not appear, the user can paste this in:\n  {url}")
    lines.append(
        "THIS IS FOR THE USER TO LOOK AT, NOT FOR YOU -- it is a 3D canvas in "
        "their browser and you cannot see it. Do not describe the toolpath; ask "
        "them what they see, or use capture_view for something you can see."
    )
    lines.append(
        "The same page has the slicer settings panel behind the toolbar's "
        "Settings button -- printer, nozzle, process, filament, and whether to "
        "orient and arrange. What they choose there is stored in "
        f"{_settings_path()} and outranks the slicer's own selection on the "
        "next slice. The chat panel's Slicer button opens the same page, so "
        "they can change it any time without asking you."
    )
    return "\n".join(lines)
