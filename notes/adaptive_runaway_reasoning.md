# Adaptive GMSH 2D runaway — Phase A (diagnosis + measurement)

Date: 2026-10-10. Scope: **diagnosis and measurement only** — no production code
change. Repro on `benchmarks/geometries/venturi.step` via the app's own GMSH
volume CLI (the exact `gmsh_wrapper.py volume --flags` invocation the GUI's
`GmshVolumeWorker` / `run_gmsh_volume` use), adaptive sizing
(`--max-cell=0 --min-cell=0 --max-cells-target=200000 --detail=medium`).

All logs live **outside the repo** in `C:\cfmesh_bench\adaptA\` (raw,
per-line timestamped). Temporary probe scripts lived under
`.superpowers/sdd/2026-10-05-poly-beat-snappy-program/` and were deleted after
this report was written; the logs are the durable evidence.

## TL;DR

* The runaway **reproduces deterministically** in ≤6 s and then never
  terminates. It is a 2D **MeshAdapt** loop on the two **Cone** surfaces
  (surface 3 and surface 4) of the venturi; the invalid-element count grows
  without bound (2 → … → 6524) and no `.msh` is ever written.
* The no-output watchdog is **structurally unable to fire**: output keeps
  arriving, and the largest silent gap measured over 8 minutes was **251.3 s**,
  just under the 300 s base threshold.
* The robust retry (`POLYFOAMMESH_GMSH_ROBUST=1`) **converges in 6.4 s** and
  produces a valid `.msh` (`$Nodes`/`$Elements` present) with a clean
  `checkMesh`.
* Robust **+ `Mesh.Optimize`/`OptimizeNetgen` = 1** also converges (6.4 s) and
  **improves** max skewness (3.34 → 1.22) and max non-orthogonality
  (49.0 → 46.6). Strong Phase C candidate.
* The robust **size-field sanity guard never engages** on this geometry
  (`min_size` = 1.2572e-5 m vs floor = 1.5e-10 m), so it costs **zero** here;
  disabling it or loosening it changes nothing (all three produce the same
  converged mesh up to GMSH run-to-run variance).
* Root-scale finding: `venturi.step` declares **millimetres**, so GMSH +
  `Geometry.OCCTargetUnit=M` sees a **0.15 mm** part (`max_extent=1.502e-4 m`).
  The `medium` detail absolute floor `df["min_size"]=4e-4 m` is **larger than
  the whole part**, which forces `coarse_max=4e-4`; the size field is
  `SizeMin=1.2572e-5` vs `SizeMax=4e-4` on a 1.5e-4 m domain — a pathological
  ratio that MeshAdapt cannot reconcile on the cones.

## 1. The runaway — signature (Task 1)

Command (verbatim, `C:\cfmesh_bench\adaptA\repro_adaptive_8min.log`):

```
python src/polyfoammesh/core/gmsh_wrapper.py volume
  --step=...\benchmarks\geometries\venturi.step
  --msh=...\repro_adaptive_8min.msh
  --detail=medium --n-layers=0 --bl-thickness=0 --bl-expansion=1.2
  --max-cell=0 --min-cell=0 --max-cells-target=200000
  --surface-resolution=medium --bl-wall-size-floor=0
