#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""The face-markup tool pair: freecad/freecadclaude/freecad_tools/tools_model.py.

view_model_3d's error paths and the whole of read_model_markup's resolution
logic under a real FreeCAD: export a box and a box-with-a-hole, publish,
simulate an uploaded ModelMarkupDoc -- constructed by hand, since a real
browser upload is Phase 4's territory, and filed through
``model_server._record_upload``, the exact function the HTTP handler calls
after storing the body -- and assert the confidence rules:

  - an unchanged shape hash resolves to the right "FaceN";
  - a mutated object's mark degrades to a warning carrying the recorded
    centroid, never a confident face name -- while a mark on a DIFFERENT,
    untouched object stays confident (the check is per-object, matching
    diagnostics._shape_metrics' granularity);
  - an object that no longer exists degrades the same way;
  - a face_index out of range for the recorded faces map is a warning, not
    a crash;
  - a publish that is gone (or absent from the document) degrades too;
  - a malformed document reads as "no markup", not a traceback.

The confirmation render is attempted whenever the environment can create an
offscreen view; under a bare freecadcmd there is no GUI document, the tool
degrades to its text-only result, and the test records that rather than
failing -- a GL context is not this file's contract (the render verification
is a separate, interactive check on a real display).

Imported through ``freecadclaude`` rather than by path, like
test_device_tools: tools_model asks the *package's* model_server whether it is
running and publishes into its feed, so a module loaded a second way would
have its own separate state and the test would be asking the wrong object.

    freecadcmd /abs/path/to/eval/test_model_tools.py
    python3 eval/test_model_tools.py     # skips the cases that need FreeCAD

(freecadcmd needs an ABSOLUTE path -- given a relative one it silently runs
nothing and still exits 0.)

