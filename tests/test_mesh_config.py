"""W10: one MeshConfig shared by GUI, project, CLI and presets."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from polyfoammesh.core.bl_config import BLConfig  # noqa: E402
from polyfoammesh.core.mesh_config import (  # noqa: E402
    MeshConfig,
    MeshConfigError,
    add_cli_arguments,
    config_from_cli_args,
    target_cells_for_slider,
)
from polyfoammesh.core.mesh_presets import (  # noqa: E402
    BUILTIN_MESH_PRESETS,
    MeshPresetStore,
    list_mesh_presets,
    resolve_mesh_preset,
)


def test_round_trip_keeps_config_bl_and_provenance():
    bl = BLConfig(
        n_layers=6, first_height=0.0003, growth_rate=1.35,
        min_base_edge_fraction=0.7, terminate_by_budget=False,
        edge_cap_percentile=25.0,
        local_termination="decoupled_vertex", local_height_retry=True,
        provenance={"n_layers": "preset"},
    )
    cfg = MeshConfig(
        detail_slider=14,
        mesher="ftetwild_poly",
        adaptive_sizing=False,
        ftet_eps_frac=0.08,
        ftet_adaptive=False,
        ftet_simplify=True,
        bl=bl,
        provenance={"detail_slider": "user", "mesher": "preset"},
    )
    data = cfg.to_mapping()
    assert data["schema_version"] == 1
    again = MeshConfig.from_mapping_tolerant(data)[0]
    assert again == cfg
    assert again.provenance["detail_slider"] == "user"
    assert again.provenance["mesher"] == "preset"
    assert again.bl.provenance["n_layers"] == "preset"


def test_legacy_qsettings_adapter_migrates_bl_and_detail():
    class _Settings:
        values = {
            "params/detail_slider": 16,
            "params/bl_checked": True,
            "params/bl_config": json.dumps({
                "schema_version": 1, "n_layers": 4,
                "first_layer_fraction": 0.02,
                "growth_rate": 1.3,
            }),
        }

        def value(self, key, default=None, type=None):  # noqa: A002
            raw = self.values.get(key, default)
            if type is bool and raw is not None:
                return bool(raw)
            return raw

    cfg = MeshConfig.from_settings(_Settings())
    assert cfg.detail_slider == 16
    assert cfg.detail == "fine"
    assert cfg.bl.enabled is True
    assert cfg.bl.n_layers == 4
    assert cfg.bl.first_layer_fraction == pytest.approx(0.02)
    assert cfg.bl.growth_rate == pytest.approx(1.3)


def test_new_project_key_wins_over_legacy_keys():
    class _Settings:
        values = {
            "params/mesh_config": MeshConfig(
                detail_slider=4, mesher="cfmesh",
            ).to_json(),
            "params/detail_slider": 18,
            "params/bl_checked": False,
        }

        def value(self, key, default=None, type=None):  # noqa: A002
            return self.values.get(key, default)

    cfg = MeshConfig.from_settings(_Settings())
    assert cfg.detail_slider == 4
    assert cfg.mesher == "cfmesh"


def test_template_preset_adapter_uses_the_slider_mapping_and_warns_on_ratios():
    class _Template:
        detail = "coarse"
        bl_enabled = True
        bl_n_layers = 8
        max_cell_ratio = 0.04
        min_cell_ratio = 0.004

    cfg, warnings = MeshConfig.from_template_preset(_Template())
    assert cfg.detail_slider == 4
    assert cfg.bl.enabled is True
    assert cfg.bl.n_layers == 8
    assert any("max_cell_ratio" in w for w in warnings)


def test_validation_declares_clamps_and_rejects_in_strict_mode():
    cfg = MeshConfig(
        detail_slider=99, mesher="bogus", ftet_eps_frac=2.0,
        bl=BLConfig(n_layers=99),
    )
    with pytest.raises(MeshConfigError):
        cfg.raise_if_invalid()
    fixed, warnings = cfg.normalized()
    assert fixed.detail_slider == 20
    assert fixed.mesher == "gmsh_direct_poly"
    assert fixed.ftet_eps_frac == pytest.approx(0.5)
    assert fixed.bl.n_layers == 40
    assert len(warnings) >= 4
    assert fixed.provenance["detail_slider"] == "clamped"


def test_tolerant_load_of_bad_mapping_never_raises():
    cfg, warnings = MeshConfig.from_mapping_tolerant(["not", "an", "object"])
    assert cfg == MeshConfig()
    assert warnings


def test_cli_and_gui_build_the_same_config():
    parser = argparse.ArgumentParser()
    add_cli_arguments(parser)
    args = parser.parse_args([
        "--detail-slider", "16",
        "--mesher", "ftetwild_poly",
        "--ftet-simplify",
        "--bl-n-layers", "4",
        "--bl-first-layer-fraction", "0.02",
        "--bl-growth", "1.3",
    ])
    cli = config_from_cli_args(args)
    direct = MeshConfig(
        detail_slider=16,
        mesher="ftetwild_poly",
        ftet_simplify=True,
        bl=BLConfig(
            enabled=False,
            n_layers=4,
            first_layer_fraction=0.02,
            growth_rate=1.3,
        ),
    )
    assert cli.to_mapping(include_provenance=False) == direct.to_mapping(
        include_provenance=False
    )


def test_slider_target_is_shared_and_monotonic():
    assert target_cells_for_slider(0) == 10_000
    assert target_cells_for_slider(20) == 20_000_000
    assert target_cells_for_slider(4) < target_cells_for_slider(10) < (
        target_cells_for_slider(16)
    )
    assert MeshConfig(detail_slider=10).target_cells == target_cells_for_slider(10)


def test_builtin_presets_are_ordered_measured_or_marked_experimental():
    fast = BUILTIN_MESH_PRESETS["Fast"]
    balanced = BUILTIN_MESH_PRESETS["Balanced"]
    fine = BUILTIN_MESH_PRESETS["Fine"]
    assert fast.config.target_cells < balanced.config.target_cells < (
        fine.config.target_cells
    )
    assert "cube_duct" in fast.evidence
    assert "R1 173334" in balanced.evidence
    assert fast.experimental is False
    assert balanced.experimental is False
    assert fine.experimental is True
    assert fine.evidence


def test_user_preset_round_trip_and_reserved_names(tmp_path):
    store = MeshPresetStore(tmp_path / "mesh_presets.json")
    cfg = MeshConfig(
        detail_slider=6, mesher="cfmesh",
        bl=BLConfig(n_layers=2),
    )
    store.save("My coarse", cfg)
    again = MeshPresetStore(tmp_path / "mesh_presets.json")
    assert again.get("My coarse") == cfg
    assert again.last_used == "My coarse"
    assert resolve_mesh_preset(again, "My coarse").origin == "user"
    with pytest.raises(ValueError):
        store.save("Fast", cfg)


def test_invalid_user_preset_is_clamped_and_reported(tmp_path):
    path = tmp_path / "mesh_presets.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "kind": "mesh",
        "last_used": "bad",
        "presets": {
            "bad": {"config": {
                "schema_version": 1,
                "detail_slider": 99,
                "mesher": "bogus",
                "bl": {"n_layers": 999},
            }},
        },
    }), encoding="utf-8")
    store = MeshPresetStore(path)
    result = store.load_all()
    assert "bad" in result.presets
    assert result.presets["bad"].detail_slider == 20
    assert result.presets["bad"].mesher == "gmsh_direct_poly"
    assert result.presets["bad"].bl.n_layers == 40
    assert result.warnings


def test_preset_kind_mismatch_is_reported_not_decoded(tmp_path):
    path = tmp_path / "mesh_presets.json"
    path.write_text(json.dumps({
        "schema_version": 1, "kind": "bl", "presets": {"x": {"config": {}}},
    }), encoding="utf-8")
    store = MeshPresetStore(path)
    result = store.load_all()
    assert result.presets == {}
    assert result.errors
