# SPDX-License-Identifier: LGPL-2.1-or-later
"""annotate_view / read_annotation -- let the user draw ON a screenshot.

A round trip in two halves, because the user has to act in the middle and a
tool call must never block the GUI thread waiting for them:

  annotate_view   grab the user's own 3D view -> save it -> open it in their
                  image editor. Returns immediately; the user draws and saves.
  read_annotation re-read that same file and hand it back as an inline image.

There is deliberately no pixel diffing. Claude SEES the returned image, so a
circle round a boss or an arrow at an edge is read directly off the picture --
which also means the user can mark it up any way they like (colour, arrows,
text, crops) instead of matching a scheme the code knows how to detect.

What the code contributes is the context a picture can't carry: which document
and objects are in shot, the world extents of that geometry, and the camera
angle it was taken from -- so a mark in the image can be tied back to a place
in the model.
"""

import os
import subprocess
import sys

from .render import _VIEW_PREF_PATH, _orbit_angles_from_view, _camera_angle_note
from .session import PARAM_PATH, _artifact_path

#: Per-platform default image editor. macOS Preview and Windows Paint both open
#: a PNG ready to draw on; on Linux the desktop's default handler is the only
#: portable answer. Override with the "AnnotateEditor" FreeCADClaude preference
#: (an application name/path) when the default isn't the tool you mark up in.
_DEFAULT_EDITORS = {
    "darwin": ["open", "-a", "Preview"],
    "win32": ["mspaint"],
}

#: The most recent annotate_view, so read_annotation needs no argument in the
#: common case ("I've drawn on it"). Holds the context captured at grab time --
#: the camera angle and extents are true of the IMAGE, so they must be recorded
#: when it was taken, not recomputed later against a view the user has since
#: orbited away.
_last_annotation = {"path": None, "context": ""}


def _editor_command(path):
    """The command list that opens `path` for editing, or None to fall back to
    the platform's default handler."""
    import FreeCAD

    configured = FreeCAD.ParamGet(PARAM_PATH).GetString("AnnotateEditor", "").strip()
    if configured:
        # A bare app name on macOS still needs `open -a`; elsewhere it's the exe.
        return ["open", "-a", configured, path] if sys.platform == "darwin" else [configured, path]
    base = _DEFAULT_EDITORS.get(sys.platform)
    return (base + [path]) if base else None


def _open_for_editing(path):
    """Launch the user's image editor on `path`, without blocking.

    Popen, never run/call: this executes on FreeCAD's GUI thread, so waiting for
    the editor to exit would freeze the whole application for as long as the
    user spent drawing -- the exact failure mode a long run_python has. Returns
    a note describing what happened, since a failure to open is something the
    user needs told rather than a silent no-op.
    """
    kwargs = {}
    if sys.platform == "win32":  # don't flash a console window under the GUI
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    command = _editor_command(path)
    for attempt in [c for c in (command, _fallback_command(path)) if c]:
        try:
            subprocess.Popen(attempt, **kwargs)  # noqa: S603 - our own path
            return f"Opened it in {attempt[0]}."
        except Exception:  # noqa: BLE001 - try the fallback, then report
            continue
    if sys.platform == "win32":
        try:
            os.startfile(path)  # noqa: S606 - documented Windows shell open
            return "Opened it with the default Windows handler."
        except Exception as exc:  # noqa: BLE001
            return f"Could not open an editor automatically ({exc!r}) -- open the file manually."
    return "Could not open an editor automatically -- open the file manually."


def _fallback_command(path):
    """The desktop's default handler for the file, as a second chance."""
    if sys.platform == "darwin":
        return ["open", path]
    if sys.platform.startswith("linux"):
        return ["xdg-open", path]
    return None


def _view_context(view, doc):
    """The facts about the shot that the image itself can't carry: document,
    what's visible, its world extents, and the camera angle."""
    from .geometry import _document_bbox, _extent_report

    parts = [f"Document '{doc.Label}'."]
    try:
        visible = [o.Name for o in doc.Objects
                   if getattr(getattr(o, "ViewObject", None), "Visibility", False)
                   and getattr(o, "Shape", None) is not None and not o.Shape.isNull()]
    except Exception:  # noqa: BLE001
        visible = []
    if visible:
        shown = ", ".join(visible[:12]) + ("..." if len(visible) > 12 else "")
        parts.append(f"Visible geometry: {shown}.")
        framed = _extent_report(_document_bbox(doc, names=set(visible)))
        if framed:
            parts.append(f"It spans {framed} (world coords).")
    angles = _orbit_angles_from_view(view)
    if angles is not None:
        parts.append(_camera_angle_note(angles).strip())
    return " ".join(parts)


