# SPDX-License-Identifier: LGPL-2.1-or-later
"""Loopback HTTP server: the G-code viewer, and the slicer settings it configures.

Stdlib only, like ``device_server.py``: no Qt, no FreeCAD, not even indirectly.
The reason is the same one -- **no handler may call into FreeCAD**. A G-code
file is *pushed* here by a tool running on the GUI thread
(``tools_slice.view_gcode``); nothing arriving over the socket can cause a
FreeCAD call, a recompute or a document mutation. Keeping FreeCAD out of the
import list is the cheapest way to keep that true by construction, and it is
what lets the whole module be tested under a plain interpreter (see
``eval/test_gcode_server.py``).

    view_gcode (GUI thread) --start()--> ThreadingHTTPServer (daemon thread)
                            --publish()->   serves gcode_ui/ on 127.0.0.1
                                            GET /api/gcode/<id>
                                            GET /api/slicer/options
                                            GET/PUT /api/slicer/config

Unlike :mod:`device_server`, which binds ``0.0.0.0`` because a phone has to
reach it, this binds ``127.0.0.1``: the viewer is a desktop browser on this
machine, so nothing off the machine ever needs to connect. That is why a tool
may start it without making a security decision on the user's behalf, and why
there is no button, no QR and no idle watchdog -- it stops with FreeCAD.

The token stays anyway, exactly as :mod:`gui_bridge` keeps one on loopback: a
listener on 127.0.0.1 is reachable by every other process on the machine, and
this one serves files by id and rewrites the user's printer configuration.

Two things are passed IN from the GUI thread rather than looked up here, and
that is the whole shape of the no-FreeCAD invariant: the viewer directory (the
``GcodeUiDir`` preference overrides the committed ``gcode_ui/``) and every path
the slicer routes read -- ``slicer.json``, the slicer's own config file and the
profile roots. Each of those walks ends in a FreeCAD preference read. A handler
that "just looks one up" crosses the line.

``PUT /api/slicer/config`` validates every preset name against the discovered
lists before it writes. The alternative is a stale page storing a name that
resolves to nothing, which surfaces minutes later as a failed slice with the
argv as the only clue.
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

from . import slicer_runner, web_static

#: The built viewer (``gcode_web/`` → Vite → here). Committed to git: users
#: install from the ``main`` branch as a plain file copy, with no Node toolchain
#: to build it with. See gcode_web/vite.config.ts.
UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gcode_ui")

#: Cookie the browser gets after a successful ``?t=`` page load, so the browser's
#: own sub-resource requests (``/assets/app.js``) authenticate -- they carry
#: neither a query string we chose nor a header our JS set. Named apart from
#: ``device_server``'s: cookies are scoped by host and not by port, so both
#: servers reached at ``127.0.0.1`` would otherwise share one jar and each
#: would overwrite the other's.
_COOKIE = "fc_gcode_token"

#: Ceiling on a ``PUT /api/slicer/config`` body. The settings document is eight
#: short fields; anything near this is not one.
_MAX_CONFIG = 64 * 1024

#: How many published G-code files stay fetchable. More than one because a
#: browser tab left open on an earlier slice should keep working when the next
#: one is published, and the files are only remembered by path.
_KEEP_PUBLISHED = 8

#: What ``slicer.json`` may contain. An unknown key is refused rather than
#: stored: this file is read by ``slicer_runner.read_settings`` at slice time,
#: and a key nothing reads is a setting the user believes they have made.
_SETTINGS_KEYS = ("printer", "nozzle", "machine", "process", "filament",
                  "orient", "arrange", "deviation")

#: Bounds on the stored mesh deviation, in mm. Zero or negative is not a
#: tessellation setting, and past about 0.05 mm coarsening has no effect at all,
#: so the upper bound is only there to catch a units mistake.
_MIN_DEVIATION = 0.0005
_MAX_DEVIATION = 5.0

_lock = threading.Lock()
_state = {"server": None, "thread": None, "url": None, "token": None}

#: Everything the handlers are allowed to know about this machine, all of it
#: resolved on the GUI thread and handed over by :func:`start`.
_config = {"ui_dir": None, "settings_path": None, "conf_path": None,
           "profile_dirs": ()}

#: Published G-code, by an id we minted. A path is never taken from a client.
_files = {}
_files_order = []


class _Server(http.server.ThreadingHTTPServer):
    """The listener. ``block_on_close`` is off because ``server_close()`` is
    called from the GUI thread and there is nothing worth freezing FreeCAD to
    wait for -- the handler threads are daemons with no stream to drain, unlike
    the device server's SSE readers."""

    daemon_threads = True
    block_on_close = False

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def start(ui_dir=None, settings_path=None, conf_path=None, profile_dirs=None):
    """Start the viewer server (idempotent). Returns ``(url, token)``.

    Every argument is a path the caller resolved on the GUI thread, and they are
    re-applied on a call that finds the server already up -- a second
    ``view_gcode`` in a later conversation may have a different settings path
    or a newly-installed slicer, and the running server has no way to notice.

    Raises ``RuntimeError`` if there is no viewer to serve, which is only
    reachable in a source checkout that has never been built: the folder is
    committed, so an installed addon always has it.
    """
    with _lock:
        if ui_dir:
            _config["ui_dir"] = ui_dir
        if settings_path:
            _config["settings_path"] = settings_path
        _config["conf_path"] = conf_path or _config["conf_path"]
        if profile_dirs is not None:
            _config["profile_dirs"] = tuple(profile_dirs)

        if _state["server"] is not None:
            return _state["url"], _state["token"]

        root = _config["ui_dir"] or UI_DIR
        if not os.path.isdir(root):
            raise RuntimeError(
                f"the G-code viewer has not been built: {root} does not exist. "
                "Run 'npm ci && npm run build' in gcode_web/."
            )

        token = secrets.token_urlsafe(16)
        # Port 0 -> the OS picks a free one. Nothing needs to predict it; the
        # URL goes straight into the browser we launch.
        server = _Server(("127.0.0.1", 0), _Handler)
        server.token = token  # how _Handler reaches it (one server, one token)
        thread = threading.Thread(target=server.serve_forever,
                                  name="freecadclaude-gcode-server", daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}/?t={token}"
        _state.update(server=server, thread=thread, url=url, token=token)
        return url, token


