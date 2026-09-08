# User guide

**English** | [Italiano](USER_GUIDE.it.md)

This guide is about **how the workflow works**, not just where to click — it's
for anyone who wants to understand why the app asks for one thing before
another.

## What this app is for

Preparing a mesh for OpenFOAM "by hand" means writing dictionaries
(`blockMeshDict`, `snappyHexMeshDict`, `meshDict`) as free-form text, guessing
the right cell sizes, and discovering quality problems only after running
`checkMesh` — often after a long simulation run. PolyFoamMesh puts GMSH
(geometry analysis, sizing) and cfMesh (volume filling) behind a GUI that does
these steps with a click, shows the mesh while it's being built, and fixes the
most common quality problems on its own.

## The workflow in four stages

```
1. GEOMETRY  →  2. MESH  →  3. QUALITY  →  4. EXPORT
   (load,       (generate,   (checkMesh    (OpenFOAM
    repair)      Quick/       + auto-fix)   case / CGNS
                Advanced)                  / VTU)
```

### 1. Geometry tab

You load STEP or STL (`Ctrl+O` or drag & drop). The app automatically detects
features (sharp edges, thin gaps, curvature) that it will use to compute
sensible cell sizes — no guessing on your part. If the geometry isn't
watertight, it tells you before it even tries to mesh, saving you a failed run
after minutes of waiting.

### 2. Mesh tab — Quick Mesh vs. manual paths

- **Quick Mesh (`Ctrl+M`)**: one click, parameters auto-suggested from the
  loaded geometry. It's the starting point for everyone, even if you've never
  heard of a `meshDict`.
- **CFD Poly (GMSH, no-WSL)**: GMSH tetrahedra → polyhedral dual (STAR-CCM+
  style topology). No WSL/OpenFOAM required.
- **FEM Tetra (GMSH, no-WSL)**: pure tetrahedral mesh for FEM solvers.
- **Cartesian (cfMesh)**: Cartesian volume filling via cfMesh in WSL2 — the
  "classic" OpenFOAM route, requires OpenFOAM installed (see
  [INSTALL.md](INSTALL.md)).

The **Detail slider** (Very Fine → Very Coarse) is a cell budget
(10K–20M cells), not a guarantee: the final size also depends on the actual
geometry.

### 3. Advanced tab (when Quick Mesh isn't enough)

- Local refinement (box / sphere / cylinder)
- Boundary layer: pick the patches, number of layers, growth rate
- Per-face sizing
- Multiple regions (CHT, FSI, multi-material)

### 4. Quality tab — checkMesh + auto-fix

After generation, the app runs `checkMesh` and shows skewness,
non-orthogonality and aspect ratio with a 3D heatmap of the problematic cells.
If quality doesn't meet the thresholds, the auto-fix tries up to 3 iterations
(smoothing + targeted refinement) before asking you to intervene by hand. The
final verdict can be exported as a **PDF report** (metrics + histograms +
screenshot) — handy for documenting the mesh in a project.

> Note: a "quality passed" mesh and a "successfully generated" mesh are two
> different things. If the requested algorithm fails the quality thresholds,
> the engine may automatically switch to a different topology (e.g. from hex
> to tetrahedral) to still guarantee a result — this is reported, not hidden
> — see [residual_risks.md](residual_risks.md).

### 5. Export

- **OpenFOAM case**: ready for `simpleFoam` / `pimpleFoam` / etc.
- **CGNS / VTU**: for ParaView or other solvers.
- **Tools → Launch ParaView**: if installed, opens the result directly.

## New Case Wizard (guided alternative)

`File → New Case` opens a 3-step wizard (Geometry → Mesh → Quality) that walks
you through the same pipeline in sequence, for anyone who'd rather not use the
tabs directly.

## Handy shortcuts

| Key | Action |
|---|---|
| `Ctrl+O` | Load geometry |
| `Ctrl+M` | Quick Mesh |
| `Ctrl+R` | Generate mesh / cancel |
| `Ctrl+Z` / `Ctrl+Y` | Undo / redo (parameters) |
| `Ctrl+N` | Reset |

## Next step

Not sure where to start? Follow the "Try it in two minutes" section in
[INSTALL.md](INSTALL.md).