_ANNOTATE_VIEW_SCHEMA = {
    "name": "annotate_view",
    "description": (
        "Screenshot what the user is looking at right now and OPEN IT IN THEIR "
        "IMAGE EDITOR so they can draw on it -- circle a face, arrow at an edge, "
        "scribble a dimension -- then tell you about it. Use this whenever "
        "pointing at geometry in words is the hard part ('this corner', 'the "
        "boss near the slot'), or when you need the user to show you WHERE "
        "rather than describe it.\n"
        "This returns as soon as the editor opens; it does NOT wait. Tell the "
        "user the file is open, ask them to draw, SAVE, and say when they're "
        "done -- then call read_annotation to see what they marked. Nothing is "
        "detected by colour or shape: you read the marks off the image, so any "
        "kind of marking works.\n"
        "Read-only: it grabs the already-rendered view, and never moves the "
        "user's camera or changes the document."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "width": {
                "type": "integer",
                "description": "Image width px (default 1600). Bigger gives the user more room to draw accurately.",
            },
        },
        "additionalProperties": False,
    },
}


def _run_annotate_view(args):
    import FreeCAD
    import FreeCADGui

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return "No active document."
    view = FreeCADGui.activeView()
    if view is None or not hasattr(view, "saveImage"):
        return (
            "The active tab isn't a 3D view -- click into the user's 3D view "
            "(not a Spreadsheet/TechDraw/other tab) and try again."
        )

    width = int(args.get("width") or 1600)
    if width <= 0:
        width = 1600
    png_path = _artifact_path("annotations", "annotate", ".png")

    # GrabFramebuffer reads the pixels already on screen -- the user's real
    # camera, draw style and background -- so what they draw on is exactly what
    # they were looking at. Same mechanism (and same restore) as
    # capture_user_view; only valid because this view is actually visible.
    params = FreeCAD.ParamGet(_VIEW_PREF_PATH)
    prev_method = params.GetString("SavePicture", "")
    params.SetString("SavePicture", "GrabFramebuffer")
    try:
        view.saveImage(png_path, width)
    except Exception as exc:  # noqa: BLE001
        return f"Could not capture the active view: {exc!r}"
    finally:
        params.SetString("SavePicture", prev_method)

    context = _view_context(view, doc)
    _last_annotation.update(path=png_path, context=context)
    opened = _open_for_editing(png_path)
    return (
        f"Captured the user's current view to {png_path}. {opened}\n\n"
        f"{context}\n\n"
        "Ask the user to draw on it, SAVE the file (in place -- same path), and "
        "say when they're done. Then call read_annotation to see the markup. "
        "They can mark it however they like; you'll read it off the image."
    )


_READ_ANNOTATION_SCHEMA = {
    "name": "read_annotation",
    "description": (
        "Re-read the image from annotate_view AFTER the user has drawn on it, "
        "and return it inline so you can see their markup. Call this once they "
        "say they've saved. Defaults to the most recent annotate_view, so no "
        "argument is needed in the normal flow; pass 'path' to re-read a "
        "specific file.\n"
        "If it looks unchanged, the user probably hasn't saved yet (or saved a "
        "copy elsewhere) -- say so and ask, rather than guessing at marks that "
        "aren't there."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Image file to read (default: the last annotate_view).",
            },
        },
        "additionalProperties": False,
    },
}


def _run_read_annotation(args):
    path = (args.get("path") or _last_annotation["path"] or "").strip()
    if not path:
        return "No annotated image yet -- call annotate_view first."
    if not os.path.isfile(path):
        return (
            f"No file at {path}. If the editor saved a copy somewhere else, pass "
            "that path; otherwise re-run annotate_view."
        )

    parts = [f"The annotated image from {path}, as the user saved it."]
    if path == _last_annotation["path"] and _last_annotation["context"]:
        # The context belongs to the ORIGINAL grab -- the extents and camera
        # angle describe the picture, not wherever the user's view has drifted
        # to since -- so it's replayed from what annotate_view recorded.
        parts.append(_last_annotation["context"])
    parts.append(
        "Read the user's marks straight off the image and relate them to the "
        "geometry using the extents and camera angle above."
    )
    return "\n\n".join(parts), path
