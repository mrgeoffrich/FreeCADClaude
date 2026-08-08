# SPDX-License-Identifier: LGPL-2.1-or-later
"""Read-only document probes.

Three tools, and the split between the first two is the point:

``get_objects``      the SURVEY -- every object, but only the fields that
                     identify it and let you pick the next one to look at.
``describe_objects`` the DETAIL -- everything worth knowing about a named few:
                     full placement, shape metrics, attachment, dependencies.
``get_selection``    the current GUI context (what the user is pointing at).

The survey used to carry the detail for every object at once, which measured
across 34 logged calls came to 15.9 KB a call (up to 31 KB) of which most was
never referred to again -- 38% of it the Origin planes/axes every Body carries,
whose entries are identical boilerplate reporting a literally infinite
(+/-1e100) bounding box. See ``_survey_object`` for what each trim was checked
against.
"""

from .diagnostics import _body_states, _ERROR_FLAGS, _shape_metrics
from .doc_notes import notes_block
from .geometry import _bbox_dict, _document_bbox, _MAX_SANE_EXTENT
from .print_meta import any_direction_set, direction_entry, DIRECTION_NOTE
from .gui_state import _active_edit_summary

_GET_OBJECTS_SCHEMA = {
    "name": "get_objects",
    "description": (
        "Survey the active FreeCAD document: its name, its overall bounding box, "
        "and one compact entry per object -- internal name, label, type, the "
        "container it sits in, key dimensions, and visibility (as JSON). Call "
        "this first to see what's there and to get the internal names every "
        "other tool takes. It also returns the document's standing 'notes' when "
        "it has any -- the free text saying what the model is for, how its parts "
        "relate and how it is to be printed; read that before planning anything, "
        "since none of it is derivable from the geometry. For a PartDesign "
        "document it also returns each Body's "
        "'chain': the features that actually build its shape, in order -- read "
        "that before editing an existing Body, since it is the only place the "
        "build order is visible and a feature missing from it contributes "
        "nothing however healthy it looks. Deliberately shallow: bounding boxes "
        "are reported for the objects that are drawn in their own right (Bodies, "
        "sketches, datums, Part/Mesh features) but NOT for the features inside a "
        "Body, whose box is just the running solid the Body already reports. For "
        "placement and rotation, shape metrics (volume, area, face/edge counts, "
        "validity), attachment, or what links to what, follow up with "
        "describe_objects on the handful you care about."
    ),
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}

#: Spelled out only when a Body actually has an off-chain feature, which measured
#: across 58 real bodies is never unless something went wrong -- so the common
#: case costs nothing and the broken one arrives explained.
_OFF_CHAIN_NOTE = (
    "These solid features sit in the Body but nothing between them and the Tip "
    "references them, so they contribute NOTHING to its shape -- they still "
    "recompute cleanly on their own branch, which is why no error mentions them. "
    "Either re-link them into the chain via BaseFeature, or delete them."
)

#: With no Tip there is no chain at all, so every feature lands off it -- a
#: different fault from a side branch, and _OFF_CHAIN_NOTE's advice (re-link via
#: BaseFeature) would be the wrong repair.
_NO_TIP_NOTE = (
    "This Body has no Tip, so it builds NO shape and every feature below is "
    "inert. Set body.Tip to whichever feature should be last -- deleting the tip "
    "feature with doc.removeObject does not move the Tip back on its own."
)

#: How the survey points at the detail tool. One line, once per payload, rather
#: than a per-object hint: the whole reason the survey is thin is that most
#: objects are never looked at twice.
_DETAIL_HINT = (
    "Shallow survey. For rotation, volume/area/face counts, shape validity, "
    "attachment or dependency links, call describe_objects on the names you want."
)

#: Says out loud why object_count exceeds the number of entries. Without it the
#: gap reads as objects gone missing, which costs exactly the exploratory
#: run_python this tool exists to remove.
_ORIGIN_NOTE = (
    "Origin planes/axes are not listed separately -- every one is identical "
    "boilerplate. Each Body/Part carries its own set under its 'origin' key, "
    "which is where the exact suffixed names (XY_Plane002, ...) are. That is the "
    "whole difference between object_count and the entries above."
)


