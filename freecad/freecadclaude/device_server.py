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
directory of static files plus the four ``/api/*`` routes below.

Phase 4 adds the round trip, and the invariant survives it intact:

    GET  /api/latest      what send_to_device last published (JSON)
    GET  /api/image/<id>  those bytes, by an id WE minted (never a path)
    POST /api/upload      an image + an opaque annotation document
    GET  /api/events      SSE; one 'published' event per send_to_device

The push direction never originates here -- :func:`publish` is called BY the
GUI thread, from ``tools_device.send_to_device``, and all this module does is
remember a path it was handed. The pull direction only ever reads a file that
already exists, and the upload direction only ever writes into a folder the GUI
thread told us about (see :func:`set_upload_dir`). No handler resolves a
session folder, reads a preference, or otherwise reaches for FreeCAD, which is
why none of this needs marshalling onto the GUI thread and why a LAN request
still cannot freeze it.
"""

import http.server
import json
import mimetypes
import os
import posixpath
import re
import secrets
import socket
import sys
import tempfile
import threading
import time
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

#: Upload ceiling. The client already downscales to a 1568px long edge before
#: POSTing (see web/src/canvas.ts), so anything approaching this is either a
#: client that didn't, or someone else on the wifi filling a disk.
_MAX_UPLOAD = 12 * 1024 * 1024

#: What an upload is allowed to be, decided by the first bytes of the FILE and
#: not by the Content-Type the client declared -- a declared type is a claim,
#: and the point of the check is the case where the claim is false.
_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
)

#: How many past captures stay fetchable. One would do for the normal flow, but
#: a device that was mid-load when the next capture landed would then 404 on the
#: id it had already been given.
_KEEP_PUBLISHED = 8

#: SSE keepalive interval. Also how quickly a reader notices the server stopped,
#: and how quickly a comment line notices a device that walked out of range --
#: the write is what fails, so it doubles as the dead-connection probe.
_SSE_PING = 20.0

#: ``name="..."`` of a part's Content-Disposition. Anchored on a ``;`` or the
#: start so it can't match the ``filename="..."`` sitting next to it.
_PART_NAME_RE = re.compile(rb'(?:^|;)\s*name="([^"]*)"')


class _Feed:
    """The published capture, the uploads that came back, and the Condition
    that wakes the SSE readers.

    One module-level instance, deliberately outliving any single ``start()``:
    ``read_device_image`` is often called after the user has stopped the server,
    and an upload that made it to disk shouldn't become unreadable because the
    listener went away.
    """

    def __init__(self):
        self.cond = threading.Condition()
        self.seq = 0            # bumped per publish; SSE readers track it
        self.latest = None      # the most recent published record, or None
        self.images = {}        # id -> record, capped at _KEEP_PUBLISHED
        self.order = []         # those ids, oldest first
        self.uploads = []       # upload records, oldest first
        self.upload_dir = None  # handed in by the GUI thread; see set_upload_dir
        self.closed = True      # no server up -> SSE readers should let go
        self.on_upload = None   # chat_panel's "an image arrived" hook


_feed = _Feed()


class _Server(http.server.ThreadingHTTPServer):
    """The listener, with two departures from the stdlib default.

    ``block_on_close``: an ``/api/events`` reader sits in a blocking wait for as
    long as the tablet holds the page open, and ``server_close()`` is called
    from the GUI thread. Joining those threads would freeze FreeCAD until the
    device closed its tab. They are daemons and stop themselves via
    ``_feed.closed`` (see :func:`stop`), so there is nothing to wait for.

    ``handle_error``: a device closing its tab, sleeping, or walking out of wifi
    range resets whatever it was holding -- and with an event stream open it is
    always holding something. The default prints a full traceback per drop,
    into FreeCAD's console, for something entirely routine. Anything that isn't
    a connection giving way is still reported.
    """

    daemon_threads = True
    block_on_close = False

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


_state = {"server": None, "thread": None, "url": None, "token": None}
_lock = threading.Lock()


def start(upload_dir=None):
    """Start the device server (idempotent). Returns ``(url, token)``.

    The URL is the one to type or scan on the device: it carries the token as
    ``?t=``, because a 22-character case-sensitive secret is not something
    anyone should be retyping on a tablet.

    ``upload_dir`` is where POSTed images land. It is passed IN rather than
    worked out here, and that is the whole shape of the no-FreeCAD invariant in
    one argument: resolving ``<session>/mobile/`` goes through
    ``session.artifacts_dir()``, which reads a FreeCAD preference. The caller
    (the chat panel, on the GUI thread) resolves it once and hands over a
    string; every later refresh comes the same way, from
    :func:`publish`. With none set, uploads are refused rather than guessed at.
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
        server = _Server(("0.0.0.0", 0), _Handler)
        server.token = token  # how _Handler reaches it (one server, one token)

        with _feed.cond:
            if upload_dir:
                _feed.upload_dir = upload_dir
            _feed.closed = False

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
    # Let the SSE readers go FIRST: each is parked in cond.wait() and would
    # otherwise sit there for up to _SSE_PING seconds after the socket died,
    # holding a thread per paired device.
    with _feed.cond:
        _feed.closed = True
        _feed.cond.notify_all()
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


# -- the push direction, called BY the GUI thread ---------------------------
# Everything below is the tool's half of the round trip. None of it runs on an
# HTTP thread, and none of it calls into FreeCAD either -- send_to_device does
# the FreeCAD work first and hands the results over as a path plus a plain dict.


def set_upload_dir(path):
    """Point uploads at `path` (an absolute directory). GUI thread only."""
    with _feed.cond:
        _feed.upload_dir = path


def set_upload_hook(callback):
    """Register "an image arrived", called with the stored path.

    Invoked ON THE HTTP WORKER THREAD, so whatever is registered has to be
    thread-safe: the chat panel passes a bound Qt signal's ``emit``, which Qt
    then queues onto the GUI thread. Nothing here may touch a widget.
    """
    with _feed.cond:
        _feed.on_upload = callback


def publish(path, meta=None, upload_dir=None):
    """Make `path` the capture the device can fetch, and wake the SSE readers.

    `meta` is stored and served verbatim -- this module has no opinion about
    what a capture's metadata contains, which is what lets phase 5 add the
    scale fields without touching the server. Returns the record; its ``id`` is
    what ``/api/image/<id>`` serves, and the only handle the device ever gets.
    A generated id rather than the filename is also the containment: no client
    string is ever joined onto a path.
    """
    record = {
        "id": secrets.token_urlsafe(8),
        "path": path,
        "meta": dict(meta or {}),
        "published_at": time.time(),
    }
    with _feed.cond:
        if upload_dir:
            _feed.upload_dir = upload_dir
        _feed.images[record["id"]] = record
        _feed.order.append(record["id"])
        while len(_feed.order) > _KEEP_PUBLISHED:
            _feed.images.pop(_feed.order.pop(0), None)
        _feed.latest = record
        _feed.seq += 1
        _feed.cond.notify_all()
    return record


def published_record(image_id=None):
    """The published capture with `image_id` (default: the latest), or None.

    How ``read_device_image`` replays the context recorded at PUBLISH time --
    camera angle, visible objects, world extents -- rather than recomputing it
    against a document the user has since moved on from.
    """
    with _feed.cond:
        if image_id is None:
            return _feed.latest
        return _feed.images.get(image_id)


def uploads():
    """Every image the device has POSTed this run, oldest first (a copy)."""
    with _feed.cond:
        return list(_feed.uploads)


def _public_record(record):
    """The JSON view of a published capture -- what the web app sees."""
    if record is None:
        return None
    return {
        "id": record["id"],
        "url": "/api/image/" + record["id"],
        "published_at": record["published_at"],
        "meta": record["meta"],
    }


# -- uploads ----------------------------------------------------------------


def _multipart_boundary(content_type):
    """The boundary bytes of a ``multipart/form-data`` Content-Type, or None."""
    main, _, params = (content_type or "").partition(";")
    if main.strip().lower() != "multipart/form-data":
        return None
    for param in params.split(";"):
        name, _, value = param.strip().partition("=")
        if name.strip().lower() == "boundary":
            return value.strip().strip('"').encode("ascii", "ignore") or None
    return None


def _parse_multipart(body, boundary):
    """``{part name: bytes}`` for a multipart/form-data body.

    Hand-rolled rather than ``cgi.FieldStorage``: ``cgi`` is gone in 3.13, and
    ``email``'s parser wants the whole thing decoded as text. Two parts and no
    nesting is not much to parse -- but it is byte-exact on purpose, because
    the payload is a PNG. In particular nothing is ``strip()``ped off the data:
    the only CRLF that isn't image content is the one immediately before the
    next delimiter, so exactly that one is removed.
    """
    parts = {}
    for raw in body.split(b"--" + boundary):
        if not raw.startswith(b"\r\n"):
            continue  # the preamble (empty), or the closing "--" delimiter
        head, sep, data = raw[2:].partition(b"\r\n\r\n")
        if not sep:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        name = _part_name(head)
        if name is not None:
            parts[name] = data
    return parts


def _part_name(head):
    """The form field name declared in a part's headers, or None."""
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-disposition:"):
            match = _PART_NAME_RE.search(line)
            return match.group(1).decode("utf-8", "replace") if match else None
    return None


def _image_suffix(data):
    """``.png``/``.jpg`` per the file's own leading bytes, or None if it is
    neither -- the check that decides whether an upload is stored at all."""
    for magic, suffix in _IMAGE_MAGIC:
        if data.startswith(magic):
            return suffix
    return None


def _store_upload(folder, image, suffix, doc):
    """Write an accepted upload into `folder`. Returns ``(path, doc_path)``.

    The client's own filename is not consulted, at all: the name is generated
    with ``mkstemp`` in one fixed directory, the same mechanism (and the same
    reason) as ``freecad_tools.session._artifact_path``. A traversal attempt in
    the multipart ``filename=`` therefore has nothing to traverse -- there is no
    code path that joins it onto anything.
    """
    os.makedirs(folder, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="upload_", suffix=suffix, dir=folder)
    with os.fdopen(fd, "wb") as fh:
        fh.write(image)
    doc_path = None
    if doc is not None:
        # Stored beside the image, opaquely: the annotation document's schema
        # belongs to the web app and to read_device_image, not to the server.
        doc_path = os.path.splitext(path)[0] + ".json"
        with open(doc_path, "wb") as fh:
            fh.write(doc)
    return path, doc_path


def _record_upload(path, doc_path, doc, size):
    """File the stored upload and tell whoever is listening. HTTP thread."""
    record = {
        "path": path,
        "doc_path": doc_path,
        "doc": (doc or b"").decode("utf-8", "replace"),
        "bytes": size,
        "received_at": time.time(),
    }
    with _feed.cond:
        _feed.uploads.append(record)
        hook = _feed.on_upload
    if hook is not None:
        try:
            hook(path)
        except Exception:  # noqa: BLE001 - a listener must not break the reply
            pass
    return record


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
    """Static files from :data:`UI_DIR` plus the four ``/api/*`` routes,
    token-gated. Nothing here reads the document, and nothing here can."""

    # Keep-alive: a page load is index.html plus its script and stylesheet, and
    # a fresh TCP connection each over wifi is latency we can just not spend.
    # Every reply below sets Content-Length, which is what makes this safe --
    # with the single exception of the event stream, which has no length and so
    # bypasses _send and closes the connection to frame itself.
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

        # API routes are matched BEFORE the static fall-through, so a file
        # dropped into device_ui/api/ can never shadow one of them.
        route = urllib.parse.urlsplit(self.path).path
        if route == "/api/latest":
            self._send_json(200, {"published": _public_record(_feed.latest)})
            return
        if route.startswith("/api/image/"):
            self._send_published(route[len("/api/image/"):])
            return
        if route == "/api/events":
            self._stream_events()
            return
        if route.startswith("/api/"):
            self._send_json(404, {"error": "no such endpoint"})
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

    def do_POST(self):  # noqa: N802
        self._set_cookie = False
        if not self._authorized():
            self._send(403, b"forbidden", "text/plain; charset=utf-8")
            return
        if urllib.parse.urlsplit(self.path).path != "/api/upload":
            self._send_json(404, {"error": "no such endpoint"})
            return
        self._handle_upload()

    # -- /api/image/<id> -------------------------------------------------

    def _send_published(self, image_id):
        """Serve a published capture's bytes by the id publish() minted.

        The id is a dictionary key, never a path fragment -- ``_resolve_static``
        exists for the static tree and there is deliberately no equivalent here,
        because nothing the client sends reaches the filesystem in the first
        place.
        """
        record = published_record(urllib.parse.unquote(image_id))
        if record is None:
            self._send_json(404, {"error": "no such image"})
            return
        try:
            with open(record["path"], "rb") as fh:
                body = fh.read()
        except OSError:
            # The session folder prunes to its most recent files, so a very old
            # capture can outlive its bytes. Say which it was.
            self._send_json(404, {"error": "that capture is no longer on disk"})
            return
        self._send(200, body, _content_type(record["path"]))

    # -- POST /api/upload ------------------------------------------------

    def _handle_upload(self):
        """Take one image (plus its opaque annotation document) off the device.

        Four gates, in cost order: the declared length, the multipart framing,
        the file's own magic bytes, and a configured destination folder. The
        magic-byte check is the one that matters -- the Content-Type of a part
        is whatever the client felt like writing.
        """
        try:
            length = int(self.headers.get("Content-Length"))
        except (TypeError, ValueError):
            self._reject(411, "Content-Length required")
            return
        if length < 0 or length > _MAX_UPLOAD:
            self._reject(413, f"upload too large (max {_MAX_UPLOAD // (1024 * 1024)} MB)")
            return

        boundary = _multipart_boundary(self.headers.get("Content-Type"))
        if boundary is None:
            self._reject(415, "expected multipart/form-data")
            return

        folder = _feed.upload_dir
        if not folder:
            # Only reachable if the server was started without one, which the
            # chat panel doesn't do -- but guessing a folder here is exactly the
            # kind of reaching-into-FreeCAD this module refuses to do.
            self._send_json(503, {"error": "no upload folder configured"})
            return

        body = self.rfile.read(length)  # exact: keep-alive framing depends on it
        parts = _parse_multipart(body, boundary)
        image = parts.get("image")
        if not image:
            self._send_json(400, {"error": "no 'image' part in the upload"})
            return
        suffix = _image_suffix(image)
        if suffix is None:
            self._send_json(415, {"error": "only PNG or JPEG images are accepted"})
            return

        try:
            path, doc_path = _store_upload(folder, image, suffix, parts.get("doc"))
        except OSError as exc:
            self._send_json(500, {"error": f"could not store the upload: {exc}"})
            return
        _record_upload(path, doc_path, parts.get("doc"), len(image))
        # The stored NAME, not the path: the device has no business knowing
        # where on the user's disk this landed.
        self._send_json(200, {"ok": True, "name": os.path.basename(path)})

    def _reject(self, status, message):
        """Refuse a request whose body we are NOT going to read.

        Leaving an unread body on a keep-alive connection desynchronises it --
        the next request would be parsed out of the middle of a PNG -- so
        anything that bails before ``rfile.read`` closes the connection instead.
        """
        self.close_connection = True
        self._send_json(status, {"error": message})

    # -- GET /api/events (SSE) -------------------------------------------

    def _stream_events(self):
        """One ``published`` event per send_to_device, until the device leaves.

        The one reply that does not go through ``_send``: an event stream has no
        length to declare, so it is framed by closing the connection (a legal
        HTTP/1.1 body delimiter) rather than by keep-alive.

        The token arrives as ``?t=`` on this route and only this route, because
        ``EventSource`` cannot set a request header -- ``_authorized`` already
        accepts the query form everywhere, so nothing special is needed here
        beyond saying why it's used. It doesn't widen the exposure: the token is
        in the page URL to begin with, and ``Referrer-Policy: no-referrer``
        keeps it off outbound requests.

        Woken by ``_feed.cond``, never by watching the filesystem. Waking on a
        timeout too is what makes both the keepalive and a prompt exit on
        ``stop()`` fall out of the same loop.
        """
        if self.command == "HEAD":
            self._send(200, b"", "text/event-stream; charset=utf-8")
            return

        # Subscribe BEFORE the headers go out, not after. A client treats the
        # arrival of the headers as "I am connected", so anything published
        # between here and there has to be delivered rather than skipped -- and
        # reading the sequence afterwards drops exactly that window on the
        # floor. It costs nothing: the loop below compares against this on its
        # first pass, so a publish that beats us to the wait is still sent.
        seen = _feed.seq

        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Connection", "close")
        self.end_headers()

        try:
            # An immediate comment line flushes the response headers, which is
            # what makes EventSource fire onopen rather than sit pending.
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                with _feed.cond:
                    if _feed.seq == seen and not _feed.closed:
                        _feed.cond.wait(_SSE_PING)
                    if _feed.closed:
                        return
                    payload = None
                    if _feed.seq != seen:
                        seen = _feed.seq
                        payload = _public_record(_feed.latest)
                if payload is None:
                    self.wfile.write(b": ping\n\n")
                else:
                    self.wfile.write(
                        b"event: published\ndata: "
                        + json.dumps(payload).encode("utf-8")
                        + b"\n\n"
                    )
                self.wfile.flush()
        except OSError:
            return  # the tab closed, or the device went out of range

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

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

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
