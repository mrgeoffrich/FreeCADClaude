# SPDX-License-Identifier: LGPL-2.1-or-later
"""inspect_api -- resolve real FreeCAD signatures/docstrings so the model
doesn't have to guess them."""

from .namespace import scripting_namespace

_INSPECT_API_SCHEMA = {
    "name": "inspect_api",
    "description": (
        "Look up the real signatures and docstrings of FreeCAD API names BEFORE "
        "writing run_python, so you don't guess parameters. Pass 'names': a LIST "
        "of dotted names to resolve in the run_python namespace (FreeCAD, App, "
        "Part, Sketcher, PartDesign, Draft, Gui, doc, and the active document's "
        "objects). For each it returns the type, a Python signature when one is "
        "available, the docstring (which for FreeCAD's C++ methods usually spells "
        "out the accepted argument forms), and then either -- for modules/classes "
        "-- the list of public members, -- for a list/tuple value -- its items, "
        "or -- for a document object instance (has a PropertiesList) -- every "
        "property name AND its current value in one shot (e.g. 'doc.ExampleBox"
        "Instance' already returns 'Length=20.0 mm, Placement=..., ...' with no "
        "extra round trip needed). Examples: ['Sketcher.Constraint', "
        "'Part.makeBox', 'doc.ExampleBodyInstance', "
        "'doc.ExampleSketchInstance.addGeometry'] -- the last two are "
        "illustrative; substitute the real internal Name of a body/sketch "
        "already in the document (check get_objects if unsure -- it's rarely "
        "literally 'Body'/'Sketch'). Only resolves things already reachable by "
        "attribute access -- NOT 'Type::String' names like "
        "'PartDesign::AdditiveBox' or 'PartDesign::Body' (those are passed as "
        "strings to addObject/newObject, not imported -- and don't swap '::' "
        "for '.' and guess a module attribute either, e.g. 'PartDesign.Body' "
        "is NOT a thing; the PartDesign/Part/Sketcher modules expose almost no "
        "feature classes directly, only a few free functions like "
        "Part.makeBox). There is nothing to inspect until you've created one "
        "-- go straight to doc.addObject('PartDesign::Body', 'Body') (or "
        "body.newObject(...) inside a Body), THEN inspect the resulting "
        "object, e.g. 'doc.ExampleBoxInstance'. Watch out for 'Sketcher.Sketch' "
        "specifically -- it resolves (no error) but is a different, lower-level "
        "class from the one your sketches actually are ('Sketcher::SketchObject', "
        "which isn't reachable as a module attribute at all), so its methods carry "
        "thin/misleading docstrings, e.g. 'Sketcher.Sketch.addGeometry' gives just "
        "one line while the real 'doc.ExampleSketchInstance.addGeometry' spells out "
        "every overload and argument. Always inspect sketch methods via an actual "
        "instance, never via 'Sketcher.Sketch'. "
        "Read-only and needs no approval: it only walks attribute chains, "
        "never calls or subscripts. Look up everything you're unsure of in ONE "
        "call, then write the code."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Dotted API names to inspect, e.g. 'Sketcher.Constraint'",
            },
        },
        "required": ["names"],
    },
}


def _is_dotted_name(expr):
    """True iff `expr` is only attribute access on a name -- no calls/subscripts.

    Guarantees eval()-ing it can't execute a function or run arbitrary code, so
    inspect_api stays a read-only path (the one mutation path is run_python).
    """
    import ast

    try:
        node = ast.parse(expr.strip(), mode="eval").body
    except SyntaxError:
        return False
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name)


