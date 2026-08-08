# SPDX-License-Identifier: LGPL-2.1-or-later
"""Drive the desktop slicer as a subprocess, and resolve the presets it needs.

Stdlib only, like ``device_server.py``: no Qt, no FreeCAD, not even indirectly.
A slice takes about twenty seconds for a trivial part and much longer for a real
one, and FreeCAD's API has to be driven from its GUI thread -- so the slicer
runs on a daemon thread that touches neither. Keeping FreeCAD out of the import
list is what makes that true by construction, and it is what lets the whole
module be tested under a plain interpreter (see ``eval/test_slicer_runner.py``).

Everything environmental is therefore passed in: the binary, the profile roots,
the slicer's config file, the settings file, the job directory. Each of those
comes from a FreeCAD preference or a session folder, and only the GUI thread may
read those -- so a function here that looked one up would put a preference read
on a worker thread and take the module's testability with it.

    tools_slice (GUI thread)                        this module
      resolve objects, export the 3MF
      resolve_presets(...) --------------------->   pure functions over paths
      start_job(...) ------------------------->     a daemon thread
                                                     the slicer, as a subprocess
      read_slice_result -> job_status(...) <----    the job table
                                                   set_done_hook fires here

Preset resolution is the substantial half. Bambu Studio keeps its live GUI
selection in ``BambuStudio.conf``, so the printer does not have to be configured
twice -- but that selection is whatever the user last had open, and a session
left on a 0.2 nozzle has a 0.2 machine, a 0.2 process and a 0.2 filament
selected. So the nozzle is pinned (0.4 unless asked otherwise) and all three
presets are re-derived together against the machine preset that matches it:
snapping the machine alone leaves the other two incompatible with it.
Compatibility is read out of each preset's own ``compatible_printers`` field and
never parsed out of its name. Which of the four resolution levels supplied each
preset is reported, because "why did it slice at 0.2mm" is otherwise
unanswerable.

The slicer owns its config and its presets; we only read them. That file may be
half-written while the slicer is running, so every read of it degrades to the
next resolution level instead of raising.
"""

import copy
import glob
import json
import os
import shutil
import subprocess
import sys
import threading
import time

#: The two slicers this drives. They are forks of each other and their command
#: lines have diverged, so which one it is decides which flags it gets.
BAMBU = "bambu"
ORCA = "orca"

_FLAVOUR_LABELS = {BAMBU: "Bambu Studio", ORCA: "OrcaSlicer"}


class UnknownSlicer(ValueError):
    """The binary is not one whose command line is known (see :data:`_FLAGS`).

    Raised by :func:`build_argv`, and so by :func:`start_job` before it starts
    anything. It carries the message the user needs, since this is the one
    failure the slicer itself can never report.
    """

#: The nozzle a machine preset is snapped to when the caller names none. It is
#: the vendor's own base: the 0.2, 0.6 and 0.8 presets inherit from the 0.4 one
#: and the model-level JSON lists 0.4 first.
DEFAULT_NOZZLE = "0.4"

#: Where a resolved preset came from, most specific first. Reported per preset.
SOURCE_ARGUMENT = "argument"
SOURCE_CONFIG = "slicer.json"
SOURCE_STUDIO = "studio"
SOURCE_MACHINE_DEFAULT = "machine default"
SOURCE_FALLBACK = "fallback"

#: A preset kind is also the folder it lives in, under every profile root.
_KINDS = ("machine", "process", "filament")

#: The system tree is flat (``<root>/filament/<name>.json``) and a user tree
#: nests a level deeper (``user/<id>/filament/base/<name>.json``), so the glob
#: needs a ``**`` and ``recursive=True`` to reach both.
_PRESET_GLOB = os.path.join("**", "*.json")


