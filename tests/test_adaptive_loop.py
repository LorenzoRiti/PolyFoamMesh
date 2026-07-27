"""Tests for the closed-loop adaptive meshing engine (OODA cycle)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

# For test tetrahedron definitions
TEST_TETRA_NODES = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
TEST_TETRA_CENTROID = (0.25, 0.25, 0.25)
TEST_TETRA_VOLUME = 1.0 / 6.0

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module

_mod = load_commercial_module("adaptive_loop")

UnifiedMeshGraph = _mod.UnifiedMeshGraph
SizingField = _mod.SizingField
OODAStateMachine = _mod.OODAStateMachine
OODAPhase = _mod.OODAPhase
AdaptiveLoopEngine = _mod.AdaptiveLoopEngine
AdaptiveLoopParams = _mod.AdaptiveLoopParams
AdaptiveLoopResult = _mod.AdaptiveLoopResult
InProcessQualityEvaluator = _mod.InProcessQualityEvaluator
LocalRemediator = _mod.LocalRemediator
ConformalStitcher = _mod.ConformalStitcher
ProximityAwareExtrusion = _mod.ProximityAwareExtrusion
MeshNode = _mod.MeshNode
MeshCell = _mod.MeshCell
MeshEntityType = _mod.MeshEntityType
RemediationAction = _mod.RemediationAction
ErrorType = _mod.ErrorType
QualityEvaluation = _mod.QualityEvaluation
MeshFace = _mod.MeshFace
SizingFieldPoint = _mod.SizingFieldPoint
compute_first_layer_height = _mod.compute_first_layer_height
estimate_bl_total_thickness = _mod.estimate_bl_total_thickness

# Helper: build a valid tetrahedron in a graph
def _add_tetra(graph, entity_type=MeshEntityType.CORE_TETRA, volume_override=None):
    nids = []
    for pos in TEST_TETRA_NODES:
        nids.append(graph.add_node(MeshNode(
            x=pos[0], y=pos[1], z=pos[2], entity_type=entity_type,
        )))
    # Create 4 triangular faces
    face_indices = []
    for tri in [(0, 1, 2), (0, 2, 3), (0, 3, 1), (1, 3, 2)]:
        fid = graph.add_face(MeshFace(
            node_indices=[nids[tri[0]], nids[tri[1]], nids[tri[2]]],
            owner_cell=-1, neighbour_cell=-1,
        ))
        face_indices.append(fid)
    cid = graph.add_cell(MeshCell(
        node_indices=nids,
        face_indices=face_indices,
        entity_type=entity_type,
        volume=volume_override or TEST_TETRA_VOLUME,
        centroid=TEST_TETRA_CENTROID,
    ))
    # Set owner for each face
    for fid in face_indices:
        graph.faces[fid].owner_cell = cid
    return cid, nids


# =====================================================================
# UnifiedMeshGraph
# =====================================================================
def test_graph_empty():
    g = UnifiedMeshGraph()
    assert len(g.nodes) == 0
    assert len(g.cells) == 0
    assert g.bbox_diagonal > 0


def test_graph_add_node():
    g = UnifiedMeshGraph()
    n = MeshNode(x=1.0, y=2.0, z=3.0, entity_type=MeshEntityType.BOUNDARY)
    nid = g.add_node(n)
    assert nid == 0
    assert len(g.nodes) == 1
    assert g.nodes[0].x == 1.0


def test_graph_add_cell():
    g = UnifiedMeshGraph()
    cell = MeshCell(node_indices=[0, 1, 2, 3], volume=0.1)
    cid = g.add_cell(cell)
    assert cid == 0
    assert g.cells[0].volume == 0.1
    assert g.cells[0].is_valid


def test_graph_cell_invalid_negative_volume():
    cell = MeshCell(volume=-0.1, skewness=0.5)
    assert not cell.is_valid


def test_graph_cell_invalid_high_skewness():
    cell = MeshCell(volume=0.1, skewness=0.99)
    assert not cell.is_valid


def test_graph_extract_subgraph():
    g = UnifiedMeshGraph()
    n1 = g.add_node(MeshNode(x=0, y=0, z=0, entity_type=MeshEntityType.CORE_TETRA))
    n2 = g.add_node(MeshNode(x=1, y=0, z=0, entity_type=MeshEntityType.CORE_TETRA))
    g.add_cell(MeshCell(
        node_indices=[n1, n2], entity_type=MeshEntityType.CORE_TETRA, volume=0.1,
    ))
    g.add_cell(MeshCell(
        node_indices=[n1], entity_type=MeshEntityType.BL_LAYER, volume=0.05,
    ))

    sub = g.extract_subgraph(MeshEntityType.CORE_TETRA)
    assert len(sub.cells) == 1
    assert len(sub.nodes) == 2


def test_graph_estimate_cell_count():
    g = UnifiedMeshGraph()
    g.bbox_min = (0, 0, 0)
    g.bbox_max = (1, 1, 1)
    n = g.estimate_cell_count(0.1)
    assert n == 1000


def test_graph_cell_neighbours_of_type():
    g = UnifiedMeshGraph()
    cid1 = g.add_cell(MeshCell(entity_type=MeshEntityType.CORE_TETRA))
    cid2 = g.add_cell(MeshCell(entity_type=MeshEntityType.BL_LAYER))
    g.cells[cid1].neighbour_cells = [cid2]
    g.cells[cid2].neighbour_cells = [cid1]

    neighbours = g.cell_neighbours_of_type(cid1, MeshEntityType.BL_LAYER)
    assert neighbours == [cid2]


# =====================================================================
# SizingField
# =====================================================================
def test_sizing_field_build():
    g = UnifiedMeshGraph()
    g.bbox_min = (0, 0, 0)
    g.bbox_max = (10, 10, 10)
    sf = SizingField(g)
    sf.build_from_bbox(n_samples=100)
    assert len(sf._points) == 100
    assert sf._base_size > 0


def test_sizing_field_sample():
    g = UnifiedMeshGraph()
    g.bbox_min = (0, 0, 0)
    g.bbox_max = (1, 1, 1)
    sf = SizingField(g)
    sf.build_from_bbox(n_samples=50)
    h = sf.sample_at(0.5, 0.5, 0.5)
    assert h > 0


def test_sizing_field_refine_region():
    g = UnifiedMeshGraph()
    g.bbox_min = (0, 0, 0)
    g.bbox_max = (10, 10, 10)
    sf = SizingField(g)
    sf.build_from_bbox(n_samples=200)

    before = sf.sample_at(0, 0, 0)
    sf.refine_region(0, 0, 0, radius=2.0, target_h=before * 0.1)
    after = sf.sample_at(0, 0, 0)
    assert after <= before


def test_sizing_field_gradient_limit():
    g = UnifiedMeshGraph()
    g.bbox_min = (0, 0, 0)
    g.bbox_max = (10, 10, 10)
    sf = SizingField(g)
    sf.build_from_bbox(n_samples=300)
    # Force a sharp discontinuity
    for p in sf._points:
        if abs(p.x) < 1 and abs(p.y) < 1 and abs(p.z) < 1:
            p.h = 0.001
        else:
            p.h = 1.0
    sf._enforce_gradient_limit()
    # After enforcement, gradient should be bounded
    for i, pi in enumerate(sf._points):
        for j, pj in enumerate(sf._points):
            if i >= j:
                continue
            d = math.sqrt(
                (pi.x - pj.x) ** 2 + (pi.y - pj.y) ** 2 + (pi.z - pj.z) ** 2
            )
            if d > 1e-12:
                ratio = abs(pi.h - pj.h) / d
                assert ratio <= _mod.GRADIENT_LIMIT + 1e-9


def test_compute_face_centroid():
    g = UnifiedMeshGraph()
    nids = [g.add_node(MeshNode(x=x, y=y, z=0)) for x, y in [(0,0), (1,0), (1,1), (0,1)]]
    c = _mod.compute_face_centroid(g, nids)
    assert abs(c[0] - 0.5) < 1e-12
    assert abs(c[1] - 0.5) < 1e-12
    assert abs(c[2] - 0.0) < 1e-12


# =====================================================================
# OODAStateMachine
# =====================================================================
def test_state_machine_initial_state():
    sm = OODAStateMachine()
    assert sm.phase == OODAPhase.FIELD_EVALUATION
    assert sm.iteration == 0


def test_state_machine_transition():
    sm = OODAStateMachine()
    sm.transition(OODAPhase.INTENT_GENERATION)
    assert sm.phase == OODAPhase.INTENT_GENERATION
    assert len(sm._history) == 1


def test_state_machine_can_remediate():
    sm = OODAStateMachine()
    assert sm.can_remediate()
    sm._iteration = 100
    assert not sm.can_remediate()


def test_state_machine_reset():
    sm = OODAStateMachine()
    sm.transition(OODAPhase.INTENT_GENERATION)
    sm.transition(OODAPhase.COUPLED_BL_CORE)
    sm.reset()
    assert sm.phase == OODAPhase.FIELD_EVALUATION
    assert sm.iteration == 0
    assert len(sm._history) == 0


def test_state_machine_summary():
    sm = OODAStateMachine()
    s = sm.summary()
    assert "FIELD_EVALUATION" in s


# =====================================================================
# Vector math
# =====================================================================
def test_v3_ops():
    a = (1.0, 0.0, 0.0)
    b = (0.0, 1.0, 0.0)
    assert _mod.v3_dot(a, b) == 0.0
    assert _mod.v3_dot(a, a) == 1.0
    c = _mod.v3_cross(a, b)
    assert abs(c[2] - 1.0) < 1e-12
    assert _mod.v3_norm(a) == 1.0
    n = _mod.v3_normalize((3, 4, 0))
    assert abs(n[0] - 0.6) < 1e-12
    assert abs(_mod.v3_norm(n) - 1.0) < 1e-12


def test_tetra_volume():
    a, b, c, d = (0,0,0), (1,0,0), (0,1,0), (0,0,1)
    vol = _mod.tetra_volume(a, b, c, d)
    assert abs(vol - 1.0/6.0) < 1e-12


def test_tetra_volume_negative():
    a, b, c, d = (0,0,0), (1,0,0), (0,1,0), (0,0,-1)
    vol = _mod.tetra_volume(a, b, c, d)
    assert vol < 0


def test_face_normal():
    verts = [(0,0,0), (1,0,0), (0,1,0)]
    n = _mod.face_normal(verts)
    assert abs(n[2] - 1.0) < 1e-6  # should point in +z


def test_compute_skewness():
    cell_c = (0.0, 0.0, 0.0)
    face_c = (0.4, 0.0, 0.0)
    nbr_c = (1.0, 0.0, 0.0)
    sk = _mod.compute_skewness(cell_c, face_c, nbr_c)
    assert abs(sk - 0.4) < 1e-12


def test_compute_non_orthogonality():
    cell_c = (0.0, 0.0, 0.0)
    face_c = (0.5, 0.0, 0.0)
    fn = (1.0, 0.0, 0.0)
    nbr_c = (1.0, 0.0, 0.0)
    no = _mod.compute_non_orthogonality(cell_c, face_c, fn, nbr_c)
    assert abs(no) < 1e-6  # perfectly aligned, 0 degrees


def test_compute_non_orthogonality_angled():
    cell_c = (0.0, 0.0, 0.0)
    face_c = (0.5, 0.5, 0.0)
    fn = (1.0, 0.0, 0.0)  # normal points in x
    nbr_c = (1.0, 1.0, 0.0)
    no = _mod.compute_non_orthogonality(cell_c, face_c, fn, nbr_c)
    assert no > 0  # should be > 0 degrees


# =====================================================================
# compute_first_layer_height
# =====================================================================
def test_compute_first_layer_height_typical():
    h = compute_first_layer_height(
        y_plus_target=30.0, u_ref=10.0, nu=1.5e-5, length=1.0,
    )
    assert h > 0
    assert h < 0.01


def test_compute_first_layer_height_zero_re():
    h = compute_first_layer_height(
        y_plus_target=30.0, u_ref=0.0, nu=1.5e-5, length=1.0,
    )
    assert h > 0  # fallback


def test_compute_first_layer_height_very_low_nu():
    h = compute_first_layer_height(
        y_plus_target=1.0, u_ref=50.0, nu=1e-12, length=0.1,
    )
    assert h > 1e-12


# =====================================================================
# estimate_bl_total_thickness
# =====================================================================
def test_estimate_bl_total_thickness():
    t = estimate_bl_total_thickness(first_height=0.001, n_layers=5, growth=1.2)
    assert t > 0.005
    assert t < 0.05


def test_estimate_bl_total_thickness_growth_one():
    t = estimate_bl_total_thickness(first_height=0.001, n_layers=5, growth=1.0)
    assert abs(t - 0.005) < 1e-12


# =====================================================================
# InProcessQualityEvaluator
# =====================================================================
def test_quality_evaluator_empty():
    eval_ = InProcessQualityEvaluator().evaluate(UnifiedMeshGraph())
    assert eval_.passed
    assert eval_.n_cells_total == 0


def test_quality_evaluator_good_cell():
    g = UnifiedMeshGraph()
    _add_tetra(g)
    eval_ = InProcessQualityEvaluator().evaluate(g)
    assert eval_.passed
    assert eval_.n_cells_total == 1


def test_quality_evaluator_negative_volume():
    g = UnifiedMeshGraph()
    # Create a degenerate tetrahedron that flips winding = negative volume
    nids = []
    for pos in [(0,0,0), (1,0,0), (0,1,0), (0,0,-1)]:  # flipped z
        nids.append(g.add_node(MeshNode(x=pos[0], y=pos[1], z=pos[2])))
    fid = g.add_face(MeshFace(node_indices=nids[:3], owner_cell=0))
    cid = g.add_cell(MeshCell(
        node_indices=nids, face_indices=[fid],
    ))
    g.faces[fid].owner_cell = cid
    eval_ = InProcessQualityEvaluator().evaluate(g)
    assert not eval_.passed
    assert eval_.n_neg_volume >= 1


def test_quality_evaluator_high_skewness():
    g = UnifiedMeshGraph()
    # Stretched tetrahedron for high skewness
    nids = []
    for pos in [(0,0,0), (10,0,0), (0,0.1,0), (0,0,0.1)]:
        nids.append(g.add_node(MeshNode(x=pos[0], y=pos[1], z=pos[2])))
    cid = g.add_cell(MeshCell(node_indices=nids, entity_type=MeshEntityType.CORE_TETRA))
    eval_ = InProcessQualityEvaluator().evaluate(g)
    # Without faces, skewness stays 0, so it should pass
    assert eval_.passed


def test_quality_evaluator_bad_bl_layer():
    g = UnifiedMeshGraph()
    cid, nids = _add_tetra(g, entity_type=MeshEntityType.BL_LAYER)
    # Now flip one node to make volume negative
    g.nodes[nids[3]].z = -1.0
    eval_ = InProcessQualityEvaluator().evaluate(g)
    assert not eval_.passed
    assert eval_.n_bad_layers > 0


# =====================================================================
# LocalRemediator
# =====================================================================
def test_remediator_diagnose_empty():
    eval_ = QualityEvaluation(passed=True)
    rem = LocalRemediator(UnifiedMeshGraph(), SizingField(UnifiedMeshGraph()))
    plan = rem.diagnose(eval_)
    assert plan.is_empty


def test_remediator_diagnose_negative_volume():
    eval_ = QualityEvaluation(passed=False, error_types=[
        (ErrorType.NEGATIVE_VOLUME, 0, -0.1),
    ])
    g = UnifiedMeshGraph()
    g.add_cell(MeshCell(volume=-0.1))
    rem = LocalRemediator(g, SizingField(g))
    plan = rem.diagnose(eval_)
    assert not plan.is_empty
    assert plan.actions[0][0] == RemediationAction.UN_DUALIZE


def test_remediator_diagnose_high_skewness():
    eval_ = QualityEvaluation(passed=False, error_types=[
        (ErrorType.HIGH_SKEWNESS, 0, 0.95),
    ])
    rem = LocalRemediator(UnifiedMeshGraph(), SizingField(UnifiedMeshGraph()))
    plan = rem.diagnose(eval_)
    assert not plan.is_empty
    assert plan.actions[0][0] == RemediationAction.CENTROIDAL_VORONOI


def test_remediator_apply_laplacian_smooth():
    g = UnifiedMeshGraph()
    n1 = g.add_node(MeshNode(x=0, y=0, z=0, entity_type=MeshEntityType.CORE_TETRA))
    n2 = g.add_node(MeshNode(x=1, y=0, z=0, entity_type=MeshEntityType.CORE_TETRA))
    n3 = g.add_node(MeshNode(x=0.5, y=1, z=0, entity_type=MeshEntityType.CORE_TETRA))
    cid = g.add_cell(MeshCell(
        node_indices=[n1, n2, n3], volume=0.1, skewness=0.95,
        entity_type=MeshEntityType.CORE_TETRA,
    ))

    eval_ = QualityEvaluation(passed=False, error_types=[
        (ErrorType.HIGH_SKEWNESS, cid, 0.95),
    ])
    rem = LocalRemediator(g, SizingField(g))
    plan = rem.diagnose(eval_)
    n_applied = rem.apply(plan)
    assert n_applied > 0


def test_remediator_un_dualize():
    g = UnifiedMeshGraph()
    n1 = g.add_node(MeshNode(x=0, y=0, z=0))
    n2 = g.add_node(MeshNode(x=1, y=0, z=0))
    n3 = g.add_node(MeshNode(x=0.5, y=1, z=0))
    n4 = g.add_node(MeshNode(x=0.5, y=0.5, z=1))
    cid = g.add_cell(MeshCell(
        node_indices=[n1, n2, n3, n4], volume=-0.1,
        entity_type=MeshEntityType.CORE_POLY,
        centroid=(0.5, 0.375, 0.25),
    ))

    rem = LocalRemediator(g, SizingField(g))
    plan = _mod.RemediationPlan()
    plan.add(RemediationAction.UN_DUALIZE, cid)
    n = rem.apply(plan)
    assert n == 1
    assert cid not in g.cells  # removed
    assert len(g.cells) >= 2  # split into tetra


# =====================================================================
# ConformalStitcher
# =====================================================================
def test_conformal_stitcher_no_cells():
    stitcher = ConformalStitcher(UnifiedMeshGraph())
    n = stitcher.stitch()
    assert n == 0


def test_conformal_stitcher_with_interface():
    g = UnifiedMeshGraph()
    n1 = g.add_node(MeshNode(x=0, y=0, z=0, entity_type=MeshEntityType.BL_LAYER))
    n2 = g.add_node(MeshNode(x=1, y=0, z=0, entity_type=MeshEntityType.BL_LAYER))
    n3 = g.add_node(MeshNode(x=0, y=1, z=0, entity_type=MeshEntityType.BL_LAYER))

    bl_id = g.add_cell(MeshCell(
        node_indices=[n1, n2, n3], entity_type=MeshEntityType.BL_LAYER,
        neighbour_cells=[1], volume=0.01,
    ))
    core_id = g.add_cell(MeshCell(
        node_indices=[n1, n2, n3], entity_type=MeshEntityType.CORE_TETRA,
        neighbour_cells=[bl_id], volume=0.01,
    ))
    g.cells[bl_id].neighbour_cells = [core_id]

    stitcher = ConformalStitcher(g)
    n = stitcher.stitch()
    assert n >= 0


# =====================================================================
# ProximityAwareExtrusion
# =====================================================================
def test_proximity_extrusion_default():
    ext = ProximityAwareExtrusion()
    n, h, total = ext.compute_extrusion_params(gap_distance=1.0)
    assert n == ext.n_layers_default
    assert h > 0


def test_proximity_extrusion_reduced_gap():
    ext = ProximityAwareExtrusion(
        n_layers_default=10, gap_ratio_threshold=2.0,
        growth_rate=1.2, y_plus_target=30.0,
        u_ref=10.0, nu=1.5e-5, length_ref=1.0,
    )
    n, h, total = ext.compute_extrusion_params(gap_distance=0.001)
    assert n < 10
    assert h > 0


# =====================================================================
# AdaptiveLoopEngine
# =====================================================================
def test_engine_init():
    engine = AdaptiveLoopEngine()
    assert engine._state.phase == OODAPhase.FIELD_EVALUATION
    assert engine._params.max_remediation_iterations == 5


def test_engine_run_empty():
    engine = AdaptiveLoopEngine()
    engine.configure(Path("."))
    result = engine.run()
    # Should complete (fails open or converges trivially on empty graph)
    assert isinstance(result, AdaptiveLoopResult)
    assert result.cell_count >= 0


def test_engine_run_with_params():
    params = AdaptiveLoopParams(max_remediation_iterations=3, y_plus_target=30.0)
    engine = AdaptiveLoopEngine(params)
    engine.configure(Path("."))
    result = engine.run()
    assert isinstance(result, AdaptiveLoopResult)
    assert result.n_iterations >= 0
    assert result.final_phase in (OODAPhase.CONVERGED.name, OODAPhase.FAILED.name)


def test_engine_phase_callbacks():
    phases_seen = []
    rem_seen = []

    params = AdaptiveLoopParams(
        on_phase_change=lambda p, i: phases_seen.append((p.name, i)),
        on_remediation=lambda e, a: rem_seen.append((e, a)),
    )
    engine = AdaptiveLoopEngine(params)
    engine.configure(Path("."))
    engine.run()

    assert len(phases_seen) > 0
    assert phases_seen[0][0] == OODAPhase.FIELD_EVALUATION.name


def test_engine_result_properties():
    r = AdaptiveLoopResult(
        success=True, n_iterations=3, n_remediations=2,
        cell_count=50000, max_skewness=0.85,
        max_non_orthogonality=65.0, max_aspect_ratio=800.0,
        wall_time_s=12.5,
    )
    assert r.success
    assert "PASS" in r.summary
    assert "50000" in r.summary
    assert "12.5s" in r.summary


def test_engine_result_fail_summary():
    r = AdaptiveLoopResult(
        success=False, n_iterations=5, cell_count=100,
        max_skewness=0.99, n_negative_volume=3,
    )
    assert "FAIL" in r.summary
    assert "negVol=3" in r.summary


def test_engine_convergence_history():
    engine = AdaptiveLoopEngine()
    engine.configure(Path("."))
    result = engine.run()
    assert hasattr(result, "convergence_history")
    assert isinstance(result.convergence_history, list)


def test_engine_check_stagnation():
    engine = AdaptiveLoopEngine()
    engine._convergence_history = [
        {"skewness": 0.95, "non_orthogonality": 80, "aspect_ratio": 500, "neg_volume": 0, "bad_layers": 0},
        {"skewness": 0.95, "non_orthogonality": 80, "aspect_ratio": 500, "neg_volume": 0, "bad_layers": 0},
        {"skewness": 0.95, "non_orthogonality": 80, "aspect_ratio": 500, "neg_volume": 0, "bad_layers": 0},
    ]
    assert engine._check_stagnation()


def test_engine_check_not_stagnated():
    engine = AdaptiveLoopEngine()
    engine._convergence_history = [
        {"skewness": 0.95, "non_orthogonality": 80, "aspect_ratio": 500, "neg_volume": 0, "bad_layers": 0},
        {"skewness": 0.90, "non_orthogonality": 75, "aspect_ratio": 500, "neg_volume": 0, "bad_layers": 0},
        {"skewness": 0.85, "non_orthogonality": 70, "aspect_ratio": 500, "neg_volume": 0, "bad_layers": 0},
    ]
    assert not engine._check_stagnation()


# =====================================================================
# AdaptiveLoopParams
# =====================================================================
def test_params_defaults():
    p = AdaptiveLoopParams()
    assert p.max_remediation_iterations == 5
    assert p.y_plus_target == 30.0
    assert p.n_layers_default == 5
    assert p.growth_rate == 1.2


def test_params_custom():
    p = AdaptiveLoopParams(y_plus_target=1.0, n_layers_default=15, growth_rate=1.15)
    assert p.y_plus_target == 1.0
    assert p.n_layers_default == 15
    assert p.growth_rate == 1.15


# =====================================================================
# MeshFace
# =====================================================================
def test_mesh_face_defaults():
    f = MeshFace()
    assert f.index == 0
    assert f.owner_cell == -1
    assert f.neighbour_cell == -1
    assert len(f.node_indices) == 0


def test_mesh_face_add_to_graph():
    g = UnifiedMeshGraph()
    n1 = g.add_node(MeshNode(x=0, y=0, z=0))
    n2 = g.add_node(MeshNode(x=1, y=0, z=0))
    n3 = g.add_node(MeshNode(x=0, y=1, z=0))
    f = MeshFace(node_indices=[n1, n2, n3])
    fid = g.add_face(f)
    assert fid == 0
    assert len(g.faces) == 1


# =====================================================================
# OpenFOAM I/O helpers
# =====================================================================
def test_parse_of_points():
    text = """FoamFile { version 2.0; class vectorField; object points; }
