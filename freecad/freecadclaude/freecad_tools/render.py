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
import math

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
#: cutaway). Measured against the alternatives on a real part: this slate
#: blue-grey separates from FreeCAD's default shape grey by a colour distance of
#: 354 (black managed 350, white 189), so it gives _looks_blank the widest margin
#: AND -- the reason it beats the black it replaced -- a through-slot or bore
#: reads unmistakably as "background seen through the part" rather than as one
#: more dark face. (The SVG path keeps its white background: that's black
#: line-art, not shaded geometry.)
_CAPTURE_BG = "#3A4A5A"

#: Multisampling for the offscreen render. saveImage takes a 6th `samples`
#: argument that the addon never used to pass, so every capture was rendered with
#: NO anti-aliasing (the AntiAliasing preference is 0 by default too). Measured on
#: one iso view of a real part: samples 0 -> 38 unique colours and 97% of edges a
#: hard 1px step; samples 8 -> 212 colours and 6%. 16 renders identically to 8 on
#: this GL stack, so 8 is the ceiling worth asking for.
_MSAA_SAMPLES = 8

#: Appearance used for the shot: a pale body so the near-black edges actually
#: contrast against it, and a definite edge width. FreeCAD's default shape grey
#: (0.447) sits too close to its default edge colour (0.098) to read as an
#: outline at capture sizes.
_SHOT_DIFFUSE = (0.82, 0.84, 0.87, 0.0)
_SHOT_EDGE_COLOR = (0.0, 0.0, 0.0, 0.0)
_SHOT_LINE_WIDTH = 2.0

#: How the shot is drawn. 'shaded' and 'xray' both draw shaded-with-edges; they
#: differ only in Transparency, which has no draw-style equivalent and so goes
#: through the ViewObject (saved and restored like the rest of the appearance).
#: 'wireframe' is a draw style, so it rides the per-view override in
#: _force_draw_style -- no document mutation at all, and it dies with the
#: throwaway view. Setting a ViewObject's DisplayMode instead does nothing here,
#: and that is not a Coin quirk: _force_draw_style's override deliberately
#: outranks per-object modes so a capture can't inherit e.g. Points from one
#: object. Measured the confusing way first -- DisplayMode read back as
#: 'Wireframe' and the render came out shaded.
_STYLES = ("shaded", "wireframe", "xray")
_DEFAULT_STYLE = "shaded"

#: style -> the viewer override mode that draws it.
_OVERRIDE_MODES = {"shaded": "Flat Lines", "xray": "Flat Lines", "wireframe": "Wireframe"}
#: 60% reads as see-through while the silhouette and surface shading survive;
#: at 80% the form starts dissolving into the background.
_XRAY_TRANSPARENCY = 60

#: The draw-style argument, shared by capture_view and cutaway.
_STYLE_SCHEMA_PROPS = {
    "style": {
        "type": "string",
        "enum": list(_STYLES),
        "description": (
            "How to draw the shot. 'shaded' (default) is solid faces with edges "
            "-- the right pick for reading outer form. 'xray' keeps the shaded "
            "surface and silhouette but makes it semi-transparent so internal "
            "features show through: usually the best 'what's inside' view. "
            "'wireframe' draws every edge including the ones behind, hiding "
            "nothing -- the most literal see-through, but a busy image on a "
            "complex part. For a clean flat section through the solid rather "
            "than a see-through, use the cutaway tool instead."
        ),
    },
}


def _mdi_subwindows():
    """The main window's current set of MDI subwindows (one per open document
    view/tab) -- diffed before/after creating a view to spot which subwindow
    it landed in, since FreeCAD's own Python view objects don't expose
    hide/show/close (those are plain Qt widget operations)."""
    from PySide import QtWidgets

    import FreeCADGui

    mdi_area = FreeCADGui.getMainWindow().findChild(QtWidgets.QMdiArea)
    return set(mdi_area.subWindowList()) if mdi_area else set()


