# W13 - BL directly on tets + one Polymesh CTA: diagnosis

Date: 2026-10-23.  Inputs: `bl_poly.py`, `bl_config.py`,
`openfoam_runner.py`, `tet_poly_dual.py`, `params_panel.py`,
`main_window.py`, `mesh_config.py`, `mesh_presets.py`, `preset_store.py`,
`tests/test_mesher_buttons.py`, `tests/test_mesh_config.py`,
`.opencode/REGRESSION_CASES.md`, W10.1/W10.3 notes.

## 1. Current BL on the dual (measured baseline)

- fTetWild source: 10/15 corpus cases produced, 9/15 at >=98% coverage;
  GMSH/poly: 3/15 produced, 0/15 at >=98%.
- R1 (`gmsh_direct_20261020_173334`, 380,011 tets): dual-only 79,416
  cells, 1 pyramid, NO 48.46, skew 3.31; dual+BL (5 layers,
  h1=1.26 mm) 253,681 cells, 39 pyramids, 5 skew, 26 non-ortho errors,
  Failed 3.  W10.1 quality policy took that to 0 pyramids / 1 skew /
  Failed 1 at 99.47% coverage, h1 ratio 0.113 (support cap).
- Venturi: 43,782 prisms, 60.7% wall, 199 pyramids, aspect 209, NO/skew
  unchanged; tube: 6 layers, 100% coverage.
- The dual boundary is the hard part: the median dual is exact but its
  boundary cells can wrap concave features and produce inverted face
  pyramids.  The BL then adds 38 defect faces on R1.

## 2. Why direct tet BL

The prism shell can be built on the original tet boundary: wall triangles
are the source faces, and the existing `PolyBoundaryLayerEngine` already
replaces wall faces, moves the adjacent core vertices and validates closure,
manifoldness and positive volumes.  The core remains tetrahedral.  This
avoids the dual boundary entirely.

Where it can win:
- thin features and curved walls where the dual star has no room;
- patch preservation: the input tet boundary is used verbatim;
- h1 fidelity: no dual boundary-cell support cap in the first layer;
- fewer topology stages (no dual before BL).

Where it can lose:
- prism-to-tet transition quality and aspect ratio;
- more cells than the dual (one prism per input boundary triangle instead
  of one per collapsed dual boundary vertex);
- input tet defects are inherited (the engine allows input pyramid count
  as a tolerance, but the final checkMesh remains the gate);
- a coarse tet boundary can lack interior support or produce non-positive
  core cells.

## 3. Topology

For each selected boundary face (triangle from the tet boundary):
1. compute the outward normal and the first interior support vertex;
2. extrude `n_layers` points along the normal with the configured
   first height/growth, using the same per-vertex budgets as the dual path;
3. build one prism per layer and move the adjacent tet core shell to the
   last layer (existing BL engine);
4. close the prism side faces, preserve the boundary patch tables, and
   validate closure/manifold/positive volumes before writing.

The prototype deliberately reuses the engine's existing prism-to-core
transition.  It does not introduce a second transition algorithm.

## 4. UI map before W13

| legacy key | visible UI | actual path | tests |
|---|---|---|---|
| `gmsh_direct_poly` | Polymesh (primary) | GMSH tet -> barycentric dual -> BL | `test_bl_poly`, GUI/preset tests, R1 |
| `ftetwild_poly` | "Polymesh sperimentale" | fTetWild tet -> dual -> BL | ftetWild tests, issue #12 |
| `auto_tet` | "Auto (GMSH surface + fTetWild volume)" | `choose_tet_source` -> fTet/GMSH -> dual | `test_tet_source`, W10.2 |
| `cfmesh` | Cartesian cfMesh | cfMesh hex -> optional polyDualMesh | mesh/export tests |
| `gmsh_direct` | removed button; internal fallback | pure FEM tetra | config/legacy dispatch |
| `autopoly` (legacy/UI) | old automatic path | `_resolve_auto_mesher` -> cfmesh/poly | main-window tests |

There are currently 4 visible engine buttons; the experimental fTetWild and
Auto entries look like product choices rather than the same Polymesh family.
The W10.2 auto policy is opt-in; its full corpus A/B is not green, so it
must not become the default by UI hierarchy alone.

## 5. W13 design

One visible CTA **Polymesh**; all engines remain in
`core/mesher_registry.py`:
- `gmsh_direct_poly` - measured legacy default (GMSH tet -> dual);
- `auto_tet` - Auto source policy with visible GMSH fallback (opt-in until
  its A/B is green);
- `ftetwild_poly` - fTetWild tet -> dual;
- `bl_on_tets` - W13 prototype: direct tet boundary BL, no dual (opt-in);
- `gmsh_direct` - pure FEM tetra (Advanced/CLI);
- `cfmesh` - hex cfMesh (Advanced/CLI).

Old keys and aliases (`autopoly`, `poly`) migrate through the registry; no
module/test/note is deleted.  The Advanced `Polymesh engine` combo is the
only selector; selecting an engine updates the hidden legacy button state
and the poly-checkbox contract.  The log/report always name the effective
engine and `mode=bl_on_tets` provenance.

BL-on-tets reuses `PolyBoundaryLayerEngine` through `core/bl_tets.py`; the
existing report contract, per-patch fields and checkMesh pipeline are
unchanged.  Defaults remain untouched.

## 6. A/B plan

Same input tet, same n_layers/h1/growth, same selected patches, same
checkMesh:
- R1 real tet (measured);
- venturi, thin_gap, tube as the next cases (not all available as fresh
  tet polyMeshes in this session; only R1 had a reconstructible 380k-tet
  source).
Metrics: coverage, h1 median/ratio, pyramids/skew/non-ortho/aspect,
prisms, cells, BL time, final checkMesh.

## 7. Load estimate and risks

Files: `mesher_registry.py`, `bl_tets.py`, `openfoam_runner.py`,
`mesh_config.py`, `params_panel.py`, `main_window.py`, tests (BL, registry,
UI), docs/notes.  ~700-1000 net lines including tests/docs.  Main risks:
tet transition rejection, extra cell count, UI migration regression, and
mistaking the opt-in A/B win for a production promotion.  The response is
to keep `bl_on_tets` opt-in until thin-feature and corpus evidence exists.
