# Lane D2 — zonal worst-element aspect relaxation (prediction BEFORE testing)

## Literature grounding

- Freitag, "On combining Laplacian and optimization-based mesh smoothing
  techniques" (1997): optimization-based smoothing that targets the WORST
  elements (minimax flavour) beats pure Laplacian, which optimizes the
  average and ignores the tail. Our global objective does exactly what
  Freitag warns about: 14 bad cells drown in 10,608 good ones.
- Chen/Holst ODT and Du CVT were considered and REJECTED for this lane:
  they move primal tet vertices (interior-only in classic ODT; B-ODT
  extends to boundaries), but our defect sits in dual cells at the CAP
  boundary layer whose elongation comes from the dual construction, not
  from primal vertex positions alone — plus no primal tet mesh is
  available for the cylinder case (only the dual), so primal-side methods
  cannot even run here. Zonal dual-side relaxation applies where the
  defect lives, with available inputs.

## Design (new opt-in, default path bit-identical)

`smooth_dual_mesh(..., worst_aspect_thr=0.0)`: 0 = off (today's behaviour
exactly). When > 0, each iteration:
1. computes per-cell aspect (existing replica),
2. builds the zone = cells with ar > thr, vertex mask = zone vertices,
3. adds MASKED variants of the active candidate moves (displacement
   zeroed outside the zone) — moves the global steps cannot express,
4. acceptance unchanged (defect counts dominate, guards hold) with the
   objective gaining a zonal term `lam_zone * max(0, max_aspect-thr)^2`
   (L_inf on the worst cell) ON TOP of the existing global aspect term
   (kept, so non-zone damage still counts — the trial-D lesson: invisible
   sub-threshold drift is what failed checkMesh last time). A tail SUM was
   tried first and measurably FAILED (max ROSE 4.489 -> 4.542 while the sum
   "improved") — minimax means minimax. lam_zone balanced from a measured
   scale print (base vs zone term at start; cylinder L_inf: ~5834).

## Prediction

Targeted at the measured tail (74 cells > 3.5, max 4.489): masked moves
should reduce the zonal excess WITHOUT the global side-effects that
vetoed zone-helping global moves — predicted max-aspect drop of 0.2-0.5
(weaker than merge's -0.48 because vertices move less than topology, but
without merge's severity cost since no cells are destroyed/created).
Real checkMesh decides: promotion needs max-aspect down AND no severity
regression (NOmax/skew/failed not worse) — the exact bar merge failed.

Falsification: no accepted zonal move (zone already a constrained optimum
given fixed topology + guards) → documented negative; the remaining path
is then ONLY the split operator (Lane D verdict stands, strengthened:
neither vertex moves of any flavour nor merges can close it).

Protocol: drive `smooth_dual_mesh` directly on `cylinder_axis_both`
(same isolation as the merge trial — no reconversion), write scratch
copy, real OF2512 checkMesh vs `checkMesh_ref.txt` (aspect 4.489,
NOmax 36.56, skew 1.17, failed 1).

## Results (measured 2026-09-15, real OF2512 checkMesh via WSL)

Control first: production-mirror flags (compactness+axis, no zonal) move
ZERO vertices — `both` is converged, harness neutral. Then:

| run | maxAspect (in-proc) | movedVerts |
|---|---|---|
| zonal thr=3.5, lam=10 (sum term, weak) | 4.489 -> 4.597 WORSE | 454 |
| zonal thr=3.5, lam=564 (sum, balanced) | 4.489 -> 4.542 WORSE | — |
| zonal thr=3.5, lam=5838 (L_inf) | 4.489 -> 4.468 | 454 |
| zonal thr=3.0, lam=2600, 30 it (L_inf) | 4.489 -> 4.468 SAME WALL | 1267 |

Real checkMesh, final config vs `checkMesh_ref.txt`:

| metric | reference | zonal | Δ |
|---|---|---|---|
| max aspect ratio | 4.489 | 4.468 | **-0.5%** |
| non-ortho max / avg | 36.56 / 9.606 | 36.52 / 9.602 | same/better |
| max skewness | 1.17 | 1.168 | same |
| failed checks | 1 | 1 | same |

Verdict: **mechanism works exactly as designed (focused moves, ZERO
severity cost — the anti-merge-trial), but magnitude is negligible
(-0.5%) and converged (3x iterations, wider zone: same 4.468 wall).**
Two intermediate failures were caught and fixed honestly along the way:
masked candidates evaluated LAST never ran (reordered first); tail-SUM
objective raised the max while "improving" (replaced by L_inf). The
remaining 4.47 -> 3.02 gap is topology-bound: no vertex move of any
flavour (global, compactness, axis-balance, zonal-masked) can fix
in-plane-elongated cap slabs — only a directional SPLIT operator (or
finer cap tessellation at meshing time, changing cell count) can.
Anisotropic gmsh resizing was considered and rejected on mechanism:
uniform cap refinement scales thickness and in-plane extent together
(ratio unchanged); in-plane-only refinement just adds cells without
fixing per-cell quality parity. NOT promoted to production wiring (gain
below promotion noise, though the opt-in mode stays tested in code);
Lane D negative stands, now triply strengthened (smoothing optimum +
merge wall + zonal wall).
