# NON-ORTHO (first target: the hex `max_non_ortho` gate cluster) — reasoning (2026-10-05)

Written BEFORE any experiment and BEFORE any mesher change (zero mesher code
touched by this note). Lane chosen in `benchmarks/results/GAP_ANALYSIS.md`
(commit `78cff6f`); A-side baseline = `benchmarks/results/comparison_20261005T113621.json`
(commit `b1bc71f`).

## Gap being addressed

Rows from `GAP_ANALYSIS.md` (numbers verbatim):

- **Row 11** — groove1 / hex / threshold miss / `non_ortho_max`: us **75.98**, best snappy **42.71**. Notes: "Our only failing runner on groove1 (poly 50.59, gmsh 61.07 pass); both snappy pass."
- **Row 13** — slot1 / hex / threshold miss / `non_ortho_max`: us **77.28**, best snappy **39.40**. Notes: "poly (56.01) and gmsh (65.58) pass here; both snappy pass."
- **Row 14** — thin_gap / hex / threshold miss / `non_ortho_max`: us **73.75**, best snappy **42.28**. Notes: "snappy_expert timed out (1201.28 s) so best valid snappy is snappy_base; poly (66.25) and gmsh (65.84) pass."
- **Row 12** — mini_valve / hex / threshold miss / `non_ortho_max`: us **72.58**, best snappy **n/a (both timed out)**. Notes: "snappy produced no mesh (1204.07 / 1200.91 s timeouts); poly/gmsh 0 cells — no runner passed this geometry."

Lane context (GAP_ANALYSIS): `max_non_ortho` is the dominant cluster — **15 of 31
rows (48%), 8 of 10 geometries** — and it is the only cluster that decides the
gate: **all 9 of our quality-metric threshold misses are `non_ortho_max`**, 4 of
them these hex rows. Gate value: `THRESHOLDS["non_ortho_max"] = 70.0`
(`src/polyfoammesh/core/quality_thresholds.py:31`).

### Why this cluster and not the worst absolute row (venturi/poly 146.41)

1. **4 gate flips in one runner path vs 1 row.** All four hex rows fail on
   `non_ortho_max` *only* (baseline JSON: `failed_thresholds = ["non_ortho_max"]`),
   so one fix can take hex from 5/10 to 9/10 — past both snappy runners (6/10).
2. **The worst absolute row is not one coherent fix.** venturi/poly 146.41 is a
   single row; poly's other two gate misses (concave_bracket 105.41,
   cube_duct 80.64) go through the tet→dual/smoothing path — a different lane
   per runner, 3 rows total vs hex's 4.
3. **3 of 4 rows are fast-tier** (groove1 6.74 s, slot1 11.06 s, thin_gap
   29.41 s wall in the baseline) → cheap A/B iteration; mini_valve is full
   tier but hex finishes there in 41.29 s.
4. **Cross-runner control:** on the same fixtures poly (50.59 / 56.01 / 66.25),
   gmsh_direct (61.07 / 65.58 / 65.84) and both snappy runners pass, so the
   geometry is not hopeless — the loss is specific to the hex path.

Primary gap geometry: **groove1 × hex** (fast tier, cheapest iteration).
Secondary: slot1, thin_gap, mini_valve.

### Read-only probe run BEFORE the prediction (no meshing, no code change)

Fixture feature width (from the STL vertices) vs the `max_cell` the hex path
actually asks cfMesh for (`geometry.py:suggest_cell_sizes(detail="medium")`,
recomputed read-only) and the baseline result:

