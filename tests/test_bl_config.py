"""W7: the single BL configuration contract.

These tests pin the object that GUI, CLI and the worker share:
canonical units, validation, provenance, schema version and the legacy
project migration.  No Qt, no meshing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from polyfoammesh.core.bl_config import (  # noqa: E402
    BLConfig,
    BLConfigError,
    SCHEMA_VERSION,
    add_cli_arguments,
    config_from_cli_args,
)
from polyfoammesh.core.bl_presets import BLPresetStore  # noqa: E402


def test_defaults_are_the_historical_effective_values():
    c = BLConfig()
    assert c.enabled is True
    assert c.n_layers == 3
    assert c.first_height == 0.0
    # Measured historical effective default: Qt rounded the old 0.005 to
    # 0.01 because setDecimals() came after setValue(). W7 preserves it.
    assert c.first_layer_fraction == 0.01
    assert c.growth_rate == 1.2
    assert c.apply_to_all is False
    assert c.min_base_edge_fraction == 0.9
    assert c.terminate_by_budget is True
    assert c.edge_cap_percentile == 0.0
    assert c.local_termination == "off"
    assert c.local_height_retry is False
    assert c.local_max_rounds == 3
    assert c.velocity == 10.0
    assert c.fluid == "air"
    assert c.wall_treatment == "wall_functions"
    assert c.y_plus_target == 30.0
    assert all(v == "default" for v in c.provenance.values())


def test_round_trip_keeps_values_schema_and_provenance():
    c = BLConfig(
        n_layers=7,
        first_height=0.0004,
        growth_rate=1.35,
        apply_to_all=True,
        min_base_edge_fraction=0.72,
        terminate_by_budget=False,
        edge_cap_percentile=50.0,
        local_termination="decoupled_vertex",
        local_height_retry=True,
        local_max_rounds=5,
        velocity=3.5,
        fluid="water",
        wall_treatment="wall_resolved",
        provenance={"n_layers": "preset", "edge_cap_percentile": "user"},
    )
    data = c.to_mapping()
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["provenance"]["n_layers"] == "preset"
    assert data["provenance"]["edge_cap_percentile"] == "user"

    again = BLConfig.from_mapping(data)
    assert again == c
    assert again.provenance == c.provenance
    assert again.y_plus_target == 1.0


def test_strict_validation_reports_field_and_does_not_clamp():
    c = BLConfig(n_layers=99, growth_rate=1.0, local_termination="bogus")
    problems = c.validate()
    assert any("n_layers" in p for p in problems)
    assert any("local_termination" in p for p in problems)
    with pytest.raises(BLConfigError):
        c.raise_if_invalid()


def test_normalize_clamps_every_field_once_and_declares_it():
    c = BLConfig(
        n_layers=99,
        first_layer_fraction=2.0,
        growth_rate=3.0,
        min_base_edge_fraction=-0.2,
        edge_cap_percentile=-1.0,
        local_max_rounds=99,
    )
    fixed, warnings = c.normalized()
    assert fixed.n_layers == 40
    assert fixed.first_layer_fraction == 1.0
    assert fixed.growth_rate == 2.0
    assert fixed.min_base_edge_fraction == 0.0
    assert fixed.edge_cap_percentile == 0.0
    assert fixed.local_max_rounds == 10
    assert len(warnings) >= 6
    assert any("n_layers" in w and "99" in w and "40" in w for w in warnings)
    assert fixed.provenance["n_layers"] == "clamped"


def test_tolerant_load_never_raises_and_reports_bad_values():
    data = {
        "schema_version": SCHEMA_VERSION,
        "n_layers": "many",
        "edge_cap_percentile": 500,
        "local_termination": "not-a-mode",
        "local_height_retry": True,
        "wall_treatment": "unknown",
    }
    c, warnings = BLConfig.from_mapping_tolerant(data)
    assert c.n_layers == BLConfig().n_layers
    assert c.edge_cap_percentile == 100.0
    assert c.local_termination == "off"
    assert c.local_height_retry is False  # invalid without decoupled_vertex
    assert c.wall_treatment == "wall_functions"
    assert warnings


def test_tolerant_load_of_a_non_mapping_returns_defaults():
    c, warnings = BLConfig.from_mapping_tolerant(["not", "a", "config"])
    assert c == BLConfig()
    assert warnings


def test_legacy_project_fraction_is_converted_to_metres_once():
    legacy = {
        "bl_n_layers": 4,
        "bl_thickness_ratio": 0.005,
        "bl_expansion_ratio": 1.3,
        "bl_enabled": True,
    }
    c, warnings = BLConfig.from_mapping_tolerant(legacy, max_cell=0.2)
    assert c.n_layers == 4
    assert c.first_height == pytest.approx(0.001)
    assert c.first_layer_fraction == pytest.approx(0.005)
    assert c.growth_rate == pytest.approx(1.3)
    assert c.provenance["first_height"] == "user"
    assert not warnings


def test_legacy_absolute_first_layer_wins_over_fraction():
    legacy = {
        "nLayers": 5,
        "firstLayerThickness": 0.00025,
        "thicknessRatio": 0.005,  # old key misuse: a fraction, not growth
        "applyToAll": True,
    }
    c, warnings = BLConfig.from_mapping_tolerant(legacy, max_cell=0.05)
    assert c.n_layers == 5
    assert c.first_height == pytest.approx(0.00025)
    assert c.apply_to_all is True
    assert c.growth_rate == BLConfig().growth_rate
    assert any("growth" in w.lower() for w in warnings)


def test_engine_kwargs_are_the_exact_engine_contract():
    c = BLConfig(
        n_layers=6,
        first_height=0.0003,
        growth_rate=1.25,
        min_base_edge_fraction=0.7,
        terminate_by_budget=False,
        edge_cap_percentile=40.0,
        local_termination="decoupled_vertex",
        local_height_retry=True,
        local_max_rounds=4,
    )
    kw = c.engine_kwargs(max_cell=0.1)
    assert kw == {
        "n_layers": 6,
        "first_height": 0.0003,
        "growth_rate": 1.25,
        "min_base_edge_fraction": 0.7,
        "terminate_by_budget": False,
        "edge_cap_percentile": 40.0,
        "local_termination": "decoupled_vertex",
        "local_height_retry": True,
        "local_max_rounds": 4,
    }


def test_standard_only_defaults_off_and_round_trips():
    c = BLConfig()
    assert c.standard_only is False
    data = c.to_mapping()
    assert data["standard_only"] is False
    back, warnings = BLConfig.from_mapping_tolerant(
        {"standardOnly": True, "n_layers": 3}
    )
    assert not warnings
    assert back.standard_only is True
    # Legacy/malformed values never break loading: fail-soft to default.
    soft, warnings = BLConfig.from_mapping_tolerant({"standard_only": "maybe"})
    assert soft.standard_only is False
    assert any("standard_only" in w for w in warnings)


def test_fraction_mode_resolves_against_max_cell():
    c = BLConfig(first_layer_fraction=0.01)
    assert c.first_height_mode == "fraction"
    assert c.resolved_first_height(0.05) == pytest.approx(0.0005)
    with pytest.raises(BLConfigError):
        c.resolved_first_height(0.0)
    c.first_height = 0.0002
    assert c.first_height_mode == "absolute"
    assert c.resolved_first_height(0.05) == pytest.approx(0.0002)


def test_legacy_meshdict_dict_is_cfmesh_compatible_and_real():
    c = BLConfig(first_layer_fraction=0.002, growth_rate=1.3)
    d = c.legacy_meshdict_dict(max_cell=0.1, wall_patches=["wall", "surface_2"])
    assert d["nLayers"] == 3
    assert d["thicknessRatio"] == pytest.approx(1.3)
    assert d["firstLayerThickness"] == pytest.approx(0.0002)
    assert d["applyToAll"] is False
    assert d["wallPatches"] == ["wall", "surface_2"]
    # bindings that used to be dead in the autopoly path now have values
    assert d["enabled"] is True
    assert d["maxThickness"] > 0
    assert d["minBaseEdgeFraction"] == 0.9
    assert d["terminateByBudget"] is True


def test_cli_and_object_build_the_same_config():
    parser = pytest.importorskip("argparse").ArgumentParser()
    add_cli_arguments(parser)
    args = parser.parse_args([
        "--bl-n-layers", "8",
        "--bl-first-height", "0.0002",
        "--bl-growth", "1.4",
        "--bl-min-base-edge-fraction", "0.6",
        "--bl-edge-cap-percentile", "25",
        "--bl-local-termination", "decoupled_vertex",
        "--bl-local-height-retry",
        "--bl-terminate-by-budget", "off",
    ])
    cli = config_from_cli_args(args)
    direct = BLConfig(
        n_layers=8,
        first_height=0.0002,
        growth_rate=1.4,
        min_base_edge_fraction=0.6,
        edge_cap_percentile=25.0,
        local_termination="decoupled_vertex",
        local_height_retry=True,
        terminate_by_budget=False,
    )
    assert cli.engine_kwargs(0.1) == direct.engine_kwargs(0.1)
    assert cli.provenance["n_layers"] == "user"


def test_preset_store_round_trip_last_used_and_atomic_reload(tmp_path):
    path = tmp_path / "bl_presets.json"
    store = BLPresetStore(path)
    cfg = BLConfig(n_layers=9, first_height=1e-4, growth_rate=1.5)
    store.save("Thin wall", cfg)
    store.set_last_used("Thin wall")

    again = BLPresetStore(path)
    loaded = again.load_all()
    assert loaded.errors == []
    assert again.names() == ["Thin wall"]
    assert again.last_used == "Thin wall"
    assert again.get("Thin wall") == cfg
    assert json.loads(path.read_text())["schema_version"] == SCHEMA_VERSION


def test_preset_out_of_range_clamps_instead_of_breaking_the_project(tmp_path):
    path = tmp_path / "bl_presets.json"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "last_used": "bad",
        "presets": {
            "bad": {
                "config": {
                    "schema_version": SCHEMA_VERSION,
                    "nLayers": 999,
                    "growthRate": 5.0,
                    "edgeCapPercentile": 150,
                }
            }
        },
    }
    path.write_text(json.dumps(payload))
    store = BLPresetStore(path)
    loaded = store.load_all()
    assert "bad" in loaded.presets
    assert loaded.presets["bad"].n_layers == 40
    assert loaded.presets["bad"].growth_rate == 2.0
    assert loaded.presets["bad"].edge_cap_percentile == 100.0
    assert loaded.warnings


def test_corrupt_preset_file_is_reported_not_raised(tmp_path):
    path = tmp_path / "bl_presets.json"
    path.write_text("{not json")
    store = BLPresetStore(path)
    loaded = store.load_all()
    assert loaded.presets == {}
    assert loaded.errors


def test_delete_preset_removes_it_from_disk(tmp_path):
    path = tmp_path / "bl_presets.json"
    store = BLPresetStore(path)
    store.save("a", BLConfig())
    store.save("b", BLConfig(n_layers=4))
    store.delete("a")
    again = BLPresetStore(path)
    again.load_all()
    assert again.names() == ["b"]
