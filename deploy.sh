#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later
# Deploys the ClaudeChat addon into the FreeCAD user Mod directory (macOS/Linux).
# The macOS/Linux counterpart of deploy.ps1.
#
# Usage:
#   ./deploy.sh          clean-copy the addon into the user Mod dir
#   ./deploy.sh --link   symlink it instead, so edits go live with no redeploy
#                        (just restart FreeCAD) -- the recommended dev setup
#
# Restart FreeCAD after deploying, then pick the 'Claude Chat' workbench.
set -euo pipefail

# Source = the folder this script lives in.
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODE="copy"
[ "${1:-}" = "--link" ] && MODE="link"

# Find FreeCAD's console binary so we can ask it for the *real* user data dir.
# FreeCAD 1.x uses a version-namespaced path like .../FreeCAD/v1-1/, so we must
# not hard-code the bare location.
FREECADCMD=""
for cand in \
    "$(command -v freecadcmd 2>/dev/null || true)" \
    "$(command -v FreeCADCmd 2>/dev/null || true)" \
    /Applications/FreeCAD*.app/Contents/Resources/bin/freecadcmd ; do
    if [ -n "$cand" ] && [ -x "$cand" ]; then FREECADCMD="$cand"; break; fi
done

# getUserAppDataDir() returns the versioned dir, e.g. .../FreeCAD/v1-1/.
USERDIR=""
if [ -n "$FREECADCMD" ]; then
    USERDIR="$("$FREECADCMD" -c "import FreeCAD; print(FreeCAD.getUserAppDataDir())" 2>/dev/null \
              | grep -iE 'FreeCAD' | tail -n1 | tr -d '\r')"
fi
if [ -z "$USERDIR" ] || [ ! -d "$USERDIR" ]; then
    # Fallback: newest versioned dir under the standard location, else bare.
    BASE="$HOME/Library/Application Support/FreeCAD"      # macOS
    [ -d "$BASE" ] || BASE="$HOME/.local/share/FreeCAD"   # Linux
    VERDIR="$(ls -d "$BASE"/v* 2>/dev/null | sort | tail -n1 || true)"
    USERDIR="${VERDIR:-$BASE}"
    echo "  (could not query FreeCAD; falling back to $USERDIR)"
fi

USERDIR="${USERDIR%/}"   # strip getUserAppDataDir()'s trailing slash
MODDIR="$USERDIR/Mod"
DEST="$MODDIR/ClaudeChat"
mkdir -p "$MODDIR"

echo "Deploying ClaudeChat ($MODE)"
echo "  from: $SOURCE"
echo "  to:   $DEST"

if [ "$MODE" = "link" ]; then
    # rm on a symlink removes only the link; on a real dir it removes the copy.
    # Either way the source repo is untouched.
    rm -rf "$DEST"
    ln -s "$SOURCE" "$DEST"
    echo "Done (symlinked). Restart FreeCAD, then pick the 'Claude Chat' workbench."
    exit 0
fi

# --- copy mode ---
# If DEST is a symlink (e.g. from a previous --link), remove just the link first
# so the clean-up below never recurses into and clobbers the source repo.
if [ -L "$DEST" ]; then
    echo "  (replacing existing symlink with a copy)"
    rm -f "$DEST"
fi

# Start clean so removed files don't linger, but PRESERVE vendor/ if present
# (so a code-only redeploy doesn't force a dependency re-install).
if [ -d "$DEST" ]; then
    find "$DEST" -mindepth 1 -maxdepth 1 ! -name vendor -exec rm -rf {} +
fi
mkdir -p "$DEST"

# Copy everything except build/VCS cruft, the deploy/install scripts, the
# vendor dir, the eval harness, and stray exports.
rsync -a \
    --exclude '__pycache__' \
    --exclude '.git' \
    --exclude '.gitignore' \
    --exclude '.gitattributes' \
    --exclude 'deploy.ps1' \
    --exclude 'deploy.sh' \
    --exclude 'install_deps.ps1' \
    --exclude 'vendor' \
    --exclude 'eval' \
    --exclude '*.stl' \
    "$SOURCE"/ "$DEST"/

# Drop any __pycache__ dirs that slipped through.
find "$DEST" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

echo "Done. Restart FreeCAD, then pick the 'Claude Chat' workbench."
