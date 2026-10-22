# W10.1 - BL fidelity: diagnosis (2026-10-22)

Scope: why the real BL does not deliver the requested first-layer height
(h1), why the R1 checkMesh defects exist, and which W5/W6 contract is still
missing. Inputs: `notes/m2_milestone_diagnosis.md`,
`notes/w5_bl_diagnosis.md`, `notes/w7_bl_gui_*`, `bl_poly.py`,
`openfoam_runner.py`, `bl_config.py`, `main_window.py`,
`.opencode/prompts/REGRESSION_CASES.md`, the R1 mesh and the real
`m2_e2e_r1` / `m2_e2e_cube` artefacts.

## 1. Measured state

### 1.1 R1 173334 (real case)

Reference: `C:\cfmesh_cases\m2_e2e_r1`, blockMesh `checkMesh` (OpenFOAM
2512), 2026-10-22. The mesh has 253,681 cells, 174,265 prisms, 100% BL
coverage, 5 layers on every wall face.

| Metric | Requested | Achieved | Ratio |
|---|---:|---:|---:|
| first layer h1 (median) | 1.26 mm | 0.1418 mm | **0.113x** |
| h1 p10 | 1.26 mm | 0.0874 mm | 0.069x |
| h1 max | 1.26 mm | 0.956 mm | 0.759x |
| layers | 5 | 5 (all 34,853 faces) | 1.0 |
| coverage | >=98% | 100% | pass |
| checkMesh | pass | **Failed 3**: 39 pyramids, 5 skew, 26 non-ortho | fail |

Per-patch h1 medians are 0.131-0.147 mm; no patch is close to 1.26 mm.

h1 histogram (layered wall faces, 34,602 values):

| h1 range (mm) | faces |
|---|---:|
| <0.05 | 4 |
| 0.05-0.10 | 6,713 |
| 0.10-0.20 | 19,695 |
| 0.20-0.50 | 8,103 |
| 0.50-1.00 | 87 |
| >=1.00 | **0** |

The reconstruction from `mesh.msh` (fresh dual, same 5-layer request)
reproduced 176,975 prisms, 100% coverage, 5 layers and the same checkMesh
class: 39 pyramids, 5 skew, 26 non-ortho, Failed 3. Its readback h1 median is
0.1423 mm, confirming the M2 number is not an old-mesh artefact.

### 1.2 cube_duct

| Metric | Requested | Achieved | Ratio |
|---|---:|---:|---:|
| h1 median | 0.5 mm | 0.0176 mm | **0.035x** |
| h1 p10 | 0.5 mm | 0.0117 mm | 0.023x |
| h1 max | 0.5 mm | 0.661 mm | 1.32x |
| coverage | >=98% | 100% | pass |
| checkMesh | pass | Failed 1 (non-ortho 80.48 deg) | fail |

22,055 of the 30,895 layered faces have h1 < 0.05 mm; only 40 have
0.2-0.5 mm. The distribution is bimodal by patch (some patches ~0.19 mm,
most ~0.016 mm), so a global median hides most of the failure.

### 1.3 Where h1 is lost (R1, measured on the fresh dual, before BL)

Using the engine's own `_build_precompute` on the dual-only mesh:

- 70,293 wall vertices, 35,395 selected boundary faces;
- `max_h = angle_fade * min(0.5*d, 0.5*edge_cap)`, clamp 0.5;
- **64,615 vertices (91.9%) are support-bound** (`0.5*d` is the smaller
  term); **5,665 (8.1%) are edge-bound**; 0 are fade-bound;
- **70,280 / 70,293 vertices have `max_h < h1`**; only 13 can host the
  requested first layer;
- `max_h / full_5_layer_stack` ranges 0.0104..0.1638 (p50 0.0593);
- the full 5-layer stack is 9.376 mm; the median local support is
  ~0.555 mm, so a requested 1.26 mm first layer alone is larger than the
  local first-cell depth.

Root cause: the standard path sets `zero_concave=True`, which forces every
selected face to the full `n_layers` and **compresses every layer** into the
per-vertex `max_h`. `terminate_by_budget` computes the support-limited layer
count in `nv0`, but the binary complete-coverage branch replaces it with
`n_layers`. The compression is therefore not a validation fallback: it is
the normal path on a fine dual. The fallback scales (1.0, 0.6, ... 0.1) do
not help because scale 1.0 already validates; they only make h1 smaller.
This is the structural cap the old "success" hid.

The only paths that can put 1.26 mm into the first cell are:
1. **consume/reflow**: move the interior dual vertices to create room.
   Current measurement: `consume=True` is rejected on R1 because the
   sliver detector flags all 35,395 wall faces (threshold
   `0.05 * target_depth`; target depth 9.376 mm) and disables the reflow.
