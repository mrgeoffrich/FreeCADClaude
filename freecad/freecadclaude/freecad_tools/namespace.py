# SPDX-License-Identifier: LGPL-2.1-or-later
"""The FreeCAD names bound for scripting -- run_python's namespace.

run_python execs against this and inspect_api resolves against it, so the two
MUST agree: inspect_api exists precisely to tell Claude what run_python will
have bound, and a name that resolves in one but not the other is exactly the
guess it's there to prevent. Built in one place so they can't drift.
"""

#: Modules pre-imported into the scripting namespace. Best effort -- a FreeCAD
#: build without one of them simply omits it rather than failing the call.
_SCRIPTING_MODULES = ("Part", "Sketcher", "PartDesign", "Draft")


def scripting_namespace(doc=None):
    """The names run_python binds: FreeCAD/App, FreeCADGui/Gui, the scripting
    modules, and ``doc`` (the active document).

    Pass `doc` explicitly from run_python, which creates a document when there
    isn't one; inspect_api passes nothing and simply omits ``doc`` when no
    document is open (there is then nothing to inspect on it).
    """
    import FreeCAD

    ns = {"FreeCAD": FreeCAD, "App": FreeCAD}
    if doc is None:
        doc = FreeCAD.ActiveDocument
    if doc is not None:
        ns["doc"] = doc
    try:
        import FreeCADGui

        ns["FreeCADGui"] = FreeCADGui
        ns["Gui"] = FreeCADGui
    except Exception:  # noqa: BLE001 - no GUI (freecadcmd): the rest still works
        pass
    for mod_name in _SCRIPTING_MODULES:
        try:
            ns[mod_name] = __import__(mod_name)
        except Exception:  # noqa: BLE001
            pass
    return ns
