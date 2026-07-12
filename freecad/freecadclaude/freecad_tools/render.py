# SPDX-License-Identifier: LGPL-2.1-or-later
"""The offscreen render path: a throwaway 3D view, its camera, and the PNG.

Shared by capture_view / crop_view / cutaway. None of this touches the
user's own view: an offscreen view is created, framed, grabbed, and closed.
_offscreen_shot wraps that whole setup/teardown -- it's what the three tools
actually enter; each then does only the part that makes it different (aim the
camera / insert a clip plane / replay the last camera and zoom).

capture_view and cutaway also share their whole front and back half -- the
argument validation (_capture_setup) and the result sentences
(_camera_angle_note / _shown_extents_note) -- plus the schema properties that
describe those arguments to Claude. Those live here too, so the two tools
cannot describe or handle the same knob differently.
"""

import contextlib

from .geometry import (
    _EXTENT_KEYS,
    _crop_bbox,
    _document_bbox,
    _extent_args,
    _extent_report,
)
from .visibility import (
    _isolate_visibility,
    _restore_selection,
    _restore_visibility,
    _suspend_selection,
    _visibility_keep_set,
)

_VIEW_PRESETS = {
    "iso": "viewIsometric", "isometric": "viewIsometric", "axonometric": "viewAxonometric",
    "front": "viewFront", "rear": "viewRear", "back": "viewRear", "top": "viewTop",
    "bottom": "viewBottom", "left": "viewLeft", "right": "viewRight",
}

#: View's own render-backend preference. Forced to an offscreen-safe value
#: for the duration of a capture (see _run_capture_view) -- "GrabFramebuffer"
#: reads whatever's currently painted on screen, which our throwaway view never has.
_VIEW_PREF_PATH = "User parameter:BaseApp/Preferences/View"

#: Background for saveImage on every raster capture (capture_view/crop_view/
#: cutaway). Black rather than white: FreeCAD's default shaded geometry is mid
#: grey, which is low-contrast on white -- a black backdrop makes the part's
#: silhouette and shading read far more clearly for the vision model. (The SVG
#: path keeps its white background: that's black line-art, not shaded geometry.)
_CAPTURE_BG = "Black"


def _mdi_subwindows():
    """The main window's current set of MDI subwindows (one per open document
    view/tab) -- diffed before/after creating a view to spot which subwindow
    it landed in, since FreeCAD's own Python view objects don't expose
    hide/show/close (those are plain Qt widget operations)."""
    from PySide import QtWidgets

    import FreeCADGui

    mdi_area = FreeCADGui.getMainWindow().findChild(QtWidgets.QMdiArea)
    return set(mdi_area.subWindowList()) if mdi_area else set()


def _force_flat_lines(view):
    """Force `view` to render every object shaded-with-edges ("Flat Lines"),
    regardless of each object's own DisplayMode or whatever draw style the
    user's real view currently happens to be set to -- so a capture always
    reads clearly instead of silently inheriting e.g. Wireframe/Points if
    that's what a particular object (or the user) is using elsewhere.

    setOverrideMode is per-viewer state on the throwaway Coin viewer this
    view owns; it never touches the user's real view or any ViewObject
    property. No-op on FreeCAD builds that predate the Python binding for
    View3DInventorViewer.setOverrideMode (FreeCAD/FreeCAD#19044, Jan 2025).
    """
    try:
        view.getViewer().setOverrideMode("Flat Lines")
    except Exception:  # noqa: BLE001
        pass