def _force_draw_style(view, style=_DEFAULT_STYLE):
    """Force how `view` draws, overriding each object's own DisplayMode and
    whatever draw style the user's real view currently happens to be set to --
    so a capture always reads clearly instead of silently inheriting e.g.
    Wireframe/Points if that's what a particular object (or the user) is using
    elsewhere.

    That deliberate outranking is also why a ViewObject's DisplayMode has no
    effect on a capture, and why `style` is the only way to change how a shot is
    drawn. The single place the override is set, so the default and the
    caller-chosen style can't be applied by two different mechanisms.

    setOverrideMode is per-viewer state on the throwaway Coin viewer this
    view owns; it never touches the user's real view or any ViewObject
    property. No-op on FreeCAD builds that predate the Python binding for
    View3DInventorViewer.setOverrideMode (FreeCAD/FreeCAD#19044, Jan 2025).
    """
    try:
        view.getViewer().setOverrideMode(_OVERRIDE_MODES.get(style, "Flat Lines"))
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
    _force_draw_style(view)

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


def _default_shape_rgb():
    """FreeCAD's configured default shape colour as an (r, g, b) 0..1 triple."""
    import FreeCAD

    raw = FreeCAD.ParamGet(_VIEW_PREF_PATH).GetUnsigned("DefaultShapeColor", 3435973887)
    return (((raw >> 24) & 0xFF) / 255.0,
            ((raw >> 16) & 0xFF) / 255.0,
            ((raw >> 8) & 0xFF) / 255.0)


def _set_diffuse(view_object, rgba):
    """Write one diffuse colour onto a ViewObject's ShapeAppearance material.

    ShapeAppearance is a tuple of materials; mutating a member in place doesn't
    stick, so the whole tuple has to be reassigned.
    """
    materials = view_object.ShapeAppearance
    material = materials[0]
    material.DiffuseColor = rgba
    view_object.ShapeAppearance = (material,) + tuple(materials[1:])


@contextlib.contextmanager
def _shot_appearance(doc, keep_names, style=_DEFAULT_STYLE):
    """Give the shot a pale body with dark, definite edges -- then put the
    document's own appearance back.

    ONLY objects still on FreeCAD's default shape colour are touched. That
    restriction is the whole design: a user who has colour-coded an assembly is
    carrying real information in those colours, and flattening them to grey to
    make a prettier picture would destroy the thing the picture is for. On one
    real document that is 22 objects normalised while the gold wall, the red/
    green/blue datum axes and the blue sketches keep their meaning.

    Restores on every exit path, like the visibility isolation it sits beside.
    """
    default = _default_shape_rgb()
    saved = []
    saved_transparency = []
    # PASS 1 -- record every value we might change, BEFORE changing any of them.
    # Setting Transparency on a Body propagates to the features inside it, so a
    # save-then-set-as-you-go loop reads an already-propagated value for objects
    # it reaches later and "restores" them to the shot's value. That leaked 60%
    # transparency into a real document; the split is what stops it.
    for obj in doc.Objects:
        if obj.Name not in keep_names:
            continue
        view_object = getattr(obj, "ViewObject", None)
        if view_object is None:
            continue
        props = view_object.PropertiesList
        # Transparency is NOT gated on the default-colour test: an xray that
        # left a user-coloured part opaque would hide exactly what the caller
        # asked to see through.
        if style == "xray" and "Transparency" in props:
            try:
                saved_transparency.append((view_object, view_object.Transparency))
            except Exception:  # noqa: BLE001
                pass
        if "ShapeAppearance" not in props:
            continue
        try:
            diffuse = view_object.ShapeAppearance[0].DiffuseColor
        except Exception:  # noqa: BLE001
            continue
        if any(abs(a - b) > 0.02 for a, b in zip(diffuse[:3], default)):
            continue  # user-chosen colour: leave it alone
        saved.append((view_object, tuple(diffuse),
                      getattr(view_object, "LineWidth", None),
                      getattr(view_object, "LineColor", None)))

    try:
        # PASS 2 -- apply, now that everything is recorded.
        for view_object, _diffuse, line_width, line_color in saved:
            try:
                _set_diffuse(view_object, _SHOT_DIFFUSE)
                if line_width is not None:
                    view_object.LineWidth = _SHOT_LINE_WIDTH
                if line_color is not None:
                    view_object.LineColor = _SHOT_EDGE_COLOR
            except Exception:  # noqa: BLE001 - leave it as it was
                pass
        for view_object, _transparency in saved_transparency:
            try:
                view_object.Transparency = _XRAY_TRANSPARENCY
            except Exception:  # noqa: BLE001
                pass
        yield
    finally:
        for view_object, diffuse, line_width, line_color in saved:
            try:
                _set_diffuse(view_object, diffuse)
                if line_width is not None:
                    view_object.LineWidth = line_width
                if line_color is not None:
                    view_object.LineColor = line_color
            except Exception:  # noqa: BLE001
                pass
        for view_object, transparency in saved_transparency:
            try:
                view_object.Transparency = transparency
            except Exception:  # noqa: BLE001
                pass


