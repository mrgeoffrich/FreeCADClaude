#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later
# End-to-end eval (macOS/Linux): launch FreeCAD, run a prompt through the real
# FreeCADClaude agent (auto-approving run_python), snapshot the doc to JSON.
# The macOS/Linux counterpart of eval/run.ps1.
#
# Usage:
#   ./eval/run.sh                                             # default box prompt
#   ./eval/run.sh -p "Create a cylinder r5 h30 named C"
#   ./eval/run.sh -p "..." -e '"type":\s*"Part::Cylinder"'   # PASS/FAIL regex
#   ./eval/run.sh -p "..." -t 300                            # timeout seconds
#   ./eval/run.sh -c multifeature                            # a named in-tree case
#   ./eval/run.sh -l                                         # list named cases
#
# Exit: 0 = PASS (an -e regex matched, or no -e given and the run completed),
#       1 = FAIL (-e given but didn't match), 2 = eval didn't complete.
#
# IMPORTANT -- the result JSON is only a shallow snapshot (object names, types,
# dimensions). It's enough for an -e regex like "did object X get created", but
# to judge HOW the agent behaved -- tool-call order, cut direction, whether a
# warning fired, how many steps it took -- read the run's own session folder:
#   ~/FreeCADClaude/<newest>/stream.jsonl  (tool calls + per-op volume/solid
#                                           delta and ⚠ notes in tool results)
#   ~/FreeCADClaude/<newest>/scripts/      (every approved run_python, in order)
# That is where the real signal for a behaviour/prompt change lives.
set -euo pipefail

PROMPT="Create a box exactly 20 x 20 x 20 mm. Do not ask questions."
TIMEOUT=240
EXPECT=""

# Named, in-tree eval cases so a complex multi-feature prompt is repeatable
# rather than re-typed. A case sets PROMPT (and may bump TIMEOUT); pass `-c NAME`.
# Most set no EXPECT: for a creative multi-feature build the shallow snapshot
# can't prove success, so the real signal is the session trace (see header).
# `-t`/`-p`/`-e` given AFTER `-c` still override, so keep `-c` first.
load_case() {
    case "$1" in
        box)
            PROMPT="Create a box exactly 20 x 20 x 20 mm. Do not ask questions." ;;
        multifeature)
            PROMPT="Create a 20 x 20 x 20 mm cube, then add exactly one feature per face: \
on the BOTTOM face, cut a 5 mm radius hemisphere into the cube; \
on the LEFT face, add a raised 8 x 10 mm rectangular pad standing 4 mm off the face; \
on the RIGHT face, add a complex revolved shape standing off the face; \
on the FRONT face, cut 4 small squares into it; \
on the BACK face, add a small cylinder standing off the face. \
Work through the faces one at a time and do not ask questions."
            TIMEOUT=600 ;;
        *) echo "unknown case: $1 (try: box, multifeature)" >&2; exit 2 ;;
    esac
}

while [ $# -gt 0 ]; do
    case "$1" in
        -p|--prompt)  PROMPT="$2"; shift 2 ;;
        -t|--timeout) TIMEOUT="$2"; shift 2 ;;
        -e|--expect)  EXPECT="$2"; shift 2 ;;
        -c|--case)    load_case "$2"; shift 2 ;;
        -l|--list)    echo "cases: box, multifeature"; exit 0 ;;
        -h|--help)    sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

# Find the FreeCAD GUI binary -- NOT freecadcmd. The eval needs the GUI so the
# chat panel and agent actually run. macOS: the .app's inner Mach-O entry point;
# Linux: `freecad` on PATH.
FREECAD=""
for cand in \
    "$(command -v freecad 2>/dev/null || true)" \
    /Applications/FreeCAD*.app/Contents/MacOS/FreeCAD ; do
    if [ -n "$cand" ] && [ -x "$cand" ]; then FREECAD="$cand"; break; fi
done
[ -n "$FREECAD" ] || { echo "Could not find the FreeCAD GUI binary." >&2; exit 2; }

RESULT="${TMPDIR:-/tmp}/freecadclaude_eval_result.json"
LOG="${RESULT%.json}.log"
rm -f "$RESULT"

export FREECADCLAUDE_EVAL=1
export FREECADCLAUDE_EVAL_PROMPT="$PROMPT"
export FREECADCLAUDE_EVAL_RESULT="$RESULT"
export FREECADCLAUDE_EVAL_TIMEOUT="$TIMEOUT"

echo "Launching FreeCAD eval..."
echo "  binary: $FREECAD"
echo "  prompt: $PROMPT"

# Launch the binary DIRECTLY, in the background -- never `open -a FreeCAD`: `open`
# wouldn't pass these env vars, and macOS would just re-activate an already-running
# FreeCAD instance instead of starting this eval-driven one. FreeCAD writes the
# result and quits itself; we poll for the file and only kill it (by the PID we
# launched, never a blanket pkill that could hit the user's real FreeCAD) if it
# overruns. Its console spew goes to $LOG so this output stays clean.
"$FREECAD" >"$LOG" 2>&1 &
FCPID=$!

GRACE=$((TIMEOUT + 120))
deadline=$(( $(date +%s) + GRACE ))
while [ ! -f "$RESULT" ]; do
    kill -0 "$FCPID" 2>/dev/null || break            # FreeCAD exited on its own
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "Eval overran ${GRACE}s -- killing FreeCAD (pid $FCPID)." >&2
        kill "$FCPID" 2>/dev/null || true
        break
    fi
    sleep 3
done

# Let a self-quitting FreeCAD close cleanly, then make sure the process is gone.
if [ -f "$RESULT" ]; then
    for _ in 1 2 3 4 5; do kill -0 "$FCPID" 2>/dev/null || break; sleep 1; done
    kill "$FCPID" 2>/dev/null || true
fi

if [ ! -f "$RESULT" ]; then
    echo "No result file produced (eval did not complete). FreeCAD log: $LOG" >&2
    exit 2
fi

echo "=== EVAL RESULT ==="
cat "$RESULT"
echo

# Point at the freshest session folder -- the real signal for a behaviour change.
SESSION="$(ls -dt "$HOME/FreeCADClaude"/*/ 2>/dev/null | grep -vE '/(sketches|unsaved)/$' | head -n1 || true)"
if [ -n "$SESSION" ]; then
    echo "Session trace: ${SESSION}stream.jsonl  (and ${SESSION}scripts/)"
    # The eval saves the finished model to the session root and a per-step .FCStd
    # under steps/ -- open these to inspect the geometry at each build step.
    for f in "$SESSION"*.FCStd; do
        [ -e "$f" ] && echo "Saved model:   $f"
    done
    [ -d "${SESSION}steps" ] && echo "Step models:   ${SESSION}steps/  ($(ls -1 "${SESSION}steps"/*.FCStd 2>/dev/null | wc -l | tr -d ' ') snapshots)"
fi

if [ -n "$EXPECT" ]; then
    if grep -Eq "$EXPECT" "$RESULT"; then
        echo "PASS - matched /$EXPECT/"
        exit 0
    else
        echo "FAIL - did not match /$EXPECT/" >&2
        exit 1
    fi
fi