def _offscreen_view(doc):
    """A throwaway 3D view of `doc`, for capture_view to render through
    instead of whatever view/tab the user actually has open -- so a
    screenshot never hijacks their camera, and never fails just because a
    non-3D tab (e.g. a Spreadsheet) or a different document happens to be
    focused. Returns (view, subwindow, prev_view); view/subwindow may be
    None on failure.

    Gui::Document::createView() unconditionally shows and activates the new
    view (it exists for the "split view" feature, not headless use), so it
    briefly becomes the active tab while the capture runs. That's fine to let
    happen -- the whole tool call is one blocked GUI-thread event, so Qt never
    gets a turn to paint it anyway. An earlier version tried to hide the
    subwindow and restore focus immediately, before the capture even ran; that
    extra churn (deactivating/hiding a window Qt still considered "active")
    was what confused QMdiArea's own activation-history bookkeeping and left
    the user's tabbed layout scrambled after close() -- e.g. the Start tab or
    the document reappearing untabbed. Letting the new view become active
    normally, then closing it and reasserting `prev_view` exactly once (see
    _close_offscreen_view), is the sequence Qt's bookkeeping handles cleanly.

    prev_view is handed back to the caller so _close_offscreen_view can
    reactivate it once the throwaway subwindow is actually closed.
    """
    import FreeCADGui

    gui_doc = FreeCADGui.getDocument(doc.Name)
    if gui_doc is None:
        return None, None, None

    prev_view = FreeCADGui.activeView()
    before = _mdi_subwindows()
    view = gui_doc.createView("Gui::View3DInventor")
    if view is None:
        return None, None, prev_view

    # viewTop()/viewIsometric()/fitAll() etc. animate the camera over several
    # QTimer ticks by default and return before the animation finishes; since
    # the event loop never turns during this call, disable animation so those
    # calls apply immediately/synchronously instead of capturing mid-transition.
    view.setAnimationEnabled(False)
    _force_flat_lines(view)

    subwindow = next(iter(_mdi_subwindows() - before), None)
    return view, subwindow, prev_view


def _close_offscreen_view(subwindow, prev_view=None):
    """Tear down the throwaway view and hand focus back to whatever the user
    actually had open. Closing a QMdiSubWindow makes Qt re-pick an active
    subwindow via its own activation-history bookkeeping; reasserting
    `prev_view` afterwards makes sure that pick is the user's real previous
    view, not whatever QMdiArea happened to land on."""
    if subwindow is not None:
        try:
            subwindow.close()  # WA_DeleteOnClose -- also destroys the inner view
        except Exception:  # noqa: BLE001
            pass
    if prev_view is not None:
        try:
            import FreeCADGui

            FreeCADGui.getMainWindow().setActiveWindow(prev_view)
        except Exception:  # noqa: BLE001
            pass


@contextlib.contextmanager
def _offscreen_shot(doc, keep_names, width, height):
    """The whole scaffolding around a raster capture, entered by all three
    tools (capture_view / crop_view / cutaway): yields a throwaway view of
    `doc` sized to width x height, showing only `keep_names`, with the
    selection highlight suspended.

    On the way out it puts everything back -- visibility, selection, the GUI
    document's Modified flag (toggling Visibility dirties it, and a capture
    must not make the user's document look unsaved), and the view itself. That
    restore is the invariant that keeps a read-only capture actually read-only,
    so it lives here once rather than in three hand-copied `finally` blocks; it
    runs on every exit path, including an early `return` from inside the
    `with`.

    Yields None if no offscreen view could be created -- callers bail with
    their own message.
    """
    import FreeCADGui

    view, subwindow, prev_view = _offscreen_view(doc)
    if view is None:
        yield None
        return

    gui_doc = FreeCADGui.getDocument(doc.Name)
    prev_modified = getattr(gui_doc, "Modified", None)
    saved = []
    saved_sel = []
    try:
        saved = _isolate_visibility(doc, keep_names)
        saved_sel = _suspend_selection(doc)  # drop selection highlight for the shot
        if subwindow is not None:
            subwindow.resize(width, height)
        yield view
    finally:
        _restore_visibility(saved)
        _restore_selection(saved_sel)
        if prev_modified is not None:
            try:
                gui_doc.Modified = prev_modified
            except Exception:  # noqa: BLE001
                pass
        _close_offscreen_view(subwindow, prev_view)


