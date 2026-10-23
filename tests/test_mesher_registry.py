"""W13: one Polymesh CTA, one lossless mesher registry, working migration."""
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

from polyfoammesh.core.mesh_config import (  # noqa: E402
    MeshConfig,
    add_cli_arguments,
    config_from_cli_args,
)
from polyfoammesh.core.mesh_presets import MeshPresetStore  # noqa: E402
from polyfoammesh.core.mesher_registry import (  # noqa: E402
    DEFAULT_MESHER_KEY,
    ENGINE_KEYS,
    MESHERS,
    MODE_BY_KEY,
    MODES,
    normalize_mesher_key,
)
from polyfoammesh.gui.params_panel import ParamsPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app):
    return ParamsPanel()


def test_registry_is_lossless_and_has_one_default():
    assert len(MESHERS) == len(set(MESHERS))
    assert set(MODE_BY_KEY) == set(MESHERS)
    assert len(MODES) == len(MESHERS)
    assert sum(m.default for m in MODES) == 1
    assert MODE_BY_KEY[DEFAULT_MESHER_KEY].default is True
    for old in (
        "gmsh_direct_poly", "ftetwild_poly", "auto_tet", "cfmesh",
        "gmsh_direct", "bl_on_tets",
    ):
        assert old in MODE_BY_KEY


def test_legacy_aliases_are_migrated_not_dropped():
    assert normalize_mesher_key("autopoly") == "gmsh_direct_poly"
    assert normalize_mesher_key("poly") == "gmsh_direct_poly"
    cfg, warnings = MeshConfig.from_mapping_tolerant({"mesher": "autopoly"})
    assert cfg.mesher == "gmsh_direct_poly"
    assert not warnings


def test_ui_has_exactly_one_visible_polymesh_cta(panel):
    visible = [b for b, _k in panel._mesher_keys if not b.isHidden()]
    assert len(visible) == 1
    assert visible[0].text() == "Polymesh"
    hidden = {k for b, k in panel._mesher_keys if b.isHidden()}
    assert hidden == {"ftetwild_poly", "auto_tet", "cfmesh"}
    assert "gmsh_direct" not in {k for _b, k in panel._mesher_keys}


def test_advanced_engine_lists_every_historical_path(panel):
    combo_keys = {
        panel._engine_combo.itemData(i)
        for i in range(panel._engine_combo.count())
    }
    assert combo_keys == set(ENGINE_KEYS)
    assert set(MESHERS) == combo_keys


def test_engine_selector_drives_poly_checkbox_and_dispatch(panel):
    for key in ENGINE_KEYS:
        idx = panel._engine_combo.findData(key)
        panel._engine_combo.setCurrentIndex(idx)
        assert panel.get_mesher_type() == key
        if key == "bl_on_tets":
            assert panel._poly_check.isHidden()
            assert not panel._poly_check.isChecked()
            assert panel.get_poly_conversion() is False
        elif key in ("gmsh_direct_poly", "ftetwild_poly", "auto_tet"):
            assert panel._poly_check.isHidden()
            assert panel._poly_check.isChecked()


def test_hidden_legacy_buttons_are_still_invocable(panel):
    buttons = {k: b for b, k in panel._mesher_keys}
    buttons["ftetwild_poly"].click()
    assert panel.get_mesher_type() == "ftetwild_poly"
    buttons["cfmesh"].click()
    assert panel.get_mesher_type() == "cfmesh"
    buttons["gmsh_direct_poly"].click()
    assert panel.get_mesher_type() == "gmsh_direct_poly"


def test_bl_on_tets_round_trips_through_config_preset_and_cli(tmp_path):
    cfg = MeshConfig(mesher="bl_on_tets")
    assert MeshConfig.from_mapping_tolerant(cfg.to_mapping())[0].mesher == (
        "bl_on_tets"
    )
    store = MeshPresetStore(tmp_path / "presets.json")
    store.save("BL tet", cfg)
    assert store.get("BL tet").mesher == "bl_on_tets"

    import argparse

    parser = argparse.ArgumentParser()
    add_cli_arguments(parser)
    args = parser.parse_args(["--mesher", "bl_on_tets"])
    assert config_from_cli_args(args).mesher == "bl_on_tets"


def test_project_restore_selects_the_advanced_engine(panel):
    class _Settings:
        def __init__(self):
            self.values = {
                "params/mesh_config": json.dumps(
                    MeshConfig(mesher="bl_on_tets").to_mapping()
                )
            }

        def value(self, key, default=None, type=None):  # noqa: A002
            return self.values.get(key, default)

    panel.restore_params(_Settings())
    assert panel.get_mesher_type() == "bl_on_tets"
    assert panel._engine_combo.currentData() == "bl_on_tets"
    assert panel.get_poly_conversion() is False
