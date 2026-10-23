# W11 - multi-patch and zero-config: reasoning and measured result

Date: 2026-10-23.  Diagnosis: `notes/w11_multipatch_diagnosis.md`.
Code: `mesh_converter.py`, `foam_mesh_io.py`, `openfoam_runner.py`,
`gui/main_window.py`; tests: `tests/test_mesh_converter_multiregion.py`,
`tests/test_mesh_converter.py`.

## Prediction (written from the diagnosis, before the final measurements)

If the failures are truly upstream, then:

1. A multi-region `.msh` with 3D physical groups but untagged 2D surface
   elements must produce one patch per volume name, never `surface_0`.
2. A 2D patch and a 3D region sharing a numeric tag must keep separate
   names; merging by tag would lose one of them.
3. Reading a converted mesh must report `len(neighbour) == n_internal`
   (no `-1` placeholders); otherwise BL's `n_int` is wrong and no wall is
   selected even when the patch names are correct.
4. The per-patch report must have the same patch set as the viewer.  On R1
   that means 7 rows: 5 selected wall patches, 2 non-BL caps with
   `bl_selected=false`, not 5 rows.
5. Duplicate/invalid physical names must not create duplicate or empty
   OpenFOAM patches; a non-manifold shared face must fail with a precise
   error rather than become a ghost boundary patch.
6. Zero-config must work from volume tags alone: no `boundary_patches`
   argument, names `region_N` when no field data exists, and the shared
   `patch_roles.is_wall_patch` must classify a blade `pressure_side` as a
   wall.
7. Expected suite impact: no existing contract breaks; only tests that
   encoded the old invalid `-1` neighbour length need updating.

## What was measured

### Synthetic multi-region `.msh` (declared representative)

`tests/test_mesh_converter_multiregion.py` builds real meshio/GMSH22 files
with two disconnected/connected tetrahedra and asserts the mesh written to
disk:

- volume tags only -> `region_a` 4 boundary faces + `region_b` 4; no
  manual mapping;
- untagged 2D triangles + volume tags -> same two patches, no `surface_0`;
- 2D `wall` tag 1 + 3D `fluid` tag 1 -> the surface face is `wall`, the other
  region is `region_b`; neither name overwrites the other;
- mixed: surface `wall` (tag 1) on one face of volume 2, volume `fluid`
  (tag 1) untagged elsewhere -> `wall` 1, `fluid` 4, `solid` 3, all 8
  boundary faces accounted for;
- duplicate names `wall-a` / `wall a` -> one `wall_a` patch, 8 faces;
- fully untagged -> one `default` patch with all boundary faces;
- all boundary-face counts sum to `faces - internal`;
- non-manifold face shared by three tets -> `RuntimeError: Non-manifold
  mesh...` before any file is written;
- explicit mapping wins and is sanitized (`hot fluid!` -> `hot_fluid`);
- per-patch report lists both region patches; only the selected one has
  `bl_selected=true`;
- the viewer patch map (`read_openfoam_mesh_patches`) has exactly the
  report's patch names.

Test command: `python -m pytest tests/test_mesh_converter_multiregion.py -q`
-> **13 passed** (the file also covers the existing mesh_converter
orientation regression through the same helpers).

### Real regression case R1 (`C:\cfmesh_cases\w10_1_r1_policy`)

Command used `poly_bl_patch_selection()` then `boundary_layer_report()` with
the returned names and `requested_h1=0.00126`, `velocity=10`:

- 7 boundary patches: `surface_1..surface_7`
- selected BL patches: `surface_1, surface_3, surface_4, surface_5,
  surface_6` (5); `surface_2`/`surface_7` are caps and are reported with
  `bl_selected=false`, coverage 0
- `n_boundary_faces = 36,955`; `n_wall_faces = 36,413`
- global coverage **99.51%** (selected walls)
- h1 median 0.0001455 m vs 0.00126 requested = **0.1155x**; this is the
  W10.1 support cap, not a new regression
- per-patch coverage: 99.82 / 98.63 / 98.76 / 99.13 / 99.94 % on the five
  selected patches; caps 0

This matches the W10.1/W10.3 regression contract (7 patches, 5-layer BL on
5 wall patches) and proves that the report no longer hides the two caps.

### Why the writer fix was needed

The first synthetic BL attempt selected `region_a` but built **0** prisms.
`read_polymesh` returned `neighbour` with 8 entries while the mesh had 0
internal faces, so `n_int == n_faces` and `_select_faces` saw no boundary
faces.  The converter had correctly written the patch names, but
`write_polymesh` had written its `-1` boundary placeholders.  Filtering at
the single writer (and normalising at the reader for old files) restored
the invariant every consumer already assumed.  This was a genuine root
cause, not a BL tuning problem.

## Final state / limits

- Synthetic multi-region is the declared representative because no real
  multi-region `.msh` exists in `C:\cfmesh_cases`.
- R1 has 7 patches but only 5 are walls; the two caps correctly receive no
  BL.  Per-patch h1/y+ for a patch with no layered face is `null`/0
  coverage, not fabricated.
- BL parameters remain global, with per-patch **selection** and per-patch
  **reporting**; independent per-patch parameter values were not added
  (the W7/W10 config contract stays single-source).
- Release gate measured: full fast suite (`-m "not slow and not wsl"`) =
  1682 passed, 2 skipped, 20 deselected, 1 xfailed in 184 s; repo-wide
  `ruff check src/ tests/` green.

