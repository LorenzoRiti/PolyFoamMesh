"""Boundary Layer Quality Engine — inspired by ANSA, Pointwise T-Rex.

Provides automatic wall detection, y+ estimation, BL parameter
calculation, collision detection, and quality metrics for
prism-layer meshes in OpenFOAM.

Key capabilities:
  - Automatic wall patch detection (naming heuristics)
  - y+ estimation from flow conditions (Re, U_ref, turbulence model)
  - First-layer height calculation from target y+
  - BL collision detection (overlap ratio per wall)
  - Quality metrics: skewness, non-orthogonality, aspect ratio
"""

from __future__ import annotations

import math
import logging
from dataclasses import dataclass

from polyfoammesh.core.patch_roles import split_wall_patches
from polyfoammesh.octopoda_local import octo

logger = logging.getLogger(__name__)


# Common turbulence model constants
_TURBULENCE_MODELS = {
    "kEpsilon": {"yplus_target": 30, "yplus_max": 300},
    "kOmegaSST": {"yplus_target": 1, "yplus_max": 5},
    "LES": {"yplus_target": 1, "yplus_max": 1},
    "SpalartAllmaras": {"yplus_target": 1, "yplus_max": 10},
    "Laminar": {"yplus_target": 0, "yplus_max": 0},
}


@dataclass
class FlowConditions:
    """Flow conditions for y+ estimation."""
    reynolds_number: float = 1e6
    reference_velocity: float = 1.0  # m/s
    reference_length: float = 1.0  # m
    kinematic_viscosity: float = 1e-5  # m²/s (air ~1.5e-5)
    density: float = 1.225  # kg/m³ (air)
    turbulence_model: str = "kOmegaSST"

    @classmethod
    def from_velocity(
        cls,
        reference_velocity: float,
        reference_length: float,
        kinematic_viscosity: float = 1.5e-5,
        density: float = 1.225,
        turbulence_model: str = "kOmegaSST",
    ) -> "FlowConditions":
        """Build flow conditions with Re DERIVED from U·L/ν.

        `reynolds_number` is a plain field with a 1e6 default, so constructing
        FlowConditions(reference_velocity=..., reference_length=...) silently
        keeps that default instead of the Reynolds number those values imply —
        every downstream quantity (Cf, u_tau, first layer height) is then based
        on the wrong Re. Use this constructor whenever Re is not known directly.
        """
        nu = max(kinematic_viscosity, 1e-12)
        return cls(
            reynolds_number=reference_velocity * reference_length / nu,
            reference_velocity=reference_velocity,
            reference_length=reference_length,
            kinematic_viscosity=kinematic_viscosity,
            density=density,
            turbulence_model=turbulence_model,
        )


@dataclass
class BLParameters:
    """Calculated boundary layer parameters."""
    first_layer_height: float = 0.0  # m
    n_layers: int = 10
    growth_rate: float = 1.2
    total_thickness: float = 0.0  # m
    target_yplus: float = 1.0
    estimated_yplus: float = 0.0


@dataclass
class BLCollisionReport:
    """Collision detection results per patch."""
    patch_name: str = ""
    overlap_ratio: float = 0.0  # 0 = no overlap, 1 = fully overlapped
    max_thickness_ratio: float = 0.0  # BL thickness / local cell size
    status: str = "ok"  # ok | warning | critical


@dataclass
class BLQualityMetrics:
    """Quality metrics for a boundary layer mesh."""
    avg_skewness: float = 0.0
    max_skewness: float = 0.0
    avg_non_orthogonality: float = 0.0
    max_non_orthogonality: float = 0.0
    avg_aspect_ratio: float = 0.0
    max_aspect_ratio: float = 0.0
    min_orthogonality: float = 90.0

    def passed(self, thresholds: dict[str, float] | None = None) -> bool:
        thr = thresholds or {}
        return (
            self.max_skewness <= thr.get("skewness_max", 4.0)
            and self.max_non_orthogonality <= thr.get("non_ortho_max", 65.0)
            and self.max_aspect_ratio <= thr.get("aspect_max", 1000.0)
        )


