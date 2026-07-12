# SPDX-License-Identifier: LGPL-2.1-or-later
"""What the user has OPEN IN AN EDITOR right now -- distinct from selected."""


# --- GUI edit state --------------------------------------------------------
# "What does the user currently have OPEN in an editor" -- distinct from what they
# have selected. The GUI exposes it in exactly one place: the in-edit ViewProvider.
# It is the strongest signal of what "this sketch" means when the user is sitting
# inside the Sketcher editor, and it is not derivable from the document alone.


def _active_edit_object():
    """The document object the user has open in an editor right now (the Sketcher
    editor, a task dialog), or None if they aren't editing anything."""
    try:
        import FreeCADGui

        in_edit = FreeCADGui.ActiveDocument.getInEdit()
        if in_edit is None:
            return None
        return in_edit.Object
    except Exception:  # noqa: BLE001
        # No GUI, no active GUI document, or a ViewProvider without an Object.
        return None


def _active_edit_summary():
    """The in-edit object as a JSON-able dict, or None."""
    obj = _active_edit_object()
    if obj is None:
        return None
    type_id = getattr(obj, "TypeId", "")
    return {
        "name": getattr(obj, "Name", "?"),
        "label": getattr(obj, "Label", "?"),
        "type": type_id,
        "is_sketch": type_id == "Sketcher::SketchObject",
    }


def _is_open_in_editor(obj):
    """Is `obj` the thing the user currently has open in an editor?"""
    editing = _active_edit_object()
    if editing is None:
        return False
    try:
        # Compare by name, not identity -- FreeCAD hands back a fresh proxy each
        # access, so `editing is obj` is not reliable.
        return (
            editing.Name == obj.Name
            and editing.Document.Name == obj.Document.Name
        )
    except Exception:  # noqa: BLE001
        return False
