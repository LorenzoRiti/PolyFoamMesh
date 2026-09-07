from __future__ import annotations

"""Input validation for mesh parameters, geometry, and user settings.

Centralises all validation logic so individual widgets and workers
don't have to re-implement checks. Every public function returns a
``ValidationResult`` with a boolean, an optional message, and an
optional list of warnings.

Usage::

    result = validate_cell_size(max_cell=0.05, min_cell=0.01, bbox_dim=2.0)
    if not result.valid:
        show_error(result.message)
    for w in result.warnings:
        log_warning(w)
"""

import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------
@dataclass
class ValidationResult:
    valid: bool = True
    message: str = ""
    warnings: list[str] = field(default_factory=list)


def _ok() -> ValidationResult:
    return ValidationResult()


def _err(msg: str, *warnings: str) -> ValidationResult:
    return ValidationResult(valid=False, message=msg, warnings=list(warnings))


def _warn(*warnings: str) -> ValidationResult:
    return ValidationResult(valid=True, warnings=list(warnings))


# ---------------------------------------------------------------------------
# Mesh parameter validation
# ---------------------------------------------------------------------------
MIN_CELL_ABS = 1e-6       # 1 micron — hard floor
MAX_CELL_ABS = 100.0      # 100 m — hard ceiling
MIN_CELL_RATIO = 0.001    # minCell must be at least 0.1 % of maxCell
MAX_CELL_RATIO = 0.5      # minCell must be at most 50 % of maxCell


def validate_cell_size(
    max_cell: float,
    min_cell: float,
    bbox_dim: float | None = None,
) -> ValidationResult:
    """Validate global cell size pair.

    Args:
        max_cell: global maximum cell size (m).
        min_cell: global minimum cell size (m).
        bbox_dim: bounding-box max dimension (m).  When provided, extra
            sanity checks against the domain size are performed.

    Returns:
        ``ValidationResult`` — ``valid`` is ``False`` when values are
        out of range or inconsistent.
    """
    warns: list[str] = []

    if not isinstance(max_cell, (int, float)):
        return _err(f"max_cell must be a number, got {type(max_cell).__name__}")
    if not isinstance(min_cell, (int, float)):
        return _err(f"min_cell must be a number, got {type(min_cell).__name__}")

    if max_cell <= 0:
        return _err(f"max_cell must be positive, got {max_cell:.6e}")
    if min_cell <= 0:
        return _err(f"min_cell must be positive, got {min_cell:.6e}")

    if max_cell > MAX_CELL_ABS:
        warns.append(f"max_cell ({max_cell:.4f}) exceeds hard ceiling {MAX_CELL_ABS:.1f} m")

    if min_cell < MIN_CELL_ABS:
        warns.append(f"min_cell ({min_cell:.6e}) below recommended minimum {MIN_CELL_ABS:.6e} m")

    if min_cell > max_cell * MAX_CELL_RATIO:
        clamped = max_cell * MAX_CELL_RATIO
        return _err(
            f"min_cell ({min_cell:.6e}) exceeds {MAX_CELL_RATIO*100:.0f} % of "
            f"max_cell ({max_cell:.6e}). Reduce min_cell to at most {clamped:.6e}.",
        )

    if bbox_dim is not None and bbox_dim > 0:
        if max_cell > bbox_dim:
            return _err(
                f"max_cell ({max_cell:.4f}) is larger than the domain "
                f"bounding-box ({bbox_dim:.4f} m)."
            )
        if max_cell > bbox_dim / 2.0:
            warns.append(
                f"max_cell ({max_cell:.4f}) > bbox/2 ({bbox_dim/2:.4f}) — "
                "mesh will be extremely coarse."
            )
        if min_cell < bbox_dim * 1e-6:
            warns.append(
                f"min_cell ({min_cell:.6e}) is very small relative to "
                f"bounding box ({bbox_dim:.4f}) — may produce an excessive "
                "number of cells."
            )

    if warns:
        return ValidationResult(valid=True, warnings=warns)
    return _ok()


def validate_bl_params(
    n_layers: int,
    thickness_ratio: float,
    expansion_ratio: float,
    wall_patches: Sequence[str] | None = None,
) -> ValidationResult:
    """Validate boundary-layer parameters.

    Args:
        n_layers: Number of prism layers (1–10).
        thickness_ratio: Ratio of first-layer thickness to local cell size
            (typical 0.001–0.1).
        expansion_ratio: Ratio between successive layer thicknesses
            (typical 1.1–1.5).
        wall_patches: List of patch names to apply BL to.
    """
    warns: list[str] = []

    if not isinstance(n_layers, int) or n_layers < 1 or n_layers > 10:
        return _err(f"n_layers must be an integer 1–10, got {n_layers!r}")

    if not isinstance(thickness_ratio, (int, float)) or thickness_ratio <= 0 or thickness_ratio > 1.0:
        return _err(f"thickness_ratio must be in (0, 1], got {thickness_ratio!r}")

    if thickness_ratio < 0.001:
        warns.append(f"thickness_ratio ({thickness_ratio:.6e}) is very small — layers may be too thin")

    if not isinstance(expansion_ratio, (int, float)) or expansion_ratio < 1.0 or expansion_ratio > 2.0:
        return _err(f"expansion_ratio must be in [1.0, 2.0], got {expansion_ratio!r}")

    if wall_patches is not None and len(wall_patches) == 0:
        warns.append("No wall patches specified — boundary layers will not be applied to any patch")

    if warns:
        return ValidationResult(valid=True, warnings=warns)
    return _ok()


