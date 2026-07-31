# Poly Workflow Part 2: Safe Volumetric Tetra To Poly

## Status (2026-07-31, Claude Code — owns this plan going forward)

Two leftover-tet merge strategies measured empirically on the same repro
(block-with-cylindrical-bore, 251,902 tets, medium detail, WSL checkMesh),
against the mutual-agreement-only baseline (79 wrong-oriented faces, 1 failed
check, 78.9% residual bare tets):

1. **Greedy chained leftover merge** (`_merge_leftover_tets`, this session):
   residual tets 78.9%->0.1%, but 79->6,471 wrong-oriented faces, NEW skewness
   failure (max 8,170) and NEW non-orthogonality failure. Rejected, disabled
   (implemented, not called from `run()`).
2. **Disjoint pair matching** (`_augment_disjoint_pair_matching`, found
   already on disk from the stopped opencode-loop process): residual tets
   78.9%->19.8%, 79->1,056 wrong-oriented faces (13x, still only 1 failed
   check — no new failure categories, skewness/non-orthogonality stay
   healthy). Better than (1) but still a regression on raw misoriented-face
   count. **User decision 2026-07-31: keep the higher-quality baseline over
   higher poly coverage.** Call site reverted (`fd7bc29`); re-verified after
   reverting: 74 wrong-oriented faces, 1 failed check, skewness 1.72 —
   matches baseline within GMSH's own run-to-run variance. Method stays
   implemented, not called (same as (1)).

`_merge_leftovers_safe` (also found on disk, guards against duplicate-face
merges) exists but is not called either — untested against this repro.

**Committed state as of `fd7bc29`**: TerminalFaceConverter ships at the
mutual-agreement-only baseline (~21% poly coverage, 79 wrong-oriented faces,
1 failed checkMesh check). All three merge strategies above exist in
terminal_face.py as documented, unused starting points. If higher poly
coverage is wanted later, the disjoint-pair-matching direction is the
better-performing of the two tested so far — the open problem is why its
non-mutual second-chance pairs orient worse, not the matching/pairing logic
itself.