def _camera_basis(cam):
    """(right, up, forward) unit FreeCAD.Vectors of an SoCamera's orientation in
    world coords -- forward is the look-along direction. Derived from the node's
    orientation quaternion so it's exact regardless of preset/orbit."""
    import FreeCAD

    q = cam.orientation.getValue().getValue()  # (x, y, z, w) quaternion
    rot = FreeCAD.Rotation(q[0], q[1], q[2], q[3])
    return (
        rot.multVec(FreeCAD.Vector(1, 0, 0)),
        rot.multVec(FreeCAD.Vector(0, 1, 0)),
        rot.multVec(FreeCAD.Vector(0, 0, -1)),
    )


def _ortho_camera(view):
    """`view`'s camera node iff it's an orthographic camera, else None. The
    analytic framing below only holds for orthographic projection (which is
    what capture_view uses); callers fall back to the plain fitAll frame
    otherwise."""
    try:
        from pivy import coin

        cam = view.getCameraNode()
        if cam is not None and cam.isOfType(coin.SoOrthographicCamera.getClassTypeId()):
            return cam
    except Exception:  # noqa: BLE001
        pass
    return None


def _frame_camera_on_box(view, box, aspect, margin=1.06):
    """Aim `view`'s ORTHOGRAPHIC camera at world BoundBox `box` and scale it so
    the box fills a viewport of `aspect` (= render width/height), by writing the
    camera fields directly.

    This replaces the old boxZoom-based crop, which worked in the offscreen
    viewer's pixel space -- but that throwaway view is never realized at the render
    size, so boxZoom's pixel math ran against a mismatched/degenerate viewport
    and mis-framed (blank images, or a sliver of unrelated geometry over-zoomed),
    worst of all under rotated iso/orbit cameras. Setting height/position/aspect
    on the camera node is viewport-independent, so it frames the same at any
    (or no) realized widget size. Returns True on success, False if the camera
    isn't orthographic or the box is degenerate (caller keeps the fitAll frame).
    """
    import FreeCAD

    cam = _ortho_camera(view)
    if cam is None:
        return False
    try:
        right, up, fwd = _camera_basis(cam)
        center = FreeCAD.Vector(
            (box.XMin + box.XMax) / 2.0,
            (box.YMin + box.YMax) / 2.0,
            (box.ZMin + box.ZMax) / 2.0,
        )
        # Half-extents of the box measured along the camera's own axes: how wide
        # (hu), tall (hv) and deep (hd) it is on screen. min/max over the 8
        # corners gives the tight screen-aligned bound for any orientation.
        hu = hv = hd = 0.0
        for cx in (box.XMin, box.XMax):
            for cy in (box.YMin, box.YMax):
                for cz in (box.ZMin, box.ZMax):
                    d = FreeCAD.Vector(cx, cy, cz) - center
                    hu = max(hu, abs(d.dot(right)))
                    hv = max(hv, abs(d.dot(up)))
                    hd = max(hd, abs(d.dot(fwd)))
        if hu <= 1e-9 and hv <= 1e-9:
            return False
        # Ortho height that contains the box both ways at the render's aspect.
        height = 2.0 * max(hv, hu / aspect) * margin
        if height <= 1e-9:
            return False
        # Ortho scale is set by `height`, not distance, so the standoff only has
        # to keep the box comfortably between the near/far planes.
        standoff = 2.0 * hd + height + 1.0
        eye = center - fwd * standoff
        cam.position.setValue(eye.x, eye.y, eye.z)
        cam.focalDistance.setValue(standoff)
        cam.aspectRatio.setValue(aspect)
        cam.height.setValue(height)
        pad = height * 0.1 + 1.0
        cam.nearDistance.setValue(max(1e-4, standoff - hd - pad))
        cam.farDistance.setValue(standoff + hd + pad)
        return True
    except Exception:  # noqa: BLE001 - any coin/API hiccup -> keep the fitAll frame
        return False