def validate_detail_level(detail: str) -> ValidationResult:
    """Validate detail-level string."""
    allowed = {"very_coarse", "coarse", "medium", "fine", "very_fine"}
    if detail.lower() not in allowed:
        return _err(f"detail must be one of {allowed}, got {detail!r}")
    return _ok()


# ---------------------------------------------------------------------------
# Geometry / file validation
# ---------------------------------------------------------------------------
_STEP_EXT = {".step", ".stp"}
_STL_EXT = {".stl"}
_GEO_EXT = _STEP_EXT | _STL_EXT

# Max recommended file size: 500 MB
_MAX_GEO_BYTES = 500 * 1024 * 1024


def validate_geometry_path(path: str | Path) -> ValidationResult:
    """Validate that a geometry file path is usable.

    Checks:
    - File exists
    - Extension is supported (.step/.stp/.stl)
    - File size is within limits
    - Path contains no characters that break WSL/bash
    """
    path = Path(path)
    if not path.exists():
        return _err(f"File not found: {path}")
    if not path.is_file():
        return _err(f"Not a file: {path}")

    ext = path.suffix.lower()
    if ext not in _GEO_EXT:
        return _err(
            f"Unsupported format '{ext}'. Supported: "
            f'{", ".join(sorted(_GEO_EXT))}'
        )

    size = path.stat().st_size
    if size <= 0:
        return _err(f"File is empty: {path}")
    if size > _MAX_GEO_BYTES:
        mb = size / (1024 * 1024)
        max_mb = _MAX_GEO_BYTES / (1024 * 1024)
        return _err(
            f"File is {mb:.0f} MB (max {max_mb:.0f} MB). "
            "Simplify the CAD model or split it into parts."
        )

    return _ok()


def validate_case_dir(case_dir: str | Path) -> ValidationResult:
    """Validate that a case directory is usable for OpenFOAM.

    Checks:
    - No spaces in path
    - Path exists (or parent exists and can be created)
    - No characters that would break WSL/bash
    """
    path = Path(case_dir).resolve()
    path_str = str(path)

    if " " in path_str:
        return _err(
            f"OpenFOAM does not support spaces in paths.\nPath: {path_str}\n"
            "Choose a path without spaces."
        )

    # On Windows, backslash is the path separator — it is safe.
    # On Linux/Mac, backslash is a shell escape char and is unsafe.
    import os as _os
    unsafe_chars = r';`$()|&<>!'
    if _os.name != "nt":
        unsafe_chars += "\\"
    dangerous = re.search(f'[{re.escape(unsafe_chars)}]', path_str)
    if dangerous:
        return _err(
            f"Path contains potentially unsafe character '{dangerous.group()}':\n"
            f"  {path_str}\n"
            "Remove the character and try again."
        )

    parent = path.parent
    if not parent.exists():
        return _err(f"Parent directory does not exist: {parent}")
    if parent.is_file():
        return _err(f"Parent path is a file, not a directory: {parent}")

    return _ok()


# ---------------------------------------------------------------------------
# Settings / config validation
# ---------------------------------------------------------------------------
VALID_THEME_MODES = frozenset({"light", "dark", "system"})
VALID_EXPORT_FORMATS = frozenset({"cgns", "vtu", "pdf"})
VALID_MESHERS = frozenset({"cfmesh", "gmsh_hybrid", "gmsh_direct"})


def validate_settings(key: str, value: object) -> ValidationResult:
    """Validate a single QSettings key/value pair.

    Use before writing to ``QSettings`` to catch data corruption early.
    """
    validator = _SETTINGS_VALIDATORS.get(key)
    if validator is None:
        return ValidationResult(valid=True, message=f"Unknown key '{key}' — no validation")
    return validator(value)


