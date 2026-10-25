# -*- coding: utf-8 -*-
"""Single, validated boundary-layer configuration (W7).

Why this module exists
----------------------
Before W7 the GUI built a plain ``dict`` with three keys, the worker filled
the missing ones from its own defaults, and the engine had a third set of
defaults.  ``minBaseEdgeFraction`` and ``terminateByBudget`` were hardcoded
in the panel, ``edgeCapPercentile`` was supported by the engine but no GUI
code ever sent it, and the numeric values were not persisted at all.

``BLConfig`` is now the only object that crosses the GUI -> worker -> engine
boundary.  It owns:

- canonical units (first layer is metres when ``first_height > 0``,
  otherwise ``first_layer_fraction * max_cell``, resolved in ONE place);
- the allowed ranges and the declared clamps (``normalized()`` returns one
  message per clamp);
- per-field provenance (``preset`` / ``user`` / ``default`` / ``clamped``);
- a versioned serialization (``schema_version``) used by presets and by the
  ``bl_config.json`` run record;
- a tolerant legacy adapter for the old project/session keys.

The engine is unchanged: ``engine_kwargs()`` produces exactly the keyword
arguments ``PolyBoundaryLayerEngine.run`` already accepts.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)

#: Version of the serialized BL configuration.  Bump only with a migration.
SCHEMA_VERSION = 1

#: Provenance labels.  ``clamped`` is written by ``normalized()`` when a
#: value had to be brought inside its documented range.
PROVENANCE_VALUES = ("preset", "user", "default", "clamped")

_LOCAL_TERMINATION_MODES = ("off", "decoupled", "decoupled_vertex")
_FLUIDS = ("air", "water")
_WALL_TREATMENTS = ("wall_functions", "wall_resolved")

#: Human-readable ranges, used in error messages and tooltips.
FIELD_RANGES: dict[str, tuple[float, float]] = {
    "n_layers": (1, 40),
    "first_height": (0.0, 10.0),
    "first_layer_fraction": (1e-9, 1.0),
    "growth_rate": (1.0, 2.0),
    "min_base_edge_fraction": (0.0, 2.0),
    "edge_cap_percentile": (0.0, 100.0),
    "local_max_rounds": (1, 10),
    "velocity": (0.001, 1000.0),
}

_FIELD_ORDER = (
    "enabled", "n_layers", "first_height", "first_layer_fraction",
    "growth_rate", "apply_to_all", "min_base_edge_fraction",
    "terminate_by_budget", "edge_cap_percentile", "local_termination",
    "local_height_retry", "local_max_rounds", "standard_only", "velocity", "fluid",
    "wall_treatment",
)

_BOOL_FIELDS = frozenset({
    "enabled", "apply_to_all", "terminate_by_budget", "local_height_retry",
    "standard_only",
})

#: Canonical name -> accepted aliases (legacy project, CLI, JSON).
_ALIASES: dict[str, tuple[str, ...]] = {
    "enabled": ("bl_enabled", "blEnabled"),
    "n_layers": ("nLayers", "bl_n_layers", "layers"),
    "first_height": ("firstLayerThickness", "first_height_m", "bl_first_height"),
    "first_layer_fraction": ("bl_thickness_ratio", "first_layer_fraction"),
    "growth_rate": (
        "thicknessRatio", "growthRate", "bl_expansion_ratio", "expansionRatio",
    ),
    "apply_to_all": ("applyToAll",),
    "min_base_edge_fraction": ("minBaseEdgeFraction",),
    "terminate_by_budget": ("terminateByBudget",),
    "edge_cap_percentile": ("edgeCapPercentile",),
    "local_termination": ("localTermination",),
    "local_height_retry": ("localHeightRetry",),
    "local_max_rounds": ("localMaxRounds",),
    "standard_only": ("standardOnly", "bl_standard_only"),
    "velocity": ("bl_velocity",),
    "fluid": ("bl_fluid",),
    "wall_treatment": ("wallTreatment", "bl_wall_treatment"),
}

#: The old session wrote a FRACTION under a name that says "ratio".
_LEGACY_FRACTION_KEYS = ("bl_thickness_ratio",)


class BLConfigError(ValueError):
    """Raised by the strict API when the config cannot be used."""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        out = float(value)
        return out if math.isfinite(out) else None
    if isinstance(value, str):
        try:
            out = float(value.strip())
        except ValueError:
            return None
        return out if math.isfinite(out) else None
    return None


def _canonical_fluid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    low = value.strip().lower()
    if "water" in low or low == "acqua":
        return "water"
    if "air" in low or low == "aria":
        return "air"
    return None


def _canonical_wall_treatment(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    low = value.strip().lower().replace("-", " ").replace("_", " ")
    if "resolved" in low:
        return "wall_resolved"
    if "wall function" in low or "wallfunc" in low:
        return "wall_functions"
    return None


def _canonical_local_termination(value: Any) -> str | None:
    if isinstance(value, bool):
        return "decoupled" if value else "off"
    if value is None:
        return "off"
    if not isinstance(value, str):
        return None
    low = value.strip().lower().replace("-", "_").replace(" ", "_")
    if low in ("off", "false", "none", "no", "0"):
        return "off"
    if low in ("decoupled", "true", "yes", "1"):
        return "decoupled"
    if low in ("decoupled_vertex", "vertex", "per_vertex"):
        return "decoupled_vertex"
    return None


@dataclass
class BLConfig:
    """Validated boundary-layer settings shared by GUI, CLI and worker."""

    enabled: bool = True
    n_layers: int = 3
    #: Absolute first-layer height in metres.  ``0`` means "use the
    #: fraction"; a positive value is authoritative (physics-derived case).
    first_height: float = 0.0
    #: MEASURED historical effective default.  The panel used to call
    #: setValue(0.005) before setDecimals(6), so Qt rounded it to 0.01; the
    #: W7 refactor keeps the value users actually got (no default change).
    first_layer_fraction: float = 0.01
    growth_rate: float = 1.2
    apply_to_all: bool = False
    min_base_edge_fraction: float = 0.9
    terminate_by_budget: bool = True
    edge_cap_percentile: float = 0.0
    local_termination: str = "off"
    local_height_retry: bool = False
    local_max_rounds: int = 3
    #: Standard-only policy: skip the worker's automatic quality retry
    #: (fTetWild path) and run exactly the standard attempt.  Faster and
    #: fully manual; the measured quality retry is off for this run.
    standard_only: bool = False
    velocity: float = 10.0
    fluid: str = "air"
    wall_treatment: str = "wall_functions"
    #: Per-field origin.  Excluded from equality so two configs with the same
    #: values compare equal regardless of where they came from.
    provenance: dict[str, str] = field(
        default_factory=dict, repr=False, compare=False,
    )

    def __post_init__(self) -> None:
        prov = dict(self.provenance or {})
        for name in _FIELD_ORDER:
            source = str(prov.get(name, "default"))
            prov[name] = source if source in PROVENANCE_VALUES else "default"
        self.provenance = prov

    # ------------------------------------------------------------------
    # Derived quantities
    # ------------------------------------------------------------------
    @classmethod
    def defaults(cls) -> "BLConfig":
        """A fresh default config (single source of the defaults)."""
        return cls()

    @property
    def first_height_mode(self) -> str:
        return "absolute" if float(self.first_height) > 0.0 else "fraction"

    @property
    def y_plus_target(self) -> float:
        return 1.0 if self.wall_treatment == "wall_resolved" else 30.0

    def resolved_first_height(self, max_cell: float) -> float:
        """First-layer height in metres, the ONLY unit conversion."""
        if float(self.first_height) > 0.0:
            return float(self.first_height)
        if not _is_number(max_cell) or float(max_cell) <= 0.0:
            raise BLConfigError(
                "max_cell must be > 0 to resolve the first-layer fraction"
            )
        value = float(self.first_layer_fraction) * float(max_cell)
        if not math.isfinite(value) or value <= 0.0:
            raise BLConfigError(
                "resolved first-layer height is not a positive finite number"
            )
        return value

    def total_thickness(self, max_cell: float) -> float:
        h1 = self.resolved_first_height(max_cell)
        n = max(1, int(self.n_layers))
        r = float(self.growth_rate)
        if r > 1.0:
            return h1 * (r ** n - 1.0) / (r - 1.0)
        return h1 * n

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(self) -> list[str]:
        """Return every problem as ``"field: message"``; never mutates."""
        problems: list[str] = []

        for name in _BOOL_FIELDS:
            if not isinstance(getattr(self, name), bool):
                problems.append(f"{name}: must be a boolean")

        if not isinstance(self.n_layers, int) or isinstance(self.n_layers, bool):
            problems.append("n_layers: must be an integer")
        elif not 1 <= self.n_layers <= 40:
            problems.append("n_layers: must be in 1..40")

        for name in (
            "first_height", "first_layer_fraction", "growth_rate",
            "min_base_edge_fraction", "edge_cap_percentile", "velocity",
        ):
            value = getattr(self, name)
            if not _is_number(value) or not math.isfinite(float(value)):
                problems.append(f"{name}: must be a finite number")
                continue
            lo, hi = FIELD_RANGES[name]
            if not lo <= float(value) <= hi:
                problems.append(f"{name}: must be in {lo:g}..{hi:g}")

        if not isinstance(self.local_max_rounds, int) or isinstance(
            self.local_max_rounds, bool
        ):
            problems.append("local_max_rounds: must be an integer")
        elif not 1 <= self.local_max_rounds <= 10:
            problems.append("local_max_rounds: must be in 1..10")

        if self.local_termination not in _LOCAL_TERMINATION_MODES:
            problems.append(
                "local_termination: must be one of "
                + ", ".join(_LOCAL_TERMINATION_MODES)
            )
        elif self.local_height_retry and (
            self.local_termination != "decoupled_vertex"
        ):
            problems.append(
                "local_height_retry: only valid with "
                "local_termination='decoupled_vertex'"
            )

        if self.fluid not in _FLUIDS:
            problems.append("fluid: must be 'air' or 'water'")
        if self.wall_treatment not in _WALL_TREATMENTS:
            problems.append(
                "wall_treatment: must be 'wall_functions' or 'wall_resolved'"
            )
        return problems

    def raise_if_invalid(self) -> None:
        problems = self.validate()
        if problems:
            raise BLConfigError("; ".join(problems))

    def normalized(self) -> tuple["BLConfig", list[str]]:
        """Clamp every out-of-range value and declare every clamp.

        This is the tolerant path used for presets and legacy projects.  A
        strict caller should call :meth:`validate` / :meth:`raise_if_invalid`
        instead: no value is ever changed silently there.
        """
        values: dict[str, Any] = {}
        warnings: list[str] = []
        prov = dict(self.provenance)

        def _warn(name: str, old: Any, new: Any, why: str) -> None:
            warnings.append(
                f"{name}: {old!r} {why} -> {new!r}"
            )
            prov[name] = "clamped"

        for name in _BOOL_FIELDS:
            raw = getattr(self, name)
            parsed = _as_bool(raw)
            if parsed is None:
                parsed = getattr(BLConfig(), name)
                _warn(name, raw, parsed, "is not a boolean")
            values[name] = parsed

        raw = self.n_layers
        parsed = _as_int(raw)
        if parsed is None:
            parsed = BLConfig().n_layers
            _warn("n_layers", raw, parsed, "is not an integer")
        lo, hi = FIELD_RANGES["n_layers"]
        clamped = min(max(parsed, int(lo)), int(hi))
        if clamped != parsed:
            _warn("n_layers", parsed, clamped, f"outside {int(lo)}..{int(hi)}")
        values["n_layers"] = clamped

        for name in (
            "first_height", "first_layer_fraction", "growth_rate",
            "min_base_edge_fraction", "edge_cap_percentile", "velocity",
        ):
            raw = getattr(self, name)
            parsed = _as_float(raw)
            if parsed is None:
                parsed = getattr(BLConfig(), name)
                _warn(name, raw, parsed, "is not a finite number")
            lo, hi = FIELD_RANGES[name]
            clamped = min(max(parsed, float(lo)), float(hi))
            if clamped != parsed:
                _warn(name, parsed, clamped, f"outside {lo:g}..{hi:g}")
            values[name] = clamped

        raw = self.local_max_rounds
        parsed = _as_int(raw)
        if parsed is None:
            parsed = BLConfig().local_max_rounds
            _warn("local_max_rounds", raw, parsed, "is not an integer")
        lo, hi = FIELD_RANGES["local_max_rounds"]
        clamped = min(max(parsed, int(lo)), int(hi))
        if clamped != parsed:
            _warn(
                "local_max_rounds", parsed, clamped,
                f"outside {int(lo)}..{int(hi)}",
            )
        values["local_max_rounds"] = clamped

        mode = _canonical_local_termination(self.local_termination)
        if mode is None:
            mode = BLConfig().local_termination
            _warn(
                "local_termination", self.local_termination, mode,
                "is not a known mode",
            )
        values["local_termination"] = mode

        retry = _as_bool(self.local_height_retry)
        if retry is None:
            retry = BLConfig().local_height_retry
            _warn(
                "local_height_retry", self.local_height_retry, retry,
                "is not a boolean",
            )
        if retry and mode != "decoupled_vertex":
            _warn(
                "local_height_retry", retry, False,
                "requires local_termination='decoupled_vertex'",
            )
            retry = False
        values["local_height_retry"] = retry

        fluid = _canonical_fluid(self.fluid)
        if fluid is None:
            fluid = BLConfig().fluid
            _warn("fluid", self.fluid, fluid, "is not a known fluid")
        values["fluid"] = fluid

        wall = _canonical_wall_treatment(self.wall_treatment)
        if wall is None:
            wall = BLConfig().wall_treatment
            _warn(
                "wall_treatment", self.wall_treatment, wall,
                "is not a known wall treatment",
            )
        values["wall_treatment"] = wall

        fixed = replace(self, **values, provenance=prov)
        return fixed, warnings

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def to_mapping(self, include_provenance: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
        for name in _FIELD_ORDER:
            data[name] = getattr(self, name)
        if include_provenance:
            data["provenance"] = {
                name: self.provenance.get(name, "default")
                for name in _FIELD_ORDER
            }
        return data

    # Aliases used by older call sites/tests.
    to_dict = to_mapping

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_mapping(), indent=indent, sort_keys=False)

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any] | "BLConfig",
        *,
        max_cell: float | None = None,
        strict: bool = True,
    ) -> "BLConfig":
        """Build a config from canonical or legacy keys.

        Strict (default) raises :class:`BLConfigError` on bad values.  Use
        :meth:`from_mapping_tolerant` for project/preset loading, where a
        damaged entry must never prevent the app from starting.
        """
        if isinstance(data, BLConfig):
            cfg = replace(data, provenance=dict(data.provenance))
            if strict:
                cfg.raise_if_invalid()
            return cfg
        cfg, warnings = _extract(data, max_cell=max_cell)
        if strict:
            if warnings:
                # Type/alias warnings describe a value we had to repair.
                raise BLConfigError("; ".join(warnings))
            cfg.raise_if_invalid()
            return cfg
        return cfg

    @classmethod
    def from_mapping_tolerant(
        cls,
        data: Mapping[str, Any] | "BLConfig",
        *,
        max_cell: float | None = None,
    ) -> tuple["BLConfig", list[str]]:
        """Never raise while loading a project/preset; clamp and report."""
        if isinstance(data, BLConfig):
            cfg = replace(data, provenance=dict(data.provenance))
            return cfg.normalized()
        try:
            cfg, warnings = _extract(data, max_cell=max_cell)
            fixed, clamp_warnings = cfg.normalized()
        except BLConfigError as exc:
            # A project/preset loader must always return a usable object.
            return BLConfig(), [f"invalid BL config: {exc}"]
        return fixed, warnings + clamp_warnings

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], **kw) -> "BLConfig":
        return cls.from_mapping(data, **kw)

    @classmethod
    def from_json(cls, text: str, **kw) -> "BLConfig":
        try:
            data = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise BLConfigError(f"invalid BL config JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise BLConfigError("BL config JSON must be an object")
        return cls.from_mapping(data, **kw)

    # ------------------------------------------------------------------
    # Adapters used by the rest of the app
    # ------------------------------------------------------------------
    def engine_kwargs(self, max_cell: float) -> dict[str, Any]:
        """Exactly the kwargs ``PolyBoundaryLayerEngine.run`` consumes."""
        self.raise_if_invalid()
        return {
            "n_layers": int(self.n_layers),
            "first_height": self.resolved_first_height(max_cell),
            "growth_rate": float(self.growth_rate),
            "min_base_edge_fraction": float(self.min_base_edge_fraction),
            "terminate_by_budget": bool(self.terminate_by_budget),
            "edge_cap_percentile": float(self.edge_cap_percentile),
            "local_termination": (
                False if self.local_termination == "off"
                else self.local_termination
            ),
            "local_height_retry": bool(self.local_height_retry),
            "local_max_rounds": int(self.local_max_rounds),
        }

    def legacy_meshdict_dict(
        self,
        max_cell: float,
        wall_patches: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Backward-compatible dict for the cfMesh meshDict writer.

        The legacy keys are kept because `meshDict` and the autopoly bridge
        read them.  ``enabled`` and ``maxThickness`` used to be read without
        ever being written (dead bindings); they are now real.
        """
        data: dict[str, Any] = {
            "enabled": bool(self.enabled),
            "nLayers": int(self.n_layers),
            "thicknessRatio": float(self.growth_rate),
            "firstLayerThickness": self.resolved_first_height(max_cell),
            "applyToAll": bool(self.apply_to_all),
            "minBaseEdgeFraction": float(self.min_base_edge_fraction),
            "terminateByBudget": bool(self.terminate_by_budget),
            "edgeCapPercentile": float(self.edge_cap_percentile),
            "localTermination": self.local_termination,
            "localHeightRetry": bool(self.local_height_retry),
            "localMaxRounds": int(self.local_max_rounds),
            "maxThickness": self.total_thickness(max_cell),
            "velocity": float(self.velocity),
            "fluid": self.fluid,
            "wallTreatment": self.wall_treatment,
        }
        if wall_patches is not None:
            data["wallPatches"] = list(wall_patches)
        return data

    def summary(self) -> str:
        """One-line effective-parameter record for the run log."""
        mode = self.first_height_mode
        h1 = (
            f"{self.first_height:.6g} m"
            if mode == "absolute"
            else f"{self.first_layer_fraction:.6g} x max_cell"
        )
        return (
            f"nLayers={self.n_layers} first={h1} growth={self.growth_rate:g} "
            f"apply_all={self.apply_to_all} "
            f"minBaseEdgeFraction={self.min_base_edge_fraction:g} "
            f"terminateByBudget={self.terminate_by_budget} "
            f"edgeCapPercentile={self.edge_cap_percentile:g} "
            f"localTermination={self.local_termination} "
            f"heightRetry={self.local_height_retry} "
            f"rounds={self.local_max_rounds} "
            f"standardOnly={self.standard_only} "
            f"wallTreatment={self.wall_treatment}"
        )

    def provenance_summary(self) -> str:
        return ", ".join(
            f"{name}={self.provenance.get(name, 'default')}"
            for name in _FIELD_ORDER
        )

    def with_provenance(self, source: str) -> "BLConfig":
        """Return a copy where every field is attributed to *source*."""
        if source not in PROVENANCE_VALUES:
            raise BLConfigError(f"unknown provenance {source!r}")
        return replace(
            self, provenance={name: source for name in _FIELD_ORDER},
        )

    def write_run_json(
        self,
        case_dir: Path | str,
        max_cell: float,
        *,
        warnings: Iterable[str] = (),
        extra: Mapping[str, Any] | None = None,
        filename: str = "bl_config.json",
    ) -> Path:
        """Write the effective config + provenance next to the case.

        Atomic (``os.replace``) so a crash cannot leave a half-written
        record; a failure is logged, never propagated into the mesh run.
        """
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "written_at": datetime.now(timezone.utc).isoformat(),
            "effective": self.to_mapping(),
            "first_height_resolved_m": self.resolved_first_height(max_cell),
            "warnings": list(warnings),
        }
        if extra:
            payload["run"] = dict(extra)
        path = Path(case_dir) / filename
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:  # never break a mesh run for telemetry
            logger.warning("Could not write %s: %s", path, exc)
        return path