def _apply_extent_crop(view, doc, extents, aspect):
    """Re-frame `view` on the world-space crop `extents` (from _extent_args),
    defaulting any axis the caller omitted to the document's own extent --
    capture_view's and cutaway's shared x_min..z_max handling.

    Returns a warning string if the crop couldn't be honoured (in which case
    `view` is left on the full fitAll frame, so the caller still gets a usable
    image), else None.
    """
    scene_bbox = _document_bbox(doc)
    # An empty scene bbox is only fatal if the caller leaned on it for a default:
    # a fully-specified crop needs nothing from the document.
    if scene_bbox.XMin > scene_bbox.XMax and not all(k in extents for k in _EXTENT_KEYS):
        return (
            "Warning: the document has no real geometry to crop against -- "
            "showing the full extent instead."
        )
    if not _frame_camera_on_box(view, _crop_bbox(scene_bbox, extents), aspect):
        view.fitAll()
        return (
            "Warning: could not frame the requested crop on this build -- "
            "showing the full extent instead."
        )
    return None


def _crop_camera_frame(view, x1, y1, x2, y2, aspect):
    """Zoom `view`'s ORTHOGRAPHIC camera into the normalized sub-rectangle
    (x1,y1)-(x2,y2) of what it currently frames (0-1, y from the TOP), by
    offsetting and rescaling the camera node directly -- the viewport-independent
    equivalent of the old boxZoom, for crop_view. Returns True on success, False
    if the camera isn't orthographic."""
    import FreeCAD

    cam = _ortho_camera(view)
    if cam is None:
        return False
    try:
        right, up, _fwd = _camera_basis(cam)
        height = cam.height.getValue()
        vis_w = height * aspect
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        # Shift the eye laterally so the sub-rect's centre becomes the new centre
        # (image y grows downward, i.e. against +up).
        offset = right * ((cx - 0.5) * vis_w) + up * ((0.5 - cy) * height)
        p = cam.position.getValue().getValue()
        eye = FreeCAD.Vector(p[0], p[1], p[2]) + offset
        cam.position.setValue(eye.x, eye.y, eye.z)
        # Grow the smaller side to the render aspect so nothing is squashed.
        cam.height.setValue(max(height * max(y2 - y1, x2 - x1), 1e-4))
        cam.aspectRatio.setValue(aspect)
        return True
    except Exception:  # noqa: BLE001
        return False


def _save_view_png(view, png_path, width, height):
    """Render `view` to `png_path` at width x height on the _CAPTURE_BG
    background, forcing the FBO save method (and restoring the user's) -- the
    one place capture_view/crop_view/cutaway actually write an image."""
    import FreeCAD

    params = FreeCAD.ParamGet(_VIEW_PREF_PATH)
    prev_method = params.GetString("SavePicture", "")
    params.SetString("SavePicture", "FramebufferObject")
    try:
        view.saveImage(png_path, width, height, _CAPTURE_BG)
    finally:
        params.SetString("SavePicture", prev_method)


