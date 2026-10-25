# PolyFoamMesh - User Guide

PolyFoamMesh prepares OpenFOAM meshes without writing dictionaries by hand:
load a CAD file, pick a mesher, generate, check quality, export. This guide
explains the whole workflow - what each control does, in which order, and why.

- New here? Start with [Quick start](#quick-start).
- Installing or running from source: see [INSTALL.md](INSTALL.md).

## What this app is for

Preparing a mesh for OpenFOAM "by hand" means writing `blockMeshDict`,
`snappyHexMeshDict` or `meshDict` as free-form text, guessing cell sizes, and
discovering quality problems only after `checkMesh` - often after a long
simulation. PolyFoamMesh puts GMSH (geometry analysis, sizing), the built-in
polyhedral dual mesher and cfMesh (volume filling) behind a GUI that does
these steps with a click, shows the mesh while it is being built, and reports
quality honestly.

## Quick start

1. `File > Open Geometry...` (`Ctrl+O`) or drag & drop a `.step` / `.stl`.
2. Pick a mesher in the **Mesh** tab (start with **Quick Mesh**, `Ctrl+M`).
3. Press **Generate Mesh** (`Ctrl+R`). While the mesh is being built a red
   **Cancel** button appears in the Mesh tab, right under "Reset All".
4. Watch the **log** and the **Workflow** dock: the *Next action* line always
   tells you what to do next.
5. When it finishes, `checkMesh` runs automatically and the result is logged
   and shown in the quality panel. Export with `File > Export Mesh`.

## The interface

- **Ribbon (top)**: meshing actions and export shortcuts.
- **Left dock - Workflow**: the project schematic (Geometry, Mesh, Advanced,
  Quality, Export) with live status icons, plus the **Next action** hint
  (idle / in progress / finished / error).
- **Left panel - tabs**: `Geometry`, `Mesh`, `Advanced`. All sizing and
  boundary-layer controls live here.
- **3D viewer (centre)**: CAD surfaces and the generated mesh, with the
  display toolbar described in [The viewer](#the-viewer).
- **Log (bottom)**: every engine message, including boundary-layer reports.
- **Status bar**: current operation and progress.

## Workflow in stages

```
1. GEOMETRY  ->  2. MESH  ->  3. QUALITY  ->  4. EXPORT
   (load,         (generate,    (checkMesh     (OpenFOAM
    repair)        sizing)       + report)      case / CGNS
                                               / VTU)
```

### 1. Geometry tab

Load STEP or STL (`Ctrl+O`, recent files, or drag & drop). The app:

- detects features (sharp edges, thin gaps, curvature) and computes sensible
  cell sizes from them;
- checks watertightness **before** meshing and offers automatic repair if
  needed - no failed run after minutes of waiting;
- shows the CAD in the viewer (patch colors) so you can inspect it first.

### 2. Mesh tab

- **Mesh preset**: `Fast`, `Balanced` and `Fine` are one-click full mesh
  snapshots (detail, mesher, adaptive/fTetWild settings and BL).  The status
  line states the measured evidence; `Fine` is marked **experimental**
  because its exact full E2E was not re-run on the current tree.
  - Fast: coarse detail, 3 BL layers; measured on cube_duct (334 s,
    256,767 cells, BL 100%, checkMesh Failed 1 non-ortho).
  - Balanced: medium detail, 5 BL layers; measured on R1 173334 (110 s,
    253,681 cells, BL 100%, Failed 3 documented).
  - Fine: fine detail, 5 BL layers; only the W1 fine tet stage is measured
    (2.37M tets, 3738 s), so it is experimental.
  - **Save mesh preset…** stores the current full config under a name;
    **Delete** removes a user preset.  Built-in names are reserved.
  - Selecting a preset applies it to the widgets; advanced controls remain
    editable for overrides.  The whole config is saved with the project and
    restored at startup.


- **Detail slider** (Very Coarse -> Very Fine): a cell budget from ~10K to
  ~20M cells. It is a target, not a guarantee - the real size depends on the
  geometry; the label under the slider shows the geometric estimate and the
  cap.
- **Automatic refinement (curvature, edges, narrow gaps)**: when on, the
  mesh follows the CAD geometry (exact curvature, sharp edges, narrow
  passages) within the cell budget. How tightly it follows curvature is
  derived automatically from the Detail slider - there is no second knob.
- **Polymesh (auto) and cfMesh CTAs**: the main screen shows two visible
  mesher buttons. **Polymesh** runs the geometry auto: it measures the
  input (watertight, STEP solids, suffix, patches, size, refinement
  zones, pytetwild availability) and picks the tet engine from a
  measured policy (GMSH-first with fTetWild fallback, keep-best across
  the chain) — there is no manual engine choice among the Mesher
  buttons (Advanced offers a strict Tet engine pin instead). If the first
  engine fails or its checkMesh is unaccepted, the next one runs
  automatically; a valid-but-weak primary (Mesh OK yet dual residual
  defects, e.g. leftover face pyramids) opens one challenge attempt
  with the other engine and the better mesh wins (ties keep the
  incumbent; a failed challenger restores the incumbent). When the
  mesher chain is exhausted, auto retries once
  with a direct boundary layer on the last validated tet mesh
  (requires BL enabled in the panel, no silent BL). The tet engine can
  be pinned in Advanced (Tet engine: Automatic / GMSH / fTetWild):
  a pin runs that engine alone with no fallback — a failure is a
  declared error, never a silent swap (fTetWild requires
  `pip install pytetwild`). Every attempt,
  reason and fallback is logged (`Mesher:`, `Polymesh auto:`, BL
  provenance). Debug hatch: `POLYFOAMMESH_TET_ENGINE=gmsh|ftetwild`
  forces the tet engine for one run (logged as override) — useful to
  A/B the engines on the same geometry without touching the policy.
  **Cartesian cfMesh** is the separate
  explicit hex path (requires WSL2+OpenFOAM on Windows, native OpenFOAM
  on Linux). Legacy engines (`Legacy tet -> poly`, `Auto`,
  `fTetWild`, `BL on tets`, `FEM tetra`) stay in code, CLI and config —
  only the UI shortcut was removed; debug via CLI/config.
  The engine choices are:
  - **Polymesh (auto)**: the default path - geometry auto picks GMSH
    tetrahedra or fTetWild (see above) -> polyhedral dual
    (STAR-CCM+ style topology) with boundary layers. No WSL needed.
  - **Legacy tet -> poly (GMSH)**: same pipeline with the engine pinned
    (CLI/config debug only).
  - **Polymesh fTetWild**: same poly pipeline, but the tet
    stage runs [fTetWild](https://github.com/wildmeshing/fTetWild)
    instead of GMSH (experimental, issue #12). Requires the optional
    package `pip install pytetwild`. The target edge is derived from the
    Detail slider as "cells across the cross-section" (the same
    resolution model GMSH uses here; `FTETWILD_LR` switches it to a
    bbox-relative edge instead, `FTETWILD_OPT_ITER` caps the optimization
    passes). Over-dense surfaces are decimated and Taubin-smoothed toward
    that target before fTetWild runs (it never decimates toward the
    target itself), and its output is conditioned for the poly pipeline:
    boundary pinches are split, outlier micro-clusters dropped, and the
    dual's smoothing is allowed to relax boundary vertices on this path.
    Refinement zones are carried into the GMSH **surface** (2D sizing) and
    then consumed by fTetWild through the tagged triangulation; max-cells
    target and solution size fields remain GMSH-volume-only.  Boundary
    layers are added after the poly conversion as usual.
    Log lines are prefixed `[tet:ftetwild]`, and the run starts with a
    `bbox_diag / cross_section / target_edge / input_median_edge`
    diagnostic line; fTetWild's own phase lines stream during the
    tetrahedralization (`FTETWILD_QUIET=1` silences them). Cancel always
    stops a run, and `FTETWILD_LR` can still be pushed into the
    long-run regime — the log warns explicitly when that happens.
    **Surface tolerance** (Advanced tab) is a DYNAMIC envelope: a
    percentage of the local target cell edge (default 15%), so fidelity
    scales with the resolution slider and the part instead of a fixed
    millimetre value (15% -> 1.2 mm on an 8 mm cell, 0.15 mm on a 1 mm
    cell). Lower is more faithful and slower; below ~1% fTetWild stalls.
    The absolute form is still available via `--eps-mm` / `FTETWILD_EPS_MM`
    and the relative form via `FTETWILD_EPS`. The internal edge-collapsing
    simplification is OFF by default (measured: skipping it is faster
    AND more faithful — it was the expensive phase and a deviation
    source).
    **Adaptive refinement** (checkbox, default on) builds a local sizing
    field from the measured passage thickness and the surface curvature -
    cfMesh's surfaceThicknessRefinement + surfaceCurvatureRefinement
    behaviour - so thin passages and tightly curved regions are refined
    while the bulk keeps the slider's target edge; the log reports the
    measured target-edge range. On load / scale / unit change the log also
    prints the geometry measurements every sizer uses (domain,
    cross-section, wall-thickness p10/p50, curvature-limited cell,
    suggested cells). The **Scale Factor** + **Apply Scale** in the
    Geometry tab now really apply: the meshers receive the scaled
    geometry and the mesh cache is keyed on it. Boundary layers are built with the fTetWild dual, and the
    log reports coverage and achieved first-layer thickness PER PATCH
    (warning when a patch stays below 90%, i.e. a thin feature cannot
    take the stack). Measured: much faster than GMSH on this class of
    parts (on a real 3 m pipe: 150 s vs GMSH's 649 s at matching
    resolution, with much better tet quality); on hard parts a handful
    of warped boundary faces can still trip the final checkMesh —
    historically GMSH failed the same gate on the same part in half of
    its logged runs.
  - **BL on tets (prototype, opt-in)**: builds the prism layers directly
    on the tetrahedral boundary and keeps the tet core, without the dual.
    It reuses the same BL engine, report and checkMesh gate. On the real R1
    tet, at the common 1-layer A/B point, it measured 100% coverage,
    h1 ratio 0.999, `Mesh OK`, 90.5 s; the dual path measured h1 0.474,
    221.4 s and `Failed 3`, but the direct mesh has ~3.9x more cells
    (448,864 vs 114,269). It is
    not promoted and not the default; thin-feature/venturi/tube and the
    production 5-layer comparison remain open.
  - **Dirty CAD**: fTetWild tolerates open/non-watertight input through its
    envelope (GMSH cannot load such files at all). Measured: a 10-triangle
    open cube meshes to a checkMesh-passing tet mesh (3,515 tets, skew 0.87,
    NOmax 75.3, 0 pyramids; snappyHexMesh FATALs
    on the same file with 0 cells); the dual converter correctly refuses
    the open boundary instead of corrupting it, and the per-patch report
    lists coverage 0 rather than an empty result. The mini_valve STEP
    meshes dual-only to `Mesh OK` twice (36,949 cells: NOmax 67.80,
    skew 3.92; 36,514 cells: NOmax 64.68, skew 3.93; 0 pyramids)
    via fTetWild tet + collapsed dual + boundary-defect split;
    run-to-run variance exists (~1 in 5 fTetWild runs yields a
    non-manifold boundary), so retry on that explicit error.
  - **Auto (GMSH surface + fTetWild volume)**: opt-in source policy.
    For STEP it uses the GMSH surface (physical groups, curvature/gap
    sizing, refinement zones) with the fTetWild volume; for STL it feeds
    the input surface to fTetWild (GMSH cannot rebuild a discrete surface).
    If pytetwild is unavailable it starts with GMSH. On a source failure it
    performs **one visible fallback** to the other source; the log reports
    `primary/fallback/effective source`. Explicit Polymesh and fTetWild
    choices are unchanged and never auto-substituted.
  - **Cartesian (cfMesh)**: Cartesian volume filling via cfMesh -
    requires OpenFOAM (WSL2 on Windows, native on Linux; see
    [INSTALL.md](INSTALL.md)). Optional: enable `Convert to polyhedral
    mesh` (Advanced tab) to convert the hex-dominant result to
    polyhedral - it is off by default.
  - **Quick Mesh** (`Ctrl+M`): one click, parameters auto-suggested.
- **Mesh cache (automatic)**: a tet mesh from a previous run with
  identical sizing is reused, skipping the whole GMSH stage - the slowest
  one. No setting to manage; a hit is logged as
  `[cache] reusing cached tet mesh (...)`. Clear it from
  `Tools > Clear mesh cache` if needed.
- **Generate Mesh** (`Ctrl+R`): starts the run. While running, the
  dedicated red **Cancel** (under "Reset All") stops it.
- Every run logs one `[poly] stage times: dual ... | BL precompute ... |
  BL build ...` line so you always know where the time went.
- If the actual cell count deviates a lot from the budget, the app
  performs ONE automatic size correction (logged as
  `[tet:gmsh] budget correction: ...`) before giving the mesh.

### 3. Advanced tab

Manual control for when Quick Mesh is not enough:

- **Manual cell sizes**: Max/Min cell size fields (editable when adaptive
  sizing is off).
- **Local Refinement**: refine the volume near chosen regions.
  - `+` opens the add-zone dialog (pick a point in the 3D view, or type
    coordinates) - a picked point becomes a **box** centred there.
  - **Box 3D** adds a box edited interactively with the gizmo in the viewer.
  - `-` removes the selected zone.
  - The **level** spin box (1-6) applies to the selected zone; the list shows
    each zone as `Box 1 - level 2 - 12.0 mm`.
- **Boundary Layer** (see the dedicated section below): enable it and the
  layers are derived from the flow conditions automatically; patches,
  growth, robust mode and the mesh/BL consistency hint stay available.
  The 6 advanced knobs are locked to measured defaults behind the
  **Strategy: Automatic** selector — switch to Manual only if you know
  which one you need.
- **Tet engine (Polymesh auto)**: Automatic (geometry policy), or a
  strict GMSH / fTetWild pin with no fallback.
- **OpenMP / MPI rows** appear only when the Cartesian cfMesh CTA is
  selected (they cannot act on the Polymesh path).
- **Per-face sizing** and **multi-region** options for CHT/FSI cases.

## Patches

Patch names and types drive boundary layers and the exported case, so the
app treats them as first-class:

- **On import** (STEP or STL) a "Patch splitting" dialog asks for the
  **Feature angle** (5-179 deg): faces joined by a smaller dihedral angle
  are merged into one patch (BaramMesh-style). For STEP files you can also
  keep the CAD face names when available; otherwise the surface is split
  into `surface_1...N`. The last used angle is remembered.
- **The patch panel** (left dock, bottom) lists every patch with name,
  face count and **type** - for unnamed `surface_N` meshes the type is
  inferred from the geometry (inlet/outlet/wall) so the boundary layer
  never extrudes from an inlet.
- **Names are editable**: double-click a Name cell. On CAD this renames
  the patch for the whole pipeline; **after meshing** the rename is
  written into `constant/polyMesh/boundary` (a `boundary.bak` backup is
  kept the first time).
- Selecting a row highlights the patch in the 3D view; clicking a patch
  in the viewer (Select Patch) selects its row.
- **Multi-region `.msh`**: 3D physical groups become patches automatically.
  A volume without a named 2D surface group is named from its 3D group (or
  `region_N` when only numeric tags exist); no manual tag mapping is
  required.  A 2D wall and a 3D volume may share the same numeric tag - the
  converter keeps them separate, so no region disappears.  Names are made
  safe for OpenFOAM, duplicate names merge, and a face shared by more than
  two cells is rejected as non-manifold instead of becoming a ghost patch.

### 4. Quality

When generation finishes the app runs `checkMesh` automatically and reports
skewness, non-orthogonality and aspect ratio, with the problematic cells
highlighted in the viewer and listed in the quality panel. If thresholds are
not met, the auto-fix can try smoothing + targeted refinement iterations
before asking you to intervene. The verdict can be exported as a **PDF
quality report** (`File > Export Mesh > PDF Quality Report...`).

> A "quality passed" mesh and a "successfully generated" mesh are two
> different things: if a path fails, the engine may switch strategy and
> reports the substitution in the log (`[SUBSTITUTED]`) - never silently.
> See [residual_risks.md](residual_risks.md).

### 5. Export

`File > Export Mesh`:

- **OpenFOAM Case Folder...**: copies the case to a folder you pick,
  ready to run directly in OpenFOAM (`foamRun`, `simpleFoam`, ...). No
  boundary-condition validation is required - the mesh, `system/` and the
  `0/` fields (when present) are exported; heavy regenerable artifacts
  (decomposePar output, viewer files, the GMSH `.msh`, the pre-BL backup)
  are left out.
- **BaramFlow Case Folder...**: validates the case is complete and
  internally consistent (every mesh patch has a boundary condition in
  every `0/` field) and copies it, so BaramFlow opens it without errors.
- **PDF Quality Report...**: the mesh-quality report.
- **VTU / CGNS / SU2 / GMSH / Abaqus**: solver-format mesh exports.
- **Tools > Launch ParaView**: opens the result directly if ParaView is
  installed.

## The viewer

Toolbar controls (left to right):

- **Display**: `Surface`, `Surface with Edges`, `Wireframe`. The same
  ParaView-like representation applies to the CAD and to the generated mesh;
  CAD defaults to `Surface`, the mesh to `Surface with Edges`.
- **Background**: light/dark viewer background (independent of the app
  theme).
- **Section Cut**: cuts the shown geometry with a plane through the model
  centre so you can look inside. Works in every representation; toggle off to
  remove it.
- **Select Patch**: click a boundary patch to highlight it (solid highlight)
  and log its name - useful to identify inlets/outlets/walls. Clicking the
  empty background clears the selection; clicking the highlighted patch
  again deselects it.
- **Measure**: click two points (on CAD or mesh) to get the distance in
  metres; numbered markers and a line are drawn, a summary overlay lists
  every measurement, and unchecking the tool (or `Clear`) removes them.

Boundary layers are visible **through the mesh itself**: in
`Surface with Edges` the layer stack shows as the dense edge band at the
wall - no overlay or colouring is needed.

Mouse: left-drag rotates, middle-drag pans, wheel zooms. `Box 3D` refinement
zones are edited directly with their gizmo in the viewer.

## Boundary layers

Boundary layers are inserted by the poly mesher. The default (legacy
tet→poly) and fTetWild/Auto engines add them after the dual. The opt-in
**BL on tets** engine adds them directly on the tet boundary; it is not the
default, and the log names the mode. In the **Advanced** tab:

- **Enable Boundary Layers**: master switch.
- **Apply BL to all patches**: otherwise layers are added only to wall-typed
  patches.
- **nLayers / Expansion Ratio**: number of layers and growth rate. If you do
  not know them, use the y+ calculator:
  - set **Velocity**, **Fluid** (air/water) and **Wall treatment**
    (wall-resolved y+~1 or wall-function y+~30);
  - press **Auto-compute**: the first layer height, layer count and total
    thickness are derived from the flat-plate correlation and shown with the
    Reynolds number.
- **Advanced controls (poly path)** on the Advanced tab start with a
  **Strategy** selector so you never need the 6 knobs below:
  - **Automatic (recommended)**: the knobs are locked to the measured
    defaults; the engine runs the standard stack and, on the fTetWild
    path, retries automatically with the quality attempt when the
    standard result adds pyramids.
  - **Standard only**: same defaults, local termination forced off AND
    the automatic quality retry disabled for this run (faster,
    possibly more pyramids — for deliberate A/B).
  - **Manual**: every knob below is yours, exactly as before
    (min base-edge fraction, terminate-by-budget, edge-cap percentile,
    local termination mode, local height retry, local max rounds).
  - **Edge-cap percentile** (default 0) is experimental: it has a
    measured failure mode on some duals, leave it at 0 unless you are
    deliberately testing it.
- **Presets**: save the whole BL configuration under a name with
  **Save preset…**. The list is versioned, survives a restart, and the
  last-used preset is restored when no newer saved configuration exists.
  A preset that is out of range is loaded with a declared clamp (the
  status line says so) instead of breaking the project.
- **Complex geometry**: the engine builds the full stack on the healthy
  wall and automatically leaves out the faces it cannot grow from -
  sliver faces and the inlet/outlet caps (which are not walls). The log
  reports how many faces were excluded; the layers on the rest are the
  requested ones.
- **Everything is automatic**: enabling the boundary layer computes the
  layers from the flow conditions on the spot, and the values update
  themselves when you change Velocity / Fluid / Wall treatment. Edit the
  numbers by hand if you want to - the app then leaves them alone.
- **Effective parameters are recorded**: the run log prints
  `[poly] BL effective: ...` and `[poly] BL provenance: ...`, every clamp
  as `[poly] BL config warning: ...`, and the same values with their
  provenance are written to `<case>/bl_config.json`.

What the log tells you after a run (always read these lines):

```
[bl] capped layers 14 -> 2 (mesh support at full height, 90% of wall)
[bl] 26,162 prism cells (2 layers requested, median 2, local 2..2),
     coverage 100.0% of wall faces, first layer 0.000364 m
     (requested 0.000364), total thickness 0.0008008 m
```

- `capped N -> k`: the mesh can only host `k` full-height layers; more would
  be squashed. To get more layers, use a **coarser surface mesh** (bigger
  cells) or a smaller first layer.
- `median` / `coverage`: the layers actually achieved on the wall. A run that
  would produce a single flat layer everywhere is **rejected**, not reported
  as success.
- `first layer X (requested Y)`: if X is much smaller than Y, the y+ target
  moved by the same factor (the log warns explicitly).  W10.1 also reports
  **why** the cap exists: `h1 cap: support=...%, edge=...%` comes from the
  distance to the first interior dual vertex and the local wall-edge cap;
  it is not a silent engine choice.
- **Measured h1, not the requested one**: the mesh report prints the
  achieved h1 median/p10 per patch and the ratio.  R1 is the measured
  example: 0.145 mm vs 1.26 mm requested (0.116x in the W11 read-back),
  with 91.9% of wall vertices support-bound.  A ratio below 0.9 is a
  declared gap; the fix is consume/reflow or a taller wall-adjacent dual
  cell, not a lower clamp.
- **Every patch is reported**: the per-patch table also lists patches that
  were not selected for BL, with `bl_selected=false` and coverage 0.  On R1
  this is 7 rows (5 selected walls + 2 caps), matching the viewer instead
  of hiding the caps.  Only selected walls can trigger the <90% coverage
  warning.
- **BL quality policy**: the fTetWild worker tries the standard stack, then
  a measured `decoupled_vertex` quality attempt only when the standard adds
  pyramids; it keeps the better mesh.  R1 A/B: 39 pyramids/Failed 3 ->
  0 pyramids/1 skew/Failed 1, coverage 99.47%.  Set
  `POLYFOAMMESH_BL_QUALITY_POLICY=0` to restore the historical single
  attempt for one milestone.
- **y+ per patch**: if a solver `u_tau` is not available, y+ is reported
  as `null` (never fabricated).  The optional velocity/fluid report uses
  the labelled flat-plate/pipe correlation as an estimate only; it is
  computed from the **achieved** h1, so it moves with the ratio above.
- `BL effective` / `BL provenance`: the exact values handed to the engine
  and where each came from (`default`, `user`, `preset`). This line is the
  contract; a value shown there is what the engine used.
- `BL config warning`: a value was outside its documented range (for
  example a preset with 999 layers) and was clamped to the stated value.
  Nothing is changed silently.
- `<case>/bl_config.json`: the same effective values, provenance, warnings
  and resolved first-layer height, for post-run inspection or bug reports.

If the standard path fails, the engine retries automatically, in order:

1. standard robust path;
2. **concave closure** (per-vertex termination, for concave junctions);
3. first layer halved (and then quartered), with a log line because it moves
   the y+ target.

No checkbox is needed for this - it happens by itself, and every step is
logged.

Known limit: the layer stack cannot be thicker than the first dual cell
depth; the log tells you when this binds (`capped ...`). This is a mesh
resolution limit, not a bug - see [residual_risks.md](residual_risks.md).

## Process watchdog and checkMesh

Every long phase (tet, dual, boundary layer, conversion) reports liveness
and has one declared recovery policy:

- the log shows `[watchdog] <phase>: ...` only when a phase actually stalls
  or hits its last-resort ceiling; the line names the phase, elapsed time,
  no-progress time and the action;
- a healthy but quiet phase prints `... still running` heartbeats, not a
  false failure;
- fTetWild shows its real optimizer progress (`pass N`, tets) instead of an
  indeterminate bar;
- Cancel reaches the real child process for GMSH, fTetWild, checkMesh and
  `polyDualMesh` (no orphaned WSL process).
- GMSH's parallel tessellation can produce slightly different meshes for
  the same input. For byte-stable A/B comparisons set
  `POLYFOAMMESH_GMSH_THREADS=1`; the default leaves GMSH parallel.

`checkMesh` failures are reported with their **structured defects**:
the number of failed checks, the defect kinds and counts (face pyramids,
wrong orientation, skewness, non-orthogonality, negative volume, ...),
the sets/positions checkMesh wrote, and the cause.  A failed report is never
shown as PASS.  It can only be accepted explicitly, with a written reason,
and the log says so.

## New Case Wizard and templates

- `File > New Case` (`Ctrl+N`): a guided wizard that walks the same pipeline
  step by step (geometry -> mesh -> quality) if you prefer not to use the
  tabs directly.
- `File > New from Template`: ready-made setups (internal flow, external
  aerodynamics, CHT, multiphase, moving body, combustion, solid heat
  transfer) that pre-fill solver, turbulence model and sizing.

## Themes

`View > Theme` switches between light and dark. The viewer background is a
separate control (the `Background` combo in the viewer toolbar), so you can
keep a dark viewer with a light interface or vice versa. Dialogs and panels
follow the theme; if a text is hard to read in one of the themes, report it -
contrast is checked automatically in CI-style tests.

## Shortcuts

| Key | Action |
|---|---|
| `Ctrl+O` | Load geometry |
| `Ctrl+N` | New case wizard / reset |
| `Ctrl+M` | Quick Mesh |
| `Ctrl+R` | Generate mesh / cancel |
| `Ctrl+Z` / `Ctrl+Y` | Undo / redo (parameters) |

## Troubleshooting

- **Boundary layer skipped or capped**: read the `[bl]` lines in the log. If
  it says `capped`, the mesh is too fine for the requested stack; if it says
  `rejected`, the engine refused to write a useless single-layer shell and
  tried its fallbacks. Coarser surface cells or a smaller first layer fix it.
- **Poly release residual**: the log now prints `[poly] release gate:
  accepted ...` (or the rejection reason).  R1 is accepted with an explicit
  residual: 0 inverted pyramids, 99.47% BL coverage, 1 highly-skewed face
  (`checkMesh Failed 1`); the residual is written into `bl_report`, never
  hidden.  The opt-in edge-filter 0.9 + budget combo is not promoted: it
  reaches 0 pyramids but only 87.1% coverage.
- **`checkMesh` failed**: open the quality panel, look at the highlighted
  cells, and let the auto-fix run, or relax local refinement.
- **No boundary layer on a complex part**: if the log reports that the BL
  build was refused (boundary not manifold), the app kept the dual-only
  mesh on purpose - a known limitation on some real geometries, with the
  fix scheduled; see [residual_risks.md](residual_risks.md).
- **Linux**: install from source - see [INSTALL_LINUX.md](INSTALL_LINUX.md)
  for prerequisites, OpenFOAM setup and troubleshooting (OpenFOAM is needed
  only for the cfMesh/checkMesh/foamToVTK features).
- **Cartesian/cfMesh paths unavailable**: WSL2 + OpenFOAM are required only
  for that mesher; the GMSH-based paths work standalone.
- **Viewer looks empty after meshing**: press the display combo (try
  `Surface with Edges`) and check the log for the foamToVTK step.

## Where to get help

- [INSTALL.md](INSTALL.md) - installation, first run, "try it in two minutes".
- [residual_risks.md](residual_risks.md) - known limitations, honestly
  documented.
- The app log (`[SUBSTITUTED]`, `[bl]`, `[poly]` tags) is the single source of
  truth for what the engines actually did.
