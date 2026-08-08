#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Request-handling tests for freecad/freecadclaude/device_server.py.

Static serving and the token gate; the round trip -- publish/fetch, the SSE
wake-up, and the four upload gates (size, framing, magic bytes, and a filename
that tries to traverse); and the lifecycle -- what "New" clears, and the idle
auto-stop. The measurement payload rides along in the same checks rather than
in new ones, because it added no server surface: the scale is a few more keys
in a metadata dict this module already stores verbatim, and the annotation
document is a JSON part it already writes to disk without parsing. What is
asserted is exactly that -- that structure survives it untouched. All of it in
one file, on purpose: they share a running server and the checks are cheapest
when they can see each other's state (an upload has to show up in ``uploads()``,
a publish has to reach a stream opened before it, and ``reset_session`` has to
be seen to remove both).

The tools that drive this module are covered next door, in
``eval/test_device_tools.py``.

The module under test imports no Qt and no FreeCAD (that's the invariant that
keeps the LAN server unable to reach the document), so this needs no GUI and no
running FreeCAD -- any Python 3.8+ will do:

    python3 eval/test_device_server.py
    freecadcmd /abs/path/to/eval/test_device_server.py

(freecadcmd needs an ABSOLUTE path -- given a relative one it silently runs
nothing and still exits 0.)

Requires the web app to have been built (``npm install && npm run build`` in
web/), which produces the committed ``freecad/freecadclaude/device_ui/``.

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
_SHIM = "_fcc_device_test"


