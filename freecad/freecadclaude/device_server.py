# SPDX-License-Identifier: LGPL-2.1-or-later
"""LAN HTTP server that hands the annotation web app to a phone or tablet.

Stdlib only, like ``mcp_server.py``: no Qt, no FreeCAD, not even indirectly.
That is a deliberate constraint, not a coincidence -- **this server never calls
into FreeCAD**. Captures are *pushed* to it by a tool running on the GUI thread
(phase 4); nothing arriving over the network can cause a FreeCAD call, a
recompute or a document mutation. Keeping the import list free of FreeCAD is
the cheapest way to keep that invariant true by construction, and it also means
the whole module is testable under a plain interpreter (see
``eval/test_device_server.py``).

It also makes this module importable from any thread, which matters because
``start()`` hands the socket to a daemon thread and returns immediately.

    chat panel (GUI thread)  --start()-->  ThreadingHTTPServer (daemon thread)
                                              serves device_ui/ over the LAN

Unlike :mod:`gui_bridge`, which binds ``127.0.0.1``, this binds ``0.0.0.0`` --
a device on the same wifi has to reach it. The threat model is therefore
*someone else on your network*, and the mitigations are: off by default, a
fresh token per start, and a request surface that is a single realpath-contained
directory of static files. Phase 1 has no ``/api/*`` routes at all.
"""

import http.server
import mimetypes
import os
import posixpath
import secrets
import socket
import threading
import urllib.parse

#: The built web app (``web/`` → Vite → here). Committed to git: users install
#: from the ``main`` branch as a plain file copy, with no Node toolchain to
#: build it with. See web/vite.config.ts.
UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "device_ui")

#: Cookie the browser gets after a successful ``?t=`` page load. Sub-resource
#: requests (``/assets/app.js``) are issued by the browser itself and can carry
#: neither a query string we chose nor a header our JS set, so without this the
#: page would authenticate and then fail to load its own script. HttpOnly is
#: free here: the page keeps its own copy of the token in sessionStorage (see
#: src/token.ts) for the ``X-FC-Token`` header the API will want in phase 4.
_COOKIE = "fc_token"

#: Content types, spelled out rather than left to ``mimetypes``. On Windows
#: ``mimetypes`` reads the registry, where ".js" is routinely mapped to
#: "text/plain" or "application/x-javascript" by whatever installed itself
#: last -- and a script served as text/plain is refused by every browser's
#: strict MIME checking for ES modules. The fallback still covers anything a
#: later phase drops into the folder.
_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
}

_state = {"server": None, "thread": None, "url": None, "token": None}
_lock = threading.Lock()


def start():
    """Start the device server (idempotent). Returns ``(url, token)``.

    The URL is the one to type or scan on the device: it carries the token as
    ``?t=``, because a 22-character case-sensitive secret is not something
    anyone should be retyping on a tablet.
    """
    with _lock:
        if _state["server"] is not None:
            return _state["url"], _state["token"]

        if not os.path.isdir(UI_DIR):
            # Only reachable in a source checkout that has never been built --
            # the folder is committed, so an installed addon always has it.
            raise RuntimeError(
                f"the device UI has not been built: {UI_DIR} does not exist. "
                "Run 'npm install && npm run build' in web/."
            )

        token = secrets.token_urlsafe(16)
        # Port 0 -> the OS picks a free one. Nothing else needs to predict it;
        # the URL is handed to the device by QR or copy/paste.
        server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), _Handler)
        server.daemon_threads = True
        server.token = token  # how _Handler reaches it (one server, one token)

        thread = threading.Thread(
            target=server.serve_forever,
            name="freecadclaude-device-server",
            daemon=True,
        )
        thread.start()

        url = f"http://{lan_address()}:{server.server_address[1]}/?t={token}"
        _state.update(server=server, thread=thread, url=url, token=token)
        return url, token


def stop():
    """Stop the server and forget its token (idempotent).

    The next ``start()`` mints a fresh token, so a device paired with the old
    one is deauthorised by stopping -- that is the intended way to revoke.
    """
    with _lock:
        server = _state["server"]
        _state.update(server=None, thread=None, url=None, token=None)
    if server is None:
        return
    # shutdown() blocks until serve_forever's loop exits; it must not be called
    # from the serving thread itself, and never is -- callers are the GUI thread.
    server.shutdown()
    server.server_close()


def is_running():
    """Whether the server is currently listening."""
    return _state["server"] is not None


def current_url():
    """The pairing URL if running, else ``None``."""
    return _state["url"]