def _body_chains(doc):
    """Per-Body build order for get_objects, plus anything that fell off it."""
    bodies = []
    for name, st in _body_states(doc).items():
        entry = {"name": name, "label": st["label"], "tip": st["tip"],
                 "chain": st["chain"]}
        if st["orphans"]:
            entry["not_in_chain"] = st["orphans"]
            entry["not_in_chain_note"] = _NO_TIP_NOTE if not st["tip"] else _OFF_CHAIN_NOTE
        if st["after_tip"]:
            # Ordinary "Tip moved back to edit an earlier feature", not breakage.
            entry["after_tip"] = st["after_tip"]
        if st["cycle"]:
            entry["basefeature_cycle"] = True
        bodies.append(entry)
    return bodies


# Properties worth reporting when present (most are FreeCAD Quantities).
_REPORTED_PROPS = ("Length", "Width", "Height", "Radius", "Radius1", "Radius2", "Angle")

#: The Origin group every Body and Std Part carries: one App::Origin holding
#: three App::Plane, three App::Line and an App::Point. 592 of the 1419 objects
#: across the logged get_objects calls -- 38% of the payload -- and every entry
#: is identical boilerplate whose bounding box is +/-1e100. What DOES matter is
#: the names, because a multi-body document numbers them (XY_Plane002) and
#: picking the wrong suffix is a logged error (KeyError: 'XY_Plane'). So they
#: are collapsed onto their owner's entry instead of listed: fewer bytes AND the
#: which-Body-owns-which mapping, which the flat list never carried.
_ORIGIN_TYPES = frozenset(
    ("App::Origin", "App::Plane", "App::Line", "App::Point")
)


def _origin_owners(doc):
    """``({origin-member name: owner name}, [members with no owner])``.

    Walks each App::Origin's OriginFeatures rather than guessing from names, so
    the suffixes are whatever the document actually assigned.
    """
    owned, seen = {}, set()
    for obj in doc.Objects:
        if (getattr(obj, "TypeId", "") or "") != "App::Origin":
            continue
        owner = _container_name(obj) or obj.Name
        members = getattr(obj, "OriginFeatures", None) or getattr(obj, "Group", None) or []
        for member in members:
            name = getattr(member, "Name", None)
            if name:
                owned[name] = owner
                seen.add(name)
        owned[obj.Name] = owner
        seen.add(obj.Name)
    loose = [o.Name for o in doc.Objects
             if (getattr(o, "TypeId", "") or "") in _ORIGIN_TYPES and o.Name not in seen]
    return owned, loose


def _container_name(obj):
    """Name of the Body or Std Part ``obj`` sits in, or None."""
    try:
        parent = obj.getParentGeoFeatureGroup()
    except Exception:  # noqa: BLE001 - not every object type has one
        return None
    return getattr(parent, "Name", None)


def _reported_bbox(obj):
    """The object's world bounding box as a dict, or None when it shouldn't be
    reported.

    Skipped for the solid features INSIDE a Body (anything derived from
    PartDesign::Feature): their box is the running solid at that point in the
    chain, which the Body's own entry already gives, and 29% of them are exact
    duplicates of each other. Checked against the logged sessions rather than
    assumed -- of the non-integer bbox numbers only an in-body feature carried,
    505 were never referred to again. The ones that WERE (76 of 82) came from
    sketches, which is why sketches keep theirs.
    """
    try:
        if obj.isDerivedFrom("PartDesign::Feature"):
            return None
    except Exception:  # noqa: BLE001
        pass
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return None
    try:
        bbox = shape.BoundBox
        if not bbox.isValid():
            return None
        # Datum-style shapes report an effectively infinite box; it frames
        # nothing and would poison a crop copied straight out of here.
        if max(abs(bbox.XMin), abs(bbox.XMax), abs(bbox.YMin),
               abs(bbox.YMax), abs(bbox.ZMin), abs(bbox.ZMax)) > _MAX_SANE_EXTENT:
            return None
        return _bbox_dict(bbox)
    except Exception:  # noqa: BLE001
        return None


def _dimensions(obj):
    """The handful of dimensional properties worth carrying in the survey."""
    dims = {}
    for prop in _REPORTED_PROPS:
        if hasattr(obj, prop):
            value = getattr(obj, prop)
            dims[prop] = getattr(value, "Value", value)  # Quantity -> float
    return dims


