# Lane C — smoothed normals blended by dual_convexity (prediction BEFORE testing)

## What changes (small, well-scoped)

`run()` currently rejects `normal_method="smoothed"` together with
`concavity_criterion="dual_convexity"`. That rejection is stale: the
smoothing block blends by `(1 - angle_fade[w])`, and under the dual
criterion `angle_fade[w]` already CARRIES `dual_fade[w]` (precompute fills
it and skips the angular loop). So unlocking the combo needs no new
machinery — just lifting the guard for `"smoothed"` (keeping
`"most_visible"` blocked: it replaces normals inside the skipped angular
loop, so it genuinely cannot work under dual). Default unchanged;
opt-in only.

## Prediction

On the valve, `angle_fade` is exactly 1.0 on all 226,538 wall vertices
(smoothed+angle was a measured no-op: 7048 vs 7054 excluded, -0.08%),
while `dual_fade` fires (fraction of incident wall faces owned by
non-convex dual cells, min 0.05) exactly at the concave-feature vertices.
Blending the Laplacian-smoothed field in precisely there should give a
REAL, non-zero coverage effect vs dual+area_weighted: smoothed normals at
a concave vertex point toward the neighbours' consensus instead of the
locally unstable average, so fewer freshly-extruded prisms should invert
at full height. Predicted direction: FEWER excluded faces than the
dual+area_weighted baseline (magnitude unknown — could be modest, the
dual fade already thins layers at those vertices, so smoothing acts on
top of an already-terminated stack; the effect lives in the transition
band, not the defect core).

Quality prediction: most_visible-like MIXED checkMesh pattern, not
height_retry-like neutrality — smoothing changes extrusion DIRECTION at
rescued vertices (same risk class that cost most_visible +503 severeNO),
whereas retry only shortens heights. If severeNO jumps by hundreds vs the
dual baseline with no coverage gain, do NOT promote.

Falsification: |Δ excluded| < 0.5% vs dual baseline = still a no-op (the
dual fade may already terminate everything smoothing could rescue) —
document and do NOT promote. A coverage REGRESSION vs dual baseline means
smoothing destabilizes the transition band — document and keep blocked
(behavioural revert, not just docs).

## Protocol

Fresh pristine `valve_baseline` copies; A = `concavity_criterion=
"dual_convexity"` + `normal_method="area_weighted"`, B = same +
`normal_method="smoothed"`; both `local_termination="decoupled_vertex"`,
`n_layers=2`, `first_height=1e-5`, `growth_rate=1.2`, `apply_to_all=True`.
Real OF2512 checkMesh on both, same metric table as Lane B.

## Results (measured 2026-09-15, real OF2512 checkMesh via WSL)

Both success=True at scale 1.0 (A 276s, B 333s), decoupled_vertex rounds.

| metric (build) | A dual+area_weighted | B dual+smoothed | Δ |
|---|---|---|---|
| local_excluded_faces | 6470 | 6455 | **-15 (-0.23%)** |
| n_cells_after (engine) | 589681 | 589709 | +28 |
| prisms | 437596 | 437624 | +28 |

| metric (checkMesh) | A | B | Δ |
|---|---|---|---|
| severely non-ortho | 9668 | 9672 | +4 |
| non-ortho avg / max | 18.5253 / 95.88 | 18.5263 / 95.87 | ~0 |
| face pyramid errors | 852 | 852 | 0 |
| max skewness | 17.197 | 17.208 | +0.011 |
| highly skew faces | 101 | 101 | 0 |
| face-tet errors | 2379 | 2380 | +1 |
| high aspect ratio | 1000 | 1000 | 0 |
| failed checks | 9 | 9 | 0 |

Verdict: **FALSIFIED — still a no-op, do NOT promote.** |Δ excluded| =
0.23% < 0.5% bar; checkMesh is identical to the face. Mechanism re-read:
the dual fade already terminates the stack AT the concave vertices
(count drops to 0..1 layers there), so there is almost no transition
band left where a better normal could rescue a prism — smoothing answers
a question the dual criterion has already settled. (Side finding: the
dual+area_weighted baseline itself, 6470, already beats angle+area_weighted
7054 by -8.3%: the dual criterion's targeted termination is the real
coverage lever here; smoothing direction is second-order on top of it.)

Decision: the unlock STAYS in code (tested opt-in, no default change, no
regression: -15 not +), but documented as measured no-op on the valve —
no promotion, no further work on this combo. If a future geometry shows
wide transition bands under dual fade, re-measure there.
