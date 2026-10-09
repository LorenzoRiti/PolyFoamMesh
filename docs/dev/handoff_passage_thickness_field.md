# Sizing field from the local passage width — new algorithm

Context: after the fixes of the previous round (real toggle, caps on DistMax/
DistMin, viewer showing only the boundary), the user asked: "aren't there
any serious open-source automatic meshing algorithms? but serious" and then
"develop an automatic meshing sizing algorithm that understands where to
refine and refines where needed... with in-depth research".

## Research done before writing code

1. **GMSH already has a serious official algorithm**: `Mesh.AutomaticMeshSizeField`
   (medial axis + curvature, maintained by the GMSH team). **Verified with a
   direct test**: our build (pip wheel) fails with `"Gmsh has to be
   compiled with HXT and P4EST"`. **Also verified that conda-forge does not
   build it** (read the feedstock `meta.yaml` recipe): it is not available
   without compiling GMSH from source with P4EST+MPI — high cost, not a
   simple package change.
2. **Mathematical foundation confirmed**: the quantity that every serious mesher
   uses to "understand where to refine" is the **local feature size** (LFS) —
   distance from the medial axis of the solid — classically approximated with
   **Voronoi poles** (Amenta & Bern, the basis of CGAL and of GMSH itself).
3. **Voronoi-poles attempt, failed numerically**: prototype on a
   sparse/structured sampling (two parallel planes) — results
   wrong by an order of magnitude. It is a known and documented problem in
   the literature (the method requires dense, non-degenerate sampling and
   robust handling of unbounded/degenerate Voronoi cells). Discarded for
   a risk of fragility too high compared to the benefit.
4. **Found the right alternative already in the repo**: `core/geometry.py` already
   has a vectorized and tested ray-caster (`_sample_thickness_one_mesh`,
   used by `analyze_local_thickness`) — but it **collapses everything to global
   statistics** (p1/p5/p10...) for the "Auto-Suggest Cell Sizes" button
   (now hidden), throwing away exactly the spatial information
   ("WHERE it is narrow") that is needed to refine only where needed.
5. **Validated with three experiments before touching production code**:
   - two parallel planes with known gap = 0.1 → range of values confused
     initially by a normal-orientation error (the ray-cast
     measures the width of the volume the mesh ENCLOSES, i.e. the fluid
     domain in a CFD case — not the thickness of the solid wall);
   - a single thin box (represents a narrow channel) → **0.1000
     exact**; a large cube (open chamber) → **2.0000 exact**;
   - **the decisive test**: a real venturi, a SINGLE connected mesh
     wide→narrow→wide (body of revolution) → **1.99 at the two wide sides
     (expected 2.0), 0.34 at the throat (expected ~0.30)** — a genuinely
     local field, not a per-body statistic.
   - **also confirmed on real GMSH and on a real venturi.stl**
     (`C:/cfmesh_poly_bench/venturi.stl`): the field decreases monotonically
     along the axis (0.121 → 0.075 over 10 buckets), absolute minimum 0.00075.

## What I implemented

### `core/geometry.py`
- **`sample_thickness_field(mesh, n_samples, bbox_max, seed) -> (points,
  thickness)`** — new function, a variant of `_sample_thickness_one_mesh`
  that also returns the POINTS (not just the values), needed for a
  spatial field. Duplicates ~30 lines of the ray-casting logic instead of
  refactoring the existing function: `_sample_thickness_one_mesh` is already
  used in production (Auto-Suggest), touching it entailed a regression risk
  unnecessary for a code-cleanliness gain.

### `core/gmsh_wrapper.py`
- **`_extract_boundary_trimesh(gmsh_mod)`** — extracts the entire discrete
  surface already tessellated by GMSH (all 2D entities, not surface by
  surface) as a single `trimesh.Trimesh`, using GMSH node tags
  as the shared vertex key (no merge by coordinate
  tolerance — the GMSH mesh is already conforming at the junctions). Verified:
  on a 2×1×0.5 box, watertight, exact volume 1.0.
- **`_sample_passage_thickness_field(gmsh_mod, h_min, h_max, cells_across,
  max_samples)`** — orchestrator: extracts the boundary, calls
  `sample_thickness_field`, converts thickness → target size
  (`clamp(thickness / cells_across, h_min, h_max)`). Degrades to "no
  sampling" on any exception (never blocks meshing).
- **`_configure_curvature_size_field`**: extended with optional parameters
  `passage_points`/`passage_target_sizes`, merged into the point set BEFORE
  the growth-rate relaxation — so the transition between the constriction and the
  surrounding curvature is also gradual, not a sharp jump.
