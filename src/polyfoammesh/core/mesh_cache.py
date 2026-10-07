"""Persistent cache of GMSH tetrahedral (volume) meshes.

GMSH dominates the wall time of a meshing run: serial surface
serialization and optimization phases take minutes even when only the
boundary-layer settings changed.  Iterating on y+/layers should therefore
reuse the previously meshed tetrahedra instead of re-running GMSH.

This module keys a cached ``.msh`` on a *fingerprint* of the inputs that
actually affect the tetrahedral mesh (geometry, sizing, mesher, detail,
refinement zones) and deliberately EXCLUDES every boundary-layer
parameter — changing the number of layers, the first-layer thickness, the
expansion ratio or the BL robustness flags must still hit the same cached
tet mesh, because the boundary layer is (re)applied downstream.

Layout under the app data directory::

    %APPDATA%/polyfoammesh/mesh_cache/
        <fingerprint>.msh        cached tetrahedral mesh
        <fingerprint>.json       sidecar metadata

Every public function is fail-soft: a cache problem logs at debug/warning
level and returns ``None``/``False``/``0`` — it must never block meshing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Cache format version — bump when the canonical input schema changes so
#: stale fingerprints are not silently reused.
CACHE_VERSION = 1

#: Inputs that affect the TET MESH ONLY, in canonical (hashed) form.
#:
#: Explicitly EXCLUDED (they must NOT change the fingerprint):
#:   nLayers, firstLayerThickness / bl_thickness, thicknessRatio /
#:   bl_expansion, bl_wall_size_floor, applyToAll, minBaseEdgeFraction,
#:   terminateByBudget, and every other boundary-layer / solver setting.
#: Unknown keys passed in ``inputs`` are ignored on purpose, so a caller
#: that accidentally forwards BL params cannot invalidate the cache.
CANONICAL_KEYS: tuple[str, ...] = (
    "geometry_path",
    "geometry_size",
    "geometry_mtime_ns",
    "mesher",
    "detail",
    "adaptive",
    "max_cell",
    "min_cell",
    "max_cells_target",
    "surface_resolution",
    "feature_angle",
    "refinement_zones",
    "size_field_file",
    # Boundary-layer inputs that SHAPE THE .msh ITSELF: the GMSH stage
    # creates a BoundaryLayer field from n_layers/bl_thickness/bl_expansion
    # (the near-wall anisotropic tet layout the dual is built on) and a
    # wall-size floor field.  Excluding these made the cache return a STALE
    # .msh after a BL change (wrong near-wall layers, wrong wall floor) -
    # the cache must invalidate whenever any of them changes.  The remaining
    # BL settings (applyToAll, sliver filters, budget termination) do NOT
    # touch the GMSH stage and stay excluded.
    "n_layers",
    "bl_thickness",
    "bl_expansion",
    "bl_wall_size_floor",
)


def _jsonable(value: Any) -> Any:
    """Best-effort JSON-able projection (tuples -> lists, Path -> str)."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return value


def _canonical_value(value: Any) -> Any:
    """Deterministic canonical form for one fingerprint input.

    Refinement zones are lists of dicts (order-independent), so each zone
    is serialized and the list is sorted; everything else is passed
    through unchanged.
    """
    if isinstance(value, (list, tuple, set)):
        try:
            serialized = [
                json.dumps(_jsonable(v), sort_keys=True, separators=(",", ":"))
                for v in value
            ]
            return sorted(serialized)
        except (TypeError, ValueError):
            return repr(value)
    return _jsonable(value)


