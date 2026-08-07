# SPDX-License-Identifier: LGPL-2.1-or-later
"""The document's standing context notes -- a CLAUDE.md for the FCStd.

Free text describing what the geometry cannot state: what the thing is for, how
its parts relate, whether it is 3D printed and on what printer. Stored in an
``App::TextDocument`` object so the user can open it in FreeCAD's own text
editor tab and correct it, and so it travels inside the saved file.

Reading is folded into ``get_objects`` (see ``notes_block``) rather than left to
a "read the notes first" line in the system prompt: a standing instruction of
that shape has measured near-zero compliance in this addon, while a payload
arriving in a tool result gets used.

Staleness is tracked structurally. Each write stamps the document's top-level
part names onto the notes object; a later ``get_objects`` compares that list to
the document as it stands and says which parts came or went. Feature-level churn
inside a Body does not count, because notes about purpose and part relationships
survive it.
"""

import datetime

#: Internal Name of the notes object. Renaming its Label is fine -- _find_notes
#: falls back to the marker property.
_NOTES_NAME = "ClaudeNotes"

#: Stamped at write time: the top-level part names the notes were written
#: against, and when. Also the marker that identifies a renamed notes object.
_STRUCTURE_PROP = "NotesStructure"
_UPDATED_PROP = "NotesUpdated"

#: App::Prop_Output. A write to a property carrying it does not mark the object
#: touched, so recording the fingerprint cannot trigger a rebuild.
_PROP_OUTPUT = 8

#: Ceiling on a single write. Notes past this are a build log, not context.
MAX_NOTES_CHARS = 20000

#: How much of the text get_objects carries inline. Beyond it the block says to
#: read the rest before writing, since a write replaces the whole text.
_EMBED_CHARS = 12000

_ABSENT_NOTE = (
    "This document has no standing notes yet. Once you know what it is for -- "
    "its purpose, how its parts relate and mate, whether it is to be 3D printed "
    "and on what printer, in what material and orientation -- record that with "
    "document_notes. Keep dimensions and anything else readable off the model "
    "out of it."
)

_TRUNCATED_NOTE = (
    "Only the first {shown} of {total} characters are shown. Call "
    "document_notes with no arguments to read the rest BEFORE writing -- a "
    "write replaces the whole text, so writing from this excerpt would drop "
    "the tail."
)

_STALE_NOTE = (
    "These notes were written against {old}. Since then: {changes}. Check them "
    "against the model and update with document_notes once this round of work "
    "is done."
)


def _top_level_parts(doc):
    """Names of the distinct parts and containers the notes describe.

    Bodies and Std Parts always count. A loose shape object counts when it sits
    in no container. Sketches, datums and origin members do not -- they are
    structure inside a part, not a part.

    PartDesign features are excluded even when they sit in no container. One
    that lost its Body (``removeObject`` on a Body leaves its children behind)
    would otherwise read as a new top-level part and flag the notes stale.
    """
    parts = []
    for obj in doc.Objects:
        type_id = getattr(obj, "TypeId", "") or ""
        if type_id in ("PartDesign::Body", "App::Part"):
            parts.append(obj.Name)
            continue
        try:
            if not (obj.isDerivedFrom("Part::Feature")
                    or obj.isDerivedFrom("Mesh::Feature")):
                continue
            if obj.isDerivedFrom("Sketcher::SketchObject") \
                    or obj.isDerivedFrom("PartDesign::Feature"):
                continue
            if obj.getParentGeoFeatureGroup() is not None:
                continue
        except Exception:  # noqa: BLE001 - not every object answers these
            continue
        parts.append(obj.Name)
    return sorted(parts)


def _find_notes(doc):
    """The document's notes object, or None."""
    obj = doc.getObject(_NOTES_NAME)
    if obj is not None and (getattr(obj, "TypeId", "") or "") == "App::TextDocument":
        return obj
    for candidate in doc.Objects:
        if (getattr(candidate, "TypeId", "") or "") != "App::TextDocument":
            continue
        if _STRUCTURE_PROP in (getattr(candidate, "PropertiesList", None) or []):
            return candidate
    return None


def _ensure_prop(obj, name, type_id):
    if name in (getattr(obj, "PropertiesList", None) or []):
        return
    obj.addProperty(type_id, name, "Claude", "", _PROP_OUTPUT)


def _stamp(obj, doc):
    """Record what the notes were written against, so staleness is detectable."""
    _ensure_prop(obj, _STRUCTURE_PROP, "App::PropertyStringList")
    _ensure_prop(obj, _UPDATED_PROP, "App::PropertyString")
    setattr(obj, _STRUCTURE_PROP, _top_level_parts(doc))
    setattr(obj, _UPDATED_PROP,
            datetime.datetime.now().isoformat(timespec="seconds"))


def read_notes(doc):
    """The notes text, or "" when the document has none."""
    obj = _find_notes(doc)
    return (getattr(obj, "Text", "") or "") if obj is not None else ""


def write_notes(doc, text):
    """Replace the notes, creating the object on first use.

    Returns ``(created, previous_length)``. Runs in its own transaction so the
    user can undo it; note that the fingerprint properties themselves are not
    undoable, which only means a stale fingerprint on a rare undo.
    """
    obj = _find_notes(doc)
    created = obj is None
    doc.openTransaction("Claude: document notes")
    try:
        if created:
            obj = doc.addObject("App::TextDocument", _NOTES_NAME)
            obj.Label = _NOTES_NAME
        previous = len(getattr(obj, "Text", "") or "")
        obj.Text = text
        _stamp(obj, doc)
        doc.commitTransaction()
    except Exception:
        doc.abortTransaction()
        raise
    try:
        # Clear the touch from setting Text without a document-wide recompute,
        # which would rebuild any geometry that happened to be touched already.
        obj.purgeTouched()
    except Exception:  # noqa: BLE001
        pass
    return created, previous


def _stale_note(obj, doc):
    """How the document's parts have changed since the notes were written, or
    None when they haven't (or when nothing was ever stamped)."""
    recorded = getattr(obj, _STRUCTURE_PROP, None)
    if not recorded:
        return None
    current = _top_level_parts(doc)
    added = [n for n in current if n not in recorded]
    removed = [n for n in recorded if n not in current]
    if not added and not removed:
        return None
    changes = []
    if added:
        changes.append(", ".join(added) + " added")
    if removed:
        changes.append(", ".join(removed) + " removed")
    return _STALE_NOTE.format(old=", ".join(recorded), changes="; ".join(changes))


def notes_block(doc):
    """The ``notes`` section of a get_objects payload, or None when there is
    nothing worth saying (an empty document nobody has modelled in yet)."""
    obj = _find_notes(doc)
    if obj is None:
        if not _top_level_parts(doc):
            return None
        return {"text": None, "missing": _ABSENT_NOTE}

    text = getattr(obj, "Text", "") or ""
    if not text.strip():
        return {"text": None, "missing": _ABSENT_NOTE}

    block = {"text": text[:_EMBED_CHARS]}
    if len(text) > _EMBED_CHARS:
        block["truncated"] = _TRUNCATED_NOTE.format(shown=_EMBED_CHARS,
                                                    total=len(text))
    updated = getattr(obj, _UPDATED_PROP, "") or ""
    if updated:
        block["updated"] = updated
    stale = _stale_note(obj, doc)
    if stale:
        block["stale"] = stale
    return block