def _read_json(path):
    """Parse the JSON at `path`, or None if it cannot be read or parsed.

    Every read of a slicer-owned file goes through this. The config may be
    mid-write and a preset may be truncated, and a resolution that raised would
    turn a recoverable miss into a failed slice.
    """
    if not path:
        return None
    try:
        with open(path, "rb") as fh:
            return json.loads(fh.read().decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None


def _split_list(value):
    """A ``;``-joined string, or a list, as a list of non-empty strings.

    The same field is spelled both ways across these files: a machine preset's
    ``nozzle_diameter`` is ``["0.4"]`` while a model's is ``"0.4;0.2;0.6;0.8"``.
    """
    if isinstance(value, str):
        items = value.split(";")
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        return []
    return [str(item).strip() for item in items if str(item).strip()]


def _as_name(value):
    """`value` as a preset name, or None if it is not usable as one."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


# -- finding the slicer, its config and its presets -------------------------
# Read-only filesystem probes, every candidate list overridable by the caller.


def slicer_flavour(path):
    """Which slicer the executable at `path` is, by its name, or None."""
    name = os.path.basename(str(path or "")).lower()
    if "orca" in name:
        return ORCA
    if "bambu" in name:
        return BAMBU
    return None


def _mac_candidates():
    return ["/Applications/BambuStudio.app/Contents/MacOS/BambuStudio",
            "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer"]


def _windows_candidates():
    roots = [os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
             os.environ.get("LOCALAPPDATA"), "C:\\Program Files"]
    found = []
    for folder, exe in (("Bambu Studio", "bambu-studio.exe"),
                        ("OrcaSlicer", "orca-slicer.exe")):
        for root in roots:
            if root:
                found.append(os.path.join(root, folder, exe))
    return found


def _linux_candidates():
    found = []
    for command, appimage in (("bambu-studio", "BambuStudio"),
                              ("orca-slicer", "OrcaSlicer")):
        on_path = shutil.which(command)
        if on_path:
            found.append(on_path)
        found.append(os.path.join("/opt", appimage, command))
        found.extend(sorted(glob.glob(
            os.path.expanduser("~/Applications/%s*.AppImage" % appimage))))
        found.extend(sorted(glob.glob("/opt/%s*.AppImage" % appimage)))
    return found


def _binary_candidates():
    """Every place a slicer might be, Bambu Studio's places first."""
    if sys.platform == "darwin":
        return _mac_candidates()
    if os.name == "nt":
        return _windows_candidates()
    return _linux_candidates()


def discover_binary(candidates=None):
    """The slicer executable to drive, or None if none is installed.

    Returns ``{"path", "flavour", "label"}``. Bambu Studio comes ahead of
    OrcaSlicer because the invocation here is verified against it, and it is the
    only flavour :func:`build_argv` will build a command line for. The flavour is
    ``None`` for a file whose name says neither.

    Every candidate is a read-only filesystem probe. Nothing here starts what it
    finds: these are GUI applications, and running one puts a window on the
    user's screen.
    """
    for path in (candidates if candidates is not None else _binary_candidates()):
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            flavour = slicer_flavour(path)
            return {"path": path, "flavour": flavour,
                    "label": _FLAVOUR_LABELS.get(flavour, "slicer")}
    return None


def _config_base():
    """The per-user config folder the slicer keeps its own settings in."""
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support")
    if os.name == "nt":
        return os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


def _conf_candidates():
    base = _config_base()
    return [os.path.join(base, "BambuStudio", "BambuStudio.conf"),
            os.path.join(base, "OrcaSlicer", "OrcaSlicer.conf")]


def discover_conf_path(candidates=None):
    """The slicer's own config file, or None. Only the macOS path is verified."""
    for path in (candidates if candidates is not None else _conf_candidates()):
        if path and os.path.isfile(path):
            return path
    return None


#: Where the bundled ``profiles`` folder sits relative to the executable, per
#: install layout: a macOS app bundle, a Windows or Linux install directory, and
#: a distribution package that splits code from data.
_RESOURCE_PROFILES = (
    os.path.join("..", "Resources", "profiles"),
    os.path.join("resources", "profiles"),
    os.path.join("..", "share", "BambuStudio", "resources", "profiles"),
    os.path.join("..", "share", "OrcaSlicer", "resources", "profiles"),
)


def _resources_profiles(binary):
    """The slicer's bundled ``profiles`` folder, from its executable, or None."""
    if not binary:
        return None
    here = os.path.dirname(os.path.abspath(binary))
    for relative in _RESOURCE_PROFILES:
        candidate = os.path.normpath(os.path.join(here, relative))
        if os.path.isdir(candidate):
            return candidate
    return None


def discover_profile_dirs(binary=None, conf_path=None, extra=()):
    """Profile roots in precedence order, highest first.

    A root is a folder holding ``machine``/``process``/``filament`` subfolders:
    ``<config>/user/<id>`` on the user side and
    ``<resources>/profiles/<vendor>`` on the system side. The user roots come
    first, which is the slicer's own precedence -- a user preset shadows a
    system one of the same name. `extra` comes ahead of both, for a root the
    user configured by hand.

    Only the vendors the user has actually added a printer for are included,
    read from the config's ``models``. With no config to read, every vendor tree
    is offered instead.
    """
    roots = [path for path in extra if path and os.path.isdir(path)]
    if conf_path:
        user_root = os.path.join(os.path.dirname(conf_path), "user")
        roots.extend(sorted(path for path in glob.glob(os.path.join(user_root, "*"))
                            if os.path.isdir(path)))
    base = _resources_profiles(binary)
    if not base:
        return roots
    vendors = []
    for model in studio_selection(conf_path).get("models") or ():
        vendor = model.get("vendor")
        root = os.path.join(base, vendor) if vendor else None
        if root and root not in vendors and os.path.isdir(root):
            vendors.append(root)
    if not vendors:
        vendors = [path for path in sorted(glob.glob(os.path.join(base, "*")))
                   if os.path.isdir(os.path.join(path, "machine"))]
    roots.extend(vendors)
    return roots


def studio_selection(conf_path):
    """The slicer's live GUI selection, plus the printers the user has added.

    ``{}`` when the file is missing, unreadable, or not the shape expected, so
    the caller's next resolution level takes over. Keys: ``machine``,
    ``process``, ``filament`` (the first extruder's -- the file carries one
    entry per extruder or AMS slot, and a single-material slice takes the
    first), ``filaments`` (all of them), and ``models``, as
    ``[{"model", "vendor", "nozzles": [...]}]``.
    """
    data = _read_json(conf_path)
    if not isinstance(data, dict):
        return {}
    presets = data.get("presets")
    presets = presets if isinstance(presets, dict) else {}
    filaments = _split_list(presets.get("filaments"))
    models = []
    declared = data.get("models")
    for entry in declared if isinstance(declared, (list, tuple)) else ():
        if not isinstance(entry, dict) or not _as_name(entry.get("model")):
            continue
        models.append({"model": entry["model"].strip(),
                       "vendor": (entry.get("vendor") or "").strip(),
                       "nozzles": _split_list(entry.get("nozzle_diameter"))})
    selection = {"machine": _as_name(presets.get("machine")),
                 "process": _as_name(presets.get("process")),
                 "filament": filaments[0] if filaments else None,
                 "filaments": filaments,
                 "models": models}
    if not models and not any(selection[kind] for kind in _KINDS):
        return {}
    return selection


def index_presets(profile_dirs):
    """``{kind: {preset name: absolute path}}`` over `profile_dirs`.

    The name is the filename without ``.json``, which is how the config refers
    to a preset. The first root to offer a name keeps it, so the caller's
    precedence order is what decides between a user preset and a system one.

    The ``machine`` folder holds two kinds of file -- the machine presets and
    the model-level ``<model>.json`` -- and they are told apart by their
    ``type`` field where that matters.
    """
    index = {kind: {} for kind in _KINDS}
    for root in profile_dirs or ():
        for kind in _KINDS:
            pattern = os.path.join(root, kind, _PRESET_GLOB)
            for path in sorted(glob.glob(pattern, recursive=True)):
                name = os.path.splitext(os.path.basename(path))[0]
                index[kind].setdefault(name, os.path.abspath(path))
    return index


def _lookup(kind, value, index):
    """``(name, path)`` for a preset name or a path to a JSON; path may be None.

    A path always wins, so a preset discovery misses -- one under a profile root
    nobody knows about, or a name the scheme does not match -- is still
    reachable.
    """
    text = str(value).strip()
    if text.lower().endswith(".json") or os.path.isabs(text):
        path = os.path.abspath(os.path.expanduser(text))
        if os.path.isfile(path):
            data = _read_json(path) or {}
            name = _as_name(data.get("name")) or \
                os.path.splitext(os.path.basename(path))[0]
            return name, path
        return text, None
    return text, index[kind].get(text)


def _field(path, key, kind, index, default=None):
    """The value of `key` on the preset at `path`, following ``inherits``.

    A preset carries only the fields it changes, so a user copy of a system one
    declares almost nothing -- its parent is named by ``inherits`` and resolved
    by name through the same index. The walk stops on a repeat, since a
    hand-edited chain can point back at itself.
    """
    seen = set()
    while path and path not in seen:
        seen.add(path)
        data = _read_json(path) or {}
        if key in data:
            return data[key]
        parent = data.get("inherits")
        path = index[kind].get(parent) if isinstance(parent, str) else None
    return default


def _compatible(path, machine_name, kind, index):
    """Whether the preset at `path` may be used with the machine preset named
    `machine_name`.

    ``compatible_printers`` is the ground truth. The convention that an
    unsuffixed preset name means a 0.4 nozzle holds today, but a name is not
    what this keys off. A preset that declares no list anywhere in its
    ``inherits`` chain is unconstrained.
    """
    printers = _field(path, "compatible_printers", kind, index)
    if isinstance(printers, str):
        printers = [printers]
    if not printers:
        return True
    return machine_name in printers


def _selectable(path):
    """Whether a preset is one a user may choose.

    ``instantiation: "false"`` marks the internal base profiles the vendor tree
    is built out of -- ``fdm_process_common`` and the like. They declare no
    ``compatible_printers``, so a compatibility filter alone keeps them, and
    they outnumber the real presets several to one. Only the preset's own
    declaration counts: a user copy of a selectable preset declares nothing and
    is selectable.
    """
    data = _read_json(path) or {}
    return str(data.get("instantiation", "true")).strip().lower() != "false"


def _machine_table(index):
    """``{(printer model, nozzle): machine preset name}``.

    Nozzle size is a field, so this is built by reading every machine preset and
    never by parsing names. The model-level ``<model>.json`` sits in the same
    folder and lists every nozzle the printer supports, so it is skipped on its
    ``type``; taking it for a preset would match every nozzle at once.
    """
    table = {}
    for name in sorted(index["machine"]):
        path = index["machine"][name]
        data = _read_json(path)
        if not isinstance(data, dict) or data.get("type") != "machine":
            continue
        model = _as_name(_field(path, "printer_model", "machine", index)) or ""
        nozzles = _split_list(_field(path, "nozzle_diameter", "machine", index))
        variant = _as_name(_field(path, "printer_variant", "machine", index))
        if variant:
            nozzles.append(variant)
        for nozzle in nozzles:
            table.setdefault((model, nozzle), name)
    return table


def _machine_for(index, model, nozzle, table=None):
    """``(name, path)`` of the machine preset for `model` at `nozzle`."""
    table = _machine_table(index) if table is None else table
    name = table.get((model or "", nozzle))
    if name is None and not model:
        # Nothing says which printer, so any machine at that nozzle will do.
        for (_model, size), candidate in sorted(table.items()):
            if size == nozzle:
                name = candidate
                break
    if name is None:
        return None, None
    return name, index["machine"][name]


def _machine_default(path, kind, index):
    """The process or filament preset name a machine preset declares as its
    default, or None. ``default_filament_profile`` is a list, one per
    extruder."""
    key = "default_print_profile" if kind == "process" else "default_filament_profile"
    value = _field(path, key, "machine", index)
    if isinstance(value, str):
        return _as_name(value)
    if isinstance(value, (list, tuple)) and value:
        return _as_name(value[0])
    return None


def _model_materials(index, model):
    """The model-level ordered filament preference list, for a dropdown."""
    path = index["machine"].get(model)
    data = _read_json(path) if path else None
    if not isinstance(data, dict) or data.get("type") != "machine_model":
        return []
    return _split_list(data.get("default_materials"))


# -- resolution -------------------------------------------------------------


def read_settings(path):
    """``slicer.json`` as a dict, or ``{}``.

    The settings page writes this file and this module reads it, with no hook
    and no HTTP in between: the choice has to survive a restart and is not
    per-session, and neither side needs FreeCAD to be mid-turn. Never raises,
    since the page may be writing it right now.
    """
    data = _read_json(path)
    return data if isinstance(data, dict) else {}


def _studio_model(studio, index):
    """The printer model the slicer's selection is for, or the first one added."""
    name = studio.get("machine")
    path = index["machine"].get(name) if name else None
    declared = _as_name(_field(path, "printer_model", "machine", index)) \
        if path else None
    if declared:
        return declared
    models = studio.get("models") or []
    return models[0]["model"] if models else None


def resolve_presets(conf_path=None, profile_dirs=(), overrides=None,
                    config_path=None, fallbacks=None, nozzle=None,
                    printer=None, index=None):
    """Resolve the machine, process and filament presets to absolute JSON paths.

    Four levels, most specific first:

        1. `overrides`   what the caller asked for -- a preset name, or a path
                         to a JSON, which always wins
        2. `config_path` ``~/FreeCADClaude/slicer.json``, what the settings page
                         last stored
        3. `conf_path`   the slicer's own live selection, snapped to `nozzle`
        4. `fallbacks`   names to fall back on when everything above is missing

    A name at level 1 or 2 that no preset answers to leaves that kind
    unresolved and says so, so the caller can refuse. Replacing it with
    something else that happens to slice is how a stale stored name turns into a
    part printed at the wrong layer height.

    Level 3 re-derives all three presets together, against the machine preset
    matching `nozzle` (:data:`DEFAULT_NOZZLE` when the caller and the settings
    file both name none). The slicer's own process and filament are kept only if
    their ``compatible_printers`` names that machine; otherwise the machine's
    declared defaults are used and the substitution is reported.

    Returns::

        {"machine": {"name", "path", "source"} or None,
         "process": ..., "filament": ...,
         "printer": the printer model the machine was chosen for,
         "nozzle": the nozzle it was pinned to,
         "notes": [str, ...],     # every substitution and every miss
         "missing": [kind, ...]}  # what there is nothing to slice with
    """
    overrides = {kind: value for kind, value in (overrides or {}).items() if value}
    fallbacks = {kind: value for kind, value in (fallbacks or {}).items() if value}
    settings = read_settings(config_path)
    target = str(nozzle or settings.get("nozzle") or DEFAULT_NOZZLE).strip()
    studio = studio_selection(conf_path)
    if index is None:
        index = index_presets(profile_dirs)

    notes = []
    if conf_path and not studio:
        notes.append("%s could not be read, so the slicer's own selection was "
                     "not used" % os.path.basename(conf_path))
    resolved = {kind: None for kind in _KINDS}
    stale = set()

    for source, values in ((SOURCE_ARGUMENT, overrides), (SOURCE_CONFIG, settings)):
        for kind in _KINDS:
            if resolved[kind] is not None or kind in stale or not values.get(kind):
                continue
            name, path = _lookup(kind, values[kind], index)
            if path:
                resolved[kind] = {"name": name, "path": path, "source": source}
            else:
                stale.add(kind)
                notes.append("the %s preset '%s' from the %s is not among the "
                             "discovered presets, so none was resolved for it"
                             % (kind, name, source))

    model = _as_name(printer) or _as_name(settings.get("printer")) \
        or _studio_model(studio, index)

    if resolved["machine"] is None and "machine" not in stale and (studio or model):
        selected = studio.get("machine")
        path = index["machine"].get(selected) if selected else None
        nozzles = _split_list(_field(path, "nozzle_diameter", "machine", index)) \
            if path else []
        if path and target in nozzles:
            resolved["machine"] = {"name": selected, "path": path,
                                   "source": SOURCE_STUDIO}
        else:
            name, snapped = _machine_for(index, model, target)
            if snapped:
                resolved["machine"] = {"name": name, "path": snapped,
                                       "source": SOURCE_STUDIO}
                if selected:
                    notes.append("the slicer had '%s' selected; pinned to the "
                                 "%s mm nozzle preset '%s'"
                                 % (selected, target, name))
            else:
                notes.append("no machine preset for %s at a %s mm nozzle is among "
                             "the discovered presets"
                             % (model or "that printer", target))

    machine = resolved["machine"]
    for kind in ("process", "filament"):
        if resolved[kind] is not None or kind in stale or machine is None:
            continue
        selected = studio.get(kind)
        name, path = _lookup(kind, selected, index) if selected else (None, None)
        if path and _compatible(path, machine["name"], kind, index):
            resolved[kind] = {"name": name, "path": path, "source": SOURCE_STUDIO}
            continue
        if not selected:
            reason = "the slicer had no %s selected" % kind
        elif path is None:
            reason = ("the slicer's %s '%s' is not among the discovered presets"
                      % (kind, selected))
        else:
            reason = ("the slicer's %s '%s' is not compatible with '%s'"
                      % (kind, selected, machine["name"]))
        default = _machine_default(machine["path"], kind, index)
        dname, dpath = _lookup(kind, default, index) if default else (None, None)
        if dpath:
            resolved[kind] = {"name": dname, "path": dpath,
                              "source": SOURCE_MACHINE_DEFAULT}
            notes.append("%s, so that machine's default '%s' was used"
                         % (reason, dname))
        else:
            notes.append("%s, and that machine declares no usable default" % reason)

    for kind in _KINDS:
        if resolved[kind] is not None or kind in stale or not fallbacks.get(kind):
            continue
        name, path = _lookup(kind, fallbacks[kind], index)
        if path:
            resolved[kind] = {"name": name, "path": path, "source": SOURCE_FALLBACK}
        else:
            notes.append("the fallback %s preset '%s' is not among the discovered "
                         "presets" % (kind, name))

    return {"machine": resolved["machine"], "process": resolved["process"],
            "filament": resolved["filament"], "printer": model, "nozzle": target,
            "notes": notes,
            "missing": [kind for kind in _KINDS if resolved[kind] is None]}


def discover_options(conf_path, profile_dirs, machine=None, index=None):
    """What a settings UI has to offer: printers, nozzles, and the presets one
    machine can use.

    Every process and filament preset under `profile_dirs` is read, because both
    the filters are fields inside the files: ``compatible_printers`` for the
    machine, and ``instantiation`` for whether a preset is one a user may pick at
    all (see :func:`_selectable`). That is a few thousand small reads on a full
    vendor tree, which belongs behind a request the user made rather than inside
    a slice.

    `machine` is the machine preset the lists are filtered for, defaulting to
    the slicer's own selection and then to the first printer's
    :data:`DEFAULT_NOZZLE` machine. Returns::

        {"printers": [{"model", "vendor", "nozzles": [...],
                       "machines": {nozzle: machine preset name},
                       "materials": [filament name, ...]}],
         "selected": {"machine", "process", "filament"},
         "machine": the machine the lists are for, or None,
         "processes": [{"name", "path", "default"}],
         "filaments": [{"name", "path", "default"}]}
    """
    if index is None:
        index = index_presets(profile_dirs)
    studio = studio_selection(conf_path)
    table = _machine_table(index)

    printers = []
    for entry in studio.get("models") or ():
        model = entry["model"]
        machines = {}
        for nozzle in entry.get("nozzles") or ():
            name = table.get((model, nozzle))
            if name:
                machines[nozzle] = name
        printers.append({"model": model, "vendor": entry.get("vendor", ""),
                         "nozzles": list(entry.get("nozzles") or ()),
                         "machines": machines,
                         "materials": _model_materials(index, model)})

    chosen = _as_name(machine)
    if chosen is None:
        selected = studio.get("machine")
        chosen = selected if selected in index["machine"] else None
    if chosen is None and printers:
        machines = printers[0]["machines"]
        chosen = machines.get(DEFAULT_NOZZLE) or \
            (sorted(machines.values())[0] if machines else None)

    lists = {"process": [], "filament": []}
    machine_path = index["machine"].get(chosen) if chosen else None
    for kind in ("process", "filament"):
        default = _machine_default(machine_path, kind, index) if machine_path else None
        for name in sorted(index[kind]):
            path = index[kind][name]
            if chosen and not _compatible(path, chosen, kind, index):
                continue
            if not _selectable(path):
                continue
            lists[kind].append({"name": name, "path": path,
                                "default": name == default})

    return {"printers": printers,
            "selected": {kind: studio.get(kind) for kind in _KINDS},
            "machine": chosen,
            "processes": lists["process"], "filaments": lists["filament"]}


# -- the command line -------------------------------------------------------

#: Which flags each slicer takes. Bambu Studio is the only entry: these are GUI
#: applications, so a flag one of them does not take opens a modal dialog on the
#: user's desktop and prints nothing anyone here can read -- there is no error to
#: report and nothing to retry against. OrcaSlicer is identified but absent:
#: it is a fork of Bambu Studio and its command line is unverified, so it gets no
#: command line at all until someone checks the flags.
_FLAGS = {
    BAMBU: {"settings": "--load-settings", "filaments": "--load-filaments",
            "arrange": "--arrange", "ensure_on_bed": "--ensure-on-bed",
            "repetitions": "--repetitions"},
}

#: ``--slice`` takes a plate number, 0 meaning every plate. There is no
#: ``--export-gcode``: the G-code is written into ``--outputdir``.
_ALL_PLATES = "0"

#: ``--load-settings`` takes the machine and process presets as one argument,
#: joined with a semicolon. Verified on macOS and unverified on Windows, where
#: ``;`` is also the path separator.
_SETTINGS_SEP = ";"


def _preset_path(presets, kind):
    """The JSON path for `kind`, from a :func:`resolve_presets` result or from a
    plain ``{kind: path}`` mapping."""
    entry = (presets or {}).get(kind)
    if isinstance(entry, dict):
        entry = entry.get("path")
    return str(entry) if entry else None


def _flags_for(flavour, binary):
    """The flag table for `flavour`, or raise :class:`UnknownSlicer`."""
    flags = _FLAGS.get(flavour)
    if flags is not None:
        return flags
    if flavour == ORCA:
        reason = ("OrcaSlicer's command line has not been verified against this "
                  "addon, so no slicing arguments are built for it")
    else:
        reason = "it is not a slicer whose command line is known here"
    raise UnknownSlicer(
        "%s: %s. Point the SlicerPath preference at Bambu Studio, or pass "
        "flavour=%r if that binary is known to take Bambu Studio's flags."
        % (binary, reason, BAMBU))


def build_argv(binary, presets, model_path, outputdir, arrange=True,
               ensure_on_bed=True, copies=1, flavour=None):
    """The command line that slices `model_path` into `outputdir`.

    `binary` is the executable, or a list whose first element is one. `presets`
    is a :func:`resolve_presets` result or a ``{kind: path}`` mapping; a preset
    it has nothing for is left off the command line.

    `flavour` defaults to whatever the binary's own name says it is. Anything but
    ``bambu`` raises :class:`UnknownSlicer` and no command line is produced at
    all, because a command line that cannot work is worse than none: the wrong
    flag opens a dialog on the user's desktop and returns no text to report. A
    caller that has verified another slicer's flags states ``flavour`` itself.

    The verified Bambu Studio invocation is::

        BambuStudio --load-settings "<machine.json>;<process.json>" \\
                    --load-filaments "<filament.json>" \\
                    --slice 0 --outputdir <dir> <model.3mf>
    """
    prefix = [str(binary)] if isinstance(binary, str) else [str(arg) for arg in binary]
    flavour = flavour or slicer_flavour(prefix[0])
    flags = _flags_for(flavour, prefix[0])

    argv = list(prefix)
    settings = [path for path in (_preset_path(presets, "machine"),
                                  _preset_path(presets, "process")) if path]
    if settings:
        argv += [flags["settings"], _SETTINGS_SEP.join(settings)]
    filament = _preset_path(presets, "filament")
    if filament:
        argv += [flags["filaments"], filament]
    if "arrange" in flags:
        argv += [flags["arrange"], "1" if arrange else "0"]
    if ensure_on_bed and "ensure_on_bed" in flags:
        argv.append(flags["ensure_on_bed"])
    if int(copies or 1) > 1 and "repetitions" in flags:
        argv += [flags["repetitions"], str(int(copies))]
    argv += ["--slice", _ALL_PLATES, "--outputdir", str(outputdir), str(model_path)]
    return argv


# -- the job table ----------------------------------------------------------

#: How long a slice may run before the child is killed. A trivial part takes
#: about twenty seconds, so this is a ceiling for one that will never finish.
DEFAULT_TIMEOUT = 900.0

#: What a job leaves in its own folder, beside the slicer's own output.
LOG_NAME = "slicer.log"
JOB_NAME = "job.json"
RESULT_NAME = "result.json"

#: No console window under a windowed FreeCAD on Windows, where an inherited
#: console pops up over the application and can hang it. Zero elsewhere.
_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

#: How many jobs the in-memory table keeps. Dropping one only loses the handle;
#: its folder on disk still holds ``job.json`` and the log.
_KEEP_JOBS = 40

#: Left out of a job's public record: a Popen has no JSON form, and the child is
#: terminate_all's to signal.
_PRIVATE_FIELDS = ("process", "thread", "cancelled")

_jobs = {}
_jobs_order = []
_jobs_lock = threading.Lock()
_hooks = {"done": None}


def set_done_hook(callback):
    """Register "a slice finished", called with the job's public record.

    Invoked on the worker thread, so whatever is registered has to be
    thread-safe: the chat panel passes a bound Qt signal's ``emit``, which Qt
    then queues onto the GUI thread. Nothing here may touch a widget, and
    nothing here may call into FreeCAD.
    """
    with _jobs_lock:
        _hooks["done"] = callback


def _unique_id(base):
    """A free job id starting from `base`. Called with the lock held."""
    if base not in _jobs:
        return base
    for suffix in range(2, 100):
        candidate = "%s-%d" % (base, suffix)
        if candidate not in _jobs:
            return candidate
    return "%s-%d" % (base, int(time.time()))


def _public(record):
    """A caller's copy of a job record: JSON-able, elapsed time filled in, the
    child process left out."""
    view = copy.deepcopy({key: value for key, value in record.items()
                          if key not in _PRIVATE_FIELDS})
    end = record["finished_at"] or time.time()
    view["elapsed"] = round(end - record["started_at"], 3)
    return view


def _write_log(path, output):
    """Write the child's combined output beside the job. A log that cannot be
    written must not cost the outcome, so the failure is swallowed."""
    try:
        with open(path, "wb") as fh:
            fh.write(output or b"")
    except OSError:
        pass


def _write_job_json(record):
    """Record the job in its own folder -- the argv, the presets and where each
    came from, the options, the note and the timestamps. Written at the start
    and again at the end, so a job that never finishes still has one."""
    try:
        path = os.path.join(record["job_dir"], JOB_NAME)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(_public(record), fh, indent=2, sort_keys=True)
    except (OSError, TypeError, ValueError):
        pass


def start_job(binary, presets, model_path, job_dir, options=None, note=None):
    """Start a slice on a daemon thread and return its job id immediately.

    `options` may carry ``arrange``, ``ensure_on_bed``, ``copies``, ``flavour``
    and ``timeout``; anything absent takes its default. `note` is free text
    stored in ``job.json``, which is what a job read back weeks later has to
    explain itself with.

    The id is the job folder's own name, so the handle the caller quotes and the
    record on disk agree.

    Raises :class:`UnknownSlicer` before anything is written or started when the
    binary is not one whose flags are known.
    """
    options = dict(options or {})
    argv = build_argv(binary, presets, model_path, job_dir,
                      arrange=bool(options.get("arrange", True)),
                      ensure_on_bed=bool(options.get("ensure_on_bed", True)),
                      copies=int(options.get("copies") or 1),
                      flavour=options.get("flavour"))
    os.makedirs(job_dir, exist_ok=True)
    record = {
        "id": None,
        "status": "running",
        "argv": argv,
        "presets": copy.deepcopy(presets),
        "options": options,
        "note": note,
        "job_dir": job_dir,
        "model_path": model_path,
        "log_path": os.path.join(job_dir, LOG_NAME),
        "timeout": float(options.get("timeout") or DEFAULT_TIMEOUT),
        "started_at": time.time(),
        "finished_at": None,
        "returncode": None,
        "error": None,
        "result": None,
    }
    with _jobs_lock:
        record["id"] = _unique_id(os.path.basename(os.path.normpath(job_dir)) or "job")
        _jobs[record["id"]] = record
        _jobs_order.append(record["id"])
        while len(_jobs_order) > _KEEP_JOBS:
            _jobs.pop(_jobs_order.pop(0), None)
    _write_job_json(record)

    thread = threading.Thread(target=_run_job, args=(record,),
                              name="freecadclaude-slicer", daemon=True)
    record["thread"] = thread
    thread.start()
    return record["id"]


def _spawn(record):
    """Start the child and register it. Returns ``(process, error)``.

    Both happen under the jobs lock. Registering the process after starting it
    would leave a window in which :func:`terminate_all` sees no child and the
    slice runs on unattended; holding the lock across both means a quit either
    sets ``cancelled`` before the spawn, which is checked here, or waits and
    finds the process.
    """
    with _jobs_lock:
        if record.get("cancelled"):
            return None, "the slice was stopped before it started"
        try:
            record["process"] = subprocess.Popen(
                record["argv"], cwd=record["job_dir"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, creationflags=_CREATION_FLAGS)
        except (OSError, ValueError) as exc:
            return None, "the slicer would not start: %s" % exc
        return record["process"], None


def _run_job(record):
    """Run the slicer to completion on the worker thread.

    Piped stdio, and stdin closed so a child that reads it cannot sit waiting
    for input nobody will type. The output is collected, not
    inherited, which is also what keeps it out of FreeCAD's console.
    """
    timeout = record["timeout"]
    proc, error = _spawn(record)
    if proc is None:
        _finish(record, None, b"", error)
        return

    try:
        output = proc.communicate(timeout=timeout)[0] or b""
    except subprocess.TimeoutExpired:
        # Killed on expiry: the caller has a deadline of its own, and a slice
        # this long is not going to land.
        proc.kill()
        output = proc.communicate()[0] or b""
        _finish(record, proc.returncode, output,
                "the slicer did not finish within %.0f s and was killed" % timeout)
        return
    except OSError as exc:
        _finish(record, proc.returncode, b"",
                "the slicer's output could not be read: %s" % exc)
        return

    cancelled = "the slice was stopped before it finished" \
        if record.get("cancelled") else None
    _finish(record, proc.returncode, output, cancelled)


def _slicer_error(returncode, result):
    """Why this slice failed, or None if it succeeded.

    The slicer's own ``result.json`` names the failure where the exit code only
    counts it, so its ``error_string`` is preferred as the reason. A zero exit
    code with a zero ``return_code`` is a success whatever else the file says.
    """
    stated = None
    code = None
    if result is not None:
        text = result.get("error_string")
        if isinstance(text, str) and text.strip() and \
                not text.strip().startswith("Success"):
            stated = text.strip()
        raw = result.get("return_code")
        code = int(raw) if isinstance(raw, (int, float)) else None
    if code is not None and code != 0:
        return stated or "the slicer reported return_code %d" % code
    if returncode is None:
        return stated or "the slicer did not report an exit code"
    if returncode != 0:
        return stated or "the slicer exited with code %d" % returncode
    return None


def _finish(record, returncode, output, error):
    """File the outcome, write the log and ``job.json``, and fire the hook."""
    _write_log(record["log_path"], output)
    result = _read_json(os.path.join(record["job_dir"], RESULT_NAME))
    if not isinstance(result, dict):
        result = None
    if error is None:
        error = _slicer_error(returncode, result)
    with _jobs_lock:
        record["returncode"] = returncode
        record["result"] = result
        record["error"] = error
        record["status"] = "failed" if error else "succeeded"
        record["finished_at"] = time.time()
        record["process"] = None
        hook = _hooks["done"]
    _write_job_json(record)
    if hook is not None:
        try:
            hook(_public(record))
        except Exception:  # noqa: BLE001 - a listener must not break the job
            pass


def job_status(job_id):
    """The public record of `job_id`, or None if the table no longer has it."""
    with _jobs_lock:
        record = _jobs.get(job_id)
        return _public(record) if record else None


def latest_job():
    """The most recently started job's public record, or None."""
    with _jobs_lock:
        if not _jobs_order:
            return None
        return _public(_jobs[_jobs_order[-1]])


def terminate_all(grace=2.0):
    """Stop every slice still running. Returns how many there were.

    Wired to FreeCAD's ``aboutToQuit``: the children are held by daemon
    threads, so quitting mid-slice would otherwise leave a slicer running with
    nothing left to collect it. A job whose child has not been spawned yet is
    marked all the same, and :func:`_spawn` then never starts it.
    """
    with _jobs_lock:
        running = [record for record in _jobs.values()
                   if record["status"] == "running"]
        for record in running:
            record["cancelled"] = True
        live = [(record, record["process"]) for record in running
                if record.get("process")]
    for _record, proc in live:
        try:
            proc.terminate()
        except OSError:
            pass
    deadline = time.monotonic() + grace
    for _record, proc in live:
        try:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
        except OSError:
            pass
    return len(running)