| geometry | thin feature (m) | feature source | max_cell (m) | cells across feature | hex max_non_ortho | verdict |
|---|---|---|---|---|---|---|
| groove1 | 0.008 (slot walls y ±0.004) | fixture STL | 0.0094 | **0.85** | 75.98 | FAIL |
| slot1 | 0.0015 (slot walls y ±0.00075) | fixture STL | 0.0047 | **0.32** | 77.28 | FAIL |
| thin_gap | 0.001 (gap walls z ±0.0005) | fixture STL | 0.0023 | **0.43** | 73.75 | FAIL |
| mini_valve | 0.002 | sampler `min_thickness` | 0.0075 | **0.27** | 72.58 | FAIL |
| multi_inlet | 0.012 | sampler `min_thickness` | 0.0068 | 1.76 | 49.96 | PASS |
| concave_bracket | 0.008 | sampler `min_thickness` | 0.0037 | 2.16 | 67.30 | PASS |
| cube_duct | 0.035 | sampler `min_thickness` | 0.0094 | 3.72 | 64.86 | PASS |
| venturi | 0.0534 | sampler `min_thickness` | 0.0141 | 3.79 | 53.33 | PASS |
| cylinder | 0.100 | sampler `min_thickness` | 0.0187 | 5.35 | 52.83 | PASS |

Separation on all 9 hex quality-relevant geometries (dirty_cad excluded — 0
cells for every runner): **every failure has ≤0.85 cells across the thinnest
feature; every pass has ≥1.76** (nothing measured in 0.85–1.76 — the band the
B-side will land in is exactly what the experiment tests).

## Hypothesis

- `core/geometry.py:suggest_cell_sizes` sizes the octree from *global*
  ray-cast thickness percentiles (`analyze_local_thickness`) clamped to
  `bbox/8`; the sampler is blind to open slots — it reports p10 = 0.020 m on
  slot1 (real slot 0.0015 m) and 0.021 m on groove1 (real groove 0.008 m).
- `core/meshdict_gen.py:build_meshdict_lines` then emits **only**
  `maxCellSize/minCellSize` + BL for this path: `boundaryCellSize` (L203),
  `localRefinement` (L311), `objectRefinements` (L296) and `patchCellSize`
  (L183) are implemented but emitted only when the caller passes them.
- The benchmark hex path (`benchmarks/run_benchmarks.py:_benchmark_one`, L402)
  and the production fallback (`core/openfoam_runner.py:1002`,
  `commercial/mesh_engine.py:597`) pass none of them — so nothing ever tells
  cfMesh "this feature is thinner than max_cell, refine here".
- Mechanism: where the feature is thinner than `max_cell`, one octree cell
  spans both opposing feature walls; projecting two walls onto one cell
  produces the >70° internal faces checkMesh reports (75.98/77.28/73.75/72.58).
  `min_cell` cannot save it — it is only a lower bound, and nothing requests
  refinement (thin_gap already gets `min_cell` = 0.0001 and still fails).
- Contrast: snappy refines **every** surface cell to
  `level (level level)` = `min_cell` (`benchmarks/snappy_baseline.py:150`) and
  passes all four rows at 39.40–42.71; poly/gmsh carry their own size field.

## Prediction (write BEFORE the experiment)

With the follow-up fix (feature-keyed refinement directive in the hex meshDict,
behind the flag named below), on the gap cluster:

- **groove1**: hex `max_non_ortho` **75.98 → 50–68** (i.e. `< 70`, PASS);
  cells 9768 → 15 000–30 000 (≤3×); wall 6.74 s → ≤15 s.
- **slot1**: `77.28 → 50–68` (PASS); cells 34 152 → 45 000–90 000 (≤3×);
  wall 11.06 s → ≤25 s.
- **thin_gap**: `73.75 → 50–68` (PASS); cells 666 976 → ≤1 000 000 (≤1.5×);
  wall 29.41 s → ≤60 s.
- **mini_valve** (full tier): `72.58 → 55–69` (PASS, weakest confidence —
  most degrees of freedom); cells 611 958 → ≤2×; wall 41.29 s → ≤90 s.
- **multi_inlet** (the directive also fires there, 1.76 cells across): stays
  PASS — `49.96 → 45–65`.
- **No change expected on healthy geometries** (directive does not fire,
  ≥2.16 cells across): cylinder `52.83`, cube_duct `64.86`, venturi `53.33` —
  **identical metrics** (pipeline is deterministic: groove1 reproduced
  9768 cells / 75.98° in three independent runs — 20260922T172929,
  20261005T094047, 20261005T113621).
