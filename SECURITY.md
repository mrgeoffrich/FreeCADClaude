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

The **Device** button starts a second, separate HTTP server that binds
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

## Supported versions

Only the latest `main` is supported; there are no backported fixes.
