"""Tests for the OODA-workflow integration adapter."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module

_mod = load_commercial_module("adaptive_integration")

OODAWorkflowAdapter = _mod.OODAWorkflowAdapter
AdaptiveIntegrationResult = _mod.AdaptiveIntegrationResult


def test_result_defaults():
    r = AdaptiveIntegrationResult()
    assert not r.success
    assert r.cell_count == 0
    assert r.phase_history == []


def test_result_summary_pass():
    r = AdaptiveIntegrationResult(
        success=True, cell_count=50000, max_skewness=0.85,
        max_non_orthogonality=65.0, wall_time_s=10.5,
    )
    s = r.summary
    assert "PASS" in s
    assert "50000" in s
    assert "10.5s" in s


def test_result_summary_fail():
    r = AdaptiveIntegrationResult(
        success=False, cell_count=100, max_skewness=0.99, wall_time_s=5.0,
    )
    assert "FAIL" in r.summary


def test_adapter_init():
    a = OODAWorkflowAdapter()
    assert a._case_dir is None
    assert a._meshes is None


def test_adapter_can_build_its_mesh_engine():
    """Regression: the adaptive path crashed before it ever meshed.

    ``OODAWorkflowAdapter._run_serial`` builds its engine with
    ``MeshEngine(self._of_config)`` (adaptive_integration.py:141), but
    ``MeshEngine.__init__`` accepted no argument — a hard TypeError on the
    first adaptive run, reachable from the Solve Adaptive ribbon button.
    The suite never caught it because these tests mock the engine away.

    The adapter's own ``_of_config`` is ``None`` unless one was injected, so
    the real call is ``MeshEngine(None)`` — it must fall back to a default
    OFConfig rather than propagating the None into the engine.
    """
    from cfmesh_autogui.commercial.mesh_engine import MeshEngine

    a = OODAWorkflowAdapter()
    assert a._of_config is None
    engine = MeshEngine(a._of_config)  # used to raise TypeError
    assert engine._of_config is not None, "None config must fall back to a default"

    from cfmesh_autogui.config import OFConfig

    cfg = OFConfig()
    b = OODAWorkflowAdapter(cfg)
    assert MeshEngine(b._of_config)._of_config is cfg, "injected config must be used"


def test_adapter_configure():
    a = OODAWorkflowAdapter()
    a.configure(Path("/tmp/test_case"), meshes=[], geometry_path="/tmp/test.stl")
    assert a._case_dir is not None
    assert a._meshes == []


def test_adapter_run_no_case():
    a = OODAWorkflowAdapter()
    try:
        a.run()
        assert False, "Should have raised RuntimeError"
    except RuntimeError:
        pass


def test_adapter_emit():
    messages = []
    OODAWorkflowAdapter._emit(
        lambda msg, prog: messages.append((msg, prog)),
        "testing", 0.5,
    )
    assert len(messages) == 1
    assert messages[0][0] == "testing"
    assert messages[0][1] == 0.5


def test_adapter_emit_none():
    OODAWorkflowAdapter._emit(None, "no op", 0.5)


def test_phase_intent_generation_no_meshes():
    a = OODAWorkflowAdapter()
    a._meshes = None
    sizing = a._phase_intent_generation()
    assert "max_cell" in sizing
    assert "bl_enabled" in sizing
    assert sizing["detail_level"] == "medium"


def test_export_report(tmp_path):
    a = OODAWorkflowAdapter()
    a._result = AdaptiveIntegrationResult(
        success=True, cell_count=1000, max_skewness=0.5,
        max_non_orthogonality=30.0, n_ooda_iterations=3,
        phase_history=["FIELD_EVALUATION", "CONVERGED"],
    )
    p = tmp_path / "report.json"
    a._export_report(p)
    assert p.exists()
    data = json.loads(p.read_text())
    assert data["success"]
    assert data["cell_count"] == 1000
    assert data["iterations"]["n_ooda"] == 3


def test_decide_fixes_high_skewness():
    from cfmesh_autogui.commercial.quality_engine import QualityMetrics, QualityReport
    qr = QualityReport()
    qr.metrics.max_skewness = 0.95
    qr.metrics.max_non_orthogonality = 30.0
    qr.metrics.neg_cells = 0
    fixes = OODAWorkflowAdapter()._decide_cfmesh_fixes(qr, {"max_cell": 0.05, "min_cell": 0.01})
    assert len(fixes) >= 1
    if fixes:
        assert fixes[0].get("max_cell", 0) > 0.05


def test_decide_fixes_high_non_ortho():
    from cfmesh_autogui.commercial.quality_engine import QualityMetrics, QualityReport
    qr = QualityReport()
    qr.metrics.max_skewness = 0.5
    qr.metrics.max_non_orthogonality = 80.0
    qr.metrics.neg_cells = 0
    fixes = OODAWorkflowAdapter()._decide_cfmesh_fixes(qr, {"max_cell": 0.05, "bl_enabled": True, "bl_n_layers": 5})
    bl_fixes = [f for f in fixes if "bl_n_layers" in f]
    assert len(bl_fixes) >= 1


def test_run_parallel_no_case():
    a = OODAWorkflowAdapter()
    assert isinstance(a, OODAWorkflowAdapter), f"Expected instance, got {type(a)}"
    assert hasattr(a, 'run_parallel'), f"Adapter lacks run_parallel: {dir(a)[:20]}"
    try:
        a.run_parallel(n_cores=2)
        assert False, "Should have raised RuntimeError"
    except RuntimeError:
        pass


def test_parallel_emits_progress():
    messages = []
    a = OODAWorkflowAdapter()
    a.configure(Path("/tmp/par_test"))
    # Should not raise even though no real mesher available
    # (will fail at mesh generation but that's expected)
    try:
        a.run_parallel(n_cores=2, max_iterations=1, callback=lambda m, p: messages.append(m))
    except Exception:
        pass
    # Callback should have been called at least once
    assert len(messages) >= 1


def test_parallel_sets_ncores_in_sizing():
    a = OODAWorkflowAdapter()
    a.configure(Path("/tmp/ncores_test"))
    a._meshes = []
    sizing = a._phase_intent_generation()
    assert sizing.get("n_cores", 1) == 1  # not set by serial intent


def test_parallel_report_diff_name():
    a = OODAWorkflowAdapter()
    a._result = AdaptiveIntegrationResult(success=True, phase_history=["PARALLEL"])
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        a._export_report(Path(tmp) / "ooda_parallel_report.json")
        assert (Path(tmp) / "ooda_parallel_report.json").exists()


if __name__ == "__main__":
    test_result_defaults()
    test_result_summary_pass()
    test_result_summary_fail()
    test_adapter_init()
    test_adapter_can_build_its_mesh_engine()
    test_adapter_configure()
    test_adapter_run_no_case()
    test_adapter_emit()
    test_adapter_emit_none()
    test_phase_intent_generation_no_meshes()
    test_decide_fixes_high_skewness()
    test_decide_fixes_high_non_ortho()
    test_run_parallel_no_case()
    test_parallel_emits_progress()
    test_parallel_sets_ncores_in_sizing()
    test_parallel_report_diff_name()
    print("ALL PASS")
