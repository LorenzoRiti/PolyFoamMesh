# Gap analysis — baseline vs snappy (2026-10-05)

Source: benchmarks/results/LEADERBOARD.md + comparison_20261005T113621.json

## Executive summary

Across the 10-geometry baseline our runners pass thresholds on 50% (hex), 40% (poly) and 50% (gmsh_direct) of geometries, against 60% for both snappy_base and snappy_expert; snappy wins 5 of 10 geometry cells (snappy_expert 3, snappy_base 2), hex wins 3, and no runner passes dirty_cad or mini_valve. The gap table contains 31 rows, and the dominant cluster is `max_non_ortho` — 9 absolute-threshold misses plus 6 relative losses, i.e. 15 rows spanning 8 of the 10 geometries, which is also the highest-ranked spec severity that actually has rows (neg_cells is 0 in all 50 records). The single most important problem is non-ortho quality: hex fails the non-ortho gate on groove1, slot1, thin_gap and mini_valve, poly on concave_bracket, cube_duct and venturi, gmsh_direct on cube_duct and venturi, and on 6 passing cells both snappy runners still post a lower `max_non_ortho`. Secondary clusters are clearly smaller: 6 skew relative losses (3 of them poly), 7 `cells_produced` aborts (3 of them dirty_cad, where all five runners produce nothing), and 3 timeouts — all of them snappy's, never ours.

## Gap table (severity order)

Severity order (spec): neg cells > non-ortho > skew > aspect > time. Rows sorted by that order; ties broken by how many geometries share the gap (cluster = gap type + metric, counted in distinct geometries).

- `cells_produced` (no mesh produced) is **not** one of the spec's five severity categories. These 7 rows are listed first — a runner that emits 0 cells is a total failure — and this placement is flagged as **ambiguous per spec** (rows 1, 4, 6; the remaining four cross-reference them).
- neg-cells rows: **0** (`neg_cells` is 0 in all 50 records). aspect rows: **0** (no runner fails on `aspect_max` alone; aspect appears only inside Notes).
- † rows are rule-3 timeouts of a **snappy** runner (no our-runner timed out in this baseline); listed because rule 3 covers any runner, and marked in Notes.

