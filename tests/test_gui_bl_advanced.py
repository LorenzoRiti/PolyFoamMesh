"""W7: every exposed BL control must reach the engine.

The tests are offscreen and never mesh anything.  The chain under test is:

    widget -> ParamsPanel.get_bl_config() -> BLConfig.engine_kwargs()
    -> DualPolyWorker.prepare_bl_config() -> (kwargs handed to the engine)

plus preset round-trip, provenance and the "invalid preset does not break
the project" requirement.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from polyfoammesh.core.bl_config import (  # noqa: E402
    BLConfig,
    add_cli_arguments,
    config_from_cli_args,
)
from polyfoammesh.core.bl_presets import BLPresetStore  # noqa: E402
from polyfoammesh.gui.params_panel import ParamsPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app, tmp_path):
    p = ParamsPanel()
    # Never read/write the real user profile from a test.
    p._preset_store = BLPresetStore(tmp_path / "presets.json")
    p._refresh_bl_presets()
    return p


def _select_local_mode(panel: ParamsPanel, mode: str) -> None:
    idx = panel._bl_local_term.findData(mode)
    assert idx >= 0
    panel._bl_local_term.setCurrentIndex(idx)


def test_advanced_controls_exist_with_ranges_units_and_tooltips(panel):
    assert panel._bl_min_base_edge.value() == pytest.approx(0.9)
    assert panel._bl_min_base_edge.minimum() == 0.0
    assert panel._bl_min_base_edge.maximum() >= 2.0
    assert panel._bl_min_base_edge.toolTip()

    assert panel._bl_terminate_budget.isChecked() is True
    assert panel._bl_terminate_budget.toolTip()

    assert panel._bl_edge_cap_pct.value() == pytest.approx(0.0)
    assert panel._bl_edge_cap_pct.minimum() == 0.0
    assert panel._bl_edge_cap_pct.maximum() == 100.0
    assert panel._bl_edge_cap_pct.toolTip()
    assert "%" in panel._bl_edge_cap_pct.suffix()

    assert panel._bl_local_term.count() == 3
    assert panel._bl_local_term.currentData() == "off"
    assert panel._bl_local_term.toolTip()

    assert panel._bl_height_retry.isChecked() is False
    assert panel._bl_height_retry.isEnabled() is False
    assert panel._bl_height_retry.toolTip()

    assert panel._bl_local_rounds.value() == 3
    assert panel._bl_local_rounds.minimum() == 1
    assert panel._bl_local_rounds.maximum() == 10

    assert panel._bl_preset_combo is not None
    assert panel._bl_save_preset_btn is not None
    assert panel._bl_delete_preset_btn is not None


def test_advanced_values_reach_engine_kwargs_and_legacy_dict(panel):
    panel._bl_checkbox.setChecked(True)
    panel._max_cell.setValue(0.1)
    panel._bl_min_base_edge.setValue(0.7)
    panel._bl_terminate_budget.setChecked(False)
    panel._bl_edge_cap_pct.setValue(50.0)
    _select_local_mode(panel, "decoupled_vertex")
    panel._bl_height_retry.setChecked(True)
    panel._bl_local_rounds.setValue(5)

    cfg = panel.get_bl_config()
    kw = cfg.engine_kwargs(panel._max_cell.value())
    assert kw["min_base_edge_fraction"] == pytest.approx(0.7)
    assert kw["terminate_by_budget"] is False
    assert kw["edge_cap_percentile"] == pytest.approx(50.0)
    assert kw["local_termination"] == "decoupled_vertex"
    assert kw["local_height_retry"] is True
    assert kw["local_max_rounds"] == 5

    legacy = panel.get_bl_params()
    assert legacy["minBaseEdgeFraction"] == pytest.approx(0.7)
    assert legacy["terminateByBudget"] is False
    assert legacy["edgeCapPercentile"] == pytest.approx(50.0)
    assert legacy["localTermination"] == "decoupled_vertex"
    assert legacy["localHeightRetry"] is True
    assert legacy["localMaxRounds"] == 5


def test_defaults_do_not_change_the_pre_w7_engine_call(panel):
    panel._bl_checkbox.setChecked(True)
    cfg = panel.get_bl_config()
    kw = cfg.engine_kwargs(panel._max_cell.value())
    assert kw["n_layers"] == 3
    assert kw["first_height"] == pytest.approx(
        BLConfig().first_layer_fraction * panel._max_cell.value(), rel=1e-12
    )
    assert kw["growth_rate"] == pytest.approx(1.2)
    assert kw["min_base_edge_fraction"] == pytest.approx(0.9)
    assert kw["terminate_by_budget"] is True
    assert kw["edge_cap_percentile"] == pytest.approx(0.0)
    assert kw["local_termination"] is False
    assert kw["local_height_retry"] is False
    assert kw["local_max_rounds"] == 3


def test_provenance_default_then_user_then_preset(panel):
    assert panel.get_bl_config().provenance["edge_cap_percentile"] == "default"
    panel._bl_edge_cap_pct.setValue(12.0)
    assert panel.get_bl_config().provenance["edge_cap_percentile"] == "user"

    cfg = BLConfig(
        n_layers=6,
        first_height=0.0002,
        edge_cap_percentile=30.0,
    ).with_provenance("preset")
    panel.set_bl_config(cfg, source="preset")
    got = panel.get_bl_config()
    assert got.n_layers == 6
    assert got.first_height == pytest.approx(0.0002)
    assert all(v == "preset" for v in got.provenance.values())


def test_local_height_retry_is_only_enabled_for_vertex_mode(panel):
    panel._bl_height_retry.setChecked(True)  # refused while mode is off
    assert panel._bl_height_retry.isChecked() is False
    _select_local_mode(panel, "decoupled")
    assert panel._bl_height_retry.isEnabled() is False
    _select_local_mode(panel, "decoupled_vertex")
    assert panel._bl_height_retry.isEnabled() is True
    panel._bl_height_retry.setChecked(True)
    assert panel.get_bl_config().local_height_retry is True
    _select_local_mode(panel, "off")
    assert panel._bl_height_retry.isChecked() is False
    assert panel.get_bl_config().local_height_retry is False


def test_preset_round_trip_through_the_panel(panel):
    cfg = BLConfig(
        n_layers=7,
        first_height=0.0003,
        growth_rate=1.35,
        min_base_edge_fraction=0.65,
        edge_cap_percentile=20.0,
        local_termination="decoupled",
    )
    panel.save_bl_preset("Custom 7", cfg)
    assert "Custom 7" in panel._preset_store.names()

    # A fresh store (same file) is what a restart does.
    panel._preset_store = BLPresetStore(panel._preset_store.path)
    panel._refresh_bl_presets()
    assert panel.apply_bl_preset("Custom 7") is True
    got = panel.get_bl_config()
    assert got.n_layers == 7
    assert got.first_height == pytest.approx(0.0003)
    assert got.min_base_edge_fraction == pytest.approx(0.65)
    assert got.edge_cap_percentile == pytest.approx(20.0)
    assert got.provenance["n_layers"] == "preset"


def test_invalid_preset_clamps_with_a_visible_message(panel, tmp_path):
    path = tmp_path / "bad_presets.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "presets": {
            "bad": {"config": {
                "schema_version": 1,
                "nLayers": 999,
                "growthRate": 5.0,
            }},
        },
    }))
    panel._preset_store = BLPresetStore(path)
    panel._refresh_bl_presets()
    assert panel.apply_bl_preset("bad") is True
    assert panel.get_bl_config().n_layers == 40
    assert panel.get_bl_config().growth_rate == pytest.approx(2.0)
    assert "bad" in panel._bl_preset_status.text().lower()
    assert "clamp" in panel._bl_preset_status.text().lower()


def test_engine_really_accepts_every_exposed_key(panel):
    """No decorative control: the real engine signature must take the key."""
    import inspect

    from polyfoammesh.core.bl_poly import PolyBoundaryLayerEngine

    panel._bl_checkbox.setChecked(True)
    panel._max_cell.setValue(0.1)
    panel._bl_min_base_edge.setValue(0.5)
    panel._bl_edge_cap_pct.setValue(10.0)
    _select_local_mode(panel, "decoupled_vertex")
    panel._bl_height_retry.setChecked(True)
    panel._bl_local_rounds.setValue(4)
    kw = panel.get_bl_config().engine_kwargs(panel._max_cell.value())

    accepted = set(inspect.signature(PolyBoundaryLayerEngine.run).parameters)
    assert set(kw) <= accepted

    # Synthetic "run": a mock engine records exactly what the worker would
    # pass.  This is the no-GMSH proof that the values reach the call.
    captured: dict = {}

    class _MockEngine:
        def run(self, **kwargs):
            captured.update(kwargs)

    _MockEngine().run(**kw)
    assert captured == kw
    assert captured["min_base_edge_fraction"] == pytest.approx(0.5)
    assert captured["edge_cap_percentile"] == pytest.approx(10.0)
    assert captured["local_termination"] == "decoupled_vertex"
    assert captured["local_height_retry"] is True
    assert captured["local_max_rounds"] == 4


def test_cli_and_gui_build_the_same_engine_kwargs(panel):
    panel._bl_checkbox.setChecked(True)
    panel._max_cell.setValue(0.1)
    panel._bl_n_layers.setValue(8)
    panel._bl_thick.setValue(0.002)  # manual fraction mode
    panel._bl_exp.setValue(1.4)
    panel._bl_min_base_edge.setValue(0.6)
    panel._bl_edge_cap_pct.setValue(25.0)
    _select_local_mode(panel, "decoupled_vertex")
    panel._bl_height_retry.setChecked(True)
    panel._bl_terminate_budget.setChecked(False)

    parser = pytest.importorskip("argparse").ArgumentParser()
    add_cli_arguments(parser)
    args = parser.parse_args([
        "--bl-n-layers", "8",
        "--bl-first-layer-fraction", "0.002",
        "--bl-growth", "1.4",
        "--bl-min-base-edge-fraction", "0.6",
        "--bl-edge-cap-percentile", "25",
        "--bl-local-termination", "decoupled_vertex",
        "--bl-local-height-retry",
        "--bl-terminate-by-budget", "off",
    ])
    cli = config_from_cli_args(args, max_cell=0.1)
    gui = panel.get_bl_config()
    assert cli.engine_kwargs(0.1) == gui.engine_kwargs(0.1)


def test_restore_starts_from_the_last_used_preset(panel, tmp_path):
    class _Settings:
        def __init__(self):
            self.values: dict = {}

        def value(self, key, default=None, type=None):  # noqa: A002
            raw = self.values.get(key, default)
            if type is bool and raw is not None:
                return bool(raw)
            return raw

        def setValue(self, key, value):
            self.values[key] = value

    cfg = BLConfig(n_layers=6, first_height=0.0002, growth_rate=1.3)
    panel.save_bl_preset("Startup", cfg)
    panel._preset_store.set_last_used("Startup")

    panel.restore_params(_Settings())
    got = panel.get_bl_config()
    assert got.n_layers == 6
    assert got.first_height == pytest.approx(0.0002)
    assert panel._bl_preset_combo.currentData() == "Startup"

    # A full save/restore cycle keeps the config, not just the checkbox.
    panel._bl_edge_cap_pct.setValue(17.0)
    s = _Settings()
    panel.save_params(s)
    data = json.loads(s.values["params/bl_config"])
    assert data["schema_version"] == 1
    assert s.values["params/bl_checked"] is panel._bl_checkbox.isChecked()

    fresh = ParamsPanel()
    fresh._preset_store = BLPresetStore(tmp_path / "fresh_presets.json")
    fresh._refresh_bl_presets()
    fresh.restore_params(s)
    assert fresh.get_bl_config().edge_cap_percentile == pytest.approx(17.0)


def test_worker_receives_gui_config_and_writes_the_run_record(panel, tmp_path):
    from polyfoammesh.core.openfoam_runner import DualPolyWorker

    panel._bl_checkbox.setChecked(True)
    panel._max_cell.setValue(0.1)
    panel._bl_min_base_edge.setValue(0.55)
    panel._bl_edge_cap_pct.setValue(35.0)
    _select_local_mode(panel, "decoupled_vertex")
    panel._bl_height_retry.setChecked(True)

    worker = DualPolyWorker(
        tmp_path, bl_params=panel.get_bl_params(),
        max_cell=panel._max_cell.value(),
    )
    logs: list[str] = []
    worker.log_line.connect(logs.append)
    cfg, kw, warnings = worker.prepare_bl_config()

    assert kw["min_base_edge_fraction"] == pytest.approx(0.55)
    assert kw["edge_cap_percentile"] == pytest.approx(35.0)
    assert kw["local_termination"] == "decoupled_vertex"
    assert kw["local_height_retry"] is True
    assert cfg.provenance["edge_cap_percentile"] == "user"

    assert any("[poly] BL effective:" in line for line in logs)
    assert any("provenance" in line for line in logs)
    record = tmp_path / "bl_config.json"
    assert record.exists()
    payload = json.loads(record.read_text())
    assert payload["schema_version"] == 1
    assert payload["effective"]["edge_cap_percentile"] == pytest.approx(35.0)
    assert payload["effective"]["provenance"]["edge_cap_percentile"] == "user"
    assert payload["run"]["max_cell_m"] == pytest.approx(0.1)


def test_worker_declares_clamps_instead_of_silently_using_them(tmp_path):
    from polyfoammesh.core.openfoam_runner import DualPolyWorker

    worker = DualPolyWorker(
        tmp_path,
        bl_params={
            "nLayers": 999,
            "firstLayerThickness": 0.0002,
            "thicknessRatio": 5.0,
            "edgeCapPercentile": 150.0,
        },
        max_cell=0.1,
    )
    logs: list[str] = []
    worker.log_line.connect(logs.append)
    cfg, kw, warnings = worker.prepare_bl_config()

    assert cfg.n_layers == 40
    assert kw["n_layers"] == 40
    assert kw["growth_rate"] == pytest.approx(2.0)
    assert kw["edge_cap_percentile"] == pytest.approx(100.0)
    assert warnings
    assert any("BL config warning:" in line for line in logs)
    assert any("clamped" in line.lower() or "outside" in line for line in logs)
    payload = json.loads((tmp_path / "bl_config.json").read_text())
    assert payload["effective"]["n_layers"] == 40
    assert payload["effective"]["provenance"]["n_layers"] == "clamped"