@contextlib.contextmanager
def _offscreen_shot(doc, keep_names, width, height, style=_DEFAULT_STYLE):
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
        # Per-view override: no document mutation, and it dies with the view.
        _force_draw_style(view, style)
        with _shot_appearance(doc, keep_names, style):
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


def _screen_half_extents(cam, box):
    """``(center, (hu, hv, hd))`` -- how wide, tall and deep world BoundBox `box`
    is along `cam`'s own axes, i.e. its on-screen proportions.

    min/max over the 8 corners gives the tight screen-aligned bound at any camera
    orientation. Shared by the framing (_frame_camera_on_box) and the auto render
    size (_fit_render_size) on purpose: the size we pick and the frame we set have
    to be measuring the same box the same way, or the image is letterboxed by
    exactly the amount they disagree.
    """
    import FreeCAD

    right, up, fwd = _camera_basis(cam)
    center = FreeCAD.Vector(
        (box.XMin + box.XMax) / 2.0,
        (box.YMin + box.YMax) / 2.0,
        (box.ZMin + box.ZMax) / 2.0,
    )
    hu = hv = hd = 0.0
    for cx in (box.XMin, box.XMax):
        for cy in (box.YMin, box.YMax):
            for cz in (box.ZMin, box.ZMax):
                d = FreeCAD.Vector(cx, cy, cz) - center
                hu = max(hu, abs(d.dot(right)))
                hv = max(hv, abs(d.dot(up)))
                hd = max(hd, abs(d.dot(fwd)))
    return center, (hu, hv, hd)


def _visible_extent(cam_height, aspect):
    """The world ``(width, height)`` an orthographic camera of `cam_height`
    actually shows in a viewport of `aspect` (= render width / height).

    `SoOrthographicCamera.height` is the world height of the view volume only
    while the viewport is wider than it is tall. Coin's viewport mapping scales
    the volume by 1/aspect at render time once the viewport goes portrait, so
    there `height` is the world WIDTH instead. Framing or measuring a portrait
    render as if `height` were the height zooms out by exactly 1/aspect.

    Measured, not derived from the Coin sources: `cam.getViewVolume(aspect)`
    reports the unadjusted volume at every aspect, so the scaling is only
    visible in a rendered image. Six renders at aspects 0.25-0.50 and three at
    1.14-2.28 fit these two branches to within the antialiased outline.
    """
    if aspect >= 1.0:
        return cam_height * aspect, cam_height
    return cam_height, cam_height / aspect


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
        center, (hu, hv, hd) = _screen_half_extents(cam, box)
        if hu <= 1e-9 and hv <= 1e-9:
            return False
        # The camera height whose _visible_extent contains the box both ways.
        if aspect >= 1.0:
            height = 2.0 * max(hv, hu / aspect) * margin
        else:
            height = 2.0 * max(hv * aspect, hu) * margin
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


def _framed_box(doc, keep_names, extents):
    """The world box a capture will actually frame: the SHOWN objects, narrowed
    by any crop axis the caller gave.

    ``keep_names`` matters, and leaving it off was a real bug: an omitted crop
    axis defaulted to the whole document's extent, so cropping x on one object
    out of 36 blew y and z out to everything else in the file. Measured on a
    122x6.6mm door in a document whose other parts sit near the origin: a
    requested 63mm-wide crop rendered the door at 4.9% of the frame height,
    jammed against the top edge -- a NARROWER crop came out worse than no crop
    at all. The framing then reported the right extents (_shown_extents_note
    already passed keep_set) while having framed a different box entirely.
    """
    base = _document_bbox(doc, names=keep_names)
    return _crop_bbox(base, extents) if extents else base


