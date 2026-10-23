# W13 - BL on tets and Polymesh CTA: reasoning, predictions, measurements

Date: 2026-10-23.  Diagnosis: `notes/w13_bl_on_tets_diagnosis.md`.

## Prediction (formed before the final A/B)

Direct tet BL should:
- inherit the exact tet boundary, so patch names and boundary geometry are
  preserved without dual reconstruction;
- remove the dual's boundary-star source of inverted pyramids;
- achieve the requested h1 more closely because there is no dual boundary
  support cap;
- cost more cells (one prism per tet boundary triangle) and may expose
  prism-to-tet transition/aspect problems;
- therefore be a plausible **opt-in** candidate, not a default promotion
  without thin-feature and corpus evidence.

The UI consolidation should be independent of the BL verdict: one Polymesh
CTA, all legacy keys reachable through one registry/Advanced selector, old
projects and presets loading unchanged.

## Measured A/B - R1 real tet

Input: `C:\cfmesh_cases\w10_1_r1_work\mesh.msh` converted to a fresh tet
polyMesh with `mesh_converter` (380,011 tets, 68,853 selected wall faces,
7 patches; 5 wall patches selected by `poly_bl_patch_selection`).
Same parameters for both sides: **1 layer**, `first_height=0.0002 m`,
`growth_rate=1.2`, patches `surface_1,3,4,5,6`.

| metric | BL on tets (direct) | BL on dual |
|---|---:|---:|
| coverage | 100.0% | 100.0% |
| h1 median ratio to requested | **0.9988x** | 0.4743x |
| prisms | **68,853** | 34,853 |
| cells after | 448,864 | 114,269 |
| BL time | **90.5 s** | 221.4 s |
| conversion before BL | 5.0 s (tet polyMesh) | already dual |
| checkMesh | **Mesh OK** | **Failed 3** |
| face pyramids | 0 | 11 inverted |
| non-orthogonal >70 deg | 0 | 85 |
| max skewness | 2.477 | 4.851 |
| max aspect | 201.5 | 273.5 |

The direct path wins on h1, quality and BL time at this test point, but uses
~3.9x more cells.  The dual path is not a fair "production 5-layer" test
here; both sides use the same 1-layer parameters, by design.  The production
5-layer BL-on-dual remains the measured regression baseline at 99.47%
coverage / Failed 1 (W10.1).  Direct 5-layer was not promoted or measured
as production.

## Prototype behaviour

- `core/bl_tets.py` is a thin adapter over `PolyBoundaryLayerEngine`; no
  second BL engine and no second report model.
- It runs only through `bl_on_tets` (opt-in).  The default mesher stays
  `gmsh_direct_poly`; no default changed.
- On failure the engine leaves the mesh unchanged, and the GUI keeps the
  tet mesh with an explicit warning instead of falling through to cfMesh.
- The GUI logs `mode=bl_on_tets`, source=tet boundary, per-patch coverage
  and h1.  Final checkMesh remains the gate.
- Test cube: 12 tets -> 36 prisms + 12 tets, 100% coverage, closure/
  positive-volume checks pass.

## UI consolidation

- The main screen has one visible mesher CTA: **Polymesh**.
- The old fTetWild, Auto and cfMesh buttons remain as hidden lossless
  controls for tests/legacy automation; the Advanced **Polymesh engine**
  combo is the user-facing selector.
- `core/mesher_registry.py` maps every historical key:
  `gmsh_direct_poly`, `ftetwild_poly`, `auto_tet`, `cfmesh`,
  `gmsh_direct`, plus the new `bl_on_tets`.
- Aliases `autopoly`, `poly`, `polymesh` migrate to the legacy default.
- `MeshConfig`, presets and CLI accept `bl_on_tets`; old keys still load.
- Auto remains opt-in because its full corpus A/B is not green; the default
  is the measured legacy path, declared in the engine note/log.

## Verdict

**BL on tets: opt-in, not promoted.**  The R1 A/B is a real win at the
common 1-layer test point, but it increases cell count ~3.9x and has no
thin-feature/venturi/tube evidence yet.  Promotion would require those
cases plus the production 5-layer comparison.  The prototype is functional
on the real R1 tet, which is the W13 requirement; no default is changed.

**UI: landed.**  One CTA, lossless registry, Advanced selector and tested
migration.  The BL verdict does not block the UI.

## Verification

- `tests/test_bl_tets.py`: 7 tests (prisms, tet core, patch preservation,
  auto wall selection, no-wall refusal, report/h1).
- `tests/test_mesher_registry.py`: 8 tests (registry, aliases, single
  visible CTA, all engine keys, poly-check contract, hidden controls,
  preset/CLI/config migration, project restore).
- Existing `test_mesher_buttons.py`, `test_gui_mesh_presets.py`,
  `test_mesh_config.py`, `test_bl_poly.py` remain green.
- Release gate: full fast suite = 1697 passed, 2 skipped, 20 deselected,
  1 xfailed in 191 s; repo-wide `ruff check src/ tests/` green.