def _describe_api(obj, name):
    """A compact signature/doc/members block for one resolved API object."""
    import inspect

    lines = [f"## {name}"]
    try:
        sig = str(inspect.signature(obj))
    except (TypeError, ValueError):
        sig = None
    if sig:
        lines.append(f"signature: {name.split('.')[-1]}{sig}")
    else:
        lines.append(f"type: {type(obj).__name__}")

    doc = inspect.getdoc(obj)
    if doc:
        doc = doc.strip()
        if len(doc) > 2000:
            doc = doc[:2000] + " […]"
        lines.append(doc)

    props = getattr(obj, "PropertiesList", None)
    if isinstance(props, (list, tuple)) and not (inspect.ismodule(obj) or inspect.isclass(obj)):
        rows = []
        for prop in props:
            if prop.startswith("_"):
                continue
            try:
                value = repr(getattr(obj, prop))
            except Exception as exc:  # noqa: BLE001
                value = f"<error: {exc!r}>"
            if len(value) > 200:
                value = value[:200] + " […]"
            rows.append(f"{prop}={value}")
        if rows:
            lines.append("properties: " + ", ".join(rows[:60]) + (" …" if len(rows) > 60 else ""))

        # PropertiesList covers only the App *properties*. A document object's
        # METHODS -- and the plain Python attributes that aren't App properties
        # (a sketch's DoF, ConflictingConstraints, RedundantConstraints ...) --
        # are invisible in it, which used to make them undiscoverable: the only
        # way to find moveGeometry/setDatum/DoF was to already know the name and
        # guess. Walk dir() so they're listed.
        extras, methods = [], []
        for member in sorted(dir(obj)):
            if member.startswith("_") or member in props:
                continue
            try:
                value = getattr(obj, member)
            except Exception:  # noqa: BLE001
                continue
            if callable(value):
                methods.append(member)
                continue
            text = repr(value)
            if len(text) > 120:
                text = text[:120] + " […]"
            extras.append(f"{member}={text}")
        if extras:
            lines.append("other attributes (NOT in PropertiesList): " + ", ".join(extras))
        if methods:
            lines.append("methods: " + ", ".join(methods))
    elif inspect.ismodule(obj) or inspect.isclass(obj):
        members = [m for m in dir(obj) if not m.startswith("_")]
        if members:
            shown = ", ".join(members[:60])
            lines.append("members: " + shown + (" …" if len(members) > 60 else ""))
    elif isinstance(obj, (list, tuple)):
        items = [repr(x) for x in obj[:60]]
        if items:
            lines.append("items: " + ", ".join(items) + (" …" if len(obj) > 60 else ""))
    return "\n".join(lines)


def _find_instance_of_type(type_id):
    """The first object in the active document whose TypeId is (or derives from)
    `type_id`, e.g. 'Sketcher::SketchObject' -> the document's first sketch."""
    import FreeCAD

    doc = FreeCAD.ActiveDocument
    if doc is None:
        return None
    for obj in doc.Objects:
        try:
            if obj.TypeId == type_id or obj.isDerivedFrom(type_id):
                return obj
        except Exception:  # noqa: BLE001
            continue
    return None


def _describe_by_type_id(name):
    """Describe a FreeCAD *type* by finding a live instance of it.

    The classes document objects actually are ('Sketcher::SketchObject',
    'PartDesign::Body') are not reachable as module attributes -- 'Sketcher.
    SketchObject' raises AttributeError -- so asking about one used to return a
    bare "could not resolve" and nothing else. Since the real API lives on the
    instance anyway, resolve it to one. Accepts either the 'Module::Type' form or
    the 'Module.Type' spelling that doesn't resolve as an attribute chain.
    """
    type_id = name.replace(".", "::") if "::" not in name else name
    if "::" not in type_id:
        return None
    obj = _find_instance_of_type(type_id)
    if obj is None:
        return None
    described = _describe_api(obj, f"{name}  (via the live instance '{obj.Name}')")
    return (
        described
        + f"\n\n(NOTE: '{name}' is a FreeCAD type name, not an importable class -- "
        f"'{type_id}' is what you pass to addObject() as a STRING. There is nothing "
        f"to import, so the above describes '{obj.Name}', an actual "
        f"{type_id} in this document, which carries the real API.)"
    )


def _run_inspect_api(args):
    names = args.get("names")
    if isinstance(names, str):
        names = [names]
    if not names:
        return "Pass 'names': a list of dotted API names to look up (e.g. ['Sketcher.Constraint'])."

    ns = scripting_namespace()  # exactly what run_python will bind
    blocks = []
    for raw in names:
        name = str(raw).strip()

        # 'Sketcher::SketchObject' isn't valid Python, so it never reaches eval --
        # handle the type-name form before the dotted-name gate rejects it.
        if "::" in name:
            described = _describe_by_type_id(name)
            blocks.append(
                described
                or f"## {name}\n(no object of type '{name}' exists in this document "
                "yet -- create one with addObject('{0}', ...) first, then inspect the "
                "resulting object by its Name.)".format(name.replace(".", "::"))
            )
            continue

        if not _is_dotted_name(name):
            blocks.append(
                f"## {name}\n(skipped: inspect_api only resolves dotted names like "
                "'Sketcher.Constraint' -- it never calls functions or subscripts.)"
            )
            continue
        try:
            obj = eval(name, dict(ns))  # noqa: S307 - validated as a dotted name only
        except Exception as exc:  # noqa: BLE001
            # e.g. 'Sketcher.SketchObject' -- a real FreeCAD type, but not a module
            # attribute. Fall back to a live instance rather than giving up.
            described = _describe_by_type_id(name)
            blocks.append(described or f"## {name}\n(could not resolve: {exc!r})")
            continue
        blocks.append(_describe_api(obj, name))
    return "\n\n".join(blocks)
