#!/usr/bin/env bash
# PolyFoamMesh launcher (Linux/macOS).
#
# Usage: ./launch.sh   (or: bash launch.sh)
#
# Equivalent in spirit to launch.ps1: it probes each candidate interpreter for
# the app's GUI dependencies (PySide6 + pyvista) and uses the first one that
# passes, instead of blindly trusting the first `python` on PATH (which is
# often an unrelated venv and dies with an ImportError).
#
# Nothing Windows-specific is set here. OpenFOAM is only needed for the
# Cartesian cfMesh / checkMesh paths; install it and source its bashrc (or set
# POLYFOAMMESH_OF_BASHRC) separately. See docs/INSTALL.md.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_PATH="$SCRIPT_DIR/src/polyfoammesh/app.py"

probe() {
    # $1 = interpreter path/name; succeeds if PySide6 + pyvista import.
    local exe="$1"
    [ -n "$exe" ] || return 1
    command -v "$exe" >/dev/null 2>&1 || [ -x "$exe" ] || return 1
    "$exe" -c "import PySide6, pyvista" >/dev/null 2>&1
}

PY=""
for candidate in \
    "${POLYFOAMMESH_PYTHON:-}" \
    python3 \
    python
do
    if probe "$candidate"; then
        PY="$candidate"
        break
    fi
done

if [ -z "$PY" ]; then
    echo ""
    echo "PolyFoamMesh: no Python with the app's dependencies (PySide6 + pyvista) found." >&2
    echo "Tried: \${POLYFOAMMESH_PYTHON:-}, python3, python" >&2
    echo ""
    echo "Install them, for example:" >&2
    echo "  python3 -m venv .venv && . .venv/bin/activate" >&2
    echo "  pip install -e ." >&2
    echo "or point POLYFOAMMESH_PYTHON at the right interpreter." >&2
    exit 1
fi

echo "Starting PolyFoamMesh (Python: $PY)..."
exec "$PY" "$APP_PATH" "$@"
