# CFMesh-AutoGUI — Autonomous Overnight Mission

You are working alone, unattended, for many hours. Nobody will answer questions.
When you hit a fork, pick the option you can **verify**, write down why, and keep going.

---

## 1. Mission

`C:\Users\Davide Valoroso\cfmesh-autogui` is a PySide6 desktop app that generates
OpenFOAM meshes using cfMesh (`cartesianMesh` via WSL2) and GMSH.

Turn it into a **preprocessor that produces genuinely good CFD meshes with minimal
user input**. Two things matter, in this order:

1. **Mesh quality** — the mesh a non-expert gets by pressing one button must pass
   `checkMesh` and be good enough to actually run a solver on.
2. **Automation** — the app decides sizing, refinement, and boundary layers from the
   geometry itself. The user supplies a CAD file and an intent ("internal flow",
   "external aero"), not twenty numbers.

Everything else (UI polish, extra formats, unwired `commercial/` modules) is
secondary. **A pretty app that makes bad meshes is a failure.**

Output must also stay openable in **BaramFlow** (NextFOAM's open-source OpenFOAM
GUI), which reads native OpenFOAM cases.

---

## 2. Environment — read this before your first command

| Fact | Value |
|---|---|
| Project root | `C:\Users\Davide Valoroso\cfmesh-autogui` |
| **Python** | `C:\Users\Davide Valoroso\AppData\Local\Programs\Python\Python311\python.exe` |
| Test command | `<that python> -m pytest tests/ -q` |
| OpenFOAM | v2512 in WSL2 Ubuntu, env at `/usr/lib/openfoam/openfoam2512/etc/bashrc` |
| Platform | Windows 11, PowerShell + Git Bash both available |

**The bare `python` on PATH is the WRONG interpreter** (a venv without pytest/PySide6).
Always use the full path above. This will waste an hour if you forget it.

### WSL is unreliable here — plan around it
WSL2 shuts down on idle and sometimes hangs with no output and near-zero CPU.
- Put a **timeout on every** `wsl.exe` call. No exceptions.
- Health-check before a benchmark batch:
  `wsl.exe -e bash -lc "echo alive"` with a 30s timeout. If it fails,
  `wsl.exe --shutdown`, wait, retry once, then log it and skip WSL work this round.
- `tests/test_e2e_workflow.py::test_full_workflow` and
  `tests/test_openfoam_runner.py::test_mesh_worker` do real WSL runs. They have
  180s watchdogs (already added). They may fail for environment reasons — that is
  **not** a code regression. Deselect them for fast iteration:
  ```
  -m pytest tests/ -q --deselect tests/test_e2e_workflow.py::test_full_workflow --deselect tests/test_openfoam_runner.py::test_mesh_worker
  ```
  Run them (once, with WSL confirmed alive) before you finish for the night.

### STEP 0 — DO THIS FIRST, BEFORE ANY EDIT
**The project is not a git repository.** You are about to make many unsupervised
changes with no undo. Fix that immediately:

```bash
cd "/c/Users/Davide Valoroso/cfmesh-autogui"
git init
git add -A
git commit -m "Baseline before autonomous meshing-quality work"
```

Then **commit after every verified improvement**, with a message saying what changed
and what evidence proved it. If a change makes benchmark quality worse, `git revert`
it — do not leave experiments lying around.

### Verified-available cfMesh tooling (I confirmed these exist in your WSL)
`cartesianMesh` · `surfaceFeatureEdges` · `FMSToSurface` · `surfaceGenerateBoundingBox`
· `preparePar` · `checkMesh` · `foamToVTK`

`foamToCGNS` does **not** exist — CGNS export goes through meshio.

---

## 3. Ground truth — current state (do not re-litigate)

Test suite: **474 passed, 1 skipped** (plus the 2 WSL tests above).
`pyflakes` reports **zero undefined names** across `src/` and `tests/`.

A long debugging session just fixed ~21 real bugs. **Do not "re-fix" these** — they
are done and verified:
- `openfoam_runner.py` `_now()` was undefined (crashed every real mesh run)
- `mesh_converter.py` inverted `startFace` arithmetic
- `boundary_reader.py` non-greedy regex lost every patch when `inGroups` present
- `commercial/exporter.py` had the same regex bug
- `watertight.py` — every pipeline step called unimported names
- `gmsh_wrapper.py` BL field configured but never activated (`setAsBoundaryLayer`)
- `suggest_cell_sizes` returned min > max on compact geometries
- 5-level detail slider only had 3 backend presets
- Viewer: Measure, Select Patch, Section Cut all repaired
- "Volume Mesh" view now shows real internal cells via `foamToVTK` (was boundary-only)
- BaramFlow case-folder export added under File → Export Mesh
- **Patch types**: cfMesh types every patch `wall` unless a `renameBoundary` block
  says otherwise. It was never emitted, so inlets/outlets came out as `wall` —
  geometrically fine, physically unusable (no flow can enter a wall). `meshDict`
  now emits `renameBoundary`; verified `inlet/outlet → type patch` in the output.