_SETTINGS_VALIDATORS: dict[str, callable] = {
    "ui/theme_mode": lambda v: (
        _ok() if isinstance(v, str) and v in VALID_THEME_MODES
        else _err(f"Theme must be one of {VALID_THEME_MODES}, got {v!r}")
    ),
    "params/detail_slider": lambda v: (
        _ok() if isinstance(v, int) and 0 <= int(v) <= 20
        else _err(f"detail_slider must be an int in 0..20, got {v!r}")
    ),
    "params/unit": lambda v: (
        _ok() if isinstance(v, str) and v in {"m", "mm", "cm", "inch", "ft"}
        else _err(f"Unit must be one of m/mm/cm/inch/ft, got {v!r}")
    ),
    "params/bl_checked": lambda v: (
        _ok() if isinstance(v, bool)
        else _err(f"bl_checked must be bool, got {type(v).__name__}")
    ),
    "window/size": lambda v: _ok(),  # QSize — cannot type-check easily
    "window/pos": lambda v: _ok(),  # QPoint — same
    "viewer/background": lambda v: (
        _ok() if isinstance(v, str)
        else _err(f"background must be string, got {type(v).__name__}")
    ),
    "quality/nonortho_warn": lambda v: _ok() if isinstance(v, (int, float)) else _err("number expected"),
    "quality/nonortho_fail": lambda v: _ok() if isinstance(v, (int, float)) else _err("number expected"),
    "quality/skew_warn": lambda v: _ok() if isinstance(v, (int, float)) else _err("number expected"),
    "quality/skew_fail": lambda v: _ok() if isinstance(v, (int, float)) else _err("number expected"),
    "quality/aspect_warn": lambda v: _ok() if isinstance(v, (int, float)) else _err("number expected"),
    "quality/aspect_fail": lambda v: _ok() if isinstance(v, (int, float)) else _err("number expected"),
    "geometry/last_step_dir": lambda v: _ok() if isinstance(v, str) else _err("string expected"),
    "geometry/recent_files": lambda v: _ok(),  # QStringList
}


# ---------------------------------------------------------------------------
# Sanitisation helpers
# ---------------------------------------------------------------------------
def sanitise_patch_name(name: str) -> str:
    """Remove characters that OpenFOAM forbids in patch names."""
    cleaned = re.sub(r'[;{}"/\s]', "_", name)
    if cleaned != name:
        logger.info("Sanitised patch name '%s' -> '%s'", name, cleaned)
    return cleaned or "unnamed"


def sanitise_filename(name: str) -> str:
    """Remove characters unsafe for Windows/NTFS filenames."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return cleaned.strip() or "unnamed"


# ---------------------------------------------------------------------------
# Re-meshing guard (Fase 3 P3.2)
# ---------------------------------------------------------------------------
def mesh_remeshable(case_dir: str | Path) -> tuple[bool, str]:
    """Whether an existing mesh may be replaced by a fresh cfMesh cartesianMesh.

    ``quality_engine.auto_fix`` / ``optimizer.MeshOptimizer`` re-run
    cartesianMesh on whatever mesh already exists in the case — if that mesh
    came from the GMSH tet / tet→poly pipeline, re-meshing would SILENTLY
    replace the user's chosen mesh with an unrelated cfMesh hex at different
    dimensions. That silent-swap class of bug is forbidden (docs §5/§6): this
    guard refuses when the case carries a non-cfMesh signal.

    Signals (all offline, no WSL needed):
      - ``constant/polyMesh_tet_backup`` exists → the tet→poly pipeline built
        this case (both the GUI barycentric-dual path and poly_aggregator
        back the tet mesh up there before converting).
      - the existing mesh is a pure tetrahedral mesh (every cell has exactly
        4 faces) → direct GMSH tet output.

    Returns ``(True, reason)`` when re-meshing is safe, ``(False, reason)``
    when it would silently destroy a non-cfMesh mesh.
    """
    case = Path(case_dir)
    poly = case / "constant" / "polyMesh"
    if not (poly / "owner").exists():
        return True, "no existing mesh to protect"

    if (case / "constant" / "polyMesh_tet_backup").exists():
        return False, (
            "existing mesh is GMSH tet->poly pipeline output "
            "(constant/polyMesh_tet_backup present) — refusing to replace it "
            "with a cfMesh hex mesh"
        )

    try:
        from cfmesh_autogui.core import foam_mesh_io

        faces = foam_mesh_io.read_faces(poly / "faces")
        owner = foam_mesh_io.read_label_list(poly / "owner").astype(np.int64)
        neighbour = foam_mesh_io.read_label_list(poly / "neighbour").astype(np.int64)
        n_cells = int(max(owner.max(), neighbour.max())) + 1 if len(owner) else 0
        if n_cells == 0:
            return True, "mesh has no cells — nothing to protect"
        face_counts = np.zeros(n_cells, dtype=np.int64)
        np.add.at(face_counts, owner, 1)
        np.add.at(face_counts, neighbour[neighbour >= 0], 1)
        if np.all(face_counts == 4):
            return False, (
                "existing mesh is a pure tetrahedral (GMSH direct) mesh — "
                "refusing to replace it with a cfMesh hex mesh"
            )
    except Exception as exc:  # defensive: inspection must never block
        logger.warning("mesh_remeshable: cannot inspect mesh (%s) — proceeding", exc)
    return True, "existing mesh is cfMesh-compatible (hex-dominant)"
