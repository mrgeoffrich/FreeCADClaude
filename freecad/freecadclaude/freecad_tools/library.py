# SPDX-License-Identifier: LGPL-2.1-or-later
"""The script library: reusable run_python modules that outlive a conversation.

Two roots, both importable from run_python and both listed by ``script_library``:

    bundled    <addon>/library/          ships with the addon -- every user gets it
    personal   ~/FreeCADClaude/library/  this machine's own, grown mid-conversation

Personal comes FIRST on sys.path, so a personal module shadows a bundled one of
the same name. The index says so when it happens: a silently shadowed module is
a script whose source is not the source that runs, which is the one failure mode
here that produces confidently wrong answers rather than an error.

The index is built with ``ast`` -- parsed, never imported. A module here is
FreeCAD-flavoured (``import Part`` at the top), so importing one to read its
docstring would fail anywhere but inside FreeCAD and would run its top-level
code as a side effect of listing it. Parsing costs neither, and is why this
module is testable under a bare interpreter.

Infra, not a tool: it imports nothing from ``tools_*`` and no FreeCAD at all.
"""

import ast
import os

from .session import artifacts_dir

#: The shipped half, beside references/ in the addon. Read-only by convention --
#: a save always lands in the personal root, and promoting one into the addon is
#: a deliberate commit, not something a tool call can do.
BUNDLED_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "library")

#: Longest module summary carried in the index. The index is paid for on every
#: listing, so a module whose docstring opens with three paragraphs of rationale
#: contributes its first paragraph and a pointer to the file, not the essay.
_MAX_SUMMARY = 240


def personal_root():
    """Absolute path to the personal library, created on demand, or None.

    Outside every session folder, beside ``sketches/`` and ``slicer.json``, for
    the same reason those are: a script that was worth keeping outlives the
    conversation that produced it, and session folders are pruned.

    None rather than raising when the artifacts dir can't be resolved -- that
    read goes through a FreeCAD preference, so it is unavailable outside
    FreeCAD and can fail inside it. The bundled half needs neither, and losing
    the whole library to a preference read would be the wrong trade.
    """
    try:
        path = os.path.join(artifacts_dir(), "library")
        os.makedirs(path, exist_ok=True)
    except Exception:  # noqa: BLE001 - see above: degrade to bundled-only
        return None
    return path


def roots():
    """[(origin, path), ...] most-specific first -- the sys.path order.

    Omits the personal root when it is unavailable, so everything downstream
    sees a shorter list rather than a None to guard against.
    """
    personal = personal_root()
    roots_ = [("bundled", BUNDLED_ROOT)]
    if personal:
        roots_.insert(0, ("personal", personal))
    return roots_


def ensure_on_path():
    """Put both roots at the FRONT of sys.path, personal first, idempotently.

    Called from ``scripting_namespace`` so run_python and inspect_api get the
    same importable set -- ``from bisect_with_pegs import bisect`` rather than
    ``exec(open(...).read())``, which is what the scripts in a session folder
    had to do and is why they could not import each other.
    """
    import sys

    for _, path in reversed(roots()):
        if not os.path.isdir(path):
            continue
        if path in sys.path:
            sys.path.remove(path)  # re-insert, so the intended order is restored
        sys.path.insert(0, path)


# ------------------------------------------------------------------ the index

def _collapse(text):
    """Whitespace-collapse a docstring fragment onto one line."""
    return " ".join(text.split())


def _first_paragraph(doc):
    """The docstring's opening paragraph, collapsed and length-capped."""
    if not doc:
        return ""
    para = doc.strip().split("\n\n")[0]
    summary = _collapse(para)
    if len(summary) > _MAX_SUMMARY:
        summary = summary[:_MAX_SUMMARY].rsplit(" ", 1)[0] + " ..."
    return summary


