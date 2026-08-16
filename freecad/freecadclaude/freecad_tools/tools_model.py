# SPDX-License-Identifier: LGPL-2.1-or-later
"""view_model_3d / read_model_markup -- the round trip through the desktop browser.

The same two-halves shape as ``tools_device``, and for the same reason: the user
has to act in the middle, and a tool call must never block the GUI thread
waiting for them.

  view_model_3d    export the real BREP geometry, publish it to the loopback
                   model server, open the user's browser (or rely on the tab
                   that is already open), return NOW. The user orbits the
                   actual solid in the browser, clicks the face(s) they mean,
                   types a note, presses Send.
  read_model_markup  read the markup document back: the exact (ObjectName,
                   "FaceN") references to hand to run_python -- each one
                   re-validated against the live document's shape hash -- plus
                   a server-side render of the marked faces, highlighted.

Where this differs from send_to_device is what a mark NAMES. A pen stroke on a
flattened screenshot is a pixel; a click on the tessellated BRep is a face
ordinal, which resolves back to a real ``Shape.Faces`` index. The picture is
still worth having (Claude confirms "yes, that's the boss on the left"), but
the payload is the face reference, and the whole point of the hash check below
is that a reference is only ever reported with confidence when the geometry it
names has not changed since export.

**Nothing in here runs on an HTTP thread.** Both functions execute on the GUI
thread like every other tool; ``model_export.export_brep`` does the FreeCAD
work up front and hands the server a plain dict of paths, hashes and face maps
-- the whole of the "the HTTP server never calls into FreeCAD" invariant, the
same shape as the device and G-code round trips.
"""

import contextlib
import json
import webbrowser

from . import model_export
from .render import (
    _DEFAULT_HEIGHT,
    _DEFAULT_WIDTH,
    _apply_camera_plan,
    _objects_schema_prop,
    _offscreen_shot,
    _resolve_camera_args,
    _save_view_png,
)
from .session import _artifact_path, _session_subdir

#: The viewer's own marked-face colour (model_web/src/App.tsx MARK_COLOR), so
#: the server-side confirmation render and the browser show the same "picked"
#: orange. Alpha 0.0, the per-face DiffuseColor convention for opaque.
_HIGHLIGHT_RGBA = (1.0, 0.55, 0.12, 0.0)

#: Camera for the confirmation shot. A simple iso -- this is "what was marked",
#: not a composed view, and the marked faces are highlighted, so any angle
#: that shows the object reads the same answer.
_CONFIRM_VIEW = "iso"


def _open_in_browser(url):
    """Launch the user's default browser on `url`, without blocking.

    ``webbrowser.open`` spawns the browser and returns; it never waits for the
    tab to close, which is the whole requirement here -- this runs on FreeCAD's
    GUI thread. Returns a note: a failure to open is something the user has to
    be told rather than a silent no-op, and the URL is in the tool result
    either way (the same shape as ``tools_slice._open_in_browser``).
    """
    try:
        opened = webbrowser.open(url, new=2)
    except Exception:  # noqa: BLE001 - a browser failure is a note, not a crash
        opened = False
    if opened:
        return "Opened it in the user's browser."
    return (
        "Could not open a browser automatically -- the user can paste the "
        "page URL below."
    )


_VIEW_MODEL_3D_SCHEMA = {
    "name": "view_model_3d",
    "description": (
        "Open the REAL 3D geometry of the named object(s) in the user's "
        "desktop browser, so they can click the exact face(s) they mean and "
        "send the marks back to you. This is the 3D sibling of send_to_device: "
        "instead of a flat picture on a tablet, the browser shows the actual "
        "solid -- the native BREP, tessellated in the browser -- orbitable, "
        "and a click names a real face of the part, not a pixel on a "
        "screenshot.\n"
        "It exports the geometry to the session folder, starts the viewer "
        "server if it isn't already running, opens a browser tab, and returns "
        "STRAIGHT AWAY -- it does not wait for the user. Tell them the model "
        "is in their browser, ask them to click the face(s) they mean, type a "
        "note and press Send, and then call read_model_markup for the exact "
        "face references plus a rendered confirmation picture. If the viewer "
        "was already running, no new tab is opened: the tab that is open "
        "updates with the new export.\n"
        "Read-only: exporting is a pure read of Shape and nothing in the "
        "document changes."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "objects": _objects_schema_prop("to show in the model viewer"),
        },
        "required": ["objects"],
        "additionalProperties": False,
    },
}