def _survey_object(obj, container, origin_members):
    """One object's survey entry -- identity plus only what earns its bytes.

    Three fields the old entry always carried are now conditional, each on a
    measurement over the logged calls: ``position`` was [0,0,0] on 82% of
    objects, ``label`` mostly repeats the name, and ``visible`` is only
    interesting when true (omitted means hidden, or no GUI at all).
    """
    info = {"name": obj.Name, "type": obj.TypeId}
    if obj.Label and obj.Label != obj.Name:
        info["label"] = obj.Label
    if container:
        info["in"] = container

    placement = getattr(obj, "Placement", None)
    if placement is not None:
        base = placement.Base
        position = [round(base.x, 3), round(base.y, 3), round(base.z, 3)]
        if any(position):
            info["position"] = position

    dims = _dimensions(obj)
    if dims:
        info["dimensions"] = dims

    bbox = _reported_bbox(obj)
    if bbox is not None:
        info["bounding_box"] = bbox

    info.update(direction_entry(obj))

    if origin_members:
        info["origin"] = origin_members

    view = getattr(obj, "ViewObject", None)
    if view is not None:
        try:
            if view.Visibility:
                info["visible"] = True
        except Exception:  # noqa: BLE001
            pass

    if any(flag in (getattr(obj, "State", None) or []) for flag in _ERROR_FLAGS):
        info["invalid"] = True  # last recompute failed
    return info


def _run_get_objects(args):
    import json

    import FreeCAD

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return json.dumps({"document": None, "object_count": 0, "objects": []})

    origin_of, loose_origin = _origin_owners(doc)
    by_owner = {}
    for member, owner in origin_of.items():
        # The App::Origin container itself is not a plane or an axis to sketch
        # on, so it isn't worth naming alongside them.
        if (getattr(doc.getObject(member), "TypeId", "") or "") != "App::Origin":
            by_owner.setdefault(owner, []).append(member)

    objects = []
    for obj in doc.Objects:
        if obj.Name in origin_of:
            continue  # collapsed onto its owner's entry
        objects.append(_survey_object(obj, _container_name(obj),
                                      by_owner.get(obj.Name)))

    result = {"document": doc.Label}
    # First, ahead of the object list: the standing notes are the context the
    # rest of the payload is read against, and they carry what no geometry can.
    notes = notes_block(doc)
    if notes:
        result["notes"] = notes
    result["object_count"] = len(doc.Objects)
    result["objects"] = objects
    if loose_origin:
        result["origin"] = loose_origin
    if origin_of:
        result["origin_note"] = _ORIGIN_NOTE
    if any_direction_set(doc):
        result["print_direction_note"] = DIRECTION_NOTE
    bodies = _body_chains(doc)
    if bodies:
        result["bodies"] = bodies
    scene_bbox = _document_bbox(doc)
    if scene_bbox.XMin <= scene_bbox.XMax:
        result["bounding_box"] = _bbox_dict(scene_bbox)
    result["detail"] = _DETAIL_HINT
    # Compact separators, not indent=2: this is a bulk list Claude reads rather
    # than parses, and the indentation was a third of the payload.
    return json.dumps(result, separators=(",", ":"))


# ---- describe_objects ------------------------------------------------------

_DESCRIBE_OBJECTS_SCHEMA = {
    "name": "describe_objects",
    "description": (
        "Everything worth knowing about specific objects, by name (batch as many "
        "as you like in one call). The deep companion to get_objects' shallow "
        "survey: for each object it returns full placement INCLUDING rotation, "
        "its world bounding box, shape metrics (solids/faces/edges/vertexes, "
        "volume, area, centre of mass, and whether the shape is geometrically "
        "valid), its dimensional properties, sketch/datum attachment "
        "(AttachmentSupport, MapMode, AttachmentOffset), and its dependency "
        "links both ways -- what it is built from and what would break if you "
        "deleted it. For a Body it also gives the tip and the build chain; for a "
        "sketch, its degrees of freedom, solver state and open/closed wire "
        "counts (call get_sketch for the actual GeoIds and constraint indices). "
        "Use it instead of a run_python that just prints properties -- and reach "
        "for it whenever you need a volume, a face count, a rotation or 'what "
        "uses this'. Slower and much larger per object than get_objects, so name "
        "the objects you actually care about rather than the whole document."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Internal names of the objects to describe, e.g. "
                    "[\"Body\", \"Pad001\", \"Sketch003\"]. Labels are accepted "
                    "too and resolved to the object that carries them."
                ),
            },
        },
        "required": ["names"],
        "additionalProperties": False,
    },
}