```

Bounded at 480 s (killed). Result: `rc=1 timed_out=True elapsed=480.3s`, **no
`.msh` written**. Exact verbatim GMSH strings (spacing preserved):

```
Warning : 2 elements remain invalid in surface 3
Info    : [ 20%] Serializing surface 3 and refining all its bounding edges
Info    : [ 20%] Meshing surface 3 (Cone, MeshAdapt)
Info    : [ 20%] 8-| Splitting those edges and trying again
Info    : [ 20%] :-) All edges recovered after 1 iteration
Info    : [ 40%] Meshing surface 4 (Cone, MeshAdapt)
```

Later, with the same wording but a growing count:

```
Warning : 364 elements remain invalid in surface 4
Warning : 2610 elements remain invalid in surface 3
Warning : 5244 elements remain invalid in surface 4
Warning : 6524 elements remain invalid in surface 3
```

**Affected surfaces:** `surface 3` and `surface 4` — both reported as
`(Cone, MeshAdapt)`. The other four surfaces (Cylinder/Plane) mesh fine. This is
exactly the class the robust path was written for ("MeshAdapt stalls on this CAD
class").

**Invalid-count progression over time** (warning count, cumulative max), from
the 8-minute run:

```
[  1.61s] 2        [  4.36s] 14      [458.64s] 6524   (16 warnings / 15 splits)
progression: 2, 2, 2, 6, 4, 2, 2, 4, 2, 14, 9, 2, 364, 2610, 5244, 6524
```

The count is small and noisy for ~30 s, then takes off and never comes back
down: **364 → 2610 → 5244 → 6524**.

**"Splitting those edges" rounds:** **15** in 480 s (first @1.61 s, last
@458.84 s).

**Cadence (important for signature detection):**

| metric | min | avg | max |
|---|---|---|---|
| interval between `remain invalid` warnings | 0.130 s | 30.469 s | **259.660 s** |
| largest gap between *any* two output lines | — | — | **251.30 s** |

The largest silent gap (251.3 s, between `:-) All edges recovered` @207.34 s and
`6524 elements remain invalid` @458.64 s) is **below the 300 s base no-output
watchdog**, and the invalid/split messages themselves arrive at intervals well
under it. This is why the watchdog never fires on this stall: it is not silent
enough. (`GMSH_STALL_TIMEOUT_S = 300`, size-scaled only for ≥500k cells; here
`est_cells=200000` → budget stays 300 s.)

## 2. Robust retry (Task 2)

Same command + `POLYFOAMMESH_GMSH_ROBUST=1` (`robust_200k.log`).

**Exact robust banner lines:**

```
[gmsh] robust mode ON (POLYFOAMMESH_GMSH_ROBUST=1): OCC healing + Delaunay 2D + no optimizer
[gmsh] robust[step]: healShapes -> 61 entities
[gmsh] robust: volume algorithm -> Delaunay 3D (1) for STEP/OCC (HXT boundary recovery was not dual-able on this class)
[gmsh] robust: surface algorithm -> Delaunay (5) instead of MeshAdapt (stalls on this CAD class)
[gmsh] robust: Delaunay (5) pinned explicitly on 6 2D surface(s); no per-surface fallback
[gmsh] Mesh.Optimize/OptimizeNetgen DISABLED (robust mode)
```

**Converges:** yes. **Wall time: 6.4 s** (vs ≥480 s and no output for the
default path). **Valid `.msh`:** yes — `robust_200k.msh` (729,046 B) contains
`$Nodes … $EndNodes` and `$Elements … $EndElements`; the CLI returned
`{"success": true, …}`.

**Pipeline continuation** (`gmshToFoam` → `TetPolyDualConverter` dual-only →
real WSL `checkMesh`), `pipeline_robust_200k.log`:

| stage | wall |
|---|---|
| GMSH (robust) | 6.4 s |
| gmshToFoam | 4.6 s |
| dual (12,916 tets → 2,941 poly cells, defects 8 pyramid) | 2.9 s |
| real checkMesh | 1.0 s |

Real `checkMesh` numbers:

| metric | value |
|---|---|
| cells | 2,941 |
| points | 16,482 |
| Max non-orthogonality | 49.03 |
| Average non-orthogonality | 11.33 |
| Max skewness | 3.34 |
| negative volume cells | 0 |
| pyramids | 0 |
| result | **Mesh OK** |

## 3. Optimizer + guard experiments (Task 3, probe copy only)

Probe copy `gmsh_wrapper_probe.py` (a copy of the current file with two
controlled hooks: `POLYFOAMMESH_PROBE_ROBUST_OPTIMIZE=1` re-enables optimizers
under robust; `POLYFOAMMESH_PROBE_GUARD=on|off|loose` varies the size-field
guard). **No change was made to `src/`.**

### 3a. Robust + `Mesh.Optimize=1` + `Mesh.OptimizeNetgen=1`

**Converges: yes, 6.4 s.** The GMSH log shows the optimizers actually running:
`Optimizing mesh...` → `No ill-shaped tets in the mesh :-)` (local) and
`Optimizing mesh (Netgen)...` → `326 → 132 → 31 → 0 illegal tets` →
`Volume Optimization` (0.32 s). **Zero** invalid-element warnings / split
rounds (the flat 2D mesh was good enough that no edge splitting was needed).

`checkMesh` (real):

| metric | robust (opt OFF) | robust (opt ON) |
|---|---|---|
| cells | 2,941 | 2,644 |
| Max non-orthogonality | 49.03 | **46.61** |
| Average non-orthogonality | **11.33** | 13.31 |
| Max skewness | 3.34 | **1.22** |
| negative volume | 0 | 0 |
| pyramids | 0 | 0 |
| result | Mesh OK | **Mesh OK** |

The optimizer costs ~0.3 s and **halves-or-better max skewness**, trading a
small increase in *average* non-orthogonality. No hang.

### 3b. Size-field sanity guard: on / off / loose

The guard (`gmsh_wrapper.py:1409`) clamps `min_size` up to
`max_extent * 1e-6` **only in robust mode** (and only if `min_size` is below
it). The probe copy printed the sizing on every run:

```
[gmsh-probe] sizing: max_extent=0.0001502 coarse_max=0.0004
              geom_min=1.2572e-05 min_size=1.2572e-05
              guard=on     abs_min_floor=1.5e-10
              guard=off    abs_min_floor=0
              guard=loose  abs_min_floor=1.5e-12