def _run_view_model_3d(args):
    import FreeCAD

    from .. import model_server

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return "No active document."

    names = args.get("objects")
    if not names:
        return (
            "view_model_3d requires 'objects': a list of object Names to show "
            "in the model viewer. Call get_objects first."
        )
    objs = []
    for n in names:
        obj = doc.getObject(n)
        if obj is None:
            return f"No object named '{n}'."
        objs.append(obj)

    # <session>/models/ -- both directions of the round trip live here (the
    # exported .brp files and the markup documents the browser sends back),
    # mirroring how the device round trip uses one folder for both directions.
    # Resolved here on the GUI thread and handed to the server as a plain
    # string; the server never resolves a session folder itself.
    try:
        out_dir = _session_subdir("models")
    except OSError as exc:
        return (
            f"Could not create the model export folder: {exc}. Nothing was "
            "exported or published."
        )

    try:
        export_result = model_export.export_brep(objs, out_dir)
    except ValueError as exc:
        return str(exc)

    # Auto-start is where "no button" lives for this feature, exactly like
    # view_gcode: the loopback server is started on demand by the tool itself.
    if not model_server.is_running():
        try:
            url, _token = model_server.start(upload_dir=out_dir)
        except RuntimeError as exc:
            return f"The model viewer could not be started: {exc}"
        except OSError as exc:
            return (
                f"The model viewer could not be started: {exc}. Nothing was "
                "published."
            )
        opened = _open_in_browser(url)
    else:
        # No real way to detect "a tab is open" from Python; "was the server
        # already running" is the deliberate stand-in -- a tab does not usually
        # outlive the server that serves it -- and the open tab picks the new
        # publish up over SSE.
        url = model_server.current_url()
        opened = (
            "The model viewer was already running, so no new browser tab was "
            "opened -- the tab that is open updates with this export."
        )

    record = model_server.publish(export_result, upload_dir=out_dir)

    names_str = ", ".join(sorted(export_result["objects"]))
    lines = [
        f"Published the 3D geometry of {names_str} "
        f"({len(export_result['objects'])} object(s); BREP files in {out_dir}) "
        f"to the model viewer, publish id {record['id']}.",
        f"{opened} The page is at {url} -- the user can paste it if no tab "
        "appeared.",
        "This returned as soon as the export was published; it does NOT wait. "
        "Tell the user the model is in their browser: they can orbit it, click "
        "the face(s) they mean, type a note, and press Send. When they say "
        "they've sent it, call read_model_markup -- it returns the exact "
        "Object.FaceN references (re-validated against the current document) "
        "plus a rendered picture of the marked faces. Don't call it before "
        "they've pressed Send; it returns the newest markup document, which "
        "until then is nothing at all.",
    ]
    return "\n".join(lines)


_READ_MODEL_MARKUP_SCHEMA = {
    "name": "read_model_markup",
    "description": (
        "Read the markup the user sent from the model viewer (after "
        "view_model_3d) and return both the exact face references to script "
        "against and a rendered picture of the marked faces, so you can see "
        "what was picked.\n"
        "Each reference is re-validated against the LIVE document before it is "
        "reported: a mark is given as a confident 'Object.FaceN' only when the "
        "object's shape hash still matches the export the mark was drawn on. "
        "If the geometry changed in between, or the object is gone, you get "
        "the face's recorded centroid/normal/area and an explicit warning to "
        "re-locate it -- never a face name that may point at the wrong place. "
        "A mark on one object is not affected by a change to a different one.\n"
        "Call it once the user says they've pressed Send. Defaults to the "
        "newest markup document; pass 'index' to look further back. Calling it "
        "before anything has been sent back returns a plain message saying so."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "index": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "How far back to look: 0 (default) is the newest markup "
                    "document the viewer sent, 1 the one before it, and so on."
                ),
            },
        },
        "additionalProperties": False,
    },
}