- **Boundary layers were collapsing**: cfMesh's `thicknessRatio` is the layer-to-layer
  GROWTH ratio (~1.1–1.3) and the first layer is absolute via `maxFirstLayerThickness`.
  There is **no `expansionRatio` key** — cfMesh ignores it. The code was emitting
  `thicknessRatio 0.005` (the first-layer *fraction*) plus an ignored `expansionRatio`,
  i.e. telling cfMesh each layer should be 0.005× the previous one. Now mapped
  correctly, plus `optimiseLayer`/`untangleLayers`; verified cfMesh logs
  "Starting creating layer cells".
- `main_window.py` auto-fix retry called `write_meshdict(patch_sizes=...)` — not a
  real parameter (`TypeError`), and dropped the user's cell sizes. Fixed.
- **checkMesh parsing was broken against real v2512 output**: non-orthogonality uses
  `Max: ... average: ...` (colon), skewness/aspect carry no average at all, and
  `Min volume = 6.3e-06.` ends in a period. The regexes required `average = ` with
  `re.DOTALL`, so non-ortho/skew/aspect silently parsed as **0** (a perfect mesh,
  always) and min-volume raised `ValueError`. Fixed and pinned by
  `tests/test_checkmesh_parsing.py` against verbatim real output.
- `core/geometry.check_watertight()` added — reusable pre-flight refusal of leaky
  geometry (the benchmark's negative case depends on it).

### Open finding, not yet fixed
A plain sphere tessellates **non-watertight** (`tessellate_patches` on
`cq.Workplane("XY").sphere(0.5)` → 8002 faces, `is_watertight False`). Any closed
curved body will therefore be falsely reported leaky and refused. Worth a proper
fix — it currently forces the `external_aero` benchmark to use a cylindrical body.

### Verified cfMesh reference — real key names
Confirmed against a working third-party meshDict and cfMesh's own behaviour.
**`expansionRatio` is not a cfMesh key.** The boundary-layer keys that exist:

```
boundaryLayers {
  patchBoundaryLayers {
    "wall.*" {
      nLayers 2; optimiseLayer 1; untangleLayers 1;
      thicknessRatio 1.15;              // layer-to-layer GROWTH ratio
      maxFirstLayerThickness 0.0001;    // ABSOLUTE, in metres
    }
  }
  optimisationParameters {
    nSmoothNormals 1; maxNumIterations 5; featureSizeFactor 0.3;
    reCalculateNormals 2; relThicknessTol 0.1;
  }
}
renameBoundary {                        // without this EVERY patch becomes `wall`
  defaultName wall; defaultType wall;
  newPatchNames { "inlet.*" { newName inlet; type patch; } ... }
}
```

**Local prior art worth reading** — there is a full open-source OpenFOAM GUI
already on this machine, inside WSL:
`/home/lorenzoriti/SplashFOAM/` (`Source/Splash.py`, `Meshing/system/meshDict`).
It drives cfMesh for the same purpose. Read it before designing anything new.

### What is genuinely still missing (I verified each of these)
The `meshDict` writer (`core/meshdict_gen.py`) still does **not** use, anywhere:
- **FMS / feature edges** — no `surfaceFeatureEdges`, no `.fms`, no `edgeMeshRefinement`
- `objectRefinements` (box / sphere / cone / line local refinement)
- `surfaceMeshRefinement`
- per-patch boundary-layer control (one regex, same params for every wall)
- `optimisationParameters` (BL smoothing/untangling controls — see block above)
- any threading / multi-core setting

There is **no benchmark suite** and **no objective measurement of mesh quality**.
That is the single biggest gap: right now nobody — including you — can tell whether
a change made meshes better or worse.

**Known-good reference point** (measured after the fixes above, use as your first
baseline): test cylinder, `maxCellSize 0.25 / minCellSize 0.125`, 3 BL layers →
`checkMesh` reports **Mesh OK**, 7694 cells, max non-orthogonality 61.5
(avg 15.0), max skewness 0.66, max aspect ratio 39.6. Case left at
`C:\cfmesh_cases\verify_fix` for comparison.

---

## 4. Definition of "a good mesh" — your objective function

Make this measurable before you optimise anything.

**Hard gates (a mesh that violates any of these is a FAILURE):**
| Metric | Threshold |
|---|---|
| `checkMesh` overall | must report `Mesh OK` |
| Negative-volume cells | 0 |
| Max non-orthogonality | < 70° |
| Max skewness | < 4.0 |
| Face pyramids / concave cells | 0 errors |
| Patches in output | every input patch present, none empty |

**Quality targets (optimise these):**
| Metric | Target |
|---|---|
| Max non-orthogonality | < 65° (ideally < 60°) |
| Average non-orthogonality | < 15° |
| Max aspect ratio | < 100 for internal flow, < 1000 with BL |
| BL coverage on wall patches | > 90% of wall faces get the requested layers |
| Feature edges captured | sharp edges (dihedral > 30°) visible in the mesh, not rounded off |
| Cell count | within ±40% of the pre-run estimate |

**Automation targets:**
- One button ("Quick Mesh") on a clean watertight CAD → mesh passing all hard gates,
  no manual parameter tuning, on **every** benchmark geometry.
- Wall-clock: a ~200k-cell benchmark case should mesh in a few minutes, not an hour.

---

## 5. P0 — The measuring stick now EXISTS — use it

`benchmarks/run_benchmarks.py` works and is the authority on whether a change
helped. Run it before and after every algorithmic change:

```
<python> benchmarks/run_benchmarks.py
```

**Current baseline (measured, 6/6 passing):**

| case | cells | nonOrtho | skew | aspect | note |
|---|---|---|---|---|---|
| pipe | 4328 | 23.4 | 0.54 | 4 | |
| pipe_constriction | 4144 | 41.6 | 1.11 | 4 | throat resolved (min cell 0.03 vs 0.05) |
| box_obstacle | 7776 | 18.5 | 0.66 | 4 | |
| external_aero | 2720 | 15.2 | 0.58 | 3 | |
| thin_gap | 3327032 | 46.5 | 1.07 | 4 | 90 s — see lead 1 |
| non_watertight | — | — | — | — | correctly rejected |

**FIXED since the first baseline — multi-scale sizing was blind.** `pipe` and
`pipe_constriction` used to produce the *identical* 608 cells. Two causes, both
now fixed and pinned by `tests/test_sizing_resolves_features.py`:
`_sample_thickness_one_mesh` took the FARTHEST ray hit (`.max()`) in both
directions, measuring model extent rather than local wall-to-wall distance (every
sample collapsed to one value); and min-cell came from the p5/p10 percentile,
which by construction cannot see a feature covering ~3% of the surface area. Now
the nearest hit is used and min-cell comes from p1, with higher sample counts.
Result: 608 → 12888 cells on the constricted case, quality still within gates.

**Remaining leads:**

1. **`thin_gap` costs 3.3M cells / 90 s — this is the case for local refinement.**
   The geometry has been fixed (it was a solid slab despite its name; it is now a
   real 1 mm passage between two plates cut out of a 0.1 m domain) and both sizing
   functions now separate bulk scale (p50) from thinnest feature (p1). What is
   left is architectural: cfMesh's octree is **isotropic**, and `patchCellSize`
   applies to a whole patch, so "refine only inside the gap" cannot currently be
   expressed — on a single-patch geometry any local requirement becomes global.
   3.3M may well be the honest cost of resolving a 1 mm gap in a 0.1 m domain this
   way. **The real fix is `objectRefinements`** (§7.4): detect *where* the thin
   region is and emit a refinement box around it. That needs
   `analyze_local_thickness` to keep sample POSITIONS, which it currently throws
   away — it returns percentiles only. Start there.
2. The constricted case's non-orthogonality rose 16 → 42 as it got refined. Still
   well inside the gates, but watch it when refining further.
3. ~~No pre-run guard on cell count~~ — done. `MainWindow._confirm_large_mesh`
   asks before starting above 2M cells or 5 min estimated, and names the cell
   sizes driving the cost. Pinned by `tests/test_large_mesh_guard.py`.

Bugs already fixed in the harness (do not reintroduce): case dirs were created
under `tempfile.mkdtemp()`, i.e. under `C:\Users\Davide Valoroso\...`; the space
broke the WSL command (`cd: too many arguments`) so **all 6 cases failed in
~0.3s**. Work dirs now live under `C:/cfmesh_bench` and paths are `shlex.quote`d.
`checkMesh` also needs `system/fvSchemes` + `fvSolution` or it exits 1 and every
case reports 0 cells. And `success` came from `MeshQualityReport.passed`, which
ignores non-orthogonality/skewness — the harness reported PASS on a mesh
violating a hard gate until the gates were made decisive.

### Still to build here
- `compare.py` exists but has not been exercised against two real runs — verify it.
- No local VTK-based quality path yet (fast iteration without WSL).

Create `benchmarks/` with:

1. **Geometry set** (generate with cadquery/gmsh — check them in, they must be
   reproducible). Start from `sample_cad/cylinder_test.stl` and add cases that
   stress different failure modes:
   - simple pipe (baseline, must be perfect)
   - pipe with a sharp constriction (multi-scale sizing)
   - box with a sharp-edged internal obstacle (feature-edge capture)
   - external-aero style: body inside a farfield box (BL + large size ratio)
   - thin-walled / small-gap geometry (proximity refinement)
   - a deliberately non-watertight part (the app must **refuse clearly**, not produce garbage)

2. **`benchmarks/run_benchmarks.py`** — for each geometry: run the app's real
   meshing pipeline headlessly (import the core modules, don't drive the GUI),
   run `checkMesh`, parse metrics, write `benchmarks/results/<timestamp>.json`.
   Must be resilient: per-case timeout, WSL health check, continue on failure.

3. **`benchmarks/compare.py`** — diff two result files, print a table of
   metric deltas, and exit non-zero on regression of any hard gate.

4. **Fast local quality check without WSL**: VTK exposes `vtkMeshQuality`
   (via `pyvista`) which computes skewness / aspect ratio / non-orthogonality-like
   metrics directly on the `foamToVTK` output. Use it for tight iteration loops;
   use real `checkMesh` for the authoritative numbers.

**Record a baseline immediately** and commit it. Every later claim of "better" must
cite a benchmark diff against it.

---

## 6. Research phase — read real sources, don't guess

Everything in §7 is a **hypothesis I did not verify**. Confirm each against primary
sources before implementing. Budget real time for this; it will save more than it costs.

**Highest-value source — the cfMesh source itself.** It is the only complete
specification of what `meshDict` accepts:
```bash
wsl.exe -e bash -lc "find / -path /proc -prune -o -iname '*.C' -path '*cfmesh*' -print 2>/dev/null | head -50"
wsl.exe -e bash -lc "ls \$FOAM_TUTORIALS/mesh/cfMesh"   # working meshDict examples
```
Read the dictionary-parsing code and the tutorials. Extract the **complete** list of
supported keys and their exact semantics. Write what you learn to
`docs/cfmesh_meshdict_reference.md` so it is not lost.

**Other sources worth studying** (use WebSearch/WebFetch):
- **BaramMesh / BaramFlow** (NextFOAM, open source) — the most directly relevant
  prior art: an OpenFOAM GUI mesher. Study how they structure auto-sizing, patch
  handling, and their meshing workflow. Also confirms exactly what case layout
  BaramFlow expects to open.
- **snappyHexMeshDict template** (`$FOAM_ETC/caseDicts/mesh/generation/`) — even
  though you use cfMesh, its concepts are the industry vocabulary: feature-edge
  refinement, refinement surfaces/regions with level ranges, layer controls,
  `relativeSizes`, quality controls.
- **Gmsh size fields** — `Distance` + `Threshold` + `Min` field composition is the
  textbook way to build a sizing field; `MeshSizeFromCurvature`; `BoundaryLayer` field.
- **Netgen / TetGen** — curvature-and-proximity-driven sizing literature.
- Commercial workflow references for *concepts only* (Fluent Watertight Geometry
  Workflow, ANSA, Pointwise, Star-CCM+ Automated Mesh): what questions they ask the
  user, in what order, and which decisions they make automatically. Do not copy any
  proprietary code or assets.

---

## 7. P1 — Algorithmic work queue (ordered by expected mesh-quality gain)

Verify each hypothesis first; measure every change against the benchmark.

### 7.1 Feature-edge capture via FMS — likely the single biggest win
`surfaceFeatureEdges` exists in your WSL and is unused. Sharp edges are currently
rounded off by the octree, which is the classic "cfMesh mesh looks melted" problem.
- Pipeline: STL → `surfaceFeatureEdges -angle <θ>` → `.fms` → use the `.fms` as
  `surfaceFile` in meshDict.
- Verify: does cfMesh honour feature edges automatically from an `.fms`, or does it
  also need `edgeMeshRefinement` / a separate `.emesh`? **Read the source.**
- The app already has `core/feature_detector.py` computing sharp edges — reconcile
  the two rather than duplicating.
- Measure: sharp-obstacle benchmark, before/after edge fidelity + checkMesh.

### 7.2 Octree-aware cell sizing
cfMesh is octree-based: refinement levels are almost certainly powers of two of
`maxCellSize`. If so, a `minCellSize` that is not `maxCellSize / 2^k` is silently
rounded — meaning much of the current sizing precision is illusory.
- **Verify this against the source**, then snap suggested sizes to the real grid and
  tell the user the effective value.
- Also cap `maxCellSize / minCellSize`: very large ratios cause slow meshing and bad
  transitions. Find the practical limit empirically with the benchmark.

### 7.3 Boundary layers that a solver can actually use

**The y+ physics already exists and is now correct** — `commercial/bl_engine.py`
(`BLEngine.calculate_from_flow`). Two bugs in it were fixed and pinned by
`tests/test_bl_physics.py`: `FlowConditions.reynolds_number` is a plain field
defaulting to 1e6, so passing velocity/length silently kept the wrong Re (use the
new `FlowConditions.from_velocity()`); and the solver pinned `n_layers=10` and
solved for the growth rate, letting it reach 2.0 — each layer nearly doubling.
The rate is now fixed in [1.05, 1.5] and the layer count is derived. Verified
behaviour: y+=1 (kOmegaSST) → y1=3.4e-5 m, 28 layers; y+=30 (kEpsilon) →
y1=1.0e-3 m, 10 layers; total thickness matches 0.37·L/Re^0.2.

**Now wired to the GUI** (`tests/test_bl_gui_wiring.py` pins the whole chain).
The Boundary Layers group takes velocity, fluid and wall treatment, and
"Calcola strati dalla fisica (y+)" derives nLayers / first-layer thickness /
growth ratio, reporting Re and the y+ target back to the user. Widget ranges had
to be widened (nLayers was capped at 10, the thickness fraction floored at 0.001)
— both silently truncated what the physics asked for on wall-resolved cases.

**Still open here:** Quick Mesh does not use it (it still applies whatever is in
the panel); no per-patch BL (one regex, same parameters for every wall); the
`optimisationParameters` block is still not emitted.

- Reference for the correlation now implemented:
  ```
  Re    = U·L/ν
  Cf    ≈ 0.026·Re^(-1/7)          (verify correlation choice)
  τ_w   = Cf·ρ·U²/2
  u_τ   = sqrt(τ_w/ρ)
  y₁    = y⁺_target·ν/u_τ
  δ₉₉   ≈ 0.37·L·Re^(-0.2)
  n     from geometric series: y₁·(rⁿ−1)/(r−1) ≈ δ₉₉
  ```
  Ask the user only for physically meaningful inputs (velocity, fluid, wall-function
  vs resolved: y⁺≈30–300 or y⁺≈1) and derive `nLayers`, first-layer thickness, and
  expansion ratio.
- **Suspicious**: the current code passes both `thicknessRatio` *and* `expansionRatio`
  to cfMesh. Check the source for what each actually means — this may be a real bug
  or a no-op.
- Add per-patch BL (only true walls; never inlets/outlets), and investigate
  `optimiseLayer`, `nGrowLayers`, `maxFirstLayerThickness`.
- Measure BL coverage: fraction of wall faces that actually received layers.

### 7.4 A real sizing field (curvature + proximity + features)
Today's `suggest_cell_sizes` samples ray-cast local thickness percentiles — decent,
but it ignores curvature and treats the whole model globally.
- Combine, taking the minimum at each location: curvature-driven size
  (n cells per radius of curvature), proximity/gap-driven size (m cells across a gap),
  feature-proximity size, and a global bbox-derived ceiling.
- Emit as per-patch `patchCellSize` plus `objectRefinements` boxes/spheres around
  small features — verify `objectRefinements` syntax in the source.
- Guard the invariant `min ≤ max` everywhere (this class of bug already bit once).

### 7.5 Multi-core meshing
No thread setting exists anywhere. Find how cfMesh selects thread count
(`OMP_NUM_THREADS`? a meshDict key? `-maxThreads`?) and expose it, defaulting to
`cpu_count - 1`. Measure the actual speedup — do not assume it.

### 7.6 Honest failure instead of silent garbage
`allowDisconnected 1` is currently emitted **unconditionally**. That flag suppresses a
genuine error signal — a leaky/disconnected domain quietly produces a wrong mesh
instead of failing. Investigate and make it conditional, defaulting to off.
Pair with the existing watertight check so the user gets a clear, early diagnosis.

### 7.7 Quality-driven auto-fix loop
`QualityFixWorker` and `commercial/quality_engine.py` exist but their fix strategies
are crude (scale sizes by fixed factors). Make each fix target the metric that
actually failed, and prove convergence on a deliberately bad benchmark case.

---

## 8. P2 — Only after P0/P1 measurably land

- Wire up or delete the ~20 unused `commercial/` modules — a module nothing calls is
  a liability. Decide per module and record the decision.
- Guided workflow in the spirit of Fluent's Watertight Geometry Workflow:
  Import → Health check → Intent → Sizing → BL → Mesh → Quality → Export,
  with the app pre-filling every step and the user only overriding.
- Full BaramFlow round-trip test: mesh here, actually open the exported case in
  BaramFlow, confirm patches/types/fields are all read correctly.
- UI/UX consistency pass.

---

## 9. Loop protocol — repeat until the goal holds

Each iteration:
1. Pick the **highest-value unfinished item** (§7 order unless benchmarks say otherwise).
2. State the hypothesis and how you will measure it — in the journal, before coding.
3. Verify the hypothesis against a primary source (cfMesh source / docs / experiment).
4. Implement the smallest change that tests it.
5. Run: fast test suite (must stay green) → benchmarks → `compare.py` vs baseline.
6. **Better** → commit with the evidence in the message, update baseline.
   **Worse or neutral** → revert, and write down what you learned. A recorded negative
   result is real progress; a silently abandoned experiment is waste.
7. Append to the journal. Next iteration.

**Journal: `docs/NIGHT_LOOP_JOURNAL.md`** — append-only, one entry per iteration:
hypothesis · what you did · measured result (real numbers) · decision · next step.
Assume the next agent (or a human) starts cold from this file.

---

## 10. Guardrails

- **Never report a result you did not measure.** No "should improve quality" — run it
  and paste the numbers. If WSL was down and you could not verify, say exactly that.
- **Never weaken a test to make it pass.** If a test fails, either the code is wrong
  or the test encodes a wrong expectation — investigate and say which.
- The 474 passing tests are the regression net. Keep them green. Add tests for every
  algorithmic change, especially invariants (`min ≤ max`, no empty patches,
  every detail level maps to a real preset).
- Prefer editing existing modules over adding new ones. The codebase already carries
  ~20 unused modules; do not grow that pile.
- Run `pyflakes` over `src/` and `tests/` before each commit — it caught several
  genuinely fatal bugs in this codebase (undefined names in live code paths).
- Comments explain **why**, matching the surrounding style. No decorative banners,
  no emoji-tagged status markers.
- Do not touch anything outside the project directory except the scratchpad.
- Do not push anywhere, do not publish anything, do not install system-wide software.
  Python packages into the existing interpreter are fine when genuinely needed.

---

## 11. Definition of done

You may consider the mission complete when **all** of these hold and the journal
contains the evidence:

1. `benchmarks/run_benchmarks.py` runs the whole geometry set unattended.
2. **Every** benchmark geometry passes all hard gates in §4 using only the automated
   path (no hand-tuned parameters).
3. Feature edges are demonstrably captured (before/after evidence on the
   sharp-obstacle case).
4. Boundary layers are derived from physics (y⁺ target), applied only to walls, with
   measured coverage > 90%.
5. The non-watertight benchmark produces a clear, early, actionable error — never a
   silently bad mesh.
6. A case exported for BaramFlow opens correctly with all patches intact.
7. Test suite green; `pyflakes` clean; every improvement is a separate commit with
   its measured justification.

If you finish early, keep going down §8 — but never at the cost of §4.

---

## 12. If you get stuck

Do not stall silently and do not fake completion. Instead:
- Write the blocker in the journal with everything you tried.
- Move to the next independent item — most of §7 can proceed in any order.
- If WSL is the blocker, switch to work that needs no WSL: the GMSH direct path,
  local VTK-based quality metrics, sizing-field unit tests, UI work.
- Leave the tree committed, green, and clearly documented at all times, so whoever
  reads this next can pick up mid-flight.
