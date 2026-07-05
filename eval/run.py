#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""End-to-end eval: launch FreeCAD, run a prompt through the real FreeCADClaude
agent (auto-approving run_python), snapshot the resulting document to JSON.

Cross-platform (Windows / macOS / Linux) — the single Python replacement for the
old run.ps1 + run.sh pair. Stdlib only, so no venv or `pip install` is needed;
run it with any Python 3.8+ (`python3 eval/run.py ...`).

Usage:
    python3 eval/run.py                                          # default box prompt
    python3 eval/run.py -p "Create a cylinder r5 h30 named C"
    python3 eval/run.py -p "..." -e '"type":\\s*"Part::Cylinder"' # PASS/FAIL regex
    python3 eval/run.py -p "..." -t 300                          # timeout seconds
    python3 eval/run.py -c multifeature                          # a named in-tree case
    python3 eval/run.py -l                                       # list named cases

Exit: 0 = PASS (an -e regex matched, or no -e given and the run completed),
      1 = FAIL (-e given but didn't match), 2 = eval didn't complete / bad usage.

IMPORTANT -- the result JSON is only a shallow snapshot (object names, types,
dimensions). It's enough for an -e regex like "did object X get created", but to
judge HOW the agent behaved -- tool-call order, cut direction, whether a warning
fired, how many steps it took -- read the run's own session folder:
    ~/FreeCADClaude/<newest>/stream.jsonl  (tool calls + per-op volume/solid
                                            delta and warning notes in results)
    ~/FreeCADClaude/<newest>/scripts/      (every approved run_python, in order)
That is where the real signal for a behaviour/prompt change lives; this script
prints its path on exit.
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

DEFAULT_PROMPT = "Create a box exactly 20 x 20 x 20 mm. Do not ask questions."
DEFAULT_TIMEOUT = 240

# Named, in-tree eval cases so a complex multi-feature prompt is repeatable rather
# than re-typed. A case sets `prompt` (and may bump `timeout`); an explicit -p/-t
# still overrides. Most set no expect: for a creative multi-feature build the
# shallow snapshot can't prove success -- the real signal is the session trace.
CASES = {
    "box": {
        "prompt": "Create a box exactly 20 x 20 x 20 mm. Do not ask questions.",
    },
    "multifeature": {
        "prompt": (
            "Create a 20 x 20 x 20 mm cube, then add exactly one feature per face: "
            "on the BOTTOM face, cut a 5 mm radius hemisphere into the cube; "
            "on the LEFT face, add a raised 8 x 10 mm rectangular pad standing 4 mm off the face; "
            "on the RIGHT face, add a complex revolved shape standing off the face; "
            "on the FRONT face, cut 4 small squares into it; "
            "on the BACK face, add a small cylinder standing off the face. "
            "Work through the faces one at a time and do not ask questions."
        ),
        "timeout": 600,
    },
}


def find_freecad():
    """Locate the FreeCAD *GUI* binary (NOT freecadcmd -- the eval needs the GUI
    so the chat panel and agent actually run). Returns a path or None."""
    candidates = []
    on_path = shutil.which("freecad")
    if on_path:
        candidates.append(on_path)
    if sys.platform == "win32":
        candidates += glob.glob(r"C:\Program Files\FreeCAD*\bin\freecad.exe")
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            candidates += glob.glob(os.path.join(local, "Programs", "FreeCAD*", "bin", "freecad.exe"))
    else:
        # macOS: the .app's inner Mach-O entry point (harmless no-match on Linux).
        candidates += glob.glob("/Applications/FreeCAD*.app/Contents/MacOS/FreeCAD")
    for cand in candidates:
        if cand and os.path.isfile(cand) and (sys.platform == "win32" or os.access(cand, os.X_OK)):
            return cand
    return None


def parse_args():
    p = argparse.ArgumentParser(
        prog="run.py",
        description="End-to-end FreeCADClaude eval: launch FreeCAD, run a prompt "
                    "through the real agent, snapshot the document to JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exit 0=PASS, 1=FAIL (-e didn't match), 2=eval didn't complete.",
    )
    # Defaults are None so we can tell an explicit flag from a case-supplied value.
    p.add_argument("-p", "--prompt", default=None, help="prompt to send the agent")
    p.add_argument("-t", "--timeout", type=int, default=None,
                   help="agent turn timeout in seconds (default %d)" % DEFAULT_TIMEOUT)
    p.add_argument("-e", "--expect", default=None,
                   help="PASS/FAIL regex matched against the result JSON")
    p.add_argument("-c", "--case", default=None, choices=list(CASES),
                   help="a named in-tree case (sets the prompt/timeout)")
    p.add_argument("-l", "--list", action="store_true", help="list named cases and exit")
    return p.parse_args()


def resolve_prompt_timeout(args):
    """Apply precedence: explicit -p/-t > case value > built-in default."""
    case = CASES.get(args.case) if args.case else {}
    prompt = args.prompt if args.prompt is not None else case.get("prompt", DEFAULT_PROMPT)
    if args.timeout is not None:
        timeout = args.timeout
    else:
        timeout = case.get("timeout", DEFAULT_TIMEOUT)
    return prompt, timeout


def find_session():
    """Newest per-run session folder under ~/FreeCADClaude (excluding the
    session-less `sketches`/`unsaved` dirs). Fallback when the result JSON has no
    saved_documents to derive it from."""
    root = os.path.join(os.path.expanduser("~"), "FreeCADClaude")
    try:
        entries = [os.path.join(root, d) for d in os.listdir(root)]
    except OSError:
        return None
    dirs = [d for d in entries
            if os.path.isdir(d) and os.path.basename(d) not in ("sketches", "unsaved")]
    if not dirs:
        return None
    return max(dirs, key=lambda d: os.path.getmtime(d))


def print_session_trace(report):
    """Point at the run's session folder + saved models -- the real signal for a
    behaviour change. Prefer the result JSON's saved_documents (authoritative);
    fall back to the newest session dir."""
    saved = [s for s in (report.get("saved_documents") or []) if s]
    session = os.path.dirname(saved[0]) if saved else find_session()
    if not session:
        return
    print("Session trace: %s  (and %s)"
          % (os.path.join(session, "stream.jsonl"), os.path.join(session, "scripts")))
    for m in saved or sorted(glob.glob(os.path.join(session, "*.FCStd"))):
        print("Saved model:   %s" % m)
    steps = os.path.join(session, "steps")
    if os.path.isdir(steps):
        n = len(glob.glob(os.path.join(steps, "*.FCStd")))
        print("Step models:   %s  (%d snapshots)" % (steps, n))


def main():
    args = parse_args()
    if args.list:
        print("cases: %s" % ", ".join(CASES))
        return 0

    prompt, timeout = resolve_prompt_timeout(args)

    freecad = find_freecad()
    if not freecad:
        print("Could not find the FreeCAD GUI binary.", file=sys.stderr)
        return 2

    result = os.path.join(tempfile.gettempdir(), "freecadclaude_eval_result.json")
    log = os.path.splitext(result)[0] + ".log"
    try:
        os.remove(result)
    except OSError:
        pass

    env = dict(os.environ)
    env["FREECADCLAUDE_EVAL"] = "1"
    env["FREECADCLAUDE_EVAL_PROMPT"] = prompt
    env["FREECADCLAUDE_EVAL_RESULT"] = result
    env["FREECADCLAUDE_EVAL_TIMEOUT"] = str(timeout)

    print("Launching FreeCAD eval...")
    print("  binary: %s" % freecad)
    print("  prompt: %s" % prompt)

    # Launch the binary DIRECTLY, in the background. FreeCAD writes the result and
    # quits itself; we poll for the file and only kill it if it overruns. Its
    # console spew goes to the log file so this output stays clean.
    #
    # Windows caveat (CLAUDE.md gotcha): freecad.exe is a launcher that detaches
    # and returns immediately, so the process WE spawn is not the GUI process --
    # we can't track its exit or kill it by PID. There we poll for the file only,
    # and on overrun fall back to `taskkill /IM freecad.exe`. On macOS/Linux the
    # spawned PID *is* FreeCAD, so we track its exit and kill only that PID (never
    # a blanket pkill that could hit the user's own running FreeCAD).
    windows = sys.platform == "win32"
    with open(log, "w") as logf:
        proc = subprocess.Popen([freecad], stdout=logf, stderr=subprocess.STDOUT, env=env)

    grace = timeout + 120
    deadline = time.time() + grace
    overran = False
    while not os.path.exists(result):
        if not windows and proc.poll() is not None:
            break  # FreeCAD exited on its own
        if time.time() >= deadline:
            overran = True
            print("Eval overran %ds -- killing FreeCAD." % grace, file=sys.stderr)
            if windows:
                subprocess.run(["taskkill", "/F", "/IM", "freecad.exe"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                proc.terminate()
            break
        time.sleep(3)

    # Let a self-quitting FreeCAD close cleanly, then make sure the (tracked) PID
    # is gone. On Windows there's nothing to reap -- the launcher already exited.
    if os.path.exists(result) and not windows and not overran:
        for _ in range(5):
            if proc.poll() is not None:
                break
            time.sleep(1)
        if proc.poll() is None:
            proc.terminate()

    if not os.path.exists(result):
        print("No result file produced (eval did not complete). FreeCAD log: %s"
              % log, file=sys.stderr)
        return 2

    with open(result, encoding="utf-8") as fh:
        raw = fh.read()
    print("=== EVAL RESULT ===")
    print(raw)

    try:
        report = json.loads(raw)
    except ValueError:
        report = {}
    print_session_trace(report)

    if args.expect:
        if re.search(args.expect, raw):
            print("\nPASS - matched /%s/" % args.expect)
            return 0
        print("\nFAIL - did not match /%s/" % args.expect, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