```

`min_size` = 1.26e-5 m is **~5 orders of magnitude above** the floor in every
variant → the clamp branch **never executes**. All three converge in ≈6 s and
write a valid `.msh`. SHA-256 of the three outputs differ
(`guard_off` vs `guard_loose` vs `robust_200k`), but that is **GMSH run-to-run
variance**, not the guard: the guard is provably inactive, and the mesh
node/element counts vary slightly between identical runs as well. Conclusion:
**on `venturi.step` the guard costs exactly zero quality and is not needed for
convergence.** (It may still matter on other geometry classes, e.g. the
pathological `Parte2.stp` request it was written for.)

`checkMesh` on `guard_off` (for completeness): 2,779 cells, max NO 43.32, avg
10.72, max skew 1.58, Mesh OK — same band as the other robust runs.

## 4. Fallback baseline — adaptive OFF (Task 4)

Explicit manual sizing, resolution-matched to the adaptive request
(`--max-cell=3e-6 --min-cell=6e-7`, detail medium) → converged in **8.4 s**,
235,038 nodes / 1,414,964 elements → `.msh` 76.9 MB. The healthy signature is
present but small and self-recovering: 3 invalid warnings `[2, 7, 2]`, 3 split
rounds, then `Done meshing 2D` and write.

Pipeline (`pipeline_manual_equiv_200k.log`):

| stage | wall |
|---|---|
| GMSH (manual) | 8.4 s |
| gmshToFoam | 14.7 s |
| dual (1,322,193 tets → 235,038 poly cells, defects 38 pyramid) | 80.9 s |
| real checkMesh | 11.0 s |

Real `checkMesh` numbers:

| metric | value |
|---|---|
| cells | 235,038 |
| points | 1,414,851 |
| Max non-orthogonality | 59.31 |
| Average non-orthogonality | 13.69 |
| Max skewness | **4.64** (1 highly skew face) |
| negative volume cells | 0 |
| pyramids | 0 |
| result | **Failed 1 mesh checks** |

So the quality bar set by the finite, manual, adaptive-OFF path is a valid but
only *marginal* mesh: it is 80× finer than the robust retry's mesh, yet its
**max skewness (4.64) is worse** than the robust retry's (3.34, opt off; 1.22,
opt on). The robust retry is not just faster and finite — it is at least as
clean as the manual fallback.

## 5. Signature for Phase B + healthy counter-evidence (Task 5)

### Comparative table (all per-line timestamped logs)

| run | max invalid | #invalid msgs | #splits | max silent gap | `.msh`? | wall |
|---|---|---|---|---|---|---|
| **runaway** (`repro_adaptive_8min`) | **6524** | 16 | 15 | **251.3 s** | **no** | 480 s (killed) |
| robust | 2 | 2 | 2 | 1.6 s | yes | 6.4 s |
| robust + optimizer | 0 | 0 | 0 | 1.6 s | yes | 6.4 s |
| guard off | 2 | 2 | 2 | 1.6 s | yes | 5.7 s |
| guard loose | 0 | 0 | 0 | 1.6 s | yes | 5.4 s |
| manual equiv (explicit) | 7 | 3 | 3 | 1.8 s | yes | 8.4 s |
| healthy venturi (adaptive, target 0) | 6 | 4 | 4 | 1.6 s | yes | 5.2 s |
| healthy Parte2 (adaptive, target 0) | 0 | 0 | 0 | 129.1 s | yes | 172 s |

### Proposed detection rule

Track, from the streamed GMSH log during the 2D surface phase:

* `m` = the maximum integer parsed from
  `r"(\d+) elements? remain invalid in surface \d+"` seen so far this pass;
* `s` = number of `"Splitting those edges and trying again"` events this pass;
* `t2d` = seconds since the matching `"Meshing 2D..."` with no intervening
  `"Done meshing 2D"`.

**FIRE (kill + robust retry) when EITHER:**

1. **Magnitude gate:** `m >= 100` — any surface with ≥100 simultaneously-invalid
   elements is not recovering. (Healthy observed max = 7 across this corpus; the
   runaway reaches 364/2610/5244/6524.) Margin ≈ 14×.
2. **Growth gate:** `s >= 8` **and** `t2d >= 30 s` **and** the per-event invalid
   counts are non-decreasing over the last ≥3 events. (Runaway: 15 splits,
   counts 364→2610→5244→6524; every healthy run has ≤4 splits.)

Rule 1 alone is the recommended primary: trivial to implement, and the healthy
margin is large. Rule 2 catches a hypothetical slow-growth case where the count
stays low per event but the run never terminates.

**False-positive check against healthy runs (all pass):**

* `healthy_venturi_t0` (same geometry, same strings): 4 invalid msgs (max **6**),
  4 splits, but completes 2D in <5 s → m<100 and s<8 → **no fire**.
* `healthy_parte2_t0` (cone/cylinder): 0 invalid, 0 splits, 172 s → **no fire**.
* `manual_equiv_200k`: max invalid **7**, 3 splits, completes → **no fire**.
* robust / robust+opt / guard variants: max invalid ≤2 → **no fire**.

### Why the existing watchdog is not a substitute

`GMSH_STALL_TIMEOUT_S = 300` fires only on *silence*. The runaway is not silent:
max silent gap 251 s < 300 s (and the invalid/split lines keep arriving). A
content-aware signature is required; a pure no-output watchdog cannot detect
this stall.

## 6. Recommendation for Phases B/C/D

* **Phase B (detection):** implement the magnitude gate (`m >= 100`) plus the
  growth gate (`s >= 8 & non-decreasing & t2d >= 30 s`) as a log-line classifier
  in the GMSH streaming path (`GmshVolumeWorker.run` / `_stream_subprocess`
  consumer). On fire: kill the attempt and retry exactly as the existing
  no-output stall does (`POLYFOAMMESH_GMSH_ROBUST=1`). Keep the 8-minute hard
  ceiling as a backstop. Parsing is cheap (two regexes) and the 14× margin means
  no tuning.
* **Phase C (quality):** the robust retry is the correct recovery, but on this
  geometry it converges to a mesh **~80× coarser than requested** because the
  `medium` absolute floor `df["min_size"]=4e-4 m` exceeds the (mm-declared)
  part. Two independent Phase C levers to evaluate:
  1. **Enable the optimizer in robust mode** (`Mesh.Optimize=1`,
     `Mesh.OptimizeNetgen=1`): converges in 6.4 s and improves max skewness
     3.34 → 1.22. Make the robust optimizers opt-in/configurable rather than
     hard-off, or run a short optimize pass after the robust 2D completes.
  2. **Fix the units/detail-floor interaction** so the adaptive target is
     actually honoured (the mm-declared `venturi.step` reads as a 0.15 mm part;
     `df["min_size"]` should not exceed a fraction of `max_extent`). This is the
     real root cause of both the runaway and the coarse robust result, and it
     affects every STEP that declares mm.
* **Phase D (validation):** gate any change on `venturi.step` (must converge and
  write a valid `.msh`), on `healthy_parte2_t0`-style adaptive runs (must still
  produce ~100k nodes with no false runaway kill), and on the manual fallback
  (must match-or-beat max skew ≤ 4.64). Regression: `gmsh_wrapper` output must
  stay bit-identical when the new detection/optimizer flags are off.

## 7. Evidence index (`C:\cfmesh_bench\adaptA\`)

| file | content |
|---|---|
| `repro_adaptive_8min.log` | full timestamped runaway, 480 s, no `.msh` |
| `repro_adaptive_8min.tail200.log` | last 200 lines of the runaway |
| `quick200k.log` | first 150 s confirmation of the runaway |
| `robust_200k.log` + `robust_200k.msh` | robust retry, 6.4 s, valid |
| `robust_opt_200k.log` + `.msh` | robust + optimizer, 6.4 s, skew 1.22 |
| `guard_off_200k.log`, `guard_loose_200k.log` | guard variations |
| `manual_equiv_200k.log` + `.msh` | adaptive-OFF fallback, 8.4 s |
| `pipeline_*.log` | gmshToFoam + dual + real checkMesh per run |
| `healthy_venturi_t0.log`, `healthy_parte2_t0.log` | healthy counter-evidence |
| `guard_off_200k`/`robust_200k`/`manual_equiv_200k` `.msh` | written meshes |