def _looks_blank(png_path):
    """True if the saved PNG is essentially just the render background (plus the
    tiny axis gizmo) -- i.e. the framing missed the geometry. Lets the capture
    tools tell Claude 'that came out empty' instead of silently handing back a
    background-only image it can't tell apart from a genuinely wrong/hidden model.

    Blank means "predominantly the KNOWN capture background" (`_CAPTURE_BG`), not
    "predominantly whatever colour the corner pixel happens to be". Sampling the
    corner as background looks right until a crop (crop_view, or capture_view with
    x_min..z_max) zooms entirely inside a solid face: then the whole frame -- corner
    included -- is the object's shaded grey, so a corner-relative test reads a
    perfectly-framed close-up as uniform "background" and fires a bogus 'came out
    empty' warning (and, on the capture_view path, throws the crop away for a
    fitAll fallback). Anchoring to the real background instead means a uniform
    *non-background* fill correctly counts as content. All three callers render via
    _save_view_png on _CAPTURE_BG, so this is exact for every one. Best-effort: any
    read failure returns False (assume not blank)."""
    try:
        from PySide import QtGui

        img = QtGui.QImage(png_path)
        if img.isNull() or img.width() == 0 or img.height() == 0:
            return False
        w, h = img.width(), img.height()
        bg = QtGui.QColor(_CAPTURE_BG)  # the background we actually rendered on
        if not bg.isValid():  # fall back to a corner only if the name won't parse
            bg = img.pixelColor(0, 0)
        br, bgc, bb = bg.red(), bg.green(), bg.blue()
        step = max(1, min(w, h) // 150)  # subsample to ~30k points
        content = total = 0
        y = 0
        while y < h:
            x = 0
            while x < w:
                c = img.pixelColor(x, y)
                if abs(c.red() - br) + abs(c.green() - bgc) + abs(c.blue() - bb) > 60:
                    content += 1
                total += 1
                x += step
            y += step
        return total > 0 and (content / total) < 0.004
    except Exception:  # noqa: BLE001
        return False


#: The exact camera + render size of the most recent capture_view, saved so
#: crop_view can reproduce the framing Claude just saw and zoom into a
#: sub-rectangle of it (see _run_crop_view). Written at the tail of
#: _run_capture_view while the offscreen view is still alive.
_last_capture = {"camera": None, "width": None, "height": None, "doc": None, "keep": None}


def _orbit_rotation(azimuth_deg, elevation_deg):
    """Camera orientation for an orbit-style (azimuth, elevation) angle around
    the model, in FreeCAD's Z-up world.

    azimuth: degrees around the vertical Z axis. 0 looks at the model's FRONT
      (camera on -Y, same as the 'front' preset); +90 swings to the right side
      (camera on +X), 180 to the back, -90 to the left. Turning right is +.
    elevation: degrees above the horizon. 0 is eye-level/side-on, +90 looks
      straight down (top), -90 straight up (bottom).

    Returns a FreeCAD.Rotation mapping the camera's local axes (X=right, Y=up,
    Z=toward the viewer) to world -- feed rot.Q to setCameraOrientation and
    then fitAll() to frame the model and fix the focal depth, exactly as
    FreeCAD's own BIM/Draft code does. The cardinal (azimuth, elevation) pairs
    reproduce the matching presets (verified against front/right/back/left/
    top/bottom), so orbit and preset framing agree.
    """
    import math

    import FreeCAD

    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    ca, sa = math.cos(az), math.sin(az)
    ce, se = math.cos(el), math.sin(el)
    # zc: unit vector from the model centre toward the eye (camera "backward").
    zc = FreeCAD.Vector(sa * ce, -ca * ce, se)
    # yc: screen-up = world +Z carried onto the view plane. This closed form is
    # exact even looking straight down/up (where projecting +Z would collapse):
    # there it tends to the in-plane heading, keeping up continuous.
    yc = FreeCAD.Vector(-sa * se, ca * se, ce)
    xc = yc.cross(zc)  # screen-right; completes a right-handed camera basis
    return FreeCAD.Rotation(xc, yc, zc, "ZXY")


def _orbit_angles_from_view(view):
    """The (azimuth, elevation) degrees describing where `view`'s camera
    currently sits, read back from its actual view direction -- the inverse of
    _orbit_rotation. Lets a preset (iso/front/...) report the concrete angle it
    resolved to, so the next capture_view can orbit a little off it. Returns
    None if the direction can't be read. Elevation is +/-90 looking straight
    down/up (azimuth is then indeterminate and reported as ~0)."""
    import math

    try:
        d = view.getViewDirection()  # unit vector the camera looks ALONG
    except Exception:  # noqa: BLE001
        return None
    # zc = model-centre -> eye = the reverse of the look direction.
    zx, zy, zz = -d.x, -d.y, -d.z
    norm = math.sqrt(zx * zx + zy * zy + zz * zz) or 1.0
    zx, zy, zz = zx / norm, zy / norm, zz / norm
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, zz))))
    azimuth = math.degrees(math.atan2(zx, -zy))  # inverse of the zc formula
    return azimuth, elevation


