# TASK PROMPT — continue: solution-adaptive refinement + automatic mesh resolution

Repo: `C:\Users\Davide Valoroso\cfmesh-autogui` (Windows 11, WSL2 Ubuntu,
OpenFOAM 2512 at `/usr/lib/openfoam/openfoam2512`). Branch `master`.
Session started from commit `1513a39`. **NOTHING IS COMMITTED.** Everything
below is uncommitted working-tree state — run `git status` and `git diff` first.

**Python with the deps** (gmsh 4.15.2, pyvista 0.48.4, numpy 2.4.6, cadquery 2.8.0):
```
C:\Users\Davide Valoroso\AppData\Local\Programs\Python\Python311\python.exe
```
NOT the bare `python` on PATH — it has no gmsh. There is no venv.

**Work dirs must have NO SPACES** — `OFConfig.validate_case_path()` rejects them
and the repo path itself has one. Use `C:\cfmesh_work\...`.

> ⚠️ **ANOTHER AGENT IS WORKING IN THIS REPO CONCURRENTLY** on the poly-converter
> workstream (`core/tet_poly_dual.py`, `commercial/tet_poly_volume.py`,
> `tools/tet_poly_dual_cli.py`, `docs/poly_dual_*.md`). It is actively editing
> **`gui/main_window.py`** and **`core/openfoam_runner.py`**. One edit this
> session hit a "file modified on disk" warning. Keep changes to those two files
> **strictly additive** (append new classes at end of file, add optional kwargs)
> and re-read before editing. Do not reformat or reorganise them.

---

# PART 1 — WHAT THIS IS

Two related things were worked on. Both are about the same underlying complaint
from the user (Lorenzo/Davide, a CFD engineer — he knows the physics, so give him
numbers, not reassurance):

**(A) Solution-adaptive mesh refinement (SAMR).** Run a CFD solve, find where
the flow is under-resolved, and remesh finer there. Built, working, validated.

