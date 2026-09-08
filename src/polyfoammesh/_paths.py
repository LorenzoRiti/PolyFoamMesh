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
from pathlib import Path

logger = logging.getLogger(__name__)

#: Directory name under %APPDATA% where the app stores its data.
APP_DATA_DIR = "polyfoammesh"
#: Pre-rename directory name, migrated once to APP_DATA_DIR.
LEGACY_DATA_DIR = "cfmesh-autogui"


def _base_dir() -> Path:
    return Path(os.environ.get("APPDATA", os.path.expanduser("~")))


def app_data_dir() -> Path:
    """Return the app data directory ``%APPDATA%\\polyfoammesh``."""
    return _base_dir() / APP_DATA_DIR


def migrate_legacy_app_data() -> Path:
    """Copy the legacy ``%APPDATA%\\cfmesh-autogui`` dir into the new one.

    Runs once: if the new directory does not exist and the legacy one does,
    the legacy tree is copied over (logs, session snapshots, octopoda
    telemetry). Never overwrites an existing new directory. Best-effort —
    a failure is logged, never fatal.
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
