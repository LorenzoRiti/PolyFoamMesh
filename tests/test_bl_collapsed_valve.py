"""H4 regression: production (collapsed) valve dual + vertex-exclusion BL.

Pins the H4 result.  On the production converter output
(``collapse_smooth_edges=True, boundary_feature_angle=40.0,
collapse_volume_tolerance=0.10`` — the exact kwargs used by
``core/openfoam_runner.py``) the face-level exclusion cascade stalls a few
pyramid violations above the input (FASE 7/8: 373-385 vs 370), while the
``decoupled_vertex`` mode closes at scale 0.6 with 4,008 of 42,130 wall
faces excluded and 75,360 prism cells (5 builds, ~297 s).

Deterministic numbers; engine gate only, no WSL required.  Marked slow:
the collapsed conversion takes ~2 min and the BL ~5 min.

Skipped when the valve tet backup is absent (it lives outside the repo,
under C:/polybench/valve1).
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

from polyfoammesh.core.bl_poly import PolyBoundaryLayerEngine  # noqa: E402
from polyfoammesh.core.tet_poly_dual import TetPolyDualConverter  # noqa: E402
from _test_helpers import space_free_tmp_root  # noqa: E402

TET_BACKUP = Path("C:/polybench/valve1/constant/polyMesh_tet_backup")
WORK_ROOT = space_free_tmp_root() / "cfmesh_bench" / "bl_collapsed_valve"


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    shutil.rmtree(WORK_ROOT, ignore_errors=True)


@pytest.mark.slow
def test_collapsed_valve_vertex_exclusion_closes():
    if not TET_BACKUP.exists():
        pytest.skip(f"valve tet backup not present: {TET_BACKUP}")
    case = WORK_ROOT / "case"
    shutil.rmtree(case, ignore_errors=True)
    poly = case / "constant" / "polyMesh"
    poly.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TET_BACKUP, poly)
    (case / "system").mkdir(parents=True, exist_ok=True)

    # production converter kwargs (keep in sync with
    # core/openfoam_runner.py:1652)
    dres = TetPolyDualConverter(
        case, log=lambda m: None,
        collapse_smooth_edges=True,
        boundary_feature_angle=40.0,
        collapse_volume_tolerance=0.10,
    ).run()
    assert dres.success, dres.errors

    r = PolyBoundaryLayerEngine(case, log=lambda m: None).run(
        n_layers=2, first_height=1e-5, growth_rate=1.2, apply_to_all=True,
        local_termination="decoupled_vertex",
        concavity_criterion="dual_convexity",
    )
    assert r.success, (r.errors, r.warnings)
    assert r.stats.get("scale") == 0.6, r.stats
    assert r.stats.get("local_excluded_faces") == 4008, r.stats
    assert r.stats.get("local_termination_mode") == "decoupled_vertex"
    assert r.n_prism_cells == 75360, r.n_prism_cells
    # the written mesh must carry the BL invariants
    from polyfoammesh.core import foam_mesh_io as fio
    points, faces, owner, neigh, patches = fio.read_polymesh(poly)
    n_int = len(neigh)
    assert len(owner) == len(faces)
    assert len(neigh) == n_int
    assert int(max(owner.max(), neigh.max())) + 1 > 152086