Neither of TetPolyVolumeConverter (this doc's Recommended Algorithm) nor
`tet_poly_volume.py` exists yet — Part 2's dual/aggregation design below is
still unimplemented. Everything shipped so far is inside the existing
`TerminalFaceConverter` (`terminal_face.py`), not a new module.

## Objective

Implement a real volumetric tetrahedral-to-polyhedral conversion. The output
must be polyhedral in the interior and on the boundary, preserve CFD patches,
and pass OpenFOAM quality checks before it is shown as the final mesh.

## Non-Goals

- Do not use `polyDualMesh` on a tetrahedral input.
- Do not convert only the surface.
- Do not claim success from return code alone.
- Do not enable the converter on mixed tet+prism input until a hybrid algorithm
  is implemented.
- Do not re-enable the previously measured greedy leftover merge without a
  quality gate.

## Input Contract

Read the original GMSH `.msh` directly when possible. Do not depend on a
partially rewritten OpenFOAM `polyMesh` for the primary conversion path.

Required input:

- points;
- tetrahedral volume cells;
- surface triangles and their physical patch IDs;
- optional cell zones.

Reject input containing unsupported volume elements unless the hybrid path is
explicitly selected.

## Recommended Algorithm

Use a boundary-aware weighted dual/aggregation pipeline:

```text
GMSH tetra mesh
  -> validate tetra cells and physical surfaces
  -> improve/validate tetra quality
  -> compute one dual point per tetra (circumcenter when safe,
     centroid/weighted center for obtuse tetra)
  -> construct dual faces from primal edges
  -> construct dual cells around primal vertices
  -> clip/reconstruct boundary cells against original surface triangles
  -> preserve physical patch IDs
  -> orient every face using owner/neighbour cell centers
  -> validate manifold topology and cell volumes
  -> optional conservative polyhedral smoothing
  -> checkMesh
  -> atomically promote candidate case
```

The boundary step is mandatory. A dual mesh built only from interior adjacency
will produce a visually polyhedral surface but an invalid or tetrahedral
interior.

## Implementation Structure

Create a dedicated module instead of expanding the current experimental
converter:

```text
src/cfmesh_autogui/commercial/tet_poly_volume.py
```

Suggested API:

```python
@dataclass
class TetPolyResult:
    success: bool
    tetra_cells: int
    poly_cells: int
    residual_tetra: int
    boundary_faces: int
    wrong_oriented_faces: int
    negative_cells: int
    errors: list[str]


class TetPolyVolumeConverter:
    def __init__(self, msh_path: Path, candidate_case: Path): ...

    def run(self) -> TetPolyResult: ...

    def validate_input(self, mesh) -> None: ...

    def build_dual_points(self, points, tetra_cells): ...

    def build_primal_adjacency(self, tetra_cells): ...

    def build_dual_faces(self, points, tetra_cells, dual_points): ...

    def build_dual_cells(self, adjacency, dual_faces): ...

    def reconstruct_boundary(self, surface_faces, physical_tags, dual_cells): ...

    def orient_and_validate(self, cells, faces): ...

    def write_candidate(self, mesh_data, candidate_case): ...
```

## Topology Invariants

Before running `checkMesh`, validate in Python:

1. Every output cell has at least four faces.
2. Every output face is referenced by one or two cells only.
3. A face referenced once is a boundary face.
4. A face referenced twice has distinct owner and neighbour.
5. Owner and neighbour indices are in range.
6. Internal faces are sorted in OpenFOAM upper-triangular order.
7. Boundary faces are contiguous and match `startFace`/`nFaces`.
8. Every output cell has positive signed volume.
9. Every physical surface triangle belongs to exactly one boundary patch.
10. No original surface face is silently dropped.

If any invariant fails, delete only the temporary candidate and return failure.

## Boundary Reconstruction

For each primal boundary triangle:

1. Identify its adjacent tetrahedron.
2. Identify the dual cell(s) touching that boundary region.
3. Clip the dual cell against the triangle/surface patch.
4. Emit the original boundary polygon with its physical patch name.
5. Emit the internal transition faces with consistent winding.

Do not invent a single `defaultFaces` patch when physical patch data exists.

## Quality Gate

The candidate is accepted only if all of the following are true:

```text
checkMesh: Mesh OK
residual tetrahedra = 0
negative-volume cells = 0
open cells = 0
wrong-oriented faces = 0
boundary patches preserved
```

Recommended warning targets:

```text
max non-orthogonality <= 70 degrees
max skewness <= 4
max aspect ratio <= 1000
```

The GUI must show the actual cell-type breakdown, not only total cells.

## Adaptive Refinement Integration

Geometry-based refinement and solution-based AMR are different stages:

### Before conversion

- curvature;
- sharp features;
- proximity/gap width;
- user-selected surfaces/regions;
- boundary-layer requirements.

These define the target tetra size field.

### After a first CFD solution

- normalized jumps in `U`, `p`, `T`;
- velocity gradient/vorticity;
- Hessian or residual indicator;
- optional adjoint indicator for drag/pressure-drop targets.

Refine the tetra base, remap fields conservatively, then repeat tet-to-poly.
Do not attempt generic `dynamicRefineFvMesh` on arbitrary polyhedra; that tool
is primarily a hex refinement engine.

## GUI Integration

The existing GUI remains the shell. Add only a new backend stage:

```text
Tetrahedral (FEM)
  -> pure tet case

Polyhedral (CFD)
  -> pure tet case
  -> TetPolyVolumeConverter
  -> candidate checkMesh
  -> promote candidate
```

The progress log must show:

```text
[poly] Input: N tetrahedra, M boundary faces
[poly] Dual points built
[poly] Boundary cells reconstructed
[poly] Candidate: K polyhedra, residual tetra=0
[poly] checkMesh: Mesh OK
[poly] Promoted final candidate
```

On failure:

```text
[poly] FAILED: candidate rejected
[poly] Original tetrahedral mesh preserved
```

## Testing Plan

### Unit tests

- two tetrahedra sharing one face -> one valid poly cell;
- cube/box constrained tetra mesh -> all volume cells polyhedral;
- boundary patch preservation;
- face orientation and owner/neighbour invariants;
- negative-volume rejection;
- candidate rollback on write failure.

### Integration tests

1. Small pure-tet pipe.
2. Pipe with contraction.
3. Block with cylindrical bore.
4. Real `Parte4.stp` geometry.
5. 1M-cell case.
6. 10M-cell case, if hardware permits.

### Acceptance evidence

For every test, save:

- input `.msh` cell counts;
- output poly cell counts;
- residual tetra count;
- OpenFOAM cell-type breakdown;
- complete `checkMesh` output;
- boundary patch comparison before/after;
- mass/volume comparison before/after.

## Implementation Order

1. Finish Part 1 and freeze the valid tetra path.
2. Implement input reader directly from `.msh`.
3. Implement dual topology in memory only.
4. Add boundary reconstruction.
5. Add invariant validator.
6. Add atomic candidate writer.
7. Run `checkMesh` on candidate.
8. Integrate the worker into the existing GUI.
9. Expose Polyhedral (CFD) only after the small pure-tet acceptance test is
   green.
10. Add large-mesh performance tests and the 20M operational budget.

## Critical Rule

Until the candidate reaches `Mesh OK` with zero residual tetrahedra, the app
must keep the original tetrahedral mesh and must not display a green completed
poly workflow.

## Complex Geometry: Conversion Must Be Incremental

## Regression Guard Before Poly Work

The poly test is invalid if the preceding tetra worker did not start. Always
distinguish these log states:

```text
run started, no GMSH stage      -> GUI/thread routing regression
GMSH stage, no worker thread    -> worker setup regression
worker thread, no subprocess    -> subprocess dispatch regression
subprocess, no .msh              -> GMSH/geometry/sizing failure
.msh and tet checkMesh pass     -> only now test tet-to-poly
```

Before every poly implementation change, run the Part 1 tetra smoke test. If
it fails, stop Part 2 and repair the tetra path first. The poly worker must be
loaded only after the tetra candidate has passed `checkMesh`.

The following invariant must be tested in the GUI routing code:

```python
if mesher == "gmsh_direct":
    assert poly_worker_not_started
    assert polyDualMesh_not_started
```

The only allowed output of the tetra branch is a valid tetrahedral OpenFOAM
case. Poly conversion is a later, isolated candidate operation.

The poly worker must be protected from duplicate starts. A second click, a
stale checkMesh callback, or a retry from an older run must never launch a
second conversion against the same case. Use the run id plus worker/thread
state guards, and ignore stale callbacks before any filesystem write.

Never use a previously generated case to diagnose a regression. Old cases can
contain partial `faces`, `owner` or cached VTU files from an interrupted
conversion. Every regression test creates a new timestamped case and saves the
full log.

The conversion of a complex mesh must not be one opaque operation. Split it
into independently observable stages:

```text
P1 read original GMSH .msh
P2 validate volume element types
P3 build primal adjacency
P4 compute dual points
P5 build internal dual faces
P6 reconstruct/clip boundary cells
P7 orient and validate topology
P8 write poly candidate atomically
P9 run checkMesh
P10 promote candidate
```

The progress log must print the stage, elapsed time, processed count and
heartbeat. For example:

```text
[poly 4/10] dual points 120000/250000
[poly 5/10] internal faces 480000/900000
```

### Memory and cancellation

For large cases, adjacency must be compact and incremental. Do not create
multiple full copies of every face/cell structure. Use integer arrays and
release the GMSH/meshio object before building the dual candidate.

The converter must:

- run outside the Qt GUI thread;
- expose a cancellation event checked between chunks;
- stop within a bounded interval after cancellation;
- remove only its temporary candidate directory;
- leave the original tetra case untouched.

### Complex boundary strategy

Boundary reconstruction is the likely slowest and most failure-prone stage.
Process it patch by patch and validate each patch before continuing:

1. map physical GMSH surface IDs to patch names;
2. collect boundary triangles by patch;
3. find the adjacent tetra and dual cell for each triangle;
4. clip or rebuild the boundary poly face;
5. validate one owner per boundary face;
6. record progress and continue with the next patch.

If one patch is invalid, reject the candidate. Do not write a partial boundary
file.

### Timeout policy

Use separate budgets for each stage. A timeout must identify the stage:

```text
poly conversion timeout during boundary reconstruction, patch=wall_03
```

Never report a generic `poly failed` message when the actual problem is a
specific patch, memory exhaustion, malformed element block, or topology
invariant.

### Large mesh policy

The supported operational range is up to 20M tetra/poly cells, but the code
must not pretend that every computer can run 20M cells. Before P1, calculate a
resource estimate and require confirmation for expensive runs. The 20M value is
an upper supported target, not a silent auto-coarsening rule.

The requested cell size must remain visible in the log. If an effective size
differs, log both values and the reason:

```text
requested max=0.0010 m
effective max=0.0010 m
estimated cells=8,400,000
budget=20,000,000
```

### Failure and rollback matrix

| Failed stage | Allowed result |
|---|---|
| read/validate | original tetra preserved |
| dual construction | original tetra preserved |
| boundary reconstruction | original tetra preserved |
| topology validation | candidate deleted, original preserved |
| checkMesh | candidate rejected, original preserved |
| promotion/copy | original and candidate preserved for diagnosis |

There is no path in which a failed complex-geometry conversion can replace the
active tetra mesh with an incomplete poly mesh.