**(B) The automatic mesh was far too coarse to be usable.** This is a
long-standing complaint ("questo è un problema che ho dall'inizio dello sviluppo
della GUI"). He builds 9M-cell meshes in a commercial tool with MANUAL
refinement; this app's auto mode was giving 750k–2.2M and he suspected that was
too few. **He was right, and the cause is now found and fixed** — see Part 3.

---

# PART 2 — SAMR: WHAT WAS BUILT AND VERIFIED

## 2.1 Do not confuse it with the three adaptive things already in the repo

- **`gmsh_wrapper._configure_adaptive_sizing()`** — refines from CAD *geometry*
  (curvature, small curves, gaps) before any mesh/solve exists. The SAMR size
  field is MIN-combined with it so geometry refinement is never coarsened away.
- **`commercial/adaptive_loop.py`** (2,681 lines) + `adaptive_integration.py`,
  `adaptive_cli.py`, `gui/ooda_panel.py` — the OODA engine. Observes **mesh
  quality** (skewness, non-orthogonality). Every "gradient" in it is sizing-field
  grading, NOT a flow gradient. Untouched.
- **`commercial/amr.py`** (444 lines) — **the original brief didn't mention this
  and it matters.** It genuinely does gradient refinement via `postProcess
  grad(U)` → `topoSet fieldToCell` → `refineMesh`. NOT used because: it never
  runs a solver (needs fields to already exist at time 0); `refineMesh
  useHexTopology` is the hex family the design doc rules out for tet/poly; and it
  cannot coarsen and leaves hanging nodes. It is currently dead code as far as
  the GUI goes. **Don't delete it without checking `adaptive_cli.py`.**

## 2.2 The new module: `core/solution_adaptive.py`

Loop: mesh → solve (`simpleFoam`) → read fields (`foamToVTK` + PyVista) →
indicator → target size field → **remesh from the ORIGINAL CAD** → repeat.
Remeshing (not in-place cell splitting) is the deliberate choice: it gives a
clean conforming tet mesh, no hanging nodes, and size can move both ways.

**The indicator, and the reasoning (he will ask):**
```
eta = h * ||grad U||_F / U_ref        h = V^(1/3)
```
It's the Taylor remainder of the discretisation. A FV scheme reconstructs the
solution across a cell from cell-centred values; the leading term it cannot
represent scales with how much the solution changes over one cell, `h*|grad U|`.
Normalising by a reference speed makes it the **dimensionless fraction of the
flow's own velocity scale being lost inside one cell**. eta=0.5 → the cell
resolves nothing; eta=0.01 → locally linear, refining buys little. It peaks
where the physics is: contractions, shear layers, separation, wakes.

Refinement targets **equidistribution**: eta is linear in h, so
`h_new = h*(eta_t/eta)`. Cells below eta_t are untouched — the field NEVER asks
to coarsen, which is what makes MIN-combining with geometry sizing correct.

`U_ref` = **99th percentile** of |U|, not the max (on a real case the max is a
spurious BC-corner cell that would deflate the whole field).

**Key API:**
```python
AdaptiveParams(inlet_velocity, solver_iterations=400, final_solver_iterations=1500,
               refine_quantile=0.85, max_refine_ratio=3.0, min_cell_size=None,
               grid_resolution=64, grid_growth_rate=1.4, max_cycles=3,
               max_cells=3_000_000, qoi_tolerance=0.02, ...)
compute_indicator(grid, velocity_field="U", u_ref=None) -> dict
target_size_field(indicator, refine_quantile, max_refine_ratio, min_cell_size, max_cells)
write_structured_size_field(path, centres, h_target, bounds, resolution, growth_rate, baseline)
sample_structured_field(lattice, origin, spacing, points)
predict_cells_from_lattice(lattice, origin, spacing, centres, volumes, calibration, h_current)
run_solver(case_dir, of_config, application, timeout_s, on_line) -> dict
load_solution(case_dir, of_config, timeout_s, on_line) -> pyvista grid
pressure_drop_qoi(grid, case_dir) -> float
SolutionAdaptiveRefiner(params, of_config, on_line).run(initial_case_dir, bounds,
                                                        remesh_fn, work_dir)
```
`remesh_fn(size_field: Path, cycle: int) -> (case_dir, n_cells)` is a
**caller-supplied callback** — the main extension seam. It must regenerate from
the ORIGINAL CAD via `generate_volume_mesh(size_field_file=...)`.

## 2.3 The GMSH seam

- `gmsh_wrapper.apply_solution_size_field(gmsh, file, existing_bg_field)` installs
  a `Structured` background field, **MIN-combined** with the geometry field
  (never `setAsBackgroundMesh` alone — that would discard geometry sizing).
- `generate_volume_mesh(..., size_field_file=...)`.
- `gmsh_wrapper.__main__` reads env var **`GMSH_SOLUTION_SIZE_FIELD`**.
- `openfoam_runner.GmshVolumeWorker(size_field_file=...)` sets that env var.

**Two empirically-verified GMSH facts the design depends on** (probed live
against GMSH 4.15.2 — do not re-derive):
1. The `Structured` field text format works and its index order is
   `(i*n1 + j)*n2 + k` = plain C order. A lattice fine in half a box gave a
   **117:1** node ratio.
2. **`Mesh.CharacteristicLengthMin` is a HARD FLOOR applied AFTER the background
   field.** Floor at 0 → 27,263 nodes; floor at the coarse value → 443 nodes.
   `apply_solution_size_field` lowers it for exactly this reason. **If refinement
   ever "runs fine but changes nothing", check this first.**

## 2.4 VERIFIED RESULTS — venturi

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

- Physics correct: |U|max = 17.36 m/s at x=0.2408 m (the throat). Inlet 1.0 m/s,
  area ratio (50/15)^2 = 11.1 → continuity predicts ~11 m/s.
- **54 s for the whole cycle** (solve 37 s, remesh 16 s).
- Cell-count prediction error **1.1%**.

## 2.5 Five real bugs found by RUNNING it (all fixed) — the actual substance

1. **The case template could not produce a runnable case at all.** No `0/nut`
   (simpleFoam+kOmegaSST won't start), no wall functions, zero inlet velocity,
   `fixedValue 0` omega at outlets. Nobody had noticed because **nothing in this
   app had ever run a solver** — `main_window` logs "Setup complete" and stops.
2. **`writeInterval` > `endTime` ⇒ NO time directory written.** 300 iterations
   ran 38 s and wrote nothing. Hides on exactly the short intermediate solves the
   loop needs, because a *converged* run escapes via `writeAndEnd()`.
   `setup_case` now clamps `writeInterval` to `endTime`.
3. **The size field scattered EVERY cell, not just refined ones** (a flaw in the
   original SAMR design). The initial mesh already has 0.77 mm near-wall cells vs
   a 7.5 mm lattice spacing, so nearly every lattice node inherited a
   sub-millimetre size and GMSH refined the whole domain. Symptom: straight
   section growing 9.58x vs throat 12.90x — nearly uniform refinement that "ran
   fine". Fix: only cells with `h_target < h_current` enter the lattice, coarse
   baseline elsewhere; GMSH's MIN with geometry sizing restores the near-wall
   mesh by itself.
4. **Cell-count prediction was 16x wrong ⇒ the budget guard was dead.** Predicted
   116,598, got 1,822,916. Causes: the per-cell wish list isn't what GMSH sees
   (the lattice smears), and a **unit conflation** — `h = V^(1/3)` is a
   VOLUME-EQUIVALENT length, a GMSH size field value is a CHARACTERISTIC EDGE
   length; a tet of edge `a` has volume `a^3/(6*sqrt2)`, so 1.82x in length and
   8.49x in count. Fix: predict by sampling the real lattice at real cell
   centres, min-combined with current sizes, times a calibration constant the
   refiner **recalibrates after every remesh**. Error went 0.064x → 1.001x.

5. **The calibration was applied to the geometry-governed part too, putting an
   unreachable floor under every prediction.** Found on the multi-cycle run:
   budget 2.5M, predicted 13.7M, produced **10.3M** — a 4x overshoot, with the
   loop exhausting its relaxation attempts and proceeding anyway every time.
   Cause: `h = V**(1/3)` makes `sum(V/h**3)` IDENTICALLY the current cell count,
   so where geometry sizing wins the MIN the contribution is already exact.
   Multiplying it by the tet calibration (~5) invented ~5x more cells than exist
   and floored every prediction at `n_cells * calibration` — 1.27M * 5.68 =
   **7.19M**, so a 2.5M budget could never be reached no matter how far the
   target size relaxed. Fix: `_prediction_components()` splits the prediction
   into lattice-governed and geometry-governed halves and applies the
   calibration ONLY to the former (it encodes how GMSH reads a field value as an
   EDGE length; the geometry half needs no conversion). Recalibration now solves
   `actual = cal*lat_sum + geom_sum` for `cal`.
   Verified on the real cycle-2 data: old floor 7,187,404 (unreachable); new
   model converges in 2 attempts to 2,335,841 <= 2,500,000.

**Also established (don't redo):** raising the refine *quantile* is a useless
budget knob — 0.85 → 0.9953 moved the prediction only 1.82M → 1.23M, because
dropping marginal cells barely shrinks the refined *volume*. Scaling the target
*size* works analytically: count goes as h^-3, so `s = (predicted/budget)^(1/3)`
lands in ~2 steps, and it preserves WHERE refinement goes.

---

# PART 3 — THE BIG ONE: AUTOMATIC MESH RESOLUTION WAS UNUSABLE

**This is the user's longest-standing complaint and it is now diagnosed.**

## 3.1 The bug

In the automatic path (no explicit "Max cells target"), the bulk cell size was:
```
coarse_max = max_extent * 0.02 * max_mult
```
i.e. a fixed fraction of the **largest bounding-box dimension**. On an elongated
part that is the LENGTH, which has nothing to do with the passage the flow goes
through.

For the valve `Parte4.stp` (3.0 x 0.255 x 0.255 m) at "medium":
`3.0 * 0.02 * 1.5 = 9 cm` bulk cells against a **25.5 cm passage** — fewer than
**3 cells across the bore**. Useless for CFD.

**Measured consequence — bulk cell counts on the valve:**

| detail | OLD size | NEW size | across | OLD bulk cells | NEW bulk cells |
|---|---|---|---|---|---|
| coarse | 12.00 cm | 1.96 cm | 13 | 750 | 171,798 |
| medium | 9.00 cm | 1.28 cm | 20 | **1,779** | 625,574 |
| fine | 6.00 cm | 0.80 cm | 32 | 6,003 | 2,562,352 |
| very_fine | 4.50 cm | 0.53 cm | 48 | 14,229 | **8,647,939** |

The bulk was producing **~1,800 cells**. The 750k–2.2M he observed came almost
entirely from curvature/small-feature refinement near surfaces — a mesh that is
fine on the walls and **resolves nothing in the passage**. Note `very_fine` now
lands at 8.6M, essentially matching his 9M hand-built commercial mesh.

## 3.2 The fix (already applied, `core/gmsh_wrapper.py`)

- Added **`cells_across`** to `_GMSH_DETAIL`: very_coarse 8, coarse 13, medium 20,
  fine 32, very_fine 48. Rules of thumb: <10 across a passage cannot resolve a
  developing profile; ~20 is a usable RANS bulk; 30–50 is production.
- In `_configure_adaptive_sizing`, size the bulk off the **CROSS-SECTION**
  (`cross_scale`, the bbox MEDIAN dimension):
  `coarse_max_detail = min(extent_based, cross_scale / cells_across)`.
  Taking the min means it can only ever improve resolution, never coarsen a
  geometry the old rule handled well (on a cubic domain the two agree).
- **`_hardware_budget`**: bytes/cell 2500 → 1000 (a tet mesh is ~20 B/cell in
  GMSH's own storage; the downstream stages run SEQUENTIALLY, not at once, which
  the old comment wrongly assumed), fraction 0.4 → 0.5, and a new floor against
  **total** RAM (`_total_ram_bytes()`, added) so a merely-busy machine doesn't
  silently mesh coarser. On the user's 33 GB machine: budget **2.5M → 8.35M**.

## 3.3 Verification done

- Adaptive mesh on the venturi STEP at "medium": **374,501 elements in 19.5 s**,
  no stall. (The code comments warned about past stalls when the mesh got finer —
  this did not reproduce.)
- `tests/test_solution_adaptive.py` — 13 tests, pass in ~6 s.
- 3 failures in `tests/test_sizing_resolves_features.py` are **PRE-EXISTING** —
  proven by `git stash`-ing the gmsh_wrapper changes and reproducing them. They
  concern `analyze_local_thickness`/`suggest_cell_sizes` in `geometry.py`, which
  was NOT touched. **Someone should fix them, but they are not from this work.**

## 3.4 WHAT STILL NEEDS DOING ON THIS — IMPORTANT

**The `cells_across` change is verified on the venturi but NOT yet on the real
valve.** This is the highest-priority remaining item:

1. Mesh `C:\Users\Davide Valoroso\Desktop\Report\Parte4.stp` at medium and fine
   in the adaptive path (`max_cell_size=0`), and report actual cell count, wall
   time, and peak RAM. Expect ~600k (medium) to ~2.5M (fine) bulk plus surface
   refinement. **If GMSH stalls or blows RAM, the fix is `cells_across`, not
   reverting the concept** — lower the medium value, or clamp
   `coarse_max_cross` against `hw_floor`.
2. Run `checkMesh` on the result. A much finer bulk changes the
   quality/non-orthogonality picture and may interact with the boundary-layer
   settings and with `poly_dual_risk` (89 surfaces → already flagged risky).
3. Sanity-check the elongated-geometry edge cases: a long thin pipe, and a nearly
   cubic block, to confirm the `min()` really is a no-op on the latter.
4. Consider whether the GUI should SHOW the implied resolution ("20 cells across
   the passage, ~625k bulk cells") before meshing. He would almost certainly
   rather see that number than a detail-level word.

---

# PART 4 — EVERYTHING ELSE REMAINING, PRIORITISED

### 4.1 Valve part validation for SAMR (acceptance criterion #5)
Run the adaptive loop on `Parte4.stp` and **report wall-clock per cycle
honestly**. Measured scaling so far: 76,659 cells → 300 iters in 38 s
(0.127 s/iter); 1,265,388 cells → 250 iters took several minutes. A multi-cycle
solve+remesh at 2M+ cells may be impractical. **If it is too slow, say so
explicitly and propose what to cut** (coarser first-pass mesh for the initial
indicator — the module supports this, just mesh coarse and let the loop refine;
fewer cycles; cheaper indicator). Do not ship a technically-correct 20-minute
loop without flagging it.

### 4.2 The main-window button (last piece of GUI wiring)
Already done: `openfoam_runner.SolutionAdaptiveWorker` (appended at end of file)
with `log_line`/`finished`/`failed`/`cycle_done` signals and a `cancel()` slot;
`GmshVolumeWorker(size_field_file=...)`.
Still to do: the button in `main_window.py` + a `remesh_fn` reusing the window's
existing GMSH-volume path. **Coordinate with the other agent first.**
One button, no manual zone drawing (that defeats the purpose).

Logging is already complete engine-side — every stage emits a tagged line
(`[cycle N]`, `[solve]`, `[indicator]`, `[adaptive]`): solve start with cell
count, iteration progress every 25 steps with residuals, solve finish with
convergence status, indicator stats + peak location, cells marked + target
sizing, lattice shape + predicted cells, budget relaxation, remesh before/after,
recalibration, QoI + % change, stop reason. **Lorenzo has repeatedly insisted
"tutto deve essere esplicito nel log"** — connect `log_line` to the visible panel,
not just a Python logger. See `gui/log_tags.py` for colourising.

### 4.3 Verify the short-solve hypothesis (still UNMEASURED)
`solver_iterations=400` for intermediate cycles rests on "the indicator only
needs the spatial structure of grad(U), which is established long before
residuals bottom out". **This is a hypothesis, not a measurement.** Run the same
mesh to 200 / 400 / full convergence, compute the indicator each time, and report
the correlation of the normalised eta fields. If 400-iter correlates >0.95 with
converged, say so with the number. If not, raise the default and say that.

### 4.4 RERUN the multi-cycle demo — the last run predates the bug-5 fix
`--max-cells 2500000 --cycles 3` completed but with the broken budget model
(log: `C:\cfmesh_work\venturi_run6.log`). Results, 2 cycles:

| metric | before | after | growth |
|---|---|---|---|
| throat window | 3,284 | 3,797,959 | 1156.50x |
| straight window | 3,827 | 11,097 | **2.90x** |
| throat/straight ratio | 0.86 | **342.25** | |
| total | 76,659 | 10,322,312 | (budget was 2.5M!) |

The **targeting is excellent** — the straight section stayed at 2.90x across two
cycles while the throat went 1156x. The only failure was the budget overshoot,
now fixed. **Rerun this** and confirm it lands under budget.

QoI behaviour observed: pressure drop 32.669 -> 34.303 (**+5.00%**), above the 2%
tolerance so the loop correctly did NOT stop — physically right, since resolving
the throat raised peak velocity 12.66 -> 14.38 m/s and hence the loss. **Nobody
has yet seen the QoI criterion actually FIRE** (i.e. a cycle where it converges
and stops); that needs a run with enough budget headroom to reach cycle 3.

Runtime data so far: 76,659 cells → 300 iters in 38 s (0.127 s/iter);
1,265,388 cells → 250 iters in ~10 min; remesh to 10.3M took 137 s.

### 4.5 Tests + commit
`tests/test_solution_adaptive.py` exists (13 tests, no OpenFOAM needed). Missing:
tests for `infer_patch_roles`, the `writeInterval` clamp, and the new
`cells_across` sizing. **Nothing is committed** — commit in logical chunks:
(1) gmsh_wrapper size-field seam, (2) case_setup runnability, (3) solution_adaptive
engine + tests, (4) auto-resolution fix, (5) validation harness.

---

# PART 5 — TRAPS. DO NOT REDO THESE.

- **Don't** `setAsBackgroundMesh()` the solution field directly — it replaces the
  geometry field. Always MIN-combine.
- **Don't** forget `Mesh.CharacteristicLengthMin` — silent no-op (§2.3).
- **Don't** use `repr()` on numpy scalars when writing the size field: under
  numpy 2, `repr(np.float64(0.01))` is `'np.float64(0.01)'` and GMSH cannot parse
  it. Use `repr(float(v))`. There is a test for this.
- **Don't** reach for `dynamicRefineFvMesh` or `refineMesh` — ruled out (§2.1).
- **Don't** hand-roll VTU/field parsing; PyVista is a dependency, proven at ~2M cells.
- **Don't** replace the volume-weighted percentiles in `_weighted_stats` with
  plain ones. Plain percentiles let a corner of tiny cells drag the threshold to
  wherever the mesh is already finest — the feedback loop that makes naive AMR
  refine the same spot forever.
- **Don't** scatter unrefined cells into the lattice (§2.5 item 3).
- **Don't** validate only on the valve — the venturi is the case where the right
  answer is known by hand.
- `_weighted_stats` smuggles a closure under key `"_quantile_fn"` in a dict
  otherwise typed `float`. Ugly, works, `target_size_field` depends on it.
- `compute_indicator` promotes cell→point data to take the gradient (VTK
  differentiates over shape functions, needing nodal values) and demotes after.
  That round trip costs some smoothing — the place to look if the indicator ever
  looks over-diffused on a coarse mesh.
- **Patch names from GMSH are `surface_N`.** `case_setup.infer_patch_roles()` +
  `set_wall_patch_types()` classify them geometrically. `gmshToFoam` types every
  patch `patch`, and wall-function BCs abort at startup unless walls are typed
  `wall` in the mesh boundary — hence `set_wall_patch_types`.

---

# PART 6 — VERIFICATION COMMANDS

```bash
PY="/c/Users/Davide Valoroso/AppData/Local/Programs/Python/Python311/python.exe"

# deps
"$PY" -c "import gmsh,pyvista,numpy,cadquery;print('ok')"

# OpenFOAM toolchain
wsl -e bash -lc 'source /usr/lib/openfoam/openfoam2512/etc/bashrc; which simpleFoam foamToVTK checkMesh'

# fast unit tests (no OpenFOAM, ~6 s)
"$PY" -m pytest tests/test_solution_adaptive.py -q

# full venturi acceptance run (~1 min)
"$PY" -u tools/venturi_amr_validation.py --cycles 3 --mesh-size 10 \
    --solve-iters 300 --final-iters 600 --max-cells 700000

# geometry/mesh only, no solve
"$PY" -u tools/venturi_amr_validation.py --no-solve

# adaptive sizing straight from the CLI (max_cell_size=0 => adaptive path)
"$PY" -m cfmesh_autogui.core.gmsh_wrapper volume <STEP> <OUT.msh> medium 0 0 1.2 0 0 0
```

**Report style he wants:** real numbers, before/after, and an honest statement
when something is too slow or didn't work. He spotted the coarse-mesh problem
himself from experience; he will spot a hand-wave too.
