# HANDOFF — Solution-Adaptive Mesh Refinement (SAMR)

**Status: WORKING END-TO-END ON THE VENTURI. All three section-5 acceptance
criteria met with real numbers (see §A below). Remaining: valve-part validation
at 750k–2.2M tets, GUI wiring, and pytest coverage.**

---

## §A. VENTURI ACCEPTANCE RESULTS (verified 2026-08-01)

Command:
```
python tools/venturi_amr_validation.py --cycles 3 --mesh-size 10 \
    --solve-iters 300 --final-iters 600 --max-cells 700000
```

| metric | before | after | growth |
|---|---|---|---|
| cells in throat window | 3,284 | 118,546 | **36.10x** |
| cells in straight window | 3,827 | 6,734 | **1.76x** |
| throat/straight ratio | 0.86 | **17.60** | 20x better |
| total cells | 76,659 | 706,903 | 9.2x |

- (a) **Physics correct**: |U|max = 17.36 m/s at x = 0.2408 m — the throat.
  Inlet 1.0 m/s, area ratio (50/15)^2 = 11.1, so continuity predicts ~11 m/s.
  The indicator's own peak sits at x = 0.283 m, also in the throat.
- (b) **Cells went where the physics demanded**: 36.10x at the throat.
- (c) **Straight sections did NOT balloon**: 1.76x.
- **Runtime: 54 s for the whole cycle** (solve 37 s, remesh 16 s) at
  76,659 -> 706,903 cells.
- Cell-count prediction error: **1.1%** (predicted 699,386, actual 706,903).

For contrast, the same harness before the §B.3 fix produced throat 12.90x vs
straight 9.58x, ratio 1.15 — i.e. nearly uniform refinement that technically
"ran" and would have passed a less careful eyeball check.

---

## §B. FOUR REAL BUGS FOUND BY RUNNING IT (all fixed)

These are the substance of the work; the module itself was the easy part.

1. **The case template could not produce a runnable case at all.** No `0/nut`
   (simpleFoam+kOmegaSST refuses to start), no wall functions, zero inlet
   velocity, `fixedValue 0` omega at outlets. Nothing had ever run a solver, so
   nobody had noticed. Fixed in `case_setup.py`; see §3.

2. **`writeInterval` > `endTime` meant NO time directory was ever written.**
   `writeControl` is `timeStep`, so a 300-iteration run with
   `writeInterval 1000` completed in 38 s and wrote nothing; the loop then died
   with "the solver produced no solution to compute an indicator from". Only
   bites when the iteration limit is hit — a *converged* run escapes via
   `writeAndEnd()` — so it hides precisely on the short intermediate solves the
   loop depends on. `setup_case` now clamps `writeInterval` to `endTime`.

3. **The size field was scattering EVERY cell, not just refined ones.** This
   was the big one, and it was a flaw in the original design of this module.
   The initial mesh already contains 0.77 mm cells near walls (from geometry
   sizing) against a 7.5 mm lattice spacing, so almost every lattice node
   inherited a sub-millimetre size and GMSH refined the entire domain. Symptoms:
   straight section growing 9.58x, and a budget loop that could never converge
   because it was fighting an inherited floor unrelated to the flow.
   Fix: only cells with `h_target < h_current` enter the lattice; everything
   else sits at a coarse baseline. The unrefined bulk does not need to be in
   this field — GMSH min-combines it with the geometry sizing, which reproduces
   near-wall refinement by itself.

4. **The cell-count prediction was 16x wrong, so the budget guard was dead.**
   Predicted 116,598, got 1,822,916. Two causes: (i) the per-cell wish list is
   not what GMSH sees — it goes through a lattice that smears small sizes; and
   (ii) a unit conflation, `h = V^(1/3)` is a VOLUME-EQUIVALENT length while a
   GMSH size field value is a CHARACTERISTIC EDGE length, differing by
   `(6*sqrt2)^(1/3) ~ 1.82` in length and `8.49` in count.
   Fix: predict by sampling the actual lattice trilinearly at real cell centres
   (keeps the estimate inside the fluid domain), min-combined with the current
   sizes to model GMSH's own MIN, times a calibration constant that the refiner
   **recalibrates after every remesh** from the real before/after counts.
   Prediction error went 0.064x -> 1.001x on the ground-truth case, and 1.1% in
   the live run.

