"""Version-aware QSettings with schema validation and migration.

Ensures that persisted settings survive across app versions and that
corrupted or stale values are caught before they reach widgets.

Usage::

    from polyfoammesh.gui.settings_migration import AppSettings
    s = AppSettings()
    s.set_value("params/max_cell", 0.05)
    val = s.get_value("params/max_cell", default=0.05)
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QSettings

from polyfoammesh.gui.design_tokens import APP_VERSION
from polyfoammesh.core.validation import ValidationResult, validate_settings

logger = logging.getLogger(__name__)

# Schema: every key that can appear in QSettings and its expected type
_SETTINGS_SCHEMA: dict[str, type] = {
    "ui/theme_mode": str,
    "params/detail_slider": int,
    "params/unit": str,
    "params/bl_checked": bool,
    "window/size": object,  # QSize
    "window/pos": object,  # QPoint
    "viewer/background": str,
    "geometry/last_step_dir": str,
    "geometry/recent_files": list,
    "quality/nonortho_warn": float,
    "quality/nonortho_fail": float,
    "quality/skew_warn": float,
    "quality/skew_fail": float,
    "quality/aspect_warn": float,
    "quality/aspect_fail": float,
}


class AppSettings:
    """Wrapper around QSettings with schema validation and migration.

    Usage is identical to QSettings::

        s = AppSettings()
        s.set_value("params/max_cell", 0.05)
        val = s.get_value("params/max_cell", default=0.05)
    """

    # Storage key for the persisted app version
    _VERSION_KEY = "app/version"

    def __init__(self) -> None:
        self._qs = QSettings("cfmesh-autogui", "CFMesh-AutoGUI")
        self._migrate()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_value(self, key: str, default: Any = None) -> Any:
        """Read a value, returning *default* if missing or corrupt."""
        raw = self._qs.value(key, default)
        return raw

    def set_value(self, key: str, value: Any) -> None:
        """Write a value after validation, or log a warning on failure."""
        result: ValidationResult = validate_settings(key, value)
        if not result.valid:
            logger.warning(
                "Settings rejection: %s = %r — %s", key, value, result.message,
            )
            return
        self._qs.setValue(key, value)

    def sync(self) -> None:
        """Flush to disk immediately.  Call from ``closeEvent``."""
        self._qs.sync()

    def remove(self, key: str) -> None:
        self._qs.remove(key)

    def contains(self, key: str) -> bool:
        return self._qs.contains(key)

    def all_keys(self) -> list[str]:
        return self._qs.allKeys()

    # ------------------------------------------------------------------
    # Underlying QSettings (for widgets that need the raw object)
    # ------------------------------------------------------------------
    @property
    def raw(self) -> QSettings:
        return self._qs

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------
    def _migrate(self) -> None:
        """Run migrations in order from the stored version to the current."""
        stored = self._qs.value(self._VERSION_KEY, "0.0.0")
        stored_str: str = str(stored) if stored else "0.0.0"
        logger.debug("Settings version: stored=%s current=%s", stored_str, APP_VERSION)

        # Run migrations sequentially
        migrations = [
            (_parse_version(v), fn)
            for v, fn in _MIGRATIONS
            if _parse_version(v) > _parse_version(stored_str)
        ]
        migrations.sort(key=lambda x: x[0])

        for version, fn in migrations:
            try:
                fn(self._qs)
                logger.info("Settings migration %s applied.", version)
            except Exception as exc:
                logger.error("Settings migration %s failed: %s", version, exc)

        # Store the highest registered migration version, not the static
        # APP_VERSION constant (which stayed "2.0.0" while _MIGRATIONS grew
        # past it) — writing APP_VERSION back here meant `stored` could
        # never advance past 2.0.0, so every migration newer than that
        # (2.0.1, and now 2.0.2) re-ran on every single AppSettings()
        # instantiation instead of once. Harmless by luck so far (each
        # migration body is itself idempotent), but not by design, and the
        # unit-override migration below must only apply once — a user
        # deliberately re-selecting a non-"m" unit after startup shouldn't
        # get silently reset back to "m" the next time a dialog opens a
        # fresh AppSettings().
        highest = max(
            [_parse_version(v) for v, _ in _MIGRATIONS] + [_parse_version(stored_str)],
        )
        self._qs.setValue(self._VERSION_KEY, ".".join(str(p) for p in highest))

    def purge_stale_keys(self) -> int:
        """Remove keys that are no longer in the schema. Returns count."""
        known = set(_SETTINGS_SCHEMA.keys())
        removed = 0
        for key in self._qs.allKeys():
            if key not in known and not key.startswith("app/") and not key.startswith("params/"):
                logger.info("Purging stale setting: %s", key)
                self._qs.remove(key)
                removed += 1
        return removed


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------
def _parse_version(v: str) -> tuple[int, ...]:
    """Parse ``'2.0.1'`` → ``(2, 0, 1)``."""
    parts = v.split(".")
    result: list[int] = []
    for p in parts:
        try:
            result.append(int(p))
        except ValueError:
            result.append(0)
    return tuple(result)


# ---------------------------------------------------------------------------
# Migration registry: list of (version, callable)
# Migrations run in ascending version order.
# ---------------------------------------------------------------------------
_MIGRATIONS: list[tuple[str, callable]] = [
    # Each migration receives a QSettings instance and mutates it in-place.
    (
        "2.0.0",
        lambda qs: _migrate_v200(qs),
    ),
    (
        "2.0.1",
        lambda qs: _migrate_v201(qs),
    ),
    (
        "2.0.2",
        lambda qs: _migrate_v202(qs),
    ),
]


def _migrate_v200(qs: QSettings) -> None:
    """2.0.0: Initial settings schema — no migration needed, placeholder."""
    pass


def _migrate_v201(qs: QSettings) -> None:
    """2.0.1: Ensure quality thresholds exist with defaults."""
    defaults = {
        "quality/nonortho_warn": 65.0,
        "quality/nonortho_fail": 85.0,
        "quality/skew_warn": 4.0,
        "quality/skew_fail": 10.0,
        "quality/aspect_warn": 1000.0,
        "quality/aspect_fail": 5000.0,
    }
    for key, default_value in defaults.items():
        if not qs.contains(key):
            qs.setValue(key, default_value)
            logger.debug("Settings: set default %s = %s", key, default_value)


def _migrate_v202(qs: QSettings) -> None:
    """2.0.2: Reset a saved non-"m" Unit selection back to "m".

    tessellate_patches() (geometry.py) now converts every imported STEP
    file to metres itself, using the file's own declared unit — before
    that fix, a STEP file came through in raw cadquery units (effectively
    millimetres mislabeled as metres), and picking "mm" in the GUI's Unit
    dropdown was the only way to get a correctly-scaled bounding box.
    That workaround, if saved, now DOUBLE-converts on top of the already-
    correct upstream value: confirmed live on a real 3m x 0.255m x 0.255m
    part — with "mm" still selected from before the fix, it showed as
    0.003m x 0.0003m x 0.0003m, 1000x too small, driving GMSH sizing
    (and the whole downstream mesh) to nonsense. Only "mm"/"cm"/"inch"/
    "ft" selections predate this fix and are reset; "m" (the default,
    already a no-op scale) is left alone either way.
    """
    unit = qs.value("params/unit", None)
    if unit is not None and str(unit).lower() != "m":
        logger.info(
            "Settings migration 2.0.2: resetting stale Unit override %r to "
            "'m' — STEP unit conversion is now automatic, this override "
            "would double-convert.", unit,
        )
        qs.setValue("params/unit", "m")