def _parse_markup_doc(doc_text):
    """The uploaded document as a flat dict, or None when it isn't one.

    Defensive on every field, the same posture as ``tools_device._source_capture``:
    the schema belongs to the web app (``model_web/src/doc.ts``), so a
    malformed or unexpectedly-shaped document must read as "no markup", never
    raise. The server already proved the body is JSON; this is the second,
    stricter gate, and it enforces doc.ts's one load-bearing rule -- a markup
    document without a ``source.publish_id`` cannot be resolved back to the
    geometry it was drawn on, so its absence is "no publish", not a crash.
    """
    try:
        data = json.loads(doc_text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    source = data.get("source")
    publish_id = source.get("publish_id") if isinstance(source, dict) else None
    marks = data.get("marks")
    caption = data.get("caption")
    return {
        "publish_id": publish_id if isinstance(publish_id, str) else None,
        "marks": marks if isinstance(marks, list) else [],
        "caption": caption if isinstance(caption, str) else "",
    }


def _resolve_mark(doc, publish_record, mark):
    """Resolve one ``FaceMark`` against the LIVE document.

    Returns a dict: ``{"id", "object", "face_index", "note", "confident",
    "face_name", "recorded", "face_count", "reason"}``. ``confident`` is True
    only when the publish record exists, the object is still in the document
    with a shape, and its shape hash matches the export the mark was drawn on
    -- the per-OBJECT granularity of ``diagnostics._shape_metrics``, so a mark
    on one object is not invalidated by a change to a different one.
    ``recorded`` is the face's centroid/normal/area from the publish record
    (None when the publish, the object, or that face index is unavailable);
    ``face_count`` is how many faces that export recorded for the object, for
    the out-of-range message.
    """
    base = {
        "id": mark.get("id") if isinstance(mark, dict) else None,
        "object": mark.get("object") if isinstance(mark, dict) else None,
        "face_index": mark.get("face_index") if isinstance(mark, dict) else None,
        "note": mark.get("note") if isinstance(mark, dict) else "",
        "confident": False,
        "face_name": None,
        "recorded": None,
        "face_count": None,
        "reason": None,
    }
    if not isinstance(base["note"], str):
        base["note"] = ""
    if (not isinstance(base["object"], str) or not isinstance(base["face_index"], int)
            or base["face_index"] < 0):
        base["reason"] = "malformed"
        return base
    if publish_record is None:
        base["reason"] = "no_publish"
        return base
    entry = (publish_record.get("objects") or {}).get(base["object"])
    if not isinstance(entry, dict):
        base["reason"] = "not_in_export"
        return base
    faces = entry.get("faces") or {}
    base["face_count"] = len(faces)
    recorded = faces.get(str(base["face_index"]))
    if not isinstance(recorded, dict):
        base["reason"] = "face_out_of_range"
        return base
    base["recorded"] = recorded
    if doc is None:
        base["reason"] = "no_document"
        return base
    live = doc.getObject(base["object"])
    if live is None:
        base["reason"] = "object_gone"
        return base
    shape = getattr(live, "Shape", None)
    if shape is None or shape.isNull():
        base["reason"] = "no_shape"
        return base
    if shape.hashCode() != entry.get("shape_hash"):
        base["reason"] = "hash_changed"
        return base
    base["confident"] = True
    base["face_name"] = f"Face{base['face_index'] + 1}"
    return base


def _format_point(values):
    """``[x, y, z]`` as the compact ``"[1, 2.5, 3]"`` the messages quote.

    ``+ 0.0`` normalises a -0.0 component (FreeCAD normals carry them) so the
    quoted point reads ``[0, ...]`` rather than ``[-0, ...]``.
    """
    return "[" + ", ".join(f"{v + 0.0:g}" for v in values) + "]"


def _degraded_line(r):
    """One mark that did NOT resolve confidently: what is known about the face
    at export time, and an explicit instruction to re-locate rather than trust
    a face number. Naming a FaceN that may point at the wrong place is exactly
    the confident-wrong-answer this addon refuses elsewhere
    (``diagnostics._pre_existing_failure_note``); the recorded centroid is the
    honest evidence to re-locate from instead.
    """
    who = f"{r['object']} (face index {r['face_index']})"
    note = f' The user\'s note: "{r["note"]}".' if r["note"] else ""
    if r["reason"] == "malformed":
        return (
            "- WARNING: a mark is malformed (no usable object name and/or "
            "face_index) and was skipped." + note
        )
    if r["reason"] == "no_publish":
        return (
            f"- WARNING: {who} cannot be validated -- the export it was drawn "
            "on is no longer available to this conversation, so nothing about "
            "the face can be checked against the current document. Ask the "
            "user to send the model again with view_model_3d and re-mark it."
            + note
        )
    if r["reason"] == "not_in_export":
        return (
            f"- WARNING: {who} names an object that was not part of the export "
            "the document was drawn on." + note
        )
    if r["reason"] == "face_out_of_range":
        count = r["face_count"] if isinstance(r["face_count"], int) else "?"
        return (
            f"- WARNING: {who} is out of range for that export, which recorded "
            f"{count} face(s). The mark cannot be resolved to a face." + note
        )
    if r["reason"] == "no_document":
        return (
            f"- WARNING: {who} cannot be validated -- there is no active "
            "document to check it against. Open the document the marks were "
            "made on, then call read_model_markup again." + note
        )
    if r["reason"] == "object_gone":
        return (
            f"- WARNING: {who} -- the object no longer exists in the current "
            "document."
            + _recorded_face_tail(r)
            + note
            + " Re-locate the face before scripting against it."
        )
    if r["reason"] == "no_shape":
        return (
            f"- WARNING: {who} -- the object no longer has a shape."
            + _recorded_face_tail(r)
            + note
            + " Re-locate the face before scripting against it."
        )
    # hash_changed -- the one the whole staleness check exists for.
    return (
        f"- WARNING: {who} -- the object's geometry has changed since the "
        "export this mark was drawn on (its shape hash differs)."
        + _recorded_face_tail(r)
        + note
        + " Re-locate the face (e.g. nearest face to that recorded point) "
        "before scripting against it -- do not assume the face number still "
        "names the same place."
    )


def _recorded_face_tail(r):
    """The 'at export the face was at centroid ...' sentence, when the publish
    record carried the face's data."""
    recorded = r.get("recorded") or {}
    centroid = recorded.get("centroid")
    if not isinstance(centroid, (list, tuple)) or len(centroid) != 3:
        return ""
    parts = [f"the face was at centroid {_format_point(centroid)}"]
    normal = recorded.get("normal")
    if isinstance(normal, (list, tuple)) and len(normal) == 3:
        parts.append(f"normal {_format_point(normal)}")
    area = recorded.get("area")
    if isinstance(area, (int, float)):
        parts.append(f"area {area:g}")
    return " At export: " + ", ".join(parts) + "."


def _run_read_model_markup(args):
    import FreeCAD

    from .. import model_server

    try:
        index = max(0, int(args.get("index") or 0))
    except (TypeError, ValueError):
        index = 0

    uploads = model_server.uploads()
    if not uploads:
        return (
            "Nothing has come back from the model viewer yet. The user has to "
            "click the face(s) they mean in the browser, type any notes, and "
            "press Send. Ask them whether they've sent it, rather than polling "
            "this."
        )
    if index >= len(uploads):
        return (
            f"Only {len(uploads)} markup document(s) have come back from the "
            f"model viewer, so index {index} doesn't exist -- 0 is the newest."
        )

    record = uploads[len(uploads) - 1 - index]
    doc_text = (record.get("doc") or "").strip()
    data = _parse_markup_doc(doc_text)
    if data is None:
        return (
            "That upload is not a readable markup document -- it does not "
            "parse as the ModelMarkupDoc the viewer sends. Treat it as no "
            "markup: ask the user to press Send in the model viewer again."
        )

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return (
            "There is no active document to validate the markup against. Open "
            "the document the marks were made on, then call read_model_markup "
            "again."
        )

    # The publish the document says it was drawn on. NEVER published_record(None)
    # here: None means "the latest", and validating a mark against whatever
    # happened to be published most recently -- possibly a different export
    # entirely -- is exactly the wrong-answer this tool exists to prevent.
    publish_id = data["publish_id"]
    publish_record = model_server.published_record(publish_id) if publish_id else None
    resolved = [_resolve_mark(doc, publish_record, mark) for mark in data["marks"]]

    which = "The newest markup document" if index == 0 else f"The markup document {index} back"
    lines = [
        f"{which} from the model viewer ({len(resolved)} face mark(s)"
        + (f", caption: {data['caption']!r}" if data["caption"] else "")
        + f"), saved at {record['path']}."
    ]
    if publish_record is not None:
        lines.append(
            f"It was drawn on export {publish_record['id']} of "
            f"{len(publish_record.get('objects') or {})} object(s)."
        )

    confident = [r for r in resolved if r["confident"]]
    degraded = [r for r in resolved if not r["confident"]]
    if not resolved:
        lines.append(
            "The document carries no face marks -- only "
            + (f"the caption {data['caption']!r}. " if data["caption"] else "nothing. ")
            + "Ask the user to click the face(s) they mean in the model viewer "
            "and press Send again."
        )
        return "\n".join(lines)

    lines.append(
        "Face references, each re-validated against the current document:"
    )
    for r in confident:
        note = f' -- note: "{r["note"]}"' if r["note"] else ""
        lines.append(f"- {r['object']}.{r['face_name']}{note}")
    for r in degraded:
        lines.append(_degraded_line(r))
    if degraded:
        lines.append(
            "The WARNING lines above are NOT safe to script against as named "
            "faces -- the geometry may have moved under the mark. Re-locate "
            "each one (e.g. nearest face to its recorded point) before using "
            "it, because confidently naming the wrong face is worse than "
            "naming none."
        )

    # -- the confirmation render -----------------------------------------
    # Server-side, on the GUI thread: a fresh offscreen render (never a client
    # screenshot) with the confidently-marked faces highlighted via per-face
    # diffuse colour. The references are the payload; a render that cannot
    # happen (no GL context, no GUI) degrades to a note, never a failure.
    marked_faces = {}
    for r in confident:
        marked_faces.setdefault(r["object"], []).append(r["face_index"])
    png_path = None
    if marked_faces:
        png_path, render_note = _render_highlight(doc, marked_faces)
        lines.append(
            "The confirmation render of the marked faces is attached -- the "
            "orange faces are the confident marks above."
            if png_path
            else f"Note: {render_note}"
        )

    text = "\n".join(lines)
    return (text, png_path) if png_path else text


def _render_highlight(doc, marked_faces):
    """Render a confirmation shot of the objects with confident marks, their
    marked faces painted in the viewer's own mark orange.

    Returns ``(png_path, "")`` or ``(None, why_not)`` -- the face references
    are the payload, so a failed render must not take them down with it. The
    shot goes through ``render._offscreen_shot`` exactly like capture_view:
    visibility isolated to the marked objects, everything restored on exit.
    """
    plan, _err = _resolve_camera_args({"view": _CONFIRM_VIEW})
    names = sorted(marked_faces)
    try:
        png_path = _artifact_path("captures", "model_markup", ".png")
    except OSError as exc:
        return None, f"the confirmation image could not be written: {exc}."
    try:
        with _offscreen_shot(doc, set(names), _DEFAULT_WIDTH, _DEFAULT_HEIGHT) as view:
            if view is None:
                return (
                    None,
                    "an offscreen view for the confirmation image could not be "
                    "created (no 3D view in this session) -- the face "
                    "references above still stand.",
                )
            err = _apply_camera_plan(view, plan)
            if err:
                return None, err
            with _face_highlight(doc, names, marked_faces):
                _save_view_png(view, png_path, _DEFAULT_WIDTH, _DEFAULT_HEIGHT)
    except Exception as exc:  # noqa: BLE001 - best-effort render; the text stands alone
        return None, f"the confirmation image could not be rendered: {exc!r}."
    return png_path, ""


@contextlib.contextmanager
def _face_highlight(doc, names, marked_faces):
    """Paint the marked faces of `names` in the highlight orange for the
    duration of the ``with``, then put every per-face colour back.

    The same save-then-mutate-then-restore discipline as
    ``render._shot_appearance`` -- which recolors every default-coloured object
    UNIFORMLY, the opposite of what a per-face highlight needs, so this is a
    separate helper and ``_shot_appearance`` is not called from here. It is
    entered INSIDE ``render._offscreen_shot``: the colours saved here are the
    ones the shot actually renders with, the restore puts those back, and
    ``_offscreen_shot``'s own finally restores the document's true originals
    afterwards -- so the document is untouched on every exit path.
    """
    saved = []
    for name in names:
        obj = doc.getObject(name)
        if obj is None:
            continue
        view_object = getattr(obj, "ViewObject", None)
        if view_object is None:
            continue
        colors = _read_face_colors(view_object)
        if not colors:
            continue
        try:
            face_count = len(obj.Shape.Faces)
        except Exception:  # noqa: BLE001 - no shape, nothing to highlight
            continue
        try:
            _write_face_colors(
                view_object,
                _highlight_colors(colors, face_count, marked_faces.get(name) or ()),
            )
        except Exception:  # noqa: BLE001 - leave it as it was
            continue
        saved.append((view_object, colors))
    try:
        yield
    finally:
        for view_object, colors in saved:
            try:
                _write_face_colors(view_object, colors)
            except Exception:  # noqa: BLE001 - best effort, like _shot_appearance
                pass


def _highlight_colors(saved, face_count, marked):
    """`saved` (the per-face diffuse RGBA list) with the 0-based `marked` face
    indices set to the highlight orange, padded to `face_count`.

    A saved list shorter than the face count means a uniformly-coloured
    object; FreeCAD repeats the last entry for the remaining faces, and this
    reproduces that rule so the painted shot matches what the property would
    have drawn anyway. Pure logic -- testable without FreeCAD.
    """
    colors = list(saved) if saved else []
    while len(colors) < face_count:
        colors.append(colors[-1] if colors else _HIGHLIGHT_RGBA)
    for face_index in marked:
        if 0 <= face_index < face_count:
            colors[face_index] = _HIGHLIGHT_RGBA
    return colors


def _rgba(value):
    """`value` (an SbColor or a 3/4-sequence) as a 4-tuple (r, g, b, a)."""
    if hasattr(value, "getValue"):
        value = value.getValue()
    parts = tuple(value)
    if len(parts) == 3:
        return parts + (0.0,)
    return parts


def _read_face_colors(view_object):
    """The per-face diffuse RGBA list of `view_object`, or None if unreadable.

    Two APIs, one datum: on FreeCAD 1.1 (the addon's target) the live read is
    ``ShapeAppearance.getDiffuseColors()``, backed underneath by the classic
    per-face ``DiffuseColor`` property that older builds (0.20/0.21) expose
    directly. Either may hold a single entry for a uniformly-coloured object
    -- ``_highlight_colors`` reproduces the repeat-last-entry rule when it
    pads. Never raises.
    """
    shape_appearance = getattr(view_object, "ShapeAppearance", None)
    if shape_appearance is not None:
        getter = getattr(shape_appearance, "getDiffuseColors", None)
        if callable(getter):
            try:
                colors = list(getter())
            except Exception:  # noqa: BLE001 - fall through to the next form
                colors = None
            if colors:
                return [_rgba(c) for c in colors]
        try:
            colors = [_rgba(m.DiffuseColor) for m in shape_appearance]
        except Exception:  # noqa: BLE001
            colors = []
        if colors:
            return colors
    try:
        colors = list(view_object.DiffuseColor)
    except Exception:  # noqa: BLE001
        return None
    return [_rgba(c) for c in colors] if colors else None


def _write_face_colors(view_object, colors):
    """Set the per-face diffuse colors of `view_object`. Returns True on success.

    Reassigns the WHOLE ``ShapeAppearance`` tuple as fresh ``Material``
    instances, one per entry in `colors` -- NOT the classic per-face
    ``DiffuseColor`` list property, and not ``ShapeAppearance.
    setDiffuseColors`` either. Measured directly, not assumed: on a real
    FreeCAD 1.1 build, writing the colour data through either of those
    updates the stored values but not Coin's material BINDING, and an
    offscreen FBO grab (unlike the live interactive view, which never
    exposed the problem) then renders the shape as edges only -- every face
    silently unfilled. Comparing a plain ``render._offscreen_shot`` capture
    against one that additionally wrote per-face colours this way was what
    surfaced it: identical shape, identical camera, wireframe only once a
    colour list had been assigned via the property.

    ``render._set_diffuse`` already reassigns ``ShapeAppearance`` wholesale
    for exactly this reason, one material at a time (for the shot's single
    pale body colour); this is that same pattern fanned out to every entry
    in `colors`, preserving each material's other fields (ambient, emissive,
    specular, shininess, transparency) from whatever was there before --
    only ``DiffuseColor`` changes.

    Falls back to the classic property only where ``ShapeAppearance`` is
    absent entirely (pre-1.0 builds); that path is untested for offscreen
    rendering and may carry the same limitation, since it's the same
    property whose write behaviour is what turned out to be the problem.
    """
    import FreeCAD

    shape_appearance = getattr(view_object, "ShapeAppearance", None)
    if shape_appearance:
        base = shape_appearance[0]
        materials = []
        for index, rgba in enumerate(colors):
            source = shape_appearance[index] if index < len(shape_appearance) else base
            material = FreeCAD.Material()
            material.AmbientColor = source.AmbientColor
            material.DiffuseColor = rgba
            material.EmissiveColor = source.EmissiveColor
            material.SpecularColor = source.SpecularColor
            material.Shininess = source.Shininess
            material.Transparency = source.Transparency
            materials.append(material)
        try:
            view_object.ShapeAppearance = tuple(materials)
            return True
        except Exception:  # noqa: BLE001 - fall through to the classic property
            pass
    try:
        view_object.DiffuseColor = list(colors)
        return True
    except Exception:  # noqa: BLE001
        return False