**Also learned, and worth not re-deriving:** raising the refine *quantile* is a
useless budget knob. Measured: 0.85 -> 0.9953 moved the prediction only
1.82M -> 1.23M and never reached a 700k budget, because dropping marginal cells
barely shrinks the refined *volume*. Scaling the target *size* works
analytically instead — count goes as h^-3, so `s = (predicted/budget)^(1/3)`
lands on the budget in ~2 steps — and it preserves WHERE refinement goes while
relaxing only HOW MUCH.

Repo: `C:\Users\Davide Valoroso\cfmesh-autogui` (Windows, WSL2 Ubuntu,
OpenFOAM 2512 at `/usr/lib/openfoam/openfoam2512`). Branch `master`.
Work started from commit `1513a39`. **Nothing has been committed yet** — all
changes below are uncommitted working-tree edits. Run `git diff` first.

Python that has the deps (gmsh 4.15.2, pyvista 0.48.4, numpy 2.4.6):
`C:\Users\Davide Valoroso\AppData\Local\Programs\Python\Python311\python.exe`
— NOT the bare `python` on PATH, which has no gmsh. There is no venv.

---

## 0. TL;DR of what changed

| File | State | What |
|---|---|---|
| `src/cfmesh_autogui/core/solution_adaptive.py` | **NEW** | The whole SAMR engine. Verified end-to-end on the venturi — see §A. |
| `tools/venturi_amr_validation.py` | NEW (other agent, + my `--max-cells` and `window_counts` fixes) | The validation harness. Drives the engine and prints the acceptance numbers. |
| `src/cfmesh_autogui/core/gmsh_wrapper.py` | modified | New `apply_solution_size_field()`; new `size_field_file=` param on `generate_volume_mesh()`; background-field tag now threaded so fields compose instead of clobbering. |
| `src/cfmesh_autogui/core/case_setup.py` | modified | `setup_case()` can now write a case that is actually **runnable** (real inlet velocity, `nut`, wall functions, `inletOutlet` outlets, residual control, endTime). It previously could not be. |
| `docs/solution_adaptive_handoff.md` | NEW | This file. |

