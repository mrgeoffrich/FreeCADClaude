#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Request-handling tests for freecad/freecadclaude/gcode_server.py.

Same shape and the same reasons as ``eval/test_device_server.py``: static
serving and the token gate, the publish/fetch round trip, and the two routes the
settings panel talks to. Three of the checks here are the ones worth having.

**Path traversal**, because the request surface is a directory of built assets
and the containment check is the only thing between a URL and the rest of the
disk. It now lives in ``web_static.resolve``, shared with the device server, so
it is exercised from both sides.

**The ``PUT`` validation**, because a stored preset name that nothing answers to
does not fail here -- it fails minutes into a slice with the argv as the only
clue. Every name in a proposed ``slicer.json`` is checked against what is
actually installed, and a valid-looking document with one bad name in it has to
be refused whole.

**The import list.** The module imports no Qt and no FreeCAD, which is what
stops an HTTP worker thread reaching the document, so this needs no GUI and no
running FreeCAD -- any Python 3.8+ will do:

    python3 eval/test_gcode_server.py

No slicer is started, here or anywhere else: they are GUI applications, and a
flag one does not take opens a dialog on the user's desktop. The preset tree
below is a handful of synthetic JSON files.

Requires the viewer to have been built (``npm ci && npm run build`` in
gcode_web/), which produces the committed ``freecad/freecadclaude/gcode_ui/``.

Exit: 0 = all passed, 1 = a failure.
"""

import http.client
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
import urllib.parse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PACKAGE = os.path.join(_ROOT, "freecad", "freecadclaude")

#: Module roots that must not appear because of our import. Snapshotted before
#: it rather than looked for afterwards, so running this inside an interpreter
#: that already has FreeCAD loaded still asks the right question.
_FORBIDDEN_ROOTS = ("FreeCAD", "FreeCADGui", "PySide", "PySide2", "PySide6",
                    "PyQt5", "PyQt6", "shiboken2", "shiboken6", "MeshPart")


def _forbidden(modules):
    return {name for name in modules if name.split(".")[0] in _FORBIDDEN_ROOTS}


_PRELOADED = _forbidden(sys.modules)

#: A stand-in parent package for the modules loaded by path below.
#:
#: Not ``from freecad.freecadclaude import gcode_server``: that would run the
#: real package, and the point of loading by path is that these modules are
#: standalone -- nothing else in the addon is imported. But ``gcode_server``
#: does import two stdlib-only siblings (``slicer_runner`` and ``web_static``,
#: the shared static serving), and a relative import needs a package to resolve
#: against. A stub module with a ``__path__`` gives it one and nothing else:
#: this package has no ``__init__`` to run and can reach only that folder.
_SHIM = "_fcc_gcode_test"


def _load(name):
    """Import ``freecad/freecadclaude/<name>.py`` under the stub package."""
    if _SHIM not in sys.modules:
        parent = types.ModuleType(_SHIM)
        parent.__path__ = [_PACKAGE]
        sys.modules[_SHIM] = parent
    full = _SHIM + "." + name
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full, os.path.join(_PACKAGE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


gcode_server = _load("gcode_server")
web_static = _load("web_static")

_failures = []


def check(name, condition, detail=""):
    status = "ok  " if condition else "FAIL"
    print(f"  {status} {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(name)


def _request(host, port, path, headers=None, method="GET", body=None):
    """One request. Returns ``(status, response, body)``.

    Raw http.client rather than urllib: several of these paths must go out on
    the wire *unnormalised* (urllib collapses ``..`` segments client-side,
    which would test urllib instead of the server).
    """
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.putrequest(method, path, skip_host=False, skip_accept_encoding=True)
        if body is not None:
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            conn.putheader(key, value)
        conn.endheaders()
        if body is not None:
            conn.send(body)
        resp = conn.getresponse()
        return resp.status, resp, resp.read()
    finally:
        conn.close()


def _put_config(host, port, token, payload):
    """``(status, document)`` for a PUT of `payload` to /api/slicer/config."""
    body = json.dumps(payload).encode("utf-8")
    status, _resp, raw = _request(host, port, "/api/slicer/config",
                                  {"X-FC-Token": token}, method="PUT", body=body)
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, {"error": raw[:200]}


# -- the synthetic slicer install -------------------------------------------
# One printer with two nozzles, and presets whose names say less than their
# compatible_printers does -- so a validator that parsed names instead of
# reading the field would pass the wrong things.

MODEL = "Bambu Lab P2S"
OTHER_MODEL = "Bambu Lab P1S"
M04 = "Bambu Lab P2S 0.4 nozzle"
M02 = "Bambu Lab P2S 0.2 nozzle"
P04 = "0.20mm Standard @BBL P2S"
P02 = "0.10mm Standard @BBL P2S 0.2 nozzle"
BASE_PROCESS = "fdm_process_common"
F04 = "Bambu PLA Basic @BBL P2S"
F02 = "Bambu PLA Basic @BBL P2S 0.2 nozzle"


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    return path


def _build_install(root):
    """A profile tree and a config file, as the panel's discovery reads them."""
    system = os.path.join(root, "profiles", "BBL")
    _write_json(os.path.join(system, "machine", MODEL + ".json"),
                {"type": "machine_model", "name": MODEL,
                 "nozzle_diameter": "0.4;0.2", "default_materials": F04})
    _write_json(os.path.join(system, "machine", M04 + ".json"),
                {"type": "machine", "name": M04, "printer_model": MODEL,
                 "printer_variant": "0.4", "nozzle_diameter": ["0.4"],
                 "default_print_profile": P04, "default_filament_profile": [F04]})
    _write_json(os.path.join(system, "machine", M02 + ".json"),
                {"type": "machine", "name": M02, "inherits": M04,
                 "printer_variant": "0.2", "nozzle_diameter": ["0.2"],
                 "default_print_profile": P02, "default_filament_profile": [F02]})
    for name, printer in ((P04, M04), (P02, M02)):
        _write_json(os.path.join(system, "process", name + ".json"),
                    {"type": "process", "name": name,
                     "compatible_printers": [printer]})
    # A vendor base profile: compatible with everything because it constrains
    # nothing, and not something a user may choose.
    _write_json(os.path.join(system, "process", BASE_PROCESS + ".json"),
                {"type": "process", "name": BASE_PROCESS, "instantiation": "false"})
    for name, printer in ((F04, M04), (F02, M02)):
        _write_json(os.path.join(system, "filament", name + ".json"),
                    {"type": "filament", "name": name,
                     "compatible_printers": [printer]})

    conf = _write_json(os.path.join(root, "config", "BambuStudio.conf"),
                       {"models": [{"model": MODEL, "nozzle_diameter": "0.2;0.4",
                                    "vendor": "BBL"},
                                   {"model": OTHER_MODEL, "nozzle_diameter": "0.4",
                                    "vendor": "BBL"}],
                        "presets": {"machine": M04, "process": P04,
                                    "filaments": [F04]}})
    return {"conf": conf, "profile_dirs": [system]}


