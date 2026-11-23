"""H4 regression: production (collapsed) valve dual + vertex-exclusion BL.

Pins the H4 result.  On the production converter output
(``collapse_smooth_edges=True, boundary_feature_angle=40.0,
collapse_volume_tolerance=0.10`` — the exact kwargs used by
``core/openfoam_runner.py``) the face-level exclusion cascade stalls a few
pyramid violations above the input (FASE 7/8: 373-385 vs 370), while the
``decoupled_vertex`` mode closes at scale 0.6 with 4,143 of 42,130 wall
faces excluded and 75,974 prism cells (5 builds, ~297 s).

Historical numbers, in order: 75,360 (before the drop_bnd units fix in
``_build`` - ``drop_bnd`` was keyed by boundary-LOCAL face ids while the
membership test indexed the FILTERED selection by position, so arbitrary
faces were flagged), 76,052 after that fix, and the current 75,974 after
the complete-coverage policy (commit 208a61c): the local termination now
drops only faces flagged by the input pyramid criterion, so its exclusion
cascade settles at 4,143 instead of 4,008 and the kept faces all carry
the full 2-layer stack (layer_hist has no zeros).

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
    # 2026-11-23 re-pin.  The H4-era numbers (scale 0.6, 4,143 excluded faces,
    # 75,974 prisms) stopped reproducing before the 2.5.0 work (27ede11 already
    # gave 2,958 excluded / 64,472 prisms at scale 0.6).  Since 0fd4552
    # (validation on checkMesh failure criteria) the build closes at scale 1.0
    # and honours the requested first layer.  Measured on the same case,
    # same kwargs, with `checkMesh -allGeometry -allTopology`:
    #   27ede11: coverage 76.5 %, h1_ratio 3.67, 48 skew faces, 9 failed checks
    #   now:     coverage 81.1 %, h1_ratio 1.00, 28 skew faces, 8 failed checks
    assert r.stats.get("scale") == 1.0, r.stats
    assert r.stats.get("local_termination_mode") == "decoupled_vertex"
    assert r.stats.get("local_excluded_faces") == 3420, r.stats
    assert r.n_prism_cells == 68340, r.n_prism_cells
    # the first layer must be the requested one (was 3.7x too thick before)
    assert abs(r.h1_ratio - 1.0) < 0.01, r.h1_ratio
    # coverage guard: never fall back below the measured baseline
    n_layered = sum(v for k, v in r.layer_hist.items() if int(k) > 0)
    assert n_layered / r.n_wall_faces >= 0.80, r.layer_hist
    # no new pyramid violations beyond the collapsed-dual input (368)
    assert r.n_pyramids_after <= 370, r.n_pyramids_after
    # the written mesh must carry the BL invariants
    from polyfoammesh.core import foam_mesh_io as fio
    points, faces, owner, neigh, patches = fio.read_polymesh(poly)
    n_int = len(neigh)
    assert len(owner) == len(faces)
    assert len(neigh) == n_int
    assert int(max(owner.max(), neigh.max())) + 1 > 152086