def _apply_camera_orientation(view, rot):
    """Aim `view`'s camera with a Base.Rotation (camera axes -> world), the way
    FreeCAD's BIM code does (setCameraOrientation(rot.Q) + fitAll). Returns True
    on success; the caller fitAll()s afterwards to frame the model and set the
    focal depth. Falls back to the raw Coin camera node if the high-level call
    is missing on some build."""
    try:
        view.setCameraOrientation(rot.Q)
        return True
    except Exception:  # noqa: BLE001 - drop to the underlying Coin camera node
        try:
            view.getCameraNode().orientation.setValue(list(rot.Q))
            return True
        except Exception:  # noqa: BLE001
            return False


def _resolve_camera_args(args):
    """Parse the shared capture_view/cutaway camera args into a plan, or an error.

    Angle comes EITHER from a named preset ('view') OR a custom orbit
    (azimuth/elevation degrees); azimuth/elevation win if given, otherwise fall
    back to the preset, defaulting to iso when 'view' is omitted too. Returns
    ``(plan, None)`` or ``(None, error_string)``. plan keys: ``orbit`` (bool);
    ``azimuth``/``elevation`` (floats, when orbit); ``preset`` (View method
    name) / ``view_arg`` (str, when preset); ``label`` (for the artifact name).
    """
    az_arg, el_arg = args.get("azimuth"), args.get("elevation")
    orbit = az_arg is not None or el_arg is not None
    if orbit:
        try:
            azimuth = float(az_arg) if az_arg is not None else 0.0
            elevation = float(el_arg) if el_arg is not None else 0.0
        except (TypeError, ValueError):
            return None, "azimuth and elevation must be numbers in degrees."
        # Elevation is a tilt above/below the horizon; past +/-90 you'd just
        # cross over to the other side, so clamp it. Azimuth wraps freely.
        elevation = max(-90.0, min(90.0, elevation))
        return {
            "orbit": True, "azimuth": azimuth, "elevation": elevation,
            "label": f"orbit_az{azimuth:g}_el{elevation:g}",
        }, None

    view_arg = str(args.get("view") or "").strip().lower() or "iso"
    preset = _VIEW_PRESETS.get(view_arg)
    if preset is None:
        return None, (
            f"Unknown 'view' {args.get('view')!r}. Pick one of: "
            f"{', '.join(sorted(set(_VIEW_PRESETS)))}, or pass azimuth/"
            "elevation (degrees) for a custom angle."
        )
    return {
        "orbit": False, "preset": preset, "view_arg": view_arg,
        "label": f"view_{view_arg}",
    }, None


def _apply_camera_plan(view, plan):
    """Aim `view`'s camera per a _resolve_camera_args plan, then fitAll to frame
    the model and set the focal depth. Returns an error string, or None."""
    if plan["orbit"]:
        if not _apply_camera_orientation(view, _orbit_rotation(plan["azimuth"], plan["elevation"])):
            return "Could not set a custom camera angle on this FreeCAD build -- use a named 'view' preset instead."
    elif hasattr(view, plan["preset"]):
        getattr(view, plan["preset"])()
    try:
        view.fitAll()
    except Exception:  # noqa: BLE001
        pass
    return None


# ---- the capture_view / cutaway common half --------------------------------
# The two tools differ only in what happens INSIDE the offscreen view (aim the
# camera vs. also insert a clip plane). Everything around that -- which objects
# to show, which camera angle, how big, which crop, and the sentences describing
# the result -- is identical, and so are the schema properties that ask Claude
# for it. Both halves live here so the pair cannot drift apart.


def _objects_schema_prop(what="to show", extra=""):
    """The REQUIRED 'objects' property of capture_view/cutaway."""
    description = (
        f"REQUIRED. Internal Names (from get_objects, e.g. 'Body', 'Box001' -- NOT "
        f"Labels) of the object(s) {what}. Only these are made visible for the shot "
        "and everything else in the document is hidden, so the shot is precisely "
        "controlled and auto-framed on exactly them. Naming a container (an "
        "App::Part/Group, or a PartDesign Body) shows its contents. The user's real "
        "view is left untouched -- prior visibility is restored right afterwards."
    )
    return {
        "type": "array", "items": {"type": "string"}, "minItems": 1,
        "description": description + (f" {extra}" if extra else ""),
    }


