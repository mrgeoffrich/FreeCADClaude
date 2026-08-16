# SPDX-License-Identifier: LGPL-2.1-or-later
"""send_to_device / read_device_image -- the round trip through the tablet.

The same two-halves shape as ``tools_annotate``, and for the same reason: the
user has to act in the middle, and a tool call must never block the GUI thread
waiting for them.

  send_to_device    render a view, publish it to the LAN server, return NOW.
                    The user's phone/tablet gets it over SSE, they draw on it.
  read_device_image re-read whatever they sent back, as an inline image.

Where this differs from annotate_view is only the transport -- an image editor
on this machine becomes a browser on a device across the room -- and everything
that made annotate_view work carries over unchanged: no pixel diffing (Claude
reads the marks off the picture, so the user can mark up however they like), and
the code's contribution is the context the picture can't hold, recorded at
PUBLISH time and replayed on the way back.

**Nothing in here runs on an HTTP thread.** Both functions execute on the GUI
thread like every other tool; the server is handed a finished PNG plus a plain
dict, and hands back plain dicts of its own. That is the whole of the "the HTTP
server never calls into FreeCAD" invariant in practice -- see device_server's
module docstring for the other half of it.
"""

import json
import os
import time

from .geometry import _document_bbox, _extent_report
from .render import (
    _apply_camera_plan,
    _camera_angle_note,
    _camera_schema_props,
    _capture_optics,
    _capture_setup,
    _fit_render_size,
    _looks_blank,
    _measured_angles,
    _objects_schema_prop,
    _offscreen_shot,
    _orbit_angles_from_view,
    _save_view_png,
    _shown_extents_note,
)
from .session import _artifact_path, _session_subdir

#: Told to the user (and to Claude) whenever the server isn't up. Names the
#: button, because "start the device server" is not a thing anyone can act on.
_NOT_RUNNING = (
    "The device server isn't running, so there's nowhere to send this. Ask the "
    "user to press the **Connect Mobile** button in the Claude panel "
    "and scan the code with their phone or tablet -- then call send_to_device "
    "again."
)


def device_upload_dir():
    """``<session>/mobile/``, created and pruned -- both directions of the round
    trip live here (``sent_*`` going out, ``upload_*`` coming back).

    Resolved HERE and handed to the server as a plain string, never resolved by
    the server itself: this goes through ``session_dir`` -> ``artifacts_dir``,
    which reads a FreeCAD preference, and an HTTP worker thread touching FreeCAD
    is precisely the thing the design refuses. Calling it also prunes the
    folder, which is the other reason every path in goes through it.
    """
    return _session_subdir("mobile")


def _with_face_on_default(args):
    """`args` with the camera defaulted to a face-on ``front`` view.

    capture_view defaults to iso, because an iso reads shape best. This one
    defaults face-on because the image is going somewhere the user will
    *measure* on: on an axis-aligned view two of the three world axes are true
    on screen, while on an oblique one every distance is foreshortened and any
    number read off it is simply wrong. (That is what the published scale
    metadata records; the default is worth having either way.) An explicit view
    or an azimuth/elevation still wins -- Claude asking for an angle means it
    wants that angle.
    """
    if args.get("view") or args.get("azimuth") is not None or args.get("elevation") is not None:
        return args
    return dict(args, view="front")


def _publish_context(doc, keep_set, angles):
    """The facts about the shot that the image itself can't carry: document,
    what's in it, its world extents, and the camera angle.

    Recorded at publish time and replayed verbatim by read_device_image -- the
    same reason ``_last_annotation["context"]`` is: these describe the PICTURE,
    not wherever the user's view (or the document) has moved on to by the time
    the marked-up copy comes back.
    """
    parts = [f"Document '{doc.Label}'."]
    if keep_set:
        shown = sorted(keep_set)
        listed = ", ".join(shown[:12]) + ("..." if len(shown) > 12 else "")
        parts.append(f"Objects in shot: {listed}.")
    extents = _shown_extents_note(doc, keep_set).strip()
    if extents:
        parts.append(extents)
    angle = _camera_angle_note(angles).strip()
    if angle:
        parts.append(angle)
    return " ".join(parts)


