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
including a confirmation-gated `run_python` tool that executes Python, and a
`Write` tool that can create or overwrite files on disk. Treat prompts, and any
files Claude is asked to open, as untrusted input. The most valuable reports
involve the addon doing something destructive or executing code **without** the
expected confirmation step.

## Supported versions

Only the latest `main` is supported; there are no backported fixes.