Scratchpad (throwaway, safe to delete):
`C:\Users\DAVIDE~1\AppData\Local\Temp\claude\C--Users-Davide-Valoroso-cfmesh-autogui\94ffce05-649f-4cf6-a8da-aeba57f1bab9\scratchpad\`
containing `probe_structured.py` (GMSH field-format probe) and
`test_indicator.py` (analytic indicator test).

---

## 1. What was established BEFORE writing code (do not re-derive this)

### 1.1 There are four adaptive-sounding things in this repo. Only one is new.

Confirmed by reading the code, not by grep alone:

- **`gmsh_wrapper._configure_adaptive_sizing()`** (line ~481) — refines from CAD
  *geometry* (curvature, small curves, detected gaps) before any mesh or solve
  exists. Out of scope, untouched, and the new size field is deliberately
  MIN-combined with it.
- **`commercial/adaptive_loop.py`** (2,681 lines) + `adaptive_integration.py`,
  `adaptive_cli.py`, `gui/ooda_panel.py` — the OODA engine. Observes **mesh
  quality** (skewness, non-orthogonality, aspect ratio) and remediates bad
  cells. Every "gradient" in it is sizing-field grading, not a flow gradient.
  Untouched.
- **`commercial/amr.py`** (444 lines) — **the original handoff did not mention
  this file, and it matters.** It genuinely does gradient-based refinement:
  `postProcess -func 'grad(U)'` → `mag(grad(U))` → `topoSet fieldToCell` →
  `refineMesh -overwrite` → remap fields via `cellMap`. It is well-commented
  and its comments record real bugs already fixed (e.g. `refineMesh` has no
  `-dry-run`; `set` must be a word not a dict block).
  **Why it is NOT the answer to this task, and why I built alongside it rather
  than extending it:**
  1. It never runs a solver. It operates on fields that must *already* exist,
     at time 0. There is no solve→indicate→remesh loop, only a
     indicate→refine→indicate loop over static data.
  2. `refineMesh` with `useHexTopology yes` is the hex-oriented family the
     design doc in `poly_workflow_part2.md` explicitly rules out for this
     app's tet/poly meshes.
  3. It cannot coarsen (its own docstring says so), and it leaves hanging
     nodes. Remeshing from CAD has neither limitation.
  **Decision to revisit if you disagree:** `amr.py` could be kept as a
  cheap in-place alternative for hex meshes. It is currently dead code as far
  as the GUI is concerned. Do not delete it without checking `adaptive_cli.py`.
- **NEW: `core/solution_adaptive.py`** — the actual task. Remesh-based SAMR.

### 1.2 Nothing in this app ever runs a solver

`case_setup.setup_case()` writes a case; `main_window.py:3758` logs
`"[solver] Setup complete"` and **stops there**. There was no code anywhere that
executes `simpleFoam`. That is why `run_solver()` had to be written from
scratch, and why `setup_case` had to be made capable of producing a case that
can actually run (see §3).

### 1.3 Two empirical findings about GMSH that the design depends on

Probed live with `scratchpad/probe_structured.py` against GMSH 4.15.2:

1. **GMSH's `Structured` background-field text format works and its index
   order is `(i*n1 + j)*n2 + k` (plain C order).** A lattice asking for size
   0.02 in half a unit box and 0.15 in the other half produced
   **27,032 vs 231 nodes** — a 117:1 ratio in the expected direction.
   This is what makes a genuinely spatially-continuous size field possible,
   instead of approximating a shear layer with axis-aligned balls.

2. **`Mesh.CharacteristicLengthMin` is a HARD FLOOR applied AFTER the
   background field is evaluated.** Same lattice, same geometry: floor at 0 →
   27,263 nodes; floor left at the coarse value 0.15 → **443 nodes**.
   **This is the trap that would have made the whole feature silently do
   nothing.** `apply_solution_size_field()` lowers the floor to the field's own
   minimum for exactly this reason. If refinement ever appears to "run fine but
   change nothing", check this first.

### 1.4 A pre-existing bug found in passing (NOT fixed — decide what to do)

The old `refinement_zones` block in `generate_volume_mesh` (line ~1360) calls
`setAsBackgroundMesh()` unconditionally, which **replaces** the
geometry-adaptive background field installed by `_configure_adaptive_sizing`.
So using the GUI's "Manual refinement zones" silently discards all
curvature/small-feature/gap sizing. It also sets `Ball` `VOut = -1`, a
negative target size whose behaviour inside a `Min` field is at best undefined.

I did **not** change that behaviour — it is orthogonal to this task and
changing it would alter existing meshes users may be relying on. But the new
code threads a `bg_field_tag` through so the new path composes correctly, and
the fix for the old path is now a two-line change if you want it.

---

## 2. What was built: `core/solution_adaptive.py`

### 2.1 The indicator, and why (acceptance criterion #2)

```
eta_c = h_c * ||grad U||_F / U_ref        h_c = V_c^(1/3)
```

**Reasoning, grounded in CFD fundamentals, not "this is common":** a
finite-volume scheme reconstructs the solution across a cell from cell-centred
values; the leading term it *cannot* represent is the Taylor remainder, which
scales with how much the solution actually changes over one cell width, i.e.
`h·|grad U|`. Normalising by a reference speed makes eta the **dimensionless
fraction of the flow's own velocity scale being lost inside a single cell**.
`eta = 0.5` means velocity changes by half the reference speed across one cell
— that cell resolves nothing. `eta = 0.01` means the field is locally almost
linear at cell scale and refining further buys little. That is exactly "is this
region under-resolved for the flow", and by continuity it peaks where area
changes fastest (a throat), plus at shear layers, separation and wakes.

`U_ref` is the **99th percentile** of |U|, not the max — on a real case the max
is usually one spurious cell at a BC corner, which would deflate the entire
indicator field.

Refinement targets **equidistribution**: drive eta toward a uniform target
`eta_t`, and since eta is linear in h, `h_new = h_old * (eta_t/eta_c)`, clamped.
Cells below `eta_t` are untouched — **the field never asks to coarsen**, which
is what makes MIN-combining with the geometry field correct.

Hessian/adjoint indicators are deliberately deferred: more powerful (adjoint can
target pressure drop directly) but both need machinery that does not exist here,
and the simple one is what can be *proven* to put cells in the physically
obvious place first.

### 2.2 Key API

```python
AdaptiveParams(inlet_velocity, solver_iterations=400, final_solver_iterations=1500,
               residual_control=1e-4, refine_quantile=0.85, max_refine_ratio=3.0,
               min_cell_size=None, grid_resolution=64, grid_growth_rate=1.4,
               max_cycles=3, max_cells=3_000_000, qoi_tolerance=0.02, ...)

