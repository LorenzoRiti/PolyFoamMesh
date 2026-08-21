#!/usr/bin/env python3
"""Check that the app version is in sync across its three sources of truth.

The version lives in three places that must never drift apart:

    pyproject.toml                      version = "X.Y.Z"
    src/cfmesh_autogui/_version.py      __version__ = "X.Y.Z"
    installer/inno_setup.iss            #define MyAppVersion "X.Y.Z"

This script reads all three and exits non-zero if any of them disagree, so
CI (and a local run) can fail loudly instead of shipping a build whose
installer claims a different version than the package it installs.

Usage::

    python tools/check_version_sync.py

Exit codes: 0 = in sync, 1 = drift detected.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_VERSION_RE = re.compile(r'version\s*=\s*"([^"]+)"')
_DUNDER_RE = re.compile(r'__version__\s*=\s*"([^"]+)"')
_ISS_RE = re.compile(r'#define\s+MyAppVersion\s+"([^"]+)"')


def _read_version(path: Path, pattern: re.Pattern[str]) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"ERROR: cannot read {path}: {exc}")
        return None
    m = pattern.search(text)
    if m is None:
        print(f"ERROR: no version pattern found in {path}")
        return None
    return m.group(1)


def main() -> int:
    sources = {
        "pyproject.toml": (ROOT / "pyproject.toml", _VERSION_RE),
        "src/cfmesh_autogui/_version.py": (
            ROOT / "src" / "cfmesh_autogui" / "_version.py", _DUNDER_RE,
        ),
        "installer/inno_setup.iss": (
            ROOT / "installer" / "inno_setup.iss", _ISS_RE,
        ),
    }

    versions: dict[str, str] = {}
    ok = True
    for label, (path, pattern) in sources.items():
        v = _read_version(path, pattern)
        if v is None:
            ok = False
            continue
        versions[label] = v
        print(f"{label:<38} {v}")

    if not versions:
        print("ERROR: could not read any version source.")
        return 1

    first = next(iter(versions.values()))
    for label, v in versions.items():
        if v != first:
            ok = False
            print(f"DRIFT: {label} is {v}, expected {first}")

    if ok:
        print(f"OK: all sources agree on version {first}")
        return 0
    print("VERSION DRIFT DETECTED — align the sources above before releasing.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
