"""Single source of truth for the PolyFoamMesh version.

Imported by both `polyfoammesh/__init__.py` (package `__version__`)
and `polyfoammesh/gui/design_tokens.py` (UI `APP_VERSION`), so the
two can never drift apart again.

NOTE on the installer: `installer/inno_setup.iss` carries its own
`MyAppVersion` (2.3.0) which is the version of the *standalone build*,
not of the Python package — it was historically one minor ahead and is
not wired to this file. Bump it manually when producing a new installer.
"""

__version__ = "2.5.0"


def build_id() -> str:
    """Short self-report of the running code (git SHA + dirty flag).

    Screenshots and logs become self-diagnosing: a report of "pin
    ignored" is only actionable when we know WHICH code ran, and the
    frozen exe is routinely older than the source tree.  Fail-soft by
    design (never block startup: "unknown" when git is unavailable).
    """
    import os
    import subprocess

    sha = "unknown"
    dirty = ""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10, cwd=here,
        ).stdout.strip() or "unknown"
        if sha != "unknown":
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, timeout=10, cwd=here,
            ).stdout.strip()
            dirty = "-dirty" if status else ""
    except Exception:
        pass
    return f"{sha}{dirty}"
