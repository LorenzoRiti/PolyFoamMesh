"""W18: two visible CTAs (Polymesh auto + cfMesh), no manual Engine combo."""
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


def test_registry_is_lossless_and_auto_is_the_default():
    assert len(MESHERS) == len(set(MESHERS))
    assert set(MODE_BY_KEY) == set(MESHERS)
    assert len(MODES) == len(MESHERS)
    assert sum(m.default for m in MODES) == 1
    assert DEFAULT_MESHER_KEY == "auto"
    assert MODE_BY_KEY["auto"].default is True
    for old in (
        "gmsh_direct_poly", "ftetwild_poly", "auto_tet", "cfmesh",
        "gmsh_direct", "bl_on_tets",
    ):
        assert old in MODE_BY_KEY


def test_legacy_aliases_are_migrated_not_dropped():
    assert normalize_mesher_key("autopoly") == "gmsh_direct_poly"
    cfg, warnings = MeshConfig.from_mapping_tolerant({"mesher": "autopoly"})
    assert cfg.mesher == "gmsh_direct_poly"
    assert not warnings
    assert normalize_mesher_key("auto") == "auto"


def test_ui_has_exactly_two_visible_ctas(panel):
    visible = {k for b, k in panel._mesher_keys if not b.isHidden()}
    assert visible == {"auto", "cfmesh"}
    texts = {b.text() for b, _k in panel._mesher_keys if not b.isHidden()}
    assert any("Polymesh" in t for t in texts)
    assert any("cfMesh" in t for t in texts)


def test_engine_pin_defaults_to_automatic(panel):
    # The W18 "no manual Engine combo" is superseded by user request: the
    # mesher CTAs stay automatic-only, but Advanced offers a strict pin
    # (Automatic default, no silent behaviour change).
    assert panel.get_tet_engine_override() is None
    assert panel._tet_engine_combo.currentData() is None
    assert panel._tet_engine_combo.count() == 3


def test_engine_pin_selects_strict_key(panel):
    idx = panel._tet_engine_combo.findData("gmsh_direct_poly")
    assert idx >= 0
    panel._tet_engine_combo.setCurrentIndex(idx)
    assert panel.get_tet_engine_override() == "gmsh_direct_poly"


def test_engine_pin_contract_panel_to_decision(panel):
    """The exact user path: pin fTetWild in Advanced -> the override feeds
    a strict single-engine plan (no fallback to widen silently)."""
    from polyfoammesh.core.tet_source import pinned_polymesh_plan

    idx = panel._tet_engine_combo.findData("ftetwild_poly")
    assert idx >= 0
    panel._tet_engine_combo.setCurrentIndex(idx)
    override = panel.get_tet_engine_override()
    assert override == "ftetwild_poly"
    plan = pinned_polymesh_plan(override, True)
    assert plan.chain() == ["ftetwild_poly"]
    assert "gmsh" not in plan.chain()


def test_run_start_locks_pin_and_strategy(panel):
    """Mid-run control changes silently did nothing (reported as 'ftet
    pinned yet gmsh running'): the pin and strategy lock with the run."""
    panel.set_all_enabled(False)
    assert not panel._tet_engine_combo.isEnabled()
    assert not panel._bl_strategy.isEnabled()
    panel.set_all_enabled(True)
    assert panel._tet_engine_combo.isEnabled()
    assert panel._bl_strategy.isEnabled()
    # Re-enable reapplies the strategy lock (auto keeps knobs locked).
    assert panel.get_bl_strategy() == "auto"
    for knob in panel._advanced_bl_knobs():
        assert not knob.isEnabled()


def test_cfmesh_only_groups_follow_the_cta(panel):
    # Polymesh auto: accelerators that cannot act stay hidden.
    panel._on_mesher_changed("auto")
    assert panel._openmp_group.isHidden()
    assert panel._parallel_group.isHidden()
    panel._on_mesher_changed("cfmesh")
    assert not panel._openmp_group.isHidden()
    assert not panel._parallel_group.isHidden()
    panel._on_mesher_changed("bl_on_tets")
    assert panel._parallel_group.isHidden()