def _capture_meta(doc, names, keep_set, plan, angles, size, note, context, optics):
    """The metadata published alongside the PNG.

    Served verbatim by ``GET /api/latest`` (the web app reads ``document`` and
    ``view`` for its status bar, and ``scale``/``camera`` for its millimetres)
    and replayed by read_device_image, so it is one dict with two readers
    rather than two descriptions of the same capture.

    ``optics`` is render._capture_optics, read inside the offscreen view while
    the camera still exists. ``scale`` is null when there was no orthographic
    camera to derive one from -- "no scale known", which the web app renders as
    a pixel-only measurement rather than treating as an error.
    """
    return {
        "document": doc.Label,
        "objects": list(names),
        # None for a custom orbit -- there is no preset name to give.
        "view": None if plan["orbit"] else plan["view_arg"],
        "camera": {
            "azimuth": None if angles is None else round(angles[0], 1),
            "elevation": None if angles is None else round(angles[1], 1),
            "projection": optics["projection"],
            # The flag the whole measurement story turns on: false means every
            # distance in the image is foreshortened.
            "axis_aligned": optics["axis_aligned"],
        },
        "image": {"width": size[0], "height": size[1]},
        "extents": _extent_report(_document_bbox(doc, names=keep_set)),
        "note": note,
        # {"mm_per_px": float, "confidence": "exact"|"approximate",
        #  "plane": "..."}, or None.
        "scale": optics["scale"],
        # The sentence read_device_image replays. Carried in the metadata rather
        # than in a second table keyed by id, so there is one record of a
        # capture and the server is its only store.
        "context": context,
    }


_SEND_TO_DEVICE_SCHEMA = {
    "name": "send_to_device",
    "description": (
        "Render a view of the model and PUSH IT to the phone or tablet the user "
        "has paired, so they can draw on it with a pen and send it back. Use "
        "this when you need the user to show you WHERE rather than describe it "
        "('which corner?', 'how much wider?', 'mark the faces you want "
        "filleted') and they're working on a tablet -- it's the same round trip "
        "as annotate_view, but on the device in their hand instead of an image "
        "editor on the desktop.\n"
        "This returns as soon as the image is published; it does NOT wait. Tell "
        "the user it's on their device, ask them to mark it up and press Send, "
        "and then call read_device_image to see what came back. Nothing is "
        "detected by colour or shape -- you read the marks off the image, so any "
        "kind of marking works.\n"
        "The image goes out with the exact millimetres-per-pixel of its camera "
        "attached, so a dimension the user draws on it comes back as a real "
        "length. That is why this defaults to a face-on 'front' view rather "
        "than an angled one: on an axis-aligned view two of the three world "
        "axes are true on screen, while on an oblique projection every distance "
        "is foreshortened and the measurement is downgraded.\n"
        "Read-only -- it renders through a separate offscreen camera and never "
        "touches the user's view or the document. Requires the user to have "
        "pressed the Device button first."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "objects": _objects_schema_prop("to send to the device"),
            **_camera_schema_props("front"),
            "note": {
                "type": "string",
                "description": (
                    "Short line shown with the image on the device, saying what "
                    "you want marked (e.g. 'circle the boss you meant' or 'draw "
                    "where the slot should end')."
                ),
            },
        },
        "required": ["objects"],
        "additionalProperties": False,
    },
}