def _apply_extent_crop(view, doc, extents, aspect, keep_names=None):
    """Re-frame `view` on the world-space crop `extents` (from _extent_args),
    defaulting any axis the caller omitted to the extent of the objects being
    shown -- capture_view's and cutaway's shared x_min..z_max handling.

    Returns a warning string if the crop couldn't be honoured (in which case
    `view` is left on the full fitAll frame, so the caller still gets a usable
    image), else None.
    """
    scene_bbox = _document_bbox(doc, names=keep_names)
    # An empty scene bbox is only fatal if the caller leaned on it for a default:
    # a fully-specified crop needs nothing from the document.
    if scene_bbox.XMin > scene_bbox.XMax and not all(k in extents for k in _EXTENT_KEYS):
        return (
            "Warning: the shown objects have no real geometry to crop against -- "
            "showing the full extent instead."
        )
    # Via _framed_box, so the box framed here is by construction the same one
    # _fit_render_size then measures to pick the image shape.
    if not _frame_camera_on_box(view, _framed_box(doc, keep_names, extents), aspect):
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
        vis_w, vis_h = _visible_extent(height, aspect)
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        # Shift the eye laterally so the sub-rect's centre becomes the new centre
        # (image y grows downward, i.e. against +up).
        offset = right * ((cx - 0.5) * vis_w) + up * ((0.5 - cy) * vis_h)
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
    background, multisampled, forcing the FBO save method (and restoring the
    user's) -- the one place capture_view/crop_view/cutaway actually write an
    image.

    FramebufferObject matters: it renders offscreen at exactly the size asked
    for, where GrabFramebuffer would scale up whatever the throwaway widget
    happened to realize at.
    """
    import FreeCAD

    params = FreeCAD.ParamGet(_VIEW_PREF_PATH)
    prev_method = params.GetString("SavePicture", "")
    params.SetString("SavePicture", "FramebufferObject")
    try:
        try:
            view.saveImage(png_path, width, height, _CAPTURE_BG, "", _MSAA_SAMPLES)
        except Exception:  # noqa: BLE001
            # Builds without the samples argument (or a GL stack that refuses the
            # count) still take the 4-arg form: an aliased image beats no image.
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
_last_capture = {"camera": None, "width": None, "height": None, "doc": None,
                 "keep": None, "style": None}


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


#: The camera-angle arguments as capture_view and cutaway take them (see
#: _camera_schema_props for the one knob that varies, and _resolve_camera_args,
#: which parses them).
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


def _camera_schema_props(default_view="iso"):
    """:data:`_CAMERA_SCHEMA_PROPS` with a different documented default view.

    Only the default differs between the tools that aim a camera --
    send_to_device defaults face-on rather than iso (see its own note) -- so it
    is a parameter here rather than a second hand-written copy of the
    description. Two spellings of the same knob is exactly what keeping these
    schema fragments in render.py exists to prevent. Returns fresh dicts, so a
    caller merging them into its schema cannot mutate the shared one.
    """
    return dict(
        _CAMERA_SCHEMA_PROPS,
        view=dict(
            _CAMERA_SCHEMA_PROPS["view"],
            description=(
                "Camera preset: iso/front/rear/top/bottom/left/right (default "
                f"{default_view}). Ignored when azimuth/elevation are given."
            ),
        ),
    )


#: The render-size arguments, shared by capture_view and cutaway.
_SIZE_SCHEMA_PROPS = {
    "width": {"type": "integer", "description": "Image width px (default 1280)"},
    "height": {"type": "integer", "description": "Image height px (default 960)"},
}

#: 1280x960 (1.23 MP) sits near Claude's image ceiling (~1.15-1.2 MP / 1568px long
#: edge); larger just gets downscaled again, so this is the detail sweet spot.
_DEFAULT_WIDTH, _DEFAULT_HEIGHT = 1280, 960

#: Auto-size budget: the same 1.23 MP of detail, and the same 1568px long edge
#: past which Claude downscales anyway. Below ~2:1 the area binds and you get
#: 1280x960; above it the long edge does.
_PIXEL_BUDGET = _DEFAULT_WIDTH * _DEFAULT_HEIGHT
_MAX_LONG_EDGE = 1568
#: How far the auto size will stretch from square. A door 18x longer than it is
#: wide would otherwise ask for a 1568x85 letterbox -- past about 4:1 the extra
#: elongation stops buying visible detail and starts costing legibility, so the
#: remainder is left for crop_view to zoom into.
_MAX_AUTO_ASPECT = 4.0


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

    style = str(args.get("style") or _DEFAULT_STYLE).strip().lower()
    if style not in _STYLES:
        return None, (
            f"Unknown 'style' {args.get('style')!r}. Pick one of: "
            f"{', '.join(_STYLES)}."
        )

    width = int(args.get("width", _DEFAULT_WIDTH))
    height = int(args.get("height", _DEFAULT_HEIGHT))
    return {
        "doc": doc,
        "keep_set": _visibility_keep_set(doc, names),
        "plan": plan,
        "style": style,
        "width": width,
        "height": height,
        # An explicit size is honoured as given; only a defaulted one gets
        # reshaped to the geometry by _fit_render_size.
        "explicit_size": "width" in args or "height" in args,
        "extents": _extent_args(args),
        "aspect": float(width) / float(height),
    }, None


def _fit_render_size(view, doc, setup):
    """Shape the IMAGE to the framed geometry, rather than letterboxing the
    geometry into a fixed 4:3 image. Returns the ``(width, height)`` to save at.

    A 122x6.6mm door framed correctly into 1280x960 still lands in 6.8% of the
    image height with the rest black -- a right answer that is a useless picture,
    and in a logged session it's what made Claude abandon looking at the part and
    start reading it numerically instead (~1,700 Part.slice() calls, 2m46s of
    frozen GUI). Matching the image to the object puts the same door across ~94%
    of the width at 3x the height, for the same pixel budget.

    Must run AFTER _apply_camera_plan: how wide and tall a box looks depends on
    where the camera ended up pointing, so this reads the live camera basis
    rather than re-deriving it from the plan, which would duplicate the
    preset->orientation mapping and let the two drift.

    Falls back to the caller's size whenever anything is unclear (explicit size,
    no ortho camera, degenerate box, framing refused) -- the fallback is the old
    behaviour, so this can only improve a shot or leave it alone.
    """
    width, height = setup["width"], setup["height"]
    if setup["explicit_size"]:
        return width, height
    box = _framed_box(doc, setup["keep_set"], setup["extents"])
    cam = _ortho_camera(view)
    if cam is None or box.XMin > box.XMax:
        return width, height
    try:
        _, (hu, hv, _) = _screen_half_extents(cam, box)
        if hu <= 1e-9 or hv <= 1e-9:
            return width, height
        aspect = min(max(hu / hv, 1.0 / _MAX_AUTO_ASPECT), _MAX_AUTO_ASPECT)
        w = min(float(_MAX_LONG_EDGE), math.sqrt(_PIXEL_BUDGET * aspect))
        h = w / aspect
        if h > _MAX_LONG_EDGE:  # tall-and-thin: the long edge is the height
            h = float(_MAX_LONG_EDGE)
            w = h * aspect
        w, h = max(1, int(round(w))), max(1, int(round(h)))
        # fitAll (or the crop) framed for the OLD aspect, so a size change has to
        # re-frame or the object sits letterboxed in the new shape.
        if not _frame_camera_on_box(view, box, float(w) / float(h)):
            return width, height
        return w, h
    except Exception:  # noqa: BLE001 - any coin/API hiccup -> the asked-for size
        return width, height


# ---- what a PIXEL of the saved image is worth -------------------------------
# The capture tools hand Claude a picture; send_to_device hands it to a human
# who will MEASURE on it. That needs one number the image itself can't carry --
# millimetres per pixel -- and one caveat that matters more than the number:
# unprojecting a screen point through an ortho camera gives a ray, not a point,
# so a distance between two screen points is a distance IN THE PROJECTION
# PLANE. On a front view world X and Z are true and depth is unmeasurable; on
# an oblique view everything is foreshortened and the number is simply wrong.
#
# This lives in render.py because both facts are camera facts, and the camera
# is here. It is a pure read -- nothing below sets a field on anything, so a
# capture stays as non-mutating as it was.

#: How far off a world axis the camera may sit and still count as axis-aligned.
#: The presets land exactly on these values, so this is float slop rather than
#: a tolerance worth leaning on: half a degree of tilt already costs ~0.9mm at
#: the far edge of a 100mm part.
_AXIS_TOLERANCE_DEG = 0.5


def _is_axis_aligned(angles):
    """True when the camera looks straight down a world axis, so that two of
    the three world axes are true on screen.

    `angles` is an (azimuth, elevation) pair as _orbit_angles_from_view reads
    them back off the live view -- i.e. where the camera ACTUALLY ended up,
    which is why this can be asked about a preset and about a custom orbit in
    the same way. Looking straight down (elevation +/-90) counts whatever the
    azimuth is: azimuth is indeterminate there, and the view is a true top
    view either way.
    """
    if angles is None:
        return False
    azimuth, elevation = angles
    if abs(abs(elevation) - 90.0) <= _AXIS_TOLERANCE_DEG:
        return True
    if abs(elevation) > _AXIS_TOLERANCE_DEG:
        return False
    # Azimuth within tolerance of a multiple of 90 -- front/rear/left/right.
    return abs(((azimuth + 45.0) % 90.0) - 45.0) <= _AXIS_TOLERANCE_DEG


def _projection_plane(angles):
    """The sentence saying which world plane distances in this image are true
    in, and which axis is unmeasurable.

    Carried in the metadata rather than written as prose by whoever reports the
    capture, because it has to survive the whole round trip: it is published
    with the image, comes back attached to the annotation document the device
    sends, and is what read_device_image quotes. A caveat regenerated at the
    far end would be a guess about a camera that no longer exists.
    """
    if not _is_axis_aligned(angles):
        return (
            "the camera is not axis-aligned, so every distance in this image is "
            "foreshortened by an unknown amount"
        )
    azimuth, elevation = angles
    if abs(abs(elevation) - 90.0) <= _AXIS_TOLERANCE_DEG:
        return (
            "distances are true in the world X/Y plane; height (world Z) is not "
            "measurable from this image"
        )
    if abs(((azimuth + 90.0) % 180.0) - 90.0) <= _AXIS_TOLERANCE_DEG:
        return (
            "distances are true in the world X/Z plane; depth (world Y) is not "
            "measurable from this image"
        )
    return (
        "distances are true in the world Y/Z plane; width (world X) is not "
        "measurable from this image"
    )


def _ortho_mm_per_px(cam, width_px, height_px):
    """Millimetres per rendered pixel for orthographic camera node `cam`, or
    None.

    The image is rendered at exactly `width_px` x `height_px` (which
    _save_view_png guarantees by forcing the FBO save method), so the world
    height that _visible_extent reads off the camera at that aspect divides into
    it exactly: a division, not an estimate. The aspect comes from the render
    size rather than `cam.aspectRatio` because the two only agree when
    _frame_camera_on_box ran, and it has several paths that decline.

    None whenever this can't be a number -- a non-orthographic camera, or a
    degenerate size -- and that None is the "no scale available" signal, not an
    error.
    """
    if cam is None or width_px <= 0 or height_px <= 0:
        return None
    try:
        cam_height = float(cam.height.getValue())
    except Exception:  # noqa: BLE001 - any coin/API hiccup means no scale
        return None
    if cam_height <= 0.0:
        return None
    _, world_height = _visible_extent(cam_height, float(width_px) / float(height_px))
    return world_height / float(height_px)


def _capture_optics(view, width_px, height_px, angles):
    """Everything about the camera that a MEASUREMENT on the saved image
    depends on: ``{"projection", "axis_aligned", "scale"}``.

    **Call this after _apply_camera_plan AND after the final render size.**
    Both halves of mm/px move underneath it: _fit_render_size can re-frame the
    camera (changing `height`) and change the pixel height in the same call, so
    reading either one earlier gives a number for a shot that was never taken.
    It is the same ordering rule _fit_render_size itself documents, for the
    same reason -- how big the box looks depends on where the camera ended up.

    `scale` is None when there is no orthographic camera to derive it from. It
    is NOT dropped for an oblique camera: the confidence is downgraded to
    'approximate' and the number kept, because Claude has to be able to tell
    "no measurement" from "a measurement you shouldn't machine to", and an
    absent field makes those two identical.
    """
    cam = _ortho_camera(view)
    axis_aligned = _is_axis_aligned(angles)
    mm_per_px = _ortho_mm_per_px(cam, width_px, height_px)
    scale = None
    if mm_per_px is not None:
        scale = {
            # 6dp: mm/px runs around 0.08 on a typical capture, so this is far
            # below anything measurable and short enough to read in the JSON.
            "mm_per_px": round(mm_per_px, 6),
            "confidence": "exact" if axis_aligned else "approximate",
            "plane": _projection_plane(angles),
        }
    return {
        "projection": "orthographic" if cam is not None else "perspective",
        "axis_aligned": axis_aligned,
        "scale": scale,
    }


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
