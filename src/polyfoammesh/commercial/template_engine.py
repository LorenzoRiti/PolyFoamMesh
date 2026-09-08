"""Template engine — preset manager + user template save/load.

Provides predefined case templates (internal_flow, external_aero, cht,
multiphase, moving_body, conjugate_ht, combustion) and allows users
to save/load custom templates.

Each template contains: geometry example path, mesh preset parameters,
BC configuration, and solver configuration for SolverSetup.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from polyfoammesh.octopoda_local import octo

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "templates"
USER_TEMPLATES_DIR = Path.home() / "cfmesh_templates"


@dataclass
class TemplateMetadata:
    """Metadata for a case template."""
    name: str = ""
    description: str = ""
    category: str = ""  # internal_flow, external_aero, cht, multiphase, etc.
    icon: str = "default"  # icon key for UI grid
    solver: str = "simpleFoam"
    turbulence: str = "kOmegaSST"
    tags: list[str] = field(default_factory=list)


@dataclass
class TemplatePreset:
    """Complete template preset with geometry, mesh, BC, and solver parameters."""
    metadata: TemplateMetadata = field(default_factory=TemplateMetadata)

    # Geometry defaults
    geometry_hint: str = ""

    # Mesh defaults
    max_cell_ratio: float = 0.05  # fraction of bbox
    min_cell_ratio: float = 0.005
    detail: str = "medium"
    bl_enabled: bool = True
    bl_n_layers: int = 5

    # Solver defaults (passed to SolverConfig)
    end_time: float = 1000.0
    delta_t: float = 1.0
    write_interval: int = 100

    # Custom fields (stored as JSON)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": {
                "name": self.metadata.name,
                "description": self.metadata.description,
                "category": self.metadata.category,
                "icon": self.metadata.icon,
                "solver": self.metadata.solver,
                "turbulence": self.metadata.turbulence,
                "tags": self.metadata.tags,
            },
            "geometry_hint": self.geometry_hint,
            "max_cell_ratio": self.max_cell_ratio,
            "min_cell_ratio": self.min_cell_ratio,
            "detail": self.detail,
            "bl_enabled": self.bl_enabled,
            "bl_n_layers": self.bl_n_layers,
            "end_time": self.end_time,
            "delta_t": self.delta_t,
            "write_interval": self.write_interval,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TemplatePreset:
        md = data.get("metadata", {})
        return cls(
            metadata=TemplateMetadata(
                name=md.get("name", ""),
                description=md.get("description", ""),
                category=md.get("category", ""),
                icon=md.get("icon", "default"),
                solver=md.get("solver", "simpleFoam"),
                turbulence=md.get("turbulence", "kOmegaSST"),
                tags=md.get("tags", []),
            ),
            geometry_hint=data.get("geometry_hint", ""),
            max_cell_ratio=data.get("max_cell_ratio", 0.05),
            min_cell_ratio=data.get("min_cell_ratio", 0.005),
            detail=data.get("detail", "medium"),
            bl_enabled=data.get("bl_enabled", True),
            bl_n_layers=data.get("bl_n_layers", 5),
            end_time=data.get("end_time", 1000.0),
            delta_t=data.get("delta_t", 1.0),
            write_interval=data.get("write_interval", 100),
            extra=data.get("extra", {}),
        )


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------
_BUILTIN_PRESETS: list[TemplatePreset] = [
    TemplatePreset(
        metadata=TemplateMetadata(
            name="Internal Flow",
            description="Flusso interno in condotti, pipe, valvole — simpleFoam, kOmegaSST",
            category="internal_flow",
            icon="pipe",
            solver="simpleFoam",
            turbulence="kOmegaSST",
            tags=["interno", "tubo", "condotto", "RANS"],
        ),
        max_cell_ratio=0.04,
        min_cell_ratio=0.004,
        bl_n_layers=5,
        end_time=2000.0,
    ),
    TemplatePreset(
        metadata=TemplateMetadata(
            name="External Aerodynamics",
            description="Aerodinamica esterna: ala, fusoliera, veicolo — simpleFoam, kOmegaSST",
            category="external_aero",
            icon="airfoil",
            solver="simpleFoam",
            turbulence="kOmegaSST",
            tags=["esterno", "aero", "ala", "veicolo", "RANS"],
        ),
        max_cell_ratio=0.06,
        min_cell_ratio=0.003,
        bl_enabled=True,
        bl_n_layers=8,
        end_time=3000.0,
    ),
    TemplatePreset(
        metadata=TemplateMetadata(
            name="Conjugate Heat Transfer",
            description="Scambio termico fluido-solido — chtMultiRegionFoam, kOmegaSST",
            category="cht",
            icon="thermal",
            solver="chtMultiRegionFoam",
            turbulence="kOmegaSST",
            tags=["CHT", "termico", "fluido-solido", "accoppiato"],
        ),
        max_cell_ratio=0.04,
        min_cell_ratio=0.005,
        bl_n_layers=3,
        end_time=5000.0,
        delta_t=0.5,
    ),
    TemplatePreset(
        metadata=TemplateMetadata(
            name="Multi-Phase (VOF)",
            description="Flusso bifase con interfaccia libera: onda, dam break — interFoam",
            category="multiphase",
            icon="waves",
            solver="interFoam",
            turbulence="kEpsilon",
            tags=["multifase", "VOF", "interfaccia", "libero"],
        ),
        max_cell_ratio=0.03,
        min_cell_ratio=0.002,
        bl_enabled=False,
        end_time=500.0,
        delta_t=0.01,
        write_interval=10,
    ),
    TemplatePreset(
        metadata=TemplateMetadata(
            name="Moving Body (Overset)",
            description="Corpo in movimento: pistone, profilo oscillante — overPimpleDyMFoam",
            category="moving_body",
            icon="move",
            solver="overPimpleDyMFoam",
            turbulence="kOmegaSST",
            tags=["overset", "moving", "6DOF", "oscillante"],
        ),
        max_cell_ratio=0.04,
        min_cell_ratio=0.004,
        bl_n_layers=5,
        end_time=100.0,
        delta_t=0.01,
        write_interval=10,
    ),
    TemplatePreset(
        metadata=TemplateMetadata(
            name="Combustion",
            description="Combustione in camera: reactingFoam, EDC — reactingFoam",
            category="combustion",
            icon="flame",
            solver="reactingFoam",
            turbulence="kEpsilon",
            tags=["combustione", "reagente", "fiamma", "EDC"],
        ),
        max_cell_ratio=0.03,
        min_cell_ratio=0.002,
        bl_n_layers=3,
        end_time=100.0,
        delta_t=0.001,
        write_interval=10,
    ),
    TemplatePreset(
        metadata=TemplateMetadata(
            name="Heat Transfer (Solid)",
            description="Conduzione termica in solido: laplacianFoam",
            category="conjugate_ht",
            icon="thermometer",
            solver="laplacianFoam",
            turbulence="laminar",
            tags=["termico", "solido", "conduzione", "laplaciano"],
        ),
        max_cell_ratio=0.05,
        min_cell_ratio=0.01,
        bl_enabled=False,
        end_time=10000.0,
        delta_t=10.0,
        write_interval=100,
    ),
]


class TemplateEngine:
    """Template manager — lists built-in + user templates, applies presets.

    Usage::

        te = TemplateEngine()
        for t in te.list_templates():
            print(t.metadata.name, t.metadata.category)
        te.apply_template("Internal Flow", case_dir)
    """

    def __init__(self) -> None:
        self._user_dir = USER_TEMPLATES_DIR
        self._user_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------
    def list_templates(self, category: str = "") -> list[TemplatePreset]:
        """List all available templates, optionally filtered by category."""
        templates = list(_BUILTIN_PRESETS) + self._list_user_templates()
        if category:
            templates = [t for t in templates if t.metadata.category == category]
        return templates

    def list_categories(self) -> list[str]:
        """Return unique category names from all templates."""
        cats = {t.metadata.category for t in _BUILTIN_PRESETS}
        for t in self._list_user_templates():
            cats.add(t.metadata.category)
        return sorted(cats)

    def get_template(self, name: str) -> TemplatePreset | None:
        """Find a template by name (built-in or user)."""
        for t in self.list_templates():
            if t.metadata.name == name:
                return t
        return None

    # ------------------------------------------------------------------
    # Applying
    # ------------------------------------------------------------------
    def apply_template(
        self, template: TemplatePreset | str,
        case_dir: Path | str,
        geometry_path: str = "",
        bbox_dim: float | None = None,
    ) -> list[str]:
        """Apply a template preset to a case directory.

        Writes meshDict, controlDict, fvSchemes, fvSolution, and 0/ fields
        using the parameters from the template.

        Args:
            template: TemplatePreset object or name string.
            case_dir: Target OpenFOAM case directory.
            geometry_path: Optional geometry file to reference.

        Returns:
            List of files written.
        """
        if isinstance(template, str):
            found = self.get_template(template)
            if found is None:
                raise ValueError(f"Template '{template}' not found")
            template = found

        case_dir = Path(case_dir)
        files_written: list[str] = []

        from polyfoammesh.core.meshdict_gen import write_meshdict
        from polyfoammesh.commercial.solver_setup import (
            SolverSetup, SolverConfig, SolverType,
            TurbulenceModel, SchemePreset,
        )

        # meshDict's BL contract: thicknessRatio = growth ratio (>1),
        # firstLayerThickness = absolute metres (same bug found and fixed
        # elsewhere this session in watertight.py/fault_tolerant.py/
        # mesh_engine.py/optimizer.py — "thicknessRatio": 0.005 here is a
        # first-layer FRACTION, not a growth ratio; passed straight through
        # it gets clamped to a default and the first-layer size is dropped).
        # Also missing patch_names — without renameBoundary, every
        # inlet/outlet/wall patch reverts to cfMesh's default `wall` type.
        # By the time a template is applied, this case already has a real
        # mesh from an earlier pipeline step, so read its actual patch names
        # rather than needing fresh geometry.
        patch_names: list[str] | None = None
        try:
            from polyfoammesh.core.boundary_reader import parse_boundary
            boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
            if boundary_path.exists():
                patch_names = [p.name for p in parse_boundary(boundary_path)]
        except Exception as exc:
            logger.debug("apply_template: could not read existing patch names: %s", exc)

        # Convert ratio → absolute meters using bbox, or use ratio as-is
        # if bbox is unavailable (legacy behavior, assumes metric geometry)
        if bbox_dim and bbox_dim > 0:
            max_abs = max(template.max_cell_ratio * bbox_dim, 0.001)
            min_abs = max(template.min_cell_ratio * bbox_dim, 0.0001)
        else:
            max_abs = template.max_cell_ratio
            min_abs = template.min_cell_ratio
        write_meshdict(
            case_dir,
            max_cell_size=max_abs,
            min_cell_size=min_abs,
            bl_params={
                "nLayers": template.bl_n_layers,
                "thicknessRatio": 1.2,
                "firstLayerThickness": 0.005 * max_abs,
            } if template.bl_enabled else None,
            patch_names=patch_names,
        )
        files_written.append("system/meshDict")

        # Map template solver string to SolverType enum
        solver_map = {
            "simpleFoam": SolverType.SIMPLE_FOAM,
            "pimpleFoam": SolverType.PIMPLE_FOAM,
            "pisoFoam": SolverType.PISO_FOAM,
            "reactingFoam": SolverType.REACTING_FOAM,
            "chtMultiRegionFoam": SolverType.CHT_MULTI_REGION,
            "overPimpleDyMFoam": SolverType.OVER_PIMPLE,
        }
        turb_map = {
            "laminar": TurbulenceModel.LAMINAR,
            "kEpsilon": TurbulenceModel.K_EPSILON,
            "kOmegaSST": TurbulenceModel.K_OMEGA_SST,
        }

        solver_type = solver_map.get(template.metadata.solver, SolverType.SIMPLE_FOAM)
        turb_type = turb_map.get(template.metadata.turbulence, TurbulenceModel.K_OMEGA_SST)

        config = SolverConfig(
            solver=solver_type,
            turbulence=turb_type,
            schemes=SchemePreset.BILANCIATO,
            end_time=template.end_time,
            delta_t=template.delta_t,
            write_interval=template.write_interval,
        )

        ss = SolverSetup()
        ss.configure(config)
        files_written.extend(ss.write_all(case_dir))

        octo.log_event("template_engine", "template_applied", {
            "template": template.metadata.name,
            "case_dir": str(case_dir),
            "files": len(files_written),
        })
        return files_written

    # ------------------------------------------------------------------
    # User templates (save/load)
    # ------------------------------------------------------------------
    def save_user_template(self, preset: TemplatePreset) -> Path:
        """Save a user-defined template to disk."""
        safe_name = preset.metadata.name.replace(" ", "_").replace("/", "_")
        path = self._user_dir / f"{safe_name}.json"

        data = preset.to_dict()
        data["created_at"] = datetime.now().isoformat()
        path.write_text(json.dumps(data, indent=2, default=str))

        octo.log_event("template_engine", "user_template_saved", {
            "name": preset.metadata.name,
            "path": str(path),
        })
        return path

    def load_user_template(self, path: Path | str) -> TemplatePreset:
        """Load a user template from a JSON file."""
        path = Path(path)
        data = json.loads(path.read_text())
        return TemplatePreset.from_dict(data)

    def delete_user_template(self, name: str) -> bool:
        """Delete a user-saved template by name."""
        safe_name = name.replace(" ", "_").replace("/", "_")
        path = self._user_dir / f"{safe_name}.json"
        if path.exists():
            path.unlink()
            logger.info("User template deleted: %s", name)
            return True
        return False

    def _list_user_templates(self) -> list[TemplatePreset]:
        """Load all user-saved templates from disk."""
        templates: list[TemplatePreset] = []
        if not self._user_dir.exists():
            return templates
        for f in sorted(self._user_dir.glob("*.json")):
            try:
                templates.append(self.load_user_template(f))
            except Exception as exc:
                logger.warning("Failed to load user template %s: %s", f.name, exc)
        return templates