2. **topology**: replace the wall-adjacent dual cells with taller cells
   before extrusion (W10.3 territory).
Neither is implemented in this pass. A smaller `clamp_factor` only worsens
h1; it cannot exceed the `0.5*min_edge/support` cap.

### 1.4 checkMesh 39 pyramids: measured class (premise correction)

The W10.1 brief said "R1 39 pyramids = terminator class". The measurement
does not support that for R1:

- the build reports **0 terminator faces** (`n_terminator_faces=0`); all
  35,395 selected faces have 5 layers;
- our exact local criterion finds 33 violated **internal** faces; 31 of the
  first 33 have an owner id below the prism start (79,416) and a neighbour
  id above it, i.e. the face between the **original core cell and the last
  prism**;
- the remaining count difference (33 local vs 39 checkMesh) is within the
  known centroid/measure difference, but none of the local violations is a
  boundary face.

So the R1 pyramid class is **compressed top face vs. a thin core cell**,
not the partial-patch terminator strip. The core cell's wall vertices are
moved inward by the stack; when the stack is deeper than the local first
cell, the moved wall face crosses the core centroid and checkMesh reports a
face pyramid. `pyr_tolerance=200` accepts up to 200 such faces; the
post-BL `clear_boundary_pyramids_case` only clears BOUNDARY pyramids, so it
cannot fix these internal ones. This is why "Failed 3" survived every
coverage-focused fallback.

A partial-patch terminator test (`tests/test_bl_poly.py:184`) still shows
the historical design: when the adjacent face has no BL, the prism side
strips are **boundary** faces and the unselected wall face moves to the
stack top. This is a different geometry class (a real BL/non-BL rim). An
internal transition there requires splitting wall vertices/cells; it is
not a one-line patch. The W10.1 terminator goal is therefore still open,
with the exact reason recorded here.

### 1.5 Quality-policy A/B (measured after the diagnosis)

The quality attempt (`decoupled_vertex`, `pyr_tolerance=0`, then
`clear_boundary_pyramids_case`) was run on the same fresh dual through the
new executor. It excludes 187 of 35,395 wall faces (0.53%), keeps 99.47%
coverage, and clears 8 boundary pyramids:

| Attempt | Prisms | Coverage | h1 median | Pyramids | Skew | Non-ortho errors | checkMesh |
|---|---:|---:|---:|---:|---:|---:|---|
| standard | 176,975 | 100% | 0.1423 mm | 39 | 5 | 26 | Failed 3 |
| quality | 176,040 | 99.47% | 0.1424 mm | **0** | **1** | **0** | **Failed 1** |

This is a real quality improvement and is why the policy is worth landing,
but it does not change the h1 cap (0.113x). It also confirms the 39
pyramids were the compressed top-face class: removing the 187 rim faces
removes them, while the h1 budget is unchanged.

## 2. Current chain and duplication

1. `PolyBoundaryLayerEngine.run` owns the scale fallback (1.0..0.1),
   `zero_concave` complete-coverage branch, local-termination branch and
   keep-best by first valid attempt.
2. `DualPolyWorker.prepare_bl_config` translates `BLConfig` and applies the
   fTetWild overrides: `consume=False`, `min_base_edge_fraction=0`,
   `drop_feature_dihedral=50`, `pyr_tolerance=200`,
   `cover_thin_first=False`. Those overrides are why the real R1 run
   accepts a compressed stack and 200 pyramids.
3. The benchmark wrapper has its own hardcoded retry on non-positive
   volume (`decoupled_vertex`, `local_height_retry=True`).
4. cfMesh `RetryRunner` has a different "retry without BL" fallback.
5. GUI/CLI get a boolean/coverage report but no cap reason, no y+ and no
   policy/attempt log.

W5's `BLAttemptPolicy` + executor remains unimplemented. This is visible:
the same R1 failure is "success" in the worker, a hardcoded retry in the
benchmark, and no BL in cfMesh.

## 3. Report W6

`boundary_layer_report` has coverage, n_layered, h1 median/p10 and
per-patch values. It does not have:

- requested h1 and ratio (the worker computes a ratio only in a log line);
- the cap source (support/edge/fade) and the affected face count;
- y+ (no u_tau; BLConfig has velocity/fluid/wall treatment and target y+);
- a machine-readable acceptance/gap reason.

The GUI log prints the h1 ratio, but not per-patch y+ or the cap reason, and
the result JSON has no policy/attempt/best record.

## 4. Proposed design

### 4.1 `bl_fidelity.py` (new, pure functions)

