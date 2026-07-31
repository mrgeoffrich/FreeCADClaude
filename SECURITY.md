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

## Supported versions

Only the latest `main` is supported; there are no backported fixes.