def mesh_fingerprint(inputs: dict) -> str:
    """SHA256 over the canonical JSON of the TET-mesh inputs.

    Only :data:`CANONICAL_KEYS` are considered; any other key (notably all
    boundary-layer parameters) is ignored.  Missing keys hash as ``null``,
    so the result is stable across calls with equivalent inputs.
    """
    payload = {key: _canonical_value(inputs.get(key)) for key in CANONICAL_KEYS}
    payload["_v"] = CACHE_VERSION
    try:
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), default=str,
        )
    except (TypeError, ValueError):
        canonical = repr(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cache_root() -> Path:
    """Directory holding cached meshes (under the app data dir).

    Lives next to logs/session snapshots rather than inside the timestamped
    case directories, so it survives case cleanup.
    """
    from polyfoammesh import _paths

    return _paths.app_data_dir() / "mesh_cache"


def _entry_paths(fingerprint: str, root: Path | None = None) -> tuple[Path, Path]:
    base = root if root is not None else cache_root()
    return base / f"{fingerprint}.msh", base / f"{fingerprint}.json"


def lookup(fingerprint: str) -> Path | None:
    """Return the cached ``.msh`` path when present, non-empty and described
    by a sidecar ``.json``.  ``None`` on any miss or error (fail-soft)."""
    try:
        msh, meta = _entry_paths(str(fingerprint))
        if not msh.is_file() or not meta.is_file():
            return None
        if msh.stat().st_size <= 0:
            logger.debug("Mesh cache entry %s is empty — ignoring", msh)
            return None
        return msh
    except Exception:  # pragma: no cover - defensive
        logger.debug("Mesh cache lookup failed", exc_info=True)
        return None


def metadata(fingerprint: str) -> dict:
    """Read the sidecar metadata for *fingerprint* (empty dict on failure)."""
    try:
        _msh, meta = _entry_paths(str(fingerprint))
        if not meta.is_file():
            return {}
        with meta.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.debug("Mesh cache metadata read failed", exc_info=True)
        return {}


def store(fingerprint: str, msh_path: Path | str, meta: dict | None = None) -> bool:
    """Copy ``msh_path`` into the cache atomically, with a JSON sidecar.

    Writes to a ``.tmp`` file then :func:`os.replace` so a reader never sees
    a half-written entry.  Returns ``True`` on success; every failure is
    logged and returns ``False``.
    """
    try:
        source = Path(msh_path)
        if not source.is_file():
            logger.debug("Mesh cache store skipped: %s missing", source)
            return False
        size = source.stat().st_size
        if size <= 0:
            logger.debug("Mesh cache store skipped: %s is empty", source)
            return False

        root = cache_root()
        root.mkdir(parents=True, exist_ok=True)
        dest_msh, dest_meta = _entry_paths(str(fingerprint), root)

        tmp_msh = dest_msh.with_suffix(".msh.tmp")
        # copyfile (not copy2): the cached entry's mtime should reflect when
        # it was stored, which is what prune() orders by.
        shutil.copyfile(source, tmp_msh)
        os.replace(tmp_msh, dest_msh)

        payload = dict(meta or {})
        payload.setdefault("fingerprint", str(fingerprint))
        payload.setdefault(
            "created",
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        payload["msh_size"] = dest_msh.stat().st_size
        payload["cache_version"] = CACHE_VERSION

        tmp_meta = dest_meta.with_suffix(".json.tmp")
        with tmp_meta.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True, default=str)
        os.replace(tmp_meta, dest_meta)
        logger.debug("Mesh cache stored %s (%.1f MB)", dest_msh, size / 1e6)
        return True
    except Exception:
        logger.warning("Mesh cache store failed (continuing without cache)",
                       exc_info=True)
        return False


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        logger.debug("Mesh cache: could not delete %s", path, exc_info=True)


def prune(max_entries: int = 5, max_bytes: int = 2_000_000_000) -> None:
    """Keep the newest entries within both a count and a byte budget.

    Deletes the oldest ``.msh``/``.json`` pairs once either limit is
    exceeded.  Never raises.
    """
    try:
        root = cache_root()
        if not root.is_dir():
            return
        entries: list[tuple[int, int, str, Path]] = []
        for msh in root.glob("*.msh"):
            try:
                st = msh.stat()
            except OSError:
                continue
            fingerprint = msh.stem
            entries.append((st.st_mtime_ns, st.st_size, fingerprint, msh))
        # Newest first.
        entries.sort(key=lambda e: e[0], reverse=True)
        limit_entries = max(1, int(max_entries))
        limit_bytes = max(1, int(max_bytes))
        kept = 0
        used = 0
        for _mtime, size, fingerprint, msh in entries:
            if kept >= limit_entries or used + size > limit_bytes:
                _safe_unlink(msh)
                _msh_meta, meta = _entry_paths(fingerprint, root)
                _safe_unlink(meta)
                _safe_unlink(msh.with_suffix(".msh.tmp"))
                _safe_unlink(meta.with_suffix(".json.tmp"))
                continue
            kept += 1
            used += size
    except Exception:  # pragma: no cover - defensive
        logger.debug("Mesh cache prune failed", exc_info=True)


def clear() -> int:
    """Delete every cache entry; return how many entries were removed.

    Orphan sidecars without an ``.msh`` are removed too but not counted.
    Never raises.
    """
    removed = 0
    try:
        root = cache_root()
        if not root.is_dir():
            return 0
        for msh in list(root.glob("*.msh")):
            fingerprint = msh.stem
            _safe_unlink(msh)
            removed += 1
        for stray in list(root.glob("*.json")):
            _safe_unlink(stray)
        for tmp in list(root.glob("*.tmp")):
            _safe_unlink(tmp)
    except Exception:  # pragma: no cover - defensive
        logger.debug("Mesh cache clear failed", exc_info=True)
    return removed


__all__ = [
    "CACHE_VERSION",
    "CANONICAL_KEYS",
    "mesh_fingerprint",
    "cache_root",
    "lookup",
    "metadata",
    "store",
    "prune",
    "clear",
]
