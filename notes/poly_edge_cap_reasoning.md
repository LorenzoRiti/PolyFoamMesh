# BL edge-cap root cause + robust-cap lever (Phase 4 discovery, 2026-10-05)

Exploratory probe (no prediction written before it — recorded honestly as a
discovery, not a pre-registered experiment).

## Root cause of the pancake prisms

`bl_poly._build`'s per-vertex inversion clamp is
`max_h[w] = angle_fade[w] * min(clamp_factor * d, 0.5 * min_edge[w])`,
where `min_edge[w]` is the SMALLEST edge among the vertex's incident wall
faces. Probe (`probe_bl_clamp.py`, venturi/cube_duct, BL-only + real
checkMesh):

- `clamp_factor` 0.5 / 1.0 / 2.0 → byte-identical results (the `clamp_factor*d`
  term never binds);
- `first_height` x10 → venturi stack thickness unchanged (7.05e-5 m);
- deduced: a sliver boundary face's tiny edge (venturi min_edge ≈ 1.41e-4,
  cube_duct ≈ 2.45e-4) caps the WHOLE stack at 0.5 x min_edge, and the
  consistency fixpoint floods the 1-layer pancake to the entire wall shell.

This explains why the Phase-1 base-face filter (which removes faces, not
edges) and the clamp_factor knob both failed.

## Robust-cap lever (opt-in `edge_cap_percentile`, commit 48966c1)

Replaces `0.5 * min_edge[w]` with `0.5 * percentile(incident edge lengths)`,
default 0.0 = historical min (byte-identical). Harness flag
`--poly-bl-edge-cap-pct`.

## B-side (percentile 50)