def lan_address():
    """This machine's primary LAN address, as the device would reach it.

    ``gethostbyname(gethostname())`` is unreliable (it happily returns
    127.0.1.1, or a VPN/loopback adapter). Opening a UDP socket toward an
    address forces the OS to pick the interface it would actually route
    through, and reading the socket's local end back gives that interface's
    address. No packet is sent -- UDP ``connect`` only sets the peer -- and
    192.0.2.0/24 is the reserved documentation range, so there is nothing there
    to reach even if one were.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 1))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"  # no network at all; the URL is at least honest
    finally:
        sock.close()


def _resolve_static(url_path):
    """Absolute path of the file `url_path` names inside :data:`UI_DIR`, or None.

    Deliberately not ``SimpleHTTPRequestHandler.translate_path``: that resolves
    against the *process* cwd, which for FreeCAD is wherever the user launched
    it from. This resolves against one fixed directory and then confirms, with
    ``realpath`` on both ends, that the result is still under it -- so ``..``
    segments, an absolute path, a Windows drive letter and a symlink pointing
    out of the tree are all rejected by the same check rather than by four
    separate string tests.
    """
    path = urllib.parse.urlsplit(url_path).path
    try:
        path = urllib.parse.unquote(path, errors="strict")
    except UnicodeDecodeError:
        return None
    if "\x00" in path:
        return None
    if path in ("", "/"):
        path = "/index.html"

    root = os.path.realpath(UI_DIR)
    try:
        target = os.path.realpath(os.path.join(root, path.lstrip("/")))
    except (OSError, ValueError):
        return None
    if target != root and not target.startswith(root + os.sep):
        return None
    if not os.path.isfile(target):
        return None
    return target


def _content_type(path):
    ext = posixpath.splitext(path)[1].lower()
    return _TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"


class _Handler(http.server.BaseHTTPRequestHandler):
    """Static files from :data:`UI_DIR`, token-gated. Nothing else, in phase 1."""

    # Keep-alive: a page load is index.html plus its script and stylesheet, and
    # a fresh TCP connection each over wifi is latency we can just not spend.
    # Every reply below sets Content-Length, which is what makes this safe.
    protocol_version = "HTTP/1.1"
    # Don't advertise the Python version to the LAN.
    server_version = "FreeCADClaude"
    sys_version = ""

    def do_GET(self):  # noqa: N802 (Qt/stdlib API casing)
        # Reset per request, not per instance: with keep-alive one handler
        # object serves every request on the connection, so a flag left set by
        # the page load would re-send Set-Cookie on each asset after it.
        self._set_cookie = False
        if not self._authorized():
            # 403, not 401: a WWW-Authenticate challenge would make the browser
            # pop a credentials dialog nobody can answer.
            self._send(403, b"forbidden", "text/plain; charset=utf-8")
            return

        path = _resolve_static(self.path)
        if path is None:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        self._send(200, body, _content_type(path))

    def do_HEAD(self):  # noqa: N802
        # Same policy as GET; BaseHTTPRequestHandler suppresses the body for us
        # only if we do, so just answer the headers by hand.
        self.do_GET()

    # -- auth ------------------------------------------------------------

    def _authorized(self):
        """True if this request carries the current token, in any of its forms.

        Three forms because three different callers need one: the device
        arrives with ``?t=`` (a scanned or typed URL), the page's own fetches
        will use ``X-FC-Token`` (phase 4), and the browser's sub-resource
        requests carry only the cookie we set on the way in.
        """
        token = getattr(self.server, "token", None)
        if not token:
            return False
        header = self.headers.get("X-FC-Token")
        if header and secrets.compare_digest(header, token):
            return True
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        supplied = (query.get("t") or [""])[0]
        if supplied and secrets.compare_digest(supplied, token):
            self._set_cookie = True
            return True
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == _COOKIE and value and secrets.compare_digest(value, token):
                return True
        return False

    # -- responses -------------------------------------------------------

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # The built asset names are fixed (no content hashes -- see
        # vite.config.ts), so a cached copy would survive a rebuild and serve
        # stale JS forever. Nothing here is big enough for caching to matter.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # The token is in the page URL; don't let it ride out on a Referer.
        self.send_header("Referrer-Policy", "no-referrer")
        if getattr(self, "_set_cookie", False):
            self.send_header(
                "Set-Cookie",
                f"{_COOKIE}={self.server.token}; Path=/; HttpOnly; SameSite=Strict",
            )
        # No CORS headers, at all: the page is same-origin with everything it
        # talks to, so any CORS header here would only be granting access to
        # something that shouldn't have it.
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        """Silence. The default writes a line per request to stderr, which under
        a windowed FreeCAD goes nowhere useful and under freecadcmd buries the
        actual output."""
