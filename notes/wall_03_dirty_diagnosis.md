# WALL-03 — Dirty CAD / mini_valve: diagnosis

Date: 2026-10-23. Zero-cost step: no runs, no code changes. Numbers are
quoted from the cited notes; the two STLs were inspected read-only
(trimesh) for the geometric facts.

## 1. Measured state

### dirty_cad (shared failure — snappy fails too)

- GAP_ANALYSIS rows 1–3: 0/5 runners produce cells. Ours abort with
  0 cells (non-watertight input); snappy FATALs:
  `Point (-0.03 -0.03 -0.03) is not inside the mesh` — **not a
  competitive loss** (GAP, GOAL M2 audit).
- Geometry today: `benchmarks/geometries/dirty_cad.stl` = 8 vertices,
  10 faces, bounds ±0.03 m — a broken 6 cm cube (12 triangles needed,
  2 missing), non-watertight **by construction**. Nothing to heal back
  to: facets are simply absent.

### mini_valve (prompt premise STALE — newer file wins)

- Prompt claims 0/5 (GMSH exit 1, self-intersecting facets). That is the
  2026-10-05 baseline (GAP rows 4–5, 12, 29–30).
- 2026-10-06 Lane A (newer): generator fixed (fused watertight body),
  deterministic STEP export, sibling-`.step` harness preference,
  Delaunay(1) for STEP. Committed result
  (`comparison_20261006T170414.json`, GOAL, `poly_edge_cap_reasoning.md`
  Lane A): **poly 9/10 vs snappy 6/10**, mini_valve **PASS 64.33**
  (detail=fine, dual-only — BL failing declared), multi_inlet PASS
  49.99 where snappy fails skew, venturi PASS 43.28 via STEP.
- Current `mini_valve.stl`: 1,272 verts / 2,544 faces, **watertight =
  True** — the self-intersecting-facet defect is gone from the file.
  `mini_valve.step` (36 KB) exists as the exact-BRep alternative.
- BL on mini_valve (Lane A follow-up): BUILDS but quality collapses
  (NOmax 101.8, skew 16.2) — worse than dual-only 64.3. Finer sizing
  does not fix it; the binding term is support depth `d`, not edge cap
  (experiments reverted).
- Consequence for this lane: mini_valve's "0/5" must be **re-verified,
  not assumed**. The exit clause "con BL o dual-only dichiarato"
  explicitly admits the dual-only outcome Lane A already measured.

### multi_inlet (regression anchor, fixed)

- Was 5 disjoint solids (generator bug); fixed to 1 fused body
  (Lane A). PASS 49.99 where snappy fails. Must stay PASS.

## 2. Where each engine dies today (mechanism map)

- **GMSH discrete-STL** (`classifySurfaces` + reparametrization): dies on
  self-intersecting/open facets (old mini_valve, dirty_cad — nothing to
  classify into a volume: dirty_cad has no inside).
- **GMSH STEP/OCC** (`_heal_occ_geometry` healShapes + sew, Delaunay):
  the working path for dirty-ish BReps (Parte2 702k nodes verified).
  mini_valve.step is the candidate input.
- **fTetWild** (envelope-based, tolerant by design): the candidate for
  dirty_cad (missing facets are inside the envelope ambiguity, not a
  hard error). pytetwild is installed.
- **Dual** (`TetPolyDualConverter`): needs a watertight tet boundary;
  refuses otherwise (closure gate) — correct behavior, not the wall.
- **Direct tet + BL** (W13 `bl_tets`, opt-in): the exit route where the
  dual cannot close; same checkMesh gate.
- **Report** (`boundary_layer_report`): returns `{}` when no wall faces
  are layered — on dirty cases that is currently silence (3.5).

## 3. Lever sequence for this lane

1. **3.1 healing**: measure what `healShapes` + sew already do on
   mini_valve.step (count healed entities from the log line); aggressive
   beyond that only with a deviation metric. dirty_cad has no BRep —
   healing does not apply (2 missing triangles, not a healable defect).
2. **3.2 STEP vs STL**: mini_valve.step + Delaunay(1) + detail=fine
   (the Lane A recipe) re-verified on current tree; STL discrete path
   as the fallback measurement, not the primary.
3. **3.3 fTetWild on dirty**: dirty_cad envelope ladder
   (eps tight → loose) + GMSH fallback, all logged with numbers.
   dirty_cad is 10 triangles: success = any checkMesh-evaluable mesh;
   Mesh OK = beat-snappy.
4. **3.4 direct tet + BL**: where the dual cannot close (none expected
   on these two if tet succeeds — the tet meshes will be small and
   clean; the lever is queued, not primary).
5. **3.5 report on dirty**: per-patch coverage/h1/defects instead of
   `{}` when BL fails or no wall is selected. Small, code-level, and
   independent of the meshing outcome — do it if a wall opens it.

## 4. Reuse / refactor / eliminate

- Reuse unchanged: `run_gmsh_volume` (+ `POLYFOAMMESH_GMSH_ROBUST=1`
  env), `gmshToFoam` via WSL, `TetPolyDualConverter`,
  `ftetwild_wrapper` volume path, `bl_tets`, `boundary_layer_report`,
  real checkMesh, `CheckMeshReport`.
- Probes run under `C:\wall03_probe`; zero `src/` changes until a
  probe clears its bar. No second pipeline, no second report model.
- Nothing is removed in this lane step.

## 5. Load estimate and risks

- Diagnosis + reasoning + 2–3 probes: one session. mini_valve GMSH
  fine + dual + checkMesh is the long pole (unknown minutes).
- Risk 1 (likely): mini_valve re-verifies PASS dual-only → its clause
  is closed by verification, not by new code; remaining work is
  dirty_cad + (optionally) direct-tet BL.
- Risk 2: dirty_cad meshes but fails checkMesh → parity-with-snappy
  argument (0 cells/FATAL vs evaluable mesh), lane OPEN, user decides.
- Risk 3: GMSH fine on mini_valve.step stalls → fall back to medium
  (PASS threshold only needs <70; fine was for margin), declare.