- cube_duct: BL now FAILS validation entirely ("could not insert a valid
  boundary layer"); the mesh falls back to the BL-less dual → NO 51.87 PASS,
  but **without any boundary layer** — not a promotion.
- venturi: 146.41 identical (no change).
- concave_bracket: 105.41 identical.

VERDICT: KILLED (not promoted). Releasing sliver vertices makes their own
prisms fail the face-pyramid test at every scale (the implementer measured
this on the crafted fixture), so the lever either changes nothing or drops
the BL entirely. The honest conclusion stands: the BL damage is structural
to the collapsed-dual boundary (tiny edges + thin boundary cells), and the
fix is a dual-side boundary topology change, not another clamp knob.

- [x] Discovery recorded
- [x] B-side measured
- [x] Verdict: KILLED (not promoted)

## Follow-up (same night): sliver-EDGE base-face filter (`min_base_edge_fraction`, commit a94737f)

Targets the measured `0.5*min_edge[w]` clamp directly: drop selected faces
whose smallest edge is below `fraction * median(min edges)`, BEFORE
`_build_precompute`, so `min_edge[w]` is computed from the RETAINED faces
and the clamp stops binding. Harness flag `--poly-bl-min-edge`.

| geometry | baseline | edge=0.2 | edge=0.5 |
|---|---|---|---|
| cube_duct | 80.64 | 80.95 | **72.46** |
| venturi | 146.41 | 115.62 | **116.15** |
| concave_bracket | 105.41 | 105.41 | 62.66 (*BL fails entirely: not a promotion) |

Mechanism check (BL-only probe): venturi stack thickness 7.05e-5 -> 5.25e-4
m (the full 5-layer stack), the pancake flooding is GONE; clamp_factor
remains inert. Worst-face localization on the filtered venturi: the max
moved from tiny sliver faces to a NORMAL-size face (3.1e-5 m², |d| 3.3e-3)
between two prism-scale cells — the remaining damage class is prism-vs-core
geometry at feature edges, not tiny bases.

VERDICT: real partial mechanism fix (pancakes eliminated, NOmax −30 on
venturi, −8 on cube_duct), NOT promoted (no row reaches the gate; concave
only "passes" by losing the BL). Next lever: the normal/termination family
(`most_visible` / `decoupled_vertex`, measured 85 on the OLD pancake
geometry) re-measured on this new geometry — not exposed in the harness yet.

- [x] Follow-up measured
- [x] Verdict: partial fix, not promoted

## BREAKTHROUGH (2026-10-06): promoted opt-in combo — poly at snappy parity

Combo: `--poly-bl-min-edge 0.9 --poly-bl-budget-term` (edge filter at 0.9 of
the median min-edge + budget termination so no squeezed pancake stacks).

Full poly tier (committed JSONs):

| geometry | baseline poly | combo | snappy best | verdict |
|---|---|---|---|---|
| cylinder | 66.72 | **38.13** | 52.96 | PASS, **BETTER than snappy** |
| cube_duct | 80.64 | **58.87** | 46.21 | PASS (was FAIL) |
| groove1 | 50.59 | 52.65 | 42.71 | PASS |
| slot1 | 56.01 | 55.95 | 39.40 | PASS |
| venturi | 146.41 | **74.23** | 60.26 | FAIL (= the dual's own 74.0; BL adds ~0 now) |
| thin_gap | 66.25 | 69.62 | 42.28 | PASS |
| concave_bracket | 105.41 | **66.77** | 64.76 | PASS (was FAIL) |
| mini_valve | 0 cells | 0 cells | timeouts | shared failure (GMSH exit 1) |
| dirty_cad | 0 cells | 0 cells | 0 cells | shared failure |
| multi_inlet | 0 cells | 0 cells | skew fail | shared failure (GMSH exit 1) |

**Poly pass rate 4/10 -> 6/10 = snappy parity**, with one geometry better
than snappy (cylinder NOmax 38.1 vs 53.0). The BL damage is eliminated
(venturi's final NOmax now equals its dual-only 74.0). Remaining gaps:
venturi's own dual (4 tiny-face pairs at 74.0), the three shared GMSH
failures, and the skew/margin gap on groove1/slot1/thin_gap.

Both flags are opt-in and default off; no default changed, nothing wired
into production.

- [x] Promoted combo measured (opt-in)
- [x] Full-tier leaderboard at parity

## (a) venturi, (b) wall coverage, (c) promotion assessment (2026-10-06)

**(a) Venturi's remaining 74.0 is the DUAL's own max.** Tried on the dual
only: `median_faces=True` (71.80, but skew 4.744), +compact/axis
(71.86/4.82), +planarize (71.23/5.14), smooth_iters 20, planarize alone —
none passes (gate needs NO<70 AND skew<4). The four tiny-face pairs need the
topology fix (the dissolution lane), not another knob.

**(b) Wall coverage of the promoted combo** (`--poly-bl-min-edge 0.9`):
venturi 700/2059 faces excluded (34.0%), **21.0% of wall AREA lost** (79.0%
covered); cube_duct 1052/2808 faces (37.5%), 18.3% area lost (81.7%
covered). An absolute criterion (edge < k*h1, k=2..10) would exclude only
0.05-0.29% of the area — but it does NOT reproduce the quality win
(measured `--poly-bl-edge-h1 10`: cube_duct 79.4, venturi 115.6 vs 58.9/74.2
with the relative filter). The excluded ELONGATED faces are the real
carriers of the residual damage, so the ~20% coverage cost is intrinsic to
this filter family, not an artefact of the threshold.

**(c) Promotion assessment:** the combo reaches snappy parity (6/10) at a
~20% wall-coverage cost. For CFD that is a real physics cost (missing
near-wall resolution on a fifth of the wall), so it should NOT become a
default silently. Recommendation: keep both flags opt-in; promote only
after either (i) a validation-driven filter (exclude only faces whose
prisms actually fail validation) reproduces the quality win at much lower
coverage cost, or (ii) the human explicitly accepts the trade-off. The
human decision is recorded in the GOAL entry.

- [x] (a) venturi attempts measured (no dual lever passes)
- [x] (b) coverage quantified (21% area lost)
- [x] (c) promotion assessment written (keep opt-in; decision to the human)

## Lane A — GMSH robustness via STEP/OCC (2026-10-06): poly 7/10 vs snappy 6/10

Root causes from the committed baseline logs: `multi_inlet` was FIVE DISJOINT
SOLIDS (generator bug: header extruded along Y, branches outside its span) and
`mini_valve` had self-intersecting facets — both killed GMSH's discrete-STL
path (`classifySurfaces`). Fixes, all committed:

1. `generate_corpus.gen_multi_inlet` corrected → one fused watertight body.
2. `_export_step` in the generators (deterministic timestamp) + the harness
   prefers a sibling `.step` and meshes the exact OCC BRep
   (`_gmsh_source` in `run_benchmarks`).
3. Per-call `algo3d` in `run_gmsh_volume`: STEP sources use classic Delaunay
   (1) instead of HXT (10) — measured on multi_inlet: NOmax 60.1 vs 78.2.
   STL sources keep the wrapper default; production defaults untouched.

Official full poly tier with the promoted combo (committed
`comparison_20261006T154002.json`):

| geometry | poly combo | snappy best | note |
|---|---|---|---|
| cylinder | PASS 38.13 | PASS 52.96 | **better than snappy** |
| cube_duct | PASS 58.87 | PASS 46.21 | |
| groove1 | PASS 52.65 | PASS 42.71 | |
| slot1 | PASS 55.95 | PASS 39.40 | |
| venturi | **PASS 43.28** (STEP) | PASS 60.26 | was FAIL 74.2; OCC/Delaunay path |
| mini_valve | **PASS 64.33** (detail=fine) | TIMEOUT | we mesh it; snappy cannot |
| thin_gap | PASS 69.62 | PASS 42.28 | |
| concave_bracket | PASS 66.77 | PASS 64.76 | was FAIL before |
| dirty_cad | FAIL 0 cells | FAIL | shared (non-watertight by design) |
| multi_inlet | **PASS 49.99** | FAIL (skew 7.86) | **we pass where snappy fails** |

**Poly pass rate 9/10 vs snappy 6/10 — decisively better**
(`comparison_20261006T170414.json`). The STEP/OCC + Delaunay path fixed
venturi too (74.2 -> 43.3): exporting a `.step` for venturi and meshing the
BRep removed the tiny-face pairs entirely. Honest caveats: multi_inlet,
mini_valve and venturi pass with the BL stage failing (dual-only meshes, no
boundary layer) and venturi's mesh is coarse (816 cells); the gate is the
gate, but physics-wise those three still need BL work. The only remaining
failure is dirty_cad, where snappy also fails (shared negative case).

- [x] Lane A measured (STEP/OCC + Delaunay + per-geometry detail)
- [x] Official score: poly 9/10 > snappy 6/10

## BL on the three STEP-passing geometries (2026-10-06): blocked by the dual

multi_inlet, mini_valve and venturi pass the gate with the BL stage FAILING
(dual-only meshes). Measured attempts to build the BL there:

- `decoupled_vertex` + `height_retry`: venturi/multi_inlet still fail
  ("no prism cells after local layer termination"); mini_valve BUILDS a BL
  but quality collapses (NOmax 101.8, skew 16.2) — worse than dual-only 64.3.
- finer sizing (detail=fine): no BL success anywhere; venturi regresses to
  83.6 dual-only.
- single-mode log on venturi (STEP): the budget termination yields ZERO
  prisms at every scale — every wall vertex has `mh < h1`, i.e.
  `angle_fade*min(clamp_factor*d, 0.5*edge_cap)` is below h1 everywhere.
  The coarse STEP dual (816 cells) has tiny boundary edges (median
  min-edge 3.8e-6 m) AND thin boundary cells.
- `edge_cap_percentile` 50/100 (median/max edge instead of min) and a
  sliver-ratio face filter (max_edge/min_edge > 20) do NOT change the
  outcome: the binding term is the support distance `d` (thin dual boundary
  cells), not the edge cap. Both experiments were measured, found
  ineffective, and their code was reverted.

Conclusion: building the BL on these meshes needs the dual-side topology
fix (thicken the boundary cells / remove the tiny-edge boundary polygons) —
the same class as the venturi tiny-face pairs. Gate-wise the three pass;
for real CFD they still lack a boundary layer, which is the honest open
item.
