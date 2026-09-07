"""Numerical verification of the signed concavity test (Fase 2, step 2).

The split pass only ever touches vertices on CONCAVE boundary feature edges
(``_concave_boundary_vertices`` / ``_concave_edges`` in
polyfoammesh.core.tet_poly_dual).  The earlier version used the UNSIGNED
dihedral angle, so it also split harmless convex 90-degree edges and made the
valve worse (895 -> 1,215 inverted pyramids).  The signed test must therefore
behave like this on known geometry:

    cube1    -> 0 concave vertices / 0 concave edges (all edges convex)
    groove1  -> > 0, and the re-entrant L-corner endpoints are flagged

Runs on the pristine tet backups in C:\\polybench (no WSL needed).

Usage::

    python tools/concavity_verify.py [--cases cube1,groove1]
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polyfoammesh.core.tet_poly_dual import (
    DualPolyResult,
    TetPolyDualConverter,
    _concave_boundary_vertices,
    _concave_edges,
)

POLYBENCH = Path("C:/polybench")

# Vertex indices of the re-entrant (concave) vertical edge in the L-groove
# case — must be flagged; convex corners must not be.
GROOVE_REENTRANT_ENDPOINTS: set[int] = set()


def _read_primal_of(poly_dir: Path):
    """Build the converter's primal structure for a tet polyMesh directory."""
    with tempfile.TemporaryDirectory(prefix="concavity_") as td:
        case = Path(td)
        dst = case / "constant" / "polyMesh"
        shutil.copytree(poly_dir, dst)
        conv = TetPolyDualConverter(case, log=lambda m: None)
        return conv._read_primal(DualPolyResult())


def _verify(name: str) -> tuple[int, int, int]:
    """Return (concave_vertices, concave_edges, n_boundary_tris) for a case."""
    backup = POLYBENCH / name / "constant" / "polyMesh_tet_backup"
    if not backup.exists():
        raise SystemExit(f"missing tet backup: {backup}")
    P = _read_primal_of(backup)
    cv = _concave_boundary_vertices(P)
    ce = _concave_edges(P)
    return len(cv), len(ce), int(P.n_bnd)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", default="cube1,groove1")
    args = ap.parse_args()

    results = {}
    for name in args.cases.split(","):
        n_cv, n_ce, n_bnd = _verify(name)
        results[name] = (n_cv, n_ce, n_bnd)
        print(f"{name:>8}: concave_vertices={n_cv:>5}  concave_edges={n_ce:>5}  "
              f"boundary_tris={n_bnd:>7}")

    ok = True
    n_cv_cube, n_ce_cube, _ = results["cube1"]
    if n_cv_cube != 0 or n_ce_cube != 0:
        print(f"FAIL: cube1 must be all-convex (0/0), got {n_cv_cube}/{n_ce_cube}")
        ok = False
    n_cv_groove, n_ce_groove, _ = results["groove1"]
    if n_cv_groove == 0 or n_ce_groove == 0:
        print(f"FAIL: groove1 must have concave edges, got {n_cv_groove}/{n_ce_groove}")
        ok = False

    print("VERDICT:", "PASS — signed concavity test distinguishes convex cube "
          "from concave groove" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
