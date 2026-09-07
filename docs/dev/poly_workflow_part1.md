# Poly Workflow Part 1: Stabilize The Tetra Base

## Status (2026-07-31, Claude Code — owns this plan going forward)

External opencode-loop process stopped (was editing these files concurrently).
Claude Code now sole owner; this doc is updated as work lands, not left static.

Committed today (`acc0bae` and earlier): units bug (GMSH ignored declared STEP
unit, 1000x scale error), Cancel not killing GMSH subprocess, mesh-size clamp
vs domain scale, mesh sizing panel redesign (master slider), removed stale
poly-autoenable, missing frozen-build CLI dispatch.

Committed (`fd7bc29`), reviewed from the stopped opencode-loop process's
uncommitted work: overlap-run guard (`_gmsh_thread`/`_feature_thread`/
`_checkmesh_thread`), BL-disabled-for-Polyhedral guard, GMSH stage/thread-
start diagnostic logs, quality-PASS vs "Failed N mesh checks" contradiction
fix, polyhedral_preprocessor.py FoamFile header fix, non-tet-input hard
failure (was a silent fallback). No conflict with prior commits.

## Objective

Create a reproducible, valid tetrahedral OpenFOAM case before attempting any
tetra-to-poly conversion. Part 1 must not replace a valid tetra mesh with a
poly mesh.

## Current Problem

The previous workflow mixed several incompatible operations:

```text
GMSH tetra -> gmshToFoam -> polyDualMesh/experimental terminal-face
```

`polyDualMesh` is a dualizer for Cartesian/hex meshes, not a general
tetrahedral-to-polyhedral converter. The experimental terminal-face path can
produce a mixed tet/poly mesh and is not accepted as a final CFD mesh until
all quality gates pass.

## Required Behavior

1. `Tetrahedral (FEM)` always produces and preserves a tetrahedral volume mesh.
2. `Polyhedral (CFD)` starts from a pure tetrahedral volume with boundary
   layers disabled unless a dedicated tet+prism converter is available.
3. A failed conversion never overwrites the original tetra case.
4. A mesh is not reported as PASS when `checkMesh` reports failed checks.
5. The GUI reports requested and effective cell sizes and the final cell count.

## Files In Scope

- `src/cfmesh_autogui/core/gmsh_wrapper.py`
- `src/cfmesh_autogui/core/openfoam_runner.py`
- `src/cfmesh_autogui/gui/main_window.py`
- `src/cfmesh_autogui/gui/params_panel.py`
- `src/cfmesh_autogui/core/terminal_face.py`
- `tests/test_openfoam_runner.py`

Do not modify the tet source mesh in place during diagnostics. Use a new case
directory for every test.

## Cell Size Contract

The GUI must have one explicit contract:

### Manual mode

- Max Cell Size is the requested maximum size.
- Min Cell Size is the requested minimum size.
- Adaptive sizing is disabled.
- GMSH receives both values unchanged, unless the user explicitly exceeds the
  20,000,000-cell safety policy.

### Adaptive mode

- The adaptive size field is authoritative.
- Max/Min fields are informational or disabled in the UI.
- Max Cells Target is the budget.
- The log must report the effective size field and budget.

Never silently use Auto-Suggest values as manual input while adaptive mode is
active.

## Tetra Acceptance Gate

After `gmshToFoam`, run `checkMesh` before any poly operation. Record:

- total cells;
- tetrahedra, prisms, wedges, pyramids and polyhedra counts;
- boundary patch count and names;
- open cells;
- negative volumes;
- wrong-oriented faces;
- max skewness and max non-orthogonality.

For the pure tetra branch the required result is:

```text
tetrahedra > 0
prisms = 0
wedges = 0
pyramids = 0
polyhedra = 0
open cells = 0
negative volumes = 0
Mesh OK
```

If this gate fails, stop the pipeline and keep the tetra case.

## Safe Case Handling

Every topology-changing operation must write to a sibling temporary case:

```text
case/
case.poly_candidate_tmp/
```

Only after the candidate passes all checks may it replace the active mesh.
Never write partial `points`, `faces`, `owner`, `neighbour` or `boundary`
files into the active case.

## Tests For Part 1

1. Unit test manual sizing propagation from ParamsPanel to GmshVolumeWorker.
2. Unit test adaptive/manual mode selection.
3. Unit test that `Failed N mesh checks` makes `MeshQualityReport.passed`
   false.
4. Integration test on a fresh GMSH case with no boundary layers.
5. Integration test on a GMSH case with boundary layers: Polyhedral mode must
   disable or reject the incompatible prism input clearly.
6. Cancel test: no `gmsh`, `gmshToFoam`, `polyDualMesh`, `checkMesh` or
   `foamToVTK` process may remain after cancellation.

## Definition Of Done

Part 1 is complete only when:

- a fresh tet case passes `checkMesh`;
- the GUI cell sizes are visible in the generated log and match the request;
- a failed poly conversion leaves the tet mesh unchanged;
- no stale background process remains after success, failure or cancel;
- the application can be rebuilt and the focused tests pass.

Part 1 must be completed before implementing Part 2.

## Complex Geometry: No-Freeze Protocol

## Regression Triage: Tetra Must Work First

### If the log stops at `Starting meshing run`

This is not a GMSH mesh-quality problem yet. It means the GUI has not reached
the GMSH worker. The next required messages are:

```text
GMSH stage entered
GMSH worker thread started
[gmsh] Running volume mesh generation in subprocess
```

