# SPDX-License-Identifier: LGPL-2.1-or-later
"""Serving a directory of built web assets over HTTP, for the two servers that do.

Stdlib only and stateless: ``device_server`` hands the annotation
app to a phone over the LAN, ``gcode_server`` hands the G-code viewer to the
desktop browser over loopback, and both need the same two things -- a URL path
turned into a file inside one fixed directory, and a content type for it.

It lives here rather than in one of them because :func:`resolve` is the whole of
the containment: everything either server will ever serve off disk goes through
this one function, and a second copy of it is a second place for the check to be
subtly weaker. The two servers have different threat models -- one is on the LAN
and one is not -- and neither should have to read the other to find out how its
own path handling works.

Nothing here reads a preference, a session folder or a document. The root
directory is an argument, both callers pass one they resolved themselves, and no
client string is ever joined onto anything but that root.
"""

import mimetypes
import os
import posixpath
import urllib.parse

#: Content types, spelled out rather than left to ``mimetypes``. On Windows
#: ``mimetypes`` reads the registry, where ".js" is routinely mapped to
#: "text/plain" or "application/x-javascript" by whatever installed itself
#: last -- and a script served as text/plain is refused by every browser's
#: strict MIME checking for ES modules. The fallback still covers anything a
#: later phase drops into either folder.
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
    ".wasm": "application/wasm",
}


def content_type(path):
    """The Content-Type to serve `path` as."""
    ext = posixpath.splitext(path)[1].lower()
    return TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"


def resolve(root, url_path):
    """Absolute path of the file `url_path` names inside `root`, or None.

    Not ``SimpleHTTPRequestHandler.translate_path``: that resolves
    against the *process* cwd, which for FreeCAD is wherever the user launched
    it from. This resolves against one fixed directory and then confirms, with
    ``realpath`` on both ends, that the result is still under it -- so ``..``
    segments, an absolute path, a Windows drive letter and a symlink pointing
    out of the tree are all rejected by the same check rather than by four
    separate string tests.

    A directory is not a file and so resolves to None; a request for one is a
    404 rather than a listing.
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

    root = os.path.realpath(root)
    try:
        target = os.path.realpath(os.path.join(root, path.lstrip("/")))
    except (OSError, ValueError):
        return None
    if target != root and not target.startswith(root + os.sep):
        return None
    if not os.path.isfile(target):
        return None
    return target
