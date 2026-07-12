# SPDX-License-Identifier: LGPL-2.1-or-later
"""cutaway -- capture_view with a Coin clip plane, to see inside a solid.

The cut is hollow (clipping exposes interior surfaces; it doesn't cap them).
The plane lives on a throwaway offscreen view, so the document is untouched.
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
    _apply_camera_plan,
    _close_offscreen_view,
    _frame_camera_on_box,
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

#: axis name -> index into (x, y, z), for cutaway's convenience axis mode.
_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def _resolve_clip_plane(args, doc, keep_names=None):
    """Build a Coin ``SbPlane`` for the cutaway from `args`.

    Two ways to specify it:
      - ``point`` [x,y,z] + ``normal`` [x,y,z]: an arbitrary plane; the half
        that is KEPT (drawn) is the side the normal points toward.
      - ``axis`` (x/y/z) + ``position`` (mm) + ``keep`` (low/high): a plane
        perpendicular to that axis. ``position`` defaults to the bbox midpoint
        on that axis of the SHOWN objects (``keep_names``, so a bare ``axis``
        just halves what's in the shot), falling back to the whole document;
        ``keep`` (default low) chooses the smaller- or larger-coordinate side.

    Returns ``(SbPlane, description, (nx, ny, nz), None)`` on success, or
    ``(None, None, None, error_string)``. The kept-side convention was verified
    against Coin: SoClipPlane keeps points where the plane's normal points, so
    the normal below always points at the half we want to see -- the normal is
    handed back too so the caller can tell whether the camera ended up looking
    INTO the kept half (reveals the cut) or AT its outer surface (looks
    unclipped) without re-deriving it.
    """
    from pivy import coin

    point, normal = args.get("point"), args.get("normal")
    if point is not None or normal is not None:
        if point is None or normal is None:
            return None, None, None, "For a general plane give BOTH 'point' [x,y,z] and 'normal' [x,y,z]."
        try:
            px, py, pz = (float(c) for c in point)
            nx, ny, nz = (float(c) for c in normal)
        except (TypeError, ValueError):
            return None, None, None, "'point' and 'normal' must each be a list of three numbers [x, y, z]."
        n = coin.SbVec3f(nx, ny, nz)
        if n.length() < 1e-9:
            return None, None, None, "'normal' must be a non-zero vector."
        plane = coin.SbPlane(n, coin.SbVec3f(px, py, pz))
        desc = f"a plane through ({px:g}, {py:g}, {pz:g}) with normal ({nx:g}, {ny:g}, {nz:g})"
        return plane, desc, (nx, ny, nz), None

    axis = str(args.get("axis") or "").strip().lower()
    if axis not in _AXIS_INDEX:
        return None, None, None, (
            "Give a clip plane: either 'axis' (x/y/z) [with optional 'position'/'keep'], "
            "or a general 'point' [x,y,z] plus 'normal' [x,y,z]."
        )
    idx = _AXIS_INDEX[axis]

    if args.get("position") is not None:
        try:
            position = float(args["position"])
        except (TypeError, ValueError):
            return None, None, None, "'position' must be a number (mm)."
    else:
        bbox = _document_bbox(doc, keep_names)
        if bbox.XMin > bbox.XMax:
            return None, None, None, "No geometry to pick a default cut position from -- give 'position' (mm)."
        position = ((bbox.XMin + bbox.XMax) / 2, (bbox.YMin + bbox.YMax) / 2,
                    (bbox.ZMin + bbox.ZMax) / 2)[idx]

    keep = str(args.get("keep") or "low").strip().lower()
    if keep not in ("low", "high"):
        return None, None, None, "'keep' must be 'low' (smaller coordinate) or 'high' (larger)."

    # Normal points toward the half we KEEP: -axis keeps the low side, +axis high.
    nvec, pvec = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    nvec[idx] = -1.0 if keep == "low" else 1.0
    pvec[idx] = position
    plane = coin.SbPlane(coin.SbVec3f(*nvec), coin.SbVec3f(*pvec))
    desc = f"{axis} = {position:g} mm, keeping the {keep} side"
    return plane, desc, tuple(nvec), None


def _insert_clip_plane(view, clip):
    """Insert `clip` (an SoClipPlane) into `view`'s scene graph so it clips all
    geometry in world space.

    Coin issues glClipPlane under the camera's viewing matrix, so the node must
    be traversed AFTER the camera or the plane lands in the wrong space.
    FreeCAD's getSceneGraph() may or may not include the camera: if it does
    (camera is a direct child), we place the clip right after it; if it doesn't
    (the viewer applies the camera in a super-scene the Python API doesn't
    return), the returned graph is already post-camera and index 0 is correct.
    Both arrangements were exercised against pivy; the fallback covers either.
    """
    from pivy import coin

    sg = view.getSceneGraph()
    try:
        search = coin.SoSearchAction()
        search.setType(coin.SoCamera.getClassTypeId())
        search.setInterest(coin.SoSearchAction.FIRST)
        search.apply(sg)
        path = search.getPath()
        if path is not None and path.getLength() == 2:
            parent, cam = path.getNodeFromTail(1), path.getTail()
            if parent.isOfType(coin.SoGroup.getClassTypeId()):
                idx = parent.findChild(cam)
                if idx >= 0:
                    parent.insertChild(clip, idx + 1)
                    return
    except Exception:  # noqa: BLE001 - fall back to the simple post-camera insert
        pass
    sg.insertChild(clip, 0)


_CUTAWAY_SCHEMA = {
    "name": "cutaway",
    "description": (
        "Screenshot like capture_view, but with a CLIP PLANE that slices the "
        "model open so you can see INSIDE it -- for inspecting internal features "
        "(bores, ribs, wall thickness, pockets, cavities). Renders through the "
        "same offscreen camera, so it never disturbs the user's view or the "
        "document. The cut is HOLLOW: the clip removes the near half and exposes "
        "the interior surfaces, but the cut face itself is open, not a filled "
        "cross-section.\n"
        "Define the plane EITHER the easy way with 'axis' (x/y/z) -- a plane "
        "perpendicular to that axis at 'position' mm (defaults to the model's "
        "midpoint on that axis, i.e. cut it in half), keeping the 'low' "
        "(smaller-coordinate) or 'high' side ('keep', default low) -- OR "
        "generally with 'point' [x,y,z] and 'normal' [x,y,z], where the kept "
        "half is the side the normal points toward.\n"
        "Like capture_view, this takes a REQUIRED 'objects' list of internal "
        "Names -- only those are shown (everything else is hidden for the shot) "
        "and the clip applies to just them; the user's real view is restored "
        "after.\n"
        "Set the camera angle exactly as capture_view does: a 'view' preset "
        "(iso/front/rear/top/bottom/left/right, default iso) OR 'azimuth'+"
        "'elevation' in degrees for a custom orbit. Optionally pass "
        "x_min/x_max/y_min/y_max/z_min/z_max (mm, same as capture_view) to crop "
        "the shot to a world-space region.\n"
        "Tip: aim the camera at the cut -- e.g. cut axis=x and view from the "
        "left/right, or cut axis=z and view top/bottom -- so you look squarely "
        "into the opened part. If a cut looks flat/empty/unchanged, change "
        "EXACTLY ONE of 'keep' or 'view' (not both -- flipping both together "
        "cancels out and lands back on the same unclipped-looking angle)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "objects": {
                "type": "array", "items": {"type": "string"}, "minItems": 1,
                "description": (
                    "REQUIRED. Internal Names (from get_objects -- NOT Labels) of "
                    "the object(s) to show and cut. Only these are made visible for "
                    "the shot; everything else in the document is hidden and the "
                    "clip applies to just them. Naming a container (App::Part/Group "
                    "or a PartDesign Body) shows its contents. The user's real view "
                    "is left untouched -- prior visibility is restored right after."
                ),
            },
            "axis": {"type": "string", "description": "Cut perpendicular to this axis: x, y, or z (the simple way to define the plane)."},
            "position": {"type": "number", "description": "Where along 'axis' to cut, in mm (default: the model's midpoint on that axis)."},
            "keep": {"type": "string", "description": "Which half of an 'axis' cut to keep: 'low' (smaller coordinate) or 'high' (default low)."},
            "point": {"type": "array", "items": {"type": "number"}, "description": "A point on the clip plane [x,y,z] in mm. Use with 'normal' for an arbitrary plane instead of 'axis'."},
            "normal": {"type": "array", "items": {"type": "number"}, "description": "Clip plane normal [x,y,z]; the kept (visible) half is the side it points toward. Use with 'point'."},
            "view": {"type": "string", "description": "Camera preset: iso/front/rear/top/bottom/left/right (default iso). Ignored when azimuth/elevation are given."},
            "azimuth": {"type": "number", "description": "Custom orbit angle around the vertical axis, degrees: 0=front, +90=right, 180=back, -90=left."},
            "elevation": {"type": "number", "description": "Custom orbit angle above/below eye level, degrees: 0=side-on, +90=top-down, -90=bottom-up."},
            **_EXTENT_SCHEMA_PROPS,
            "width": {"type": "integer", "description": "Image width px (default 1280)"},
            "height": {"type": "integer", "description": "Image height px (default 960)"},
        },
        "required": ["objects"],
        "additionalProperties": False,
    },
}


def _run_cutaway(args):
    import FreeCAD
    import FreeCADGui

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return "No active document."

    names = args.get("objects")
    if not names:
        return ("cutaway requires 'objects': a list of object Names to show "
                "(everything else is hidden for the shot). Call get_objects first.")
    for n in names:
        if doc.getObject(n) is None:
            return f"No object named '{n}'."
    keep_set = _visibility_keep_set(doc, names)

    plan, err = _resolve_camera_args(args)
    if err:
        return err

    try:
        plane, clip_desc, clip_normal, err = _resolve_clip_plane(args, doc, keep_set)
    except Exception as exc:  # noqa: BLE001 - e.g. pivy/coin unavailable
        return f"Could not build the clip plane: {exc!r}"
    if err:
        return err

    try:
        from pivy import coin
    except Exception as exc:  # noqa: BLE001
        return f"Could not load the Coin3D scene-graph library for clipping: {exc!r}"

    view, subwindow, prev_view = _offscreen_view(doc)
    if view is None:
        return "Could not create an offscreen view to capture."

    # Toggling Visibility dirties the GUI document; snapshot the flag to restore.
    gui_doc = FreeCADGui.getDocument(doc.Name)
    prev_modified = getattr(gui_doc, "Modified", None)

    width = int(args.get("width", 1280))
    height = int(args.get("height", 960))
    png_path = _artifact_path("captures", "cutaway", ".png")
    extents = _extent_args(args)

    crop_warning = None
    measured = None
    degenerate_warning = None
    saved = []
    saved_sel = []
    try:
        # Show only the requested objects; the clip then applies to just them and
        # fitAll frames them. Restored in the finally below.
        saved = _isolate_visibility(doc, keep_set)
        saved_sel = _suspend_selection(doc)  # drop selection highlight for the shot
        if subwindow is not None:
            subwindow.resize(width, height)

        # Clip THIS offscreen view's scene graph (world coords -- see
        # _insert_clip_plane for the camera-order nuance). It clips only this
        # throwaway view and is discarded when the view closes, so the user's
        # real view and the document stay untouched.
        clip = coin.SoClipPlane()
        clip.plane.setValue(plane)
        clip.on.setValue(True)
        try:
            _insert_clip_plane(view, clip)
        except Exception as exc:  # noqa: BLE001
            return f"Could not apply the clip plane to the view: {exc!r}"

        err = _apply_camera_plan(view, plan)
        if err:
            return err

        if extents:
            scene_bbox = _document_bbox(doc)
            if scene_bbox.XMin <= scene_bbox.XMax or all(k in extents for k in _EXTENT_KEYS):
                crop_box = _crop_bbox(scene_bbox, extents)
                if not _frame_camera_on_box(view, crop_box, float(width) / float(height)):
                    crop_warning = (
                        "Warning: could not frame the requested crop on this build -- "
                        "showing the full extent instead."
                    )
                    view.fitAll()
            else:
                crop_warning = (
                    "Warning: the document has no real geometry to crop against -- "
                    "showing the full extent instead."
                )

        # Direction is unchanged by fitAll, so this matches the saved image.
        measured = _orbit_angles_from_view(view)

        # Detect the "cut looks unclipped" degenerate case: the camera sits ON
        # the kept side, looking further into it, so the (removed) far half was
        # already hidden behind the intact near half -- nothing visibly changes
        # from an ordinary capture_view. That's the case exactly when the
        # camera's view direction points opposite the plane's kept-side normal
        # (dot near -1); dot near +1 means the camera is on the removed side
        # looking INTO the opened cavity, which is what reveals the cut.
        try:
            d = view.getViewDirection()
            dot = d.x * clip_normal[0] + d.y * clip_normal[1] + d.z * clip_normal[2]
        except Exception:  # noqa: BLE001
            dot = None
        if dot is not None and dot < -0.75:
            degenerate_warning = (
                "Warning: this camera angle looks straight at the KEPT half's outer "
                "surface, not into the cut -- the image likely looks identical to an "
                "uncropped capture_view. To actually see inside, change EXACTLY ONE "
                "of 'keep' or 'view' (not both -- flipping both together cancels out "
                "and lands back on this same angle): either keep this 'view' and flip "
                "'keep' to the other side, or keep 'keep' as-is and move the camera to "
                "the opposite side (e.g. the opposite 'view' preset, or azimuth+180)."
            )

        _save_view_png(view, png_path, width, height)
    finally:
        _restore_visibility(saved)
        _restore_selection(saved_sel)
        if prev_modified is not None:
            try:
                gui_doc.Modified = prev_modified
            except Exception:  # noqa: BLE001
                pass
        _close_offscreen_view(subwindow, prev_view)

    text = f"Cutaway at {clip_desc}, saved to {png_path}."
    if measured is not None:
        meas_az, meas_el = measured
        text += f" Camera angle: azimuth {meas_az:.0f} deg, elevation {meas_el:.0f} deg."
    framed_extents = _extent_report(_document_bbox(doc, names=keep_set))
    if framed_extents:
        text += f" Shown geometry spans {framed_extents} (world coords)."
    text += (
        " The cut is hollow -- you're seeing the interior surfaces the clip "
        "exposed, not a filled cross-section."
    )
    if degenerate_warning:
        text += f"\n\n{degenerate_warning}"
    if crop_warning:
        text += f"\n\n{crop_warning}"
    return text, png_path
