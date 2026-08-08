# Security Policy

FreeCADClaude is an unofficial, personal open-source project maintained on a
best-effort basis. It is not affiliated with, endorsed by, or sponsored by
Anthropic.

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Use GitHub's private vulnerability reporting instead —
[**Report a vulnerability**](https://github.com/mrgeoffrich/FreeCADClaude/security/advisories/new)
— which opens a private advisory visible only to the maintainer.

As a personal project there are no formal SLAs, but I'll aim to acknowledge
genuine reports within a week or two and prioritise them.

## Scope

This addon runs Claude with tools that can act on your FreeCAD document —
including a `run_python` tool that executes Python inside the FreeCAD process,
and a `Write` tool that can create or overwrite files on disk. Treat prompts, and
any files Claude is asked to open, as untrusted input.

**There is no confirmation prompt.** Tool calls run as they arrive, by design, so
"the addon executed code without asking me" is expected behaviour rather than a
vulnerability. What remains worth reporting: anything that lets a third party
reach these tools (the bridge listens on localhost behind a shared-secret token —
a way past that token, or to bind it more widely, is a real finding), prompt
injection from file content that Claude reads, or the addon leaking credentials
or session data.

## The device annotation server

The **Connect Mobile** button starts a second, separate HTTP server that binds
`0.0.0.0` — your local network, not just localhost — so a phone or tablet can
load the annotation page. It is the only part of the addon that does this, and
the threat model is *someone else on your wifi*.

**Known and accepted, so not a finding:**

- **Plain HTTP: the token crosses the LAN in clear.** There is no TLS. Anyone
  who can observe the traffic can replay the token for as long as the server is
  up. This is a deliberate trade for a personal-use addon on a home network —
  a certificate a tablet will accept, for an ephemeral port on a rotating LAN
  address, is not something this can reasonably ship. Don't enable it on a
  network you don't trust.
- The token is in the page URL (that is what the QR encodes), so it is also in
  the device's browser history until the page rewrites it away on load.
- Uploaded images are written to disk in the session folder without being
  decoded or scanned.

**Worth reporting.** The server's central invariant is that **it never calls
into FreeCAD**: `device_server.py` imports neither FreeCAD nor Qt, no request
handler reaches the document, and captures are pushed to it by a tool running on
the GUI thread. The intended ceiling on a token-holder is therefore "read the
captures that have been pushed, and write image files into one folder" — a long
way from `run_python`. Anything that raises that ceiling is a genuine finding:

- a request path that reaches FreeCAD, the document, or the `gui_bridge` token;
- a way past the token gate (the three accepted forms are `?t=`, the
  `X-FC-Token` header, and the cookie set on an authenticated page load);
- a way to read or write outside the served UI directory / the upload folder —
  the static tree is realpath-contained and uploads are given generated names,
  so a client-supplied path or filename should have nothing to steer;
- the server starting, or staying up, without the user pressing the button (it
  is off by default and stops itself when idle).

## The slicer, and the toolpath viewer's loopback server

Two things here reach outside FreeCAD, and they are worth separating because
only one of them is new exposure.

**Launching the slicer is not new reach.** `slice_model` starts Bambu Studio as
a background subprocess with a command line the addon built. `run_python`
already executes arbitrary Python inside the FreeCAD process, so anything that
can ask for a slice can already start any process it likes — "the addon ran an
external program" is therefore expected behaviour rather than a vulnerability.
The addon refuses to build a command line for a binary it does not recognise,
but that is to avoid opening a modal dialog on your desktop with no error text,
not a security boundary.

**The viewer's HTTP server is new surface, and it is the smallest kind.**
`view_gcode` and the chat panel's **Slicer** button start a listener bound to
`127.0.0.1` on an OS-assigned port, serving the built viewer plus the G-code
that has been published to it and the slicer settings file. Nothing off this
machine can connect. It still carries a fresh per-start token — in the page URL,
in an `X-FC-Token` header, or in the cookie set on an authenticated page load —
because a loopback listener is reachable by every other process on the machine.
It stops when FreeCAD quits, and a restart mints a new token.

**Known and accepted, so not a finding:** the token is in the page URL and so in
the browser's history; there is no TLS, which on loopback is a different
proposition from the LAN server above; and there is no idle timeout, since it is
started by a tool rather than by an exposure decision.

**Worth reporting.** `gcode_server.py` has the same central invariant as
`device_server.py`: **it never calls into FreeCAD**, importing neither FreeCAD
nor Qt, with every path handed in by a tool on the GUI thread. So:

- a request path that reaches FreeCAD, the document, or the `gui_bridge` token;
- a way past the token gate, or a way to bind it to anything but `127.0.0.1`;
- a way to read or write outside the served viewer directory and the settings
  file — the static tree is realpath-contained by `web_static.resolve` and a
  published G-code is fetched by an id we minted, never by a client path;
- a `PUT /api/slicer/config` that stores a value the validator should refuse, or
  that reaches a file other than `~/FreeCADClaude/slicer.json`.

## Supported versions

Only the latest `main` is supported; there are no backported fixes.