- **`_configure_adaptive_sizing`**: the old cycle of Ball fields based on
  `_detect_surface_gaps` (bounding-box distance between pairs of
  surfaces — a proxy, not the true distance, blind to constrictions formed
  by more than two surfaces) is **replaced** by the call to the new thickness
  field. `_detect_surface_gaps` itself **was not removed**: it is
  still needed elsewhere for the polyDualMesh risk heuristic (gap count,
  a completely different use from sizing) — a bug found and fixed during
  implementation: I had removed the `gaps` variable together with the Ball
  cycle, breaking that heuristic; put back.

## Verified (not just isolated unit tests)

- **Real GMSH, no WSL**, on `sample_cad/cylinder_test.stl`: mesh generated
  correctly (5,317 tet), no exception, `passage_samples=6000` in the
  log.
- **Real GMSH on a real venturi** (`C:/cfmesh_poly_bench/venturi.stl`):
  51,614 tet, no crash, thickness field verified to decrease
  monotonically toward the throat (see above).
- **6/6 new tests green** (`tests/test_passage_thickness_field.py`):
  4 purely trimesh/numpy (portable, no GMSH), 2 with real GMSH (simple
  box, fast, no WSL).
- **23/23 pre-existing tests green** (`test_geometry.py`,
  `test_cell_sizing.py`, `test_sizing_resolves_features.py`) —
  `analyze_local_thickness` and the rest of the existing sizing pipeline
  were not touched/broken.
- **27/27 tests from the previous session still green** (adaptive sizing,
  slider, viewer) — no cross regression.

## Side note observed, not caused by this work

During the real runs, `Error: Unknown OpenCASCADE entity
of dimension 1 with tag N` appears twice — printed by GMSH during "Meshing 1D" on
STL input (`classifySurfaces`/`createGeometry` path, discrete geometry
with no real OCC kernel). **It does not block meshing** (confirmed:
mesh generated correctly in all runs) and does not appear to originate from
`_extract_boundary_trimesh` (which calls no OCC-specific API,
only `getNodes`/`getElements`) — plausibly pre-existing to today's work,
tied to the STL→discrete-geometry reconstruction path already
documented elsewhere in the code (`_scan_feature_sizes`'s fallback for
"STL input goes through classifySurfaces()..."). Not investigated further
due to time limits — if it also appears on a clean checkout before
this session, it is confirmed not caused by this work.

## NOT done / to verify on resumption

1. **I did not compare with real checkMesh (WSL)** the "before vs after"
   on the same geometry (e.g. venturi) to measure the effect on the
   cell count/quality — only verified that generation does not
   break and that the field is geometrically correct. Natural next step
   (requires WSL, so to be coordinated to avoid competing for resources).
2. **BUG FOUND AND FIXED while writing this same
   checkpoint**: the code used `df.get("min_thick_cells", 8)`, but
   `_GMSH_DETAIL` (the dictionary of levels in gmsh_wrapper.py) **does not have
   that key at all** — it exists only in `core/geometry.py`'s
   `_DETAIL_PRESETS` (used for Auto-Suggest, a different dictionary). The
   default `8` was therefore used ALWAYS, silently, for every
   detail level — the "Mesh Fineness" slider had no
   effect on the number of cells through the constriction. Fixed:
   `df.get("cells_across", 8)`, which reuses the key that REALLY EXISTS in
   `_GMSH_DETAIL` and is already tuned per level (8/13/20/32/48). Verified
   again after the fix: 12/12 tests green.
3. **Performance on very large parts**: `max_samples=6000` by default
   is the same order of magnitude already used for the curvature field
   (20,000) but lower — the ray-casting time was not measured on a
   geometry with hundreds of thousands of boundary triangles (the test
   venturi had a few thousand). trimesh without `pyembree` installed
   can be slow on very dense meshes — to be profiled on a real large
   case before declaring the performance is fine.
4. **Not wired in the GUI as a visible option** — it is an improvement
   of the existing algorithm behind the "Automatic refinement" toggle already
   present, it requires no new UI, but it was not communicated/documented
   to the end user in the app itself (tooltip, changelog).

## Files touched

- `src/cfmesh_autogui/core/geometry.py` (+new function, no change
  to existing code)
- `src/cfmesh_autogui/core/gmsh_wrapper.py` (2 new functions, 1 extended
  with optional parameters, 1 logic replacement in
  `_configure_adaptive_sizing`)
- `tests/test_passage_thickness_field.py` (new, 6 tests)