compute_indicator(grid, velocity_field="U", u_ref=None) -> dict
target_size_field(indicator, refine_quantile, max_refine_ratio,
                  min_cell_size, max_cells) -> dict
write_structured_size_field(path, centres, h_target, bounds,
                            resolution, growth_rate, baseline=None) -> dict
run_solver(case_dir, of_config, application, timeout_s, on_line) -> dict
load_solution(case_dir, of_config, timeout_s, on_line) -> pyvista grid
pressure_drop_qoi(grid, case_dir) -> float

SolutionAdaptiveRefiner(params, of_config, on_line)
    .analyse(case_dir, cycle, size_field_path, bounds) -> (CycleReport, sf_info|None)
    .run(initial_case_dir, bounds, remesh_fn, work_dir) -> AdaptiveResult
```

`remesh_fn(size_field_file: Path, cycle: int) -> (case_dir, n_cells)` is a
**caller-supplied callback**. This is the main extension seam and the reason
the module has no GUI/subprocess dependency: the caller decides whether the
remesh happens in-process, in a `GmshVolumeWorker` subprocess, etc. It must
regenerate from the **original CAD** with `generate_volume_mesh(size_field_file=...)`.

### 2.3 Deliberate design decisions, with the reasoning (challenge these if wrong)

- **Volume-weighted percentiles** for the refinement threshold. Plain
  percentiles let a corner full of tiny cells dominate the distribution and drag
  the threshold toward wherever the mesh already happens to be finest — the
  classic feedback loop that makes naive AMR refine the same spot forever.
  Volume weighting asks "what fraction of the flow *domain* is under-resolved",
  which is mesh-independent. **This is important and easy to accidentally undo.**
- **Scatter, not interpolate**, when building the lattice. Each cell centre
  writes into its 8 surrounding lattice nodes taking the minimum. The obvious
  alternative (each lattice node looks up its nearest cell) silently **loses any
  refined region thinner than the lattice spacing** — precisely the thin shear
  layers and throats worth refining. Scattering can over-cover but never miss.
- **Gradation sweep** (`_apply_gradation`) caps neighbour-to-neighbour growth so
  GMSH never absorbs an abrupt size jump with a shell of badly-shaped cells.
- **Short intermediate solves** (400 iters), long final solve (1500).
  Rationale: the indicator needs only the *spatial structure* of grad(U), which
  is established within a few hundred SIMPLE iterations, long before residuals
  bottom out. **THIS IS A HYPOTHESIS AND IS NOT YET VERIFIED — see §4.1.**
- **Stopping criterion is the QoI, not the mesh**: stop when inlet→outlet
  pressure drop changes < 2% between cycles. "The mesh changed" proves nothing;
  convergence in the engineering quantity is what says the answer stopped
  depending on the mesh.
- **Cell budget raises the quantile rather than silently truncating**, and logs
  that it did.
- **`pressure_drop_qoi` is a proxy**: mean p over cells in the first/last 5% of
  the flow-direction extent, because `internal.vtu` holds only the internal
  field (patch data is in separate files). It is used only as a *relative*
  measure between cycles, where a consistent proxy is as good as the true value.
  If you want the true area-weighted patch value, parse the boundary VTUs.

### 2.4 What IS verified (analytic test, `scratchpad/test_indicator.py`)

Analytic 1-D venturi-like field `U = (u(x),0,0)` on a unit box, `u` peaking
4x at a throat at x=0.5 with sigma=0.04, 2,560 cells:

```
U_ref=3.857
eta: min=0.0000 mean=0.0995 p95=0.6185 max=0.6185
peak at x=0.4625     <- theory says steepest du/dx at 0.5 - sigma = 0.46. Correct.
eta in the straight section: max=0.000000  (throat/straight ratio ~4.8e9)
threshold eta_t=0.3393  marked=256/2560  predicted cells=3,718
mean target size: throat=0.06044  straight=0.07310
size field lattice (49,49,49) range 0.04010..0.07310, all tokens float-parsable
```
Assertions that passed: peak sits on a throat shoulder; throat refined more than
straight section; **`h_target <= h_current` everywhere** (never coarsens);
budget clamp binds correctly (cap 3,000 → quantile auto-raised 0.85→0.9025,
predicted 2,587, marked 128).

**Caveat on that last "all tokens float-parsable" check**: it exists because
`repr(np.float64(x))` under numpy 2.x is `"np.float64(0.01)"`, which would write
a size-field file GMSH cannot parse. The code uses `repr(float(v))`. Do not
"simplify" that back.

---

## 3. `case_setup.py` — why it had to change

The old template **could not produce a runnable case**, which nobody had noticed
because nothing ever ran one:
- inlet U was `fixedValue uniform (0 0 0)` → solves to a dead, motionless flow;
- **no `0/nut` at all** → `simpleFoam` with kOmegaSST refuses to start
  ("cannot find file nut");
- walls got plain `zeroGradient` for k/omega instead of wall functions;
- outlets got `fixedValue uniform 0` for k and omega — omega=0 at an outlet is
  numerically hostile;
- no residual control, no way to set endTime.

Changes made (all backward compatible — every new arg defaults to the old
behaviour, and the only external caller is `main_window.py:2471` via
`**self._case_setup_kwargs()`):
- `_boundary_block` now takes an `overrides` dict keyed by patch role
  (`inlet`/`outlet`/`wall`/`other`) instead of a single `inlet_value`;
  new `_patch_role()` helper.
- `setup_case(..., inlet_velocity=None, end_time=None, residual_control=None,
  write_interval=None)`. Passing `inlet_velocity` switches the case into
  "actually runnable" mode: real inlet U, k/omega seeded from 5% turbulence
  intensity, `nut` written with `nutkWallFunction`, `kqRWallFunction`,
  `omegaWallFunction`, and `inletOutlet` outlets (a constriction can drive
  transient recirculation back through the outlet during the SIMPLE march, and
  `zeroGradient` there lets that convect unbounded turbulence in and diverge).

**UNVERIFIED:** none of this has been fed to `simpleFoam` yet. The very first
thing the next session should do is §4.1.

---

## 4. WHAT REMAINS — in priority order

### 4.1 DONE — the case template runs, and patch roles are inferred

`simpleFoam` starts and converges. Patch-role inference (the blocker predicted
here) was solved by `case_setup.infer_patch_roles()` + `set_wall_patch_types()`,
which classify GMSH's `surface_N` patches geometrically. Verified live:
`roles={'surface_2': 'inlet', 'surface_3': 'outlet', 'surface_1': 'wall'}`.
Workdir must still be space-free (`C:\cfmesh_work\...`) — `OFConfig.
validate_case_path()` rejects spaces and the repo path has one.


### 4.2 Verify the short-solve hypothesis (§2.3) empirically

Run the same mesh to 200, 400, and full convergence. Compute the indicator
field each time and report the **correlation / relative L2 difference of the
normalised eta field** between them. If a 400-iteration eta correlates > ~0.95
with the converged one, `solver_iterations=400` is justified — say so with the
number. **If it does not, raise the default and say that instead.** Do not ship
the current default as if it were measured; right now it is a guess.

### 4.3 DONE — venturi validated, see §A for the numbers

`tools/venturi_amr_validation.py` runs the whole thing. One caveat for whoever
picks this up: the loop stops after cycle 1 when `--max-cells` is hit, so a
multi-cycle QoI-convergence demonstration needs a budget well above the first
cycle's output (e.g. `--max-cells 2500000`).


### 4.4 Valve part (acceptance criterion #5)

`C:\Users\Davide Valoroso\Desktop\Report\Parte4.stp` — 89 surfaces,
3.0 x 0.255 x 0.255 m, 750k–2.2M tets. Report honest wall-clock per cycle. A
multi-cycle solve+remesh at 2M cells could easily be impractical.
**If it is too slow, say so explicitly and propose what to cut** (coarser
first-pass mesh for the initial indicator — the module already supports this,
just mesh coarse and let the loop refine; fewer cycles; cheaper indicator).
Do not ship a technically-correct 20-minute loop without flagging it.

Note `poly_dual_risk` fires on this part (89 surfaces > 30), so poly conversion
is known-unreliable there — validate SAMR on the **tet** mesh, and keep it
separate from the parallel poly-converter effort.

### 4.5 GUI wiring — worker DONE, button still to do

**Done:**
- `openfoam_runner.SolutionAdaptiveWorker` (appended at the end of the file):
  QObject worker with `log_line` / `finished` / `failed` / `cycle_done` signals,
  a `cancel()` slot, and a caller-supplied `remesh_fn` so the adaptive path
  reuses the GUI's existing mesh generation instead of duplicating it (which
  would let the adaptive mesh silently diverge from the normal one).
- `GmshVolumeWorker(size_field_file=...)` now sets `GMSH_SOLUTION_SIZE_FIELD`,
  which `gmsh_wrapper.__main__` already reads. That is the whole remesh seam.

**Still to do:** the button in `main_window.py`, and a `remesh_fn` that reuses
the window's existing GMSH-volume path. NOTE: another agent was editing
`main_window.py` and `openfoam_runner.py` concurrently for the poly-converter
work, so re-check both before editing — my changes there were kept deliberately
additive for that reason.

Logging is already complete on the engine side: every stage emits a tagged line
(`[cycle N]`, `[solve]`, `[indicator]`, `[adaptive]`) covering solve start with
cell count, iteration progress every 25 steps with residuals, solve finish with
convergence status, indicator stats + peak location, cells marked + target
sizing, size-field lattice shape + predicted cells, budget relaxation, remesh
before/after counts, cell-count recalibration, QoI + % change, and the stop
reason. Lorenzo's "tutto deve essere esplicito nel log" requirement is met by
the engine; the GUI just has to connect `log_line` to the panel.


### 4.6 Tests + commit

No pytest tests written yet. `scratchpad/test_indicator.py` should become a
real test under `tests/` (it needs no OpenFOAM — pure numpy/pyvista, runs in
seconds). Nothing is committed; commit in logical chunks
(gmsh_wrapper seam / case_setup runnability / solution_adaptive engine /
validation harness).

---

## 5. Traps and things NOT to do

- **Do not** call `setAsBackgroundMesh()` for the solution field directly — it
  replaces the geometry field. Always MIN-combine (`apply_solution_size_field`
  does this; `existing_bg_field` comes from `sizing_info["bg_field"]`).
- **Do not** forget `Mesh.CharacteristicLengthMin` (§1.3.2). Silent no-op.
- **Do not** use `repr()` on numpy scalars when writing the size field (§2.4).
- **Do not** reach for `dynamicRefineFvMesh` or `refineMesh` — ruled out by the
  design doc and by `amr.py`'s own experience.
- **Do not** hand-roll VTU/field parsing; PyVista is a dependency and is proven
  here at ~2M cells.
- **Do not** validate on the valve first. Venturi first, with numbers.
- **Do not** replace volume-weighted percentiles with plain ones (§2.3).
- The `_weighted_stats` return dict smuggles a closure under the key
  `"_quantile_fn"` in a dict otherwise typed `float`. It works, it is ugly, and
  `target_size_field` depends on it. Refactor deliberately or leave alone.
- `compute_indicator` promotes cell→point data to take the gradient (VTK
  differentiates over shape functions, which needs nodal values) and demotes
  afterwards. That round trip costs a little smoothing. It is the right call
  versus hand-rolling face-neighbour differencing, but it is the place to look
  if the indicator ever looks over-diffused on a coarse mesh.

---

## 6. Exact verification commands

```bash
# deps
"/c/Users/Davide Valoroso/AppData/Local/Programs/Python/Python311/python.exe" -c "import gmsh,pyvista,numpy;print('ok')"

