# cfMesh v2512 meshDict Reference

Verified keys and syntax for the `system/meshDict` file read by `cartesianMesh`.

## Tested keys (used in benchmarks, verified against cartesianMesh)

### `surfaceFile`
Path to the input surface triangulation. Accepts `.stl` (ASCII/binary) and `.fms` (cfMesh native with feature edges).

```
surfaceFile "constant/triSurface/surface.stl";
surfaceFile "constant/triSurface/surface.fms";
```

Using `.fms` preserves sharp edges (dihedral > threshold from `surfaceFeatureEdges`).

### `maxCellSize` / `minCellSize`
Global cell size bounds in metres. cfMesh maps these to the nearest octree level
where `effective_size = root_size / 2^k`. Sizes not aligned to `root_size / 2^k`
are silently rounded — use `snap_to_octree_level()` in `geometry.py` to predict
the effective value.

```
maxCellSize 0.05;
minCellSize 0.005;
```

### `patchCellSize`
Per-patch cell size override. Patches not listed use `maxCellSize`. The cell size
applies to cells intersecting that patch surface.

```
patchCellSize
{
    "inlet"  0.02;
    "outlet" 0.02;
    "wall"   0.01;
}
```

### `boundaryCellSize` + `boundaryCellSizeRefinementThickness`
Near-boundary refinement: cells within `boundaryCellSizeRefinementThickness` of
any boundary are refined to `boundaryCellSize`. After that distance, cells grow
to `maxCellSize`.

```
boundaryCellSize 0.01;
boundaryCellSizeRefinementThickness 0.05;
```

### `keepCellsIntersectingBoundary`
Boolean (0/1). When 1, cells cut by the boundary surface are kept and clipped.
When 0, they are discarded.

```
keepCellsIntersectingBoundary 1;
```

### `allowDisconnected`
Boolean (0/1). When 1, cfMesh suppresses errors for disconnected/disjoint
geometry regions. When 0 (recommended), a non-watertight or disjoint domain
causes a hard failure. Default the app emits is 0.

```
allowDisconnected 0;
```

### `maxNumIterations`
Number of octree-refinement/smoothing iterations. 10-20 is typical; 15 is the
app default. More iterations help convergence but cost linearly more time.

```
maxNumIterations 15;
```

### `boundaryLayers` / `patchBoundaryLayers`
Boundary layer (prism layer) configuration. Applied per patch group via regex.
Supports `nLayers`, `thicknessRatio` (growth ratio, >1), `maxFirstLayerThickness`,
`optimiseLayer`, `untangleLayers`.

```
boundaryLayers
{
    patchBoundaryLayers
    {
        "wall|bottom"
        {
            nLayers                 15;
            thicknessRatio          1.2;
            maxFirstLayerThickness  0.0005;
            optimiseLayer           1;
            untangleLayers          1;
        }
    }
}
```

The regex matches patch names. Only patches that exist in the boundary are
matched; unmatched patches get no layers. Use `renameBoundary` to ensure
correct patch naming before BL assignment.

Keys:
- `nLayers` — integer, number of prism layers (1-50+)
- `thicknessRatio` — float >1, layer-to-layer growth ratio (cfMesh calls this the expansion/growth factor)
- `maxFirstLayerThickness` — float, absolute first-layer height in metres. Without this cfMesh picks its own
  first-layer height based on cell size
- `optimiseLayer` — boolean (0/1), post-optimise layer positions
- `untangleLayers` — boolean (0/1), untangle folded/tangled layers

### `renameBoundary`
Remap patch types from cfMesh's default `wall` to the correct OpenFOAM boundary
types. Without this, every patch is typed `wall` — inlets/outlets cannot take
flow BCs.

```
renameBoundary
{
    defaultName     wall;
    defaultType     wall;
    newPatchNames
    {
        "inlet"
        {
            newName     inlet;
            type        patch;
        }
        "outlet"
        {
            newName     outlet;
            type        patch;
        }
    }
}
```

Type mapping used by the app:
- `inlet`, `outlet`, `opening`, `farfield` → `patch`
- `symmetry` → `symmetryPlane`
- `empty` → `empty`
- everything else → `wall`

## Supported but not yet emitted by the app

### `objectRefinements`
Local refinement boxes/spheres. Cells inside each box are refined to the
specified `cellSize`. Multiple boxes can be defined.

```
objectRefinements
{
    myBox
    {
        type    box;
        min     (-0.1 -0.1 -0.1);
        max     (0.1 0.1 0.1);
        cellSize 0.005;
    }
    mySphere
    {
        type    sphere;
        centre  (0 0 0);
        radius  0.2;
        cellSize 0.002;
    }
}
```

The app has `build_object_refinements()` in `meshdict_gen.py` ready to emit
these. Integration into `quick_mesh.py` is pending — for now it's available
for explicit callers.

### `edgeMeshRefinement`
Refine cells near feature edges from a `.eMesh` file. Used together with
`surfaceFeatureEdges` output.

```
edgeMeshRefinement
{
    edgeMesh "constant/triSurface/features.eMesh";
    cellSize 0.002;
}
```

Not yet wired in the app. The existing FMS pipeline (`generate_fms()` →
`.fms` surfaceFile) already captures feature edges without needing
`edgeMeshRefinement`.

### `surfaceMeshRefinement`
Refine cells near a surface mesh beyond the global `boundaryCellSize`.

```
surfaceMeshRefinement
{
    refinementLevel 3;
}
```

Not yet wired.

## Threading / environment variables

cfMesh uses OpenMP for parallel execution. Set `OMP_NUM_THREADS` before
invoking `cartesianMesh`:

```bash
export OMP_NUM_THREADS=7   # cpu_count - 1
cartesianMesh -case /path/to/case
```

The app's `config.py::build_command()` now sets this automatically to
`cpu_count - 1`.

There is no `nProcessingThreads` meshDict key in cfMesh v2512.

## Required case files

Minimal case directory for `cartesianMesh`:
```
case/
  constant/
    triSurface/
      surface.stl           # or surface.fms
    polyMesh/               # empty — cartesianMesh creates it
  system/
    meshDict
    controlDict
    fvSchemes               # needed even if unused
    fvSolution              # needed even if unused
```

`controlDict` must have `application cartesianMesh;`. `fvSchemes` and
`fvSolution` can be trivial (they are read but not used by the mesher).
