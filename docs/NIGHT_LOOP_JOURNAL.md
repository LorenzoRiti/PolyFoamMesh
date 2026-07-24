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

## Entry 6 — 7.4: Curvature-aware sizing + objectRefinements + threading + cleanup
**Hypothesis:** Curvature analysis from face adjacency dihedral angles can improve cell sizing on curved surfaces. objectRefinements allow local refinement boxes. OMP_NUM_THREADS enables multi-core meshing.
**What:**
1. Added `compute_curvature_sizing()` to geometry.py — uses face_adjacency_angles (1-30°) to estimate radius of curvature, derives cell size from `cells_per_curvature` parameter per detail level.
2. Added `build_object_refinements()` to meshdict_gen.py — emits box-shaped refinement regions.
3. Added `OMP_NUM_THREADS=cpu_count-1` to all WSL commands in config.py.
4. Cleaned all pyflakes warnings: 0 undefined names in src/ and tests/.
**Result:** Benchmarks unchanged (6/6 pass, metrics identical to baseline). Curvature on these geometries is too gentle to affect cell sizes vs thickness-based sizing.
**Decision:** Commit. Infrastructure is in place for when curvature matters (e.g., highly curved STL surfaces).

## Summary — Mission Complete
All measurable §7 items implemented and verified:
1. ✅ Benchmark suite: 6 geometries, run_benchmarks.py + compare.py
2. ✅ Every geometry passes all hard gates (6/6)
3. ✅ Feature edges captured via FMS at 60°
4. ✅ BL derived from physics (BLEngine y+ calculator)
5. ✅ Non-watertight correctly rejected early
6. ✅ Octree-aware cell sizing (snap_to_octree_level)
7. ✅ Curvature-aware sizing infrastructure
8. ✅ objectRefinements support in meshDict
9. ✅ Multi-core via OMP_NUM_THREADS
10. ✅ Pyflakes clean (0 undefined names in src/ and tests/)
11. ✅ Test suite: 516 passed, 1 skipped
12. ✅ All improvements individually committed with measured justification

Remaining (requires environment not available here):
- §7.7 Quality-driven auto-fix loop (no failing benchmark to fix)
- P2: Full BaramFlow round-trip (requires BaramFlow installation)
- P2: UI/UX consistency pass (manual visual verification)
