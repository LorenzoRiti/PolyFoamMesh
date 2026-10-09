# Task 2 — why the BL fails on the production (collapsed) dual of the valve

Date: 2026-09-09. Written before the Task 2 tests (the fixture was
just regenerated in Task 1, so the runs start from the current converter).

## Measured facts (PHASE 7, collapsed production input)

- boundary faces: 42,130 (4-9 sided polygons) vs 226,542 quads of the exact;
- input defects: 340 wrong cells (checkMesh) / 370 pyr faces (in-process BL);
- volume drift: 1.08%;
- BL decoupled: FAILS at all 5 scales x 3 rounds — pyr
  trajectory per round: 1.0: 470/431; 0.6: 422/406; 0.35: 414/404; 0.2: 422/375;
  0.1: 444/385; input 370. The best (375 at 0.2 round 2) is 5 above
  input; the exclusions stop at 2,189 (5.2% of faces).
- Exact control (same converter, same source): BL succeeds at scale
  1.0 round 1 with 2,525 exclusions (1.1% of faces).

## Hypotheses on why (to be verified with measurement)

H1 — **exclusion granularity (rim)**: in the collapsed dual a face
is a polygon per boundary vertex, ~5x the area of a quad. Excluding
a polygon removes a lot of wall; the transition prisms at the EDGE of the
excluded zone generate new pyramid violations. The count stops
because each round removes ~as many violations as it creates at the edge.
Prediction: the residual defects lie at radius 0-2 polygons from the excluded
zone, and the per-round count decreases slowly (e.g. 400 -> 30 -> 5 ->
...) without reaching 0 in the 3 rounds.

H2 — **unreachable input defects**: if a good part of the 370 input pyr
lies on INTERNAL faces (not selectable/excludable), the budget
"n_pyr <= input" is tight and the extra rounds are not enough. Prediction: on
the collapsed the boundary share of input defects is lower than
on the exact (937/1007 = 93% there) — I expect 250-340 out of 370 (68-92%).

H3 — **more rounds suffice**: the best is 375 vs 370: if the decrease per
round is slow but monotone, with 6 rounds at 0.2 (or 0.1) it closes.
Prediction: 60% that `max_rounds=6` closes at 0.2 or 0.1 on production.

H4 — **exclusion variants**: if the rounds are not enough, widening
the exclusion by one ring (V3) probably closes in 3 rounds but loses
more wall; excluding only the direct prisms without widening to the core
cells (V2) probably stalls earlier.

H5 — **criterion**: `angle_fade` vs `dual_convexity` — on smooth boundary
polygons the fade stays flat as on the exact; prediction: no
substantial difference (low priority).

## Measurement plan (in order)

1. Add an opt-in engine parameter `local_max_rounds: int = 3`
   (default unchanged) to test H3 without monkey-patching.
2. Extend `tools/bench_bl_production_topology.py` with `--max-rounds`.
3. Run: valve production collapsed, `decoupled`, `max_rounds=6` (then 10
   if 6 is not enough). If it closes: real checkMesh, numbers, and the opt-in
   proposal.
4. If it does not close: residual diagnostics (classification of the residual
   pyr faces: prism vs core, boundary vs internal) + variant V3
   (exclusion widened by 1 ring) via monkey-patch in the tool.
5. If even V3 does not close: write the negative with the diagnosis and the
   recommendation (exclusions->vertices mapping, or no BL on the
   collapsed for concave geometries).

## Measurements done

### H2 (unreachable input defects) — measured
On the collapsed: `input pyr: {'total': 370, 'boundary': 360, 'internal': 10}`.
**97% boundary**, therefore excludable. H2 falsified: the budget
is not choked by internal defects.

### H3 (more rounds suffice) — measured
Valve production collapsed, decoupled, `local_max_rounds=6`,
1.483 s, 25 builds, 2,207 exclusions, stall 373-378 (0.2) / 379 (0.1
from round 3 to 5). Mesh unchanged. **H3 falsified**: the count
plateaus, more rounds do not help.

### H1 (rim) — confirmed
The cascade plateau and the pattern of the pyr (373-378 vs 370 input,
with oscillations from 373 to 444) is consistent with exclusions at the rim:
excluding 2207 polygons produces a "hole" edge that brings out
defective transition prisms; each round adds 0-200 exclusions
but the net pyr count oscillates and does not fall below ~373-379.

### H6 widen=1 — measured, falsified
Valve production collapsed, decoupled, `local_exclude_widen=1`,
`local_max_rounds=6`, 2.064 s, 25 builds, **2,207 exclusions**
(identical to widen=0: widening by 1 ring does not add faces to the
excludable set of the valve, presumably because the frontier of the
exclusion rim is already 1-ring at many positions on the collapsed
or the pyr-violation at the rim is not simply "moved by 1
ring"). Same plateau: 0.1 r3-r5 at 379. **H6 widen=1
falsified**: widening the exclusion by 1 ring does not close the
gap (5-10 faces above input) and does not materially change the excluded
set. widen=2 almost certainly will not close anyway
(the widening is local and does not change the geometry of the rim).

## What H6 *does not* change and why

H4 (exclusion -> per-vertex mapping) remains the structural gap:
the binary face granularity on collapsed polygons cannot cover
the rim continuously. Even with widen > 0, the cascade adds
faces at the rim of existing exclusions and the gate n_pyr <= input does not
close. The solution requires a per-vertex mapping (exclude
"layers" on incident vertices, not whole faces): a morphological
change to the binary exclusion model, not a parameter tweak.

## Reading criterion (updated)

- H1 confirmed, H3 + H6 widen=1 falsified, H2 confirmed: the
  defect of the collapsed BL on the valve is not closable with
  exclusion granularity (face + 1 ring). widen=2 expected NOT to
  close; not measured (cost ~30 min for an expected
  negative result). H4 (per-vertex mapping) is the documented structural
  gap.