def _precheck_describe_objects(args):
    """Reject an unusable `names` in pure Python, before the GUI thread."""
    names = args.get("names")
    if not isinstance(names, list) or not names:
        return ("describe_objects needs a non-empty 'names' array of object "
                "names, e.g. {\"names\": [\"Pad\", \"Sketch001\"]}. Call "
                "get_objects first if you don't know them.")
    if not all(isinstance(n, str) and n.strip() for n in names):
        return "Every entry in 'names' must be a non-empty object name string."
    return ""


#: Link properties reported elsewhere in the entry (or too bulky to repeat):
#: a Body's Group is its whole contents and its chain already says what builds
#: it; Origin is collapsed by get_objects; the attachment block covers Support.
_SKIP_LINK_PROPS = frozenset(
    ("Group", "Origin", "OriginFeatures", "AttachmentSupport", "Support")
)


def _link_names(value):
    """Names out of any of the shapes a link property takes: a single object, a
    list of them, or a (object, [subelements]) LinkSub pair / list of pairs.

    Subelements are kept: "Fillet.Edge4" is the form that matters, since a
    dress-up pinning literal edge names is the topological-naming hazard.
    """
    def one(item):
        if item is None:
            return []
        if isinstance(item, (tuple, list)):
            # (obj, subs) pair, or a plain list of objects
            if len(item) == 2 and hasattr(item[0], "Name") and \
                    isinstance(item[1], (tuple, list, str)):
                name = item[0].Name
                subs = [item[1]] if isinstance(item[1], str) else list(item[1])
                subs = [s for s in subs if s]
                return [f"{name}.{s}" for s in subs] or [name]
            out = []
            for sub in item:
                out.extend(one(sub))
            return out
        return [item.Name] if hasattr(item, "Name") else []

    return one(value)


def _dependency_links(obj):
    """``{property: [names]}`` for every link property that resolves to
    something -- what this object is built from.

    Walked generically off PropertiesList/getTypeIdOfProperty rather than a
    hardcoded list of Profile/Base/BaseFeature/Sections/Spine, so a feature type
    nobody thought of still reports its inputs.
    """
    links = {}
    for prop in getattr(obj, "PropertiesList", None) or []:
        # `_Body` and friends are FreeCAD's own bookkeeping -- they'd report the
        # containing Body as if it were an input, which reads as a cycle.
        if prop in _SKIP_LINK_PROPS or prop.startswith("_"):
            continue
        try:
            if "PropertyLink" not in obj.getTypeIdOfProperty(prop):
                continue
            if "Hidden" in (obj.getPropertyStatus(prop) or []):
                continue
            names = _link_names(getattr(obj, prop, None))
        except Exception:  # noqa: BLE001
            continue
        if names:
            links[prop] = names
    return links


def _attachment(obj):
    """Attachment block for a sketch or datum, or None when it has no MapMode.

    AttachmentSupport is the FreeCAD 1.x spelling; Support is the pre-1.0 one,
    and documents saved by older versions still carry it.
    """
    if not hasattr(obj, "MapMode"):
        return None
    block = {"map_mode": str(getattr(obj, "MapMode", ""))}
    for prop in ("AttachmentSupport", "Support"):
        if hasattr(obj, prop):
            names = _link_names(getattr(obj, prop, None))
            if names:
                block["support"] = names
                break
    offset = getattr(obj, "AttachmentOffset", None)
    if offset is not None:
        try:
            base = offset.Base
            angle = round(offset.Rotation.Angle * 57.29577951308232, 4)
            if any((base.x, base.y, base.z)) or angle:
                block["offset"] = {
                    "position": [round(base.x, 4), round(base.y, 4), round(base.z, 4)],
                    "angle_deg": angle,
                }
        except Exception:  # noqa: BLE001
            pass
    if getattr(obj, "MapReversed", False):
        block["reversed"] = True
    return block


def _placement_block(obj):
    """Position plus rotation, the rotation only when there actually is one."""
    placement = getattr(obj, "Placement", None)
    if placement is None:
        return None
    try:
        base = placement.Base
        block = {"position": [round(base.x, 4), round(base.y, 4), round(base.z, 4)]}
        rotation = placement.Rotation
        angle = rotation.Angle * 57.29577951308232
        if abs(angle) > 1e-9:
            axis = rotation.Axis
            block["rotation"] = {
                "axis": [round(axis.x, 6), round(axis.y, 6), round(axis.z, 6)],
                "angle_deg": round(angle, 4),
                "yaw_pitch_roll": [round(v, 4) for v in rotation.getYawPitchRoll()],
            }
        return block
    except Exception:  # noqa: BLE001
        return None


