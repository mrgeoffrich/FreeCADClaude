# SPDX-License-Identifier: LGPL-2.1-or-later
"""Loopback HTTP server that hands the face-markup viewer its geometry.

Stdlib only, like ``device_server.py`` and ``gcode_server.py``: no Qt, no
FreeCAD, not even indirectly. The reason is the same one -- **no handler may
call into FreeCAD**. BREP exports are *pushed* here by a tool running on the
GUI thread (``tools_model.view_model_3d``, a later phase); nothing arriving
over the socket can cause a FreeCAD call, a recompute or a document mutation.
Keeping FreeCAD out of the import list is the cheapest way to keep that true
by construction, and it is what lets the whole module be tested under a plain
interpreter (see ``eval/test_model_server.py``).

    view_model_3d (GUI thread) --start()--> ThreadingHTTPServer (daemon thread)
                            --publish()->   serves model_ui/ on 127.0.0.1
                                            GET /api/latest
                                            GET /api/mesh/<id>/<object>.brp
                                            POST /api/upload
                                            GET /api/events (SSE)

Unlike :mod:`device_server`, which binds ``0.0.0.0`` because a phone has to
reach it, this binds ``127.0.0.1`` like :mod:`gcode_server`: the viewer is a
desktop browser on this machine, so nothing off the machine ever needs to
connect. That is why a tool may start it without making a security decision
on the user's behalf, and why there is no button, no QR and no idle watchdog
-- it stops with FreeCAD.

The token stays anyway, exactly as ``gui_bridge`` and ``gcode_server`` keep
one on loopback: a listener on 127.0.0.1 is reachable by every other process
on the machine.

Two things are passed IN from the GUI thread rather than looked up here, and
that is the whole shape of the no-FreeCAD invariant: the viewer directory
(the committed ``model_ui/``, overridable per start) and the folder uploaded
markup documents land in (``<session>/models/``). Each of those walks ends in
a FreeCAD preference read. A handler that "just looks one up" crosses the
line.

One deliberate departure from the other two servers' response headers:
``device_server`` and ``gcode_server`` send ``Cache-Control: no-store`` on
every response because their own JS/CSS carries no content hash, so a cached
copy would survive a rebuild and serve stale code forever. That reasoning
does NOT apply to ``model_ui/``'s vendored third-party assets -- a WASM
OpenCascade build pinned to an exact version, changing only on a deliberate
version bump -- so files under ``model_ui/`` are served without that header.
Everything else (the JSON API answers) still gets ``no-store``.
"""

import http.server
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import urllib.parse

from . import web_static

#: The built viewer (``model_web/`` → Vite → here). Committed to git: users
#: install from the ``main`` branch as a plain file copy, with no Node toolchain
#: to build it with. A later phase populates it; this server is ready to serve
#: it the moment it exists.
UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model_ui")

#: Cookie the browser gets after a successful ``?t=`` page load, so the browser's
#: own sub-resource requests (``/assets/app.js``) authenticate -- they carry
#: neither a query string we chose nor a header our JS set. Named apart from
#: ``device_server``'s and ``gcode_server``'s: cookies are scoped by host and
#: not by port, so servers reached at ``127.0.0.1`` would otherwise share one
#: jar and each would overwrite the other's.
_COOKIE = "fc_model_token"

#: Ceiling on a ``POST /api/upload`` body. The markup document is JSON text --
#: a handful of face marks and a caption -- so anything near this is not one.
_MAX_UPLOAD = 1 * 1024 * 1024

#: How many past publishes stay fetchable. More than one because a browser tab
#: left open on an earlier export should keep working when the next one is
#: published, and the files are only remembered by path.
_KEEP_PUBLISHED = 8

#: SSE keepalive interval. Also how quickly a reader notices the server
#: stopped: the write is what fails, so it doubles as the dead-connection
#: probe, and how quickly the page gets a timely ping to stay alive.
_SSE_PING = 20.0


