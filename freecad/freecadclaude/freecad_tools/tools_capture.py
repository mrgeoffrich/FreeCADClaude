# SPDX-License-Identifier: LGPL-2.1-or-later
"""capture_view / capture_user_view / crop_view -- the raster screenshots.

capture_view auto-frames an offscreen camera the model controls; crop_view
re-renders a sub-region of the last one; capture_user_view grabs the user's
own on-screen view exactly as painted.
"""

from .geometry import (
    _EXTENT_KEYS,
    _EXTENT_SCHEMA_PROPS,
    _crop_bbox,
    _document_bbox,
    _extent_args,
    _extent_report,
)
from .render import (
    _VIEW_PREF_PATH,
    _apply_camera_plan,
    _close_offscreen_view,
    _crop_camera_frame,
    _frame_camera_on_box,
    _last_capture,
    _looks_blank,
    _offscreen_view,
    _orbit_angles_from_view,
    _resolve_camera_args,
    _save_view_png,
)
from .session import _artifact_path
from .visibility import (
    _isolate_visibility,
    _restore_selection,
    _restore_visibility,
    _suspend_selection,
    _visibility_keep_set,
)

_CAPTURE_VIEW_SCHEMA = {
    "name": "capture_view",
    "description": (
        "Take a PNG screenshot of the active document's 3D geometry and return "
        "it inline. Renders through a separate offscreen "
        "camera, so it never disturbs whatever view/tab the user has open. Use "
        "for 3D solids/assemblies (for flat 2D geometry, prefer view_sketch_svg). "
        "Set the camera angle EITHER with 'view' (a named preset: iso, front, "
        "rear, top, bottom, left, right -- the default is iso) OR with "
        "'azimuth'+'elevation' in degrees for any custom orbit angle. Azimuth "
        "swings around the vertical axis: 0 faces the front, +90 the right "
        "side, 180 the back, -90 the left. Elevation tilts above/below eye "
        "level: 0 is side-on, +90 looks straight down from the top, -90 "
        "straight up from below. So a 3/4 view from above-front-right is about "
        "azimuth 45, elevation 30; to see a feature from below-left try azimuth "
        "-45, elevation -30. The chosen object(s) are always framed to fit, so "
        "changing the angle re-frames predictably (object centred) rather than "
        "moving the camera nearer/further. "
        "Optionally zoom to a region by giving one or more of x_min/x_max/"
        "y_min/y_max/z_min/z_max (world mm) -- any axis you omit uses the full "
        "document extent, so e.g. for 'top' you'd typically only give x_min/"
        "x_max/y_min/y_max. To instead zoom into part of the image you just got "
        "back -- without working out world coordinates -- use crop_view, which "
        "re-renders a sub-region you point at in normalized 0-1 image space."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "objects": {
                "type": "array", "items": {"type": "string"}, "minItems": 1,
                "description": (
                    "REQUIRED. Internal Names (from get_objects, e.g. 'Body', "
                    "'Box001' -- NOT Labels) of the object(s) to show. Only these "
                    "are made visible for the shot and everything else in the "
                    "document is hidden, so the capture is precisely controlled "
                    "and auto-framed on exactly them. Naming a container (an "
                    "App::Part/Group, or a PartDesign Body) shows its contents. "
                    "The user's real view is left untouched -- prior visibility is "
                    "restored right after the capture."
                ),
            },
            "view": {"type": "string", "description": "Camera preset: iso/front/rear/top/bottom/left/right (default iso). Ignored when azimuth/elevation are given."},
            "azimuth": {"type": "number", "description": "Custom orbit angle around the vertical axis, degrees: 0=front, +90=right, 180=back, -90=left. Use with elevation for an angle no preset covers."},
            "elevation": {"type": "number", "description": "Custom orbit angle above/below eye level, degrees: 0=side-on, +90=straight down (top), -90=straight up (bottom). Use with azimuth."},
            "width": {"type": "integer", "description": "Image width px (default 1280)"},
            "height": {"type": "integer", "description": "Image height px (default 960)"},
            **_EXTENT_SCHEMA_PROPS,
        },
        "required": ["objects"],
        "additionalProperties": False,
    },
}


