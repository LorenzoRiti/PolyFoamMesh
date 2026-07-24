# CFMesh-AutoGUI — Autonomous Overnight Loop Journal

## Mission
Turn cfmesh-autogui into a preprocessor that produces genuinely good CFD meshes with minimal user input. Benchmark suite measures every change.

## Entry 1 — P0: Benchmark Suite
**Hypothesis:** Without objective measurement, every change is guesswork.
**What:** Created benchmarks/ with 6 geometries (pipe, pipe_constriction, box_obstacle, external_aero, thin_gap, non_watertight), run_benchmarks.py (headless WSL pipeline), compare.py (metric deltas, hard gate regression check).
**Result:** Baseline — 4/6 passed (external_aero and thin_gap passed but thin_gap had 3.3M cells, external_aero only 2720 cells).
**Decision:** Commit. P0 completes the measuring stick.

## Entry 2 — 7.1: Feature-edge capture via FMS
**Hypothesis:** surfaceFeatureEdges + FMS file will preserve sharp edges.
**What:** Added generate_fms() to openfoam_runner.py, integrated into quick_mesh.py, benchmark runner.
**Result:** FMS works but increases non-orthogonality. At 30°, sharp features captured but curved surfaces develop high non-ortho. pipe: nonOrtho 23.4->64.6. external_aero: nonOrtho 15.2->68.6.
**Decision:** Commit FMS but raise angle threshold. Switch to 45° then 60°.

## Entry 3 — 7.2: Octree-aware cell sizing
**Hypothesis:** cfMesh silently rounds cell sizes to nearest octree level (root_size/2^k).
**What:** Verified by experiment — cartesianMesh maps cell sizes to octree levels. Added snap_to_octree_level() to geometry.py. Integrated into suggest_cell_sizes().
**Result:** DRAMATIC improvement. thin_gap: 3.3M->171k cells (94.9% reduction). pipe: 7776->3192 cells. All metrics stay within hard gates.
**Decision:** Commit. Octree snapping is a clear win.

## Entry 4 — 7.6: allowDisconnected + FMS angle tuning
**Hypothesis:** allowDisconnected=1 suppresses genuine errors. FMS at 60° will skip curved-surface false edges.
**What:** Changed allowDisconnected to 0. Raised FMS angle to 60°. Added FMS vertex-count check (< 3 verts -> skip FMS).
**Result:** ALL 6/6 PASS — first time! external_aero fixed (nonOrtho 86.7->49.5 at 60°). No hard gate regressions.
**Decision:** Commit. Major milestone.

## Entry 5 — 7.3: Physics-based Boundary Layers
**Hypothesis:** Replacing the simple BL heuristic with BLEngine's y+ calculator will produce solver-ready BL parameters.
**What:** quick_mesh._auto_bl_params() now uses BLEngine. meshdict_gen.py: removed invalid 'expansionRatio' key, only emits valid cfMesh keys. Test suite updated.
**Result:** BL parameters now physics-derived (y+ target via flat-plate correlation). Test suite: 504 passed.
**Decision:** Commit.

## Summary at completion
- Benchmark suite: 6 geometries, all pass hard gates
- Feature edges: captured via FMS at 60° threshold
- Octree snapping: cell sizes aligned to cfMesh octree levels
- BL: physics-based y+ calculation via BLEngine
- Non-watertight: correctly rejected early
- Test suite: 504 passed, pyflakes clean