#: Area and centre of mass, keyed by OCCT shape hash exactly as _shape_metrics
#: is -- and kept SEPARATE from it deliberately. That cache feeds the fingerprint
#: diff that runs twice on every run_python call, on the GUI thread; adding two
#: more GProp reads there would make every mutating call pay for something only
#: this tool asks for. Here they're worth caching in their own right: measured on
#: a 68-object document, describing 9 features costs 361 ms cold and 173 ms warm,
#: and that 173 ms was these two being recomputed.
_extra_metrics_cache = {}
_EXTRA_CACHE_CAP = 512


def _extra_metrics(obj, shape, has_solids):
    """``{area, center_of_mass}`` for ``shape``, cached by its hash."""
    key = None
    try:
        key = (obj.Name, shape.hashCode())
        hit = _extra_metrics_cache.get(key)
        if hit is not None:
            return hit
    except Exception:  # noqa: BLE001 - no usable hash: just measure it
        key = None
    extra = {}
    try:
        extra["area"] = round(shape.Area, 4)
    except Exception:  # noqa: BLE001
        pass
    try:
        if has_solids or extra.get("area"):
            com = shape.CenterOfMass
            extra["center_of_mass"] = [round(com.x, 4), round(com.y, 4),
                                       round(com.z, 4)]
    except Exception:  # noqa: BLE001
        pass
    if key is not None:
        if len(_extra_metrics_cache) >= _EXTRA_CACHE_CAP:
            _extra_metrics_cache.clear()  # bounded; a cold cache only re-measures
        _extra_metrics_cache[key] = extra
    return extra


def _shape_block(obj):
    """Topology counts, volume, area, centre of mass and geometric validity.

    The counts/volume/bbox/validity come from ``_shape_metrics``, which caches
    them by OCCT shape hash -- so an object already measured by this call's
    before/after snapshot costs nothing here.
    """
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return None
    metrics = _shape_metrics(obj.Name, shape)
    if metrics is None:
        return None
    block = {
        "solids": metrics["solids"], "faces": metrics["faces"],
        "edges": metrics["edges"], "vertexes": metrics["vertexes"],
        "volume": round(metrics["volume"], 4), "valid": metrics["valid"],
    }
    block.update(_extra_metrics(obj, shape, metrics["solids"]))
    if not metrics["valid"]:
        block["valid_note"] = (
            "The shape fails OCCT validation. It still recomputes and reports a "
            "plausible volume, so nothing else will flag it -- fix this before "
            "building on top of it."
        )
    return block


def _sketch_block(obj):
    """DoF, solver state and wire closure for a sketch -- the facts that decide
    whether it can be padded, without the GeoId dump get_sketch exists for."""
    from .diagnostics import _solver_constraint_indices

    if (getattr(obj, "TypeId", "") or "") != "Sketcher::SketchObject":
        return None
    block = {}
    try:
        block["dof"] = int(obj.DoF)
        block["fully_constrained"] = bool(getattr(obj, "FullyConstrained", False))
    except Exception:  # noqa: BLE001
        pass
    try:
        block["geometry_count"] = int(obj.GeometryCount)
        block["constraint_count"] = len(obj.Constraints)
    except Exception:  # noqa: BLE001
        pass
    for attr, key in (("ConflictingConstraints", "conflicting"),
                      ("RedundantConstraints", "redundant"),
                      ("MalformedConstraints", "malformed")):
        indices = _solver_constraint_indices(getattr(obj, attr, None))
        if indices:
            block[key] = indices  # 0-based, ready for setDatum/delConstraint
    try:
        wires = obj.Shape.Wires
        block["closed_wires"] = sum(1 for w in wires if w.isClosed())
        block["open_wires"] = sum(1 for w in wires if not w.isClosed())
    except Exception:  # noqa: BLE001
        pass
    return block or None


