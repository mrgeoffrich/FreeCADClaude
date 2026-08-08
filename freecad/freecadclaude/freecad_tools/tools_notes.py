# SPDX-License-Identifier: LGPL-2.1-or-later
"""The design-context tools: what the geometry cannot say.

``document_notes``      the document's standing notes -- purpose, how the parts
                        relate, how it is to be made.
``set_print_direction`` which way up each part is printed.

Both are write-first. ``get_objects`` already returns what they record, so
reading rarely needs a call of its own; ``document_notes`` keeps a read path
only for notes too long to embed whole, which must be read in full before a
write replaces them.
"""

from .doc_notes import MAX_NOTES_CHARS, read_notes, write_notes
from .print_meta import CUSTOM, DIRECTIONS, normalise_direction, set_direction

_DOCUMENT_NOTES_SCHEMA = {
    "name": "document_notes",
    "description": (
        "Read or replace this document's standing notes -- a CLAUDE.md for the "
        "FCStd, stored inside the file as a ClaudeNotes object the user can "
        "open and edit in FreeCAD. Call with no arguments to read; pass 'text' "
        "to replace them. get_objects already returns these notes, so the call "
        "that matters is the WRITE, at the end of a round of work that changed "
        "what the document IS or how its parts fit together -- not after every "
        "feature. Record what the geometry cannot state: what the thing is for, "
        "how the parts interrelate and mate, whether it is to be 3D printed and "
        "on which printer, in what material, at what orientation and "
        "clearances, and the decisions and constraints behind the design. Keep "
        "dimensions, feature lists, object inventories and volumes OUT -- "
        "get_objects and describe_objects read those off the model, and a "
        "number copied into prose is wrong the moment the model changes. A "
        "write replaces the whole text, so carry forward everything still true, "
        "including anything the user wrote themselves."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": (
                    "The complete new notes, as Markdown. Omit to read the "
                    "current notes instead of writing."
                ),
            },
        },
        "additionalProperties": False,
    },
}


def _precheck_document_notes(args):
    """Validate a write in pure Python, before the GUI thread."""
    text = args.get("text")
    if text is None:
        return ""
    if not isinstance(text, str):
        return ("document_notes' 'text' must be a string -- the complete new "
                "notes as Markdown. Omit it entirely to read instead.")
    if len(text) > MAX_NOTES_CHARS:
        return (f"Those notes are {len(text)} characters and the limit is "
                f"{MAX_NOTES_CHARS}. They are standing context, not a build "
                "log: cut the dimensions, feature lists and step-by-step "
                "history and keep what the model cannot say.")
    return ""


def _run_document_notes(args):
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return "No active document, so there are no notes to read or write."

    text = args.get("text")
    if text is None:
        current = read_notes(doc)
        if not current.strip():
            return (f"{doc.Label} has no notes yet. Pass 'text' to write them.")
        return current

    created, previous = write_notes(doc, text)
    if created:
        return (f"Created ClaudeNotes on {doc.Label} ({len(text)} chars). It is "
                "in the object tree, where the user can open and edit it. The "
                "notes live in the FCStd, so they persist once the document is "
                "saved.")
    return (f"Updated ClaudeNotes on {doc.Label}: {len(text)} chars replacing "
            f"{previous}. Persists once the document is saved.")


