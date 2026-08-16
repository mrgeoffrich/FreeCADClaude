#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Request-handling tests for freecad/freecadclaude/model_server.py.

The loopback server behind the face-markup viewer: static serving and the
token gate; the publish/fetch round trip -- ``/api/latest`` before and after,
``/api/mesh/<id>/<object_name>.brp`` returning the exact bytes, the SSE
wake-up; the JSON-body upload (rejected cleanly when it isn't JSON); the
``Cache-Control`` carve-out -- ``no-store`` on the JSON API answers, absent
for files served from the viewer directory; and the lifecycle -- the
``_KEEP_PUBLISHED`` cap evicting old publishes and ``reset_session`` clearing
a conversation's state. All of it in one file, on purpose: the checks share a
running server and are cheapest when they can see each other's state (an
upload has to show up in ``uploads()``, a publish has to reach a stream opened
before it, and ``reset_session`` has to be seen to remove both).

The module under test imports no Qt and no FreeCAD (that's the invariant that
keeps the loopback server unable to reach the document), so this needs no GUI
and no running FreeCAD -- any Python 3.8+ will do:

    python3 eval/test_model_server.py

Unlike test_device_server.py this needs no built web app either: the viewer
directory is a temp folder the test populates itself (``model_ui/`` is a later
phase's build output, and the server accepts its directory as an argument for
exactly that reason).

Exit: 0 = all passed, 1 = a failure.
"""

import http.client
import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import types
import urllib.parse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PACKAGE = os.path.join(_ROOT, "freecad", "freecadclaude")

#: A stand-in parent package for the module loaded by path below.
_SHIM = "_fcc_model_test"


def _load_model_server():
    """Import model_server.py directly by path.

    Not ``from freecad.freecadclaude import model_server``: importing the
    package pulls in InitGui-adjacent machinery and (via ``__init__``) modules
    that expect a FreeCAD process. The module is deliberately standalone, so
    load it that way and keep the test honest about that.

    It does import one stdlib-only sibling -- ``web_static``, the static serving
    and path containment it shares with ``device_server`` and ``gcode_server``
    -- and a relative import needs a package to resolve against. The stub below
    gives it one and nothing else: no ``__init__`` runs, and it can reach only
    that folder.
    """
    parent = types.ModuleType(_SHIM)
    parent.__path__ = [_PACKAGE]
    sys.modules[_SHIM] = parent
    path = os.path.join(_PACKAGE, "model_server.py")
    spec = importlib.util.spec_from_file_location(_SHIM + ".model_server", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


model_server = _load_model_server()

_failures = []


def check(name, condition, detail=""):
    status = "ok  " if condition else "FAIL"
    print(f"  {status} {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(name)


def _request(host, port, path, headers=None):
    """One GET. Returns ``(status, headers, body)``.

    Raw http.client rather than urllib: several of these paths must go out on
    the wire *unnormalised* (urllib collapses ``..`` segments client-side,
    which would test urllib instead of the server).
    """
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.putrequest("GET", path, skip_host=False, skip_accept_encoding=True)
        for key, value in (headers or {}).items():
            conn.putheader(key, value)
        conn.endheaders()
        resp = conn.getresponse()
        return resp.status, resp, resp.read()
    finally:
        conn.close()


def _post(host, port, path, body, content_type, headers=None, declared_length=None):
    """One POST. Returns ``(status, headers, body)``.

    ``declared_length`` overstates Content-Length and sends the (small) body
    anyway, which is how the oversize case is exercised without pushing a
    megabyte-plus over the loopback. That is exactly the gate under test: the
    server refuses on the DECLARED length, before ``rfile.read``, so it never
    reads a body larger than the number it has already checked.
    """
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.putrequest("POST", path, skip_host=False, skip_accept_encoding=True)
        conn.putheader("Content-Type", content_type)
        conn.putheader("Content-Length", str(declared_length or len(body)))
        for key, value in (headers or {}).items():
            conn.putheader(key, value)
        conn.endheaders()
        if declared_length is None:
            conn.send(body)
        resp = conn.getresponse()
        return resp.status, resp, resp.read()
    finally:
        conn.close()


def _sse_open(host, port, token):
    """Open /api/events and read past the response headers.

    Raw socket, not http.client: an event stream has no Content-Length and is
    framed by the connection closing, so the client has to read it as it
    arrives rather than as one body.
    """
    sock = socket.create_connection((host, port), timeout=10)
    sock.sendall(
        f"GET /api/events?t={token} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode("utf-8")
    )
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(4096)
        if not chunk:
            break
        head += chunk
    header, _, rest = head.partition(b"\r\n\r\n")
    return sock, header, rest


def _sse_read_event(sock, seen=b""):
    """Read until a complete SSE event (a blank-line-terminated block) that
    isn't just a comment. Returns the block, or b"" if the stream ended."""
    while True:
        while b"\n\n" in seen:
            block, _, seen = seen.partition(b"\n\n")
            if not block.startswith(b":"):  # skip ": connected" / ": ping"
                return block
        try:
            chunk = sock.recv(4096)
        except OSError:
            return b""
        if not chunk:
            return b""
        seen += chunk


