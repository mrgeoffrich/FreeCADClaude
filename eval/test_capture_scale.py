#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Scale derivation tests for the capture that goes to a device.

The pure half of the measurement path: how many millimetres a pixel of a
capture is worth (``render._ortho_mm_per_px``), whether the camera was pointed
somewhere that makes a measurement mean anything (``render._is_axis_aligned``,
``_projection_plane``), what the two combine into
(``render._capture_optics``), and the sentences read_device_image emits from
the result (``tools_device._measurement_note``).

None of it needs a GUI. The camera is a real ``SoOrthographicCamera`` node
where pivy is available -- that is what makes ``_ortho_camera``'s type check
part of the test rather than mocked away -- behind a stub view, since a
QGraphicsView is exactly the part freecadcmd has not got. What is NOT covered
here is the full round trip through ``_offscreen_shot``: that needs a realized
3D view, so the numbers below are checked against a camera whose height was set
by hand rather than by ``_frame_camera_on_box``.

    "C:\\Program Files\\FreeCAD 1.1\\bin\\freecadcmd.exe" /abs/path/to/eval/test_capture_scale.py

(freecadcmd needs an ABSOLUTE path -- given a relative one it silently runs
nothing and still exits 0.)

Exit: 0 = all passed, 1 = a failure.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The tools package, imported as a top-level `freecad_tools` rather than
# through `freecad.freecadclaude`: FreeCAD owns the `freecad` namespace package
# in its own installation, and a repo checkout can't shadow it. Every module
# under test keeps its FreeCAD imports inside functions (the contract that
# makes the package importable from any thread for its schema data), so this
# import costs nothing and pulls in no GUI.
sys.path.insert(0, os.path.join(_ROOT, "freecad", "freecadclaude"))

from freecad_tools import render, tools_device  # noqa: E402

_failures = []


