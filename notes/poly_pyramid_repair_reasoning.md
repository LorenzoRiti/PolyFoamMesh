# Concave pyramid repair (Phase 3) — reasoning (2026-10-05)

Written BEFORE the Phase-3 measurement.

## Gap being addressed

concave_bracket/poly: NOmax 62.66 (already BETTER than snappy's 64.76) but
checkMesh reports Failed because the dual carries **36 input face-pyramid
violations** (non-convex boundary cells) — measured in the dual-only
isolation probe. The BL does not create them; they are inherited.

## Mechanism

`poly_cell_merge.repair_concave_cells_if_safe` merges each non-star-shaped
cell with an interior neighbour whose virtual merge passes the pyramid test
(measured on the reference valve: 302 of 348 concave cells fixed), then
gates the whole operation with an in-process skew check (keep-best). Its
own docstring warns: apply it AFTER the boundary layer (pre-BL merges
tripled aspect on the valve) and real checkMesh can still catch a
regression the in-process gate misses (slot1: 3 new highly-skewed faces) —
hence a real-checkMesh measurement and keep-best.

## Prediction (write BEFORE the experiment)

- concave_bracket: face pyramids 36 → **0-10**; checkMesh `Failed` → `OK`
  (the 4.37 skew and 105.41 NOmax are BL-dominated and may stay); if the
  merge cannot fix enough cells, the in-process gate refuses and the mesh
  is unchanged (honest no-op).
- Healthy geometries: no concave cells → no-op.
- slot1 (documented risk): watch for new skew; if the in-process gate
  misses it and real checkMesh regresses, the flag is killed for the
  benchmark path.

## Measurement plan

- `--poly-merge-repair` post-BL in `_benchmark_poly_one`; concave full-tier
  + fast-tier regression (all 6 fast geoms).
- Kill criterion: any currently-passing row starts failing, or concave does
  not improve its pyramid count → not promoted.

## Status

- [ ] Prediction written
- [ ] B-side measured
- [ ] Verdict

## B-side (committed comparison JSONs)

- concave_bracket (full): "merge repair: fixed 68 of 81 concave cells",
  NO 105.41 → **92.84**, skew ~unchanged → still FAIL (partial win).
- venturi (fast): fixed 12 of 33, NO 146.41 → 136.22 → still FAIL.
- groove1 (fast, single runs with/without the flag): BL fails in BOTH runs
  (pre-existing: the baseline 50.59 is the dual-only number); with the flag
  the repair merges 13 of 15 cells and worsens NO 50.59 → **65.78**, skew
  3.67 → **5.5** — the documented in-process-gate blind spot materialized
  (a currently-passing row starts failing).
- cylinder/cube_duct/thin_gap/slot1: no candidates / unchanged.

VERDICT: KILLED. The repair fixes most concave cells on concave_bracket and
improves its NOmax by 12°, but it regresses groove1 badly and the in-process
gate cannot catch it — per the phase gate (no passing row may start
failing) it is not promoted. Worth revisiting only with a real-checkMesh
keep-best loop, which is not available in the benchmark path.

- [x] Prediction written
- [x] B-side measured
- [x] Verdict: KILLED (not promoted)
