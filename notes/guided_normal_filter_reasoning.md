# Lane C2 — guided normal filtering (prediction BEFORE testing)

## Literature grounding

- Zhang, Deng, Zhang, Bouaziz, Liu, "Guided mesh normal filtering",
  Pacific Graphics 2015: joint bilateral filter on FACE normals where the
  range kernel keys off GUIDANCE normals (from the locally most-consistent
  patch), not the noisy normals themselves — robust at sharp features
  where plain bilateral/Laplacian wash across the ridge.
- Zheng, Fu, Au, Tai, "Bilateral normal filtering for mesh denoising",
  TVCG 2011: two-stage (filter normals, then update), intensity difference
  = projection of the normal-difference vector; iterate to taste.
- Solomon et al.: filtering the normal FIELD (rather than positions)
  avoids shrinkage artifacts — matches our use (normals steer extrusion
  only; positions come from the pipeline).

## Adaptation to our pipeline (normals steer extrusion, nothing else moves)

New `normal_method="guided"` (opt-in, default untouched):
1. Per wall-face unit normal `n_i` + centroid `c_i` (existing `_newell`).
2. Guidance `g_i`: most-consistent local patch average (candidate patches
   = {i}, {i,j} pairs, full 1-ring; pick min normal variance).
3. Joint bilateral: `n'_i = normalize(Σ_j A_j·exp(-|c_i-c_j|²/2σ_s²)·
   exp(-|g_i-g_j|²/2σ_r²)·n_j)` over the face 1-ring, σ_s = 1.5× local
   mean edge, σ_r = 0.35 (paper mid-range for sharp features).
4. Per-vertex extrusion normal = area-weighted average of filtered
   incident face normals. Single pass (v1; iterate only if promising).
   Everything downstream (fade/clamp/build/validate) unchanged.

## Prediction (mechanism, distinct from most_visible)

most_visible picks the per-vertex min-max optimum INDEPENDENTLY per
vertex — neighbouring vertices can disagree strongly, twisting prism
SIDE faces (its residual: 584 prism-side pyramid failures, only halved
from 1223). Guided filtering smooths the normal FIELD while the guidance
term preserves ridges, so neighbouring extrusion directions AGREE more:
fewer side-twist failures, same or better ridge handling. Predicted:
excluded_faces < 6612 (most_visible) driven by fewer SIDE failures
(round-1 prism-side pyramid count < 584), with checkMesh severeNO at or
below most_visible's 10117 (smoother field => less non-orthogonality —
the opposite risk profile to most_visible's +503).

Falsification: excluded >= 6612 (field agreement doesn't rescue what
per-vertex optimality already gets), or severeNO >> 10117 (smoothing
washes a ridge the guidance should have kept) — document, do NOT
promote. Comparison is guided ALONE (no height_retry) vs most_visible,
same protocol: pristine valve_baseline, decoupled_vertex, n_layers=2,
first_height=1e-5, growth 1.2, apply_to_all, real OF2512 checkMesh.

## Results (measured 2026-09-15)

Valve (guided ALONE, same protocol as most_visible): success=True, 294s,
scale=1.0, **excluded=7054 = area_weighted to the face** (n_cells_after
589113 identical), checkMesh identical to baseline (severeNO 9614, wrong
841, skew 16.245/86, face-tets 2375, failed 9).

slot1 sharp-wall domain check: aw 510 vs guided 510 excluded, identical.

Verdict: **principled no-op, do NOT promote.** Mechanism, confirmed twice:
(a) on smooth walls the filter provably collapses to area-weighted
averaging (neighbours agree → uniform weights); (b) at TRUE ridges the
vertex normal still averages ACROSS the discontinuity no matter how well
each side is filtered — guided solves noise (a problem our analytic dual
normals don't have), while most_visible solves direction choice (the
problem we do have). No σ retune can fix this: smaller σ_r approaches raw
normals whose vertex average is again area-weighted. Code + tests stay
(documented no-op, opt-in, no default change); the lever's real domain
(noisy input meshes) does not occur in this pipeline.
