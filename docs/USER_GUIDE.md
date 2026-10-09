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

- **Detail slider** (Very Coarse -> Very Fine): a cell budget from ~10K to
  ~20M cells. It is a target, not a guarantee - the real size depends on the
  geometry; the label under the slider shows the geometric estimate and the
  cap.
- **Automatic refinement (curvature, edges, narrow gaps)**: when on, the
  mesh follows the CAD geometry (exact curvature, sharp edges, narrow
  passages) within the cell budget. How tightly it follows curvature is
  derived automatically from the Detail slider - there is no second knob.
- **Mesher choice**:
  - **Polymesh (poly)**: the default path - GMSH tetrahedra -> polyhedral
    dual (STAR-CCM+ style topology) with boundary layers. No WSL needed.
  - **Quick Mesh** (`Ctrl+M`): one click, parameters auto-suggested.
  - **Cartesian (cfMesh)**: Cartesian volume filling via cfMesh -
    requires OpenFOAM (WSL2 on Windows, native on Linux; see
    [INSTALL.md](INSTALL.md)).
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
  `[gmsh] budget correction: ...`) before giving the mesh.

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

- **OpenFOAM case**: ready for `simpleFoam` / `pimpleFoam` / etc.
- **CGNS / VTU**: for ParaView or other solvers.
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
  empty background clears the selection.
- **Measure**: click two points (on CAD or mesh) to get the distance in
  metres; numbered markers and a line are drawn, a summary overlay lists
  every measurement, and unchecking the tool (or `Clear`) removes them.
- **Show BL**: colours the boundary-layer prism cells orange over the grey
  core (works with `surface_N` meshes via geometric wall inference), so a
  sub-millimetre layer is actually visible.

Mouse: left-drag rotates, middle-drag pans, wheel zooms. `Box 3D` refinement
zones are edited directly with their gizmo in the viewer.

## Boundary layers

Boundary layers are inserted by the poly mesher (CFD Poly path). In the
**Advanced** tab:

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
- **Robust boundary layer (recommended)**: drops sliver base faces and lets
  the engine cap the stack to what the mesh can actually hold. Use it for
  real parts.
- **Complex geometry**: the engine builds the full stack on the healthy
  wall and automatically leaves out the faces it cannot grow from -
  sliver faces and the inlet/outlet caps (which are not walls). The log
  reports how many faces were excluded; the layers on the rest are the
  requested ones.
- **Everything is automatic**: enabling the boundary layer computes the
  layers from the flow conditions on the spot, and the values update
  themselves when you change Velocity / Fluid / Wall treatment. Edit the
  numbers by hand if you want to - the app then leaves them alone.
- **Mesh/BL consistency hint**: the panel compares the requested stack
  with the wall cell size the current mesh will produce and tells you if
  it fits or gets compressed; the engine compresses the stack by itself
  when needed and the log states it. When boundary layers are enabled,
  the mesher also enforces a minimum wall cell size (the stack divided
  by 3, capped at 10% of the model size), logged as
  `[gmsh] BL wall size floor: ...`.

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
  moved by the same factor (the log warns explicitly).

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
- **`checkMesh` failed**: open the quality panel, look at the highlighted
  cells, and let the auto-fix run, or relax local refinement.
- **No boundary layer on a complex part**: if the log reports that the BL
  build was refused (boundary not manifold), the app kept the dual-only
  mesh on purpose - a known limitation on some real geometries, with the
  fix scheduled; see [residual_risks.md](residual_risks.md).
- **Linux**: install from source or from the release tarball - see the
  "Linux" section of [INSTALL.md](INSTALL.md); OpenFOAM must be on PATH
  (cfMesh/checkMesh/foamToVTK features).
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