- **Cost:** ≤3× cells and ≤2× wall time on the gap rows, ~0 on the healthy
  rows; exit code of the fast gate flips from 1 to 0 for `--runner hex` on
  groove1/slot1/thin_gap if the prediction holds.
- Success bar for the cluster: hex pass rate 5/10 → 9/10 (dirty_cad stays a
  shared 0/5 failure).

## Measurement plan

- **A-side (current code)** — measured in Step 2, record refs below:
  `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" benchmarks/run_comparison.py --tier fast --geometry groove1 --geometry slot1 --geometry thin_gap --runner hex`
  plus the full-tier-only row:
  `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" benchmarks/run_comparison.py --tier full --geometry mini_valve --runner hex`
- **B-side (with fix)** — same commands, fix behind flag
  `thin_feature_refinement` (opt-in param on `write_meshdict`, surfaced as
  `--hex-thin-refine` in the harness; **default off — no default changed**,
  additive only).
- **Kill criterion:** if B does not bring the primary metric (groove1 hex
  `max_non_ortho`) below 70 — or it improves by <5° from 75.98 — **or** any
  fast-tier geometry regresses (cube_duct 64.86 → >70, or any healthy primary
  metric worse by >2°, or a currently-passing row starts failing), the lane is
  killed ("measured no-op, not promoted" — repo precedent,
  `notes/smoothed_dual_blend_reasoning.md`).

### A-side

Gate applied by the harness (`benchmarks/gates.py` +
`core/quality_thresholds.THRESHOLDS`): `max_non_ortho ≤ 70.0`,
`max_skewness ≤ 4.0`, `max_aspect_ratio ≤ 1000.0`, `neg_cells == 0`,
`cell_count > 0`, no timeout/fatal.

Run 1 (fast tier, 3 geometries, exit 1 = our runner misses a threshold —
expected):

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" benchmarks/run_comparison.py --tier fast --geometry groove1 --geometry slot1 --geometry thin_gap --runner hex
# -> benchmarks/results/comparison_20261005T123603.json (3 records)
```

Run 2 (mini_valve is full-tier only, exit 1 expected):

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" benchmarks/run_comparison.py --tier full --geometry mini_valve --runner hex
# -> benchmarks/results/comparison_20261005T123710.json (1 record)
```

Records (A-side = current code, no fix):

| geometry | record ref | cells | max_non_ortho | avg_non_ortho | max_skew | max_aspect | neg | wall s | thresholds_pass | failed |
|---|---|---|---|---|---|---|---|---|---|---|
| groove1 | `comparison_20261005T123603.json` | 9768 | **75.98** | 14.64 | 2.09 | 8.03 | 0 | 7.00 | false | `["non_ortho_max"]` |
| slot1 | `comparison_20261005T123603.json` | 34142 | **77.18** | 11.39 | 2.11 | 10.03 | 0 | 9.74 | false | `["non_ortho_max"]` |
| thin_gap | `comparison_20261005T123603.json` | 666976 | **73.76** | 6.25 | 2.16 | 9.15 | 0 | 28.30 | false | `["non_ortho_max"]` |
| mini_valve | `comparison_20261005T123710.json` | 611958 | **72.58** | 13.83 | 2.41 | 10.92 | 0 | 38.13 | false | `["non_ortho_max"]` |

A-side matches the committed baseline
(`comparison_20261005T113621.json`: 75.98 / 77.28 / 73.75 / 72.58) to
≤0.10° on groove1/mini_valve (bit-identical: 9768 and 611958 cells) and
≤0.10° on slot1/thin_gap (slot1 cells 34142 vs 34152 — small run-to-run
variation on that row, so treat ±0.5° as noise there). All four rows fail
**only** `non_ortho_max`; skew/aspect/neg_cells all clear their gates.

## Status

- [x] Prediction written (before any experiment, before any mesher change)
- [x] A-side measured
- [ ] Fix implemented (follow-up plan — needs human approval)
- [ ] B-side measured
- [ ] Verdict