class _Feed:
    """The published exports, the uploads that came back, and the Condition
    that wakes the SSE readers.

    One module-level instance, deliberately outliving any single ``start()``:
    ``read_model_markup`` (a later phase) is often called after the user has
    stopped the server, and an upload that made it to disk shouldn't become
    unreadable because the listener went away. It does not outlive a
    *conversation* -- see :func:`reset_session`.
    """

    def __init__(self):
        self.cond = threading.Condition()
        self.seq = 0            # bumped per publish; SSE readers track it
        self.latest = None      # the most recent published record, or None
        self.publishes = {}     # id -> record, capped at _KEEP_PUBLISHED
        self.order = []         # those ids, oldest first
        self.uploads = []       # upload records, oldest first
        self.upload_dir = None  # handed in by the GUI thread; see set_upload_dir
        self.closed = True      # no server up -> SSE readers should let go
        self.on_upload = None   # chat_panel's "a markup document arrived" hook


_feed = _Feed()

_lock = threading.Lock()
_state = {"server": None, "thread": None, "url": None, "token": None}

#: Everything the handlers are allowed to know about this machine, resolved on
#: the GUI thread and handed over by :func:`start`.
_config = {"ui_dir": None}


class _Server(http.server.ThreadingHTTPServer):
    """The listener. ``block_on_close`` is off because ``server_close()`` is
    called from the GUI thread and there is nothing worth freezing FreeCAD to
    wait for -- the handler threads are daemons with no stream to drain,
    unlike the device server's LAN readers.

    ``handle_error``: a browser closing its tab, or walking away, resets
    whatever it was holding -- and with an event stream open it is always
    holding something. The default prints a full traceback per drop, into
    FreeCAD's console, for something entirely routine. Anything that isn't a
    connection giving way is still reported.
    """

    daemon_threads = True
    block_on_close = False

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def start(ui_dir=None, upload_dir=None):
    """Start the model server (idempotent). Returns ``(url, token)``.

    ``ui_dir`` is the directory of built web assets to serve; ``None`` means
    the committed ``model_ui/`` beside this file. ``upload_dir`` is where
    POSTed markup documents land. Both are passed IN rather than worked out
    here, and that is the whole shape of the no-FreeCAD invariant in two
    arguments: resolving ``<session>/models/`` goes through a FreeCAD
    preference, which only the GUI thread may read. With no upload dir set,
    uploads are refused rather than guessed at.

    Raises ``RuntimeError`` if the resolved ``ui_dir`` is not a directory --
    in a source checkout that has never been built, the committed ``model_ui/``
    is absent.
    """
    with _lock:
        if ui_dir is not None:
            _config["ui_dir"] = ui_dir
        if _state["server"] is not None:
            return _state["url"], _state["token"]

        root = _config["ui_dir"] or UI_DIR
        if not os.path.isdir(root):
            raise RuntimeError(
                f"the model UI has not been built: {root} does not exist. "
                "A later phase of the face-markup plan populates it."
            )

        token = secrets.token_urlsafe(16)
        # Port 0 -> the OS picks a free one. Nothing needs to predict it; the
        # URL goes straight into the browser we launch.
        server = _Server(("127.0.0.1", 0), _Handler)
        server.token = token  # how _Handler reaches it (one server, one token)

        with _feed.cond:
            if upload_dir:
                _feed.upload_dir = upload_dir
            _feed.closed = False

        thread = threading.Thread(
            target=server.serve_forever,
            name="freecadclaude-model-server",
            daemon=True,
        )
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}/?t={token}"
        _state.update(server=server, thread=thread, url=url, token=token)
        return url, token


def stop():
    """Stop the server and forget its token (idempotent).

    Wired to FreeCAD's ``aboutToQuit``. Published exports and uploads are
    deliberately NOT dropped here: they belong to the conversation, not to the
    listener, and ``read_model_markup`` may be called after the user has
    stopped the server. "New" is what clears them (:func:`reset_session`).
    """
    with _lock:
        server = _state["server"]
        _state.update(server=None, thread=None, url=None, token=None)
    # Let the SSE readers go FIRST: each is parked in cond.wait() and would
    # otherwise sit there for up to _SSE_PING seconds after the socket died,
    # holding a thread per open tab.
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
    """The page URL if running, else ``None``."""
    return _state["url"]