| # | Geometry | Our runner | Gap type | Failing/worse metric | Us | Best snappy | Notes |
|---|----------|-----------|----------|----------------------|----|-------------|-------|
| 1 | dirty_cad | gmsh_direct | threshold miss | cells_produced (cell count) | 0 | 0 | Ambiguous severity (see above). All 5 runners produce 0 cells: non-watertight geometry, snappy FATAL `locationInMesh` — no competitive loss. |
| 2 | dirty_cad | hex | threshold miss | cells_produced (cell count) | 0 | 0 | Same shared failure as row 1 (0/5 runners pass). |
| 3 | dirty_cad | poly | threshold miss | cells_produced (cell count) | 0 | 0 | Same shared failure as row 1 (0/5 runners pass). |
| 4 | mini_valve | gmsh_direct | threshold miss | cells_produced (cell count) | 0 | 0 | Ambiguous severity. GMSH exit 1; both snappy also produced 0 cells (timed out 1204.07 / 1200.91 s) — no runner passed. |
| 5 | mini_valve | poly | threshold miss | cells_produced (cell count) | 0 | 0 | Same as row 4 (GMSH exit 1, snappy both timed out). |
| 6 | multi_inlet | gmsh_direct | threshold miss | cells_produced (cell count) | 0 | 41563–57136 | Ambiguous severity. Only competitive abort: snappy did mesh (snappy_base 41563, snappy_expert 57136 cells) though it then failed `skewness_max`; hex is the winner here (49.96 / 0.70, 2.79 s). |
| 7 | multi_inlet | poly | threshold miss | cells_produced (cell count) | 0 | 41563–57136 | Same as row 6 (0 cells while snappy meshed). |
| 8 | concave_bracket | poly | threshold miss | non_ortho_max | 105.41 | 64.76 | Also fails `skewness_max` (4.37) and aspect 566.63; snappy_expert passes (64.76 / 2.36), snappy_base also FAILs (71.75 / 5.59). |
| 9 | cube_duct | gmsh_direct | threshold miss | non_ortho_max | 71.67 | 46.21 | Only failing metric (skew 1.11, aspect 8.72 OK); both snappy pass, hex passes here (64.86). |
| 10 | cube_duct | poly | threshold miss | non_ortho_max | 80.64 | 46.21 | Also fails `skewness_max` (4.92) and aspect 554.2; both snappy pass. |
| 11 | groove1 | hex | threshold miss | non_ortho_max | 75.98 | 42.71 | Our only failing runner on groove1 (poly 50.59, gmsh 61.07 pass); both snappy pass. |
| 12 | mini_valve | hex | threshold miss | non_ortho_max | 72.58 | n/a (both timed out) | snappy produced no mesh (1204.07 / 1200.91 s timeouts); poly/gmsh 0 cells — no runner passed this geometry. |
| 13 | slot1 | hex | threshold miss | non_ortho_max | 77.28 | 39.40 | poly (56.01) and gmsh (65.58) pass here; both snappy pass. |
| 14 | thin_gap | hex | threshold miss | non_ortho_max | 73.75 | 42.28 | snappy_expert timed out (1201.28 s) so best valid snappy is snappy_base; poly (66.25) and gmsh (65.84) pass. |
| 15 | venturi | gmsh_direct | threshold miss | non_ortho_max | 73.91 | 60.26 | hex passes (53.33); aspect 11.65 OK; both snappy pass. |
| 16 | venturi | poly | threshold miss | non_ortho_max | 146.41 | 60.26 | Worst `max_non_ortho` in the baseline; aspect 108.74; hex passes (53.33). |
| 17 | cube_duct | hex | relative loss | max_non_ortho | 64.86 | 46.21 | We PASS; snappy_base 46.21, snappy_expert 64.27 (marginal 0.59 better). |
| 18 | cylinder | poly | relative loss | max_non_ortho | 66.72 | 52.96 | We PASS; snappy_base 52.96, snappy_expert 63.10; poly also loses skew (row 25). |
| 19 | groove1 | gmsh_direct | relative loss | max_non_ortho | 61.07 | 42.71 | We PASS; snappy_base 47.60, snappy_expert 42.71. |
| 20 | groove1 | poly | relative loss | max_non_ortho | 50.59 | 42.71 | We PASS; snappy_base 47.60; poly also loses skew (row 26). |
| 21 | slot1 | gmsh_direct | relative loss | max_non_ortho | 65.58 | 39.40 | We PASS; snappy_base 42.42, snappy_expert 39.40 (26.18 better). |
| 22 | slot1 | poly | relative loss | max_non_ortho | 56.01 | 39.40 | We PASS; snappy_base 42.42; poly also loses skew (row 27). |
| 23 | cylinder | gmsh_direct | relative loss | max_skewness | 0.71 | 0.45 | We PASS; snappy_base 0.45, snappy_expert 0.67. |
| 24 | cylinder | hex | relative loss | max_skewness | 0.70 | 0.45 | We PASS; snappy_base 0.45, snappy_expert 0.67 — smallest margin in the table (0.25). |
| 25 | cylinder | poly | relative loss | max_skewness | 2.77 | 0.45 | We PASS; poly also loses non-ortho (row 18). |
| 26 | groove1 | poly | relative loss | max_skewness | 3.67 | 0.65 | We PASS; snappy_base 1.83, snappy_expert 0.65. |
| 27 | slot1 | poly | relative loss | max_skewness | 3.59 | 0.50 | We PASS; snappy_base 3.05, snappy_expert 0.50. |
| 28 | venturi | hex | relative loss | max_skewness | 2.29 | 0.68 | We PASS; snappy_base 0.68, snappy_expert 0.73. |
| 29 | mini_valve | snappy_base † | timeout | timed_out (wall s) | 1204.07 | n/a (both timed out) | Rule 3, snappy runner (not ours): both snappy hit the ~1200 s limit; our hex also FAILs (non_ortho 72.58), poly/gmsh 0 cells — no runner passed. |
| 30 | mini_valve | snappy_expert † | timeout | timed_out (wall s) | 1200.91 | n/a (both timed out) | Same geometry/run pair as row 29 (both snappy timed out). |
| 31 | thin_gap | snappy_expert † | timeout | timed_out (wall s) | 1201.28 | 567.52 | Rule 3, snappy runner. snappy_base passed in 567.52 s; our poly (58.25 s) and gmsh (3.74 s) PASS — terrain C win for us; our hex FAILs non_ortho 73.75. |