#: A G-code file small enough to compare byte for byte, real enough to be one.
_GCODE = (b"; generated by BambuStudio\n; total layer number: 2\n"
          b"; FEATURE: Outer wall\nG1 X10 Y10 E1\nG1 X20 Y10 E2\n")


def _check_import_invariant():
    print("  -- the no-FreeCAD, no-Qt invariant")
    now = _forbidden(sys.modules)
    check("importing it loads no FreeCAD and no Qt module",
          now <= _PRELOADED, sorted(now - _PRELOADED))
    if _PRELOADED:
        print("       (this interpreter had %s loaded already)"
              % ", ".join(sorted(_PRELOADED)))


def _check_tokens(host, port, token):
    print("  -- the token gate")
    status, _, _ = _request(host, port, "/")
    check("no token -> 403", status == 403, f"got {status}")
    status, _, _ = _request(host, port, "/?t=" + "x" * len(token))
    check("wrong ?t= -> 403", status == 403, f"got {status}")
    status, _, _ = _request(host, port, "/", {"X-FC-Token": "nope"})
    check("wrong X-FC-Token -> 403", status == 403, f"got {status}")
    status, _, _ = _request(host, port, "/assets/app.js")
    check("assets are gated too -> 403", status == 403, f"got {status}")
    status, _, _ = _request(host, port, "/api/slicer/options")
    check("the options route is gated -> 403", status == 403, f"got {status}")
    status, _, _ = _request(host, port, "/api/slicer/config")
    check("the config route is gated -> 403", status == 403, f"got {status}")
    status, _ = _put_config(host, port, "not-the-token", {"orient": False})
    check("a PUT with the wrong token -> 403", status == 403, f"got {status}")
    # The cookie the device server sets is deliberately named apart from this
    # one: cookies are scoped by host and not by port, so a user who reached
    # both at 127.0.0.1 would otherwise have each overwrite the other's.
    status, _, _ = _request(host, port, "/", {"Cookie": f"fc_token={token}"})
    check("the device server's cookie name does not authenticate here",
          status == 403, f"got {status}")