def test_polymesh_cta_is_auto_and_default(panel):
    buttons = {k: b for b, k in panel._mesher_keys}
    assert panel.get_mesher_type() == "auto"
    buttons["auto"].click()
    assert panel.get_mesher_type() == "auto"


def test_cfmesh_cta_selects_hex_path(panel):
    buttons = {k: b for b, k in panel._mesher_keys}
    buttons["cfmesh"].click()
    assert panel.get_mesher_type() == "cfmesh"


def test_hidden_legacy_buttons_are_still_invocable(panel):
    buttons = {k: b for b, k in panel._mesher_keys}
    buttons["ftetwild_poly"].click()
    assert panel.get_mesher_type() == "ftetwild_poly"
    buttons["auto_tet"].click()
    assert panel.get_mesher_type() == "auto_tet"
    buttons["auto"].click()
    assert panel.get_mesher_type() == "auto"


def test_auto_forces_poly_conversion_on_like_polymesh(panel):
    panel._on_mesher_changed("auto")
    assert panel._poly_check.isChecked()
    assert panel._poly_check.isHidden()


def test_explicit_key_without_button_is_respected(panel):
    """Projects saved with bl_on_tets keep it; a CTA click clears it."""
    cfg = MeshConfig(mesher="bl_on_tets")
    panel.set_mesh_config(cfg)
    assert panel.get_mesher_type() == "bl_on_tets"
    assert not panel.get_poly_conversion()
    buttons = {k: b for b, k in panel._mesher_keys}
    buttons["auto"].click()
    assert panel.get_mesher_type() == "auto"


def test_legacy_explicit_key_bypass_is_exposed(panel):
    """A restored legacy engine (no button) drives the run while the CTA
    still shows Polymesh: the bypass must be queryable so the run can
    declare it (the 'ftet pinned yet gmsh running' report)."""
    assert panel.get_explicit_mesher_key() is None
    panel.set_mesh_config(MeshConfig(mesher="gmsh_direct_poly"))
    assert panel.get_explicit_mesher_key() == "gmsh_direct_poly"
    assert panel.get_mesher_type() == "gmsh_direct_poly"
    # CTA visual still shows Polymesh: the divergence is real.
    buttons = {k: b for b, k in panel._mesher_keys}
    assert buttons["auto"].isChecked()
    buttons["auto"].click()
    assert panel.get_explicit_mesher_key() is None
    assert panel.get_mesher_type() == "auto"


def test_project_restore_selects_saved_key(panel):
    class _Settings:
        def __init__(self, mesher):
            self.values = {
                "params/mesh_config": json.dumps(
                    MeshConfig(mesher=mesher).to_mapping()
                )
            }

        def value(self, key, default=None, type=None):  # noqa: A002
            return self.values.get(key, default)

    panel.restore_params(_Settings("auto"))
    assert panel.get_mesher_type() == "auto"
    panel.restore_params(_Settings("gmsh_direct"))
    assert panel.get_mesher_type() == "gmsh_direct"


def test_legacy_and_auto_round_trip_through_config_preset_and_cli(tmp_path):
    for key in ("auto", "gmsh_direct_poly", "bl_on_tets", "cfmesh"):
        cfg = MeshConfig(mesher=key)
        assert MeshConfig.from_mapping_tolerant(cfg.to_mapping())[0].mesher == key
    store = MeshPresetStore(tmp_path / "presets.json")
    store.save("auto mesh", MeshConfig(mesher="auto"))
    assert store.get("auto mesh").mesher == "auto"

    import argparse

    parser = argparse.ArgumentParser()
    add_cli_arguments(parser)
    args = parser.parse_args(["--mesher", "auto"])
    assert config_from_cli_args(args).mesher == "auto"
    args = parser.parse_args(["--mesher", "gmsh_direct_poly"])
    assert config_from_cli_args(args).mesher == "gmsh_direct_poly"
