"""Tests for calibrating the BL first layer from a SOLVED case.

The feature reads the real wall y+ (``yPlus``, or ``wallShearStress``) from a
solved OpenFOAM case in pure Python and proposes the exact first layer for the
target y+.  These tests build a tiny synthetic closed box pipe with four
boundary patches (two walls, one inlet, one outlet) so the geometric role
inference, the area weighting and the inlet/outlet exclusion are all pinned
without any OpenFOAM installation.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from polyfoammesh.core.solver_calibration import (  # noqa: E402
    calibrate_from_case,
    latest_time_with_field,
    proposed_first_layer,
)

# Box pipe: x in [0,1], y in [-0.1,0.1], z in [-0.1,0.1].
_CORNERS = [
    (0.0, -0.1, -0.1), (0.0, -0.1, 0.1), (0.0, 0.1, -0.1), (0.0, 0.1, 0.1),
    (1.0, -0.1, -0.1), (1.0, -0.1, 0.1), (1.0, 0.1, -0.1), (1.0, 0.1, 0.1),
]
# 4 side walls (each face 1 m x 0.2 m = 0.2 m^2) + inlet cap (x=0) +
# outlet cap (x=1).  Faces 0..3 are the walls, 4/5 the geometric caps.
_FACES = [
    (0, 4, 5, 1),  # y = -0.1 plane (wall, outward -y)
    (2, 3, 7, 6),  # y =  0.1 plane (wall, outward +y)
    (0, 2, 6, 4),  # z = -0.1 plane (wall, outward -z)
    (1, 5, 7, 3),  # z =  0.1 plane (wall, outward +z)
    (0, 1, 3, 2),  # x = 0     inlet cap (outward -x)
    (4, 5, 7, 6),  # x = 1     outlet cap (outward +x)
]
# surface_1 / surface_2 are the side walls (each 2 faces, 0.4 m^2); the caps
# are the geometric inlet/outlet.
_PATCHES = [
    ("surface_1", "patch", 2, 0),
    ("surface_2", "patch", 2, 2),
    ("surface_3", "patch", 1, 4),
    ("surface_4", "patch", 1, 5),
]


def _header(obj: str, n: int, body: str) -> str:
    return (
        f"FoamFile {{ version 2.0; format ascii; class {obj}; "
        f"object {obj}; }}\n"
        f"{n}\n(\n{body}\n)\n"
    )


def _write_polymesh(case_dir) -> None:
    poly = case_dir / "constant" / "polyMesh"
    poly.mkdir(parents=True, exist_ok=True)

    pts = "\n".join(f"({x} {y} {z})" for x, y, z in _CORNERS)
    (poly / "points").write_text(
        _header("vectorField", len(_CORNERS), pts), encoding="ascii"
    )
    faces = "\n".join(f"4({a} {b} {c} {d})" for a, b, c, d in _FACES)
    (poly / "faces").write_text(
        _header("faceList", len(_FACES), faces), encoding="ascii"
    )
    # One hexahedral cell: every face is a boundary face (empty neighbour).
    (poly / "owner").write_text(
        _header("labelList", len(_FACES), "\n".join(["0"] * len(_FACES))),
        encoding="ascii",
    )
    (poly / "neighbour").write_text(_header("labelList", 0, ""), encoding="ascii")

    boundary = (
        "FoamFile { version 2.0; format ascii; class polyBoundaryMesh; "
        "object boundary; }\n"
        "4\n(\n"
    )
    for name, ptype, n_faces, start in _PATCHES:
        boundary += (
            f"    {name}\n    {{\n"
            f"        type            {ptype};\n"
            f"        nFaces          {n_faces};\n"
            f"        startFace       {start};\n"
            "    }\n"
        )
    boundary += ")\n"
    (poly / "boundary").write_text(boundary, encoding="ascii")


def _write_yplus(case_dir, time_name: str = "10") -> None:
    time_dir = case_dir / time_name
    time_dir.mkdir(parents=True, exist_ok=True)
    field = (
        "FoamFile { version 2.0; format ascii; class volScalarField; "
        "object yPlus; }\n"
        "dimensions [0 0 0 0 0 0 0];\n"
        "internalField uniform 0;\n"
        "boundaryField\n"
        "{\n"
        "    surface_1\n"
        "    {\n"
        "        type            calculated;\n"
        "        value           uniform 30;\n"
        "    }\n"
        "    surface_2\n"
        "    {\n"
        "        type            calculated;\n"
        "        value           nonuniform List<scalar>\n"
        "2\n"
        "(\n"
        "10\n"
        "40\n"
        ")\n"
        ";\n"
        "    }\n"
        "    surface_3\n"
        "    {\n"
        "        type            calculated;\n"
        "        value           uniform 999;\n"
        "    }\n"
        "    surface_4\n"
        "    {\n"
        "        type            calculated;\n"
        "        value           uniform 777;\n"
        "    }\n"
        "}\n"
    )
    (time_dir / "yPlus").write_text(field, encoding="ascii")


def _build_case(tmp_path) -> None:
    _write_polymesh(tmp_path)
    _write_yplus(tmp_path)


def test_parsing_area_weighting_and_role_exclusion(tmp_path):
    _build_case(tmp_path)
    result = calibrate_from_case(tmp_path, h1_used=1e-4, nu=1.5e-5, fluid="air")

    assert result.ok, result.error
    assert result.field == "yPlus"
    assert result.time_name == "10"

    # Inlet/outlet must be excluded; only the two side walls appear.
    assert {s.name for s in result.patches} == {"surface_1", "surface_2"}

    # Each wall patch has 2 faces of 0.2 m^2 -> 0.4 m^2 (Newell).
    by_name = {s.name: s for s in result.patches}
    assert by_name["surface_1"].area == pytest.approx(0.4, rel=1e-6)
    assert by_name["surface_2"].area == pytest.approx(0.4, rel=1e-6)
    assert by_name["surface_1"].mean_yplus == pytest.approx(30.0)
    assert by_name["surface_2"].mean_yplus == pytest.approx(25.0)

    # Area-weighted overall mean over the walls (inlet 999/outlet 777 ignored):
    # (30*0.4 + 25*0.4) / 0.8 = 27.5
    assert result.mean_yplus == pytest.approx(27.5, rel=1e-9)
    assert result.p10 == pytest.approx(10.0)
    assert result.p90 == pytest.approx(40.0)


def test_spread_flag_flags_a_single_first_layer_as_impossible(tmp_path):
    _build_case(tmp_path)
    result = calibrate_from_case(tmp_path, h1_used=1e-4, nu=1.5e-5)
    # y+ spans 10..40 across the wall -> x4 spread.
    assert result.spread_ratio == pytest.approx(4.0)
    assert result.single_h1_reaches_all is False


def test_proposed_first_layer_scales_linearly(tmp_path):
    _build_case(tmp_path)
    result = calibrate_from_case(tmp_path, h1_used=1e-4, nu=1.5e-5)
    h1 = 1e-4
    proposed = proposed_first_layer(30.0, h1, result, 1.5e-5)
    assert proposed == pytest.approx(h1 * 30.0 / result.mean_yplus, rel=1e-12)
    assert proposed == pytest.approx(1.090909e-4, rel=1e-5)


def test_proposed_first_layer_clamps_to_bbox(tmp_path):
    _build_case(tmp_path)
    result = calibrate_from_case(tmp_path, h1_used=1e-4, nu=1.5e-5)
    # An absurd target must be clamped to 10% of the longest bbox extent (0.1 m).
    proposed = proposed_first_layer(1e6, 1e-4, result, 1.5e-5)
    assert proposed == pytest.approx(0.1 * result.bbox)


def test_latest_time_prefers_the_newest(tmp_path):
    _write_polymesh(tmp_path)
    _write_yplus(tmp_path, "0")
    _write_yplus(tmp_path, "50")
    time_name, field_name, path = latest_time_with_field(tmp_path)
    assert (time_name, field_name) == ("50", "yPlus")
    assert path.exists()


def test_error_path_when_no_fields(tmp_path):
    _write_polymesh(tmp_path)  # no time directory / field at all
    result = calibrate_from_case(tmp_path, h1_used=1e-4, nu=1.5e-5)
    assert result.ok is False
    assert result.error
    assert proposed_first_layer(30.0, 1e-4, result, 1.5e-5) is None


def test_error_path_for_missing_case_directory(tmp_path):
    result = calibrate_from_case(tmp_path / "does_not_exist")
    assert result.ok is False
    assert "not found" in result.error


def test_wall_shear_stress_fallback(tmp_path):
    """No yPlus -> wallShearStress: KINEMATIC by default (u_tau=sqrt|tau|).

    OpenFOAM's incompressible solvers write wallShearStress as tau_w/rho,
    so the default must NOT divide by rho (that was wrong by sqrt(rho),
    i.e. 31x for water).  The dynamic convention stays available via
    kinematic_shear=False.
    """
    import math

    _write_polymesh(tmp_path)
    time_dir = tmp_path / "20"
    time_dir.mkdir(parents=True, exist_ok=True)
    field = (
        "FoamFile { version 2.0; format ascii; class volVectorField; "
        "object wallShearStress; }\n"
        "dimensions [0 2 -2 0 0 0 0];\n"
        "internalField uniform (0 0 0);\n"
        "boundaryField\n"
        "{\n"
        "    surface_1\n"
        "    {\n"
        "        type            calculated;\n"
        "        value           uniform (0.001 0 0);\n"
        "    }\n"
        "    surface_2\n"
        "    {\n"
        "        type            calculated;\n"
        "        value           nonuniform List<vector>\n"
        "2\n"
        "(\n"
        "(0.002 0 0)\n"
        "(0.003 0 0)\n"
        ")\n"
        ";\n"
        "    }\n"
        "    surface_3\n"
        "    {\n"
        "        type            calculated;\n"
        "        value           uniform (9 9 9);\n"
        "    }\n"
        "}\n"
    )
    (time_dir / "wallShearStress").write_text(field, encoding="ascii")

    rho, nu, h1 = 1.2, 1.5e-5, 1e-4
    result = calibrate_from_case(
        tmp_path, h1_used=h1, nu=nu, rho=rho, fluid="air"
    )
    assert result.ok, result.error
    assert result.field == "wallShearStress"
    assert result.time_name == "20"

    def yplus(tau):
        # Kinematic (default): u_tau = sqrt(|tau_w|).
        return math.sqrt(tau) * h1 / nu

    by_name = {s.name: s for s in result.patches}
    assert by_name["surface_1"].mean_yplus == pytest.approx(yplus(0.001))
    assert by_name["surface_2"].mean_yplus == pytest.approx(
        (yplus(0.002) + yplus(0.003)) / 2.0
    )
    assert any("KINEMATIC" in n for n in result.notes)

    # Dynamic convention (rare): u_tau = sqrt(|tau_w|/rho).
    result_dyn = calibrate_from_case(
        tmp_path, h1_used=h1, nu=nu, rho=rho, fluid="air",
        kinematic_shear=False,
    )
    assert result_dyn.ok, result_dyn.error

    def yplus_dyn(tau):
        return math.sqrt(tau / rho) * h1 / nu

    by_name_dyn = {s.name: s for s in result_dyn.patches}
    assert by_name_dyn["surface_1"].mean_yplus == pytest.approx(
        yplus_dyn(0.001)
    )
    expected_mean = (yplus(0.001) + (yplus(0.002) + yplus(0.003)) / 2.0) / 2.0
    assert result.mean_yplus == pytest.approx(expected_mean)
    assert result.u_tau > 0


def test_latest_time_prefers_yplus_over_wall_shear_stress(tmp_path):
    _write_polymesh(tmp_path)
    time_dir = tmp_path / "30"
    time_dir.mkdir(parents=True, exist_ok=True)
    (time_dir / "wallShearStress").write_text("", encoding="ascii")
    _write_yplus(tmp_path, "30")
    _time, field_name, _path = latest_time_with_field(tmp_path)
    assert field_name == "yPlus"


# ---------------------------------------------------------------------------
# GUI: Apply must store the proposal as the authoritative absolute first layer
# ---------------------------------------------------------------------------

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def _qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_gui_apply_sets_absolute_first_layer(_qapp):
    from polyfoammesh.gui.params_panel import ParamsPanel

    panel = ParamsPanel()
    panel._bl_checkbox.setChecked(True)
    panel._max_cell.setValue(0.05)

    proposed = 2.5e-3
    panel._apply_bl_calibration(proposed, None)

    assert panel._bl_first_layer_abs == pytest.approx(proposed)
    bl_params = panel.get_bl_params()
    assert bl_params is not None
    assert bl_params["firstLayerThickness"] == pytest.approx(proposed)
    # The fraction spin box reflects the proposal for the current max cell.
    assert panel._bl_thick.value() == pytest.approx(proposed / 0.05, rel=1e-9)


def test_gui_target_yplus_reads_the_wall_treatment(_qapp):
    from polyfoammesh.gui.params_panel import ParamsPanel

    panel = ParamsPanel()
    panel._bl_wall_treatment.setCurrentIndex(0)  # Wall functions (y+ ≈ 30)
    assert panel._bl_target_yplus() == pytest.approx(30.0)
    panel._bl_wall_treatment.setCurrentIndex(1)  # Wall-resolved (y+ ≈ 1)
    assert panel._bl_target_yplus() == pytest.approx(1.0)


def test_roles_fallback_when_inference_fails(tmp_path, monkeypatch):
    """Closed/external geometries: role inference fails -> type/name fallback.

    The calibration must still work: every patch that is not explicitly
    inlet/outlet/symmetry becomes a wall (correct for a closed domain,
    which has no inlets by definition), and the result carries a note.
    """
    from polyfoammesh.core import solver_calibration as sc

    _build_case(tmp_path)

    def _boom(*_a, **_k):
        raise RuntimeError("no inlet/outlet extremes found")

    monkeypatch.setattr(sc, "infer_patch_roles", _boom)
    result = sc.calibrate_from_case(
        tmp_path, h1_used=1e-4, nu=1.5e-5, rho=1.2, fluid="air"
    )
    assert result.ok, result.error
    names = {s.name for s in result.patches}
    assert {"surface_1", "surface_2"} <= names
    assert any("No inlet/outlet" in n for n in result.notes)