def _describe_one(doc, obj):
    entry = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
    container = _container_name(obj)
    if container:
        entry["in"] = container
    entry.update(direction_entry(obj))

    view = getattr(obj, "ViewObject", None)
    if view is not None:
        try:
            entry["visible"] = bool(view.Visibility)
        except Exception:  # noqa: BLE001
            pass

    state = list(getattr(obj, "State", None) or [])
    if state:
        entry["state"] = state
    if any(flag in state for flag in _ERROR_FLAGS):
        entry["invalid"] = True

    for key, value in (("placement", _placement_block(obj)),
                       ("dimensions", _dimensions(obj) or None),
                       ("attachment", _attachment(obj)),
                       ("sketch", _sketch_block(obj)),
                       ("shape", _shape_block(obj))):
        if value:
            entry[key] = value

    bbox = getattr(getattr(obj, "Shape", None), "BoundBox", None)
    if bbox is not None:
        try:
            if bbox.isValid() and max(
                abs(bbox.XMin), abs(bbox.XMax), abs(bbox.YMin),
                abs(bbox.YMax), abs(bbox.ZMin), abs(bbox.ZMax)
            ) <= _MAX_SANE_EXTENT:
                entry["bounding_box"] = _bbox_dict(bbox)
        except Exception:  # noqa: BLE001
            pass

    links = _dependency_links(obj)
    if links:
        entry["depends_on"] = links
    # InList repeats an object once per link property that points here (a Body
    # reaches its features through both Group and Tip), so dedupe in order.
    used_by = list(dict.fromkeys(
        o.Name for o in (getattr(obj, "InList", None) or [])
        if getattr(o, "Name", None)
    ))
    if used_by:
        entry["used_by"] = used_by

    if (getattr(obj, "TypeId", "") or "") == "PartDesign::Body":
        st = _body_states(doc).get(obj.Name)
        if st:
            body = {"tip": st["tip"], "chain": st["chain"]}
            if st["orphans"]:
                body["not_in_chain"] = st["orphans"]
                body["not_in_chain_note"] = (
                    _NO_TIP_NOTE if not st["tip"] else _OFF_CHAIN_NOTE
                )
            if st["after_tip"]:
                body["after_tip"] = st["after_tip"]
            if st["cycle"]:
                body["basefeature_cycle"] = True
            entry["body"] = body
    return entry


def _resolve_object(doc, name):
    """``name`` as an internal name first, then as a label."""
    obj = doc.getObject(name)
    if obj is not None:
        return obj
    matches = doc.getObjectsByLabel(name)
    return matches[0] if matches else None


def _run_describe_objects(args):
    import json

    import FreeCAD

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return "No active document."

    described, missing = [], []
    seen = set()
    for name in args.get("names") or []:
        name = name.strip()
        if name in seen:
            continue
        seen.add(name)
        obj = _resolve_object(doc, name)
        if obj is None:
            missing.append(name)
            continue
        try:
            described.append(_describe_one(doc, obj))
        except Exception as exc:  # noqa: BLE001 - one bad object shouldn't sink the batch
            described.append({"name": name, "error": repr(exc)})

    result = {"document": doc.Label, "objects": described}
    if any("print_direction" in entry for entry in described):
        result["print_direction_note"] = DIRECTION_NOTE
    if missing:
        result["not_found"] = missing
        result["not_found_note"] = (
            "No object in this document has that internal name or label. Call "
            "get_objects for the current names -- FreeCAD suffixes duplicates "
            "(Pad, Pad001, ...) and a Label is not the name."
        )
    return json.dumps(result, indent=2)


_GET_SELECTION_SCHEMA = {
    "name": "get_selection",
    "description": (
        "The user's current GUI context as JSON -- what they are pointing at. Two "
        "parts: 'editing' = the object they have OPEN IN AN EDITOR right now, by "
        "name (e.g. the sketch open in the Sketcher editor), or null if they are "
        "not editing anything; and 'selection' = what they have SELECTED (objects "
        "plus sub-elements like Edge3/Face2/Vertex1). Use it to act on what the "
        "user clicked ('fillet this edge') or on whatever they are currently "
        "inside ('add a circle here' while a sketch is open). When 'editing' names "
        "a sketch, that is the sketch the user means -- pass its name to get_sketch "
        "rather than guessing from the document. Read-only and cheap."
    ),
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}


def _run_get_selection(args):
    import json

    # Edit state first: it's the headline, and it's the one signal that survives
    # the user having clicked nothing at all.
    out = {"editing": _active_edit_summary()}

    try:
        import FreeCADGui

        selection = FreeCADGui.Selection.getSelectionEx()
    except Exception as exc:  # noqa: BLE001
        out.update({"error": repr(exc), "selection_count": 0, "selection": []})
        return json.dumps(out, indent=2)

    picked = []
    for sel in selection:
        obj = sel.Object
        picked.append({
            "name": obj.Name,
            "label": obj.Label,
            "type": obj.TypeId,
            "subelements": list(sel.SubElementNames),
        })
    out["selection_count"] = len(picked)
    out["selection"] = picked
    return json.dumps(out, indent=2)