# -- the push direction, called BY the GUI thread ---------------------------
# Everything below is the tool's half of the round trip. None of it runs on an
# HTTP thread, and none of it calls into FreeCAD either -- view_model_3d does
# the FreeCAD work first (model_export.export_brep) and hands the results over
# as a plain dict of paths, hashes and face maps.


def set_upload_dir(path):
    """Point uploads at `path` (an absolute directory). GUI thread only."""
    with _feed.cond:
        _feed.upload_dir = path


def set_upload_hook(callback):
    """Register "a markup document arrived", called with the stored path.

    Invoked ON THE HTTP WORKER THREAD, so whatever is registered has to be
    thread-safe: the chat panel passes a bound Qt signal's ``emit``, which Qt
    then queues onto the GUI thread. Nothing here may touch a widget.
    """
    with _feed.cond:
        _feed.on_upload = callback


def reset_session(upload_dir=None):
    """Forget every publish and upload of the conversation just ended.

    Called from the chat panel's "New" (a later phase wires it). The feed
    deliberately outlives a single ``start()`` (see :class:`_Feed`), which is
    right within one conversation and wrong across two: ``read_model_markup``
    would otherwise hand Claude a document the *previous* chat's user marked
    up, and ``/api/latest`` would offer the browser an export of a document
    that conversation may not even have been about.

    `upload_dir` refreshes where uploads land, because "New" mints a new
    session id and therefore a new ``<session>/models/``. Resolved by the
    caller on the GUI thread, like every other path that reaches this module.
    """
    with _feed.cond:
        if upload_dir:
            _feed.upload_dir = upload_dir
        _feed.latest = None
        _feed.publishes.clear()
        del _feed.order[:]
        del _feed.uploads[:]
        # `seq` is deliberately NOT bumped: waking the SSE readers here would
        # push them an event whose payload is "nothing published", and a
        # browser mid-drawing has no use for that. What it already has on
        # screen is the user's own work, and it keeps it.


def publish(objects_dict, upload_dir=None):
    """Make `objects_dict` the export the browser can fetch, and wake the SSE
    readers.

    `objects_dict` is exactly what ``model_export.export_brep`` returns --
    ``{"objects": {name: {"path", "shape_hash", "faces"}}}`` -- and one
    publish mints ONE id for the whole set, however many objects it carries:
    the browser loads them together, and they were exported together from one
    document state, so they must stay one unit. The id is the only handle a
    client ever gets: it is a dictionary key, never a path fragment, so
    nothing the browser sends reaches the filesystem.

    Returns the record; its ``id`` is what ``/api/mesh/<id>/<name>.brp``
    serves. Old publishes age out past ``_KEEP_PUBLISHED`` -- a pruned id 404s
    rather than raising.
    """
    objects = {
        name: dict(entry)
        for name, entry in ((objects_dict or {}).get("objects") or {}).items()
    }
    record = {
        "id": secrets.token_urlsafe(8),
        "objects": objects,
        "published_at": time.time(),
    }
    with _feed.cond:
        if upload_dir:
            _feed.upload_dir = upload_dir
        _feed.publishes[record["id"]] = record
        _feed.order.append(record["id"])
        while len(_feed.order) > _KEEP_PUBLISHED:
            _feed.publishes.pop(_feed.order.pop(0), None)
        _feed.latest = record
        _feed.seq += 1
        _feed.cond.notify_all()
    return record


def published_record(publish_id=None):
    """The published record with `publish_id` (default: the latest), or None.

    How ``read_model_markup`` replays the hash/centroid map recorded at
    PUBLISH time -- validating a mark against the document state it was drawn
    on, rather than recomputing it against a document the user has since
    moved on from.
    """
    with _feed.cond:
        if publish_id is None:
            return _feed.latest
        return _feed.publishes.get(publish_id)


def uploads():
    """Every markup document the browser has POSTed this run, oldest first (a
    copy)."""
    with _feed.cond:
        return list(_feed.uploads)


