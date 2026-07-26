"""Automatic cleanup of old ``cfmesh_cases/`` directories.

Each meshing run creates a timestamped case directory that can be
hundreds of MB.  Over weeks of normal use these accumulate without
ever being cleaned up, potentially filling the disk.

This module provides:
- ``cleanup_old_cases()`` — remove dirs older than *max_days* or keep
  only the *keep_last* most recent ones.
- ``cases_disk_usage_mb()`` — total size of all case dirs.
- ``list_cases()`` — all case dirs sorted by age.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Default case root (same convention as main_window.py / quick_mesh.py)
_DEFAULT_ROOTS = [Path.home() / "cfmesh_cases", Path("C:/cfmesh_cases")]


def _case_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()
    for r in _DEFAULT_ROOTS:
        resolved = r.resolve()
        if resolved.exists() and resolved.is_dir() and resolved not in seen:
            roots.append(resolved)
            seen.add(resolved)
    return roots


def list_cases() -> list[Path]:
    """Return all case directories sorted by modification time (oldest first)."""
    results: list[Path] = []
    for root in _case_roots():
        for d in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime if p.is_dir() else 0):
            if d.is_dir() and not d.name.startswith("."):
                results.append(d)
    return results


def cleanup_by_age(max_days: int = 30) -> int:
    """Remove case directories older than ``max_days``.

    Returns:
        Number of directories removed.
    """
    cutoff = time.time() - max_days * 86400
    removed = 0
    for d in list_cases():
        try:
            mtime = d.stat().st_mtime
            if mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                logger.info("Cleaned old case: %s (mtime=%s)", d, time.ctime(mtime))
                removed += 1
        except OSError as exc:
            logger.warning("Could not clean %s: %s", d, exc)
    return removed


def cleanup_keep_last(keep: int = 10) -> int:
    """Keep only the ``keep`` most recent case directories.

    Returns:
        Number of directories removed.
    """
    cases = list_cases()
    if len(cases) <= keep:
        return 0
    removed = 0
    for d in cases[:-keep]:
        try:
            shutil.rmtree(d, ignore_errors=True)
            logger.info("Cleaned old case (keep_last): %s", d)
            removed += 1
        except OSError as exc:
            logger.warning("Could not clean %s: %s", d, exc)
    return removed


def cases_disk_usage_mb() -> float:
    """Return total size of all case directories in MB."""
    total = 0
    for d in list_cases():
        try:
            total += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        except OSError:
            pass
    return total / (1024 * 1024)


def auto_cleanup(keep_last: int = 10, max_days: int = 30) -> dict:
    """Run both age-based and count-based cleanup.

    Called automatically on app startup (via ``MainWindow`` init) and
    after each meshing run.

    Returns:
        Dict with keys ``removed_by_age``, ``removed_by_keep``,
        ``disk_freed_mb``, ``remaining_mb``.
    """
    usage_before = cases_disk_usage_mb()
    n_age = cleanup_by_age(max_days)
    n_keep = 0
    if keep_last > 0:
        n_keep = cleanup_keep_last(keep_last)
    usage_after = cases_disk_usage_mb()
    freed = usage_before - usage_after
    if n_age + n_keep > 0:
        logger.info(
            "Disk cleanup: removed %d case dirs (age=%d, keep=%d), "
            "freed %.1f MB, remaining %.1f MB",
            n_age + n_keep, n_age, n_keep, freed, usage_after,
        )
    return {
        "removed_by_age": n_age,
        "removed_by_keep": n_keep,
        "disk_freed_mb": round(freed, 1),
        "remaining_mb": round(usage_after, 1),
    }