- `height_cap_stats(pre, first_height, n_layers)` -> counts by binding term,
  percentiles of `max_h`, and `h1_supported_vertices`.
- `effective_h1_stats(built)` -> per-face h1, median/p10/max, histogram,
  ratio vs requested; uses first-prism owner centroids and wall-face
  centres the same way `boundary_layer_report` does.
- `estimate_u_tau(velocity, reference_length, fluid, flow_type)` -> the
  documented flat-plate/pipe correlation (`BLEngine` formula), explicitly
  an estimate, not a solver value.
- `y_plus(h1, u_tau, nu)` and `y_plus_report` per patch.

### 4.2 `bl_attempt_policy.py` (new)

- `BLAttempt` (declarative flags) and `BLAttemptPolicy` (ordered attempts,
  acceptance target: coverage >=0.98, h1 ratio >=0.9, zero added pyramids,
  real-layer guard, quality floor);
- `BLChainExecutor` runs the same `run` primitive in deterministic order,
  records every attempt, keep-best by
  `(accepted, h1_ratio, coverage, -pyramids, -wall_time)`, restores the best
  polyMesh if a later attempt is worse, and returns `BLChainResult`
  (`best`, `attempts`, `gap_reason`).
- Default policy is a single attempt that reproduces the current values
  byte-for-byte (rollback/old behaviour); optional attempts are explicit:
  quality-first (`pyr_tolerance=0`), robust edge cap, consume/reflow.
- The worker, CLI and benchmark call the same executor; the policy never
  hides a rejected attempt.

### 4.3 Report and UI

- `PolyBoundaryLayerResult` gains requested/effective h1, ratio, cap
  source counts, histogram, `n_pyramids_after`, policy/attempt/best.
- `boundary_layer_report(case, requested_h1=..., u_tau=..., nu=...)` adds
  ratio and per-patch y+; if u_tau is absent it returns
  `y_plus: null` + `y_plus_method: "unavailable"`, never a fake value.
  With velocity/fluid/bounding length it can return an explicitly labelled
  correlation estimate.
- GUI log adds one line per patch with coverage, h1, ratio and y+ (or the
  unavailable reason). JSON stores the same.
- The adaptive y+ fallback (h1/2, h1/4) stays for its measured non-fTetWild
  cases, but the fTetWild path can no longer call a compressed stack a
  "success" without the ratio warning; the policy records the cap.

### 4.4 Terminator

No default change in this pass. The measured boundary-terminator design is
kept and explicitly separated from the R1 internal-top-face class. The
internal-transition design requires splitting the wall vertex/cell at the
BL/non-BL rim (otherwise the shared vertex cannot be both layer 0 and layer
n); it is recorded as the next topology task, with the partial-patch test
as the regression guard. This is preferable to a silent boundary-to-internal
flip that would leave cells open.

## 5. Reuse / refactor / remove

**Reuse**: `PolyBoundaryLayerEngine.run` as the attempt primitive,
`boundary_layer_report` readback, `BLConfig`, `CheckMeshReport`,
`BLParameters` y+ correlation, `pyr_tolerance` semantics.

**Refactor**: move h1/cap/y+ math into `bl_fidelity.py`; add one policy
executor; have `DualPolyWorker` and the CLI use it; add report fields and
GUI logging.

**Remove/deprecate**: the implicit "coverage is success" acceptance on the
fTetWild path; the duplicate benchmark retry once the executor is wired;
unnamed cap strings. Keep the current attempt as the compatibility default.

## 6. Load estimate and risks

Files: new `bl_fidelity.py`, `bl_attempt_policy.py`; modify `bl_poly.py`,
`openfoam_runner.py`, `main_window.py`, `benchmarks/run_benchmarks.py`,
`core/validation.py`/CLI, tests; docs. ~10-14 files, ~600-900 net lines.

Risks:

- h1 cannot be improved on R1 without consume/topology; the correct result
  is a declared cap, not a fake pass;
- the quality-first attempt may reject every current build (expected);
  keep-best must preserve the current mesh and report `gap_reason`;
- terminator internalisation is a vertex-splitting change; do not ship a
  half version that opens cells;
- y+ without u_tau must stay null, not a correlation passed off as
  measured.

## 7. What this phase can and cannot close

Can close: one measured h1 cap/report across GUI/CLI/worker; one policy
executor with keep-best and attempt logs; per-patch y+ target/estimate or an
explicit unavailable flag; no silent compression.

Cannot close honestly in this pass: R1 zero pyramids and 0.9x h1; both
require consume/reflow or a topology change. They are declared with the
measured numbers above and the exact path.