If those messages are absent and there is no child GMSH/Python worker process,
debug the GUI transition (`mesher_type`, WSL check callback, feature-detect
callback, and `QThread.start`) before changing cell sizes or poly code.

If `GMSH stage entered` exists but `worker thread started` does not, the worker
construction or thread setup raised an exception. The exception must be logged
and the UI must be reset; it must not leave the Generate button disabled.

If the worker started but no child process exists, inspect the worker command
construction and frozen/source dispatch before touching the mesher algorithm.

### Observed regression evidence

On 2026-07-31 a real GUI run logged:

```text
Starting meshing run #6
Meshing run #6: mesher=gmsh_direct poly_conversion=False
```

but did not log `GMSH stage entered`, did not log `GMSH worker thread
started`, and had no GMSH child process. This is classified as a GUI stage
handoff regression, not as a tetrahedral algorithm failure. The added stage
logs must be present in the next build before any poly investigation continues.

### Multiple-run regression fixed

The GUI previously guarded only `RetryRunner.is_running`. GMSH direct uses
different workers, so repeated clicks could start overlapping GMSH, feature and
checkMesh jobs. That creates stale callbacks, apparent freezes and competing
case outputs.

The start guard must inspect at least:

```text
_gmsh_thread
_feature_thread
_checkmesh_thread
```

If one is running, a new run is rejected with a visible warning. Cancel must
call the worker `cancel()` before cleaning up its QThread. The next build must
log both:

```text
Dispatching GMSH worker
GMSH worker thread started
```

If `Tetrahedral (FEM)` stops working after a poly change, the poly work is
blocked. Do not debug both paths at once.

### Mandatory first comparison

Run a fresh tetra case with:

```text
mesher = Tetrahedral (FEM)
poly conversion = OFF
boundary layers = OFF
manual sizing = ON
auto refinement = OFF
```

Record these exact transitions:

```text
GMSH worker started
GMSH .msh written and non-empty
gmshToFoam started
gmshToFoam return code
constant/polyMesh/points, faces, owner, neighbour, boundary sizes
checkMesh started
checkMesh finished
```

The tetra test must not instantiate `TerminalFaceWorker`, `PolyDualWorker`,
`polyDualMesh`, or any candidate writer. If any of those appears in the log,
the mesher decision state is wrong.

### Required failure classification

- No `.msh`: GMSH sizing/geometry stage.
- Empty/partial `.msh`: GMSH write or cancellation cleanup.
- `gmshToFoam` non-zero: OpenFOAM conversion stage.
- `.msh` valid but missing `polyMesh`: path/case setup stage.
- `polyMesh` exists but `checkMesh` fails: conversion or boundary stage.
- tetra mode reaches any poly worker: routing regression.

### Recovery rule

When a regression is found, restore the last known-good tetra path first. Poly
changes must be behind an explicit feature branch/flag and must not alter:

- GMSH arguments;
- gmshToFoam command;
- tetra case directory creation;
- tetra checkMesh callback;
- cancellation and process cleanup.

The green poly workflow is forbidden while the independent tetra smoke test is
red.

Complex STEP/STL files can spend most of their time in geometry import,
surface classification, GMSH sizing, or volume generation. A blank GUI is not
an acceptable status. Every long operation must expose a stage and a live
heartbeat.

Required visible stages:

```text
1/6 geometry import
2/6 surface healing/classification
3/6 feature/proximity sizing
4/6 GMSH volume tetra generation
5/6 gmshToFoam conversion
6/6 checkMesh
```

The poly stage must never start while stages 1-6 are active.

### Watchdog rules

- GMSH volume generation runs in a dedicated worker/subprocess.
- `gmshToFoam`, `checkMesh`, `foamToVTK` and poly conversion each have their
  own process handle.
- Cancel must terminate the direct child, the WSL child and the process group.
- A timeout must emit a failure state, kill the process tree and leave the last
  valid tetra case untouched.
- The GUI must poll a heartbeat timestamp every few seconds. If no heartbeat
  arrives, show the current stage and elapsed time instead of appearing frozen.
- Never call a blocking subprocess from the Qt GUI thread.
- Never use a `QTimer` owned by a worker thread that is about to quit.
- Completion callbacks must be bound QObject methods or explicitly queued to
  the GUI thread; nested closures are not allowed for stage transitions.

### Preflight for expensive geometry

Before launching 3D GMSH generation, compute and log:

- CAD bounding box and occupied-volume estimate;
- surface triangle/face count;
- smallest detected feature and gap estimate;
- requested max/min cell size;
- estimated tetra count;
- estimated RAM and disk usage;
- selected cell budget, up to 20M.

If the estimate exceeds the selected budget, show the effective size that will
be used and ask for confirmation. Do not silently coarsen the input.

### Complex geometry fallback order

If a stage fails, retry only the failed stage and preserve the previous output:

1. retry GMSH without boundary layers;
2. retry with feature/proximity refinement reduced;
3. retry with the next coarser detail level;
4. if still failing, keep the geometry and report the exact stage/error.

Do not jump directly from a failed GMSH run to poly conversion. Do not reuse a
partial `.msh` or partial `polyMesh` from a failed attempt.

### Case isolation

Every run uses a unique directory:

```text
case_<timestamp>/work/
case_<timestamp>/tet_candidate/
case_<timestamp>/poly_candidate/
```

The active case is promoted only after the relevant stage passes. Old cases
must not be overwritten, and a failed run must not be able to leave an empty
`faces`, `owner` or `neighbour` file in the active case.