def _run_capture_view(args):
    import FreeCAD
    import FreeCADGui

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return "No active document."

    names = args.get("objects")
    if not names:
        return ("capture_view requires 'objects': a list of object Names to show "
                "(everything else is hidden for the shot). Call get_objects first.")
    for n in names:
        if doc.getObject(n) is None:
            return f"No object named '{n}'."
    keep_set = _visibility_keep_set(doc, names)

    plan, err = _resolve_camera_args(args)
    if err:
        return err
    orbit = plan["orbit"]
    label = plan["label"]
    if orbit:
        azimuth, elevation = plan["azimuth"], plan["elevation"]
    else:
        view_arg = plan["view_arg"]

    view, subwindow, prev_view = _offscreen_view(doc)
    if view is None:
        return "Could not create an offscreen view to capture."

    # Toggling Visibility dirties the GUI document; snapshot the flag so we can
    # restore it (we net-restore the visibility values themselves too).
    gui_doc = FreeCADGui.getDocument(doc.Name)
    prev_modified = getattr(gui_doc, "Modified", None)

    # 1280x960 (1.23 MP) sits near Claude's image ceiling (~1.15-1.2 MP / 1568px
    # long edge); larger just gets downscaled again, so this is the detail sweet spot.
    width = int(args.get("width", 1280))
    height = int(args.get("height", 960))
    png_path = _artifact_path("captures", label, ".png")
    extents = _extent_args(args)

    warnings = []
    measured = None
    saved = []
    saved_sel = []
    try:
        # Show only the requested objects; fitAll (in _apply_camera_plan) then
        # frames tightly on exactly them. Restored in the finally below.
        saved = _isolate_visibility(doc, keep_set)
        saved_sel = _suspend_selection(doc)  # drop selection highlight for the shot
        if subwindow is not None:
            subwindow.resize(width, height)

        err = _apply_camera_plan(view, plan)
        if err:
            return err

        aspect = float(width) / float(height)
        if extents:
            scene_bbox = _document_bbox(doc)
            if scene_bbox.XMin <= scene_bbox.XMax or all(k in extents for k in _EXTENT_KEYS):
                crop_box = _crop_bbox(scene_bbox, extents)
                if not _frame_camera_on_box(view, crop_box, aspect):
                    warnings.append(
                        "Warning: could not frame the requested crop on this build -- "
                        "showing the full extent instead."
                    )
                    view.fitAll()
            else:
                warnings.append(
                    "Warning: the document has no real geometry to crop against -- "
                    "showing the full extent instead."
                )

        _save_view_png(view, png_path, width, height)

        # Don't silently hand back a black frame: if it's essentially empty, tell
        # Claude (and for a crop, retry once at the full fitAll frame) so it gets
        # a signal instead of burning turns re-shooting a blank it can't diagnose.
        if _looks_blank(png_path):
            if extents and not warnings:
                view.fitAll()
                _save_view_png(view, png_path, width, height)
                if _looks_blank(png_path):
                    warnings.append(
                        "Warning: the view is empty -- no visible geometry to show "
                        "(is everything hidden?)."
                    )
                else:
                    warnings.append(
                        "Warning: the requested crop region came out empty at this "
                        "camera angle -- showing the full view instead. Re-check the "
                        "x_min..z_max values against get_objects."
                    )
            elif not warnings:
                warnings.append(
                    "Warning: the view is empty -- no visible geometry to show at this "
                    "angle (is everything hidden, or is the object off to one side?)."
                )

        # Read back the actual camera angle so the result can report it (e.g.
        # what az/el 'iso' resolved to) -- direction is unchanged by fitAll or
        # saveImage, so measuring here matches the saved image.
        measured = _orbit_angles_from_view(view)
        # Remember this exact framing so crop_view can reproduce it and zoom
        # into a sub-region (getCamera() serializes the Inventor camera node;
        # setCamera() restores it -- independent of how the frame was set).
        try:
            _last_capture.update(
                camera=view.getCamera(), width=width, height=height,
                doc=doc.Name, keep=keep_set,
            )
        except Exception:  # noqa: BLE001 - crop_view just falls back to "capture first"
            _last_capture.update(camera=None)
    finally:
        _restore_visibility(saved)
        _restore_selection(saved_sel)
        if prev_modified is not None:
            try:
                gui_doc.Modified = prev_modified
            except Exception:  # noqa: BLE001
                pass
        _close_offscreen_view(subwindow, prev_view)

    # Report the resolved camera angle (measured from the real view direction,
    # so presets like iso report their concrete az/el too) and how to nudge it.
    if measured is not None:
        meas_az, meas_el = measured
    elif orbit:
        meas_az, meas_el = azimuth, elevation
    else:
        meas_az = meas_el = None

    if orbit:
        text = f"Captured a custom view of the 3D geometry, saved to {png_path}."
    else:
        text = f"Captured the {view_arg} view, saved to {png_path}."
    if meas_az is not None:
        text += (
            f" Camera angle: azimuth {meas_az:.0f} deg, elevation {meas_el:.0f} deg. "
            "To orbit from here, call capture_view again with adjusted azimuth/"
            "elevation (azimuth + swings right / - left; elevation + lifts the "
            "camera for a more top-down look / - drops it to look upward)."
        )
    framed_extents = _extent_report(_document_bbox(doc, names=keep_set))
    if framed_extents:
        text += f" Shown geometry spans {framed_extents} (world coords)."
    if warnings:
        text += "\n\n" + "\n".join(warnings)
    return text, png_path


