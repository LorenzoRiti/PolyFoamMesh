"""W17: the release gate is executable — spec, versions, repo-clean, E2E.

Fast tests (no meshing, no WSL, no clock): version sync, spec static
checks, installer allowlist, no tracked binaries, smoke imports.
The real tet->dual flow is test_e2e_venturi_flow (slow, reuses the gate's
run_e2e_flow: one home for the flow, asserted in both places).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import release_gate as gate


def test_versions_in_sync():
    """pyproject / _version.py / inno_setup.iss agree (gate stage)."""
    status, _ = gate.stage_versions()
    assert status == "PASS"


def test_spec_static_passes():
    """Both PyInstaller specs: entry, hooks, datas, no abs paths, ftetwild."""
    status, detail = gate.stage_spec_static()
    assert status == "PASS", detail


def test_excludes_dont_amputate_imports():
    """No spec exclude amputates a direct src import (the casadi lesson)."""
    for spec_name in ("PolyFoamMesh.spec", "PolyFoamMesh-linux.spec"):
        text = (ROOT / spec_name).read_text(encoding="utf-8", errors="replace")
        excludes = set(gate._spec_excludes(text))
        assert not (excludes & gate._direct_imports()), spec_name
        assert not (excludes & gate.KNOWN_TRANSITIVE), spec_name


def test_smoke_passes():
    """App entry imports, version matches, templates present."""
    status, detail = gate.stage_smoke()
    assert status == "PASS", detail


def test_repo_clean_passes():
    """No tracked binaries; installer/ == allowlist."""
    status, detail = gate.stage_repo_clean()
    assert status == "PASS", detail


def test_stale_installers_gone():
    """The three dead installer scripts are removed from git and disk."""
    for dead in (
        "installer/installer.iss",
        "installer/installer.nsi",
        "installer/nsis_installer.nsi",
    ):
        assert not (ROOT / dead).exists(), f"{dead} still on disk"
    proc = subprocess.run(
        ["git", "ls-files", "installer/"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    tracked = set(proc.stdout.split())
    assert tracked == {str(p).replace("\\", "/") for p in gate.INSTALLER_ALLOWLIST}, tracked
    assert "2.0.1" not in (ROOT / "installer/inno_setup.iss").read_text(
        encoding="utf-8", errors="replace"
    )


def test_gate_reports_declared_limits():
    """SKIPs and LIMITS are part of the report (no silent passes)."""
    rep = gate.GateReport()
    rep.add("demo", "SKIP", "reason shown")
    text = rep.summary()
    assert "SKIP" in text and "reason shown" in text
    assert "LIMITS" in text and "clean-machine" in text


@pytest.mark.slow
def test_e2e_venturi_flow(tmp_path):
    """Real tet->dual flow on the tracked venturi STL with invariants."""
    pytest.importorskip("pytetwild")
    out = gate.run_e2e_flow("venturi.stl", tmp_path / "e2e")
    assert out["n_tets"] > 0
    assert out["dual_success"] is True
    assert out["n_poly_cells"] > 0
    assert out["min_cell_volume"] > 0
    assert out["volume_rel_err"] < 1e-6
    assert (tmp_path / "e2e" / "e2e_result.json").is_file()
