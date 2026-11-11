"""Virtual wind tunnel: sizing criteria, blockage cap, patches, refinement."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core.wind_tunnel import (  # noqa: E402
    TunnelSpec,
    compute_tunnel,
    refinement_boxes,
    tunnel_patches,
)

CAR_LO, CAR_HI = (0.0, -0.9, 0.0), (4.5, 0.9, 1.4)   # L 4.5, W 1.8, H 1.4


def test_default_clearances_follow_the_criteria():
    t = compute_tunnel(CAR_LO, CAR_HI, TunnelSpec(on_ground=True))
    assert t.lo[0] == pytest.approx(-3 * 4.5)          # 3 L upstream
    assert t.hi[0] == pytest.approx(4.5 + 8 * 4.5)     # 8 L downstream
    assert t.lo[2] == pytest.approx(0.0)               # floor on the ground
    assert t.hi[2] >= 1.4 + 5 * 1.4 - 1e-9             # >= 5 H above
    assert t.hi[1] - 0.9 >= 4 * 1.8 - 1e-9             # >= 4 D each side
    assert t.blockage <= 0.03 + 1e-12


def test_blockage_cap_widens_the_tunnel():
    # a wide flat object: the default clearances give > 3 % blockage
    t = compute_tunnel((0, -5, 0), (1, 5, 0.2),
                       TunnelSpec(on_ground=True, side_D=0.2, top_H=1.0))
    assert t.widened
    assert t.blockage == pytest.approx(0.03, rel=1e-6)


def test_flow_direction_and_free_flight():
    t = compute_tunnel((0, 0, 0), (1, 1, 1),
                       TunnelSpec(flow_axis=1, flow_sign=-1, on_ground=False))
    # flow towards -y: upstream is +y side
    assert t.hi[1] == pytest.approx(1 + 3)
    assert t.lo[1] == pytest.approx(-8)
    # free flight: floor clearance equals top clearance
    assert -t.lo[2] == pytest.approx(t.hi[2] - 1)


def test_invalid_specs_are_refused():
    with pytest.raises(ValueError):
        compute_tunnel((0, 0, 0), (1, 1, 1), TunnelSpec(flow_axis=2, up_axis=2))
    with pytest.raises(ValueError):
        compute_tunnel((0, 0, 0), (1, 1, 1), TunnelSpec(max_blockage=0.0))
    with pytest.raises(ValueError):
        compute_tunnel((0, 0, 0), (1, 0, 1))


def test_patches_enclose_the_fluid_and_keep_names():
    obj = trimesh.creation.box(extents=(1.0, 0.5, 0.4))
    obj.apply_translation((0.5, 0.0, 0.2))
    obj.metadata["name"] = "car"
    t = compute_tunnel(obj.bounds[0], obj.bounds[1], TunnelSpec(on_ground=True))
    pats = tunnel_patches([obj], t)
    names = sorted(m.metadata["name"] for m in pats)
    assert names == ["car", "farfield", "ground", "inlet", "outlet"]
    allm = trimesh.util.concatenate(pats)
    # box volume minus object volume, normals out of the fluid
    box_vol = float(np.prod(np.asarray(t.hi) - np.asarray(t.lo)))
    assert allm.volume == pytest.approx(box_vol - obj.volume, rel=1e-6)
    # inlet normal points upstream (-x)
    inlet = next(m for m in pats if m.metadata["name"] == "inlet")
    assert inlet.face_normals.mean(axis=0)[0] < -0.99


def test_refinement_boxes_cover_object_and_wake():
    t = compute_tunnel(CAR_LO, CAR_HI, TunnelSpec(on_ground=True))
    near, wake = refinement_boxes(CAR_LO, CAR_HI, t, base_cell=0.2)
    assert near["level"] == 3 and wake["level"] == 2
    assert near["cell_size"] == pytest.approx(0.05)
    assert near["xmin"] < 0.0 and near["xmax"] > 4.5          # around the car
    assert wake["xmin"] == pytest.approx(4.5) and wake["xmax"] > 4.5 + 2 * 4.5
    assert near["zmin"] == pytest.approx(0.0)                  # down to the ground
    for b in (near, wake):                                     # inside the tunnel
        for i, a in enumerate("xyz"):
            assert t.lo[i] <= b[f"{a}min"] < b[f"{a}max"] <= t.hi[i]


def test_dialog_builds_the_spec_and_flags_widening():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from polyfoammesh.gui.wind_tunnel_dialog import WindTunnelDialog

    _app = QApplication.instance() or QApplication([])
    dlg = WindTunnelDialog(CAR_LO, CAR_HI, unit_label="m")
    dlg._ground.setChecked(True)
    dlg._dir.setCurrentIndex(1)            # -X
    spec = dlg.spec()
    assert (spec.flow_axis, spec.flow_sign, spec.on_ground) == (0, -1, True)
    assert "blockage" in dlg._summary.text()
    dlg._side_D.setValue(0.5)
    dlg._top_H.setValue(0.5)
    assert "widened" in dlg._summary.text()


def test_gui_has_the_wind_tunnel_action():
    import inspect

    pytest.importorskip("PySide6")
    from polyfoammesh.gui import main_window as mw

    src = inspect.getsource(mw.MainWindow)
    assert "Create Wind Tunnel around Object..." in src
    assert "def _on_wind_tunnel" in src


def test_budget_sets_the_base_cell_and_is_met():
    from polyfoammesh.core.wind_tunnel import estimate_cells, suggest_base_cell

    t = compute_tunnel(CAR_LO, CAR_HI, TunnelSpec(on_ground=True))
    r = suggest_base_cell(CAR_LO, CAR_HI, t, obj_volume=5.0, target_cells=400_000)
    assert r["estimated_cells"] == pytest.approx(400_000, rel=0.02)
    assert r["surface_cell"] == pytest.approx(r["base_cell"] / 4)
    boxes = refinement_boxes(CAR_LO, CAR_HI, t, r["base_cell"])
    assert estimate_cells(t, boxes, r["base_cell"], 5.0) == pytest.approx(
        r["estimated_cells"])
    # a bigger budget gives finer cells
    r2 = suggest_base_cell(CAR_LO, CAR_HI, t, 5.0, target_cells=1_600_000)
    assert r2["base_cell"] < r["base_cell"]


def test_small_budget_for_a_thin_object_warns():
    from polyfoammesh.core.wind_tunnel import suggest_base_cell

    lo, hi = (0.0, 0.0, 0.0), (0.1, 0.1, 0.024)          # NACA-like
    t = compute_tunnel(lo, hi, TunnelSpec())
    r = suggest_base_cell(lo, hi, t, 1.6e-4, target_cells=50_000)
    assert r["cells_across_object"] < 10 and "budget" in r["warning"]
    with pytest.raises(ValueError):
        suggest_base_cell(lo, hi, t, 0.0, target_cells=10)
