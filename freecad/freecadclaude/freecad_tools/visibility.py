# SPDX-License-Identifier: LGPL-2.1-or-later
"""Show only the requested objects for a capture, then put everything back.

Shared by capture_view / crop_view / cutaway: each renders a chosen subset,
so it hides everything else (and suspends the selection, whose green
highlight would otherwise recolour the shot) and restores it afterwards.
"""


#: Plain container types whose own .Shape aggregation can't be trusted (e.g. FreeCAD
#: excludes a child that appears in another object's InList, treating it as an
#: intermediate/consumed shape -- wrong for cases like a body used as a Mirror source
#: where both the original and the mirror are separate final solids). Expand these into
#: their Group children instead of exporting the container's own Shape.
_CONTAINER_TYPES = ("App::Part", "App::DocumentObjectGroup")


def _expand_containers(objs):
    expanded = []
    seen = set()
    stack = list(objs)
    while stack:
        o = stack.pop(0)
        if o.Name in seen:
            continue
        seen.add(o.Name)
        if getattr(o, "TypeId", "") in _CONTAINER_TYPES:
            stack = list(getattr(o, "Group", None) or []) + stack
            continue
        expanded.append(o)
    return expanded


# --- Visibility isolation (capture_view / cutaway / crop_view) --------------
# The offscreen capture views render the DOCUMENT's own scene graph, and
# ViewObject.Visibility is document-level (shared by every view), not per-view.
# So "show only these objects for the shot" means toggling Visibility on the
# document -- which we save and restore in the tool's finally so the user's real
# view/document stays untouched (the GUI thread is blocked for the whole call,
# so nothing repaints mid-call and there's no flicker).

def _descendants(obj):
    """Everything nested under obj via .Group (Part/Group children, Body
    features), recursively."""
    out, seen, stack = [], set(), list(getattr(obj, "Group", None) or [])
    while stack:
        o = stack.pop()
        if o.Name in seen:
            continue
        seen.add(o.Name)
        out.append(o)
        stack.extend(getattr(o, "Group", None) or [])
    return out


def _ancestor_containers(obj):
    """The App::Part / Group / Body chain obj sits inside, so their group
    switches stay ON when a nested object is isolated."""
    out, cur, seen = [], obj, set()
    while cur is not None:
        parent = None
        for getter in ("getParentGeoFeatureGroup", "getParentGroup"):
            fn = getattr(cur, getter, None)
            if fn is not None:
                try:
                    parent = fn()
                except Exception:  # noqa: BLE001
                    parent = None
            if parent is not None:
                break
        if parent is None or parent.Name in seen:
            break
        seen.add(parent.Name)
        out.append(parent)
        cur = parent
    return out


def _visibility_keep_set(doc, requested_names):
    """Names that must end up VISIBLE for the shot: the requested objects, their
    container ancestors, and their CURRENTLY-visible descendants.

    Keeping only currently-visible descendants is what makes a PartDesign Body
    render correctly -- its tip feature is the visible one, so it's kept and
    shown; the intermediate features stay hidden (we don't force them True), so
    there are no overlapping solids. An App::Part keeps its curated child
    visibility the same way. (A naive "Visibility = (Name in requested)" would
    hide a Body's tip feature and render it blank -- hence the descendant walk.)
    """
    keep = set()
    for name in requested_names:
        obj = doc.getObject(name)
        if obj is None:
            continue  # caller has already error-checked; belt-and-suspenders
        keep.add(obj.Name)
        for anc in _ancestor_containers(obj):
            keep.add(anc.Name)
        for desc in _descendants(obj):
            vo = getattr(desc, "ViewObject", None)
            if vo is None:
                continue
            try:
                if bool(vo.Visibility):
                    keep.add(desc.Name)
            except Exception:  # noqa: BLE001
                pass
    return keep


def _isolate_visibility(doc, keep_names):
    """Force every object's ViewObject.Visibility to (Name in keep_names) and
    return [(view_object, prior_bool), ...] for _restore_visibility.

    Never raises: guards objects without a usable ViewObject and per-object set
    failures, and records the prior value BEFORE changing it so the restore is
    always exact.
    """
    keep, saved = set(keep_names), []
    for obj in doc.Objects:
        vo = getattr(obj, "ViewObject", None)
        if vo is None:
            continue
        try:
            prior = bool(vo.Visibility)
        except Exception:  # noqa: BLE001
            continue
        want = obj.Name in keep
        if prior == want:
            continue
        saved.append((vo, prior))
        try:
            vo.Visibility = want
        except Exception:  # noqa: BLE001
            pass  # couldn't set; prior already recorded so restore is a no-op
    return saved


def _restore_visibility(saved):
    for vo, prior in reversed(saved):
        try:
            vo.Visibility = prior
        except Exception:  # noqa: BLE001
            pass


def _suspend_selection(doc):
    """Clear `doc`'s 3D selection for an offscreen capture and return the saved
    selection for _restore_selection to put back.

    A selected object renders in the highlight colour (green), which would
    misrepresent its real appearance in the screenshot, so we drop the selection
    just for the render. Selection is transient Gui state (not a document
    property), so this doesn't dirty Modified; and because the whole tool call is
    one blocked GUI-thread event, the user's real view never repaints in between,
    so restoring it afterwards is invisible to them. Clear is scoped to `doc` so a
    selection in another open document is left untouched. Mirrors
    _isolate_visibility/_restore_visibility. Never raises.
    """
    import FreeCADGui

    try:
        saved = list(FreeCADGui.Selection.getSelectionEx(doc.Name))
    except Exception:  # noqa: BLE001
        return []
    if saved:
        try:
            FreeCADGui.Selection.clearSelection(doc.Name)
        except Exception:  # noqa: BLE001
            return []
    return saved


def _restore_selection(saved):
    """Re-add the selection cleared by _suspend_selection, sub-elements and all."""
    if not saved:
        return
    import FreeCADGui

    for sel in saved:
        try:
            subs = list(getattr(sel, "SubElementNames", None) or [])
            if subs:
                for sub in subs:
                    FreeCADGui.Selection.addSelection(sel.Object, sub)
            else:
                FreeCADGui.Selection.addSelection(sel.Object)
        except Exception:  # noqa: BLE001
            pass