def _public_record(record):
    """The JSON view of a published export -- what the web app sees.

    The stored ``path`` never leaves the server: the browser gets a URL that
    names the publish id and the object, which is the only handle it has or
    needs. The faces map and shape hash ride along unchanged, since the whole
    point of the round trip is that the browser picks faces against the same
    ordinals and the same hash the export recorded.
    """
    if record is None:
        return None
    objects = {}
    for name, entry in record["objects"].items():
        objects[name] = {
            "url": "/api/mesh/%s/%s.brp"
                   % (record["id"], urllib.parse.quote(name, safe="")),
            "shape_hash": entry["shape_hash"],
            "faces": entry["faces"],
        }
    return {
        "id": record["id"],
        "objects": objects,
        "published_at": record["published_at"],
    }


# -- uploads ----------------------------------------------------------------


def _store_upload(folder, body):
    """Write an accepted upload into `folder`. Returns the path.

    The client's own filename is not consulted, at all: the name is generated
    with ``mkstemp`` in one fixed directory, the same mechanism (and the same
    reason) as ``freecad_tools.session._artifact_path``. A traversal attempt
    therefore has nothing to traverse -- there is no code path that joins a
    client string onto anything.
    """
    os.makedirs(folder, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="upload_", suffix=".json", dir=folder)
    with os.fdopen(fd, "wb") as fh:
        fh.write(body)
    return path