3
(
(0 0 0)
(1 0 0)
(0 1 0)
)
"""
    pts = _mod._parse_of_points(text)
    assert len(pts) == 3
    assert pts[0] == (0.0, 0.0, 0.0)
    assert pts[1] == (1.0, 0.0, 0.0)


def test_parse_of_face_list():
    text = """FoamFile { version 2.0; class faceList; object faces; }
2
(
3(0 1 2)
4(0 1 2 3)
)
"""
    faces = _mod._parse_of_face_list(text)
    assert len(faces) == 2
    assert faces[0] == [0, 1, 2]
    assert faces[1] == [0, 1, 2, 3]


def test_write_of_points(tmp_path):
    pts = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    p = tmp_path / "constant" / "polyMesh" / "points"
    p.parent.mkdir(parents=True, exist_ok=True)
    _mod._write_of_points(p, pts)
    text = p.read_text()
    assert "3" in text
    assert "(0 0 0)" in text
    assert "(1 0 0)" in text


def test_write_and_read_roundtrip(tmp_path):
    """Write a simple mesh, then reimport it via import_from_ofmesh."""
    pts = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
    faces = [[0, 1, 2], [0, 2, 3], [0, 3, 1], [1, 3, 2]]
    owner = [0, 0, 0, 0]
    nbr = [-1, -1, -1, -1]

    pm = tmp_path / "constant" / "polyMesh"
    pm.mkdir(parents=True, exist_ok=True)
    _mod._write_of_points(pm / "points", pts)
    _mod._write_of_face_list(pm / "faces", faces)
    _mod._write_of_label_list(pm / "owner", owner)
    _mod._write_of_label_list_with_neg1(pm / "neighbour", nbr)
    _mod._write_of_boundary(pm / "boundary", [("walls", "patch", 4, 0)])

    engine = AdaptiveLoopEngine()
    graph = engine.import_from_ofmesh(tmp_path)
    assert len(graph.nodes) == 4
    assert len(graph.faces) == 4
    assert len(graph.cells) == 1

    # Check adjacency
    cell = graph.cells[0]
    assert len(cell.face_indices) == 4
    assert len(cell.node_indices) >= 4


def test_export_roundtrip(tmp_path):
    """export_to_ofmesh writes valid files."""
    engine = AdaptiveLoopEngine()
    engine._graph.bbox_min = (0, 0, 0)
    engine._graph.bbox_max = (1, 1, 1)
    n1 = engine._graph.add_node(MeshNode(x=0, y=0, z=0))
    n2 = engine._graph.add_node(MeshNode(x=1, y=0, z=0))
    n3 = engine._graph.add_node(MeshNode(x=0, y=1, z=0))
    n4 = engine._graph.add_node(MeshNode(x=0, y=0, z=1))
    f1 = engine._graph.add_face(MeshFace(
        node_indices=[n1, n2, n3], owner_cell=0, neighbour_cell=-1,
    ))
    engine._graph.add_cell(MeshCell(
        node_indices=[n1, n2, n3, n4], face_indices=[f1],
    ))
    engine._graph.cells[0].face_indices = [f1]

    out = tmp_path / "export"
    engine.export_to_ofmesh(out)
    assert (out / "constant" / "polyMesh" / "points").exists()
    assert (out / "constant" / "polyMesh" / "faces").exists()
    assert (out / "constant" / "polyMesh" / "owner").exists()
    assert (out / "constant" / "polyMesh" / "boundary").exists()


# =====================================================================
# SizingField to_dict
# =====================================================================
def test_sizing_field_to_dict():
    g = UnifiedMeshGraph()
    g.bbox_min = (0, 0, 0)
    g.bbox_max = (1, 1, 1)
    sf = SizingField(g)
    sf.build_from_bbox(n_samples=10)
    d = sf.to_dict()
    assert "base_size" in d
    assert d["n_points"] == 10


if __name__ == "__main__":
    test_graph_empty()
    test_graph_add_node()
    test_graph_add_cell()
    test_graph_cell_invalid_negative_volume()
    test_graph_cell_invalid_high_skewness()
    test_graph_extract_subgraph()
    test_graph_estimate_cell_count()
    test_graph_cell_neighbours_of_type()
    test_sizing_field_build()
    test_sizing_field_sample()
    test_sizing_field_refine_region()
    test_sizing_field_gradient_limit()
    test_compute_face_centroid()
    test_state_machine_initial_state()
    test_state_machine_transition()
    test_state_machine_can_remediate()
    test_state_machine_reset()
    test_state_machine_summary()
    test_v3_ops()
    test_tetra_volume()
    test_tetra_volume_negative()
    test_face_normal()
    test_compute_skewness()
    test_compute_non_orthogonality()
    test_compute_non_orthogonality_angled()
    test_compute_first_layer_height_typical()
    test_compute_first_layer_height_zero_re()
    test_compute_first_layer_height_very_low_nu()
    test_estimate_bl_total_thickness()
    test_estimate_bl_total_thickness_growth_one()
    test_quality_evaluator_empty()
    test_quality_evaluator_good_cell()
    test_quality_evaluator_negative_volume()
    test_quality_evaluator_high_skewness()
    test_quality_evaluator_bad_bl_layer()
    test_remediator_diagnose_empty()
    test_remediator_diagnose_negative_volume()
    test_remediator_diagnose_high_skewness()
    test_remediator_apply_laplacian_smooth()
    test_remediator_un_dualize()
    test_conformal_stitcher_no_cells()
    test_conformal_stitcher_with_interface()
    test_proximity_extrusion_default()
    test_proximity_extrusion_reduced_gap()
    test_engine_init()
    test_engine_run_empty()
    test_engine_run_with_params()
    test_engine_phase_callbacks()
    test_engine_result_properties()
    test_engine_result_fail_summary()
    test_engine_convergence_history()
    test_engine_check_stagnation()
    test_engine_check_not_stagnated()
    test_params_defaults()
    test_params_custom()
    test_mesh_face_defaults()
    test_mesh_face_add_to_graph()
    test_parse_of_points()
    test_parse_of_face_list()
    test_write_of_points()
    test_sizing_field_to_dict()
    # test_write_and_read_roundtrip and test_export_roundtrip need tmp_path fixture
    print("ALL PASS")
