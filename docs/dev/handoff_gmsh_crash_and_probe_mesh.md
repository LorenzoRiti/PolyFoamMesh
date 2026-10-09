# GMSH crash on periodic CAD + probe-mesh bug

Session dedicated to: making the poly path work well "in a few clicks",
fixing the crash reproduced on `Parte4.stp`, and removing dead code.

## 1. The crash: real cause and three chained fixes

### Diagnosis (verified by inspecting the B-Rep, not assumed)

`Parte4.stp` has periodic surfaces (Cone/Cylinder/BSpline) whose OCC seam
appears **twice** in their own contour — legitimate for a periodic
surface, but it is the point where GMSH's curve-intersection recovery does
not converge. Verified with `getAdjacencies`:

```
surface 85 (Cylinder): curves=[220, 221, 222, 223, 221]   <- 221 twice
surface 86 (BSpline):  curves=[224, 225, 224, 222, 223]   <- 224 twice
surface 88 (Cylinder): curves=[226, 227, 228, 227]        <- 227 twice
surface 69 (BSpline):  curves=[188, 136, 138, 140, 141, 188, 189]
```

With Frontal-Delaunay the recovery escalation loop ("N intersections in the
1D mesh", increasing levels) ends in a **segmentation fault** — a C++
process crash, NOT a Python exception: the app can neither detect nor
recover from it.

### Fix 1 — 2D algorithm: MeshAdapt instead of Frontal-Delaunay

Measured over repeated runs: MeshAdapt traverses the same surfaces but
recovers in 1 iteration in most cases, and where it fails it
raises a normal, catchable Python exception. Never observed worse on
any test case (cylinder, venturi, box).

### Fix 2 — ORDER: the algorithm options must be set BEFORE sizing

Fix 1 alone **had no effect**. The adaptive path calls
`_sample_passage_thickness_field` → `_extract_boundary_trimesh`, which runs
`mesh.generate(2)` *inside* the sizing configuration — so the 2D
meshing (where the crash happens) ran before the algorithm was
set. Measured from the log: **314 surfaces with Frontal-Delaunay against 32
with MeshAdapt** (the latter all GMSH internal fallbacks), i.e.
the requested algorithm was never applied to the pass that matters. After the
move: **321 MeshAdapt, 0 Frontal-Delaunay**.

### Fix 3 — the probe mesh must be coarse, and must be thrown away

`_extract_boundary_trimesh` generates a 2D mesh only to have something to
ray-cast against. Two distinct bugs, both introduced by the thickness-field
feature in this very session:

**3a — the probe was REUSED as the final mesh.** GMSH reuses an existing 2D
mesh when `generate(3)` starts afterwards. Verified directly: a cube
meshed at lc=0.5 and then generated at lc=0.08 **kept all 540 original
triangles**. Consequence: the final surface stayed frozen at the sizing
that existed BEFORE any field was installed, silently throwing away all the
sizing (passage, curvature, small curves) that that
function exists to compute. Fix: `mesh.clear()` after extraction — after
the fix, 540 → 2424 triangles. Regression test:
`test_probe_mesh_is_discarded_so_size_fields_still_apply`.

**3b — the probe ran at full resolution.** Being a second complete 2D
pass, it doubled the 2D cost for information (the local
passage width) that does not need resolution. And it did it right
where GMSH is fragile. Fix: the probe uses `h_max` as the maximum size,
then restores the original value. Effect measured on `Parte4.stp`:
the probe's 2D pass **finishes in 1.25 s** where before the process stayed in the
recovery loop for minutes.

## 2. Dead code removed (only what has ZERO references)

Verified one by one on `src/`, `tests/`, `tools/` and the build `.spec`:

- `scratch_cfmesh_valve_test.py`, `scratch_defeature_test.py`
- `metagpt_spec.json`, `metagpt_impl_result.json`, `save_spec.py`
- untracked: `scratch_split_ab*`, `log.mesh`, `tools/_tmp_dbg_term2.py`,
  `VoroCrustLog.txt`

**NOT removed**, although they look dead — verified reachable:
- `rthook_casadi_dlls.py` → used by `CFMesh-AutoGUI.spec` (`runtime_hooks`)
- `adaptive_cli`, `cloud_mesh`, `mosaic`, `snappy_hex_mesh`,
  `native_poly_bridge`, `disk_cleanup` → few references, all real
- `autopoly/` (C++ sources) → never compiled here, but `autopoly_bridge.py`
  imports it with an explicit Python fallback: deleting it would remove a
  feature, not dead code
- `throat_detector` → hidden checkbox, path still reachable

## 3. Decision made: `collapse_smooth_edges` stays OFF

The collapse of the dual boundary faces (implemented in a previous
session, `tet_poly_dual.py`) would give ~6× fewer prism cells in the BL and
the true STAR-CCM+ topology. **I did not enable it by default**: the measurement on
my own test advises against it — on a curved surface the collapsed polygons
are non-planar and the defects roughly **double** (96 → 198 on the test
sphere, 138 → 264 at higher resolution). For the goal "a mesh as it
should be" it would be a worsening, not an improvement. It remains available as an
explicit option, documented with its numbers.

## 4. NOT done / to verify

1. **Real checkMesh (WSL) before/after** on venturi/valve to quantify
   the effect of the fixes on the final quality — not run to avoid competing for
   CPU/WSL with the other session.
2. **The crash is not 100% eliminated** on `Parte4.stp`: the degeneration of the
   B-Rep is real and GMSH behaves non-deterministically from one run
   to the next on the same file (verified with repeated runs at identical
   parameters). The fixes drastically reduce probability and cost, and
   `main_window` now retries at a coarser level instead of repeating
   the same identical attempt.
3. **STEP repair via OCC ShapeFix**: prototyped and working
   (`ShapeFix_Shape` + `ShapeFix_Wireframe.FixSmallEdges` via OCP, which is
   **already** a dependency — no new package), writes a repaired STEP.
   Not integrated into the app: the meshing test on the repaired file was not
   completed, so I do not have proof that it really solves it. It is the
   natural next step if the crash reappears.
   Prototype: see this session's history (ShapeFix on
   `Parte4.stp` → `Parte4_healed.stp`, 11029 entities written).