def _signature(node):
    """``name(a, b=1, *rest, **kw)`` from a def node, without importing it."""
    args = node.args
    parts = []
    positional = list(getattr(args, "posonlyargs", [])) + list(args.args)
    # defaults align to the END of the positional list, so pad the front
    padding = [None] * (len(positional) - len(args.defaults))
    for arg, default in zip(positional, padding + list(args.defaults)):
        parts.append(arg.arg if default is None
                     else "%s=%s" % (arg.arg, _render(default)))
    if args.vararg:
        parts.append("*" + args.vararg.arg)
    elif args.kwonlyargs:
        parts.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        parts.append(arg.arg if default is None
                     else "%s=%s" % (arg.arg, _render(default)))
    if args.kwarg:
        parts.append("**" + args.kwarg.arg)
    return "%s(%s)" % (node.name, ", ".join(parts))


def _render(node):
    """A default value as source text, or ``...`` if this build can't unparse."""
    try:
        return ast.unparse(node)  # Python 3.9+
    except Exception:  # noqa: BLE001 - a signature is worth having without defaults
        return "..."


def _declared_order(tree):
    """The module's ``__all__`` as a list of names, or None if it has none.

    A module with genuinely reusable building blocks alongside one or two entry
    points would otherwise list the blocks first (definition order) and bury
    what a caller actually wants. ``__all__`` is the existing Python idiom for
    "this is my surface, in this order", so the index honours it rather than
    inventing a marker -- and it keeps the helpers importable, which
    underscore-prefixing them to tidy the index would not.
    """
    for node in tree.body:
        targets = ([node.target] if isinstance(node, ast.AnnAssign)
                   else getattr(node, "targets", []))
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            continue
        value = getattr(node, "value", None)
        if not isinstance(value, (ast.List, ast.Tuple)):
            return None
        names = [e.value for e in value.elts
                 if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        return names or None
    return None


def _summarise(path):
    """Parse one module into an index entry, or None if it won't parse.

    A file that does not parse is skipped rather than reported: the library is
    listed on a tool call that is about to do something else, and a syntax error
    in an unrelated module is not that call's problem. It stays visible as a
    module missing from the index.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError, ValueError):
        return None

    declared = _declared_order(tree)
    defs = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue  # a helper, not part of the module's surface
        defs[node.name] = {
            "signature": _signature(node),
            "summary": _first_paragraph(ast.get_docstring(node)),
        }
    # __all__ decides both membership and order; a name in it that is not a
    # public function here (a constant, a typo) simply has nothing to list.
    names = [n for n in declared if n in defs] if declared else list(defs)
    functions = [defs[n] for n in names]
    return {
        "module": os.path.splitext(os.path.basename(path))[0],
        "path": path,
        "summary": _first_paragraph(ast.get_docstring(tree)),
        "functions": functions,
    }


def index():
    """Every library module, personal first, each marked with its origin.

    An entry carries ``shadows`` when a personal module hides a bundled one of
    the same name, and ``shadowed_by`` on the bundled entry it hides. Both are
    listed -- dropping the hidden one would make the collision invisible, which
    is the thing worth surfacing.
    """
    entries, seen = [], {}
    for origin, root in roots():
        if not os.path.isdir(root):
            continue
        for filename in sorted(os.listdir(root)):
            if not filename.endswith(".py") or filename.startswith("_"):
                continue
            entry = _summarise(os.path.join(root, filename))
            if entry is None:
                continue
            entry["origin"] = origin
            if entry["module"] in seen:
                entry["shadowed_by"] = seen[entry["module"]]["origin"]
                seen[entry["module"]]["shadows"] = origin
            else:
                seen[entry["module"]] = entry
            entries.append(entry)
    return entries


def format_index(entries):
    """The index as the text the tool returns."""
    if not entries:
        return ("The script library is empty.\n\n" + _SAVE_HINT)

    lines = ["Reusable modules, already on sys.path for run_python -- import "
             "them, don't re-derive them:", ""]
    for entry in entries:
        head = "%s  [%s]" % (entry["module"], entry["origin"])
        if entry.get("shadowed_by"):
            head += "  -- SHADOWED by the %s copy; this file is NOT what runs" \
                % entry["shadowed_by"]
        lines.append(head)
        if entry["summary"]:
            lines.append("    " + entry["summary"])
        for func in entry["functions"]:
            lines.append("      " + func["signature"])
            if func["summary"]:
                lines.append("        " + func["summary"])
        lines.append("    source: " + entry["path"])
        lines.append("")
    lines.append("Read a module's source before calling it for anything "
                 "non-obvious -- the constants carry the reasoning (measured "
                 "clearances, why a cut goes one way), which the signature "
                 "cannot.")
    lines.append("")
    lines.append(_SAVE_HINT)
    return "\n".join(lines)


_SAVE_HINT = (
    "To add one: script_library with 'name' and 'code'. Save a script once it "
    "has proved itself on real geometry and would be worth having next time -- "
    "not every run_python call."
)


# ------------------------------------------------------------------- the save

def validate(name, code):
    """Return why this may not be saved, or "" if it may.

    The bar is what separates a library from an archive of fragments: a name
    that can be imported, code that parses, a module docstring, and at least one
    public callable. A session folder's scripts/ already keeps every call ever
    made -- this exists to hold the few worth calling again.
    """
    if not isinstance(name, str) or not name.isidentifier() or name.startswith("_"):
        return ("'%s' is not a usable module name. It has to be a plain Python "
                "identifier -- lower_snake_case, no extension, no leading "
                "underscore -- because it is imported as one: "
                "`from bisect_with_pegs import bisect`." % (name,))
    if __import__("keyword").iskeyword(name):
        return "'%s' is a Python keyword, so nothing could import it." % (name,)
    if not isinstance(code, str) or not code.strip():
        return "script_library needs the module's full source in 'code'."

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return "That code does not parse: %s (line %s)." % (exc.msg, exc.lineno)

    if not ast.get_docstring(tree):
        return ("A library module needs a module docstring, and it is not "
                "paperwork: it is what the index shows and the only place the "
                "REASONING survives -- why the approach is this one, what was "
                "measured rather than guessed, what was tried and printed "
                "badly. State what it does, then why it does it that way, then "
                "a usage example.")

    public = [n for n in tree.body
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and not n.name.startswith("_")]
    if not public:
        return ("A library module needs at least one public top-level function "
                "-- the thing a later conversation calls. A script that only "
                "runs top-level code against one document is a run_python call, "
                "and the session's scripts/ folder already keeps those. Lift "
                "the document name, object labels and output paths out into "
                "arguments.")
    declared = _declared_order(tree)
    if declared is not None and not [n for n in declared
                                     if n in {p.name for p in public}]:
        return ("__all__ names none of this module's public functions (%s), so "
                "its library entry would list nothing. Put the entry points in "
                "__all__, in the order a caller should meet them, or drop it "
                "and they will be listed in definition order."
                % ", ".join(sorted(n.name for n in public)))

    for node in public:
        if not ast.get_docstring(node):
            return ("%s() has no docstring. Every public function in the "
                    "library needs one -- its first line is what the index "
                    "shows, and an entry point nobody can read is one nobody "
                    "calls." % (node.name,))
    return ""


def save(name, code):
    """Write a validated module into the personal root; return (path, replaced).

    Raises if the personal root is unavailable -- unlike listing, a save has no
    degraded form worth offering: the bundled root is the addon's own folder and
    a tool call must not write there.
    """
    root = personal_root()
    if root is None:
        raise RuntimeError(
            "The personal library folder could not be created under the "
            "artifacts directory, so there is nowhere to save. Check the "
            "FreeCADClaude ArtifactsDir preference and the folder's permissions."
        )
    path = os.path.join(root, name + ".py")
    replaced = os.path.exists(path)
    if not code.endswith("\n"):
        code += "\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(code)
    ensure_on_path()
    return path, replaced


def shadowed_note(name):
    """A warning if this personal module now hides a bundled one, else ""."""
    if not os.path.exists(os.path.join(BUNDLED_ROOT, name + ".py")):
        return ""
    return ("\n\nNote: this now SHADOWS the bundled %s, which stays on disk but "
            "will not be what imports. If the intent was to improve the bundled "
            "one rather than fork it, the change belongs in the addon's "
            "library/ folder as a commit." % (name,))
