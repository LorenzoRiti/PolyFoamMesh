"""Single source of truth for on-disk application data paths.

Renamed in v2.1.0 from ``cfmesh-autogui`` to ``polyfoammesh`` (the package
was renamed; the on-disk data directory followed). One-time best-effort
migration from the legacy directory is performed by
:func:`migrate_legacy_app_data`, which ``app.py`` runs at startup before
logging is initialised.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

#: Directory name under the platform data root where the app stores its data.
APP_DATA_DIR = "polyfoammesh"
#: Pre-rename directory name, migrated once to APP_DATA_DIR.
LEGACY_DATA_DIR = "cfmesh-autogui"


def _base_dir() -> Path:
    """Platform data root.

    Windows: ``%APPDATA%`` (unchanged). POSIX: ``$XDG_DATA_HOME`` per the
    XDG Base Directory spec, falling back to ``~/.local/share``. Stdlib only
    — deliberately not ``QStandardPaths``, which would drag Qt into a
    non-GUI module.
    """
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", os.path.expanduser("~")))
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if not xdg_data_home:
        xdg_data_home = os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(xdg_data_home)


def app_data_dir() -> Path:
    """Return the app data directory.

    Windows: ``%APPDATA%\\polyfoammesh`` (unchanged).
    POSIX: ``$XDG_DATA_HOME/polyfoammesh`` (default
    ``~/.local/share/polyfoammesh``). Logs stay in a ``logs/`` subdirectory
    of this same root (``app.py`` joins it there), so no separate
    ``XDG_STATE_HOME`` split is introduced.
    """
    return _base_dir() / APP_DATA_DIR


def migrate_legacy_app_data() -> Path:
    """Copy the legacy data dir into the new one.

    Runs once: if the new directory does not exist and the legacy one does,
    the legacy tree is copied over (logs, session snapshots, octopoda
    telemetry). Never overwrites an existing new directory. Best-effort —
    a failure is logged, never fatal.

    The legacy directory is resolved from the same platform root as
    :func:`app_data_dir` (``%APPDATA%`` on Windows, ``$XDG_DATA_HOME`` on
    POSIX), so the Windows migration path is byte-identical to before.
    """
    new = app_data_dir()
    if new.exists():
        return new
    legacy = _base_dir() / LEGACY_DATA_DIR
    if not legacy.is_dir():
        new.mkdir(parents=True, exist_ok=True)
        return new
    try:
        shutil.copytree(legacy, new)
        logger.info("Migrated app data: %s -> %s", legacy, new)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("App data migration from %s failed: %s", legacy, exc)
        new.mkdir(parents=True, exist_ok=True)
    return new