def _run_send_to_device(args):
    from .. import device_server

    # Checked before anything is rendered: an unpaired user is the common case
    # the first time, and there's no point spending an offscreen render on it.
    # Deliberately NOT auto-starting -- this binds a LAN interface, and only a
    # button press should ever do that (see device_server's docstring).
    if not device_server.is_running():
        return _NOT_RUNNING

    setup, err = _capture_setup(_with_face_on_default(args), "send_to_device")
    if err:
        return err
    doc, keep_set, plan = setup["doc"], setup["keep_set"], setup["plan"]
    width, height = setup["width"], setup["height"]
    note = str(args.get("note") or "").strip()
    try:
        png_path = _artifact_path("mobile", "sent_" + plan["label"], ".png")
    except OSError as exc:
        # A full disk (or an unwritable ArtifactsDir) fails here, before any
        # rendering, because _artifact_path creates the folder and the file.
        return (
            f"Could not create a file for this capture: {exc}. The disk may be "
            "full, or the FreeCADClaude artifacts folder may not be writable. "
            "Nothing was sent to the device."
        )

    blank = False
    measured = None
    optics = None
    # The whole capture path, reused as-is: visibility isolation, the throwaway
    # view, the appearance, the camera, the auto size and the restore. There is
    # no rendering code in this module on purpose -- a second copy would drift
    # from capture_view and the device would start showing something subtly
    # different from what Claude sees.
    with _offscreen_shot(doc, keep_set, width, height, setup["style"]) as view:
        if view is None:
            return "Could not create an offscreen view to render the capture."
        err = _apply_camera_plan(view, plan)
        if err:
            return err
        width, height = _fit_render_size(view, doc, setup)
        try:
            _save_view_png(view, png_path, width, height)
        except Exception as exc:  # noqa: BLE001 - saveImage raises FreeCAD's own
            # error type on a failed write, not OSError, so the write failure
            # cannot be caught by class. Narrow by SITE instead: this is one
            # call, and the only thing it does is write the PNG.
            return (
                f"Could not write the capture to {png_path}: {exc}. The disk may "
                "be full. Nothing was sent to the device."
            )
        blank = _looks_blank(png_path)
        measured = _orbit_angles_from_view(view)
        # LAST, and inside the view: mm/px is the world height the camera shows
        # over the pixel height, and both were only just settled (_fit_render_size
        # can re-frame the camera as well as resize the image). The view dies
        # at the end of this block, so it is read here or not at all.
        optics = _capture_optics(view, width, height, _measured_angles(measured, plan))

    angles = _measured_angles(measured, plan)
    context = _publish_context(doc, keep_set, angles)
    meta = _capture_meta(
        doc, args.get("objects") or [], keep_set, plan, angles,
        (width, height), note, context, optics,
    )
    # Hand the upload folder over on the same call: "New" mints a fresh session
    # id, so the folder the server should write into can have moved since it was
    # started, and this is the one moment the GUI thread is here to resolve it.
    try:
        upload_dir = device_upload_dir()
    except OSError:
        # Creating <session>/mobile/ failed (a full disk again). The capture
        # itself is already written, so publish it anyway and leave the server
        # on whatever folder it had -- refusing here would throw away a picture
        # over a problem that only bites on the way back.
        upload_dir = None
    device_server.publish(png_path, meta, upload_dir=upload_dir)

    where = "a custom view" if plan["orbit"] else f"the {plan['view_arg']} view"
    # "saved to <path>.png" is the phrasing chat_panel's _extract_capture_png
    # matches on, which is what puts a thumbnail of the pushed image in the
    # transcript -- the only place the user can see what went to their tablet.
    text = (
        f"Sent {where} to the user's device; it appears there straight away "
        f"(saved to {png_path})."
    )
    text += _camera_angle_note(angles)
    text += _shown_extents_note(doc, keep_set)
    text += (
        "\n\nTell the user it's on their device now, ask them to mark it up and "
        "press Send, and wait for them to say they have -- then call "
        "read_device_image. Don't call it before they've sent something back; "
        "it returns the newest image the device uploaded, which until then is "
        "an older one or nothing at all."
    )
    if blank:
        text += (
            "\n\nWarning: that view came out empty -- nothing visible at this "
            "angle, so the user has a blank picture to draw on. Check the "
            "object names and the angle, then send it again."
        )
    if optics["scale"] is None:
        text += (
            "\n\nNote: no scale could be derived for this shot, so anything the "
            "user measures on it comes back as pixels rather than millimetres."
        )
    elif not optics["axis_aligned"]:
        # Said here as well as in the payload because this is the one moment it
        # is still cheap to fix: another send_to_device with view='front' costs
        # a round trip, whereas discovering it when the marked-up image comes
        # back costs the user's drawing.
        text += (
            "\n\nNote: this camera angle is not axis-aligned, so every distance "
            "the user measures on it is foreshortened and comes back marked "
            "'approximate'. If they're going to give you dimensions off this "
            "picture, send a face-on view (view='front'/'top'/'right') instead."
        )
    return text