def _check_static(host, port, token):
    print("  -- serving the built viewer")
    status, resp, body = _request(host, port, f"/?t={token}")
    check("page load with ?t= -> 200", status == 200, f"got {status}")
    check("index.html body served", b"<html" in body.lower(), body[:80])
    check("content type is html",
          (resp.getheader("Content-Type") or "").startswith("text/html"),
          resp.getheader("Content-Type"))
    cookie = resp.getheader("Set-Cookie") or ""
    check("page load sets the asset cookie", token in cookie, cookie)
    check("cookie is HttpOnly", "HttpOnly" in cookie, cookie)
    check("no CORS headers", resp.getheader("Access-Control-Allow-Origin") is None)

    # The browser's own sub-resource request: no query, no header, cookie only.
    status, resp, body = _request(host, port, "/assets/app.js",
                                  {"Cookie": f"fc_gcode_token={token}"})
    check("asset served on the cookie -> 200", status == 200, f"got {status}")
    check("js served as text/javascript",  # text/plain is refused for ES modules
          (resp.getheader("Content-Type") or "").startswith("text/javascript"),
          resp.getheader("Content-Type"))
    check("asset body is non-empty", len(body) > 0)
    status, resp, _ = _request(host, port, f"/assets/parser.worker.js?t={token}")
    check("the workers are served as modules too", status == 200
          and (resp.getheader("Content-Type") or "").startswith("text/javascript"),
          f"{status} {resp.getheader('Content-Type')}")
    status, _, _ = _request(host, port, f"/no-such-file.js?t={token}")
    check("missing file -> 404", status == 404, f"got {status}")


