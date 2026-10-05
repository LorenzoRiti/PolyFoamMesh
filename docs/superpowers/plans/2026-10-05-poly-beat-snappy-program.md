# Poly "Beat Snappy" Program — Implementation Plan (overnight, ~8h)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** take the poly path (GMSH tet → dual + BL) from 4/10 to ≥7/10 gate passes, matching or beating snappyHexMesh on NOmax/skew where achievable. Every lever opt-in; defaults unchanged; every claim measured.

**Architecture:** Phase 1 stops the BL from creating sliver prisms (size-based base-face filter in `bl_poly`). Phase 2 enables the dual levers the engine already has but the benchmark never uses (smoothing iterations, compactness/axis passes, non-planar face triangulation). Phase 3 repairs the input concave pyramids post-BL (`poly_cell_merge`). Phase 4 measures the remaining margins on healthy geometries. Phase 5 integrates the promoted combo into a full poly-vs-snappy leaderboard. Phases 2-3 are independent of Phase 1's outcome.

**Tech Stack:** Python 3.11, numpy, pytest, WSL2 + OpenFOAM v2512, `benchmarks/` harness.

**Spec (binding):** `notes/poly_nonortho_reasoning.md` (probe evidence + killed-lane lessons), `benchmarks/results/GAP_ANALYSIS.md`, baseline `benchmarks/results/comparison_20261005T113621.json`.

## Global Constraints

- Project interpreter only: `& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"`; repo root for all commands.
- **Opt-in only, defaults unchanged** (byte-identical default path asserted where code changes).
- **Prediction note written BEFORE each phase's experiment** (`notes/*_reasoning.md`).
- Keep-best / never write a worse mesh; real `checkMesh` decides, not the in-process replica.
- `ruff check src/ tests/ benchmarks/` + `pytest -m "not slow and not wsl"` green before each commit.
- Local only, **no push**. Commit hook: prepend the interpreter dir to PATH; never `--no-verify`. `benchmarks/results/` → `git add -f`.
- Benchmark hard cap `RUN_TIMEOUT_S = 1500`; stage budgets unchanged.
- Kill rule per phase: if the primary metric does not improve, or a healthy geometry regresses >2° / a passing row starts failing, document the negative result and move on.
- Delegation fallback (precedent): if a fixer dispatch returns empty on a complex task, the controller implements it and an independent oracle review covers it.

---

### Task 1: BL base-face size filter (`min_base_face_fraction`)

**Files:**
- Modify: `src/polyfoammesh/core/bl_poly.py` (after `_select_faces` in `run()`, ~line 953; result dataclass ~line 99)
- Test: `tests/test_bl_poly.py` (append)

**Interfaces:**
- Consumes: nothing
- Produces: `PolyBoundaryLayerEngine.run(..., min_base_face_fraction: float = 0.0)`; when `> 0`, faces in `sel` whose `sqrt(Newell area)` is below `fraction × median(sqrt(area) of sel)` are dropped from the extrusion set (terminator bands handled by `_build` — see `_select_faces` docstring "NO auto-close… every cell stays closed"); `res.n_excluded_small_faces` counts them; `0.0` → byte-identical.

- [ ] **Step 1: Failing tests** — (a) `test_min_base_face_fraction_default_off` (constructor/run default → `n_excluded_small_faces == 0`); (b) a fixture with one crafted tiny boundary face: with `min_base_face_fraction=0.2` that face is not extruded and the written mesh stays valid; (c) `min_base_face_fraction` validation (`< 0` → ValueError).
- [ ] **Step 2: Run to see them fail** (`-k base_face`).
- [ ] **Step 3: Implement** — compute per-face `sqrt(area)` for `sel` via Newell (reuse a local helper); median; filter; log `[bl] excluded N small base faces (fraction=…)`; wire the count into `PolyBoundaryLayerResult`.
- [ ] **Step 4: Run** `pytest tests/test_bl_poly.py -q -p no:cacheprovider` + `ruff check`.
- [ ] **Step 5: Commit** `feat(bl): opt-in min_base_face_fraction filter (default off)`.

### Task 2: Harness flag `--poly-bl-min-face`

**Files:**
- Modify: `benchmarks/run_benchmarks.py` (`_benchmark_poly_one` + BL call ~line 758), `benchmarks/run_comparison.py` (`run_single`, `run_parent`, CLI, `main`)
- Test: `tests/test_run_comparison.py` (append)

**Interfaces:**
- Consumes: Task 1
- Produces: `--poly-bl-min-face FLOAT` (default 0.0; parent forwards only when > 0) → `_benchmark_poly_one(bl_min_face=)` → `PolyBoundaryLayerEngine.run(min_base_face_fraction=)`.

- [ ] Steps: failing tests (default off; forwarding value; run_single kwarg), implement, run targeted, commit `bench: --poly-bl-min-face flag`.

### Task 3: Phase-1 prediction + measurement + gate

**Files:**
- Create: `notes/poly_bl_antisliver_reasoning.md` (prediction FIRST)
- Produce (gitignored, `git add -f`): `benchmarks/results/comparison_*.json`

- [ ] **Step 1: Write the prediction note** (gap rows 8/10/16; mechanism: tiny base faces → sliver prisms; expected: cube_duct 80.6→~52 PASS, venturi 146→~74, concave 105→~63; healthy unchanged; kill criterion).
- [ ] **Step 2: Targeted B-side** fractions {0.1, 0.2, 0.3}:
  `run_comparison.py --tier fast --geometry cube_duct --geometry venturi --runner poly --poly-bl-min-face 0.2`
  and concave full-tier; read records (NO/skew/aspect/pass/warnings) + logs.