# ---------------------------------------------------------------------------
# Legacy extraction
# ---------------------------------------------------------------------------
def _first_present(data: Mapping[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return None


def _extract(
    data: Mapping[str, Any], *, max_cell: float | None = None,
) -> tuple[BLConfig, list[str]]:
    """Parse canonical/legacy keys into a config + repair warnings."""
    if not isinstance(data, Mapping):
        raise BLConfigError("BL config must be a mapping")
    warnings: list[str] = []
    cfg = BLConfig()
    values: dict[str, Any] = {}
    provenance = {name: "default" for name in _FIELD_ORDER}
    found_legacy_fraction = False
    absolute_first: float | None = None

    # Schema forward-compatibility: never interpret a newer schema silently.
    schema = data.get("schema_version")
    if schema is not None:
        try:
            schema_i = int(schema)
        except (TypeError, ValueError):
            warnings.append(f"schema_version {schema!r} is not an integer")
        else:
            if schema_i > SCHEMA_VERSION:
                warnings.append(
                    f"schema_version {schema_i} is newer than supported "
                    f"{SCHEMA_VERSION}; unknown fields were ignored"
                )

    # The old growth key doubled as a first-layer fraction (<= 1).  Detect
    # and ignore that misuse instead of feeding it to the growth ratio.
    raw_growth = _first_present(
        data, ("growth_rate", "growthRate", "thicknessRatio",
               "bl_expansion_ratio", "expansionRatio"),
    )
    if raw_growth is not None:
        growth_num = _as_float(raw_growth)
        if growth_num is not None and growth_num <= 1.0:
            if growth_num > 0.0:
                # Historical bug: a fraction was passed as growth.
                warnings.append(
                    f"growth: {growth_num!r} is not a growth ratio (>1); "
                    "using the default 1.2"
                )
            raw_growth = None

    explicit_fraction = _first_present(
        data, ("first_layer_fraction", "bl_thickness_ratio"),
    )
    if "bl_thickness_ratio" in data and data.get("bl_thickness_ratio") is not None:
        found_legacy_fraction = True

    raw_first = _first_present(
        data, ("first_height", "firstLayerThickness", "first_height_m",
               "bl_first_height"),
    )
    if raw_first is not None:
        num = _as_float(raw_first)
        if num is not None and num >= 0.0:
            absolute_first = num
        else:
            warnings.append(f"first_height: {raw_first!r} is not a valid length")

    # Fraction: canonical key is taken as-is; the legacy session key is a
    # fraction of max cell and is converted ONCE when max_cell is known.
    if explicit_fraction is not None:
        num = _as_float(explicit_fraction)
        if num is None:
            warnings.append(
                f"first_layer_fraction: {explicit_fraction!r} is not a number"
            )
        else:
            values["first_layer_fraction"] = num
            provenance["first_layer_fraction"] = "user"
            if found_legacy_fraction and absolute_first is None and max_cell:
                converted = num * float(max_cell)
                absolute_first = converted
                provenance["first_height"] = "user"
                provenance["first_layer_fraction"] = "user"

    if absolute_first is not None:
        values["first_height"] = absolute_first
        provenance["first_height"] = "user"
        if "first_height" not in data and "firstLayerThickness" in data:
            provenance["first_height"] = "user"

    # Simple scalars/bools/enums through the alias table.
    for name, aliases in _ALIASES.items():
        if name in ("first_height", "first_layer_fraction", "growth_rate"):
            continue
        raw = _first_present(data, (name, *aliases))
        if raw is None:
            continue
        if name in _BOOL_FIELDS:
            parsed = _as_bool(raw)
            if parsed is None:
                warnings.append(f"{name}: {raw!r} is not a boolean")
                continue
            values[name] = parsed
        elif name in ("n_layers", "local_max_rounds"):
            parsed = _as_int(raw)
            if parsed is None:
                warnings.append(f"{name}: {raw!r} is not an integer")
                continue
            values[name] = parsed
        elif name == "local_termination":
            parsed = _canonical_local_termination(raw)
            if parsed is None:
                warnings.append(
                    f"local_termination: {raw!r} is not a known mode"
                )
                continue
            values[name] = parsed
        elif name == "fluid":
            parsed = _canonical_fluid(raw)
            if parsed is None:
                warnings.append(f"fluid: {raw!r} is not a known fluid")
                continue
            values[name] = parsed
        elif name == "wall_treatment":
            parsed = _canonical_wall_treatment(raw)
            if parsed is None:
                warnings.append(
                    f"wall_treatment: {raw!r} is not a known treatment"
                )
                continue
            values[name] = parsed
        else:  # numeric
            parsed = _as_float(raw)
            if parsed is None:
                warnings.append(f"{name}: {raw!r} is not a number")
                continue
            values[name] = parsed
        provenance[name] = "user"

    if raw_growth is not None:
        num = _as_float(raw_growth)
        if num is not None:
            values["growth_rate"] = num
            provenance["growth_rate"] = "user"

    # Explicit provenance from a serialized config wins over "user".
    raw_prov = data.get("provenance") or data.get("_provenance") or {}
    if isinstance(raw_prov, Mapping):
        for name in _FIELD_ORDER:
            src = raw_prov.get(name)
            if src in PROVENANCE_VALUES:
                provenance[name] = str(src)

    cfg = replace(cfg, **values, provenance=provenance)
    return cfg, warnings


# ---------------------------------------------------------------------------
# CLI (same object, so headless runs cannot drift from the GUI)
# ---------------------------------------------------------------------------
def _bool_cli(value: str) -> bool:
    parsed = _as_bool(value)
    if parsed is None:
        raise argparse.ArgumentTypeError(
            f"expected on/off/true/false, got {value!r}"
        )
    return parsed


def add_cli_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the canonical BL flags to a headless parser."""
    group = parser.add_argument_group("boundary layer")
    group.add_argument(
        "--bl-config", default=None,
        help="JSON file (or inline JSON) with a serialized BLConfig",
    )
    group.add_argument("--bl-enabled", type=_bool_cli, default=None)
    group.add_argument("--bl-n-layers", type=int, default=None)
    group.add_argument(
        "--bl-first-height", type=float, default=None,
        help="absolute first-layer height in metres",
    )
    group.add_argument(
        "--bl-first-layer-fraction", type=float, default=None,
        help="first layer as a fraction of max cell (used when --bl-first-height is 0)",
    )
    group.add_argument("--bl-growth", type=float, default=None)
    group.add_argument("--bl-apply-to-all", type=_bool_cli, default=None)
    group.add_argument("--bl-min-base-edge-fraction", type=float, default=None)
    group.add_argument("--bl-terminate-by-budget", type=_bool_cli, default=None)
    group.add_argument("--bl-edge-cap-percentile", type=float, default=None)
    group.add_argument(
        "--bl-local-termination", choices=_LOCAL_TERMINATION_MODES,
        default=None,
    )
    group.add_argument(
        "--bl-local-height-retry", action="store_true", default=None,
        help="only valid together with --bl-local-termination=decoupled_vertex",
    )
    group.add_argument("--bl-local-max-rounds", type=int, default=None)
    group.add_argument("--bl-velocity", type=float, default=None)
    group.add_argument("--bl-fluid", choices=_FLUIDS, default=None)
    group.add_argument(
        "--bl-wall-treatment", choices=_WALL_TREATMENTS, default=None,
    )


def config_from_cli_args(
    args: argparse.Namespace,
    *,
    max_cell: float | None = None,
) -> BLConfig:
    """Build the same BLConfig the GUI would build for these flags."""
    raw = getattr(args, "bl_config", None)
    if raw:
        text = str(raw)
        if text.lstrip().startswith("{"):
            cfg, _warnings = BLConfig.from_mapping_tolerant(
                json.loads(text), max_cell=max_cell,
            )
        else:
            path = Path(text)
            data = json.loads(path.read_text(encoding="utf-8"))
            cfg, _warnings = BLConfig.from_mapping_tolerant(
                data, max_cell=max_cell,
            )
    else:
        cfg = BLConfig()

    overrides: dict[str, Any] = {}
    attr_map = {
        "enabled": "bl_enabled",
        "n_layers": "bl_n_layers",
        "first_height": "bl_first_height",
        "first_layer_fraction": "bl_first_layer_fraction",
        "growth_rate": "bl_growth",
        "apply_to_all": "bl_apply_to_all",
        "min_base_edge_fraction": "bl_min_base_edge_fraction",
        "terminate_by_budget": "bl_terminate_by_budget",
        "edge_cap_percentile": "bl_edge_cap_percentile",
        "local_termination": "bl_local_termination",
        "local_height_retry": "bl_local_height_retry",
        "local_max_rounds": "bl_local_max_rounds",
        "velocity": "bl_velocity",
        "fluid": "bl_fluid",
        "wall_treatment": "bl_wall_treatment",
    }
    provenance = dict(cfg.provenance)
    for field_name, attr in attr_map.items():
        value = getattr(args, attr, None)
        if value is None:
            continue
        overrides[field_name] = value
        provenance[field_name] = "user"
    cfg = replace(cfg, **overrides, provenance=provenance)
    cfg, _warnings = cfg.normalized()
    cfg.raise_if_invalid()
    return cfg


def main(argv: list[str] | None = None) -> int:
    """``python -m polyfoammesh.core.bl_config`` — validate/print a config."""
    parser = argparse.ArgumentParser(
        prog="python -m polyfoammesh.core.bl_config",
        description="Validate a boundary-layer configuration and print its "
                    "effective engine keyword arguments.",
    )
    add_cli_arguments(parser)
    parser.add_argument(
        "--max-cell", type=float, default=0.05,
        help="max cell size used to resolve the first-layer fraction (m)",
    )
    parser.add_argument(
        "--print", choices=("config", "engine"), default="config",
    )
    args = parser.parse_args(argv)
    cfg = config_from_cli_args(args, max_cell=args.max_cell)
    if args.print == "engine":
        print(json.dumps(cfg.engine_kwargs(args.max_cell), indent=2))
    else:
        print(cfg.to_json())
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