def _check_traversal(host, port, token):
    print("  -- path traversal")
    # Files that certainly exist outside gcode_ui/: two levels up lands in the
    # package folder, three in freecad/.
    traversals = [
        "/../gcode_server.py",
        "/../../../CLAUDE.md",
        "/assets/../../gcode_server.py",
        "/%2e%2e/gcode_server.py",           # percent-encoded ..
        "/%2e%2e%2fgcode_server.py",         # encoded separator too
        "/..%5cgcode_server.py",             # Windows separator
        "/....//gcode_server.py",            # naive single-pass stripping
    ]
    for path in traversals:
        status, _, body = _request(host, port, f"{path}?t={token}")
        check(f"traversal rejected: {path}",
              status == 404 and b"SPDX" not in body, f"got {status}")

    absolute = "/C:/Windows/win.ini" if os.name == "nt" else "//etc/hosts"
    status, _, _ = _request(host, port, f"{absolute}?t={token}")
    check(f"absolute path rejected: {absolute}", status == 404, f"got {status}")

    # And directly against the resolver, where the containment check lives --
    # no network in the way to mask a wrong answer.
    root = gcode_server.UI_DIR
    check("resolve() finds the index",
          os.path.basename(web_static.resolve(root, "/") or "") == "index.html")
    check("resolve() refuses to leave the root",
          web_static.resolve(root, "/../gcode_server.py") is None)
    check("resolve() refuses a directory",
          web_static.resolve(root, "/assets") is None)
    check("resolve() refuses an embedded NUL",
          web_static.resolve(root, "/index.html\x00.png") is None)

    # The separator in the containment test, not merely the prefix. A sibling
    # folder whose name starts with the root's own is what a bare startswith
    # lets through, and neither served tree has such a sibling today -- so it is
    # checked against a root built to have one.
    sandbox = tempfile.mkdtemp(prefix="fcc-root-test-")
    try:
        served = os.path.join(sandbox, "ui")
        os.makedirs(os.path.join(sandbox, "ui-secrets"))
        os.makedirs(served)
        for folder, name in ((served, "index.html"),
                             (os.path.join(sandbox, "ui-secrets"), "secret.txt")):
            with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
                fh.write("x")
        check("resolve() serves a file inside the root it was given",
              web_static.resolve(served, "/index.html")
              == os.path.realpath(os.path.join(served, "index.html")))
        check("resolve() refuses a sibling folder whose name starts with the root's",
              web_static.resolve(served, "/../ui-secrets/secret.txt") is None,
              web_static.resolve(served, "/../ui-secrets/secret.txt"))
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def _check_publish(host, port, token, tmp):
    print("  -- publishing a G-code file")
    path = os.path.join(tmp, "plate_1.gcode")
    with open(path, "wb") as fh:
        fh.write(_GCODE)

    status, _, _ = _request(host, port, f"/api/gcode/nosuchid?t={token}")
    check("an unknown id -> 404", status == 404, f"got {status}")

    # An id is a dictionary key and never a path fragment, so there is no
    # containment check on this route -- which only holds while nothing joins
    # the id onto a directory. A 404 alone would not show that: a joined path
    # that misses 404s as well. What separates them is a name that WOULD
    # resolve if it were being joined.
    for probe in ("index.html", "assets/app.css", "../gcode_server.py",
                  "..%2fgcode_server.py"):
        status, _, body = _request(host, port, f"/api/gcode/{probe}?t={token}")
        check(f"an id is a key, not a path: {probe}",
              status == 404 and b"SPDX" not in body and b"<html" not in body.lower()
              and b"--panel" not in body, f"got {status}, {len(body)} bytes")

    record = gcode_server.publish(path, {"job": "143002_bracket"})
    check("publish mints an id, not a path",
          record["id"] and "/" not in record["id"] and record["id"] != path,
          record["id"])
    status, resp, body = _request(host, port,
                                  f"/api/gcode/{record['id']}?t={token}")
    check("/api/gcode/<id> returns the exact bytes",
          status == 200 and body == _GCODE, f"got {status}, {len(body)} bytes")
    check("...with the real filename attached, which the viewer branches on",
          "plate_1.gcode" in (resp.getheader("Content-Disposition") or ""),
          resp.getheader("Content-Disposition"))
    check("published_record() reads it back",
          (gcode_server.published_record(record["id"]) or {}).get("path") == path)
    check("...and defaults to the most recent",
          (gcode_server.published_record() or {}).get("id") == record["id"])

    url = gcode_server.page_url(record["id"])
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    check("page_url carries the token and the file id",
          query.get("t") == [token] and query.get("gcode") == [record["id"]], url)
    check("page_url with no file is just the page",
          gcode_server.page_url() == gcode_server.current_url(),
          gcode_server.page_url())

    # A job folder can be pruned out from under a tab left open.
    os.remove(path)
    status, _, body = _request(host, port, f"/api/gcode/{record['id']}?t={token}")
    check("a published file that has gone -> 404 saying so",
          status == 404 and b"no longer on disk" in body, f"got {status}")


def _check_options(host, port, token):
    print("  -- GET /api/slicer/options")
    status, resp, body = _request(host, port, "/api/slicer/options",
                                  {"X-FC-Token": token})
    check("options -> 200", status == 200, f"got {status}")
    check("json content type",
          (resp.getheader("Content-Type") or "").startswith("application/json"))
    options = json.loads(body)
    printers = {entry["model"]: entry for entry in options["printers"]}
    check("the printers the user has added are offered",
          sorted(printers) == sorted([MODEL, OTHER_MODEL]), sorted(printers))
    check("with the nozzles each supports and the machine preset for each",
          printers[MODEL]["nozzles"] == ["0.2", "0.4"]
          and printers[MODEL]["machines"] == {"0.2": M02, "0.4": M04},
          printers[MODEL])
    check("it filtered for the slicer's own selected machine",
          options["machine"] == M04, options["machine"])
    processes = [entry["name"] for entry in options["processes"]]
    filaments = [entry["name"] for entry in options["filaments"]]
    check("only the processes compatible with that machine are listed",
          processes == [P04], processes)
    check("the vendor's own base profiles are not offered as choices",
          BASE_PROCESS not in processes, processes)
    check("only the compatible filaments are listed", filaments == [F04], filaments)
    check("the machine's declared defaults are marked",
          [e["name"] for e in options["processes"] if e["default"]] == [P04]
          and [e["name"] for e in options["filaments"] if e["default"]] == [F04],
          (options["processes"], options["filaments"]))
    check("no filesystem paths are handed to the browser",
          all("path" not in entry
              for entry in options["processes"] + options["filaments"]),
          options["processes"][:1])
    check("the nozzle to default to is stated", options["nozzle_default"] == "0.4",
          options.get("nozzle_default"))

    # Changing the nozzle re-filters both lists, which is why the panel re-fetches
    # rather than filtering client-side.
    status, _, body = _request(host, port,
                               f"/api/slicer/options?machine={urllib.parse.quote(M02)}",
                               {"X-FC-Token": token})
    other = json.loads(body)
    check("?machine= re-filters for that machine",
          status == 200
          and [e["name"] for e in other["processes"]] == [P02]
          and [e["name"] for e in other["filaments"]] == [F02],
          (other["processes"], other["filaments"]))

    status, _, _ = _request(host, port, "/api/nope", {"X-FC-Token": token})
    check("an unknown /api route -> 404", status == 404, f"got {status}")


