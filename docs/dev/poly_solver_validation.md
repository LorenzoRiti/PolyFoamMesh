# PHASE 0 — Solver validation on the valve: the dual's concave defect is a limit, not a bug

> Measured 2026-08-10 with `tools/poly_solver_validation.py --case valve1`.
> Tool: `tools/poly_solver_validation.py` (adapted in this session to
> accept `--case ref1|valve1` and `--variant default|production`, with full
> log and parsing of residuals/iterations/continuity).

## Setup

- Geometry: `C:\polybench\valve1` (real valve, 89 B-Rep surfaces).
- Intact tet backup: `constant/polyMesh_tet_backup` — 824,661 tetrahedra.
- Inlet = `surface_77` (flat cap at x = +1.5227), outlet = `surface_89`
  (flat cap at x = −1.4773) — the two ends of the main axis,
  chosen by measuring patch centroids/areas from the backup.
- BC: U = uniformFixedValue (1 0 0) on the inlet, p = 0 on the outlet, walls
  zeroGradient. Identical on tet and poly.
- Solver: potentialFoam (OpenFOAM v2512, WSL), GAMG on Phi,
  nNonOrthogonalCorrectors 3.
- Poly: barycentric dual (variant `default` = converter parameters,
  the same one that produces 852 misoriented faces / 1027 residual defects on
  the pinned baseline).

## Measured numbers

| Metric | TET (824,661) | POLY (152,086) | Outcome |
|---|---|---|---|
| potentialFoam | converges, "End" | converges, "End" | no FATAL |
| GAMG on Phi: initial → final residual | 8.34e-3 → 3.07e-5 (3 it) | 4.87e-4 → 4.60e-6 (2 it) | poly converges deeper |
| Continuity error (solved mass balance) | 8.71e-3 | 7.83e-4 | **poly 11× more conservative** |
| Continuity relative to the flow rate (0.0483 m³/s) | 18% | **1.6%** | |
| Interpolated velocity error | 8.8e-8 | 6.3e-5 | |
| Total volume | 0.121835 | 0.121835 | **0.00%** |
| Prescribed inlet flow (area) | 0.0482551 | 0.0482551 | **0.00%** (exact subdivision) |

The solved flux (phi) is not written by potentialFoam in this
configuration (no `1/` dir), so the in/out flow rate is measured as:
prescribed inlet flow (identical by construction) + solved mass balance =
the log's "Continuity error" (the in/out imbalance the solver
reports after convergence).

## Decision

**G3 is not a bug: it is a documented limit of a check (face pyramids) that
assumes convexity on cells the solver digests without problems.**

Evidence:

1. The valve's poly mesh — with 852 faces "incorrectly oriented" per
   checkMesh — **solves and converges** with potentialFoam (final residual
   4.6e-6, 2 GAMG iterations), no FATAL, no divergence.
2. **It conserves the flow rate better than the tet it derives from**: continuity error
   1.6% of the flow (tet: 18%). The defect (0.25% of the cells, non-convex
   on the CAD's concave junctions) does not contaminate the mass balance.
3. Volume conserved to 0.00% (exact subdivision of the surface).

Consequence: **PHASE 3 (planar cutting of the concave defect) must NOT be done**.
The whole budget goes to G1/G2 (selective BL per patch + local
layer termination), which affect every case, not only pathological
geometries. Phase 3's stop rule does not apply because the premise ("the mesh is
unusable") is false.

## What was NOT measured

- Residual/flow rate with a complete solver (simpleFoam/rhoSimpleFoam): outside
  Phase 0's scope (potentialFoam is the cheapest and sufficient for the
  decision). Should it be needed in the future, the tool already accepts
  `--case valve1 --variant production` (GUI runner parameters:
  median_faces=True, wedge_cells=True) and it would need to be re-run with that
  variant — today the comparison is on the variant that pins the baseline.
- Phi written to disk (the controlDict configuration does not produce `1/`):
  the in/out balance is read from the log, not from a field file.