class BLEngine:
    """Boundary layer parameter calculator and quality analyser.

    Usage::

        bl = BLEngine()
        params = bl.calculate_from_flow(FlowConditions(reynolds_number=1e7))
        print(f"First layer: {params.first_layer_height:.6f} m")
    """

    # Fluid property defaults
    _AIR_VISCOSITY: float = 1.5e-5   # m²/s
    _WATER_VISCOSITY: float = 1e-6   # m²/s

    def calculate_from_flow(
        self, flow: FlowConditions, growth_rate: float = 1.2,
    ) -> BLParameters:
        """Calculate BL parameters from flow conditions and target y+.

        Uses the flat-plate boundary layer correlation:
            y+ = y * u_tau / nu
            Cf = 0.027 / Re_x^(1/7)  (turbulent, 1/7th power law)
            u_tau = U_ref * sqrt(Cf/2)

        Args:
            flow: free-stream conditions; build it with
                ``FlowConditions.from_velocity`` unless you know Re directly.
            growth_rate: layer-to-layer expansion, clamped to [1.05, 1.5].
                The layer COUNT is derived from it and the 99% BL thickness.
        """
        model = _TURBULENCE_MODELS.get(flow.turbulence_model, _TURBULENCE_MODELS["kOmegaSST"])
        target_yplus = model["yplus_target"]

        Re = flow.reynolds_number
        if Re <= 0:
            raise ValueError(f"Reynolds number must be positive, got {Re}")

        # Skin friction coefficient (turbulent flat plate)
        Cf = 0.027 / (Re ** (1.0 / 7.0))
        u_tau = flow.reference_velocity * math.sqrt(Cf / 2.0)

        if u_tau <= 0:
            raise ValueError("Friction velocity is zero — check flow conditions")

        # First layer height from y+ definition
        first_layer = target_yplus * flow.kinematic_viscosity / u_tau

        # Clamp first_layer to a physically sensible range: min 1nm (numerical
        # limit for double-precision on mm-scale geometries), max 10% of ref
        # length (otherwise the first layer alone would be huge).
        if first_layer < 1e-9:
            logger.warning("first_layer=%.2e too small, clamped to 1e-9", first_layer)
            first_layer = 1e-9
        max_allowed_fl = flow.reference_length * 0.1
        if first_layer > max_allowed_fl:
            logger.warning(
                "first_layer=%.6f exceeds 10%% of ref_length (%.4f), clamped to %.6f",
                first_layer, flow.reference_length, max_allowed_fl,
            )
            first_layer = max_allowed_fl

        # Total BL thickness estimate (99% of free-stream)
        delta_99 = 0.37 * flow.reference_length / (Re ** 0.2)

        # Fix the growth rate and solve for the LAYER COUNT, not the other way
        # round. Pinning n_layers=10 and solving for r let the rate reach 2.0 —
        # each layer nearly doubling — which is far outside the 1.1-1.3 range
        # meshers and solvers expect and leaves a violent size jump where the
        # layers meet the bulk mesh.
        r = min(max(growth_rate, 1.05), 1.5)
        if first_layer > 0 and delta_99 > first_layer and r > 1.0:
            # total = h1 * (r^n - 1) / (r - 1)  ->  solve for n
            n_layers = int(
                math.ceil(math.log1p(delta_99 * (r - 1.0) / first_layer) / math.log(r))
            )
            n_layers = max(1, min(n_layers, 20))  # cap at 20 for reliability
        else:
            n_layers = 1

        total = first_layer * (r ** n_layers - 1) / (r - 1) if r > 1 else first_layer * n_layers

        # Final sanity: total BL should not exceed 30% of ref length
        max_total = flow.reference_length * 0.3
        if total > max_total:
            # Reduce n_layers until total fits or n_layers==1
            while n_layers > 1 and total > max_total:
                n_layers -= 1
                total = first_layer * (r ** n_layers - 1) / (r - 1) if r > 1 else first_layer * n_layers
            logger.warning(
                "Total BL thickness reduced to %d layers = %.6fm (cap at 30%% of ref_length=%.4f)",
                n_layers, total, flow.reference_length,
            )

        octo.log_event("bl_engine", "calculate_from_flow", {
            "Re": Re, "target_y+": target_yplus,
            "first_layer_m": round(first_layer, 8),
            "n_layers": n_layers,
            "total_bl_m": round(total, 6),
        })

        return BLParameters(
            first_layer_height=round(first_layer, 8),
            n_layers=n_layers,
            growth_rate=round(r, 4),
            total_thickness=round(total, 6),
            target_yplus=target_yplus,
            estimated_yplus=target_yplus,
        )

    def detect_wall_patches(self, patch_names: list[str]) -> list[str]:
        """Auto-detect wall patches by name heuristics.

        Delegates to ``patch_roles.split_wall_patches``. Returns an empty
        list when no patch name matches a wall keyword — never falls back
        to treating every patch as a wall (extruding prisms into an inlet
        or outlet is physically wrong).
        """
        walls, excluded = split_wall_patches(patch_names)
        if not walls:
            detail = ", ".join(f"{name} ({role})" for name, role in excluded)
            logger.warning(
                "No wall patches identified by name — BL skipped. "
                "Excluded: %s", detail or "none",
            )
        octo.log_event("bl_engine", "detect_walls", {"count": len(walls)})
        return walls

    def detect_collisions(
        self, bl_params: BLParameters,
        cell_size: float,
        patch_names: list[str],
    ) -> list[BLCollisionReport]:
        """Detect boundary layer collisions.

        For each patch, computes the ratio of BL total thickness to
        local cell size. When ratio > 0.5, layers may overlap across
        thin gaps.

        Args:
            bl_params: Calculated BL parameters.
            cell_size: Local cell size at the wall (m).
            patch_names: Names of patches to analyse.

        Returns:
            List of ``BLCollisionReport``, one per patch.
        """
        reports: list[BLCollisionReport] = []
        for name in patch_names:
            ratio = bl_params.total_thickness / max(cell_size, 1e-10)
            if ratio > 0.8:
                status = "critical"
            elif ratio > 0.5:
                status = "warning"
            else:
                status = "ok"

            reports.append(BLCollisionReport(
                patch_name=name,
                overlap_ratio=round(ratio, 3),
                max_thickness_ratio=round(ratio, 3),
                status=status,
            ))

        n_critical = sum(1 for r in reports if r.status == "critical")
        if n_critical:
            logger.warning("BL collision: %d patches at critical level", n_critical)

        return reports

    def suggest_remedy(self, report: BLCollisionReport) -> str:
        """Suggest a remedy for a collision report."""
        if report.status == "ok":
            return "No action needed."
        if report.status == "warning":
            return (
                f"Reduce nLayers or growth_rate on '{report.patch_name}'. "
                f"Current overlap ratio: {report.overlap_ratio:.2f}"
            )
        return (
            f"CRITICAL on '{report.patch_name}' (ratio={report.overlap_ratio:.2f}). "
            f"Disable BL on this patch or split into sub-layers."
        )

    @staticmethod
    def yplus_from_height(
        height: float, u_ref: float, nu: float, length: float,
    ) -> float:
        """Estimate y+ from a given first-layer height.

        Inverse of the flat-plate correlation.
        """
        Re = u_ref * length / max(nu, 1e-12)
        if Re <= 0:
            return 0.0
        Cf = 0.027 / (Re ** (1.0 / 7.0))
        u_tau = u_ref * math.sqrt(Cf / 2.0)
        return height * u_tau / max(nu, 1e-12)

    @staticmethod
    def suggest_n_layers(target_thickness: float, first_height: float,
                         growth: float = 1.2) -> int:
        """Suggest number of layers to achieve target BL thickness."""
        if first_height <= 0 or growth <= 1.0:
            return 5
        n = 1
        while n <= 50:
            total = first_height * (growth ** n - 1) / (growth - 1)
            if total >= target_thickness:
                return n
            n += 1
        return 50
