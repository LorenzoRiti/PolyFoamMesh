# -*- coding: utf-8 -*-
"""One versioned mesh configuration shared by GUI, project, CLI and worker.

W7 introduced ``BLConfig`` for the boundary-layer subset.  W10 needs the
whole *mesh* preset (detail slider, mesher, adaptive/fTetWild switches and
the BL snapshot) in one object, so a preset cannot silently drift from the
project state or from the CLI.

Design decisions:

- no absolute cell sizes in the preset: max/min are derived from the detail
  slider and the geometry, so persisting them would make a preset
  geometry-specific.  The slider (0..20) is the portable sizing input;
- ``BLConfig`` stays the nested boundary-layer model (no second BL language);
- every serialized object has ``schema_version`` and per-field provenance;
- the tolerant loader accepts the old QSettings keys and the old
  ``TemplatePreset`` fields through a single adapter, with warnings.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from polyfoammesh.core.bl_config import BLConfig

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

MESHERS = (
    "gmsh_direct_poly", "ftetwild_poly", "cfmesh", "gmsh_direct", "auto_tet",
)
DETAILS = ("very_coarse", "coarse", "medium", "fine", "very_fine")

#: Single slider -> cell-target mapping (log 10K..20M).  The GUI delegates
#: here instead of keeping a second copy of the scale.
CELL_TARGET_MIN = 10_000
CELL_TARGET_MAX = 20_000_000
CELL_TARGET_BUCKETS = 20


def target_cells_for_slider(value: int) -> int:
    t = max(0, min(CELL_TARGET_BUCKETS, int(value))) / CELL_TARGET_BUCKETS
    cells = CELL_TARGET_MIN * (CELL_TARGET_MAX / CELL_TARGET_MIN) ** t
    return int(round(cells))
_DETAIL_TO_SLIDER = {
    "very_coarse": 2, "coarse": 4, "medium": 10, "fine": 16, "very_fine": 19,
}
_SLIDER_TO_DETAIL = {
    0: "very_coarse", 1: "very_coarse", 2: "very_coarse",
    3: "coarse", 4: "coarse", 5: "coarse", 6: "coarse", 7: "coarse",
    8: "medium", 9: "medium", 10: "medium", 11: "medium", 12: "medium",
    13: "fine", 14: "fine", 15: "fine", 16: "fine", 17: "fine",
    18: "very_fine", 19: "very_fine", 20: "very_fine",
}
_FIELD_ORDER = (
    "detail_slider", "mesher", "adaptive_sizing", "ftet_eps_frac",
    "ftet_adaptive", "ftet_simplify",
)
_PROVENANCE_VALUES = ("default", "user", "preset", "clamped")

#: QSettings keys read by the legacy adapter.  New writes use only
#: ``params/mesh_config``.
LEGACY_SETTINGS_KEYS = (
    "params/detail_slider", "params/bl_checked", "params/bl_config",
)


class MeshConfigError(ValueError):
    """Raised by the strict API when the mesh config cannot be used."""


def detail_from_slider(value: int) -> str:
    return _SLIDER_TO_DETAIL.get(max(0, min(20, int(value))), "medium")


def slider_from_detail(detail: str) -> int:
    return _DETAIL_TO_SLIDER.get(str(detail).strip().lower(), 10)


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
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str):
        try:
            val = float(value.strip())
        except ValueError:
            return None
        return val if math.isfinite(val) else None
    return None


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


@dataclass
class MeshConfig:
    """Portable mesh configuration (no geometry-specific cell sizes)."""

    detail_slider: int = 10
    mesher: str = "gmsh_direct_poly"
    adaptive_sizing: bool = True
    ftet_eps_frac: float = 0.15
    ftet_adaptive: bool = True
    ftet_simplify: bool = False
    #: GUI default is BL off; built-in presets opt in explicitly.
    bl: BLConfig = field(default_factory=lambda: BLConfig(enabled=False))
    provenance: dict[str, str] = field(
        default_factory=dict, repr=False, compare=False,
    )

    def __post_init__(self) -> None:
        prov = dict(self.provenance or {})
        for name in _FIELD_ORDER:
            source = str(prov.get(name, "default"))
            prov[name] = source if source in _PROVENANCE_VALUES else "default"
        self.provenance = prov

    # ------------------------------------------------------------------
    # Derived
    # ------------------------------------------------------------------
    @property
    def detail(self) -> str:
        return detail_from_slider(self.detail_slider)

    @property
    def target_cells(self) -> int:
        return target_cells_for_slider(self.detail_slider)

    @property
    def target_cells_hint(self) -> str:
        return self.detail

    # ------------------------------------------------------------------
    # Validation / clamps
    # ------------------------------------------------------------------
    def validate(self) -> list[str]:
        problems: list[str] = []
        if not isinstance(self.detail_slider, int) or isinstance(
            self.detail_slider, bool
        ):
            problems.append("detail_slider: must be an integer")
        elif not 0 <= self.detail_slider <= 20:
            problems.append("detail_slider: must be in 0..20")
        if self.mesher not in MESHERS:
            problems.append(
                "mesher: must be one of " + ", ".join(MESHERS)
            )
        for name in ("adaptive_sizing", "ftet_adaptive", "ftet_simplify"):
            if not isinstance(getattr(self, name), bool):
                problems.append(f"{name}: must be a boolean")
        if not isinstance(self.ftet_eps_frac, (int, float)) or not (
            0.0 < float(self.ftet_eps_frac) <= 0.5
        ):
            problems.append("ftet_eps_frac: must be in (0, 0.5]")
        problems.extend(f"bl.{p}" for p in self.bl.validate())
        return problems

    def raise_if_invalid(self) -> None:
        problems = self.validate()
        if problems:
            raise MeshConfigError("; ".join(problems))

    def normalized(self) -> tuple["MeshConfig", list[str]]:
        warnings: list[str] = []
        prov = dict(self.provenance)

        def _warn(name: str, old: Any, new: Any, why: str) -> None:
            warnings.append(f"{name}: {old!r} {why} -> {new!r}")
            prov[name] = "clamped"

        slider = _as_int(self.detail_slider)
        if slider is None:
            slider = 10
            _warn("detail_slider", self.detail_slider, slider, "is not an integer")
        clamped_slider = min(max(slider, 0), 20)
        if clamped_slider != slider:
            _warn("detail_slider", slider, clamped_slider, "outside 0..20")

        mesher = str(self.mesher or "").strip()
        if mesher not in MESHERS:
            _warn("mesher", self.mesher, "gmsh_direct_poly", "is not a known mesher")
            mesher = "gmsh_direct_poly"

        eps = _as_float(self.ftet_eps_frac)
        if eps is None:
            eps = 0.15
            _warn("ftet_eps_frac", self.ftet_eps_frac, eps, "is not a number")
        clamped_eps = min(max(eps, 0.01), 0.5)
        if clamped_eps != eps:
            _warn("ftet_eps_frac", eps, clamped_eps, "outside 0.01..0.5")

        flags: dict[str, bool] = {}
        for name in ("adaptive_sizing", "ftet_adaptive", "ftet_simplify"):
            parsed = _as_bool(getattr(self, name))
            if parsed is None:
                parsed = bool(getattr(MeshConfig(), name))
                _warn(name, getattr(self, name), parsed, "is not a boolean")
            flags[name] = parsed

        bl, bl_warnings = self.bl.normalized()
        warnings.extend(f"bl.{w}" for w in bl_warnings)
        fixed = replace(
            self,
            detail_slider=clamped_slider,
            mesher=mesher,
            ftet_eps_frac=clamped_eps,
            **flags,
            bl=bl,
            provenance=prov,
        )
        return fixed, warnings

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def to_mapping(self, include_provenance: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
        for name in _FIELD_ORDER:
            data[name] = getattr(self, name)
        data["bl"] = self.bl.to_mapping(include_provenance=include_provenance)
        if include_provenance:
            data["provenance"] = {
                name: self.provenance.get(name, "default")
                for name in _FIELD_ORDER
            }
            data["bl_provenance"] = dict(self.bl.provenance)
        return data

    to_dict = to_mapping

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_mapping(), indent=indent)

    @classmethod
    def from_mapping_tolerant(
        cls,
        data: Mapping[str, Any] | "MeshConfig",
        *,
        source: str = "user",
    ) -> tuple["MeshConfig", list[str]]:
        if isinstance(data, MeshConfig):
            return data.normalized()
        if not isinstance(data, Mapping):
            return cls(), [f"invalid mesh config: expected an object, got {type(data).__name__}"]
        warnings: list[str] = []
        cfg = cls()
        values: dict[str, Any] = {}
        prov = {name: "default" for name in _FIELD_ORDER}

        def _mark(name: str) -> None:
            prov[name] = source if source in _PROVENANCE_VALUES else "user"

        # Detail: accept slider or the old 5-level name.
        raw_detail = data.get("detail_slider", data.get("detailLevel"))
        if raw_detail is None and "detail" in data:
            raw_detail = slider_from_detail(str(data.get("detail")))
            _mark("detail_slider")
        if raw_detail is not None:
            parsed = _as_int(raw_detail)
            if parsed is None:
                warnings.append(f"detail_slider: {raw_detail!r} is not an integer")
            else:
                values["detail_slider"] = parsed
                _mark("detail_slider")

        raw_mesher = data.get(
            "mesher", data.get("mesher_type", data.get("mesherType")),
        )
        if raw_mesher is not None:
            mesher = str(raw_mesher).strip()
            if mesher not in MESHERS:
                warnings.append(f"mesher: {mesher!r} is not a known mesher")
            else:
                values["mesher"] = mesher
                _mark("mesher")

        for name, aliases in {
            "adaptive_sizing": ("adaptive_sizing", "adaptiveSizing"),
            "ftet_adaptive": ("ftet_adaptive", "ftetAdaptive"),
            "ftet_simplify": ("ftet_simplify", "ftetSimplify"),
        }.items():
            raw = _first(data, (name, *aliases))
            if raw is None:
                continue
            parsed = _as_bool(raw)
            if parsed is None:
                warnings.append(f"{name}: {raw!r} is not a boolean")
            else:
                values[name] = parsed
                _mark(name)

        raw_eps = _first(
            data, ("ftet_eps_frac", "ftetEpsFrac", "ftet_eps"),
        )
        if raw_eps is not None:
            parsed = _as_float(raw_eps)
            if parsed is None:
                warnings.append(f"ftet_eps_frac: {raw_eps!r} is not a number")
            else:
                # Legacy GUI field is a percentage (15 -> 0.15).
                if parsed > 1.0:
                    parsed /= 100.0
                values["ftet_eps_frac"] = parsed
                _mark("ftet_eps_frac")

        # BL: nested mapping, JSON string, or the pre-W7 split keys.
        raw_bl = data.get("bl", data.get("bl_config"))
        if isinstance(raw_bl, str):
            try:
                raw_bl = json.loads(raw_bl)
            except json.JSONDecodeError:
                warnings.append("bl: bl_config is not valid JSON; using defaults")
                raw_bl = None
        if isinstance(raw_bl, Mapping):
            bl, bl_warn = BLConfig.from_mapping_tolerant(raw_bl)
            warnings.extend(f"bl.{w}" for w in bl_warn)
            values["bl"] = bl
        else:
            legacy_bl = {
                k: data[k]
                for k in (
                    "bl_enabled", "bl_n_layers", "bl_thickness_ratio",
                    "bl_expansion_ratio", "bl_checked", "nLayers",
                    "firstLayerThickness", "thicknessRatio", "applyToAll",
                    "minBaseEdgeFraction", "terminateByBudget",
                )
                if k in data
            }
            if legacy_bl:
                if "bl_checked" in legacy_bl and "bl_enabled" not in legacy_bl:
                    legacy_bl["bl_enabled"] = legacy_bl.pop("bl_checked")
                bl, bl_warn = BLConfig.from_mapping_tolerant(legacy_bl)
                warnings.extend(f"bl.{w}" for w in bl_warn)
                values["bl"] = bl
                if "detail" in data and "detail_slider" not in data:
                    warnings.append(
                        "legacy detail name mapped to slider; "
                        "max_cell_ratio/min_cell_ratio are geometry-relative "
                        "and are not stored"
                    )

        raw_prov = data.get("provenance")
        if isinstance(raw_prov, Mapping):
            for name in _FIELD_ORDER:
                src = raw_prov.get(name)
                if src in _PROVENANCE_VALUES:
                    prov[name] = str(src)
        if isinstance(data.get("bl_provenance"), Mapping):
            # BLConfig.from_mapping_tolerant already reads its own provenance;
            # keep the explicit one for round-trip if present.
            try:
                values["bl"] = replace(
                    values.get("bl", cfg.bl),
                    provenance={
                        k: str(v) for k, v in data["bl_provenance"].items()
                        if k in values.get("bl", cfg.bl).provenance
                    },
                )
            except Exception:
                pass

        cfg = replace(cfg, **values, provenance=prov)
        fixed, clamp_warnings = cfg.normalized()
        return fixed, warnings + clamp_warnings

    @classmethod
    def from_json(cls, text: str, **kw) -> "MeshConfig":
        try:
            data = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise MeshConfigError(f"invalid mesh config JSON: {exc}") from exc
        cfg, warnings = cls.from_mapping_tolerant(data, **kw)
        if warnings:
            raise MeshConfigError("; ".join(warnings))
        return cfg

    # ------------------------------------------------------------------
    # Project / template adapters
    # ------------------------------------------------------------------
    @classmethod
    def has_settings(cls, settings) -> bool:
        """True when this project/QSettings carries a mesh config or legacy keys."""
        if settings.value("params/mesh_config", None):
            return True
        return any(
            settings.value(key, None) is not None
            for key in LEGACY_SETTINGS_KEYS
        )

    @classmethod
    def from_settings(cls, settings) -> "MeshConfig":
        """Read the new ``params/mesh_config`` or migrate the legacy keys.

        *settings* is any object with a ``value(key, default)`` method
        (QSettings, a dict wrapper in tests).
        """
        raw = settings.value("params/mesh_config", None)
        if raw:
            try:
                data = raw if isinstance(raw, dict) else json.loads(str(raw))
            except (TypeError, ValueError):
                data = None
            if isinstance(data, Mapping):
                cfg, _warn = cls.from_mapping_tolerant(data, source="user")
                return cfg
        legacy = {
            "detail_slider": settings.value("params/detail_slider", None),
            "bl_checked": settings.value("params/bl_checked", None, type=bool)
            if _supports_type(settings) else settings.value("params/bl_checked", None),
            "bl_config": settings.value("params/bl_config", None),
        }
        had_bl_state = (
            legacy["bl_checked"] is not None or legacy["bl_config"] is not None
        )
        legacy = {k: v for k, v in legacy.items() if v is not None}
        if not had_bl_state:
            # Pre-W7 projects had no BL key: the GUI default was BL off.
            legacy["bl_enabled"] = False
        if not legacy:
            return cls()
        cfg, _warn = cls.from_mapping_tolerant(legacy, source="user")
        return cfg

    @classmethod
    def from_template_preset(cls, preset) -> tuple["MeshConfig", list[str]]:
        """Map the old commercial ``TemplatePreset`` mesh fields.

        The old template stores ``max_cell_ratio``/``min_cell_ratio``, which
        are geometry-relative and cannot be represented as portable preset
        values; they are deliberately ignored with a warning.
        """
        data = {
            "detail": getattr(preset, "detail", "medium"),
            "bl_enabled": getattr(preset, "bl_enabled", True),
            "bl_n_layers": getattr(preset, "bl_n_layers", 5),
        }
        return cls.from_mapping_tolerant(data, source="preset")

    def to_engine_summary(self) -> str:
        return (
            f"detail={self.detail}({self.detail_slider}) mesher={self.mesher} "
            f"adaptive={self.adaptive_sizing} "
            f"ftet(eps={self.ftet_eps_frac:g}, adaptive={self.ftet_adaptive}, "
            f"simplify={self.ftet_simplify}) bl=[{self.bl.summary()}]"
        )


def _first(data: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return None


def _supports_type(settings) -> bool:
    """QSettings.value accepts a ``type=`` keyword; simple dicts do not."""
    try:
        import inspect

        return "type" in inspect.signature(settings.value).parameters
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# CLI (same object, no second preset language)
# ---------------------------------------------------------------------------
def add_cli_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("mesh configuration")
    group.add_argument("--mesh-config", default=None,
                       help="JSON file (or inline JSON) with a MeshConfig")
    group.add_argument("--detail-slider", type=int, default=None)
    group.add_argument("--mesher", choices=MESHERS, default=None)
    group.add_argument(
        "--adaptive-sizing", type=_bool_arg, nargs="?", const=True, default=None,
    )
    group.add_argument("--ftet-eps-frac", type=float, default=None)
    group.add_argument(
        "--ftet-adaptive", type=_bool_arg, nargs="?", const=True, default=None,
    )
    group.add_argument(
        "--ftet-simplify", type=_bool_arg, nargs="?", const=True, default=None,
    )
    group.add_argument("--bl-n-layers", type=int, default=None)
    group.add_argument("--bl-first-layer-fraction", type=float, default=None)
    group.add_argument("--bl-growth", type=float, default=None)
    group.add_argument(
        "--bl-enabled", type=_bool_arg, nargs="?", const=True, default=None,
    )


def _bool_arg(value: str) -> bool:
    parsed = _as_bool(value)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"expected on/off/true/false, got {value!r}")
    return parsed


def config_from_cli_args(args: argparse.Namespace) -> MeshConfig:
    raw = getattr(args, "mesh_config", None)
    if raw:
        if str(raw).lstrip().startswith("{"):
            cfg, _warn = MeshConfig.from_mapping_tolerant(
                json.loads(str(raw)), source="user",
            )
        else:
            data = json.loads(open(str(raw), encoding="utf-8").read())
            cfg, _warn = MeshConfig.from_mapping_tolerant(data, source="user")
    else:
        cfg = MeshConfig()

    overrides: dict[str, Any] = {}
    attr_map = {
        "detail_slider": "detail_slider",
        "mesher": "mesher",
        "adaptive_sizing": "adaptive_sizing",
        "ftet_eps_frac": "ftet_eps_frac",
        "ftet_adaptive": "ftet_adaptive",
        "ftet_simplify": "ftet_simplify",
    }
    prov = dict(cfg.provenance)
    for field_name, attr in attr_map.items():
        value = getattr(args, attr, None)
        if value is not None:
            overrides[field_name] = value
            prov[field_name] = "user"
    bl_overrides: dict[str, Any] = {}
    if getattr(args, "bl_n_layers", None) is not None:
        bl_overrides["n_layers"] = args.bl_n_layers
    if getattr(args, "bl_first_layer_fraction", None) is not None:
        bl_overrides["first_layer_fraction"] = args.bl_first_layer_fraction
        bl_overrides["first_height"] = 0.0
    if getattr(args, "bl_growth", None) is not None:
        bl_overrides["growth_rate"] = args.bl_growth
    if getattr(args, "bl_enabled", None) is not None:
        bl_overrides["enabled"] = args.bl_enabled
    if bl_overrides:
        overrides["bl"] = replace(cfg.bl, **bl_overrides)
    cfg = replace(cfg, **overrides, provenance=prov)
    fixed, _warn = cfg.normalized()
    fixed.raise_if_invalid()
    return fixed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m polyfoammesh.core.mesh_config",
        description="Validate/print the one MeshConfig used by the GUI, "
                    "project and worker.",
    )
    add_cli_arguments(parser)
    args = parser.parse_args(argv)
    cfg = config_from_cli_args(args)
    print(cfg.to_json())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