_SET_PRINT_DIRECTION_SCHEMA = {
    "name": "set_print_direction",
    "description": (
        "Record which way up a part is 3D printed, so later work can design to "
        "it. The direction names the PART-LOCAL axis that points UP in world "
        "space once the part is on the plate -- layers stack along it. '+Z up' "
        "is the part printed as modelled; '-Z up' means local -Z points up, so "
        "the part prints UPSIDE-DOWN with its local +Z face resting on the "
        "plate. It decides which features print reliably: an overhang, a "
        "bridge, a bore or a thin wall all behave differently depending on how "
        "the layers run through them, and none of that is derivable from the "
        "geometry. Set it on the PART (a PartDesign Body, a Std Part, or a "
        "standalone shape), not on a feature inside one. Batch every part in "
        "one call. Use 'Not printed' for a part that is machined, bought in or "
        "purely a reference. get_objects reports whatever is set, so there is "
        "no matching read tool -- and read it before adding features, rather "
        "than assuming the part prints in its modelled orientation."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "parts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Internal name of the part, e.g. \"Door\".",
                        },
                        "direction": {
                            "type": "string",
                            "enum": list(DIRECTIONS),
                            "description": (
                                "The part-local axis pointing UP in world space "
                                "when printed; the opposite face rests on the "
                                "plate. 'Custom' for a tilted print, which also "
                                "needs 'vector'."
                            ),
                        },
                        "vector": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 3,
                            "maxItems": 3,
                            "description": (
                                "Build direction in the part's local frame, "
                                "[x, y, z]. Required for 'Custom' and ignored "
                                "otherwise. Normalised on write."
                            ),
                        },
                    },
                    "required": ["name", "direction"],
                    "additionalProperties": False,
                },
                "description": "One entry per part.",
            },
        },
        "required": ["parts"],
        "additionalProperties": False,
    },
}


def _precheck_set_print_direction(args):
    """Validate the batch in pure Python, before the GUI thread."""
    parts = args.get("parts")
    if not isinstance(parts, list) or not parts:
        return ("set_print_direction needs a non-empty 'parts' array, e.g. "
                "{\"parts\": [{\"name\": \"Door\", \"direction\": \"+Z up\"}]}.")
    for entry in parts:
        if not isinstance(entry, dict):
            return "Every entry in 'parts' must be an object with 'name' and 'direction'."
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            return "Every entry in 'parts' needs a non-empty 'name'."
        direction = normalise_direction(entry.get("direction"))
        if direction is None:
            return (f"'{entry.get('direction')}' is not a print direction for "
                    f"{name}. Use one of: {', '.join(DIRECTIONS)}. The direction "
                    "is the part-local axis pointing UP off the build plate.")
        if direction == CUSTOM:
            vector = entry.get("vector")
            if not isinstance(vector, list) or len(vector) != 3 or \
                    not all(isinstance(v, (int, float)) for v in vector):
                return (f"{name} is 'Custom', so it needs a 'vector' of three "
                        "numbers giving the build direction in its local frame.")
            if not any(abs(float(v)) > 1e-9 for v in vector):
                return f"{name}'s 'vector' is zero-length, so it names no direction."
    return ""


def _run_set_print_direction(args):
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return "No active document, so there is no part to set a print direction on."

    from .doc_notes import _top_level_parts

    parts = _top_level_parts(doc)
    resolved, missing, not_a_part = [], [], []
    for entry in args["parts"]:
        name = entry["name"].strip()
        obj = doc.getObject(name)
        if obj is None:
            matches = [o for o in doc.Objects if o.Label == name]
            obj = matches[0] if matches else None
        if obj is None:
            missing.append(name)
            continue
        if obj.Name not in parts:
            not_a_part.append(obj.Name)
        resolved.append((obj, normalise_direction(entry["direction"]),
                         entry.get("vector")))

    if not resolved:
        return (f"None of those names are in {doc.Label}: {', '.join(missing)}. "
                "Call get_objects for the internal names.")

    doc.openTransaction("Claude: print direction")
    try:
        for obj, direction, vector in resolved:
            set_direction(obj, direction, vector)
        doc.commitTransaction()
    except Exception:
        doc.abortTransaction()
        raise

    from .print_meta import PLATE_SIDES

    lines = []
    for obj, direction, _ in resolved:
        plate = PLATE_SIDES.get(direction)
        lines.append(f"{obj.Name}: {direction}"
                     + (f" (local {plate} face on the plate)" if plate else ""))
    out = "Print direction set (persists once the document is saved).\n" + \
          "\n".join(lines)
    if missing:
        out += ("\n\nNot found, so not set: " + ", ".join(missing) +
                ". Call get_objects for the internal names.")
    if not_a_part:
        out += ("\n\nNote: " + ", ".join(not_a_part) + " is not a top-level part "
                "(a Body, a Std Part or a standalone shape). The direction was "
                "still recorded, but it belongs on the part a feature sits in, "
                "where get_objects will report it against the whole piece.")
    return out