_CAPTURE_USER_VIEW_SCHEMA = {
    "name": "capture_user_view",
    "description": (
        "Take a PNG screenshot of EXACTLY what the user is currently looking at "
        "in their own 3D view -- their real camera angle, zoom, pan, draw style "
        "(shaded/wireframe/etc.) and background. Unlike capture_view (which "
        "renders through a separate auto-framed offscreen camera and never "
        "touches the user's view), this reads the already-rendered pixels of "
        "the user's actual active view, so it never moves their camera or "
        "changes anything about the document either -- it's purely read-only. "
        "Reach for this when the user is pointing at or describing something "
        "in front of them right now ('look at this', 'why does this edge look "
        "wrong', 'see what I mean?') and you want to see precisely what they "
        "see, instead of guessing an angle with capture_view. Fails if the "
        "active tab isn't a 3D view (e.g. a Spreadsheet or TechDraw page is "
        "focused) -- ask the user to click into their 3D view and try again."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "width": {
                "type": "integer",
                "description": (
                    "Max image width px (default 1280). The user's real view's "
                    "current aspect ratio is preserved, so height follows "
                    "automatically -- this only caps output size."
                ),
            },
        },
        "required": [],
        "additionalProperties": False,
    },
}


def _run_capture_user_view(args):
    import FreeCAD
    import FreeCADGui

    view = FreeCADGui.activeView()
    if view is None or not hasattr(view, "saveImage"):
        return (
            "The active tab isn't a 3D view -- click into the user's 3D view "
            "(not a Spreadsheet/TechDraw/other tab) and try again."
        )

    width = int(args.get("width") or 1280)
    if width <= 0:
        width = 1280
    png_path = _artifact_path("captures", "user_view", ".png")

    # GrabFramebuffer reads whatever's already painted on screen -- the user's
    # real camera, draw style and background, unchanged -- as opposed to
    # FramebufferObject/CoinOffscreenRenderer, which re-render the scene (and
    # are what the throwaway offscreen views elsewhere in this file need, since
    # they're never actually painted). Only valid because this view IS visible.
    params = FreeCAD.ParamGet(_VIEW_PREF_PATH)
    prev_method = params.GetString("SavePicture", "")
    params.SetString("SavePicture", "GrabFramebuffer")
    try:
        # Passing only width (no height) -- the GrabFramebuffer backend scales
        # the captured framebuffer to this width and derives height from its
        # own aspect ratio, so the user's real window shape is preserved.
        view.saveImage(png_path, width)
    except Exception as exc:  # noqa: BLE001
        return f"Could not capture the active view: {exc!r}"
    finally:
        params.SetString("SavePicture", prev_method)

    return (
        "Captured exactly what the user currently sees in their 3D view, "
        f"saved to {png_path}."
    ), png_path


