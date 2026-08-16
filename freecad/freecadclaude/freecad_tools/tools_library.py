# SPDX-License-Identifier: LGPL-2.1-or-later
"""``script_library`` -- the reusable run_python modules, listed and added to.

A tool rather than a line in the system prompt, and that is the whole design
decision. Reference files the prompt tells Claude to read go essentially unread
(see "Just-in-time reference pointers" in CLAUDE.md); a tool schema is in
context every turn and costs no judgement call to notice. The library exists to
stop work being re-derived, so a library nobody looks in is worse than none --
it is the same cost with a maintenance burden attached.

The read side is deliberately cheap: the index is parsed with ``ast``, never
imported, so listing runs anywhere and cannot execute a module's top-level code.
"""

from . import library

_SCRIPT_LIBRARY_SCHEMA = {
    "name": "script_library",
    "description": (
        "List the reusable Python modules kept across conversations, or add one. "
        "Call with NO arguments before writing substantial run_python code: the "
        "modules are already on sys.path, so anything listed is one import away "
        "-- `from bisect_with_pegs import bisect` -- and re-deriving a technique "
        "that is already in here costs a round of failed geometry the library "
        "was built to skip. Entries carry the signature and purpose; Read the "
        "source path for the detail, because the constants are where the "
        "reasoning lives (a clearance that was printed and measured, why a cut "
        "goes one way). Pass 'name' and 'code' to save a module. Save when a "
        "script has proved itself on real geometry and a later conversation "
        "would want it -- a technique, not a one-off: every run_python call is "
        "already archived in the session's scripts/ folder, so this is for the "
        "few worth calling again. A saved module needs a module docstring "
        "carrying the why, and public functions taking the document name, "
        "object labels and output paths as ARGUMENTS rather than hardcoding "
        "them."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Module name to save as: lower_snake_case, no .py, imported "
                    "verbatim (e.g. \"bisect_with_pegs\"). Saving over an "
                    "existing personal module replaces it. Omit to list."
                ),
            },
            "code": {
                "type": "string",
                "description": (
                    "The module's complete source, including the module "
                    "docstring. Required with 'name'."
                ),
            },
        },
        "additionalProperties": False,
    },
}


def _precheck_script_library(args):
    """Validate a save in pure Python, before the GUI thread.

    A listing needs no check. A save is checked here rather than in ``run``
    because none of it needs FreeCAD -- a rejected save should never have
    occupied the GUI thread at all.
    """
    name, code = args.get("name"), args.get("code")
    if name is None and code is None:
        return ""
    if name is None:
        return ("script_library got 'code' with no 'name'. Pass the module name "
                "to save it under, or call with no arguments to list.")
    if code is None:
        return ("script_library got 'name' with no 'code'. Pass the module "
                "source to save, or call with no arguments to list.")
    return library.validate(name, code)


def _run_script_library(args):
    name, code = args.get("name"), args.get("code")
    if name is None:
        library.ensure_on_path()  # a listing implies the imports are next
        return library.format_index(library.index())

    path, replaced = library.save(name, code)
    verb = "Replaced" if replaced else "Saved"
    return ("%s %s.py in the personal library (%s).\n\nIt is importable from "
            "run_python now and in every later conversation: "
            "`from %s import ...`.%s"
            % (verb, name, path, name, library.shadowed_note(name)))