#: The fake BREP payload published and fetched back. Real bytes rather than a
#: placeholder, so the round-trip check compares what came back to what went in.
_BRP = b"OCCT shape format\x00\x01\x02binary-ish payload" * 8

_FACES = {
    "0": {"centroid": [10.0, 15.0, 0.0], "normal": [0.0, 0.0, -1.0], "area": 600.0},
    "1": {"centroid": [10.0, 15.0, 5.0], "normal": [0.0, 0.0, 1.0], "area": 600.0},
}


def _publish_dict(path):
    """The exact shape ``model_export.export_brep`` returns, handed to
    ``publish``."""
    return {"objects": {"Box": {"path": path, "shape_hash": 1234567,
                                "faces": _FACES}}}


def main():
    print("model_server request handling")

    # The viewer directory and the upload folder are handed IN, never resolved
    # by the server: the real caller (a later phase's tool) works out
    # <session>/models/ on the GUI thread, because that walk ends in a FreeCAD
    # preference read. model_ui/ does not exist yet, so the test builds its own.
    ui_dir = tempfile.mkdtemp(prefix="fcc-model-ui-")
    uploads_dir = tempfile.mkdtemp(prefix="fcc-model-test-")
    with open(os.path.join(ui_dir, "index.html"), "wb") as fh:
        fh.write(b"<!doctype html><html><head></head><body>face markup</body></html>")
    os.makedirs(os.path.join(ui_dir, "assets"))
    with open(os.path.join(ui_dir, "assets", "app.js"), "wb") as fh:
        fh.write(b"console.log('face markup viewer')")
    with open(os.path.join(ui_dir, "assets", "app.css"), "wb") as fh:
        fh.write(b"body { margin: 0; }")

    url, token = model_server.start(ui_dir=ui_dir, upload_dir=uploads_dir)
    parts = urllib.parse.urlsplit(url)
    host, port = "127.0.0.1", parts.port
    query_token = urllib.parse.parse_qs(parts.query).get("t", [""])[0]

    try:
        check("start() is idempotent", model_server.start() == (url, token))
        check("the URL carries the token as ?t=", query_token == token, url)
        check("token is url-safe and long enough", len(token) >= 20 and "?" not in token)

        # -- token rejection ---------------------------------------------
        status, _, _ = _request(host, port, "/")
        check("no token -> 403", status == 403, f"got {status}")

        status, _, _ = _request(host, port, "/?t=" + "x" * len(token))
        check("wrong ?t= -> 403", status == 403, f"got {status}")

        status, _, _ = _request(host, port, "/", {"X-FC-Token": "nope"})
        check("wrong X-FC-Token -> 403", status == 403, f"got {status}")

        status, _, _ = _request(host, port, "/assets/app.js")
        check("assets are gated too -> 403", status == 403, f"got {status}")

        # -- a known asset served ----------------------------------------
        status, resp, body = _request(host, port, f"/?t={token}")
        check("page load with ?t= -> 200", status == 200, f"got {status}")
        check("index.html body served", b"<html" in body.lower(), body[:80])
        check(
            "content type is html",
            (resp.getheader("Content-Type") or "").startswith("text/html"),
            resp.getheader("Content-Type"),
        )
        cookie = resp.getheader("Set-Cookie") or ""
        check("page load sets the asset cookie", token in cookie, cookie)
        check("cookie is HttpOnly", "HttpOnly" in cookie, cookie)
        check("no CORS headers", resp.getheader("Access-Control-Allow-Origin") is None)

        # The browser's own sub-resource request: no query, no header, cookie only.
        status, resp, body = _request(
            host, port, "/assets/app.js", {"Cookie": f"fc_model_token={token}"}
        )
        check("asset served on the cookie -> 200", status == 200, f"got {status}")
        check(
            "js served as text/javascript",  # text/plain is refused for ES modules
            (resp.getheader("Content-Type") or "").startswith("text/javascript"),
            resp.getheader("Content-Type"),
        )
        check("asset body is non-empty", len(body) > 0)

        # The API's form of the token, which the page's own fetches use.
        status, _, _ = _request(host, port, "/assets/app.css", {"X-FC-Token": token})
        check("header token works too -> 200", status == 200, f"got {status}")

        status, _, _ = _request(host, port, f"/no-such-file.js?t={token}")
        check("missing file -> 404", status == 404, f"got {status}")

        # -- path traversal ----------------------------------------------
        # Files that certainly exist outside the viewer dir, reached by walking
        # up out of it. The UI dir here is a temp folder, so the targets name
        # the package dir instead of the repo root; the point is the 404 either
        # way.
        traversals = [
            "/../model_server.py",
            "/assets/../../model_server.py",
            "/%2e%2e/model_server.py",         # percent-encoded ..
            "/%2e%2e%2fmodel_server.py",       # encoded separator too
            "/..%5cmodel_server.py",           # Windows separator
            "/....//model_server.py",          # naive single-pass stripping
        ]
        for path in traversals:
            status, _, body = _request(host, port, f"{path}?t={token}")
            check(
                f"traversal rejected: {path}",
                status == 404 and b"SPDX" not in body,
                f"got {status}",
            )

        # An absolute path pasted in as the request target: on Windows a drive
        # letter, elsewhere a rooted path. Either way it must not escape.
        absolute = "/C:/Windows/win.ini" if os.name == "nt" else "//etc/hosts"
        status, _, _ = _request(host, port, f"{absolute}?t={token}")
        check(f"absolute path rejected: {absolute}", status == 404, f"got {status}")

        # And directly against the resolver, which is where the containment
        # check lives -- no network in the way to mask a wrong answer.
        check(
            "_resolve_static resolves the index",
            os.path.basename(model_server._resolve_static("/") or "") == "index.html",
        )
        check(
            "_resolve_static refuses to leave the viewer dir",
            model_server._resolve_static("/../model_server.py") is None,
        )
        check(
            "_resolve_static refuses a directory",
            model_server._resolve_static("/assets") is None,
        )

        # -- Cache-Control: the deliberate exception ---------------------
        # The other two servers send no-store on EVERY response because their
        # own build output has no content hash. model_ui/'s future vendored
        # WASM is version-pinned and only changes on a deliberate bump, so the
        # static path must not send it; the JSON API answers still must.
        status, resp, _ = _request(host, port, "/api/latest", {"X-FC-Token": token})
        check("a JSON route carries no-store", status == 200
              and resp.getheader("Cache-Control") == "no-store",
              resp.getheader("Cache-Control"))
        status, resp, _ = _request(host, port, f"/assets/app.js?t={token}")
        check("a viewer file carries no Cache-Control at all", status == 200
              and resp.getheader("Cache-Control") is None,
              resp.getheader("Cache-Control"))

        # -- publish / fetch round trip ----------------------------------
        # The push direction: a tool on the GUI thread writes the BREP files
        # and hands over the export dict; the browser can then fetch each one
        # and nothing else.
        brep_path = os.path.join(uploads_dir, "Box.brp")
        with open(brep_path, "wb") as fh:
            fh.write(_BRP)

        status, _, body = _request(host, port, "/api/latest", {"X-FC-Token": token})
        check("/api/latest before any publish -> null", status == 200
              and json.loads(body)["published"] is None, body[:120])

        record = model_server.publish(_publish_dict(brep_path))
        status, resp, body = _request(host, port, "/api/latest", {"X-FC-Token": token})
        published = json.loads(body)["published"]
        check("/api/latest reports the publish", status == 200
              and published["id"] == record["id"], body[:200])
        box = published["objects"]["Box"]
        check("the object entry names its mesh URL",
              box["url"] == f"/api/mesh/{record['id']}/Box.brp", box["url"])
        check("the shape hash rides along", box["shape_hash"] == 1234567,
              box["shape_hash"])
        check("the face map rides along, keyed as exported",
              box["faces"]["0"]["centroid"] == [10.0, 15.0, 0.0]
              and box["faces"]["1"]["area"] == 600.0, box["faces"])
        check("the stored path does not leak",
              "path" not in box and "uploads" not in json.dumps(published),
              body[:200])
        check("json content type",
              (resp.getheader("Content-Type") or "").startswith("application/json"))

        status, resp, body = _request(host, port, box["url"], {"X-FC-Token": token})
        check("/api/mesh/<id>/<name>.brp returns the exact bytes",
              status == 200 and body == _BRP, f"got {status}, {len(body)} bytes")
        check("served as application/octet-stream",
              (resp.getheader("Content-Type") or "").startswith(
                  "application/octet-stream"),
              resp.getheader("Content-Type"))

        # The 404s the plan promises are JSON error bodies, not plain text.
        status, resp, body = _request(host, port, "/api/mesh/nosuchid/Box.brp",
                                      {"X-FC-Token": token})
        check("unknown publish id -> 404 JSON", status == 404
              and (resp.getheader("Content-Type") or "").startswith(
                  "application/json")
              and "error" in json.loads(body), f"got {status}: {body[:120]}")
        status, resp, body = _request(host, port,
                                      f"/api/mesh/{record['id']}/Nosuch.brp",
                                      {"X-FC-Token": token})
        check("unknown object name -> 404 JSON", status == 404
              and (resp.getheader("Content-Type") or "").startswith(
                  "application/json")
              and "error" in json.loads(body), f"got {status}: {body[:120]}")

        status, _, _ = _request(host, port, "/api/latest")
        check("/api/latest is token-gated -> 403", status == 403, f"got {status}")
        status, _, _ = _request(host, port, "/api/nope", {"X-FC-Token": token})
        check("unknown /api route -> 404", status == 404, f"got {status}")

        # -- SSE ---------------------------------------------------------
        status, _, _ = _request(host, port, "/api/events")
        check("/api/events is token-gated -> 403", status == 403, f"got {status}")

        # EventSource cannot set a header, so this route takes the token as
        # ?t= -- the form _authorized already accepts everywhere.
        sock, header, rest = _sse_open(host, port, token)
        try:
            check("/api/events?t= -> 200", header.startswith(b"HTTP/1.1 200"),
                  header[:60])
            check("streamed as text/event-stream",
                  b"text/event-stream" in header.lower(), header[:200])
            check("no Content-Length on a stream",
                  b"content-length" not in header.lower(), header[:200])
            second = model_server.publish(_publish_dict(brep_path))
            block = _sse_read_event(sock, rest)
            check("a publish wakes the stream",
                  block.startswith(b"event: published"), block[:80])
            payload = json.loads(block.partition(b"data: ")[2] or b"{}")
            check("the event carries the new record",
                  payload.get("id") == second["id"], block[:160])
        finally:
            sock.close()

        # -- the JSON-body upload ----------------------------------------
        before = set(os.listdir(uploads_dir))

        # A real markup document, as a later phase's model_web/src/doc.ts will
        # write it. The server stores it opaquely and never parses its schema,
        # so the thing under test is that a payload with structure survives the
        # round trip unchanged; read_model_markup quotes it verbatim and a
        # mangled byte there would be a wrong face in front of Claude.
        _DOC = (
            b'{"version":1,"source":{"kind":"freecad_model","id":"' + record["id"].encode()
            + b'","document":"Bracket"},"marks":[{"id":"m1","object":"Box",'
            b'"face_index":4,"color":null,"note":"this face"}],'
            b'"caption":"mark the boss"}'
        )
        hook = []
        model_server.set_upload_hook(hook.append)
        status, resp, body = _post(
            host, port, "/api/upload", _DOC, "application/json",
            {"X-FC-Token": token},
        )
        check("a valid JSON upload -> 200", status == 200, f"got {status}: {body[:120]}")
        stored = json.loads(body).get("name", "")
        check("it reports a generated name", stored.startswith("upload_")
              and stored.endswith(".json"), stored)
        check("the file landed in the upload dir",
              os.path.isfile(os.path.join(uploads_dir, stored)), stored)
        with open(os.path.join(uploads_dir, stored), "rb") as fh:
            check("the stored bytes are the ones sent -- verbatim",
                  fh.read() == _DOC)
        check("the upload hook is told, with the stored path",
              hook == [os.path.join(uploads_dir, stored)], hook)
        recorded = model_server.uploads()
        check("read_model_markup can see it", len(recorded) == 1
              and recorded[-1]["path"] == os.path.join(uploads_dir, stored))
        check("the doc comes back verbatim, not parsed",
              recorded[-1]["doc"] == _DOC.decode("utf-8"), recorded[-1]["doc"])
        markup = json.loads(recorded[-1]["doc"])["marks"][0]
        check("the face reference survives as data",
              markup["object"] == "Box" and markup["face_index"] == 4, markup)

        # The declared Content-Type is a claim; the body is the truth.
        status, _, _ = _post(
            host, port, "/api/upload", _DOC, "text/plain", {"X-FC-Token": token},
        )
        check("a JSON body under a different declared type is still accepted",
              status == 200, f"got {status}")

        # A malformed body is refused CLEANLY: a JSON error reply, not a
        # traceback and not a dropped connection.
        status, resp, body = _post(
            host, port, "/api/upload", b"{not json", "application/json",
            {"X-FC-Token": token},
        )
        check("malformed JSON -> 400", status == 400, f"got {status}: {body[:120]}")
        check("...as a JSON error, not a traceback",
              (resp.getheader("Content-Type") or "").startswith("application/json")
              and b"Traceback" not in body and "error" in json.loads(body),
              body[:120])
        status, _, body = _post(
            host, port, "/api/upload", b"\x89PNG\r\n\x1a\nnot text at all",
            "application/json", {"X-FC-Token": token},
        )
        check("a non-text body -> 400 too", status == 400, f"got {status}: {body[:120]}")

        # Upload gate: the declared size.
        status, _, body = _post(
            host, port, "/api/upload", b"{}", "application/json",
            {"X-FC-Token": token}, declared_length=2 * 1024 * 1024,
        )
        check("oversize -> 413", status == 413, f"got {status}: {body[:120]}")

        status, _, _ = _post(host, port, "/api/upload", _DOC, "application/json")
        check("upload is token-gated -> 403", status == 403, f"got {status}")

        status, _, _ = _post(
            host, port, "/api/nope", b"{}", "application/json", {"X-FC-Token": token},
        )
        check("POST to an unknown route -> 404", status == 404, f"got {status}")

        # Only reachable when the server was started without an upload folder
        # -- which the tool never does, and which the server must refuse rather
        # than guess at.
        model_server._feed.upload_dir = None
        try:
            status, _, body = _post(
                host, port, "/api/upload", _DOC, "application/json",
                {"X-FC-Token": token},
            )
            check("no upload folder -> 503", status == 503, f"got {status}: {body[:120]}")
        finally:
            model_server.set_upload_dir(uploads_dir)

        # -- the retention cap -------------------------------------------
        # Old publishes age out past _KEEP_PUBLISHED; a pruned id 404s, it
        # doesn't raise.
        cap = model_server._KEEP_PUBLISHED
        ids = [record["id"], second["id"]]
        for index in range(cap):
            ids.append(model_server.publish(_publish_dict(brep_path))["id"])
        check("older than _KEEP_PUBLISHED is pruned",
              model_server.published_record(ids[0]) is None
              and model_server.published_record(ids[1]) is None,
              ids[:2])
        status, _, _ = _request(host, port, f"/api/mesh/{ids[0]}/Box.brp",
                                {"X-FC-Token": token})
        check("a pruned id 404s on the mesh route", status == 404, f"got {status}")
        check("the newest survives",
              model_server.published_record(ids[-1]) is not None
              and model_server.published_record()["id"] == ids[-1])
        status, _, body = _request(host, port, f"/api/mesh/{ids[-1]}/Box.brp",
                                   {"X-FC-Token": token})
        check("the newest is still fetchable", status == 200 and body == _BRP,
              f"got {status}")

        # -- a new conversation ------------------------------------------
        # "New" in the chat panel (a later phase wires it): the publishes and
        # uploads belong to the chat that made them, and the feed outlives a
        # stop() on purpose -- so this is the only thing standing between one
        # conversation's marked-up export and the next conversation's
        # read_model_markup.
        next_dir = tempfile.mkdtemp(prefix="fcc-model-test-new-")
        model_server.reset_session(next_dir)
        check("reset_session forgets the uploads", model_server.uploads() == [])
        check("...and the published export", model_server.published_record() is None)
        status, _, body = _request(host, port, "/api/latest", {"X-FC-Token": token})
        check("...so /api/latest offers the browser nothing", status == 200
              and json.loads(body)["published"] is None, body[:120])
        status, _, _ = _request(host, port, f"/api/mesh/{ids[-1]}/Box.brp",
                                {"X-FC-Token": token})
        check("...and the old publish id is gone", status == 404, f"got {status}")
        status, _, body = _post(
            host, port, "/api/upload", _DOC, "application/json", {"X-FC-Token": token},
        )
        landed = json.loads(body).get("name", "") if status == 200 else ""
        check("the next upload lands in the new session's folder",
              status == 200 and os.path.isfile(os.path.join(next_dir, landed)),
              f"got {status}: {landed!r}")
        shutil.rmtree(next_dir, ignore_errors=True)

        # -- lifecycle ---------------------------------------------------
        check("is_running() while up", model_server.is_running())
        check("current_url() while up", model_server.current_url() == url)
    finally:
        model_server.stop()
        shutil.rmtree(uploads_dir, ignore_errors=True)

    check("is_running() after stop()", not model_server.is_running())
    check("stop() is idempotent", model_server.stop() is None)

    # A restart must mint a NEW token -- that's how stopping revokes an open
    # tab rather than merely pausing it.
    new_url, new_token = model_server.start(ui_dir=ui_dir)
    model_server.stop()
    check("restart mints a fresh token", new_token != token, new_url)
    shutil.rmtree(ui_dir, ignore_errors=True)

    print()
    if _failures:
        print(f"FAIL: {len(_failures)} check(s) failed: {', '.join(_failures)}")
        return 1
    print("PASS")
    return 0


# No __main__ guard: freecadcmd *imports* the script under a module name taken
# from the filename, so a guarded body would silently never run there -- which
# is the one interpreter this most needs to work under. It also tears the
# process down on SystemExit without flushing, so do that ourselves or the
# entire report is discarded and the run looks like it printed nothing.
_status = main()
sys.stdout.flush()
sys.exit(_status)