def _check_config(host, port, token, settings_path):
    print("  -- GET/PUT /api/slicer/config")
    status, _, body = _request(host, port, "/api/slicer/config",
                               {"X-FC-Token": token})
    check("config before anything is stored -> {}",
          status == 200 and json.loads(body)["settings"] == {}, body[:120])
    check("...and it says which file that is",
          json.loads(body)["path"] == settings_path, body[:200])

    good = {"printer": MODEL, "nozzle": "0.4", "machine": M04, "process": P04,
            "filament": F04, "orient": True, "arrange": True, "deviation": 0.05}
    status, answer = _put_config(host, port, token, good)
    check("a valid document is accepted", status == 200, (status, answer))
    check("...and stored as it was given", answer.get("settings") == good, answer)
    with open(settings_path, encoding="utf-8") as fh:
        on_disk = json.load(fh)
    check("...to slicer.json itself, which is what slicer_runner reads",
          on_disk == good, on_disk)
    status, _, body = _request(host, port, "/api/slicer/config",
                               {"X-FC-Token": token})
    check("GET reads back what PUT stored", json.loads(body)["settings"] == good)

    # The check worth having: a name nothing installed answers to does not fail
    # here, it fails minutes into a slice with the argv as the only clue.
    rejections = [
        ("an unknown process preset",
         dict(good, process="0.28mm Extra Draft @BBL P2S"), "0.28mm Extra Draft"),
        ("an unknown filament preset", dict(good, filament="Generic PETG"),
         "Generic PETG"),
        ("an unknown machine preset", dict(good, machine="Bambu Lab X9 0.4 nozzle"),
         "Bambu Lab X9"),
        ("a printer the user has not added", dict(good, printer="Prusa MK4"),
         "Prusa MK4"),
        ("a nozzle that printer does not have", dict(good, nozzle="0.8"), "0.8"),
        # Compatible with the OTHER machine, so a validator that only asked
        # "is this a preset that exists" would take it.
        ("a process for a different machine", dict(good, process=P02), P02),
        # Real, but not choosable: the vendor's own base profile.
        ("a vendor base profile", dict(good, process=BASE_PROCESS), BASE_PROCESS),
        # The lists have to be filtered for the machine in THIS document, not
        # for whatever the slicer happens to have selected. Every name here is
        # real and the machine is a 0.2 one, so only a validator that re-filtered
        # for it notices that the process belongs to the 0.4 machine.
        ("a document naming a machine other than the slicer's own",
         {"printer": MODEL, "nozzle": "0.2", "machine": M02, "process": P04,
          "filament": F02}, P04),
    ]
    for label, payload, quoted in rejections:
        status, answer = _put_config(host, port, token, payload)
        check(f"{label} is rejected", status == 400, (status, answer))
        check(f"...naming what was refused: {label}",
              quoted in str(answer.get("error", "")), answer)

    # A machine that does not match the printer and nozzle beside it: each name
    # is real on its own, and storing them together is what would be wrong.
    status, answer = _put_config(host, port, token,
                                 dict(good, nozzle="0.2", machine=M04))
    check("printer, nozzle and machine that disagree are rejected",
          status == 400 and M02 in str(answer.get("error", "")), (status, answer))

    for label, payload in (("a non-boolean orient", dict(good, orient="yes")),
                           ("a deviation of zero", dict(good, deviation=0)),
                           ("a negative deviation", dict(good, deviation=-1)),
                           ("an unknown key", dict(good, colour="red")),
                           ("a JSON array", [1, 2, 3])):
        status, answer = _put_config(host, port, token, payload)
        check(f"{label} is rejected", status == 400, (status, answer))

    status, _resp, raw = _request(host, port, "/api/slicer/config",
                                  {"X-FC-Token": token}, method="PUT",
                                  body=b"{not json")
    check("a body that is not JSON is rejected", status == 400, (status, raw[:120]))

    # Every one of those was refused whole -- the stored document is untouched.
    with open(settings_path, encoding="utf-8") as fh:
        check("no rejected document reached the file", json.load(fh) == good)

    # A partial document is legitimate: the panel stores orient/arrange before
    # a printer has been chosen, and resolve_presets treats an absent key as
    # "nothing was chosen here" rather than as an empty choice.
    status, answer = _put_config(host, port, token, {"orient": False})
    check("a document with no preset names at all is accepted",
          status == 200 and answer.get("settings") == {"orient": False},
          (status, answer))
    with open(settings_path, encoding="utf-8") as fh:
        check("...and replaces the file rather than merging into it",
              json.load(fh) == {"orient": False})

    status, _resp, _raw = _request(host, port, "/api/slicer/config",
                                   {"X-FC-Token": token}, method="PUT",
                                   body=b"x" * (65 * 1024))
    check("an oversized document -> 413", status == 413, f"got {status}")


