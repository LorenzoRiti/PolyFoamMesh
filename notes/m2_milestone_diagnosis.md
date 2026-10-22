# M2 milestone — structural audit (2026-10-22)

Scope: MESE 2 (BL everywhere, per-patch y+, complete parameters, process
robustness) against the measured tree.  Sources: `.opencode/GOAL.md`,
`ROADMAP.md` §M2/Exit M2, `benchmarks/results/20261022T090226_ftetwild.json`,
`20261011T131707_poly.json`, `LEADERBOARD.md`, `notes/w1..w8`, the real R1
log and the current code.  No code was changed for this diagnosis.

## 1. Inventory per front

### 1.1 Tet (GMSH / fTetWild)

| Item | Before M2 | After W1–W8 | Residue / cause |
|---|---|---|---|
| fTetWild fine | 1424 s, 2.37M tets (eps 0.3 mm) | 3738 s fine, max_energy 7.55, no stall; W1 confirmed 0.3 mm | Time is above the M1 target (≤700 s); fidelity-vs-time decision documented, not a bug |
| fTetWild progress | none (4 h timeout only) | W8: `pass N`/energy detector, `progress`/`heartbeat`, 600 s liveness, 4 h ceiling | GUI raises/handles failures through the existing coarser-detail retry; no full Tesla re-run |
| GMSH stall | 300 s no-output + signature detector | W8: same detector behind one `PhaseWatchdog` | MeshAdapt signature proven; no new stall |
| Real tet smoke | — | W8 cylinder STL, 7.0 s, watchdog armed, success | Healthy but small; corpus not re-run |

### 1.2 Dual

| Item | Before M2 | Measured now | Residue / cause |
|---|---|---|---|
| Fine dual | 294 s baseline | W2 plan written, **no verdict**; R1 current 26.6 s dual (was 33.8 s in baseline log) | W2 speed work (opt_iter/profiling/pre-subdivision) not executed; no default change |
| Coverage/quality | residual defects on corpus | W2 notes: thin_gap 202.7 s reference, 9/10 poly baseline retained | No new A/B; not promoted |
| Robustness | cancel flag only | W8 `PhaseWatchdog` + progress + `PhaseStalled` | No corpus run with watchdog; synthetic + R1 |

### 1.3 Boundary layer

| Item | Measured | Residue / cause |
|---|---|---|
| fTetWild BL production | **10/15** cases with `bl_prism_cells > 0`; **9/15** coverage ≥98% (coverage values: 1.0 ×9, 0.989 ×1, 0.915 ×1, 0/None ×4) | `mini_valve` dual non-manifold; `multi_inlet`/`venturi` BL attempt fails; `dirty_cad`/`non_watertight` geometry input |
| GMSH/poly BL | **3/15** BL produced; **0/15** coverage ≥98% (box 6305, cylinder 6860, pipe 8220 prisms; all others 0) | W5 diagnosis: deterministic extrusion/topology failure on GMSH duals; the W5 `BLAttemptPolicy`/executor was designed but **not implemented** |
| Real R1 BL | 100% coverage, 174,265 prisms, 5 layers, h1 0.1418 mm vs requested 1.26 mm (0.11×) | Engine clamp at the thin dual wall; y+ target is off by 0.11×. W6 per-patch y+ report **not implemented** |
| R1 final checkMesh | 253,681 cells; 39 face pyramids, 5 highly skew, 26 non-ortho errors, `Failed 3` | Residual is the known BL/terminator class; internal-pyramid repair measured and **not promoted** (W8) |
| y+ per patch | `boundary_layer_report` gives coverage/h1 p10 per patch | No achieved y+ field/log; W6 missing |
| GUI parameters | W7: one `BLConfig`, advanced tab, presets, provenance | Complete for the poly path; cfMesh still consumes the legacy dict |

### 1.4 Robustness / process