- [ ] **Step 3: Gate** (Global Constraints). Pick the best fraction; if promoted run fast+full poly regression, else document the kill.
- [ ] **Step 4: Commit artefacts + verdict note; GOAL entry.**

### Task 4: Converter smoothing pass-through (`smooth_compactness`, `smooth_axis_balance`, `smooth_worst_aspect`)

**Files:**
- Modify: `src/polyfoammesh/core/tet_poly_dual.py` (`__init__`, `_run_inner` smoothing call ~line 531)
- Test: `tests/test_poly_star_dissolution.py` or `tests/test_tet_poly_dual.py` (append)

**Interfaces:**
- Produces: `TetPolyDualConverter(..., smooth_compactness: bool = False, smooth_axis_balance: bool = False, smooth_worst_aspect: float = 0.0)` → `smooth_dual_mesh(..., use_compactness=..., use_axis_balance=..., worst_aspect_thr=...)`. Defaults off/0.0 → byte-identical.

- [ ] Steps: default-off identity test; implement; focused tests; commit `feat(poly): expose compactness/axis/worst-aspect smoothing opt-ins`.

### Task 5: Harness dual-config flags

**Files:**
- Modify: `benchmarks/run_benchmarks.py`, `benchmarks/run_comparison.py`, `tests/test_run_comparison.py`

**Interfaces:**
- `--poly-dual-smooth-iters N` (default 3), `--poly-dual-compact`, `--poly-dual-axis`, `--poly-dual-planar-fix {none,triangulate,planarize}`, `--poly-dual-worst-aspect FLOAT` → `_benchmark_poly_one(...)` → converter kwargs.

- [ ] Steps: failing tests (defaults off/3/none), implement, targeted tests, commit `bench: poly dual-config flags`.

### Task 6: Phase-2 prediction + A/B matrix

**Files:**
- Create: `notes/poly_dual_config_reasoning.md`

- [ ] **Step 1: Prediction note** (levers and expected deltas; compactness/axis measured −12.4% NOmax on cylinder; non-planar faces worsen NO/skew per the converter docstring).
- [ ] **Step 2: Matrix** on cube_duct/venturi (fast) + concave (full) + healthy (cylinder/groove1/slot1/thin_gap/multi_inlet): configs {iters10}, {compact+axis}, {compact+axis+iters10}, {+triangulate}, {triangulate}. Read records; pick per-metric winners.
- [ ] **Step 3: Gate**; combined best config regression fast+full; commit + verdict.

### Task 7: Phase-3 concave pyramid repair

**Files:**
- Modify: `benchmarks/run_benchmarks.py` (post-BL, after the BL stage), `benchmarks/run_comparison.py`, tests.
- Create: `notes/poly_pyramid_repair_reasoning.md`

**Interfaces:**
- `--poly-merge-repair` → after BL, read polyMesh, `poly_cell_merge.repair_concave_cells_if_safe(...)` (keep-best inside), write back; report `report` in warnings.

- [ ] Steps: prediction note; flag + tests; measure concave (primary: checkMesh `Failed`→OK, pyramid count) + fast/full regression (slot1 is the documented risk); gate; commit + verdict.

### Task 8: Phase-4 margins on healthy geometries

**Files:**
- Create: `notes/poly_margins_reasoning.md`

- [ ] **Step 1: Prediction note** (skew gap targets: cylinder 2.77→<0.9? groove1 3.67, slot1 3.59; levers: iters20, worst_aspect, planarity).
- [ ] **Step 2: Measure** the best combo from Tasks 3/6/7 + `--poly-dual-smooth-iters 20` and `--poly-dual-worst-aspect 5000` on cylinder/groove1/slot1; compare vs snappy; document what closes the gap and what does not (split operator stays future work).
- [ ] **Step 3: Commit + verdict.**

### Task 9: Phase-5 integration + leaderboard

**Files:**
- Produce: `benchmarks/results/comparison_<ts>.json` (full poly with the promoted combo), `benchmarks/results/LEADERBOARD_poly_optin.md`

- [ ] **Step 1: Full poly tier** with all promoted flags.
- [ ] **Step 2: Leaderboard** `compare_snappy.py --results <json> --out benchmarks/results/LEADERBOARD_poly_optin.md`; extract pass rate + per-metric table vs snappy.
- [ ] **Step 3: Commit artefacts.**

### Task 10: Docs, GOAL, final report

- [ ] Update `.opencode/GOAL.md` (Italian, numbers verbatim, promoted/killed per phase); tick/append note verdicts; append `docs/residual_risks.md` if a new limitation is found.
- [ ] Final verification: `ruff` + fast suite; commit `docs: poly beat-snappy program results (locale)`.
- [ ] Report to the human: per-phase numbers, pass rate before/after, what beats snappy and what does not, next options.

---

## Self-Review

**Coverage:** BL anti-sliver → T1-T3; dual config → T4-T6; pyramids → T7; margins → T8; integration/report → T9-T10. Each phase has a prediction, a gate, and a documented verdict. Phases 2-3 independent of Phase 1.

**Risks:** (a) anti-sliver terminator bands may add NO → gate; (b) compactness/axis measured only on cylinder → venturi may need iters too; (c) merge repair documented regression risk on slot1 → keep-best + real checkMesh; (d) WSL time is small for poly runs (seconds-minutes).

**Type consistency:** `min_base_face_fraction` → `--poly-bl-min-face`; `smooth_compactness`/`smooth_axis_balance`/`smooth_worst_aspect` → `--poly-dual-compact`/`--poly-dual-axis`/`--poly-dual-worst-aspect`; `repair_concave_cells_if_safe` → `--poly-merge-repair`.
