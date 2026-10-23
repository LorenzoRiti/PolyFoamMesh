"""W10: mesh presets are real, validated and survive the project restart."""
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

from polyfoammesh.core.bl_config import BLConfig  # noqa: E402
from polyfoammesh.core.mesh_config import MeshConfig  # noqa: E402
from polyfoammesh.core.mesh_presets import MeshPresetStore  # noqa: E402
from polyfoammesh.gui.params_panel import ParamsPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app, tmp_path):
    p = ParamsPanel()
    p._mesh_preset_store = MeshPresetStore(tmp_path / "mesh_presets.json")
    p._refresh_mesh_presets()
    return p


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


def test_builtin_fast_reaches_the_widgets_and_has_evidence(panel):
    assert panel.apply_mesh_preset("Fast") is True
    assert panel._detail_slider.value() == 4
    assert panel.get_mesher_type() == "gmsh_direct_poly"
    assert panel.get_bl_config().n_layers == 3
    assert panel.get_bl_params()["nLayers"] == 3
    assert panel._mesh_preset_combo.currentData() == "Fast"
    assert "cube_duct" in panel._mesh_preset_status.text()


def test_builtin_fine_is_marked_experimental(panel):
    assert panel.apply_mesh_preset("Fine") is True
    assert panel._detail_slider.value() == 16
    assert "experimental" in panel._mesh_preset_status.text().lower()


def test_user_mesh_preset_round_trip(panel, tmp_path):
    panel._detail_slider.setValue(7)
    panel._mesh_preset_store.save(
        "Custom",
        MeshConfig(
            detail_slider=7, mesher="cfmesh",
            bl=BLConfig(n_layers=2),
        ),
    )
    panel._refresh_mesh_presets()
    assert panel.apply_mesh_preset("Custom") is True
    assert panel._detail_slider.value() == 7
    assert panel.get_mesher_type() == "cfmesh"
    assert panel.get_bl_config().n_layers == 2
    assert "Custom" in panel._mesh_preset_store.names()


def test_reserved_builtin_name_cannot_be_overwritten(panel):
    cfg = panel.get_mesh_config()
    with pytest.raises(ValueError):
        panel._mesh_preset_store.save("Fast", cfg)
    # The GUI wrapper reports instead of raising.
    assert panel.save_mesh_preset("Fast") is False
    assert "built-in" in panel._mesh_preset_status.text().lower()


def test_invalid_preset_clamps_and_project_stays_alive(panel, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "kind": "mesh",
        "presets": {"bad": {"config": {
            "schema_version": 1, "detail_slider": 99,
            "mesher": "bogus", "bl": {"n_layers": 999},
        }}},
    }), encoding="utf-8")
    panel._mesh_preset_store = MeshPresetStore(path)
    panel._refresh_mesh_presets()
    assert panel.apply_mesh_preset("bad") is True
    assert panel._detail_slider.value() == 20
    assert panel.get_mesher_type() == "auto"
    assert panel.get_bl_config().n_layers == 40


def test_project_round_trip_uses_one_mesh_config(panel):
    panel._detail_slider.setValue(16)
    for btn, key in panel._mesher_keys:
        if key == "ftetwild_poly":
            btn.click()
            break
    panel._ftet_simplify.setChecked(True)
    panel._bl_n_layers.setValue(4)
    s = _Settings()
    panel.save_params(s)
    assert "params/mesh_config" in s.values
    data = json.loads(s.values["params/mesh_config"])
    assert data["schema_version"] == 1
    assert data["bl"]["n_layers"] == 4
    assert "params/detail_slider" not in s.values

    fresh = ParamsPanel()
    fresh._mesh_preset_store = MeshPresetStore(panel._mesh_preset_store.path)
    fresh._refresh_mesh_presets()
    fresh.restore_params(s)
    cfg = fresh.get_mesh_config()
    assert cfg.detail_slider == 16
    assert cfg.mesher == "ftetwild_poly"
    assert cfg.ftet_simplify is True
    assert cfg.bl.n_layers == 4


def test_legacy_project_keys_migrate_to_mesh_config(panel):
    s = _Settings()
    s.values.update({
        "params/detail_slider": 4,
        "params/bl_checked": True,
        "params/bl_config": json.dumps({
            "schema_version": 1, "n_layers": 6, "first_layer_fraction": 0.02,
        }),
    })
    panel.restore_params(s)
    cfg = panel.get_mesh_config()
    assert cfg.detail_slider == 4
    assert cfg.bl.enabled is True
    assert cfg.bl.n_layers == 6
    # The next save writes only the new key.
    s2 = _Settings()
    panel.save_params(s2)
    assert "params/mesh_config" in s2.values
    assert "params/bl_config" not in s2.values


def test_worker_consumes_the_mesh_config_bl_submodel(panel, tmp_path):
    from polyfoammesh.core.openfoam_runner import DualPolyWorker

    panel._bl_checkbox.setChecked(True)
    panel._bl_n_layers.setValue(6)
    worker = DualPolyWorker(
        tmp_path,
        mesh_config=panel.get_mesh_config(),
        max_cell=panel._max_cell.value(),
    )
    cfg, kw, _warnings = worker.prepare_bl_config()
    assert cfg.n_layers == 6
    assert kw["n_layers"] == 6
    assert cfg.provenance["n_layers"] in {"user", "default", "preset", "clamped"}


def test_cli_and_panel_build_the_same_config(panel):
    import argparse
    from polyfoammesh.core.mesh_config import (
        add_cli_arguments, config_from_cli_args,
    )

    panel._detail_slider.setValue(10)
    panel._bl_checkbox.setChecked(True)
    panel._bl_n_layers.setValue(5)
    panel._bl_thick.setValue(0.01)
    panel._bl_exp.setValue(1.2)
    parser = argparse.ArgumentParser()
    add_cli_arguments(parser)
    args = parser.parse_args([
        "--detail-slider", "10", "--bl-enabled",
        "--bl-n-layers", "5",
        "--bl-first-layer-fraction", "0.01", "--bl-growth", "1.2",
    ])
    cli = config_from_cli_args(args)
    gui = panel.get_mesh_config()
    assert cli.to_mapping(include_provenance=False) == gui.to_mapping(
        include_provenance=False
    )