_READ_DEVICE_IMAGE_SCHEMA = {
    "name": "read_device_image",
    "description": (
        "Read an image the user sent FROM their phone or tablet, and return it "
        "inline so you can see it. Two quite different things arrive this way, "
        "and both are worth looking at:\n"
        "- a capture you sent with send_to_device, marked up with a pen -- "
        "circled features, arrows, handwritten numbers. The camera angle, the "
        "objects in shot and their world extents are replayed here from when it "
        "was sent, so a mark in the image ties back to a place in the model.\n"
        "- a photo or a drawing off the device with no capture involved: a "
        "sketch on paper, a reference part, a product shot. Read it as "
        "reference for what the user wants built.\n"
        "Marks the user MEASURED come back as structured numbers in the "
        "annotation document, not just as ink: each dimension carries "
        "'measured_mm' (what the picture says it is) and 'target_mm' (what they "
        "want it to be) -- two different facts, so act on the target and use "
        "the measurement to understand the delta. How far to trust the "
        "measurement is stated with it.\n"
        "Call it once the user says they've pressed Send. Defaults to the "
        "newest image; pass 'index' to look further back."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "index": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "How far back to look: 0 (default) is the newest image the "
                    "device sent, 1 the one before it, and so on."
                ),
            },
        },
        "additionalProperties": False,
    },
}


def _source_capture(doc_text):
    """The published capture an upload was drawn on, per the device's own
    annotation document -- or None if it wasn't drawn on one.

    Reads exactly one field (``source.id``) out of a payload whose schema
    belongs to the web app (``web/src/doc.ts``), and does it defensively: a
    photo straight off the camera has no ``source`` at all, and a document that
    grows fields -- or bumps its ``version`` -- must not be able to break this.
    """
    from .. import device_server

    try:
        data = json.loads(doc_text or "")
    except ValueError:
        return None
    source = data.get("source") if isinstance(data, dict) else None
    image_id = source.get("id") if isinstance(source, dict) else None
    return device_server.published_record(image_id) if image_id else None


def uploaded_caption(doc_text):
    """The annotation document's ``caption`` field, or ``""`` if there is
    none.

    Public (no leading underscore) and re-exported for chat_panel: a typed
    caption is what tells it whether to act on an upload immediately or just
    say it arrived (see ``ChatWidget._on_device_upload``). Same defensive
    shape as :func:`_source_capture` and for the same reason -- the
    document's schema belongs to the web app (``web/src/doc.ts``), so a
    malformed or absent one must read as "no caption", not raise.
    """
    try:
        data = json.loads(doc_text or "")
    except ValueError:
        return ""
    caption = data.get("caption") if isinstance(data, dict) else None
    return caption.strip() if isinstance(caption, str) else ""


