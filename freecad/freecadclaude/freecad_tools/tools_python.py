# SPDX-License-Identifier: LGPL-2.1-or-later
"""run_python -- the sole document-mutating tool."""

import ast

from .namespace import scripting_namespace
from .session import _save_run_python_script, _save_step_snapshot, _save_steps_enabled

_RUN_PYTHON_SCHEMA = {
    "name": "run_python",
    "description": (
        "Execute FreeCAD Python in the running FreeCAD instance. This is how you "
        "do Sketcher work (geometry + constraints), PartDesign features "
        "(Body, Pad, Pocket, Revolution, Loft, Fillet, Chamfer, ...), Part "
        "booleans, Draft, arrays, and anything else in the API. "
        "Pre-bound names: FreeCAD, App, FreeCADGui, Gui, Part, Sketcher, "
        "PartDesign, Draft, and doc (the active document, created if none). "
        "The code runs on the GUI thread inside ONE undoable transaction. "
        "Return data by printing or by assigning to a variable named `result` "
        "(both are returned to you). On error you get the full traceback and the "
        "transaction is rolled back -- fix it and try again. If you're unsure of "
        "a method's parameters, call inspect_api first rather than guessing. "
        "Size each call to one coherent step, since the whole call is the "
        "rollback unit. The result reports what each feature added or removed, "
        "so you don't need a separate get_objects call to check it. For "
        "PartDesign, create a PartDesign::Body first and add features inside "
        "it. Your code runs as soon as you send it -- there is no approval "
        "step, and the user reads it afterwards in the transcript, so make each "
        "call one you'd be willing to have already run."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "FreeCAD Python source to execute"},
            "description": {
                "type": "string",
                "description": "One-line summary of what the code does (shown above the code in the user's transcript)",
            },
        },
        "required": ["code"],
    },
}


# Part/OCCT operations that cost tens to hundreds of milliseconds each on a real
# solid. Harmless once; ruinous a thousand times over, because run_python holds
# FreeCAD's GUI thread for the whole call -- see _heavy_loop_note.
_HEAVY_SHAPE_OPS = frozenset({
    "slice", "slices", "section", "cut", "common", "fuse", "generalFuse",
    "extrude", "revolve", "makeThickness", "makeOffsetShape", "makeOffset2D",
    "makeFillet", "makeChamfer", "makeLoft", "makePipeShell", "makeSolid",
    "distToShape", "removeSplitter", "refine", "tessellate", "recompute",
    # isInside *looks* like a cheap point test, which is exactly why it got past
    # this list once. It isn't: FreeCAD's TopoShapePy::isInside constructs a
    # fresh BRepClass3d_SolidClassifier per call, so it walks every face of the
    # solid for every point -- O(faces) per sample, not O(1). Sampled under the
    # freeze it caused: 1462 of 2206 stacks were in the classifier's constructor
    # alone, before any classifying had happened.
    "isInside",
})

# Trip count above which a loop of shape operations is worth stopping for. Sized
# off the logged sessions rather than picked: the loops that read fine sampled 7
# and 11 Z-heights, while the ones that froze FreeCAD ran 183, 245 and 1,206
# slices -- nothing real sits near the line.
_HEAVY_LOOP_TRIPS = 50

# Escape hatch, quoted in the message itself: a loop that genuinely has to run is
# one resend away, and the round-trip is the point -- it makes the cost a choice.
_SLOW_OK = "slow-ok"