def _load_device_server():
    """Import device_server.py directly by path.

    Not ``from freecad.freecadclaude import device_server``: importing the
    package pulls in InitGui-adjacent machinery and (via ``__init__``) modules
    that expect a FreeCAD process. The module is deliberately standalone, so
    load it that way and keep the test honest about that.

    It does import one stdlib-only sibling -- ``web_static``, the static serving
    and path containment it shares with ``gcode_server`` -- and a relative
    import needs a package to resolve against. The stub below gives it one and
    nothing else: no ``__init__`` runs, and it can reach only that folder.
    """
    parent = types.ModuleType(_SHIM)
    parent.__path__ = [_PACKAGE]
    sys.modules[_SHIM] = parent
    path = os.path.join(_PACKAGE, "device_server.py")
    spec = importlib.util.spec_from_file_location(_SHIM + ".device_server", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


device_server = _load_device_server()

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
    anyway, which is how the oversize case is exercised without pushing 13 MB
    over the loopback. That is exactly the gate under test: the server refuses
    on the DECLARED length, before ``rfile.read``, so it never reads a body
    larger than the number it has already checked.
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


_BOUNDARY = "----FreeCADClaudeTestBoundary"


def _multipart(parts):
    """A multipart/form-data body from ``(name, filename, bytes)`` triples."""
    body = b""
    for name, filename, data in parts:
        disposition = f'form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        body += (
            f"--{_BOUNDARY}\r\n"
            f"Content-Disposition: {disposition}\r\n"
            # A deliberately WRONG declared type on the image part: the server
            # is supposed to believe the magic bytes and nothing else.
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        body += data + b"\r\n"
    body += f"--{_BOUNDARY}--\r\n".encode("utf-8")
    return body


#: A 1x1 PNG. Real bytes rather than just the signature -- the server doesn't
#: decode it, but the round-trip check compares what came back to what went in.
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


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


#: The idle timeout the auto-stop check runs at. Seconds rather than the real
#: half hour, obviously -- what is under test is the rule (in flight = alive,
#: nothing in flight = the clock runs), not the number.
_IDLE = 0.6


def _wait_until(predicate, timeout):
    """Poll `predicate` until it holds or `timeout` elapses. Returns its final
    value -- the stop happens on a watchdog thread, so there is nothing to join."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return bool(predicate())


def _check_idle_stop():
    """The idle auto-stop: a forgotten LAN listener shuts itself down.

    Three separate claims, and the middle one is the design decision worth
    pinning: traffic keeps the server up, an OPEN EVENT STREAM keeps it up even
    with no traffic at all (a tablet with the page open is a user who is
    probably drawing, and stopping under them would cost them the send), and it
    is only once that stream ends that the clock actually runs.

    ``_SSE_PING`` is turned down for the duration because a stream notices its
    device has gone only when a write fails -- at the real 20s that is fine
    against a half-hour timeout and useless against this one.
    """
    print("  -- idle auto-stop")
    stopped = []
    device_server.set_idle_hook(stopped.append)
    ping = device_server._SSE_PING
    device_server._SSE_PING = 0.2
    try:
        url, token = device_server.start(idle_timeout=_IDLE)
        host, port = "127.0.0.1", urllib.parse.urlsplit(url).port

        # Requests spread over more than one timeout: each one restarts the
        # clock, so the server must still be up at the end of them.
        for _ in range(3):
            time.sleep(_IDLE * 0.5)
            _request(host, port, f"/?t={token}")
        check("traffic keeps the server up", device_server.is_running())

        sock, _header, _rest = _sse_open(host, port, token)
        try:
            time.sleep(_IDLE * 2.0)
            check("an open event stream counts as connected too",
                  device_server.is_running())
        finally:
            sock.close()

        check("the server stops itself once nothing is connected",
              _wait_until(lambda: not device_server.is_running(), 10.0))
        # Waited for rather than read straight away: the hook fires AFTER
        # stop() has returned, and stop() clears is_running() at its top.
        check("the idle hook is told, with the timeout it was given",
              _wait_until(lambda: stopped == [_IDLE], 5.0), stopped)
        check("stopping that way still revokes the token",
              device_server.current_url() is None)
    finally:
        device_server._SSE_PING = ping
        device_server.set_idle_hook(None)
        device_server.stop()

    # And it can be turned off, which is what a 0 preference has to mean.
    device_server.start(idle_timeout=0)
    time.sleep(_IDLE * 2.0)
    check("idle_timeout=0 disables it", device_server.is_running())
    device_server.stop()


def main():
    print("device_server request handling")

    # The upload folder is handed IN, never resolved by the server: the real
    # caller (chat_panel) works out <session>/mobile/ on the GUI thread,
    # because that walk ends in a FreeCAD preference read.
    uploads_dir = tempfile.mkdtemp(prefix="fcc-device-test-")
    url, token = device_server.start(upload_dir=uploads_dir)
    parts = urllib.parse.urlsplit(url)
    host, port = "127.0.0.1", parts.port  # bound on 0.0.0.0; reach it locally
    query_token = urllib.parse.parse_qs(parts.query).get("t", [""])[0]

    try:
        check("start() is idempotent", device_server.start() == (url, token))
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
            host, port, "/assets/app.js", {"Cookie": f"fc_token={token}"}
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
        # A file that certainly exists outside the UI dir, reached by walking up
        # out of it. Two levels lands in the package dir; three in freecad/.
        traversals = [
            "/../device_server.py",
            "/../../../CLAUDE.md",
            "/assets/../../device_server.py",
            "/%2e%2e/device_server.py",           # percent-encoded ..
            "/%2e%2e%2fdevice_server.py",         # encoded separator too
            "/..%5cdevice_server.py",             # Windows separator
            "/....//device_server.py",            # naive single-pass stripping
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
            os.path.basename(device_server._resolve_static("/") or "") == "index.html",
        )
        check(
            "_resolve_static refuses to leave UI_DIR",
            device_server._resolve_static("/../device_server.py") is None,
        )
        check(
            "_resolve_static refuses a directory",
            device_server._resolve_static("/assets") is None,
        )

        # -- publish / fetch round trip ----------------------------------
        # The push direction: a tool on the GUI thread writes a PNG and hands
        # over the path; the device can then fetch it and nothing else.
        capture_path = os.path.join(uploads_dir, "sent_view_front.png")
        with open(capture_path, "wb") as fh:
            fh.write(_PNG)

        status, _, body = _request(host, port, "/api/latest", {"X-FC-Token": token})
        check("/api/latest before any publish -> null", status == 200
              and json.loads(body)["published"] is None, body[:120])

        record = device_server.publish(
            capture_path,
            {"document": "Bracket", "view": "front",
             # The measurement fields, exactly as tools_device._capture_meta
             # writes them. The server has no opinion about any of it -- that
             # is what "stored and served verbatim" means, and it is why the
             # scale could be added without touching this module.
             "scale": {"mm_per_px": 0.084198, "confidence": "exact",
                       "plane": "distances are true in the world X/Z plane"},
             "camera": {"azimuth": 0, "elevation": 0,
                        "projection": "orthographic", "axis_aligned": True},
             "context": "Document 'Bracket'. Camera angle: azimuth 0 deg."},
        )
        status, resp, body = _request(host, port, "/api/latest", {"X-FC-Token": token})
        published = json.loads(body)["published"]
        check("/api/latest reports the publish", status == 200
              and published["id"] == record["id"], body[:200])
        check("its metadata is served verbatim",
              published["meta"]["document"] == "Bracket"
              and published["meta"]["view"] == "front"
              and published["meta"]["scale"]["mm_per_px"] == 0.084198
              and published["meta"]["camera"]["axis_aligned"] is True,
              published["meta"])
        check("it points at /api/image/<id>",
              published["url"] == "/api/image/" + record["id"], published["url"])
        check("json content type",
              (resp.getheader("Content-Type") or "").startswith("application/json"))

        status, resp, body = _request(host, port, published["url"], {"X-FC-Token": token})
        check("/api/image/<id> returns the exact bytes", status == 200 and body == _PNG,
              f"got {status}, {len(body)} bytes")
        check("served as image/png",
              (resp.getheader("Content-Type") or "").startswith("image/png"),
              resp.getheader("Content-Type"))

        status, _, _ = _request(host, port, "/api/image/nosuchid", {"X-FC-Token": token})
        check("unknown image id -> 404", status == 404, f"got {status}")
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
            check("/api/events?t= -> 200", header.startswith(b"HTTP/1.1 200"), header[:60])
            check("streamed as text/event-stream",
                  b"text/event-stream" in header.lower(), header[:200])
            check("no Content-Length on a stream",
                  b"content-length" not in header.lower(), header[:200])
            second = device_server.publish(capture_path, {"document": "Bracket", "view": "top"})
            block = _sse_read_event(sock, rest)
            check("a publish wakes the stream", block.startswith(b"event: published"), block[:80])
            payload = json.loads(block.partition(b"data: ")[2] or b"{}")
            check("the event carries the new record", payload.get("id") == second["id"],
                  block[:160])
        finally:
            sock.close()

        # -- uploads -----------------------------------------------------
        form_type = f"multipart/form-data; boundary={_BOUNDARY}"
        before = set(os.listdir(uploads_dir))

        # A real annotation document, as web/src/doc.ts writes it -- dimension,
        # normalized coordinates, measured-vs-target and all. The server stores
        # it opaquely and never parses it, so the thing under test is that a
        # payload with structure survives the multipart round trip unchanged;
        # read_device_image quotes it verbatim and a mangled byte there would
        # be a wrong number in front of Claude.
        _DOC = (
            b'{"version":1,"image":"annotation.png",'
            b'"source":{"kind":"freecad_capture","id":"AbC123","document":"Bracket",'
            b'"camera":{"projection":"orthographic","view":"front","axis_aligned":true},'
            b'"scale":{"mm_per_px":0.084198,"confidence":"exact","plane":"world X/Z"}},'
            b'"annotations":[{"id":"d1","type":"dimension","a":[0.31,0.62],"b":[0.58,0.62],'
            b'"measured_mm":24.3,"target_mm":30,"note":"widen the slot","snapped_to":null}],'
            b'"caption":"slot too narrow"}'
        )
        status, _, body = _post(
            host, port, "/api/upload",
            _multipart([("image", "annotation.png", _PNG), ("doc", None, _DOC)]),
            form_type, {"X-FC-Token": token},
        )
        check("a valid upload -> 200", status == 200, f"got {status}: {body[:120]}")
        stored = json.loads(body).get("name", "")
        check("it reports a generated name", stored.startswith("upload_")
              and stored.endswith(".png"), stored)
        check("the file landed in the upload dir",
              os.path.isfile(os.path.join(uploads_dir, stored)), stored)
        with open(os.path.join(uploads_dir, stored), "rb") as fh:
            check("the stored bytes are the ones sent", fh.read() == _PNG)
        doc_path = os.path.splitext(os.path.join(uploads_dir, stored))[0] + ".json"
        check("the doc part is stored beside it", os.path.isfile(doc_path))
        with open(doc_path, "rb") as fh:
            check("...byte for byte as it arrived", fh.read() == _DOC)
        recorded = device_server.uploads()
        check("read_device_image can see it", len(recorded) == 1
              and recorded[-1]["path"] == os.path.join(uploads_dir, stored))
        check("the doc comes back verbatim, not parsed",
              recorded[-1]["doc"] == _DOC.decode("utf-8"), recorded[-1]["doc"])
        annotation = json.loads(recorded[-1]["doc"])["annotations"][0]
        check("the two numbers survive as separate facts",
              annotation["measured_mm"] == 24.3 and annotation["target_mm"] == 30,
              annotation)
        check("...with normalized coordinates and snapped_to reserved",
              annotation["a"] == [0.31, 0.62] and annotation["snapped_to"] is None,
              annotation)

        # Upload gate 1: the declared size.
        status, _, body = _post(
            host, port, "/api/upload", b"", form_type,
            {"X-FC-Token": token}, declared_length=13 * 1024 * 1024,
        )
        check("oversize -> 413", status == 413, f"got {status}: {body[:120]}")

        # Gate 2: the file's own first bytes, NOT the declared content type
        # (the part above declares application/octet-stream and is accepted;
        # this one would declare image/png and is not).
        status, _, body = _post(
            host, port, "/api/upload",
            _multipart([("image", "evil.png", b"GIF89a" + b"\x00" * 32)]),
            form_type, {"X-FC-Token": token},
        )
        check("wrong magic bytes -> 415", status == 415, f"got {status}: {body[:120]}")

        # Gate 3: there is nothing to traverse, because the client's filename is
        # never consulted -- the name is generated in one fixed folder.
        parent = os.path.dirname(uploads_dir)
        status, _, body = _post(
            host, port, "/api/upload",
            _multipart([("image", "../../../evil.png", _PNG),
                        ("doc", None, b"{}")]),
            form_type, {"X-FC-Token": token},
        )
        name = json.loads(body).get("name", "") if status == 200 else ""
        check("a traversing filename is ignored, not obeyed",
              status == 200 and ".." not in name and "/" not in name and "\\" not in name,
              f"got {status}: {name!r}")
        check("nothing was written outside the upload dir",
              not os.path.exists(os.path.join(parent, "evil.png"))
              and not os.path.exists(os.path.join(uploads_dir, "evil.png")))
        landed = set(os.listdir(uploads_dir)) - before
        check("every new file is inside the upload dir",
              all(os.path.isfile(os.path.join(uploads_dir, f)) for f in landed), sorted(landed))

        status, _, _ = _post(
            host, port, "/api/upload", _multipart([("doc", None, b"{}")]),
            form_type, {"X-FC-Token": token},
        )
        check("no image part -> 400", status == 400, f"got {status}")

        status, _, _ = _post(
            host, port, "/api/upload", b"not multipart", "application/json",
            {"X-FC-Token": token},
        )
        check("not multipart -> 415", status == 415, f"got {status}")

        status, _, _ = _post(host, port, "/api/upload", _multipart([]), form_type)
        check("upload is token-gated -> 403", status == 403, f"got {status}")

        status, _, _ = _post(
            host, port, "/api/nope", _multipart([]), form_type, {"X-FC-Token": token}
        )
        check("POST to an unknown route -> 404", status == 404, f"got {status}")

        # -- a new conversation ------------------------------------------
        # "New" in the chat panel: the captures and uploads belong to the chat
        # that made them, and the feed outlives a stop() on purpose -- so this
        # is the only thing standing between one conversation's marked-up
        # capture and the next conversation's read_device_image.
        check("there is state to clear",
              device_server.published_record() is not None and device_server.uploads())
        next_dir = tempfile.mkdtemp(prefix="fcc-device-test-new-")
        device_server.reset_session(next_dir)
        check("reset_session forgets the uploads", device_server.uploads() == [])
        check("...and the published capture", device_server.published_record() is None)
        status, _, body = _request(host, port, "/api/latest", {"X-FC-Token": token})
        check("...so /api/latest offers the device nothing", status == 200
              and json.loads(body)["published"] is None, body[:120])
        status, _, _ = _request(host, port, published["url"], {"X-FC-Token": token})
        check("...and the old image id is gone", status == 404, f"got {status}")
        status, _, body = _post(
            host, port, "/api/upload", _multipart([("image", "a.png", _PNG)]),
            form_type, {"X-FC-Token": token},
        )
        landed = json.loads(body).get("name", "") if status == 200 else ""
        check("the next upload lands in the new session's folder",
              status == 200 and os.path.isfile(os.path.join(next_dir, landed)),
              f"got {status}: {landed!r}")
        shutil.rmtree(next_dir, ignore_errors=True)

        # -- lifecycle ---------------------------------------------------
        check("is_running() while up", device_server.is_running())
        check("current_url() while up", device_server.current_url() == url)
    finally:
        device_server.stop()
        shutil.rmtree(uploads_dir, ignore_errors=True)

    check("is_running() after stop()", not device_server.is_running())
    check("stop() is idempotent", device_server.stop() is None)

    # A restart must mint a NEW token -- that's how stopping revokes a paired
    # device rather than merely pausing it.
    new_url, new_token = device_server.start()
    device_server.stop()
    check("restart mints a fresh token", new_token != token, new_url)

    _check_idle_stop()

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
