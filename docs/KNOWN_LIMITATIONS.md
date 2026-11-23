# Known limitations

What does not work yet, or only partly, in PolyFoamMesh 2.5.0 — stated plainly,
with the measurements behind each item. Read this before trusting a result.

**The most important rule:** `checkMesh` reporting `Mesh OK` does not guarantee
that a solver will converge on the mesh. Always look at wrongly oriented faces
and at the first-layer report as well as at the verdict.

Detailed, dated measurement logs from earlier releases are in the git history
(`docs/residual_risks.md` before 2.5.0).

## Meshing: speed and sizing

- **Slider = budget, not a guarantee.** The Mesh Fineness slider (10K–20M cells)
  is a cap. The cell estimate shown comes from the tessellated solid volume and
  is omitted (not invented) when the volume cannot be trusted. On a cylinder it
  was accurate near the medium setting (1.11x) and overestimated heavily at
  finer settings (17x at 0.02 m), so treat it as an order of magnitude.
- **GMSH and cfMesh size differently.** GMSH's adaptive path sizes from real
  geometry features, cfMesh from the derived max/min cell sizes; the same slider
  position gives different cell counts.
- **GMSH tet budget** can land above target after the 3-pass correction: 1.2–1.75x
  on most parametric families, 2.9x on a straight pipe.
- **Nearby surfaces / thin gaps.** With the default size ratio (0.5) a thin gap can
  have fewer than 2 cells across. `POLYFOAMMESH_GMSH_MIN_RATIO=0.25` gives 3 cells
  across a 10 mm gap but costs 2–4x the meshing time; dual and checkMesh quality
  with 0.25 has not been measured, so the default is unchanged.
- **Thin trailing edges** (airfoil class, about 1:1500) still time out in the
  adaptive GMSH stage.
- **Size lattice.** The adaptive size field is a precomputed lattice, so tet counts
  differ by a few tens of percent on some sharp-edged geometries versus 2.4.0.
  Quality was measured on the parametric families (48/48 meshed, no timeouts) but
  not yet on every real geometry. `POLYFOAMMESH_SIZE_LATTICE=0` restores the
  previous field.
- **Timeouts.** GMSH volume meshing has a 3600 s limit in every path (an Auto run
  hands over to fTetWild after 120 s); `gmshToFoam` has 900 s. The failure is
  reported in the log.
- **Very large or very thin parts** can take more than 25 minutes, and some CAD
  (for example an exhaust manifold) is meshed by neither GMSH nor fTetWild.
- **Very large meshes** (over about 8 million faces) skip the boundary layer through
  a memory guard and are delivered as dual-only; above about 500 thousand cells the
  boundary layer gets slow.
- **Run-time estimate** shown at the start is an interval (0.7x–1.9x), not a promise.

## Polyhedral dual

- **Sharp concave throats** (venturi, valve class) keep a small number of
  non-orthogonal faces, inverted face pyramids and high local skew. On a collapsed
  valve, `checkMesh -allGeometry -allTopology` still fails 8 checks (340 wrongly
  oriented face pyramids, 10 non-orthogonality errors, 28 highly skew faces). The
  mesh is kept and shown with a warning; it is not silently replaced by tets.
- **Solver divergence with many narrow gaps.** On a pipe with 89 faces and more
  than 50 narrow gaps, the polyhedral dual created wrongly oriented faces and the
  solver diverged even though the raw tets were clean; removing the boundary layer
  did not help. A tet mesh without the dual was valid but cost 8 million tets.
  This is not yet a product policy.
- **Verdict can flip by 1–3 faces** between tet generations, because the GMSH tet
  generation and the size suggestion have a non-fixed random part.

## Boundary layer

- **Coverage is not always 100 %.** On the collapsed valve the layer closes with
  81 % wall coverage, with the requested first layer honoured. Some real parts
  still end `Failed` by 1–3 faces (pipes with the layer present; the wind-tunnel
  NACA case with 2–3 pyramids).
- **Which patches get layers.** One module decides what is a wall. If neither the
  name, the type nor the geometry identifies a wall, only known inlets, outlets
  and symmetry planes are excluded; if nothing is left, the layer is skipped
  rather than extruded into inlets. An explicit "apply to all patches" is honoured
  literally.
- **First layer** is capped by local support and by the cell size; the report
  states requested versus achieved.

## Wind tunnel

- Refinement boxes on the fTetWild path use a coarse grid (one point per maximum
  cell), so the cell budget is approximate.
- On a NACA 0024, checkMesh still reports 2–3 pyramid faces, with and without
  refinement boxes.

## Viewer

- Volume meshes above about 2 million cells are decimated for display; the mesh on
  disk is never modified. The Section Cut view shows the true internal cells.
- The viewer needs `foamToVTK` inside WSL2/OpenFOAM; when it is missing the viewer
  shows a ParaView hint instead of the mesh.

## Platforms and packaging

- **Windows is the verified platform.** Linux (native OpenFOAM, no WSL) is
  implemented and covered by unit tests and CI, but has not been smoke-tested on a
  real Linux machine; there is no AppImage or `.deb`, only a tarball.
- **No clean-machine install check.** The release gate verifies module equivalence
  of the packaged bundle, not an install on a second computer. The 2.5.0 package is
  a portable folder/zip; the Inno Setup installer is not assembled for this version.
- The REST API server is experimental and still uses the legacy workflow.

## Verification status (2.5.0)

- Fast test suite: 1,928 tests; slow and WSL suites: 23/23; release gate passing.
- Parametric geometry families (13 families, scale 1 cm to 10 m): 48/48 meshed
  except the thin-trailing-edge class above.
- The real-geometry corpus used for the measured numbers in the changelog is
  private and not distributed.