# OpenFOAM toolchain
wsl -e bash -lc 'source /usr/lib/openfoam/openfoam2512/etc/bashrc; which simpleFoam foamToVTK checkMesh'

# the analytic indicator test (no OpenFOAM needed, seconds)
cd <scratchpad> && "<py311>" test_indicator.py

# the GMSH structured-field probe
cd <scratchpad> && "<py311>" probe_structured.py
```

---

## 7. Night session results (2026-08-01, after commit 1513a39)

Night-session additions are committed (5945c6c, 4af9b7b, e879768, e3e79f7)
on top of the earlier SAMR commits (33e586f, b76c979, f052946, 0de7cac). The only
uncommitted pieces are the GUI button in `gui/main_window.py` (mixed with the
concurrent poly-converter workstream — left unstaged for that agent to
reconcile) and the concurrent agent's own poly-dual files.

### 7.1 Real valve `Parte4.stp` — the auto-resolution fix CONFIRMED (Part 3.4)

`tools/valve_resolution_check.py --detail medium|fine` on the 3.0 x 0.255 x
0.255 m part, adaptive path (`max_cell_size=0`):

| detail | cells | wall time | checkMesh |
|---|---|---|---|
| medium | 6,122,424 | 268 s | passed | maxNonOrtho 89.7, skew 1.058, neg 0 |
| fine   | 10,460,040 | 635 s | passed | maxNonOrtho 89.7, skew 1.094, neg 0 |

This directly answers Lorenzo's long-standing complaint: the old auto mode
gave 750k–2.2M (bulk ~1,800 cells on this part). The `cells_across` /
cross-section sizing fix is real and measured: medium alone now lands at
6.1M — above his 9M commercial target was never reached at the default
detail; "fine" gives 10.5M. Both pass checkMesh. Caveat: maxNonOrtho ~89.7
(> the 70° target) comes from the near-wall/surface refinement on this
89-surface part, and `poly_dual_risk=True` (89 surfaces, 50 gaps) — the SAMR
loop should stay on the tet mesh here.

### 7.2 Short-solve hypothesis MEASURED (Part 4.3)

`tools/short_solve_correlation.py`, same 76,659-cell venturi mesh solved to
200 / 400 / 3000 iterations:

| budget | Pearson r vs 3000 | rel-L2 vs 3000 |
|---|---|---|
| 200 | 0.800 | 0.398 |
| 400 | 0.938 | 0.233 |

The 3000-iteration reference did NOT itself converge (SIMPLE residuals
oscillate ~1e-2, `SIMPLE solution converged` never fired), so these numbers
understate the correlation with a genuinely converged field. Verdict:
**solver_iterations=400 is defensible for WHERE to refine** (r=0.94 captures
the spatial structure), but the indicator MAGNITUDE is not quantitatively
converged — keep the final-cycle long-solve / residualControlled run for any
number you report as a result. r at 200 (0.80) is too low; do not lower the
intermediate budget.

### 7.3 Budget fix confirmed (Part 4.4)

Multi-cycle run (`--max-cells 2500000 --cycles 3`, mesh-size 10): cycle 2's
prediction (after the calibration split fix and in-run recalibration 5.10 ->
7.09 -> 3.71) landed at 1,677,559 cells — under the 2.5M budget. The old
broken model produced 10.3M. The QoI criterion (pressure-drop change < 2%)
has still not been observed firing; cycle-2 QoI moved +11% (32.669 ->
36.271) because resolving the throat raises peak velocity (12.66 -> 14.4 m/s)
and hence the loss — physically right, just not converged yet at 3 cycles.
Pressure drops in the report are proxy values from the internal-field VTU
(first/last 5% of the flow-axis cells), not true area-weighted patch values.

### 7.4 GUI button (Part 4.2) — in the working tree, NOT committed

Ribbon button **"Solve Adaptive"** (`_on_solution_adaptive` in
`main_window.py`) + `core/gmsh_subprocess.py` (committed). Flow: requires a
meshed runnable case -> three QInputDialogs (inlet |U|, cycles, cell budget)
-> `SolutionAdaptiveWorker` with a remesh_fn using `gmsh_subprocess.
remesh_from_cad` on the ORIGINAL CAD. It reuses the exact out-of-process
GMSH path. Keep changes to `main_window.py` strictly additive (concurrent
agent owns it too).

### 7.5 Valve SAMR loop (Part 4.1) — honest runtime verdict

Measured solver scaling: 76,659 cells -> 300 iters in 41 s
(~1.78e-6 s/cell/iter). Extrapolating: a 6M-cell solve at 300 iters is
~54 min per intermediate cycle; a multi-cycle valve loop is impractical at
default detail. **Recommended cut: mesh the valve coarse (or "medium"
non-adaptive) for the FIRST indicator solve, let the loop refine from
there, cap cycles at 2.** The engine supports this naturally — the initial
mesh is just the first case; the loop refines it.

### 7.6 Tests (Part 4.5)

26 tests in the SAMR set, all green, no OpenFOAM needed:
`test_solution_adaptive` (13) + `test_infer_patch_roles` (4) +
`test_case_setup_runnable` (4) + `test_gmsh_cells_across` (5).

### 7.7 Hardware/performance optimization (night session part 2)

Goal: make the loop actually usable at scale, not just correct. Commits
`7530c8e`, `06b9dec`, `57c65cb`.

**Parallel solver — the big one.** The serial solve dominates the per-cycle
wall time and scales superlinearly with cells. `run_solver(n_cores=N)` now
solves in parallel on native WSL tmpfs (the app's parallel-mesh pattern —
OpenMPI segfaults on /mnt/c): copy case to /tmp -> decomposePar -> mpirun
`solver -parallel` -> reconstructPar -> copy the latest time dir back.
`AdaptiveParams.solve_cores` threads it through the loop; the engine (and the
GUI button) auto-stays-serial below 250k cells where the ~1 min
decompose/reconstruct overhead isn't worth it.

Measured, same 971k-cell mesh:
| mode | 300 iters | vs serial |
|---|---|---|
| serial | ~1350s (extrapolated from 100-it runs) | 1x |
| 8 cores | 279-314s | ~4x |

Two real bugs found making it work: decomposePar does NOT copy the uniform
constant/ dictionaries (transportProperties etc.) into `processorN/` — rank 0
aborts — and the file is `constant/transportProperties` (no dot), so a
`*.transportProperties` glob never matched. Both fixed.

**GMSH threading: measured ~no benefit** (valve medium 268 s single-thread
vs 274 s with 8) — the HXT/curvature workload is not OpenMP-friendly here.
The GMSH_NUM_THREADS hook exists but the GUI deliberately leaves meshing
single-threaded (`06b9dec`).

**Three more real bugs found by running the optimized loop** (`57c65cb`):
1. Budget relaxation stalled at a false floor: scale reaching `max_refine_ratio`
   collapses every `h_try` to `h_orig`, the still-mask went empty, and the
   fallback scattered current near-wall sizes — prediction pinned at 8.53M
   across no-op relaxations (the calibration-floor bug resurfacing). Scale is
   now clamped to `max_refine_ratio*0.97`; verified 47M -> one relaxation ->
   1.73M <= 2M budget.
2. `writeInterval` smaller than `endTime` (200 vs 300) made the solver write
   only time 200, so the indicator read a stale solution. `set_end_time` now
   forces `writeInterval = endTime`.
3. mpirun on WSL tmpfs is occasionally flaky (the same case solved once and
   aborted to next run) — `run_solver` retries once for the parallel path.

**End-to-end after optimization** (venturi, 76k -> ~1M): 2 cycles in ~6 min,
cycle 2 (971k cells, 300 iters, 8 cores) = 314 s vs ~1350 s serial. Combined
with the coarse-first-mesh guidance (Part 7.5), a 6M-cell valve loop is now
~15-20 min/cycle instead of ~55 min — much closer to usable.

**GUI**: `_on_solution_adaptive` auto-selects 1-8 solve cores by cell count
and logs the choice; `--cores` added to `tools/venturi_amr_validation.py`.