| Item | Before M2 | After W8 | Residue |
|---|---|---|---|
| Timeouts | nine literals across workers | named `PHASE_BUDGETS` in `phase_watchdog.py` (M2) | cfMesh `MeshWorker` still has its own 4 h loop |
| checkMesh | scalar `MeshQualityReport`, `Failed` in raw text | single `CheckMeshReport` parser, defects/locations/cause/acceptance; `MeshQualityReport` is now a view (M2) | GUI has no explicit “accept” action (product decision) |
| Orphan children | GMSH fixed, `polyDualMesh` not | `PolyDualWorker` streams, PID holder, kill tree | Watertight/export paths have their own fixed timeouts |
| Corpus watchdog run | — | not re-run (W9) | No claim of “zero stalls on corpus” |

### 1.5 GUI

W7 delivered the single `BLConfig`, all advanced BL controls, preset store,
QSettings persistence and provenance.  W8 delivered progress/ETA/heartbeat
for polydual/fTetWild/GMSH and structured checkMesh logs.  The GUI does not
expose y+ per patch (W6) or a checkMesh acceptance action.

## 2. M2 exit criteria vs reality

| Exit M2 criterion | Status | Evidence |
|---|---|---|
| BL coverage ≥98% everywhere | **NOT MET** | fTetWild 9/15 ≥98%; GMSH/poly 0/15; R1 100% |
| y+ per patch visible | **NOT MET** | per-patch h1 exists, y+ does not; R1 h1 0.11× requested |
| all BL parameters in GUI | **MET for poly** | W7 `BLConfig`, advanced tab, tests |
| 0 crash/freeze on corpus | **NOT RE-RUN** | W8 synthetic/watchdog + real R1, no corpus sweep |
| benchmark re-validation | **NOT DONE** | W2/W5/W9 plans open; no new 9/10 A/B |
| process robustness | **PARTIAL** | W8 watchdog + checkMesh report; cfMesh loop and corpus run open |

Conclusion: M2 is **not** closed.  The honest state is “W7/W8 landed; W2,
W5, W6 and the W9 corpus re-validation remain”.  This milestone fixes the
structural issues that are safe now; it does not invent coverage or y+
numbers.

## 3. Structural debt (priority)

1. **W5 BL policy/executor missing (P0, feature)**.  Attempt chains exist in
   `bl_poly.run`, `DualPolyWorker`, `run_benchmarks` and `RetryRunner`; the
   same failure is handled differently.  A shared `BLAttemptPolicy` +
   executor was designed in `notes/w5_bl_diagnosis.md` but not implemented.
   Fix requires a corpus A/B; cannot be safely squeezed into the milestone
   close.
2. **W6 y+ per patch missing (P0, feature)**.  `boundary_layer_report` has
   coverage and h1 p10 but no y+ (needs u_tau/fluid and a per-patch
   achieved value).  R1 proves the gap: 0.11× h1 means y+ is off by 9×.
3. **W2 dual speed unmeasured (P1, measurement)**.  `w2_dual_speed_reasoning`
   ends with “da misurare”; no `opt_iter`/profiling verdict.  No default
   change is allowed without it.
4. **R1 residual Failed 3 (P1, engine)**.  Internal-pyramid repair measured
   and rejected; skew 170.9 and non-ortho 128 remain.  Need topology
   (terminator cells), not vertex nudges.
5. **Legacy `validate_bl_params` (P2, compatibility)**.  Still the 1..10
   fraction API used by `commercial/watertight.py`; it cannot import
   `BLConfig` because `test_validation` exec-loads the module without the
   core package.  Declared debt; do not use for new code.
6. **cfMesh `MeshWorker` (P2, process)**.  Own 4 h runtime/ETA loop; migrate
   to `PhaseWatchdog` after W9.
7. **Duplicate geometry helpers** (`bl_poly._face_geometry` vs
   `tet_poly_dual._face_geometry`; `_cell_centroids` vs `_cell_centres`).
   Deliberate performance/precision copies; consolidating is a measured
   refactor, not a milestone-end change.
8. **Reproducibility**: two identical cube-duct GMSH runs gave different
   `.msh` SHA-256 (and a 114-cell difference downstream), so GMSH's
   parallel tessellation is not byte-deterministic.  M2 adds opt-in
   `POLYFOAMMESH_GMSH_THREADS=1` (default unchanged, parallel); the
   end-to-end hash fix is not yet measured.  Do not promise byte-identical
   GMSH output until it is.