def check(name, condition, detail=""):
    status = "ok  " if condition else "FAIL"
    print(f"  {status} {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(name)


class _Field:
    """The `.getValue()` shape of a Coin field, for the no-pivy fallback."""

    def __init__(self, value):
        self._value = value

    def getValue(self):
        return self._value


class _FakeCamera:
    def __init__(self, height):
        self.height = _Field(height)


class _View:
    """Just enough of a 3D view for _ortho_camera: it asks for the camera node
    and type-checks it. Everything else about a view is irrelevant to the
    scale, which is the point worth making by testing it this way."""

    def __init__(self, cam):
        self._cam = cam

    def getCameraNode(self):
        return self._cam


def _ortho_camera_node(height):
    """A real SoOrthographicCamera where pivy loads, else a duck-typed stand-in.

    The real node matters: `_ortho_camera` rejects anything that isn't one, so
    with the stand-in the type check is the untested part.
    """
    try:
        from pivy import coin

        cam = coin.SoOrthographicCamera()
        cam.height.setValue(height)
        return cam, True
    except Exception:  # noqa: BLE001
        return _FakeCamera(height), False


# -- axis alignment ---------------------------------------------------------
print("axis alignment")

for name, angles in [
    ("front", (0.0, 0.0)),
    ("rear", (180.0, 0.0)),
    ("right", (90.0, 0.0)),
    ("left", (-90.0, 0.0)),
    ("top", (0.0, 90.0)),
    ("bottom", (0.0, -90.0)),
]:
    check(f"{name} preset is axis-aligned", render._is_axis_aligned(angles))

# Straight down: azimuth is indeterminate there and _orbit_angles_from_view
# reports ~0, but the view is a true top view whatever it says.
check(
    "top view is axis-aligned at any azimuth",
    render._is_axis_aligned((137.0, 90.0)),
)
check("iso is NOT axis-aligned", not render._is_axis_aligned((45.0, 35.264)))
check("a 5 deg tilt is NOT axis-aligned", not render._is_axis_aligned((0.0, 5.0)))
check("a 5 deg swing is NOT axis-aligned", not render._is_axis_aligned((5.0, 0.0)))
check("no angles at all is not axis-aligned", not render._is_axis_aligned(None))

# -- the projection plane ---------------------------------------------------
print("projection plane")

check(
    "front says X/Z, depth unmeasurable",
    "X/Z" in render._projection_plane((0.0, 0.0))
    and "world Y" in render._projection_plane((0.0, 0.0)),
    render._projection_plane((0.0, 0.0)),
)
check(
    "rear also says X/Z",
    "X/Z" in render._projection_plane((180.0, 0.0)),
    render._projection_plane((180.0, 0.0)),
)
check(
    "right says Y/Z, width unmeasurable",
    "Y/Z" in render._projection_plane((90.0, 0.0))
    and "world X" in render._projection_plane((90.0, 0.0)),
    render._projection_plane((90.0, 0.0)),
)
check(
    "left also says Y/Z",
    "Y/Z" in render._projection_plane((-90.0, 0.0)),
    render._projection_plane((-90.0, 0.0)),
)
check(
    "top says X/Y, height unmeasurable",
    "X/Y" in render._projection_plane((0.0, 90.0))
    and "world Z" in render._projection_plane((0.0, 90.0)),
    render._projection_plane((0.0, 90.0)),
)
check(
    "an oblique camera says everything is foreshortened",
    "foreshortened" in render._projection_plane((45.0, 35.0)),
    render._projection_plane((45.0, 35.0)),
)

# -- mm per pixel -----------------------------------------------------------
print("mm per pixel")

cam, real_node = _ortho_camera_node(80.83)
print(f"  (camera node: {'real SoOrthographicCamera' if real_node else 'stand-in'})")

# The ortho camera's `height` IS the world height of the viewing volume, so
# this is a division and not an estimate: 80.83mm over 960px.
#
# Compared with a relative tolerance rather than exactly, and that is a fact
# about Coin rather than slack in the test: SoSFFloat is SINGLE precision, so a
# height written as 80.83 reads back as 80.83000183..., ~2e-8 relative. It
# vanishes in the 6dp rounding `_capture_optics` applies (checked below) and it
# is six orders of magnitude below anything measurable, but an exact-equality
# assertion here would fail on every real camera.
value = render._ortho_mm_per_px(cam, 960)
expected = 80.83 / 960.0
check(
    "height / pixel height, to the camera field's own precision",
    value is not None and abs(value - expected) <= expected * 1e-6,
    repr(value),
)
check("a doubled pixel height halves it", abs(render._ortho_mm_per_px(cam, 1920) - value / 2) < 1e-12)
check("no camera means no scale", render._ortho_mm_per_px(None, 960) is None)
check("a zero-pixel image means no scale", render._ortho_mm_per_px(cam, 0) is None)
zero, _ = _ortho_camera_node(0.0)
check("a degenerate camera height means no scale", render._ortho_mm_per_px(zero, 960) is None)

# -- the published optics ---------------------------------------------------
print("capture optics")

optics = render._capture_optics(_View(cam), 960, (0.0, 0.0))
check("front view is orthographic", optics["projection"] == "orthographic", repr(optics))
check("front view is axis-aligned", optics["axis_aligned"] is True)
check(
    "front view scale is exact",
    optics["scale"] is not None and optics["scale"]["confidence"] == "exact",
    repr(optics["scale"]),
)
check(
    "mm_per_px is rounded to 6dp",
    optics["scale"]["mm_per_px"] == round(80.83 / 960.0, 6),
    repr(optics["scale"]),
)

oblique = render._capture_optics(_View(cam), 960, (45.0, 35.264))
check("iso is not axis-aligned", oblique["axis_aligned"] is False)
# The whole point of the downgrade: Claude has to be able to tell "no
# measurement" from "a measurement you shouldn't machine to", and dropping the
# number makes those two identical.
check(
    "an oblique camera DOWNGRADES rather than dropping the number",
    oblique["scale"] is not None
    and oblique["scale"]["confidence"] == "approximate"
    and oblique["scale"]["mm_per_px"] > 0,
    repr(oblique["scale"]),
)

# A view whose camera node isn't orthographic (or isn't there at all): the
# "no scale available" signal, which is a state and not an error.
none = render._capture_optics(_View(object()), 960, (0.0, 0.0))
check("a non-ortho camera yields no scale", none["scale"] is None, repr(none))
check("...and says so as the projection", none["projection"] == "perspective", repr(none))
check("...while still reporting the angle it was at", none["axis_aligned"] is True)

# -- the sentences read_device_image emits ----------------------------------
print("measurement note")


def _meta(axis_aligned, scale, azimuth=0.0, elevation=0.0):
    return {
        "camera": {
            "azimuth": azimuth,
            "elevation": elevation,
            "projection": "orthographic",
            "axis_aligned": axis_aligned,
        },
        "scale": scale,
    }


aligned_note = tools_device._measurement_note(
    _meta(True, render._capture_optics(_View(cam), 960, (0.0, 0.0))["scale"])
)
check("an exact capture quotes mm per pixel", "mm per pixel" in aligned_note, aligned_note)
check("...and the projection plane", "X/Z plane" in aligned_note, aligned_note)
check("...and names the unmeasurable axis", "world Y" in aligned_note, aligned_note)
check("...without warning it off", "DOWNGRADE" not in aligned_note, aligned_note)

oblique_note = tools_device._measurement_note(
    _meta(False, oblique["scale"], azimuth=45.0, elevation=35.3)
)
check("an oblique capture still quotes the number", "mm per pixel" in oblique_note, oblique_note)
check("...marked as a downgrade", "DOWNGRADE" in oblique_note, oblique_note)
check("...saying every distance is foreshortened", "foreshortened" in oblique_note, oblique_note)
check(
    "...and telling Claude to confirm against the geometry",
    "describe_objects" in oblique_note,
    oblique_note,
)
check(
    "...while keeping the user's typed target as an instruction",
    "target_mm" in oblique_note and "instruction" in oblique_note,
    oblique_note,
)
check(
    "...and reporting the angle it was actually at",
    "azimuth 45" in oblique_note,
    oblique_note,
)

no_scale_note = tools_device._measurement_note(_meta(True, None))
check("no scale refuses millimetres outright", "pixel ratio" in no_scale_note, no_scale_note)
check(
    "...and points at where real numbers come from",
    "describe_objects" in no_scale_note or "get_sketch" in no_scale_note,
    no_scale_note,
)
# A capture published before this existed, or a photo: must not throw.
check("empty metadata is handled", "No scale" in tools_device._measurement_note({}))

print()
if _failures:
    print(f"FAIL: {len(_failures)} check(s) failed: {', '.join(_failures)}")
else:
    print("PASS")
# freecadcmd tears the process down on SystemExit WITHOUT flushing, so do it
# here or the entire report above is discarded and the run looks like it
# printed nothing at all. (Learned twice now; see test_device_server.py's tail.)
sys.stdout.flush()
sys.exit(1 if _failures else 0)
