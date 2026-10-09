# Evaluation of VoroCrust as a possible replacement for the native mesher

Working document — state updated as the phases proceed.
Updated as of 2026-08-07.

## Why

The native cut-cell mesher (`core/native_mesher.py` → `core/hex_poly_dual.py`,
see `docs/dev/poly_mesher_STATUS.md`) has two structural limits that would require
risky rewrites to be solved: snapping on walls parallel to the
grid (the dual fails with "Non-manifold primal edge" on geometries such as a
box) and local refinement (no octree). Both problems are
structural to the Cartesian/cut-cell approach, not bugs to be fixed.

[VoroCrust](https://vorocrust.sandia.gov/) (Sandia National Labs, Abdelkader
et al., *"VoroCrust: Voronoi Meshing Without Clipping"*, ACM Trans. Graph.
2020) uses a different approach — placement of Voronoi seeds guided by a
sizing field with mathematical guarantees, **no Cartesian grid** — which
structurally eliminates both problems. Open-source code:
[sandialabs/vorocrust-meshing](https://github.com/sandialabs/vorocrust-meshing),
**BSD-3-Clause** license (commercial use ok), C++/CMake/OpenMP.

**Known limit from the paper** (stated by the authors themselves, sec. 4 "Future
Work"): as of 2020 VoroCrust **does not generate boundary layers** — the same gap as
our native path today. To be re-verified against the current code (2026).

## Phases

Each phase is blocked by the previous one (see task tracker). Do not start a
phase until the previous one has an outcome recorded below.

### Phase 0 — Build in WSL + OneBox test
**Goal**: verify that it compiles and that their own test case
`OneBox` (box with flat walls — our identical failure case)
produces a valid mesh.
**Why WSL and not native Windows**: the Windows build instructions in their
repo are flagged as "under development"; WSL is already the environment this
project uses reliably for OpenFOAM/cfMesh.
**Status**: IN PROGRESS.

### Phase 1 — Verification on our real case
Export one of our STLs (the one that breaks `hex_poly_dual`) to `.obj`,
run it with VoroCrust, confirm that it solves snapping on our own
geometry (not just on their synthetic test).
**Status**: blocked by Phase 0.

### Phase 2 — Output → OpenFOAM converter
The output format (probably Exodus) must be converted to
`constant/polyMesh`. `meshio` (already a project dependency) supports Exodus —
reuse, not a new dependency. Analogous to `core/mesh_converter.py` (GMSH .msh →
OpenFOAM) already existing.
**Status**: blocked by Phase 1.

### Phase 3 — Integration as an experimental mesher
Subprocess wrapper (same pattern as `core/gmsh_wrapper.py`), bridge in the style of
`commercial/native_poly_bridge.py`, entry in the Tools menu (same "experimental, opt-in"
treatment given to today's Native Poly — not in the standard mesher combo).
**Status**: blocked by Phase 2.

### Phase 4 — Comparison on real geometries
checkMesh, times, robustness on the user's real geometries vs the current
GMSH Poly CFD path. Final decision: does it replace the native cut-cell or remain
a parallel experimental path.
**Status**: blocked by Phase 3.

## Log

- 2026-08-07: plan written, Phase 0 started.