## 4. Corrections admitted in this milestone

| Fix | File(s) | Risk | Keep-best/test |
|---|---|---|---|
| **One checkMesh parser**: `parse_checkmesh_output` maps `CheckMeshReport`; delete duplicate `_CHECK_*` regexes | `core/openfoam_runner.py` | low: same metrics on v2512/legacy, tested | existing `test_checkmesh_parsing` + `test_checkmesh_report`; R1 parsed 253,681/39/5/26 |
| **Named phase budgets**: `PHASE_BUDGETS` + `PhaseBudget`; workers' constants derive from it | `core/phase_watchdog.py`, `core/openfoam_runner.py`, `core/gmsh_subprocess.py` | low: values byte-identical (300/3600/180/600/900/14400) | `test_phase_watchdog` constants test; suite |
| **Single session BL state**: replace unused split `bl_*` fields with `bl_config` mapping; old snapshots ignore them | `core/session.py` | low: no GUI caller; loader filters unknown keys | `test_session` round-trip + legacy snapshot |
| **Reproducibility lever for GMSH**: two identical cube runs produced different `.msh` SHA-256 (4B9D… vs F4F8…); add opt-in `POLYFOAMMESH_GMSH_THREADS=1` (default unchanged) | `core/openfoam_runner.py` | low: env-gated only | `test_gmsh_watchdog` env test; hash fix not yet re-measured |
| **Document M2 gap** instead of claiming exit | docs/GOAL/ROADMAP | none | this diagnosis + E2E numbers |

Not admitted: W5 policy/executor, W6 y+, W2 speed, R1 topology repair,
cfMesh loop migration.  Reasons: feature/A-B work with regression risk; a
milestone-end “just in case” change would violate keep-best.

## 5. E2E baseline and current (real runs)

- **R1 mandatory case** (fresh `C:\cfmesh_cases\m2_e2e_r1`, current tree):
  convert 4.8 s; dual + BL 92.6 s (dual 26.6 s, BL 43.7 s); total 110.0 s.
  Result: 253,681 cells, 174,265 prisms, 100% coverage, h1 0.1418 mm vs
  1.26 requested; checkMesh `Failed 3` (39 pyramids, 5 skew, 26 non-ortho
  errors).  Baseline log (2026-10-20): 120.8 s, same cell count and same
  `Failed 3`.  **No regression; ~9% faster.**
- **Cube-duct full E2E** (GMSH tet + convert + dual + BL + checkMesh):
  tet 129.1 s, convert 9.5 s, dual+BL 183.7 s, total 334.4 s; 256,767
  cells, BL coverage 100%, checkMesh `Failed 1` (non-ortho 80.48°).  The
  BL h1 is 0.0176 mm vs 0.5 mm requested (0.035×): the healthy path
  completes, but the W5/W6 compression/y+ gap is real on this case too.
- Full corpus (15 geometries) was not re-run; that is W9.  The 9/10 poly
  vs snappy baseline therefore remains the last validated benchmark.

## 6. Reuse / refactor / remove

**Reuse**: `MeshEngine`/`TetPolyDualConverter`/`PolyBoundaryLayerEngine`
as executors; `CheckMeshReport` now the only parser; `PhaseWatchdog` as the
only liveness object; `BLConfig` as the only GUI/worker BL config.

**Refactor**: phase budgets (done), report parser (done), session BL state
(done).  Next: W5 policy/executor (feature), W6 y+ report (feature).

**Remove**: duplicate checkMesh regexes (done), split session BL fields
(done).  Do not remove the legacy `validate_bl_params` until its API user
(`commercial/watertight.py`) is migrated; it is marked in the docstring.

## 7. Load estimate and risks

This milestone: 6–8 files, ~250–350 net lines (parser consolidation, budget
constants, session cleanup, tests, docs).  It is below the prompt's 400–900
range because the larger items are feature work with mandatory A/B; forcing
them here would be the “milestone as verbal only” failure in reverse.  The
remaining W2/W5/W6/W9 work is estimated 10–18 files and 800–1,500 lines.
