# SPDX-License-Identifier: LGPL-2.1-or-later
"""Per-part 3D printing metadata: which way up the part is printed.

Build direction decides which features print reliably -- an overhang, a bridge,
a bore or a thin wall all behave differently depending on how the layers stack
through them -- and none of it is derivable from the geometry. So it is recorded
against the part.

An ``App::PropertyEnumeration`` rather than free text or a bare vector: FreeCAD
renders it as a dropdown, refuses a value outside the list, and both the value
and the list survive a reload. ``App::PropertyDirection`` holds the odd
non-axis-aligned case, and is read only when the enum says ``Custom``.

The direction names the part-local axis that ends up pointing UP in world space
once the part is on the plate; layers stack along it. "+Z up" is the part
printed as modelled. "-Z up" means local -Z points up, so the part is printed
upside-down and the face at local +Z is the one resting on the plate.

Each reported direction carries the plate side with it. That is a negation away
from the direction itself, which is exactly the step that gets reversed under
load, and reversing it puts supports and bridging on the wrong face.
"""

_DIRECTION_PROP = "PrintDirection"
_CUSTOM_PROP = "PrintDirectionCustom"
_GROUP = "3D Print"

#: App::Prop_Output -- a write must not mark the part touched, which would
#: rebuild it and everything downstream on the next recompute.
_PROP_OUTPUT = 8

NOT_SET = "Not set"
NOT_PRINTED = "Not printed"
CUSTOM = "Custom"

#: The enumeration, in the order the dropdown shows it.
DIRECTIONS = (NOT_SET, NOT_PRINTED,
              "+X up", "-X up", "+Y up", "-Y up", "+Z up", "-Z up",
              CUSTOM)

#: Unit vector for each axis-aligned choice, in the part's local frame.
AXIS_VECTORS = {
    "+X up": (1.0, 0.0, 0.0), "-X up": (-1.0, 0.0, 0.0),
    "+Y up": (0.0, 1.0, 0.0), "-Y up": (0.0, -1.0, 0.0),
    "+Z up": (0.0, 0.0, 1.0), "-Z up": (0.0, 0.0, -1.0),
}

#: The local axis resting on the build plate -- the opposite of the up axis.
PLATE_SIDES = {
    "+X up": "-X", "-X up": "+X",
    "+Y up": "-Y", "-Y up": "+Y",
    "+Z up": "-Z", "-Z up": "+Z",
}

#: Stated once per payload that reports a direction, rather than assumed. Which
#: way "-Z up" reads is the whole point of recording it.
DIRECTION_NOTE = (
    "print_direction is the part-local axis pointing UP in world space when the "
    "part is printed, and print_plate_side is the local axis resting on the "
    "build plate. '+Z up' is the part printed as modelled; '-Z up' means it is "
    "printed upside-down, with its local +Z face on the plate. Design overhangs, "
    "bridges and bores against that, not against how the part is modelled."
)

_ALIASES = {}
for _canonical in DIRECTIONS:
    _ALIASES[_canonical.lower()] = _canonical
for _axis in ("X", "Y", "Z"):
    for _sign in ("+", "-"):
        _ALIASES[f"{_sign}{_axis}".lower()] = f"{_sign}{_axis} up"
    _ALIASES[_axis.lower()] = f"+{_axis} up"  # bare axis reads as positive
_ALIASES["none"] = NOT_PRINTED
_ALIASES["no"] = NOT_PRINTED


def normalise_direction(text):
    """The canonical enum value for a caller's spelling, or None if unknown.

    Accepts the canonical names case-insensitively plus the bare axis forms
    ("+Z", "z"), so a reasonable guess doesn't cost a failed round-trip.
    """
    if not isinstance(text, str):
        return None
    return _ALIASES.get(text.strip().lower())


def _unit(vector):
    """``vector`` scaled to unit length, or None when it has no length."""
    try:
        x, y, z = (float(v) for v in vector)
    except (TypeError, ValueError):
        return None
    length = (x * x + y * y + z * z) ** 0.5
    if length < 1e-9:
        return None
    return (x / length, y / length, z / length)


def _ensure_props(obj):
    existing = getattr(obj, "PropertiesList", None) or []
    if _DIRECTION_PROP not in existing:
        obj.addProperty("App::PropertyEnumeration", _DIRECTION_PROP, _GROUP,
                        "The part-local axis that points UP in world space when "
                        "this part is printed; layers stack along it. '+Z up' is "
                        "printed as modelled. '-Z up' is printed upside-down, "
                        "with the local +Z face on the build plate.",
                        _PROP_OUTPUT)
        setattr(obj, _DIRECTION_PROP, list(DIRECTIONS))
    if _CUSTOM_PROP not in existing:
        obj.addProperty("App::PropertyDirection", _CUSTOM_PROP, _GROUP,
                        "Build direction when PrintDirection is Custom",
                        _PROP_OUTPUT)


def set_direction(obj, direction, vector=None):
    """Record ``direction`` on ``obj``. ``vector`` applies only to ``Custom``.

    The caller owns the transaction, so a batch commits as one undo step.
    """
    _ensure_props(obj)
    if direction == CUSTOM:
        import FreeCAD

        unit = _unit(vector)
        setattr(obj, _CUSTOM_PROP, FreeCAD.Vector(*unit))
    setattr(obj, _DIRECTION_PROP, direction)
    try:
        obj.purgeTouched()
    except Exception:  # noqa: BLE001
        pass


def direction_entry(obj):
    """``{"print_direction": ...}`` for a survey/detail entry, or ``{}``.

    Empty while the part carries no direction, or carries "Not set" -- an
    unanswered field is not worth bytes in every payload.
    """
    value = getattr(obj, _DIRECTION_PROP, None)
    if not value or value == NOT_SET:
        return {}
    entry = {"print_direction": value}
    if value in PLATE_SIDES:
        entry["print_plate_side"] = PLATE_SIDES[value]
    if value == CUSTOM:
        custom = getattr(obj, _CUSTOM_PROP, None)
        if custom is not None:
            # The up vector; the plate side is its negation.
            entry["print_direction_vector"] = [round(custom.x, 4),
                                               round(custom.y, 4),
                                               round(custom.z, 4)]
    return entry


def any_direction_set(doc):
    """Whether any object carries a direction, i.e. whether a payload reporting
    them needs DIRECTION_NOTE."""
    return any(direction_entry(obj) for obj in doc.Objects)
