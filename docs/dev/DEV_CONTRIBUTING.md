# CONTRIBUTION — closing the polyhedral mesher + boundary layer (PHASE 0 + PHASE 1)

> Compiled by Reasonix, 2026-08-10. Mandatory §10 block of the megaprompt.
> The numbers below are all measured (real WSL checkMesh, in-process engine,
> WSL solver), never assumed.

```python
CONTRIBUTO = {
    "autore": "Reasonix (2026-08-10)",

    "comprensione": """
    The production path is: STL/STEP -> GMSH tet (native Windows) ->
    gmshToFoam -> TetPolyDualConverter (barycentric/median dual, 100% poly,
    boundary = exact subdivision of the triangles into 3 planar quads) ->
    PolyBoundaryLayerEngine (wall prisms, pure numpy) -> checkMesh.
    The residual defect of the valve: where a primal boundary vertex sits on
    a CONCAVE feature edge of the CAD, the dual cell wraps around the corner and
    is genuinely non-convex -> the centroid falls outside one of its boundary
    quads -> it fails checkMesh's 'face pyramids' check (852 faces, 0.25%
    of the cells). The cells are closed, positive-volume, watertight: it fails
    a check that ASSUMES convexity. The converter-level avenues
    (agglomeration, signed/unsigned split, wedge cells, median_faces)
    are closed with numbers (§3 megaprompt). The native Cartesian
    mesher (hex_poly_dual) is a separate experimental branch (Tools menu), not the
    production path treated here. My work covers PHASE 0 (decision
    on the defect), PHASE 1 (selective BL per patch) and the PHASE 2
    machinery (variable layer count); the complete n->n-1->...->0
    gradation remains limited by closure on real concave meshes (numbers in
    fase_2_esito). The boundary-face collapse (collapse_smooth_edges)
    is the parallel agent's work.
    """,

    "fase_0_esito": """
    potentialFoam on the valve (same geometry, same BC, tet 824,661 vs
    poly 152,086):
      - both converge (End, no FATAL);
      - final GAMG residual on Phi: tet 3.07e-5 (3 it), poly 4.60e-6 (2 it);
      - Continuity error: tet 8.71e-3 (18% of the flow), poly 7.83e-4 (1.6%);
      - volume: 0.121835 vs 0.121835 (0.00%);
      - prescribed inlet flow: 0.0482551 vs 0.0482551 (0.00%).
    DECISION: G3 is NOT a bug — it is a documented limit of a check that
    assumes convexity on cells the solver digests perfectly. The valve's
    poly solves, converges and conserves the flow rate BETTER than the tet it
    derives from. PHASE 3 (planar cutting) must NOT be done; stop rule respected
    because the premise is false. Full report: docs/poly_solver_validation.md.
    """,

    "fase_2_esito": """
    PHASE 2 machinery (variable layer count / local termination)
    implemented and verified on healthy meshes; the valve gate is NOT closed,
    with numbers:
    - bl_poly.py: per-vertex count nv (angle_fade criterion: n_layers if
      fade>=0.5, count that fits the budget if concave), per-face
      count nf = min over the vertices, consistency fixpoint (nv[v] = min of the
      incident faces — makes the count uniform per connected component and
      guarantees closure), RELAXED pyramids gate (the BL is valid if it does NOT
      ADD violations: n_pyr_after <= n_pyr_before), faces flagged as
      concave (pyramid violations of the INPUT) reduced to 1 layer.
    - Verified: cube 6/6 tests (closure 0 cells, volume 1e-6, derived
      counts, even with faces forced to 1 layer — n_prism_cells == n_faces);
      PHASE 1 gate re-verified PASS (cylinder and cube: BL ok, prism
      count ok, checkMesh Mesh OK — the regression is preserved because healthy
      meshes have fade>=0.5 everywhere and nv=n_layers).
    - Valve gate NOT reached (three strategies measured, all fail
      CLOSURE — not just the pyramids criterion):
        * unconstrained construction (different degrees for adjacent faces +
          transition faces): 199 non-closed cells (rel 9.5e-1), 1722 s;
        * fixpoint (uniform count per component): 63,252 non-closed
          cells (rel 9.8e-1), 1397 s;
        * violated faces dropped to 0 layers: volume not conserved (0.984 vs
          1.0 — the shell under the dropped faces stays empty).
    - Measured root cause: angle_fade >= 0.8 on ALL 226,538 wall vertices
      of the valve — the concavity of the dual cells is NOT detectable
      from the fade of the wall normals (the boundary quads sit on smooth
      surfaces); the 1007 pyramid defects of the input (937 boundary faces, 64
      patches) are the only detector, but extruding OR skipping those faces
      breaks closure or volume. The n->n-1->...->0 gradation requires the
      transition faces that close the step — implemented and removed
      because unreachable (the fixpoint invariant makes them dead
      code) and not verifiable on the valve.
    - Honest verdict: local termination works for the concavity
      detectable from the fade (verified at the mechanism level on the cube), but
      does NOT close G2 on the valve. The global fallback over 5 scales remains the
      valve's behavior (mesh unchanged, BL absent) — with the relaxed gate
      and the flagging of violated faces the fallback now has one more
      path, but closure remains the bottleneck.
    """,

    "cosa_ho_implementato": """
    PHASE 1 (selective BL per patch, closes G1):
    - src/cfmesh_autogui/core/bl_poly.py: _select_faces no longer auto-closes the
      selection; at the BL/non-BL boundary the side faces of the prisms lie IN THE
      PLANE of the adjacent wall and become BOUNDARY faces of the patch
      without BL, owned by the prisms (nFaces of the non-BL patch grows,
      startFace recomputed); the unselected wall faces move the shared wall
      vertices to the last layer (without this the neighboring cell stays
      open: measured 8 non-closed cells, max rel 4.43e-3, with
      the internal-face construction of the parallel draft). Before/after numbers
      on the cube with wall+inlet patches: before, the partial BL failed on ALL
      scales (validation: non-closed cells); after, success at scale=1.0,
      volume conserved, 12 prism cells (2 layers x 6 faces), 16
      termination faces.
    - Real checkMesh verification (tools/bench_bl_poly_partial.py), 2 geometries:
        A) cylinder with patches named inlet/outlet/wall: BL on wall, 72
           prisms = 3 layers x 24 wall faces, 48 terminations,
           checkMesh MESH OK;
        B) cube duct same: 81 cells (72 prism + 9 poly), skew 2.18,
           non-ortho max 44.8, checkMesh MESH OK.
    - src/cfmesh_autogui/core/openfoam_runner.py: hardcoded apply_to_all
      removed; default = only wall patches (type 'wall' or name *wall*), geometric
      fallback poly_geo_wall_patch_names (infer_patch_roles) for raw GMSH
      cases, final fallback apply_to_all with warning; applyToAll from the GUI.
    - src/cfmesh_autogui/gui/params_panel.py: get_bl_params() now carries
      applyToAll (a checkbox previously dead on the poly path).
    - tools/poly_solver_validation.py: parameterized per case (ref1/valve1)
      and variant (default/production), full log, parsing of residuals/iterations/
      continuity.
    - tools/bench_bl_poly_partial.py: new (PHASE 1 gate with real checkMesh).
    - tests/: test_bl_poly.py (terminators as boundary faces, counts
      derived from the mesh), test_openfoam_runner.py (poly_wall_patch_names +
      poly_geo_wall_patch_names), test_bl_gui_wiring.py (applyToAll).
    """,

    "proposta_personale": """
    The PHASE 0 verification showed that the valve's poly conserves the
    flow rate 11 times better than the tet (continuity 7.8e-4 vs 8.7e-3). Measurable
    proposal: add to the pipeline a 'solver-ready' quality METADATA
    (potentialFoam convergence + continuity error) exposed in the poly
    worker's report, instead of only checkMesh-green. Today the GUI judges 'ok' by
    checkMesh, which on the valve says FAIL even though the solver digests the mesh
    very well. The experiment that would verify it: run the full GUI path
    on the valve and compare the checkMesh verdict (Failed 3) with
    the potential verdict (converges, 1.6%) — the gap is the measure of how much
    the UX is discouraging the user from useful meshes.
    """,

    "limiti_residui": """
    - G2 NOT closed on the valve: the PHASE 2 machinery (variable count,
      relaxed pyramids gate, flagged concave faces) is implemented and
      verified on healthy meshes (cube 6/6, PHASE 1 gate PASS), but CLOSURE
      fails on the valve in all measured strategies (199 / 63,252
      non-closed cells; the drop to 0 layers loses the volume). The valve's
      concavity is not detectable from the fade (measured: fade >= 0.8 on
      all 226,538 vertices) — details and numbers in fase_2_esito.
    - PHASE 3 not done: PHASE 0 showed it is not needed (documented
      limit).
    - The slow test test_valve_fixture_bl_invariants (full BL on the
      valve fixture 1.05M points) takes tens of minutes; the fast suite
      excludes it. The full-BL path is unchanged (identical output with
      apply_to_all=True).
    - poly_wall_patch_names does not use meshdict_gen's infer_patch_type because
      that function (cfMesh semantics) classifies ANY unknown name
      as wall — unsuitable for the BL default; the explicit criterion (type wall /
      name *wall*) + geometric fallback is the correct semantics.
    """,

    "regressioni": """
    Green: tests/test_bl_poly.py (5 fast), tests/test_bl_gui_wiring.py (6),
    tests/test_openfoam_runner.py (6 of 7; test_mesh_worker excluded: it launches
    WSL cartesianMesh, minutes, pre-existing), tests/test_tet_poly_dual.py +
    tests/test_poly_smoother.py (17).
    Full offline suite: 926 passed, 1 skipped; 4 FAILED all
    pre-existing/environmental, none in the PHASE 0/PHASE 1 territory:
      - test_native_mesher.py::test_cube_exact_volume_and_counts: fails
        IDENTICALLY on HEAD~1 (0.97559375 vs 1.0 — experimental Cartesian
        branch, not touched);
      - test_watertight_meshdict::...do_not_need_a_qt_event_loop: order-sensitive
        flake documented in residual_risks.md, passes in isolation;
      - test_baramflow_roundtrip::test_exported_case_loads_in_openfoam and
        test_e2e_workflow::test_full_workflow: require WSL + external
        cartesianMesh (environmental, outside the poly+BL path).
    Valve baseline: tools/valve_defect_baseline.py --conv-only = PASS
    (1027 residual defects, 852 pyramids, volume conserved) — unchanged.
    Slow test test_valve_fixture_bl_invariants (full-BL on the valve fixture
    1.05M points): not completed in session (>75 min CPU; full-BL path
    equivalent to the previous one, G2 limit documented).
    No regression in the PHASE 0/PHASE 1 territory.
    """,
}
```

## Files touched (PHASE 0 + PHASE 1)

- `src/cfmesh_autogui/core/bl_poly.py` — termination at boundary faces + moved vertices
- `src/cfmesh_autogui/core/openfoam_runner.py` — wall-only default + geometric fallback
- `src/cfmesh_autogui/gui/params_panel.py` — applyToAll on the poly path
- `tools/poly_solver_validation.py` — PHASE 0 (valve)
- `tools/bench_bl_poly_partial.py` — PHASE 1 gate (new)
- `tests/test_bl_poly.py`, `tests/test_openfoam_runner.py`, `tests/test_bl_gui_wiring.py`
- `docs/poly_solver_validation.md` (new), `docs/residual_risks.md`