Cluster sizes (used for tie-breaking inside each severity tier): `cells_produced` threshold miss = 7 rows / 3 geometries; `non_ortho_max` threshold miss = 9 rows / 7 geometries; `max_non_ortho` relative loss = 6 rows / 4 geometries; `max_skewness` relative loss = 6 rows / 4 geometries; `timed_out` = 3 rows / 2 geometries. Non-ortho total = 15 rows / 8 geometries.

## Terrain C observations

**Snappy failed, we passed**

- `multi_inlet` — the only exclusive pass in the baseline: both snappy runners FAIL `skewness_max` (7.86 / 7.72), while hex passes (49.96 non-ortho, 0.70 skew, 6.30 aspect, 2.79 s).
- `thin_gap` — snappy_expert timed out (1201.28 s) while our poly (58.25 s) and gmsh_direct (3.74 s) pass thresholds.
- `concave_bracket` — snappy_base FAILs (71.75 / 5.59) while our hex (67.30 / 2.17) and gmsh_direct (67.50 / 1.04) pass.

**We failed, snappy passed**

- `groove1` — hex FAILs non_ortho 75.98; both snappy pass (47.60 / 42.71).
- `slot1` — hex FAILs non_ortho 77.28; both snappy pass (42.42 / 39.40).
- `cube_duct` — poly FAILs (80.64 / 4.92, aspect 554.2) and gmsh_direct FAILs (71.67); both snappy pass (46.21 / 64.27).
- `venturi` — poly FAILs (146.41, aspect 108.74) and gmsh_direct FAILs (73.91); both snappy pass (63.04 / 60.26).
- `thin_gap` — hex FAILs non_ortho 73.75 while snappy_base passes (42.28); snappy_expert timed out.
- `concave_bracket` — poly FAILs (105.41 / 4.37) while snappy_expert passes (64.76 / 2.36); snappy_base also FAILs.

**Nobody passed (shared failures)**

- `dirty_cad` — 0/5: non-watertight geometry; ours abort with 0 cells, snappy FATAL `locationInMesh`. Not a competitive loss.
- `mini_valve` — 0/5: hex produces 611958 cells but FAILs non_ortho 72.58, poly/gmsh_direct exit 1 with 0 cells, both snappy time out (~1200 s). Our reliability is equal-or-better here (hex finished in 41.29 s vs snappy's 1200+ s), but nobody scores a pass.

## Chosen first lane

**Lane NON-ORTHO — `max_non_ortho` gate misses and relative losses across hex/poly/gmsh.**

Justification from the table: it is the largest cluster by a wide margin (15 of 31 rows, 48%; 8 of 10 geometries) and sits at the highest spec-listed severity that contains any rows (neg_cells tier is empty — 0 negative cells in all 50 records). It is also the only cluster that decides the pass-rate gate: all 9 of our quality-metric threshold misses are `non_ortho_max` — i.e. hex's four non-ortho gate failures (groove1, slot1, thin_gap, mini_valve) and poly's three (concave_bracket, cube_duct, venturi) and gmsh_direct's two (cube_duct, venturi). The two skew gate misses among our runners (poly on cube_duct and concave_bracket) always co-occur with a non-ortho miss, so they are carried on those rows and no row is lost to the skew tier; the skew tier of the table therefore holds only relative losses, and the time tier is snappy's own timeouts. The rival `cells_produced` cluster (7 rows) shrinks to a single competitive geometry once shared failures are excluded — dirty_cad (3 rows) and mini_valve (2 rows) also produced 0 cells for snappy, and multi_inlet/poly+gmsh (2 rows) is the only cell where we aborted and snappy meshed. Target for the future fix plan: close `max_non_ortho` on the thin/slot/groove/venturi-class geometries where hex fails the gate, and on the cells where both snappy runners already beat a passing run of ours (design to be specified in a later task — not proposed here).
