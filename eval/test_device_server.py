#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Request-handling tests for freecad/freecadclaude/device_server.py.

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
import os
import sys
import urllib.parse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_device_server():
    """Import device_server.py directly by path.

    Not ``from freecad.freecadclaude import device_server``: importing the
    package pulls in InitGui-adjacent machinery and (via ``__init__``) modules
    that expect a FreeCAD process. The module is deliberately standalone, so
    load it that way and keep the test honest about that.
    """
    path = os.path.join(_ROOT, "freecad", "freecadclaude", "device_server.py")
    spec = importlib.util.spec_from_file_location("_fcc_device_server", path)
    module = importlib.util.module_from_spec(spec)
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


def main():
    print("device_server request handling")

    url, token = device_server.start()
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

        # The API's form of the token, which phase 4 will use.
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

        # -- lifecycle ---------------------------------------------------
        check("is_running() while up", device_server.is_running())
        check("current_url() while up", device_server.current_url() == url)
    finally:
        device_server.stop()

    check("is_running() after stop()", not device_server.is_running())
    check("stop() is idempotent", device_server.stop() is None)

    # A restart must mint a NEW token -- that's how stopping revokes a paired
    # device rather than merely pausing it.
    new_url, new_token = device_server.start()
    device_server.stop()
    check("restart mints a fresh token", new_token != token, new_url)

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
