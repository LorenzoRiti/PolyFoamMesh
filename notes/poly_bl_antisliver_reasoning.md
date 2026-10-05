# BL anti-sliver — base-face size filter (Phase 1) — reasoning (2026-10-05)

Written BEFORE the Phase-1 measurement (code committed `675ee33`/`8b44472`).

## Gap being addressed

Poly rows from `benchmarks/results/GAP_ANALYSIS.md` (numbers verbatim):
cube_duct 80.64 (best snappy 46.21), venturi 146.41 (60.26), concave_bracket
105.41 (64.76). Dual-only isolation (probe 2026-10-05): 51.87 / 74.00 /
62.66+36 pyramids — the BL adds +29…+72 NOmax and aspect ×6-70 while the
average stays flat, i.e. a handful of extreme prisms.

## Hypothesis

`bl_poly` extrudes EVERY selected boundary face. The dual's collapsed walk
emits some very small boundary polygons (venturi: 83/2059 faces < 1e-6 m²,
min 4.97e-8; p1 2.85e-7 against a median of 2.36e-5). A tiny base becomes a
sliver prism whose shared face with the adjacent core cell is tiny relative
to the centre-to-centre distance — exactly what the non-orthogonality angle
measures (146.41° on a 2.4e-7 m² face). Dropping base faces whose size is a
small fraction of the median selected-face size removes the sliver seed
without touching the rest of the BL (the build already handles
selected/unselected terminator bands — "NO auto-close" in `_select_faces`).

## Prediction (write BEFORE the experiment)

- cube_duct: NOmax 80.64 → **~52-58** (the dual-only 51.87 dominates; the
  filter removes the sliver prisms) → PASS; aspect 554 → ≤30.
- venturi: NOmax 146.41 → **~74-85** (dual-only is 74.00; some terminator
  bands may add a few degrees) → still FAIL on this phase alone.
- concave_bracket: NOmax 105.41 → **~63-75** (dual-only 62.66) but the 36
  input pyramids remain → checkMesh still Failed.
- Healthy geometries (cylinder/groove1/slot1/thin_gap/multi_inlet): no
  sub-threshold base faces → identical metrics.
- Expected phase-1 pass rate: 4/10 → 5/10 (cube_duct).
- Cost: ≤5% boundary faces not extruded on the gap geometries; wall time
  ~unchanged (the filter is O(n_faces)).

## Measurement plan

- B-side: `--poly-bl-min-face` at fractions {0.2 (primary), 0.1, 0.3} on
  cube_duct+venturi (fast) and concave_bracket (full); records + logs
  (`n_excluded_small_faces`, `[bl] excluded …`).
- Kill criterion: if cube_duct does not PASS, or a healthy geometry
  regresses by >2° on the primary metric, or a passing row starts failing →
  the phase is killed and documented.

## Status

- [ ] Prediction written
- [ ] B-side measured
- [ ] Verdict


## B-side (fraction 0.2, committed comparison_20261006T011258.json)

| geometry | A-side | B-side (0.2) | verdict |
|---|---|---|---|
| cube_duct | 80.64 / skew 4.92 / aspect 554.2 | 80.61 / skew 7.77 / aspect 554.2 | NO unmoved, skew WORSE |
| venturi | 146.41 / 3.66 / 108.7 | 127.94 / 4.02 / 181.2 | NO better, skew/aspect worse |

VERDICT: KILLED. The filter excludes 140 base faces but the gate-breaking
cells are not (only) tiny-base slivers: cube_duct's 80.6 face survived, and
the terminator bands introduced by the exclusions worsen skew/aspect. The
hypothesis "tiny base faces are the dominant seed" is falsified for
cube_duct; the BL damage is broader (small cells between prisms and core,
not just tiny bases). Phase 2 (dual config) proceeds independently.

- [x] Prediction written
- [x] B-side measured
- [x] Verdict: KILLED (not promoted)