def main():
    print("gcode_server request handling")
    _check_import_invariant()

    tmp = tempfile.mkdtemp(prefix="fcc-gcode-test-")
    install = _build_install(tmp)
    settings_path = os.path.join(tmp, "slicer.json")

    # Every path is resolved by the caller and handed in: the real one is the
    # GUI thread, because each of these walks ends in a FreeCAD preference read.
    url, token = gcode_server.start(settings_path=settings_path,
                                    conf_path=install["conf"],
                                    profile_dirs=install["profile_dirs"])
    parts = urllib.parse.urlsplit(url)
    host, port = parts.hostname, parts.port
    try:
        check("start() is idempotent",
              gcode_server.start() == (url, token), gcode_server.current_url())
        check("it binds loopback, not the LAN", host == "127.0.0.1", url)
        check("the URL carries the token as ?t=",
              urllib.parse.parse_qs(parts.query).get("t") == [token], url)
        check("token is url-safe and long enough",
              len(token) >= 20 and "?" not in token)

        _check_tokens(host, port, token)
        _check_static(host, port, token)
        _check_traversal(host, port, token)
        _check_publish(host, port, token, tmp)
        _check_options(host, port, token)
        _check_config(host, port, token, settings_path)

        check("is_running() while up", gcode_server.is_running())
        check("current_url() while up", gcode_server.current_url() == url)
    finally:
        gcode_server.stop()
        shutil.rmtree(tmp, ignore_errors=True)

    check("is_running() after stop()", not gcode_server.is_running())
    check("stop() is idempotent", gcode_server.stop() is None)

    # A restart mints a new token, which is what deauthorises a tab left open
    # on the previous one.
    _new_url, new_token = gcode_server.start()
    gcode_server.stop()
    check("restart mints a fresh token", new_token != token)

    # There is no viewer to serve in a checkout that was never built, and that
    # has to be a sentence rather than a listener on a directory that isn't there.
    missing = os.path.join(tempfile.gettempdir(), "fcc-gcode-not-built")
    shutil.rmtree(missing, ignore_errors=True)
    try:
        gcode_server.start(ui_dir=missing)
        check("an unbuilt viewer directory refuses to start", False, "it started")
        gcode_server.stop()
    except RuntimeError as exc:
        check("an unbuilt viewer directory refuses to start",
              missing in str(exc) and "npm" in str(exc), str(exc))
    finally:
        gcode_server._config["ui_dir"] = None

    print()
    if _failures:
        print(f"FAIL: {len(_failures)} check(s) failed: {', '.join(_failures)}")
        return 1
    print("PASS")
    return 0


# No __main__ guard: freecadcmd *imports* the script under a module name taken
# from the filename, so a guarded body would silently never run there. It also
# tears the process down on SystemExit without flushing, so do that here or the
# whole report is discarded and the run looks like it printed nothing.
_status = main()
sys.stdout.flush()
sys.exit(_status)
