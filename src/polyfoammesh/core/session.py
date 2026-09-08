from __future__ import annotations

"""Session persistence — auto-save/restore work-in-progress across app restarts.

Saves a lightweight snapshot of the current state (open geometry file,
mesh parameters, viewer state) whenever the user makes a meaningful
change. On next launch the app can offer to restore the previous session.

Storage: a single JSON file in the app data directory.
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from polyfoammesh._paths import app_data_dir

logger = logging.getLogger(__name__)

_SESSION_DIR = app_data_dir() / "session"
_SESSION_FILE = _SESSION_DIR / "last_session.json"
_MAX_SNAPSHOTS = 5  # Keep this many historical snapshots


@dataclass
class SessionSnapshot:
    """A point-in-time snapshot of the application state."""

    version: str = ""
    timestamp: str = ""
    geometry_path: str = ""
    case_dir: str = ""
    max_cell: float = 0.05
    min_cell: float = 0.01
    detail: str = "medium"
    unit: str = "m"
    bl_enabled: bool = False
    bl_n_layers: int = 3
    bl_thickness_ratio: float = 0.005
    bl_expansion_ratio: float = 1.2
    mesher: str = "cfmesh"
    theme: str = "system"
    viewer_bg: str = "Dark"
    extra: dict[str, Any] = field(default_factory=dict)


def _ensure_dir() -> Path:
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return _SESSION_DIR


def save_snapshot(snapshot: SessionSnapshot) -> None:
    """Persist a session snapshot to disk."""
    snap_dir = _ensure_dir()
    data = asdict(snapshot)
    data["timestamp"] = datetime.now().isoformat()
    _SESSION_FILE.write_text(json.dumps(data, indent=2, default=str))

    # Archive as a numbered snapshot for rollback
    archive = snap_dir / "archived"
    archive.mkdir(exist_ok=True)
    existing = sorted(archive.glob("snapshot_*.json"))
    if len(existing) >= _MAX_SNAPSHOTS:
        existing[0].unlink()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (archive / f"snapshot_{ts}.json").write_text(
        json.dumps(data, indent=2, default=str)
    )
    logger.debug("Session snapshot saved (ts=%s)", data.get("timestamp"))


def load_snapshot() -> SessionSnapshot | None:
    """Load the most recent session snapshot, or None."""
    if not _SESSION_FILE.exists():
        return None
    try:
        data = json.loads(_SESSION_FILE.read_text())
        return SessionSnapshot(**{k: v for k, v in data.items() if k in SessionSnapshot.__dataclass_fields__})
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        logger.warning("Failed to load session snapshot: %s", exc)
        return None


def clear_snapshot() -> None:
    """Remove the session snapshot (e.g. after successful restore)."""
    if _SESSION_FILE.exists():
        _SESSION_FILE.unlink()
        logger.debug("Session snapshot cleared.")


def has_snapshot() -> bool:
    return _SESSION_FILE.exists()