def _measurement_note(meta):
    """What the numbers on this image are worth, spelled out.

    The projection-plane caveat is the whole point of this paragraph, and it is
    phrased for someone about to ACT on a number rather than to be technically
    complete. Unprojecting a screen point through an ortho camera gives a ray,
    not a point, so a distance measured on the picture is a distance in the
    projection plane: on a front view world X and Z are true and depth is
    unmeasurable; on an oblique view everything is foreshortened and the number
    is simply wrong. The three cases are deliberately different in tone --
    'measure freely', 'read the intent, confirm the value', 'this is not a
    length' -- because they need different behaviour, and a single hedge
    covering all three would be ignored in the case that matters.
    """
    camera = meta.get("camera") or {}
    scale = meta.get("scale") or {}
    mm_per_px = scale.get("mm_per_px")

    if not mm_per_px:
        return (
            "No scale was derived for this capture (there was no orthographic "
            "camera to derive one from), so nothing measured on this image can "
            "be quoted in millimetres. A dimension in the annotation document "
            "is a pixel ratio -- read it as 'this much of the picture', not as "
            "a length, and get real numbers from describe_objects or get_sketch."
        )

    plane = scale.get("plane") or ""
    if camera.get("axis_aligned") is False:
        azimuth, elevation = camera.get("azimuth"), camera.get("elevation")
        angle = ""
        if isinstance(azimuth, (int, float)) and isinstance(elevation, (int, float)):
            angle = _camera_angle_note((azimuth, elevation)).strip() + " "
        return (
            f"Measuring on this image: {mm_per_px:g} mm per pixel, but the "
            "confidence is only 'approximate' and that is a DOWNGRADE -- the "
            f"camera was not axis-aligned. {angle}Every distance in this "
            "image is foreshortened by an unknown amount, so any measured_mm "
            "in the annotation document is smaller than the real feature by an "
            "amount nothing here can recover. Read the user's INTENT off these "
            "numbers (which feature, roughly how much bigger), then confirm the "
            "real value with describe_objects or get_sketch before changing "
            "anything. A target_mm the user typed is still exactly what they "
            "asked for -- it is their instruction, not a measurement."
        )

    return (
        f"Measuring on this image: {mm_per_px:g} mm per pixel, confidence "
        f"'{scale.get('confidence') or 'exact'}'"
        + (f" -- {plane}" if plane else "")
        + ". A mark spanning the unmeasurable axis measures the projection, not "
        "the feature, so treat a dimension drawn across depth with suspicion. "
        "Everything in the projection plane is a real length and you can act on "
        "it directly."
    )


def _run_read_device_image(args):
    from .. import device_server

    try:
        index = max(0, int(args.get("index") or 0))
    except (TypeError, ValueError):
        index = 0

    uploads = device_server.uploads()
    if not uploads:
        if not device_server.is_running():
            return (
                "Nothing has come back from a device, and the device server "
                f"isn't running. {_NOT_RUNNING}"
            )
        return (
            "Nothing has come back from the device yet. The user has to pick or "
            "receive an image, mark it up and press Send. Ask them whether "
            "they've sent it, rather than polling this."
        )
    if index >= len(uploads):
        return (
            f"Only {len(uploads)} image(s) have come back from the device, so "
            f"index {index} doesn't exist -- 0 is the newest."
        )

    record = uploads[len(uploads) - 1 - index]
    path = record["path"]
    if not os.path.isfile(path):
        return (
            f"That upload is no longer on disk ({path}) -- the session folder "
            "keeps only its most recent files. Ask the user to send it again."
        )

    age = max(0, int(time.time() - record["received_at"]))
    which = "The newest image" if index == 0 else f"Image {index} back"
    parts = [
        f"{which} from the user's device, uploaded {age}s ago "
        f"({len(uploads)} received this session). Saved at {path}."
    ]

    doc_text = (record.get("doc") or "").strip()
    if doc_text:
        # Verbatim, not summarised: the device's annotation document is the half
        # of the payload carrying numbers and intent, and paraphrasing a number
        # is how a target dimension turns into a wrong one.
        parts.append(
            "The device sent this annotation document with it, verbatim:\n\n"
            f"```json\n{doc_text}\n```"
        )

    source = _source_capture(doc_text)
    if source is not None:
        meta = source.get("meta") or {}
        parts.append(
            "This is a capture you sent with send_to_device, drawn on. The "
            "context below was recorded when it was SENT -- it describes the "
            "picture, not wherever the document or the user's view has got to "
            f"since.\n\n{meta.get('context') or ''}".strip()
        )
        # Recorded at publish time and replayed here for the same reason as the
        # context: it describes the camera this picture was taken through, and
        # that camera no longer exists.
        parts.append(_measurement_note(meta))
    else:
        parts.append(
            "This did not come from a send_to_device capture -- it's an image "
            "off the device (a photo, a sketch, an existing drawing), so there "
            "is no camera angle or world scale for it. Treat any dimension you "
            "read on it as the user's own annotation, not a measurement."
        )

    parts.append(
        "Read the user's marks straight off the image. If it looks unmarked, "
        "say so and ask -- don't invent marks that aren't there."
    )
    return "\n\n".join(parts), path