_CROP_VIEW_SCHEMA = {
    "name": "crop_view",
    "description": (
        "Zoom into a sub-region of the image from your LAST capture_view and "
        "re-render it at full resolution -- the way to read fine detail or a "
        "small feature. Give the region in normalized 0-1 coordinates of the "
        "image you just saw: (0,0) is the top-left corner, (1,1) the "
        "bottom-right, (0.5,0.5) the center. x1,y1 is the top-left of the crop "
        "and x2,y2 the bottom-right (so x1<x2 and y1<y2). Unlike cropping a "
        "static picture, this re-renders the 3D scene for that region, so you "
        "get genuinely sharper geometry, not just enlarged pixels. Call "
        "capture_view first; then call crop_view (repeatedly, narrowing in) to "
        "inspect any part of it more closely. It reuses the last capture's "
        "camera, so you don't pass a 'view'."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "x1": {"type": "number", "minimum": 0, "maximum": 1,
                   "description": "Left edge of the crop, 0-1 (0=left edge, 0.5=center)"},
            "y1": {"type": "number", "minimum": 0, "maximum": 1,
                   "description": "Top edge of the crop, 0-1 (0=top edge, 0.5=center)"},
            "x2": {"type": "number", "minimum": 0, "maximum": 1,
                   "description": "Right edge of the crop, 0-1 (must be > x1)"},
            "y2": {"type": "number", "minimum": 0, "maximum": 1,
                   "description": "Bottom edge of the crop, 0-1 (must be > y1)"},
        },
        "required": ["x1", "y1", "x2", "y2"],
        "additionalProperties": False,
    },
}


def _run_crop_view(args):
    import FreeCAD
    import FreeCADGui

    camera = _last_capture.get("camera")
    if not camera:
        return (
            "No image to crop yet -- call capture_view first, then crop_view to "
            "zoom into a region of it."
        )

    try:
        x1, y1, x2, y2 = (float(args[k]) for k in ("x1", "y1", "x2", "y2"))
    except (KeyError, TypeError, ValueError):
        return "Pass x1, y1, x2, y2 as numbers in 0-1 (top-left of the crop through bottom-right)."

    # Order/clamp so a swapped or out-of-range corner still yields a sane box.
    x1, x2 = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
    y1, y2 = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))
    if (x2 - x1) < 1e-3 or (y2 - y1) < 1e-3:
        return (
            "Crop region is empty or too small -- give x1<x2 and y1<y2 spanning a "
            "visible area of the last image (values in 0-1)."
        )

    doc = None
    doc_name = _last_capture.get("doc")
    if doc_name:
        try:
            doc = FreeCAD.getDocument(doc_name)
        except Exception:  # noqa: BLE001
            doc = None
    if doc is None:
        doc = FreeCAD.ActiveDocument
    if doc is None:
        return "The document from the last capture is no longer open -- capture_view again first."

    view, subwindow, prev_view = _offscreen_view(doc)
    if view is None:
        return "Could not create an offscreen view to capture."

    # Re-apply the same object isolation as the capture we're zooming into, so
    # the crop stays visually consistent with it. Restored in the finally.
    keep_set = _last_capture.get("keep") or set()
    gui_doc = FreeCADGui.getDocument(doc.Name)
    prev_modified = getattr(gui_doc, "Modified", None)

    width = int(_last_capture.get("width") or 1280)
    height = int(_last_capture.get("height") or 960)

    blank = False
    saved = []
    saved_sel = []
    try:
        saved = _isolate_visibility(doc, keep_set)
        saved_sel = _suspend_selection(doc)  # drop selection highlight for the shot
        if subwindow is not None:
            subwindow.resize(width, height)
        try:
            view.setCamera(camera)  # reproduce EXACTLY what Claude last saw
        except Exception as exc:  # noqa: BLE001
            return f"Could not reproduce the last camera to crop from: {exc!r}"

        if not _crop_camera_frame(view, x1, y1, x2, y2, float(width) / float(height)):
            return (
                "Could not zoom into the requested region on this build "
                "(the last view isn't an orthographic camera)."
            )

        png_path = _artifact_path("captures", "crop", ".png")
        _save_view_png(view, png_path, width, height)
        blank = _looks_blank(png_path)
    finally:
        _restore_visibility(saved)
        _restore_selection(saved_sel)
        if prev_modified is not None:
            try:
                gui_doc.Modified = prev_modified
            except Exception:  # noqa: BLE001
                pass
        _close_offscreen_view(subwindow, prev_view)

    text = (
        f"Zoomed into ({x1:.2f},{y1:.2f})-({x2:.2f},{y2:.2f}) of the last view and "
        f"re-rendered that region at full resolution, saved to {png_path}."
    )
    if blank:
        text += (
            "\n\nWarning: that region came out empty -- nothing there in the last "
            "view. Pick a sub-rectangle over a visible part of it, or capture_view "
            "again to reframe."
        )
    return text, png_path
