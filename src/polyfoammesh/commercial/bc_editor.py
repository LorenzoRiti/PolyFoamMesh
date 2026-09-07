"""Boundary Condition visual editor.

Reads ``constant/polyMesh/boundary``, displays patches with semantic
colours, auto-detects BC type by name + normal direction + geometry,
allows click-to-rename/retype in 3D, provides BC presets per case type,
and exports complete ``0/`` field files.

Usage::

    bce = BCEditor()
    patches = bce.read_boundary(case_dir)
    bce.auto_detect_types(patches, bbox=(2.0, 1.0, 1.0))
    bce.rename_patch(patches, "old_name", "new_name")
    bce.export_fields(case_dir, patches, solver_template="simpleFoam")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polyfoammesh.core.patch_roles import (
    ROLE_EMPTY,
    ROLE_INLET,
    ROLE_OUTLET,
    ROLE_SYMMETRY,
    ROLE_WALL,
    match_role,
)
from polyfoammesh.octopoda_local import octo

logger = logging.getLogger(__name__)

# Semantic colours for patch types (RGB tuples for pyvista)
PATCH_COLORS = {
    "inlet": (0.2, 0.6, 1.0),     # Blue
    "outlet": (1.0, 0.3, 0.2),    # Red-Orange
    "wall": (0.6, 0.6, 0.6),      # Gray
    "symmetry": (0.2, 0.8, 0.4),  # Green
    "patch": (0.8, 0.6, 0.2),     # Amber
    "default": (0.5, 0.5, 0.8),   # Purple-blue
}

# Default BC type presets
BC_PRESETS: dict[str, dict[str, dict[str, Any]]] = {
    "inlet": {
        "U": {"type": "fixedValue", "value": "uniform (1 0 0)"},
        "p": {"type": "zeroGradient"},
        "k": {"type": "fixedValue", "value": "uniform 0.06"},
        "omega": {"type": "fixedValue", "value": "uniform 10"},
        "epsilon": {"type": "fixedValue", "value": "uniform 0.5"},
        "nut": {"type": "calculated", "value": "uniform 0"},
    },
    "outlet": {
        "U": {"type": "zeroGradient"},
        "p": {"type": "fixedValue", "value": "uniform 0"},
        "k": {"type": "zeroGradient"},
        "omega": {"type": "zeroGradient"},
        "epsilon": {"type": "zeroGradient"},
        "nut": {"type": "calculated", "value": "uniform 0"},
    },
    "wall": {
        "U": {"type": "fixedValue", "value": "uniform (0 0 0)"},
        "p": {"type": "zeroGradient"},
        "k": {"type": "kqRWallFunction", "value": "uniform 0"},
        "omega": {"type": "omegaWallFunction", "value": "uniform 1"},
        "epsilon": {"type": "epsilonWallFunction", "value": "uniform 0.1"},
        "nut": {"type": "nutkWallFunction", "value": "uniform 0"},
    },
    "symmetry": {
        "U": {"type": "symmetry"},
        "p": {"type": "symmetry"},
        "k": {"type": "symmetry"},
        "omega": {"type": "symmetry"},
        "epsilon": {"type": "symmetry"},
        "nut": {"type": "symmetry"},
    },
}


@dataclass
@dataclass
class BcPatchInfo:
    """Boundary patch info for the BC editor (extends core BcPatchInfo).
    
    Adds BC type, colour, centroid, normal, and area to the basic
    (name, nFaces, startFace) from core.boundary_reader.BcPatchInfo.
    """
    name: str = ""
    orig_name: str = ""
    n_faces: int = 0
    start_face: int = 0
    bc_type: str = "patch"  # inlet, outlet, wall, symmetry, patch
    detected_by: str = ""   # name, normal, geometry, user
    colour: tuple[float, float, float] = (0.5, 0.5, 0.5)
    centroid: tuple[float, float, float] = (0.0, 0.0, 0.0)
    normal: tuple[float, float, float] = (0.0, 0.0, 0.0)
    area: float = 0.0


class BCEditor:
    """Boundary condition editor — read, classify, edit, export.

    Usage::

        editor = BCEditor()
        patches = editor.read_boundary(case_dir)
        editor.auto_detect_types(patches, bbox=(2, 1, 1))
        editor.export_fields(case_dir, patches)
    """

    def read_boundary(self, case_dir: Path | str) -> list[BcPatchInfo]:
        """Read ``constant/polyMesh/boundary`` and return patch list.

        Args:
            case_dir: OpenFOAM case directory.

        Returns:
            List of ``BcPatchInfo`` with name, type, nFaces, startFace.
        """
        from polyfoammesh.core.boundary_reader import parse_boundary

        case_dir = Path(case_dir)
        boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
        if not boundary_path.exists():
            raise FileNotFoundError(f"Boundary file not found: {boundary_path}")

        raw_patches = parse_boundary(boundary_path)
        patches: list[BcPatchInfo] = []

        for rp in raw_patches:
            patches.append(BcPatchInfo(
                name=rp.name,
                orig_name=rp.name,
                n_faces=rp.n_faces,
                start_face=rp.start_face,
                bc_type=self._name_to_type(rp.name),
                colour=PATCH_COLORS.get(
                    self._name_to_type(rp.name),
                    PATCH_COLORS["default"],
                ),
            ))

        octo.log_event("bc_editor", "read", {"patches": len(patches)})
        return patches

    def auto_detect_types(
        self, patches: list[BcPatchInfo],
        bbox: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> int:
        """Auto-detect BC type for each patch using name + geometry.

        Detection priority:
          1. Name-based: inlet, outlet, wall, symmetry keywords
          2. Normal direction: face normal points along bbox axis
          3. Centroid position: at bbox min/max along axis

        Args:
            patches: List of BcPatchInfo to update in-place.
            bbox: Bounding box dimensions (dx, dy, dz).

        Returns:
            Number of patches whose type was changed.
        """
        changed = 0
        for p in patches:
            detected = self._detect_type(p, bbox)
            if detected != p.bc_type:
                p.bc_type = detected
                p.colour = PATCH_COLORS.get(detected, PATCH_COLORS["default"])
                p.detected_by = "name"
                changed += 1

        octo.log_event("bc_editor", "auto_detect", {"changed": changed})
        return changed

    def _name_to_type(self, name: str) -> str:
        """Map a patch name to a BC type via ``patch_roles.match_role``.

        Returns the same vocabulary as before (``"inlet"`` / ``"outlet"`` /
        ``"wall"`` / ``"symmetry"`` / ``"patch"``) so ``PATCH_COLORS`` and
        ``BC_PRESETS`` keep working; symmetry and empty both map to
        ``"symmetry"``.

        ``match_role`` — not ``classify_patch`` — is deliberate. It returns
        ``None`` for a name carrying no keyword instead of defaulting it to
        a wall, so ``_detect_type`` reaches its geometric branch for
        unnamed-patch cases (GMSH emits ``surface_N`` for every patch, and
        geometry is the only thing that can tell an inlet from a wall
        there). Defaulting those to "wall" here silently pre-empted the
        geometry path that exists precisely to answer them.
        """
        role = match_role(name)
        if role == ROLE_INLET:
            return "inlet"
        if role == ROLE_OUTLET:
            return "outlet"
        if role in (ROLE_SYMMETRY, ROLE_EMPTY):
            return "symmetry"
        if role == ROLE_WALL:
            return "wall"
        return "patch"

    def _detect_type(
        self, patch: BcPatchInfo,
        bbox: tuple[float, float, float],
    ) -> str:
        """Detect BC type using name first, then geometry."""
        # 1. Name-based
        name_type = self._name_to_type(patch.name)
        if name_type != "patch":
            return name_type

        # 2. Centroid-based (if bbox available and centroid is set)
        if bbox != (0, 0, 0) and patch.centroid != (0, 0, 0):
            cx, cy, cz = patch.centroid
            dx, dy, dz = bbox
            bbox_max = max(dx, dy, dz) or 1.0
            tol = bbox_max * 0.05

            # Check if centroid is on a bbox face
            nx, ny, nz = patch.normal
            near_x_min = abs(cx - 0) < tol
            near_x_max = abs(cx - dx) < tol if dx > 0 else False

            if near_x_min and nx > 0.5:
                return "inlet"
            if near_x_max and nx < -0.5:
                return "outlet"
            if (near_x_min or near_x_max) and abs(nx) > 0.5:
                return "symmetry" if dy > 0 and abs(cy - dy / 2) < tol else "wall"

            # Z-direction: top/bottom faces
            near_z_min = abs(cz - 0) < tol if dz > 0 else False
            near_z_max = abs(cz - dz) < tol if dz > 0 else False
            if near_z_min or near_z_max:
                return "symmetry"

        return "patch"

    def rename_patch(
        self, patches: list[BcPatchInfo],
        old_name: str, new_name: str,
    ) -> bool:
        """Rename a patch and update its colour based on the new name.

        Args:
            patches: List of patches to modify.
            old_name: Current patch name.
            new_name: New patch name.

        Returns:
            True if renamed, False if old_name not found.
        """
        for p in patches:
            if p.name == old_name:
                p.name = new_name
                p.bc_type = self._name_to_type(new_name)
                p.colour = PATCH_COLORS.get(p.bc_type, PATCH_COLORS["default"])
                octo.log_event("bc_editor", "rename", {"from": old_name, "to": new_name})
                return True
        return False

    def set_type(self, patch: BcPatchInfo, bc_type: str) -> None:
        """Manually set a patch's BC type."""
        if bc_type not in PATCH_COLORS:
            raise ValueError(f"Unknown BC type: {bc_type}. Valid: {list(PATCH_COLORS)}")
        patch.bc_type = bc_type
        patch.colour = PATCH_COLORS[bc_type]
        patch.detected_by = "user"

    # ------------------------------------------------------------------
    # BC presets
    # ------------------------------------------------------------------
    def get_preset(self, bc_type: str) -> dict[str, dict[str, Any]]:
        """Get BC field preset for a given type."""
        return BC_PRESETS.get(bc_type, BC_PRESETS["wall"])

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_fields(
        self, case_dir: Path | str,
        patches: list[BcPatchInfo],
        solver_template: str = "simpleFoam",
    ) -> list[str]:
        """Export complete ``0/`` field files for all patches.

        Writes U, p, k, omega, epsilon, nut in the ``0/`` directory
        using BC presets per patch type.

        Args:
            case_dir: Case directory (``0/`` subdir is created).
            patches: List of patches with their BC types.
            solver_template: Solver type for field selection.

        Returns:
            List of files written.
        """
        case_dir = Path(case_dir)
        dir_0 = case_dir / "0"
        dir_0.mkdir(parents=True, exist_ok=True)
        written: list[str] = []

        # Determine which fields based on solver
        turb_fields = ["k", "omega", "epsilon", "nut"]
        if solver_template in ("laminar", "laplacianFoam"):
            fields = ["U", "p"]
        else:
            fields = ["U", "p"] + turb_fields

        for field_name in fields:
            content = self._generate_field(field_name, patches)
            path = dir_0 / field_name
            path.write_text(content, encoding="ascii")
            written.append(f"0/{field_name}")

        octo.log_event("bc_editor", "export_fields", {
            "fields": len(written),
            "patches": len(patches),
        })
        return written

    def _generate_field(self, field_name: str, patches: list[BcPatchInfo]) -> str:
        """Generate a complete OpenFOAM field file."""
        # Determine dimensions and internal field
        dims_map = {
            "U": "[0 1 -1 0 0 0 0]",
            "p": "[0 2 -2 0 0 0 0]",
            "k": "[0 2 -2 0 0 0 0]",
            "omega": "[0 0 -1 0 0 0 0]",
            "epsilon": "[0 2 -3 0 0 0 0]",
            "nut": "[0 2 -1 0 0 0 0]",
        }
        cls_map = {
            "U": "volVectorField",
            "p": "volScalarField", "k": "volScalarField",
            "omega": "volScalarField", "epsilon": "volScalarField",
            "nut": "volScalarField",
        }
        internal_map = {
            "U": "uniform (0 0 0)",
            "p": "uniform 0",
            "k": "uniform 0.06",
            "omega": "uniform 10",
            "epsilon": "uniform 0.5",
            "nut": "uniform 0",
        }

        dimensions = dims_map.get(field_name, "[0 0 0 0 0 0 0]")
        cls = cls_map.get(field_name, "volScalarField")
        internal = internal_map.get(field_name, "uniform 0")

        lines = [
            f"FoamFile {{ version 2.0; format ascii; class {cls}; object {field_name}; }}",
            f"dimensions {dimensions};",
            f"internalField {internal};",
            "boundaryField",
            "{",
        ]

        for p in patches:
            preset = self.get_preset(p.bc_type)
            field_cfg = preset.get(field_name, {"type": "zeroGradient"})
            lines.append(f"    {p.name}")
            lines.append("    {")
            for key, val in field_cfg.items():
                if isinstance(val, str):
                    lines.append(f"        {key} {val};")
                elif isinstance(val, dict):
                    lines.append(f"        {key} {val};")
            lines.append("    }")

        lines.append("}")
        return "\n".join(lines) + "\n"

    def export_boundary_file(
        self, case_dir: Path | str, patches: list[BcPatchInfo],
    ) -> str:
        """Write the updated ``constant/polyMesh/boundary`` file.

        This overwrites the boundary file with the new patch names/types.

        Args:
            case_dir: Case directory.
            patches: Updated list of patches.

        Returns:
            Path to the written boundary file as string.
        """
        case_dir = Path(case_dir)
        poly_dir = case_dir / "constant" / "polyMesh"
        poly_dir.mkdir(parents=True, exist_ok=True)
        path = poly_dir / "boundary"

        lines = [
            "FoamFile { version 2.0; format ascii; class polyBoundaryMesh; object boundary; }",
            "",
            f"{len(patches)}",
            "(",
        ]
        for p in patches:
            patch_type = {
                "inlet": "patch", "outlet": "patch", "wall": "wall",
                "symmetry": "symmetry", "patch": "patch",
            }.get(p.bc_type, "patch")
            lines.append(f"    {p.name}")
            lines.append("    {")
            lines.append(f"        type {patch_type};")
            lines.append(f"        nFaces {p.n_faces};")
            lines.append(f"        startFace {p.start_face};")
            lines.append("    }")
        lines.append(")")

        path.write_text("\n".join(lines) + "\n", encoding="ascii")
        octo.log_event("bc_editor", "export_boundary", {"patches": len(patches)})
        return str(path)

    @staticmethod
    def colour_for_patch(patch: BcPatchInfo) -> tuple[float, float, float]:
        """Return the RGB colour for a patch."""
        return PATCH_COLORS.get(patch.bc_type, PATCH_COLORS["default"])
