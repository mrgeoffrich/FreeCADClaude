# SPDX-License-Identifier: LGPL-2.1-or-later
"""``document_notes`` -- read or replace the document's standing context notes.

The read half is mostly redundant: ``get_objects`` already carries the notes.
It exists for the case that redundancy cannot cover -- notes too long to embed
whole, which must be read in full before a write replaces them.
"""

from .doc_notes import MAX_NOTES_CHARS, read_notes, write_notes

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