_LOOP_NODES = (ast.For, ast.AsyncFor, ast.While,
               ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _iter_len(node):
    """Length of a loop's iterable when it's statically knowable, else None."""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return len(node.elts)
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "range"):
        return None
    vals = []
    for arg in node.args:
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, int)):
            return None
        vals.append(arg.value)
    if len(vals) == 1:
        return max(0, vals[0])
    if len(vals) == 2:
        return max(0, vals[1] - vals[0])
    if len(vals) == 3 and vals[2]:
        start, stop, step = vals
        span = (stop - start) if step > 0 else (start - stop)
        return max(0, -(-span // abs(step)))
    return None


def _loop_trips(node):
    """Best-effort trip count for one loop; None when it can't be known.

    Only the decidable forms: range() over int literals, and literal sequences.
    A `while` is None by definition, and that's not a gap in the analysis -- the
    three loops that actually froze FreeCAD were all `while x < limit: x += step`,
    so treating unknown as "assume it's big" is the case this exists for.
    """
    if isinstance(node, ast.While):
        return None
    iters = ([node.iter] if isinstance(node, (ast.For, ast.AsyncFor))
             else [gen.iter for gen in node.generators])
    total = 1
    for it in iters:
        length = _iter_len(it)
        if length is None:
            return None
        total *= length
    return total


def _is_heavy_call(node, heavy_funcs):
    """True for `shape.slice(...)`-style calls and for calls to local helpers
    already known to reach one."""
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Attribute):
        return node.func.attr in _HEAVY_SHAPE_OPS
    return isinstance(node.func, ast.Name) and node.func.id in heavy_funcs


def _call_name(node):
    return node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id


def _heavy_functions(tree):
    """Locally-defined functions that reach a heavy shape op, transitively.

    This is the part that matters. The call that froze FreeCAD for 2m46s was
    `while x <= 151.5: y = ymax(x)`, with the slice() hidden one level down in
    `def ymax` -- so looking only for a heavy op written lexically inside the
    loop would have sailed straight past the exact case worth catching.
    """
    defs = {node.name: node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    heavy = set()
    changed = True
    while changed:  # transitive closure; converges in a pass or two
        changed = False
        for name, node in defs.items():
            if name in heavy:
                continue
            if any(_is_heavy_call(sub, heavy) for sub in ast.walk(node)):
                heavy.add(name)
                changed = True
    return heavy


def _heavy_loop_note(code):
    """Stop a loop of shape operations before it freezes FreeCAD for minutes.

    run_python runs on the GUI thread, so a long call isn't slow -- it's the
    whole app unresponsive until it returns, with no way to preempt it. Returns
    a message to relay to Claude instead of running, or "" to proceed.
    """
    if _SLOW_OK in code:
        return ""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ""  # the compile() check already reported it
    heavy_funcs = _heavy_functions(tree)
    hits = {}

    def walk(node, trips):
        # trips: 1 = runs once, an int = known repeats, None = unknown/unbounded.
        if isinstance(node, _LOOP_NODES):
            length = _loop_trips(node)
            inner = None if trips is None or length is None else trips * length
            # The iterable is evaluated ONCE, at the outer rate -- so
            # `for w in shape.slice(d, z):` is a single slice, not one per wire.
            once = ([node.iter] if isinstance(node, (ast.For, ast.AsyncFor))
                    else [gen.iter for gen in getattr(node, "generators", [])])
            for sub in once:
                walk(sub, trips)
            for child in ast.iter_child_nodes(node):
                if not any(child is skip for skip in once):
                    walk(child, inner)
            return
        if (_is_heavy_call(node, heavy_funcs)
                and (trips is None or trips > _HEAVY_LOOP_TRIPS)):
            hits.setdefault((node.lineno, _call_name(node)), trips)
        for child in ast.iter_child_nodes(node):
            walk(child, trips)

    walk(tree, 1)
    if not hits:
        return ""

    where = ", ".join(
        f"{name}() at line {lineno}"
        + ("" if trips is None else f" (~{trips} times)")
        for (lineno, name), trips in sorted(hits.items())[:4]
    )
    return (
        "Heavy shape work inside a loop -- nothing ran.\n\n"
        f"    {where}\n\n"
        "run_python holds FreeCAD's GUI thread for the whole call, and each Part "
        "shape operation costs tens to hundreds of milliseconds on a real solid. "
        "A loop of them doesn't run slowly, it freezes the entire application "
        "until it returns -- one logged session locked FreeCAD up for nearly "
        "three minutes doing ~1,700 slice() calls, and the answer was thrown "
        "away on timeout anyway.\n\n"
        "There is nearly always a one-shot form:\n"
        "  - shape.slice(dir, d) already returns ALL the wires at that plane, as "
        "a plain Python list -- read the vertex coordinates off ONE call instead "
        "of re-slicing per sample point.\n"
        "  - shape.slices(dir, [d1, d2, ...]) does every plane in one go, but "
        "the result is FLAT: one Compound holding every wire from every plane, "
        "not one entry per distance. So zip(distances, result) is wrong twice "
        "over. Take result.childShapes() and group them yourself by the "
        "coordinate along dir.\n"
        "  - shape.cut([a, b, c]) / .fuse([...]) take the whole list at once.\n"
        "  - Probing for material with isInside() point by point is the slow way "
        "to ask a question OCCT answers in one call: shape.common(Part.makeLine("
        "a, b)).Edges gives the material intervals along a WHOLE line -- read "
        "the endpoints off them. That is one call instead of one per "
        "sample, and the answer is exact rather than quantised to your step "
        "size. For a height/width profile, slice the plane instead and read the "
        "extremes off the section wires.\n"
        "  - Anything returning a Part.Compound (slices, common, cut, fuse, "
        "section) hands back a SHAPE, not a Python sequence: it has no "
        "iteration and no len(). Reach the contents with .childShapes() or "
        ".SubShapes for one level down, or .Solids/.Faces/.Wires/.Edges/"
        ".Vertexes for everything of that type flattened through any nesting.\n"
        "  - Recompute once after the loop, not inside it.\n\n"
        f"If the loop really is bounded and necessary, put `# {_SLOW_OK}` in the "
        "code and resend -- but warn the user first that FreeCAD will be "
        "unresponsive while it runs."
    )


def _precheck_python(args):
    """Reject code that won't even compile, BEFORE it reaches the GUI thread.

    Run by the bridge ahead of the tool itself: code that can't compile has no
    business opening a transaction, and Claude gets the error a turn sooner.
    Returns an error string to relay to Claude, or "" when the code is
    syntactically fine. NB this only catches Python-level syntax errors -- a
    linter can't validate FreeCAD's C++ call signatures, so for *parameter*
    mistakes the agent should reach for inspect_api instead.

    It also carries the one *cost* check worth making statically
    (_heavy_loop_note), for the same reason: by the time a loop of shape
    operations is running there is nothing anyone can do but wait it out.
    """
    code = args.get("code", "")
    if not code.strip():
        return "No code provided."
    try:
        compile(code, "<run_python>", "exec")
    except SyntaxError as exc:
        where = f"line {exc.lineno}" + (f", col {exc.offset}" if exc.offset else "")
        lines = [f"SyntaxError at {where}: {exc.msg}. Nothing ran -- fix and resend."]
        detail = (exc.text or "").rstrip("\n")
        if detail:
            lines.append(detail)
            lines.append(" " * (max(1, exc.offset or 1) - 1) + "^")
        return "\n".join(lines)
    return _heavy_loop_note(code)


def _doc_alive(doc):
    """True while `doc` still references a live document. Running code can close
    the document out from under us (e.g. App.closeDocument); the handle then
    becomes a deleted C++ object and ANY attribute access on it raises, so this
    is how we detect that before touching the stale transaction. Checks the
    handle, not the name -- a closed document's name can be reused by a new one."""
    try:
        doc.Name
        return True
    except Exception:  # noqa: BLE001 - ReferenceError on a deleted document
        return False


def _document_closed_msg(doc_name, stdout_text, tb=None):
    """Reply for when run_python code closed the document it was operating on.

    The transaction we opened went with the document, so there's nothing to
    commit or roll back on our now-deleted handle -- steer back to the supported
    pattern instead of surfacing a bare 'deleted object' ReferenceError."""
    import FreeCAD

    active = FreeCAD.ActiveDocument
    parts = [
        f"run_python closed the active document '{doc_name}' mid-call. Avoid "
        "closing or recreating the document from inside run_python: each call "
        "runs in an undoable transaction on that document, so closing it leaves "
        "nothing to commit and undo can't cover the change. To redo a document's "
        "contents, remove the objects with doc.removeObject(name) and rebuild "
        "them in place instead.",
        f"The active document is now '{active.Name}'." if active is not None
        else "There is no active document now.",
    ]
    if tb:
        parts.append(
            "The code also raised before finishing (no rollback was possible -- "
            "the document was already gone):\n" + tb
        )
    if stdout_text:
        parts.append("stdout:\n" + stdout_text)
    return "\n".join(parts)


def _run_python(args):
    import contextlib
    import io
    import traceback

    import FreeCAD

    code = args.get("code", "")
    doc = FreeCAD.ActiveDocument or FreeCAD.newDocument()

    _save_run_python_script(code, args.get("description") or "")

    namespace = scripting_namespace(doc)

    existing = {obj.Name for obj in doc.Objects}
    doc_name = doc.Name  # remember it now -- the handle dies if the code closes it
    doc.openTransaction("FreeCADClaude: run_python")
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            exec(code, namespace)  # noqa: S102 - intentional: this is the tool's whole job
        if not _doc_alive(doc):
            # The code closed the document mid-run; the transaction went with it,
            # so don't touch the stale handle -- report it and bail cleanly.
            return _document_closed_msg(doc_name, stdout.getvalue())
        doc.recompute()
        doc.commitTransaction()
    except Exception:
        tb = traceback.format_exc()
        captured = stdout.getvalue()
        if not _doc_alive(doc):
            # Same case, but the code also raised: no rollback is possible on a
            # document that no longer exists, so don't crash trying to abort it.
            return _document_closed_msg(doc_name, captured, tb)
        doc.abortTransaction()
        # Safety net: if undo is disabled (so abort didn't roll back), remove any
        # objects this failed run added. No-op when abort already removed them.
        for obj in list(doc.Objects):
            if obj.Name not in existing:
                try:
                    doc.removeObject(obj.Name)
                except Exception:  # noqa: BLE001
                    pass
        msg = "Execution failed (rolled back):\n" + tb
        if captured:
            msg += "\n--- stdout before error ---\n" + captured
        return msg

    # Optional: snapshot the committed document so the build can be reviewed step
    # by step (off by default; see _save_steps_enabled). Kept out of the reply so
    # it stays a purely on-disk artifact and doesn't nudge the model.
    if _save_steps_enabled():
        _save_step_snapshot(doc, args.get("description") or "")

    parts = ["OK (committed)."]
    captured = stdout.getvalue()
    if captured:
        parts.append("stdout:\n" + captured)
    if namespace.get("result") is not None:
        parts.append("result: " + repr(namespace["result"]))
    return "\n".join(parts)
