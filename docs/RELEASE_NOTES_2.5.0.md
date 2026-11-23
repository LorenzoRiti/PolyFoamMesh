# PolyFoamMesh 2.5.0 — release notes

Release date: 2026-11-23 (prepared locally; tag and publication pending).
Full details and measurements: [CHANGELOG.md](../CHANGELOG.md).
Known limits: [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

## Highlights

- **Virtual wind tunnel** (Tools > Create Wind Tunnel around Object...): the
  loaded CAD becomes the object, the fluid is the box around it, with
  refinement boxes on the object and in the wake.
- **Faster, more predictable meshing**: adaptive GMSH size field as a
  precomputed lattice (pipe 109 -> 22 s, `lam.stp` 134 -> 22.5 s), fTetWild
  growth capped (~5x the uniform mesh), GMSH time budget before the fTetWild
  fallback, anti-loop circuit breaker. DN1000: tet 6.7 M -> 154 k tetrahedra
  (51 min -> 38 s), dual 188 -> 30 s.
- **More geometries end with a valid mesh and boundary layer**: on the 26-run
  production-chain corpus, `Mesh OK` with boundary layer went from 12 to 18
  and total time from 806 to 469 s, with no case getting worse.
- **Patch handling**: Role column (auto/inlet/outlet/symmetry/wall) that drives
  boundary layers and BCs; STEP patches grouped by the true angle between CAD
  faces instead of cutting triangles.
- **Robustness fixes**: degenerate CAD edges (sphere/fillet poles) no longer
  stall the 2D mesher (filleted boxes: 4/4 timeouts -> ~6 s); multi-pass
  tet-budget correction (`straight_pipe` 11.9x -> 2.9x, `venturi` 4.0x -> 1.7x
  over budget).
- **Better run feedback**: pre-run summary of stages and expected duration,
  load-time warnings (thin shell, very large estimates), explanatory tet-chain
  log.

## New options

| Variable | Effect |
|---|---|
| `POLYFOAMMESH_GMSH_MIN_RATIO` | Min/max adaptive size ratio (default 0.5). 0.25 resolves thin gaps (3 cells across 10 mm) at 2-4x meshing time. |
| `POLYFOAMMESH_SIZE_LATTICE=0` | Disable the size lattice (restore the previous field). |
| `POLYFOAMMESH_AUTO_GMSH_BUDGET_S` | GMSH time budget in Auto (default 120 s, 0 = off). |
| `FTETWILD_ADAPTIVE_BUDGET` | fTetWild adaptive growth cap (0 = off). |

## Validation

Fast suite 1,928 tests, slow + WSL suites 23/23, release gate (versions, spec
static checks, smoke, repo-clean, tet -> dual E2E, PyInstaller bundle) passing,
parametric families 48/48 meshed. Not verified: clean-machine install, Linux
runner, Inno Setup installer (not assembled on the development machine).

## Known limits (short)

`airfoil`-class thin trailing edges still time out in adaptive GMSH; the sharp
concave throat class (venturi, valve) still fails some checkMesh checks in
strict mode; dual quality with the size lattice is not yet measured on all real
geometries; nearby-surface refinement is opt-in.