def stop():
    """Stop the server and forget its token (idempotent).

    Wired to FreeCAD's ``aboutToQuit``. Published files are not
    dropped: a browser tab is not ours to close, and the next ``start()`` mints
    a fresh token, so an old tab is deauthorised by the restart anyway.
    """
    with _lock:
        server = _state["server"]
        _state.update(server=None, thread=None, url=None, token=None)
    if server is None:
        return
    # shutdown() blocks until serve_forever's loop exits and must not be called
    # from the serving thread; callers are the GUI thread, so it never is.
    server.shutdown()
    server.server_close()


def is_running():
    """Whether the server is currently listening."""
    return _state["server"] is not None


def current_url():
    """The page URL if running, else ``None``."""
    return _state["url"]


def page_url(file_id=None):
    """The URL to open in the browser, optionally loading a published file.

    ``?gcode=<id>`` is what the page reads on mount; the token rides along as
    ``?t=`` so the page load authenticates and sets the cookie its own assets
    and fetches then travel on.
    """
    url = _state["url"]
    if not url or not file_id:
        return url
    return url + "&gcode=" + urllib.parse.quote(str(file_id), safe="")


# -- the push direction, called BY the GUI thread ---------------------------


def publish(path, meta=None):
    """Make `path` fetchable under a minted id, and return the record.

    The id is the only handle a client ever gets: it is a dictionary key, never
    a path fragment, so nothing the browser sends reaches the filesystem.
    """
    record = {"id": secrets.token_urlsafe(8), "path": path,
              "meta": dict(meta or {}), "published_at": time.time()}
    with _lock:
        _files[record["id"]] = record
        _files_order.append(record["id"])
        while len(_files_order) > _KEEP_PUBLISHED:
            _files.pop(_files_order.pop(0), None)
    return record


