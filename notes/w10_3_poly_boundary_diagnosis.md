# W10.3 - poly quality and dual boundary: diagnosis (2026-10-23)

Scope: attribute the R1/corpus poly defects to dual-only vs BL, test the one
remaining measured opt-in combo, and decide whether a safe release mechanism
exists or the topological wall must be accepted. Inputs:
`notes/m2_milestone_diagnosis.md`, `notes/w5_bl_diagnosis.md`,
`notes/w10_1_bl_fidelity_*`, `notes/poly_*_reasoning.md`,
`notes/zonal_aspect_relaxation_reasoning.md`, `poly_remediation.py`,
`tet_poly_dual.py`, `bl_poly.py`, the R1 meshes and `LEADERBOARD.md`.

## 1. Baseline and attribution

### R1 173334

| Mesh | Cells | Pyramids | NO max | Skew max | Real checkMesh |
|---|---:|---:|---:|---:|---|
| dual-only | 79,416 | **1** | 48.46 | 3.31 | one inverted pyramid class |
| dual + old standard BL | 253,681 | **39** | 128.04 | 170.89 | Failed 3: 39 pyr, 5 skew, 26 non-ortho |
| dual + W10.1 quality BL | 255,456 | **0** | 86.03 | 4.13 | **Failed 1**: 1 skew, 0 non-ortho errors |
| dual + edge-filter 0.9/budget combo | 233,561 | **0** | not promoted | not promoted | coverage **87.1%** (below release floor) |

Measured on the fresh dual in this tree: `_detect_defects` reports
`pyramid=1, non_ortho=0, skew=0`; the quality mesh reports
`pyramid=0` (the in-process non-ortho count 65 is the >65 deg bucket, not
the real checkMesh error count; real checkMesh has 0 non-ortho errors).
W10.1's quality policy excludes 187 rim faces (0.53%) and keeps 99.47%
coverage.  It removes the 38 BL-added pyramids; it does not create a new
dual operator.

Attribution: **dual-only = 1 pyramid; old BL adds 38; quality BL removes
them.**  The 5-skew/26-non-ortho old residual is BL-added; quality leaves
1 skew.  R1 dual-only already meets the ≤1 release target.

### Venturi (the wall)

Dual-only venturi is reported at NO 97.2 / skew 18.3 / 224 pyramids in the
existing regression corpus.  No merge, smoothing, compact-fan, triangulate,
planarize, feature-angle, sharp-edge, edge-cap percentile, min-base-edge,
budget-term-alone or zonal-worst-element run has closed it; the notes show
the wall is topological: one boundary cell per boundary vertex at acute
concave folds.  The resolution slider does not move it.  W14 is the rewrite
lane.

### Corpus

The last measured poly comparison is 9/10 with the opt-in edge-filter 0.9
+ budget-term combo; the release suite retains cylinder/groove1/slot1/
thin_gap and the 9/10 result.  No new corpus run is claimed in this pass.

## 2. The combo A/B (new measurement)

The opt-in combo was run on the same R1 dual with `minBaseEdgeFraction=0.9`
and budget termination, fTetWild production overrides otherwise:

- 154,145 prisms, 5 layers;
- **0 pyramids**, coverage **87.1%** (4,566 sliver-edge faces marked thin);
- h1 ratio 0.114x, same structural cap;
- real checkMesh was not needed to reject it: coverage is below the 98%
  release floor.  The W10.1 quality attempt instead gives 99.47% / 0 pyr /
  1 skew / Failed 1.

Verdict: **not promoted**; the 87.1% coverage is a regression against the
quality policy and the relaxed coverage floor.  The old edge-filter combo
stays opt-in for the cases where it was measured.

## 3. Rejected mechanisms (do not retry)

The following are already measured and killed; W10.3 must not spend another
run on them without a genuinely new operator:

- merge + relax / concave merge: fixes many cells but regressed groove1
  (NO 50.59 -> 65.78, skew 3.67 -> 5.5) and the in-process gate missed it;
- compact-fan, triangulate, planarize, smoothing (global or zonal worst-
  element): converged or negligible; no topology change;
- feature-angle/sharp-edge/edge-cap/min-base-edge/budget-term alone: measured
  knobs, not a solution to the topological wall;
- W8 batch/sequential boundary pyramid vertex nudges: 41 -> 38, rejected.

## 4. Chosen scope

No new geometry mechanism is safe within the release scope.  The only
defensible new deliverable is a **release gate** that:

1. measures dual-only and dual+BL independently (`poly_release.py`);
2. attributes BL-added pyramids/non-ortho/skew by difference;
3. requires zero pyramids, zero negative volumes, >=98% coverage;
4. accepts only an explicit documented residual (R1 quality: one skew /
   Failed 1) and writes it into `bl_report`/log;
5. refuses the 87.1% combo and any pyramid-bearing mesh.

This is candidate (d): acceptance of the wall with numbers.  The alternative
(a) SPLIT/topological rewrite belongs to W14; it is not smuggled in here as
a partial split.

## 5. Reuse/refactor/remove

Reuse `_detect_defects`, `_exact_cell_volumes`, W10.1 quality policy and
`bl_report`.  Add only `core/poly_release.py` and the worker report hook.
Do not add a second dual converter, merge, or repair chain.  No default
change: W10.1 quality remains default-on for fTetWild; the 0.9 combo stays
opt-in.

## 6. Load estimate and risks

Files: new `poly_release.py`, worker hook, tests, diagnosis/reasoning/docs.
~4-6 files, ~250-400 net lines.  Risks: the in-process detector is not a
real checkMesh replacement; the worker report remains explicit about the
residual and does not claim a pass when checkMesh reports Failed 1.