#: The camera-angle arguments, shared verbatim by capture_view and cutaway (see
#: _resolve_camera_args, which parses them).
_CAMERA_SCHEMA_PROPS = {
    "view": {
        "type": "string",
        "description": (
            "Camera preset: iso/front/rear/top/bottom/left/right (default iso). "
            "Ignored when azimuth/elevation are given."
        ),
    },
    "azimuth": {
        "type": "number",
        "description": (
            "Custom orbit angle around the vertical axis, degrees: 0=front, +90=right, "
            "180=back, -90=left. Use with elevation for an angle no preset covers."
        ),
    },
    "elevation": {
        "type": "number",
        "description": (
            "Custom orbit angle above/below eye level, degrees: 0=side-on, +90=straight "
            "down (top), -90=straight up (bottom). Use with azimuth."
        ),
    },
}

#: The render-size arguments, shared by capture_view and cutaway.
_SIZE_SCHEMA_PROPS = {
    "width": {"type": "integer", "description": "Image width px (default 1280)"},
    "height": {"type": "integer", "description": "Image height px (default 960)"},
}

#: 1280x960 (1.23 MP) sits near Claude's image ceiling (~1.15-1.2 MP / 1568px long
#: edge); larger just gets downscaled again, so this is the detail sweet spot.
_DEFAULT_WIDTH, _DEFAULT_HEIGHT = 1280, 960


def _capture_setup(args, tool_name):
    """Validate and resolve everything capture_view and cutaway need before they
    open an offscreen view: the active document, the required 'objects' list (and
    the visibility keep-set it expands to), the camera plan, the render size and
    the crop extents.

    Returns ``(setup, None)`` or ``(None, error_string)``. setup keys: ``doc``,
    ``keep_set``, ``plan``, ``width``, ``height``, ``extents``, ``aspect``.
    """
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return None, "No active document."

    names = args.get("objects")
    if not names:
        return None, (
            f"{tool_name} requires 'objects': a list of object Names to show "
            "(everything else is hidden for the shot). Call get_objects first."
        )
    for n in names:
        if doc.getObject(n) is None:
            return None, f"No object named '{n}'."

    plan, err = _resolve_camera_args(args)
    if err:
        return None, err

    width = int(args.get("width", _DEFAULT_WIDTH))
    height = int(args.get("height", _DEFAULT_HEIGHT))
    return {
        "doc": doc,
        "keep_set": _visibility_keep_set(doc, names),
        "plan": plan,
        "width": width,
        "height": height,
        "extents": _extent_args(args),
        "aspect": float(width) / float(height),
    }, None


def _measured_angles(measured, plan):
    """The (azimuth, elevation) a capture should report: what the view actually
    resolved to (_orbit_angles_from_view, read back from the real view direction
    -- so a preset like iso reports its concrete angle), falling back to the orbit
    angles the caller asked for, else None."""
    if measured is not None:
        return measured
    if plan["orbit"]:
        return plan["azimuth"], plan["elevation"]
    return None


def _camera_angle_note(angles):
    """The ' Camera angle: ...' sentence of a capture result, or ""."""
    if angles is None:
        return ""
    azimuth, elevation = angles
    return f" Camera angle: azimuth {azimuth:.0f} deg, elevation {elevation:.0f} deg."


def _shown_extents_note(doc, keep_set):
    """The ' Shown geometry spans ...' sentence of a capture result, or "".

    Lets Claude read the shown geometry's position and size in world coords --
    and, with the camera angle, work out which way X/Y/Z run in the image --
    without a follow-up get_objects call.
    """
    framed = _extent_report(_document_bbox(doc, names=keep_set))
    return f" Shown geometry spans {framed} (world coords)." if framed else ""
