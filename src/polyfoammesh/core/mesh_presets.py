# -*- coding: utf-8 -*-
"""Mesh presets (Fast / Balanced / Fine + user snapshots).

W10 requires a preset to be a snapshot of the one ``MeshConfig``, with a
name, origin and the measurement that justifies it.  Built-ins are code
constants (so they cannot be lost with a user profile); user presets live in
the same generic :class:`PresetStore` file format as BL presets.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from polyfoammesh._paths import app_data_dir
from polyfoammesh.core.bl_config import BLConfig
from polyfoammesh.core.mesh_config import MeshConfig
from polyfoammesh.core.preset_store import PresetStore

DEFAULT_MESH_PRESET_FILENAME = "mesh_presets.json"
RESERVED_PRESET_NAMES = ("Fast", "Balanced", "Fine")


@dataclass(frozen=True)
class MeshPreset:
    """A named MeshConfig plus the evidence/origin of its values."""

    name: str
    config: MeshConfig
    origin: str = "builtin"  # builtin | user
    evidence: str = ""
    experimental: bool = False

    def to_mapping(self) -> dict:
        return {
            "name": self.name,
            "origin": self.origin,
            "evidence": self.evidence,
            "experimental": self.experimental,
            "config": self.config.to_mapping(),
        }


def _bl(layers: int) -> BLConfig:
    """The measured robust BL baseline, with only the layer count changed."""
    return BLConfig(
        enabled=True,
        n_layers=layers,
        first_height=0.0,
        first_layer_fraction=0.01,
        growth_rate=1.2,
        apply_to_all=False,
        min_base_edge_fraction=0.9,
        terminate_by_budget=True,
    )


#: Built-in presets.  Evidence is deliberately explicit; the values are
#: profiles measured on the named runs, not a claim that every geometry will
#: hit the same wall time.
BUILTIN_MESH_PRESETS: dict[str, MeshPreset] = {
    "Fast": MeshPreset(
        name="Fast",
        config=MeshConfig(detail_slider=4, mesher="gmsh_direct_poly", bl=_bl(3)),
        evidence=(
            "cube_duct full E2E (2026-10-22): 334.4 s, 256,767 cells, "
            "BL coverage 100%, checkMesh Failed 1 (non-ortho 80.48 deg); "
            "coarse detail, 3 layers"
        ),
    ),
    "Balanced": MeshPreset(
        name="Balanced",
        config=MeshConfig(detail_slider=10, mesher="gmsh_direct_poly", bl=_bl(5)),
        evidence=(
            "R1 173334 E2E (2026-10-22): 110.0 s, 253,681 cells, "
            "174,265 prisms, coverage 100%, checkMesh Failed 3 "
            "(39 pyramids + 5 skew); medium detail, 5 layers"
        ),
    ),
    "Fine": MeshPreset(
        name="Fine",
        config=MeshConfig(
            detail_slider=16,
            mesher="gmsh_direct_poly",
            bl=_bl(5),
        ),
        evidence=(
            "W1 fTetWild fine tet stage: 2.37M tets, 3738 s, max_energy 7.55; "
            "the exact Fine full-preset E2E was not re-run on the current tree"
        ),
        experimental=True,
    ),
}


class MeshPresetStore(PresetStore):
    """User-defined mesh presets, same file format as BL presets."""

    def __init__(self, path: Path | str | None = None) -> None:
        if path is None:
            path = app_data_dir() / "presets" / DEFAULT_MESH_PRESET_FILENAME
        super().__init__(
            path,
            kind="mesh",
            decoder=MeshConfig.from_mapping_tolerant,
            encoder=lambda cfg: cfg.to_mapping(),
        )

    def get(self, name: str) -> MeshConfig | None:
        return super().get(name)

    def save(self, name: str, config: MeshConfig) -> None:
        clean = str(name).strip()
        if clean in RESERVED_PRESET_NAMES:
            raise ValueError(
                f"'{clean}' is a built-in preset name; choose another name"
            )
        super().save(clean, config)

    def get_preset(self, name: str) -> MeshPreset | None:
        cfg = self.get(name)
        if cfg is None:
            return None
        return MeshPreset(
            name=name, config=cfg, origin="user", evidence="",
        )


def list_mesh_presets(store: MeshPresetStore) -> list[MeshPreset]:
    """Built-ins first, then user presets (stable, name-sorted)."""
    out = [BUILTIN_MESH_PRESETS[name] for name in ("Fast", "Balanced", "Fine")]
    out.extend(
        MeshPreset(name=name, config=store.get(name), origin="user", evidence="")
        for name in store.names()
    )
    return out


def resolve_mesh_preset(
    store: MeshPresetStore, name: str,
) -> MeshPreset | None:
    """User preset wins only if it is not a reserved built-in name."""
    preset = store.get_preset(name)
    if preset is not None:
        return preset
    return BUILTIN_MESH_PRESETS.get(name)