Exit: 0 = all passed, 1 = a failure.
"""

import json
import os
import shutil
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# `freecadclaude`, not `freecad.freecadclaude`: FreeCAD owns the `freecad`
# namespace package in its own installation and a checkout can't shadow it.
sys.path.insert(0, os.path.join(_ROOT, "freecad"))

from freecadclaude import model_server  # noqa: E402
from freecadclaude.freecad_tools import (  # noqa: E402
    PARAM_PATH,
    model_export,
    tools_model,
)

_failures = []


def check(name, condition, detail=""):
    status = "ok  " if condition else "FAIL"
    # stderr, not stdout: freecadcmd suppresses a headless script's stdout.
    print(f"  {status} {name}" + (f" -- {detail}" if detail and not condition else ""),
          file=sys.stderr)
    if not condition:
        _failures.append(name)


def _text(result):
    """A tool returns a string, or (text, png_path) when it has an image."""
    return result[0] if isinstance(result, tuple) else result


def _have_freecad():
    try:
        import FreeCAD  # noqa: F401
    except ImportError:
        return False
    return True


def _upload(folder, doc_dict):
    """File a markup document the way the HTTP handler does: stored to disk,
    then ``_record_upload`` (the exact function do_POST's handler calls)."""
    text = json.dumps(doc_dict)
    fd, path = tempfile.mkstemp(prefix="upload_", suffix=".json", dir=folder)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    model_server._record_upload(path, text, len(text.encode("utf-8")))
    return text


def _mark(mark_id, obj, face_index, note=""):
    return {"id": mark_id, "object": obj, "face_index": face_index,
            "color": None, "note": note}


def main():
    print("model tools", file=sys.stderr)
    check("the server starts stopped", not model_server.is_running())

    # -- the highlight-list math, as pure logic (no FreeCAD needed) --------
    colors = tools_model._highlight_colors([(0.8, 0.8, 0.8, 0.0)], 6, [2])
    check("a uniform saved list is padded to the face count", len(colors) == 6, len(colors))
    check("...with the marked index set to the viewer's orange",
          colors[2] == (1.0, 0.55, 0.12, 0.0), colors)
    check("...and every other face left exactly as it was",
          all(c == (0.8, 0.8, 0.8, 0.0) for i, c in enumerate(colors) if i != 2), colors)
    check("an out-of-range marked index is ignored, not raised",
          tools_model._highlight_colors([(0.8, 0.8, 0.8, 0.0)], 6, [99])[0]
          == (0.8, 0.8, 0.8, 0.0))
    check("a full per-face list is left alone apart from the marks",
          tools_model._highlight_colors([(0.1,) * 4, (0.2,) * 4, (0.3,) * 4], 3, [0])
          == [(1.0, 0.55, 0.12, 0.0), (0.2, 0.2, 0.2, 0.2), (0.3, 0.3, 0.3, 0.3)])

    # -- view_model_3d error paths (need FreeCAD) --------------------------
    # freecadcmd opens no document, which is the same state as a FreeCAD
    # sitting on the start page.
    if _have_freecad():
        sent = _text(tools_model._run_view_model_3d({"objects": ["Box"]}))
        check("view_model_3d with no document -> a message",
              sent == "No active document.", sent)
    else:
        print("  skip no-active-document (needs freecadcmd)", file=sys.stderr)

    uploads_dir = tempfile.mkdtemp(prefix="fcc-model-tools-")
    artifacts = tempfile.mkdtemp(prefix="fcc-model-tools-art-")
    try:
        if not _have_freecad():
            print("  skip the FreeCAD flow (needs freecadcmd)", file=sys.stderr)
            return _finish()

        import FreeCAD  # noqa: F401 - the guard above already proved it imports

        # Isolate the session artifacts (the <session>/models/ folder the tool
        # writes) in a temp dir so the test neither pollutes nor depends on
        # the user's real FreeCADClaude folder.
        FreeCAD.ParamGet(PARAM_PATH).SetString("ArtifactsDir", artifacts)

        doc = FreeCAD.newDocument("ModelTools")
        box = doc.addObject("Part::Box", "Box")
        box.Length, box.Width, box.Height = 20, 30, 10
        cyl = doc.addObject("Part::Cylinder", "HoleCyl")
        cyl.Radius, cyl.Height = 4, 30
        cut = doc.addObject("Part::Cut", "Cut")  # a box with a hole
        cut.Base = box
        cut.Tool = cyl
        box2 = doc.addObject("Part::Box", "Box2")  # independent of Box, for
        box2.Length, box2.Width, box2.Height = 5, 6, 7  # the per-object check
        doc.recompute()

        sent = _text(tools_model._run_view_model_3d({"objects": ["Nope"]}))
        check("view_model_3d with an unknown object name -> a message",
              "No object named 'Nope'" in sent and "Traceback" not in sent, sent)

        # -- the happy path of view_model_3d (browser open patched) --------
        opened = []
        original_open = tools_model.webbrowser.open
        tools_model.webbrowser.open = lambda url, new=0: opened.append(url) or True
        try:
            sent = _text(tools_model._run_view_model_3d({"objects": ["Box", "Cut"]}))
        finally:
            tools_model.webbrowser.open = original_open
        check("view_model_3d exported, started the server and published",
              "Published the 3D geometry of" in sent and model_server.is_running(), sent)
        check("...opening the browser exactly once",
              len(opened) == 1 and "127.0.0.1" in opened[0], repr(opened))
        check("...and telling the user what comes next",
              "press Send" in sent and "read_model_markup" in sent, sent)
        record = model_server.published_record()
        check("the publish is live in the server",
              record is not None and set(record["objects"]) == {"Box", "Cut"},
              repr(sorted(record["objects"])) if record else None)

        # -- read before anything has been sent back ------------------------
        read = _text(tools_model._run_read_model_markup({}))
        check("read_model_markup before any upload -> a message",
              "Nothing has come back" in read, read)
        check("...and it says not to poll", "polling" in read, read)

        # -- unchanged hash resolves to the right FaceN ---------------------
        export = model_export.export_brep([box, cut], tools_model._session_subdir("models"))
        rec = model_server.publish(export, upload_dir=uploads_dir)
        _upload(uploads_dir, {
            "version": 1,
            "source": {"publish_id": rec["id"]},
            "marks": [
                _mark("m1", "Box", 3, "drill here"),
                _mark("m2", "Cut", 5),
            ],
            "caption": "mark the boss",
        })
        result = tools_model._run_read_model_markup({})
        text = _text(result)
        check("unchanged hash -> confident FaceN for both objects",
              "Box.Face4" in text and "Cut.Face6" in text, text)
        check("...with the user's note quoted", "drill here" in text, text)
        check("...and no warnings at all", "WARNING" not in text, text)
        if isinstance(result, tuple):
            png = result[1]
            check("the confirmation PNG exists and is non-empty",
                  os.path.isfile(png) and os.path.getsize(png) > 0, png)
        else:
            print("  note: no offscreen view in this environment -- the "
                  "confirmation render was skipped (needs a GUI/GL context)",
                  file=sys.stderr)

        # -- a mutated object degrades; an untouched one stays confident ----
        # The per-object granularity needs an object whose shape genuinely did
        # NOT change: Cut would re-compute when Box changes (its Base is Box),
        # so a mark on Cut must degrade too -- that is the hash telling the
        # truth. Box2 is independent, so ITS mark is the one that must hold.
        export_a = model_export.export_brep([box, box2],
                                            tools_model._session_subdir("models"))
        rec_a = model_server.publish(export_a, upload_dir=uploads_dir)
        box.Length = 40
        doc.recompute()
        _upload(uploads_dir, {
            "version": 1,
            "source": {"publish_id": rec_a["id"]},
            "marks": [
                _mark("m1", "Box", 0, "this moved"),
                _mark("m2", "Box2", 0),
            ],
            "caption": "",
        })
        text = _text(tools_model._run_read_model_markup({}))
        check("a changed shape hash degrades to a warning, not a FaceN",
              "- WARNING:" in text and "Box.Face1" not in text
              and "re-locate" in text.lower(), text)
        check("...carrying the recorded centroid",
              "centroid" in text and "At export" in text, text)
        recorded = export_a["objects"]["Box"]["faces"]["0"]["centroid"]
        check("...and the centroid is the one recorded at export",
              f"[{recorded[0]:g}, {recorded[1]:g}, {recorded[2]:g}]" in text, text)
        check("the per-object check: an UNCHANGED object's mark stays confident",
              "Box2.Face1" in text and text.count("- WARNING:") == 1, text)

        # -- an object that no longer exists degrades the same way ----------
        export_b = model_export.export_brep([box2],
                                            tools_model._session_subdir("models"))
        rec_b = model_server.publish(export_b, upload_dir=uploads_dir)
        doc.removeObject("Box2")
        _upload(uploads_dir, {
            "version": 1,
            "source": {"publish_id": rec_b["id"]},
            "marks": [_mark("m1", "Box2", 0, "gone")],
            "caption": "",
        })
        text = _text(tools_model._run_read_model_markup({}))
        check("a deleted object degrades with the recorded centroid, not a FaceN",
              "no longer exists" in text and "Box2.Face1" not in text
              and "centroid" in text, text)

        # -- a face_index out of range for the recorded faces map -----------
        export_c = model_export.export_brep([cut],
                                            tools_model._session_subdir("models"))
        rec_c = model_server.publish(export_c, upload_dir=uploads_dir)
        _upload(uploads_dir, {
            "version": 1,
            "source": {"publish_id": rec_c["id"]},
            "marks": [_mark("m1", "Cut", 99, "nope")],
            "caption": "",
        })
        text = _text(tools_model._run_read_model_markup({}))
        check("an out-of-range face index is a warning, not a crash",
              "out of range" in text and "Cut.Face100" not in text
              and "Traceback" not in text, text)

        # -- a publish that is gone (or never existed) ----------------------
        _upload(uploads_dir, {
            "version": 1,
            "source": {"publish_id": "no-such-publish"},
            "marks": [_mark("m1", "Box", 0)],
            "caption": "",
        })
        text = _text(tools_model._run_read_model_markup({}))
        check("a missing publish record degrades every mark",
              "cannot be validated" in text and "no longer available" in text, text)

        # -- a document without its source publish id -----------------------
        _upload(uploads_dir, {
            "version": 1,
            "source": {},
            "marks": [_mark("m1", "Box", 0)],
            "caption": "",
        })
        text = _text(tools_model._run_read_model_markup({}))
        check("a markup document without a publish id degrades too",
              "cannot be validated" in text, text)

        # -- malformed documents read as no markup, not a crash -------------
        for bad in ("this is not json", "[1, 2, 3]"):
            fd, path = tempfile.mkstemp(prefix="upload_", suffix=".json", dir=uploads_dir)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(bad)
            model_server._record_upload(path, bad, len(bad.encode("utf-8")))
            text = _text(tools_model._run_read_model_markup({}))
            check(f"a malformed document ({bad[:20]!r}) is a plain message",
                  "not a readable markup document" in text
                  and "Traceback" not in text, text)
        # Valid JSON that is a dict but carries no marks at all is still "no
        # markup", in the other sense: a plain message, not a traceback.
        fd, path = tempfile.mkstemp(prefix="upload_", suffix=".json", dir=uploads_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write('{"version": 1}')
        model_server._record_upload(path, '{"version": 1}', len('{"version": 1}'))
        text = _text(tools_model._run_read_model_markup({}))
        check("a document with no marks is a plain message too",
              "no face marks" in text and "Traceback" not in text, text)

        # -- index selection ------------------------------------------------
        # Uploads so far, oldest first: happy, stale, gone, out-of-range,
        # no-publish, no-source, then the three empty/malformed ones.
        uploads = model_server.uploads()
        check("an index past the end -> a message, not an IndexError",
              "doesn't exist" in _text(
                  tools_model._run_read_model_markup({"index": len(uploads)})), "")
        check("index 1 looks one further back (the [1,2,3] doc)",
              "not a readable markup document" in _text(
                  tools_model._run_read_model_markup({"index": 1})), "")
        check("a negative index clamps to the newest (the no-marks doc)",
              "no face marks" in _text(
                  tools_model._run_read_model_markup({"index": -3})), "")

        # -- a second view_model_3d does not reopen the browser -------------
        opened2 = []
        tools_model.webbrowser.open = lambda url, new=0: opened2.append(url) or True
        try:
            sent = _text(tools_model._run_view_model_3d({"objects": ["Cut"]}))
        finally:
            tools_model.webbrowser.open = original_open
        check("a second view_model_3d with the server up opens no new tab",
              "already running" in sent and opened2 == [], sent)

        # -- the whole tool surface registers -------------------------------
        from freecadclaude.freecad_tools import TOOLS, list_schemas
        check("both tools are registered",
              "view_model_3d" in TOOLS and "read_model_markup" in TOOLS)
        check("their schemas carry the documented names",
              {s["name"] for s in list_schemas()} >= {"view_model_3d",
                                                      "read_model_markup"})
        check("read_model_markup's index is optional",
              "index" in TOOLS["read_model_markup"]["schema"]["inputSchema"]
              ["properties"] and "required" not in
              TOOLS["read_model_markup"]["schema"]["inputSchema"])

        FreeCAD.closeDocument(doc.Name)
    finally:
        model_server.stop()
        shutil.rmtree(uploads_dir, ignore_errors=True)
        shutil.rmtree(artifacts, ignore_errors=True)

    return _finish()


def _finish():
    check("the server stops cleanly", not model_server.is_running())

    print(file=sys.stderr)
    if _failures:
        print(f"FAIL: {len(_failures)} check(s) failed: {', '.join(_failures)}",
              file=sys.stderr)
        return 1
    print("PASS", file=sys.stderr)
    return 0


# No __main__ guard: freecadcmd *imports* the script under a module name taken
# from the filename, so a guarded body would silently never run there -- which
# is the one interpreter this most needs to work under. It also tears the
# process down on SystemExit without flushing, so do that ourselves or the
# entire report is discarded and the run looks like it printed nothing.
_status = main()
sys.stdout.flush()
sys.exit(_status)
