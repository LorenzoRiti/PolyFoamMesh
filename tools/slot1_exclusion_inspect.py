"""Slot1 decoupled_vertex exclusion anomaly: vertex valence hypothesis test.

Reuses the geometry builders from tools/h4_generalization_rebench.py
(cube_duct in-memory, groove1 and slot1 tet-backups). Performs ONE
TetPolyDualConverter conversion per geometry (no double-BL run; no
instrumentation), then measures the *valence* of every wall vertex
on the dual mesh = the number of boundary faces incident to that
vertex. The hypothesis: slot1 has higher wall-vertex valence than
groove1 / cube_duct, which is why the per-vertex exclusion propagation
in `decoupled_vertex` is 3x more aggressive than `decoupled` on slot1
(378 vs 132) but identical (0 vs 0) on the others.

Reads converted dual mesh, NOT the BL output mesh.
"""
from __future__ import annotations

import io
import shutil
import sys
from collections import Counter
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parent / "tests"))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

import numpy as np  # noqa: E402

import polyfoammesh.core.foam_mesh_io as fio  # noqa: E402
from polyfoammesh.core.tet_poly_dual import TetPolyDualConverter  # noqa: E402
from polyfoammesh.core.gmsh_subprocess import write_case_skeleton  # noqa: E402

WORK = Path("C:/polybench2/h4_generalization")
GROOVE1_TET = Path("C:/polybench/groove1/constant/polyMesh_tet_backup")
SLOT1_TET = Path("C:/polybench/slot1/constant/polyMesh_tet_backup")


# Builders copied verbatim from tools/h4_generalization_rebench.py
def build_cube_duct_case() -> Path:
    from test_tet_poly_dual import build_tet_case
    case = WORK / "case_cube"
    shutil.rmtree(case, ignore_errors=True)
    (case / "constant").mkdir(parents=True, exist_ok=True)
    c = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
    ], dtype=float)
    pts = np.vstack([c, [[0.5, 0.5, 0.5]]])
    faces2d = [
        [0, 1, 2, 3], [0, 4, 5, 1], [1, 5, 6, 2],
        [2, 6, 7, 3], [3, 7, 4, 0], [4, 7, 6, 5],
    ]
    tets = []
    for f in faces2d:
        for tri in ((f[0], f[1], f[2]), (f[0], f[2], f[3])):
            tets.append([tri[0], tri[1], tri[2], 8])
    build_tet_case(case, pts, tets, boundary_patch="all")
    return case


def prepare_tet_case(src: Path, dst: Path) -> Path:
    shutil.rmtree(dst, ignore_errors=True)
    (dst / "constant").mkdir(parents=True)
    shutil.copytree(src, dst / "constant" / "polyMesh")
    (dst / "system").mkdir(parents=True, exist_ok=True)
    write_case_skeleton(dst)
    return dst


def convert_default(case: Path) -> tuple:
    res = TetPolyDualConverter(case, log=lambda m: None).run()
    pts, faces, owner, neigh, _ = fio.read_polymesh(
        case / "constant" / "polyMesh")
    n_int = len(neigh)
    return pts, faces, owner, neigh, n_int


def wall_vertex_valence(pts, faces, n_int: int) -> tuple:
    """For every wall vertex, the number of incident boundary faces.
    Returns (mean, max, Counter of all valence values, n_wall_verts)."""
    boundary_faces = [tuple(f) for f in faces[n_int:]]
    n_wall_verts = 0
    valences: list[int] = []
    for f in boundary_faces:
        for v in f:
            pass
    # count incidence per vertex across all boundary faces
    counts: Counter = Counter()
    for f in boundary_faces:
        for v in set(f):
            counts[v] += 1
    valences = list(counts.values())
    if not valences:
        return 0.0, 0, Counter(), 0
    n_wall_verts = len(valences)
    mean_v = sum(valences) / len(valences)
    max_v = max(valences)
    return mean_v, max_v, Counter(valences), n_wall_verts


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    sources = {
        "cube_duct": (None, build_cube_duct_case),
        "groove1": (GROOVE1_TET, prepare_tet_case),
        "slot1": (SLOT1_TET, prepare_tet_case),
    }
    print(f"{'geometry':>10s}  {'n_wall_v':>9s}  {'mean':>7s}  {'max':>5s}  "
          f"{'p50':>5s}  {'p90':>5s}  {'p99':>5s}")
    results = {}
    for name, (src, builder) in sources.items():
        if src is not None and not src.exists():
            print(f"{name:>10s}  SKIPPED (tet missing: {src})")
            continue
        case = builder() if src is None else builder(src, WORK / f"{name}_case")
        try:
            pts, faces, owner, neigh, n_int = convert_default(case)
        except Exception as exc:
            print(f"{name:>10s}  CONVERT FAILED: {exc}")
            continue
        mean_v, max_v, ctr, n_wall = wall_vertex_valence(pts, faces, n_int)
        valences = sorted(ctr.values())
        if valences:
            import statistics
            p50 = statistics.median(valences)
            p90 = statistics.quantiles(valences, n=10)[-1] if len(valences) > 1 else valences[0]
            p99 = (statistics.quantiles(valences, n=100)[-1] if len(valences) > 1
                   else valences[0])
        else:
            p50 = p90 = p99 = 0
        print(f"{name:>10s}  {n_wall:>9d}  {mean_v:>7.2f}  {max_v:>5d}  "
              f"{p50:>5.1f}  {p90:>5.1f}  {p99:>5.1f}")
        results[name] = {
            "n_wall_verts": n_wall,
            "mean": mean_v,
            "max": max_v,
            "p50": p50,
            "p90": p90,
            "p99": p99,
            "distribution": dict(ctr),
        }
    # Save
    import json
    out = WORK / "slot1_valence.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