def published_record(file_id=None):
    """The published record with `file_id` (default: the most recent), or None."""
    with _lock:
        if file_id is None:
            return _files.get(_files_order[-1]) if _files_order else None
        return _files.get(file_id)


# -- the slicer settings file -----------------------------------------------


def _options(machine=None):
    """The discovered printers and the presets one machine can use.

    A thin wrapper over :func:`slicer_runner.discover_options`, with each
    preset's absolute path dropped: the page picks by name, the name is what
    gets stored, and a path in the payload is only a filesystem layout handed to
    a browser for nothing.
    """
    found = slicer_runner.discover_options(
        _config["conf_path"], _config["profile_dirs"], machine=machine)
    for kind in ("processes", "filaments"):
        found[kind] = [{"name": entry["name"], "default": entry["default"]}
                       for entry in found[kind]]
    found["nozzle_default"] = slicer_runner.DEFAULT_NOZZLE
    return found


def _bool_setting(key, value):
    if isinstance(value, bool):
        return value, None
    return None, f"'{key}' is a true/false setting, not {value!r}."


def _validate(payload):
    """``(settings, error)`` for a proposed ``slicer.json``; one of them is None.

    Every preset name is checked against what is actually installed, and a
    printer, nozzle and machine given together have to agree with each other.
    Storing a name nothing answers to is the failure this exists to prevent: it
    resolves to nothing at slice time, minutes later, with the argv as the only
    clue -- and ``resolve_presets`` refuses rather than quietly
    slicing with something else.
    """
    if not isinstance(payload, dict):
        return None, "The settings must be a JSON object."
    unknown = sorted(key for key in payload if key not in _SETTINGS_KEYS)
    if unknown:
        return None, ("Unknown setting(s): %s. Known settings are %s."
                      % (", ".join(unknown), ", ".join(_SETTINGS_KEYS)))

    settings = {}
    for key in ("orient", "arrange"):
        if key in payload:
            value, error = _bool_setting(key, payload[key])
            if error:
                return None, error
            settings[key] = value
    if "deviation" in payload:
        value = payload["deviation"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not _MIN_DEVIATION <= float(value) <= _MAX_DEVIATION:
            return None, ("'deviation' is a mesh deviation in mm, between %g and "
                          "%g." % (_MIN_DEVIATION, _MAX_DEVIATION))
        settings["deviation"] = float(value)

    names = {}
    for key in ("printer", "nozzle", "machine", "process", "filament"):
        if key not in payload:
            continue
        value = payload[key]
        if not isinstance(value, str) or not value.strip():
            return None, f"'{key}' must be a preset name, not {value!r}."
        names[key] = value.strip()

    error = _validate_names(names)
    if error:
        return None, error
    settings.update(names)
    return settings, None


def _validate_names(names):
    """Why `names` cannot be stored, or None.

    The lists are re-discovered per PUT rather than cached: the page may have
    been open since before the user added a printer in the slicer, and a cache
    would make a name that is now valid look stale (and, worse, the reverse).
    """
    if not names:
        return None
    options = _options(names.get("machine"))
    printers = {entry["model"]: entry for entry in options["printers"]}
    machines = {name for entry in options["printers"]
                for name in entry["machines"].values()}

    printer = names.get("printer")
    if printer is not None and printer not in printers:
        return _unknown("printer", printer, sorted(printers))
    nozzle = names.get("nozzle")
    if nozzle is not None:
        offered = printers[printer]["nozzles"] if printer in printers else \
            sorted({size for entry in printers.values() for size in entry["nozzles"]})
        if nozzle not in offered:
            return _unknown("nozzle", nozzle, offered)
    machine = names.get("machine")
    if machine is not None and machine not in machines:
        return _unknown("machine preset", machine, sorted(machines))
    if machine is not None and printer in printers and nozzle is not None:
        declared = printers[printer]["machines"].get(nozzle)
        if declared != machine:
            return ("'%s' is not the machine preset for a %s mm %s -- that is "
                    "'%s'. Store the three together or not at all."
                    % (machine, nozzle, printer, declared or "(none installed)"))
    for kind, key in (("processes", "process"), ("filaments", "filament")):
        chosen = names.get(key)
        if chosen is None:
            continue
        offered = [entry["name"] for entry in options[kind]]
        if chosen not in offered:
            return _unknown("%s preset" % key, chosen, offered)
    return None


def _unknown(kind, value, offered):
    """The refusal for a name nothing installed answers to, with what does."""
    if not offered:
        return ("No %s is installed that '%s' could name, so it was not stored. "
                "Nothing was found to choose from at all -- check that the "
                "slicer is installed and its profile folders are readable."
                % (kind, value))
    shown = offered[:20]
    more = "" if len(shown) == len(offered) else " (+%d more)" % (len(offered) - len(shown))
    return ("No %s called '%s' is installed, so it was not stored. Installed: %s%s."
            % (kind, value, ", ".join(shown), more))


def _read_settings():
    """The stored ``slicer.json``, or ``{}``. Never raises: the file is small,
    ours, and may not exist yet."""
    return slicer_runner.read_settings(_config["settings_path"])


def _write_settings(settings):
    """Replace ``slicer.json`` with `settings`. Returns an error, or None.

    Written to a temporary file in the same folder and moved into place, because
    ``slicer_runner.read_settings`` may be reading it from a slice on another
    thread at the same moment -- ``os.replace`` is atomic, a truncate-and-write
    is a window in which the file is half a document.
    """
    path = _config["settings_path"]
    if not path:
        # Only reachable if the server was started without one, which the tool
        # does not do -- but working a path out here is exactly the reaching
        # into FreeCAD this module refuses.
        return "no settings file is configured"
    body = json.dumps(settings, indent=2, sort_keys=True) + "\n"
    folder = os.path.dirname(path) or "."
    try:
        os.makedirs(folder, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".slicer-", suffix=".json", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.replace(temporary, path)
        except OSError:
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise
    except OSError as exc:
        return str(exc)
    return None


# -- the handler ------------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    """Static files from the viewer directory plus the three ``/api/*`` routes,
    token-gated. Nothing here reads the document, and nothing here can."""

    # Keep-alive: a page load is index.html plus a 1.1 MB script, a stylesheet
    # and three workers. Every reply below sets Content-Length, which is what
    # makes it safe.
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
        # dropped into gcode_ui/api/ can never shadow one of them.
        route = urllib.parse.urlsplit(self.path).path
        if route.startswith("/api/gcode/"):
            self._send_published(route[len("/api/gcode/"):])
            return
        if route == "/api/slicer/options":
            self._send_options()
            return
        if route == "/api/slicer/config":
            self._send_json(200, {"settings": _read_settings(),
                                  "path": _config["settings_path"]})
            return
        if route.startswith("/api/"):
            self._send_json(404, {"error": "no such endpoint"})
            return
        self._send_static()

    def do_HEAD(self):  # noqa: N802
        # Same policy as GET; _send suppresses the body itself.
        self.do_GET()

    def do_PUT(self):  # noqa: N802
        self._set_cookie = False
        if not self._authorized():
            self._send(403, b"forbidden", "text/plain; charset=utf-8")
            return
        if urllib.parse.urlsplit(self.path).path != "/api/slicer/config":
            self._reject(404, "no such endpoint")
            return
        self._store_settings()

    # -- static ----------------------------------------------------------

    def _send_static(self):
        path = web_static.resolve(_config["ui_dir"] or UI_DIR, self.path)
        if path is None:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        self._send(200, body, web_static.content_type(path))

    # -- /api/gcode/<id> -------------------------------------------------

    def _send_published(self, file_id):
        """Serve a published G-code file by the id publish() minted.

        The id is a dictionary key, never a path fragment: nothing the client
        sends reaches the filesystem, so there is no containment check to get
        wrong here.
        """
        record = published_record(urllib.parse.unquote(file_id))
        if record is None:
            self._send_json(404, {"error": "no such file"})
            return
        try:
            with open(record["path"], "rb") as fh:
                body = fh.read()
        except OSError:
            # A job folder can be pruned out from under a tab left open.
            self._send_json(404, {"error": "that G-code is no longer on disk"})
            return
        self._send(200, body, "application/octet-stream", extra={
            # The viewer branches on the file name (.gcode vs .gcode.3mf vs a
            # gzipped overlay), and an id carries none, so it travels here.
            "Content-Disposition":
                'inline; filename="%s"' % os.path.basename(record["path"]),
        })

    # -- /api/slicer/* ---------------------------------------------------

    def _send_options(self):
        """The printers, nozzles and machine-filtered preset lists.

        Reading every process and filament preset is a few thousand small file
        reads on a full vendor tree, which is why it sits behind a request the
        user made by opening the panel rather than inside a slice.
        """
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        machine = (query.get("machine") or [""])[0].strip() or None
        try:
            self._send_json(200, _options(machine))
        except OSError as exc:
            self._send_json(500, {"error": "the profile folders could not be "
                                           "read: %s" % exc})

    def _store_settings(self):
        """Replace ``slicer.json`` with the body, if every name in it is real."""
        try:
            length = int(self.headers.get("Content-Length"))
        except (TypeError, ValueError):
            self._reject(411, "Content-Length required")
            return
        if length < 0 or length > _MAX_CONFIG:
            self._reject(413, "settings document too large")
            return
        body = self.rfile.read(length)  # exact: keep-alive framing depends on it
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            self._send_json(400, {"error": "that is not JSON: %s" % exc})
            return
        settings, error = _validate(payload)
        if error:
            self._send_json(400, {"error": error})
            return
        failure = _write_settings(settings)
        if failure:
            self._send_json(500, {"error": "could not write the settings: %s"
                                           % failure})
            return
        self._send_json(200, {"ok": True, "settings": settings,
                              "path": _config["settings_path"]})

    def _reject(self, status, message):
        """Refuse a request whose body we are NOT going to read.

        Leaving an unread body on a keep-alive connection desynchronises it, so
        anything that bails before ``rfile.read`` closes the connection instead.
        """
        self.close_connection = True
        self._send_json(status, {"error": message})

    # -- auth ------------------------------------------------------------

    def _authorized(self):
        """True if this request carries the current token, in any of its forms.

        Three forms because three callers need one: the browser arrives with
        ``?t=`` in the URL the tool opened, the page's own fetches may set
        ``X-FC-Token``, and the browser's sub-resource requests carry only the
        cookie set on the way in.
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

    def _send(self, status, body, content_type, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # The built asset names are fixed (no content hashes -- see
        # gcode_web/vite.config.ts), so a cached copy would survive a rebuild
        # and serve stale JS for ever.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # The token is in the page URL; don't let it ride out on a Referer.
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        if getattr(self, "_set_cookie", False):
            self.send_header(
                "Set-Cookie",
                f"{_COOKIE}={self.server.token}; Path=/; HttpOnly; SameSite=Strict",
            )
        # No CORS headers, at all: the page is same-origin with everything it
        # talks to, so any CORS header here would only grant access to something
        # that should not have it.
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        """Silence. The default writes a line per request to stderr, which under
        a windowed FreeCAD goes nowhere useful and under freecadcmd buries the
        actual output."""
