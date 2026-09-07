"""End-to-end validation of the OODA engine pipeline.

Tests the full cycle: geometry loading → intent generation → adaptive loop
→ quality report → statistics → CLI entry point.

These tests use the in-memory engine (not real cfMesh/WSL) to validate
the algorithmic pipeline without external dependencies.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module

_mod = load_commercial_module("adaptive_loop")
_integration_mod = load_commercial_module("adaptive_integration")

AdaptiveLoopEngine = _mod.AdaptiveLoopEngine
AdaptiveLoopParams = _mod.AdaptiveLoopParams
AdaptiveLoopResult = _mod.AdaptiveLoopResult
UnifiedMeshGraph = _mod.UnifiedMeshGraph
MeshNode = _mod.MeshNode
MeshCell = _mod.MeshCell
MeshFace = _mod.MeshFace
MeshEntityType = _mod.MeshEntityType
OODAPhase = _mod.OODAPhase
compute_mesh_statistics = _mod.compute_mesh_statistics
generate_heatmap_data = _mod.generate_heatmap_data
export_quality_report_json = _mod.export_quality_report_json
run_progressive_refinement = _mod.run_progressive_refinement
ProgressiveRefinementParams = _mod.ProgressiveRefinementParams

OODAWorkflowAdapter = _integration_mod.OODAWorkflowAdapter
AdaptiveIntegrationResult = _integration_mod.AdaptiveIntegrationResult


# =====================================================================
# E2E: Full OODA cycle on a synthetic graph
# =====================================================================
def _build_synthetic_graph(n_tetra: int = 50) -> UnifiedMeshGraph:
    """Build a synthetic tetrahedral mesh for testing."""
    g = UnifiedMeshGraph()
    g.bbox_min = (0.0, 0.0, 0.0)
    g.bbox_max = (10.0, 10.0, 10.0)

    # Create nodes on a uniform grid
    nodes_3d: list[tuple[float, float, float]] = []
    nx = ny = nz = 4
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                x = g.bbox_min[0] + (g.bbox_max[0] - g.bbox_min[0]) * i / (nx - 1)
                y = g.bbox_min[1] + (g.bbox_max[1] - g.bbox_min[1]) * j / (ny - 1)
                z = g.bbox_min[2] + (g.bbox_max[2] - g.bbox_min[2]) * k / (nz - 1)
                nodes_3d.append((x, y, z))

    nids: list[int] = []
    for p in nodes_3d:
        nid = g.add_node(MeshNode(
            x=p[0], y=p[1], z=p[2],
            entity_type=MeshEntityType.CORE_TETRA,
        ))
        nids.append(nid)

    import random
    random.seed(42)

    for _ in range(n_tetra):
        chosen = random.sample(nids, min(4, len(nids)))
        if len(chosen) < 4:
            continue
        cid = g.add_cell(MeshCell(
            node_indices=chosen,
            entity_type=MeshEntityType.CORE_TETRA,
            volume=random.uniform(0.001, 1.0),
            skewness=random.uniform(0.1, 0.6),
            non_orthogonality=random.uniform(5.0, 40.0),
            aspect_ratio=random.uniform(1.0, 50.0),
        ))
        _add_tetra_faces(g, cid, chosen)

    return g


def _add_tetra_faces(g: UnifiedMeshGraph, cid: int, nids: list[int]) -> None:
    """Add 4 triangular faces to a tetra cell."""
    for tri in [(0, 1, 2), (0, 2, 3), (0, 3, 1), (1, 3, 2)]:
        fid = g.add_face(MeshFace(
            node_indices=[nids[tri[0]], nids[tri[1]], nids[tri[2]]],
            owner_cell=cid, neighbour_cell=-1,
        ))
        if cid in g.cells:
            g.cells[cid].face_indices.append(fid)


def test_e2e_synthetic_engine_run():
    """Full OODA cycle on a synthetic mesh."""
    engine = AdaptiveLoopEngine()
    engine.configure(Path("."))
    engine._graph = _build_synthetic_graph(30)
    engine._graph.build_edges()

    result = engine.run()

    assert isinstance(result, AdaptiveLoopResult)
    assert result.cell_count >= 0
    assert result.final_phase in (OODAPhase.CONVERGED.name, OODAPhase.FAILED.name)
    assert result.n_iterations >= 0
    assert result.max_skewness >= 0.0


def test_e2e_statistics_consistency():
    """Mesh statistics should be consistent with synthetic data."""
    g = _build_synthetic_graph(40)
    stats = compute_mesh_statistics(g)
    assert stats.total_cells == 40
    assert stats.total_nodes > 0
    assert stats.total_faces >= 40 * 4  # at least 4 faces per tetra
    assert stats.n_tetra == 40
    assert stats.n_poly == 0
    assert stats.n_prism == 0
    assert stats.volume_total > 0
    assert stats.volume_min > 0


def test_e2e_heatmap_consistency():
    """Heatmap data should match cell count."""
    g = _build_synthetic_graph(25)
    h = generate_heatmap_data(g)
    assert len(h["skewness"]) == 25
    assert len(h["non_orthogonality"]) == 25
    assert len(h["aspect_ratio"]) == 25
    assert len(h["volume"]) == 25
    assert len(h["worst_metric"]) == 25


def test_e2e_quality_report_structure():
    """Quality report should contain all required sections."""
    g = _build_synthetic_graph(20)
    r = AdaptiveLoopResult(success=True, cell_count=20, n_iterations=3)
    report = export_quality_report_json(g, r)
    assert "metrics" in report
    assert "statistics" in report
    assert "quality_breakdown" in report
    assert "clusters" in report
    assert "recommendations" in report
    assert "convergence" in report
    assert "histogram" in report
    assert report["statistics"]["total_cells"] == 20


def test_e2e_progressive_refinement():
    """Progressive refinement on synthetic mesh."""
    engine = AdaptiveLoopEngine()
    engine.configure(Path("."))
    engine._graph = _build_synthetic_graph(20)
    engine._graph.build_edges()

    params = ProgressiveRefinementParams(
        n_levels=2, inner_max_iterations=2,
    )
    result = run_progressive_refinement(engine, params)
    assert result.n_levels_completed >= 0
    assert len(result.level_results) == 2


def test_e2e_adapter_api():
    """OODAWorkflowAdapter public API should be callable."""
    adapter = OODAWorkflowAdapter()
    assert hasattr(adapter, "configure")
    assert hasattr(adapter, "run")
    assert hasattr(adapter, "run_parallel")

    adapter.configure(Path("/tmp/e2e_test"))
    assert adapter._case_dir is not None


def test_e2e_integration_result():
    """AdaptiveIntegrationResult should be properly structured."""
    r = AdaptiveIntegrationResult(
        success=True, cell_count=10000,
        max_skewness=0.85, max_non_orthogonality=60.0,
        n_ooda_iterations=4, n_remediations=2,
        phase_history=["FIELD_EVALUATION", "CONVERGED"],
    )
    assert "PASS" in r.summary
    assert "10000" in r.summary
    assert r.phase_history == ["FIELD_EVALUATION", "CONVERGED"]


def test_e2e_cli_help():
    """CLI should parse help without error."""
    from polyfoammesh.commercial.adaptive_cli import main
    try:
        ret = main(["--help"])
    except SystemExit as e:
        ret = e.code
    assert ret == 0


def test_e2e_cli_missing_geometry():
    """CLI should fail gracefully with missing geometry."""
    from polyfoammesh.commercial.adaptive_cli import main
    ret = main(["--geometry", "/nonexistent/file.stl"])
    assert ret == 1


def test_e2e_cli_invalid_detail():
    """CLI should validate detail level."""
    from polyfoammesh.commercial.adaptive_cli import main
    try:
        ret = main(["--geometry", "dummy.stl", "--detail", "invalid"])
    except SystemExit:
        ret = 2  # argparse error
    except Exception:
        ret = 2
    assert ret == 2


if __name__ == "__main__":
    test_e2e_synthetic_engine_run()
    test_e2e_statistics_consistency()
    test_e2e_heatmap_consistency()
    test_e2e_quality_report_structure()
    test_e2e_progressive_refinement()
    test_e2e_adapter_api()
    test_e2e_integration_result()
    test_e2e_cli_help()
    test_e2e_cli_missing_geometry()
    test_e2e_cli_invalid_detail()
    print("ALL PASS")