def _record_upload(path, doc, size):
    """File the stored upload and tell whoever is listening. HTTP thread."""
    record = {
        "path": path,
        "doc": doc,
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
    """Absolute path of the file `url_path` names inside the viewer directory,
    or None.

    The containment check itself is :func:`web_static.resolve`, shared with
    ``device_server`` and ``gcode_server``: one implementation, so the servers
    cannot drift into different ideas of what ``..`` means.
    """
    return web_static.resolve(_config["ui_dir"] or UI_DIR, url_path)


class _Handler(http.server.BaseHTTPRequestHandler):
    """Static files from the viewer directory plus the four ``/api/*`` routes,
    token-gated. Nothing here reads the document, and nothing here can."""

    # Keep-alive: a page load is index.html plus its script and stylesheet and
    # a several-MB WASM kernel. Every reply below sets Content-Length, which is
    # what makes this safe -- with the single exception of the event stream,
    # which has no length and so bypasses _send and closes the connection to
    # frame itself.
    protocol_version = "HTTP/1.1"
    # Don't advertise the Python version, even on loopback.
    server_version = "FreeCADClaude"
    sys_version = ""

    def do_GET(self):  # noqa: N802 (stdlib API casing)
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
        # dropped into model_ui/api/ can never shadow one of them.
        route = urllib.parse.urlsplit(self.path).path
        if route == "/api/latest":
            self._send_json(200, {"published": _public_record(_feed.latest)})
            return
        if route.startswith("/api/mesh/"):
            self._send_mesh(route[len("/api/mesh/"):])
            return
        if route == "/api/events":
            self._stream_events()
            return
        if route.startswith("/api/"):
            self._send_json(404, {"error": "no such endpoint"})
            return
        self._send_static()

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

    # -- static ----------------------------------------------------------

    def _send_static(self):
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
        # cacheable=True: files under model_ui/ are the vendored, version-pinned
        # third-party assets, NOT this server's own un-hashed build output -- so
        # the usual no-store does not apply to them (see the module docstring).
        self._send(200, body, web_static.content_type(path), cacheable=True)

    # -- /api/mesh/<id>/<object_name>.brp --------------------------------

    def _send_mesh(self, remainder):
        """Serve one published object's BREP bytes.

        ``remainder`` is ``<id>/<object_name>.brp``. The id is a dictionary
        key and the object name is matched against the names the publish
        actually carried, so nothing the client sends reaches the filesystem
        in the first place.
        """
        publish_id, _, name = remainder.partition("/")
        record = published_record(urllib.parse.unquote(publish_id))
        if record is None:
            self._send_json(404, {"error": "no such publish"})
            return
        name = urllib.parse.unquote(name)
        if name.endswith(".brp"):
            name = name[:-len(".brp")]
        entry = record["objects"].get(name)
        if entry is None:
            self._send_json(404, {"error": "no such object"})
            return
        try:
            with open(entry["path"], "rb") as fh:
                body = fh.read()
        except OSError:
            # The session folder prunes to its most recent files, so a very old
            # export can outlive its bytes. Say which it was.
            self._send_json(404, {"error": "that export is no longer on disk"})
            return
        self._send(200, body, web_static.content_type(entry["path"]))

    # -- POST /api/upload ------------------------------------------------

    def _handle_upload(self):
        """Take one markup document off the browser.

        The body is a plain JSON document -- there is no image in this round
        trip; the picture Claude eventually sees comes from a server-side
        render in a later phase, never a client screenshot. The body is parsed
        only far enough to prove it IS JSON, then stored byte for byte:
        the markup document's schema belongs to the web app and to
        ``read_model_markup``, not to this server.
        """
        try:
            length = int(self.headers.get("Content-Length"))
        except (TypeError, ValueError):
            self._reject(411, "Content-Length required")
            return
        if length < 0 or length > _MAX_UPLOAD:
            self._reject(413, f"upload too large (max {_MAX_UPLOAD // (1024 * 1024)} MB)")
            return

        folder = _feed.upload_dir
        if not folder:
            # Only reachable if the server was started without one, which the
            # tool does not do -- but guessing a folder here is exactly the
            # kind of reaching-into-FreeCAD this module refuses to do.
            self._send_json(503, {"error": "no upload folder configured"})
            return

        body = self.rfile.read(length)  # exact: keep-alive framing depends on it
        try:
            doc = body.decode("utf-8")
            json.loads(doc)  # a JSON-ness check; the schema itself is opaque
        except (UnicodeDecodeError, ValueError) as exc:
            self._send_json(400, {"error": f"that is not JSON: {exc}"})
            return

        try:
            path = _store_upload(folder, body)
        except OSError as exc:
            self._send_json(500, {"error": f"could not store the upload: {exc}"})
            return
        _record_upload(path, doc, len(body))
        # The stored NAME, not the path: the browser has no business knowing
        # where on the user's disk this landed.
        self._send_json(200, {"ok": True, "name": os.path.basename(path)})

    def _reject(self, status, message):
        """Refuse a request whose body we are NOT going to read.

        Leaving an unread body on a keep-alive connection desynchronises it --
        the next request would be parsed out of the middle of the last one --
        so anything that bails before ``rfile.read`` closes the connection
        instead.
        """
        self.close_connection = True
        self._send_json(status, {"error": message})

    # -- GET /api/events (SSE) -------------------------------------------

    def _stream_events(self):
        """One ``published`` event per publish, until the browser leaves.

        The one reply that does not go through ``_send``: an event stream has
        no length to declare, so it is framed by closing the connection (a
        legal HTTP/1.1 body delimiter) rather than by keep-alive.

        The token arrives as ``?t=`` on this route and only this route, because
        ``EventSource`` cannot set a request header -- ``_authorized`` already
        accepts the query form everywhere, so nothing special is needed here
        beyond saying why it's used. It doesn't widen the exposure: the token
        is in the page URL to begin with, and ``Referrer-Policy: no-referrer``
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
            return  # the tab closed

    # -- auth ------------------------------------------------------------

    def _authorized(self):
        """True if this request carries the current token, in any of its forms.

        Three forms because three different callers need one: the browser
        arrives with ``?t=`` in the URL the tool opened, the page's own fetches
        use ``X-FC-Token``, and the browser's sub-resource requests carry only
        the cookie we set on the way in.
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

    def _send(self, status, body, content_type, cacheable=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if not cacheable:
            # The JSON API answers carry no content hash, so a cached copy
            # would survive a rebuild and serve stale data forever. The
            # model_ui/ static files are the deliberate exception -- see the
            # module docstring.
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
        # something that should not have it.
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        """Silence. The default writes a line per request to stderr, which under
        a windowed FreeCAD goes nowhere useful and under freecadcmd buries the
        actual output."""
