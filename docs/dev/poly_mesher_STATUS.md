# Polyhedral mesher status (CFMesh-AutoGUI 2.1.0)

Technical document — what exists, how it works, what has been verified,
limits and next steps. Updated as of 2026-08-07.

## 1. Goal (from the reference paper)

The user wants the **STAR-CCM+/ANSYS Fluent** topology:
[Sosnowski, Krzywanski, Gnatowska, E3S Web Conf. 14, 01027 (2017)](https://www.e3s-conferences.org/articles/e3sconf/pdf/2017/02/e3sconf_ef2017_01027.pdf)
defines the polyhedral mesh as the **TET → POLY conversion**: new
faces between the edge centroids and the face centers, connected to the
cell center (the **barycentric dual**), with merging of adjacent faces.

**Correct path: tet (GMSH) → dual → 100% irregular poly.**
The median dual of a **Cartesian** grid is self-dual (cubes at the
center) and does NOT give the desired topology — the "Native Poly"
(Cartesian) path was removed from the GUI for this reason.

## 2. Active path: CFD Poly (GMSH, no-WSL)

```
STL/STEP → GMSH volume (tet) → checkMesh (WSL, gate) → TetPolyDualConverter
         → constant/polyMesh 100% poly (3-10 faces/cell) → final checkMesh
```

- Generation: **GMSH + our dual** (`core/tet_poly_dual.py`,
  824k tet → dual in ~33 s, pure numpy).
- WSL is needed **only** for the checkMesh validation (the gate "first
  checkMesh PASS, then dual"), never to generate.

### End-to-end verification (venturi.stl, coarse)
| Step | Result |
|---|---|
| GMSH tet | 39,792 cells, 1 s |
| checkMesh (tet) | **Mesh OK** (skew 2.89, non-ortho 54.6°, pyramids OK) |
| dual | 8,074 cells 100% poly (4-10 faces, no hex) |
| checkMesh (poly) | **Mesh OK** |

## 3. Fixes applied today (commits 0234bae → a829131 → 16e52e1 → 80d9bbf → 44b9613/8a9312e)

| Fix | File | Effect |
|---|---|---|
| `forReparametrization=True` | `core/gmsh_wrapper.py` | unblocks "Invalid exterior boundary mesh for parametrization" on closed discrete STLs |
| `n_int = count(neighbour >= 0)` | `core/tet_poly_dual.py` | dual on tet with padded neighbour (msh_to_of_polymesh) |
| cleanup of contained coplanar triangles | `core/native_mesher.py`, `core/stl_writer.py` (GMSH export) | STLs with overlapping triangles (e.g. a duct with a badly cut hole) no longer break GMSH/HXT |
| removal of "Native Poly" from the GUI | gui/params_panel, main_window, mesh_engine | the Cartesian dual was confusing; the right path remains |
| one-dir spec + gmsh DLL + `_cli_main` | CFMesh-AutoGUI.spec, app.py, gmsh_wrapper | frozen exe: GMSH works (verified: 46,496 elements) |

## 4. Relevant modules

- `core/tet_poly_dual.py` — TET→POLY dual (the engine of the CFD Poly path).
- `core/hex_poly_dual.py` — generic median dual (hex/poly, used by the
  removed native path; kept for tests/bench).
- `core/native_mesher.py` — castellated cut-cell + scanline classification
  (module of the native path; used by the tests, no GUI reference).
- `core/gmsh_wrapper.py` / `gmsh_subprocess.py` — GMSH tet generation.
- `core/mesh_converter.py` — `.msh` → OpenFOAM polyMesh.
- `commercial/mesh_engine.py` — algorithm selection (9 algorithms, no
  NativePoly).
- `commercial/native_poly_bridge.py` — bridge of the native path
  (tests/bench only).

## 5. Known limits (measured, honest)

1. **Self-intersecting STLs** (triangles that cross, hole not
   cut in the CAD): GMSH fails with "PLC Error: two segments
   intersect". The coplanar cleanup removes the contained parts but does not recreate the
   holes — the STL must be re-exported from the CAD.
2. **The dual of the tet requires clean cells**: GMSH produces them (checkMesh
   PASS), so ok.
3. **Quality of the native Cartesian-dual mesh** (removed): skew 7.58,
   non-ortho 44.6 — it is not the requested topology.
4. **WSL paths** (cfMesh, checkMesh): require WSL2+OpenFOAM; the
   demo package does not include it.

## 6. Suggested next steps

1. ~~**Snapping/quality phase** for the native path~~ — **done**
   (commit `67fa285`, 2026-08-07): `hex_poly_dual.py` now calls
   `poly_smoother.smooth_dual_mesh` with the same keep-best contract already
   used by `tet_poly_dual.py` (accepted only if the total defects do not
   worsen and no cell volume becomes non-positive). Measured on an
   icosahedral sphere via `native_mesher` → `hex_poly_dual`: subdiv=2/
   cell=0.15, total defects 33 → 6; subdiv=3/cell=0.12, 249 → 231.
   It does not close the skew to zero by itself — point 6.1 below still stands.
2. **Clean-machine test** of the installer (no VM available in
   the environment; verified for self-containment + operation from the
   installed path).
3. Authenticode signing to remove the SmartScreen warning.

### 6.1 Toward an scFLOW-style mesher (voxel/cut-cell → automatic poly)

Comparison with scFLOW (Cradle CFD) of 2026-08-07: same architectural
principle (Cartesian grid → cut-cell → poly), same path
as our `native_mesher.py` + `hex_poly_dual.py`. State updated
after the 2026-08-07 implementation (commits `67fa285`..`7273a8d`):

- **Phase 4a — quality-driven smoothing on the cut-cell dual**: DONE
  (§6 point 1 above).
- **Phase 4b — graded grid**: DONE, but with a narrower purpose
  than an octree. `native_mesher.__init__` accepts `margin_growth` (>1.0
  = opt-in): inside the tight bounding box of the surface the
  spacing stays EXACTLY uniform `cell_size` (surface capture
  bit-for-bit identical); in the margin/pad band around it grows
  geometrically. It solves the cost of an extended domain for external
  flow (verified: margin=8·cell, sphere — candidate grid
  23³=12,167 → 17³=4,913 points, same final mesh: 260 cells, same
  volume, same closure). It does **not solve** local refinement near
  a small detail INTERNAL to the bbox, far from the domain boundary —
  that remains a true octree with hanging-node/T-junction handling
  (face split on the coarse side), not attempted: the risk is
  silently producing non-conforming cells, without a dedicated face
  merging algorithm and a validation campaign.
- **Phase 5 — BL sizing wired**: DONE, but only the COMPUTATION. `NativeMesher(
  ..., flow=FlowConditions(...))` calls `commercial/bl_engine.py`
  (already existing, already used by the GMSH/cfMesh path) and populates
  `NativeMeshResult.bl_recommendation` (n layers, first-layer height,
  growth rate, collision ratio per patch). It does **not insert prismatic
  cells**: that extrusion (wall faces + topological surgery
  on the adjacent cut-cell) is a new algorithm to be
  written from scratch, estimated as the largest of the three pieces — not attempted.
- **Phase 6 — exposure in the bridge**: DONE to the extent that a
  consumer exists: `commercial/native_poly_bridge.py` (`NativePolyParams
  .margin_growth`/`.flow`, `NativePolyResult.bl_recommendation`)
  wires 4b and 5 end-to-end (verified via `run_native_poly`). It is NOT
  "per-part/region" refinement (it needs the octree of point 4b extended) and
  it does NOT reactivate the GUI button — that removal (`16e52e1`) remains a
  deliberate product choice, not reopened here.

What remains, in order of increasing estimated cost/risk: 1) a true
octree with hanging-node for internal local refinement; 2) per-part/region
refinement on top of the octree; 3) native BL prismatic extrusion.
None of the three is "connecting existing pieces" like 4a/4b/5/6 — they are
new geometric algorithms that deserve a dedicated session with a
regression campaign, not a patch in the same work round.
