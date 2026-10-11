# Engineering reasoning notes

These are **internal engineering notes**, not user documentation. Each file
records the reasoning behind a specific change to the meshing engine: the
symptom that was observed, the root cause that was found, the fix that was
applied, and the evidence (measurements, benchmarks, test names) that showed
the fix worked. They exist so a future maintainer can understand *why* the code
looks the way it does instead of re-deriving it — or undoing it. They are
historical records of a decision at a point in time, so a note may describe a
state that later changed; the code and tests remain the source of truth.

## By theme

### Boundary layer (BL)
- `bl_real_layers_reasoning.md`, `bl_local_height_fallback_reasoning.md`, `collapsed_bl_reasoning.md` — BL layer count/thickness collapse on the poly path.
- `local_height_retry_reasoning.md`, `combined_normal_height_retry_reasoning.md`, `guided_normal_filter_reasoning.md`, `most_visible_normal_reasoning.md` — per-vertex height budgets, retries and normal selection.
- `normal_smoothing_reasoning.md`, `smoothed_dual_blend_reasoning.md`, `smoothing_gate_fix_reasoning.md` — normal smoothing and the blend/gate between BL and dual core.
- `poly_bl_antisliver_reasoning.md`, `thinfirst_reasoning.md`, `zonal_aspect_relaxation_reasoning.md` — sliver avoidance, thin-feature-first handling, aspect relaxation.

### Dual / polyhedral mesher
- `poly_dual_config_reasoning.md`, `poly_cell_merge_reasoning.md`, `poly_edge_cap_reasoning.md`, `poly_pyramid_repair_reasoning.md` — dual construction, cell merging, edge caps, pyramid repair.
- `production_topology_reasoning.md`, `decoupled_reasoning.md`, `early_exit_reasoning.md`, `fase3_reasoning.md` — production topology, decoupling and control-flow changes.

### GMSH / geometry / feature detection
- `complex_geometry_reasoning.md`, `h4_generalization_reasoning.md`, `h4_vertex_exclusion_reasoning.md`, `slot1_exclusion_anomaly_reasoning.md` — behaviour and exclusions on hard geometries.
- `getme_compactness_reasoning.md`, `cylinder_aspect_clusters_reasoning.md`, `global_windings_speed_reasoning.md` — compactness, aspect clustering, winding orientation.

### Quality / non-orthogonality
- `non-ortho_reasoning.md`, `poly_nonortho_reasoning.md`, `skewness_formula_fix_reasoning.md` — non-orthogonality drivers and the skewness metric fix.

### Performance / memory / sizing
- `perf_baseline_phase0.md`, `sizing_estimate_rebench_reasoning.md`, `split_rounds_rebench_reasoning.md` — profiling baselines and sizing benchmarks.
- `ram_crash_rootcause_reasoning.md`, `adaptive_runaway_reasoning.md` — memory blow-up and adaptive-refinement runaway root causes.

### Cross-cutting
- `_buglog.md` — out-of-lane findings and repo-hygiene measurements (clone size, branch state, stale README claims).
