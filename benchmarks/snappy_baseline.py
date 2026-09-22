"""Versioned snappyHexMesh case builders: 'base' (manual/tutorial style)
and 'expert' (feature edges + tuned layers). Reproducible templates —
never hand-tuned per run (spec D3).

No WSL here: builders are pure; execution lives in run_comparison.py.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import numpy as np


def cell_size_to_level(min_cell: float, max_cell: float) -> int:
    """Octree level so finest cells ~= min_cell (each level halves size)."""
    if min_cell <= 0 or max_cell <= 0:
        return 1
    ratio = max_cell / min_cell
    level = 0
    while (2 ** level) < ratio and level < 20:
        level += 1
    return max(1, level)


def bbox_of(meshes: list) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    mins = np.min([m.vertices.min(axis=0) for m in meshes], axis=0)
    maxs = np.max([m.vertices.max(axis=0) for m in meshes], axis=0)
    bmin = (float(mins[0]), float(mins[1]), float(mins[2]))
    bmax = (float(maxs[0]), float(maxs[1]), float(maxs[2]))
    return bmin, bmax


def find_location_in_mesh(meshes: list) -> tuple[float, float, float]:
    """First 5x5x5 grid point contained by every watertight mesh; falls back
    to bbox centre (best effort for open/dirty geometry)."""
    bmin, bmax = bbox_of(meshes)
    axes = [np.linspace(bmin[i], bmax[i], 5) for i in range(3)]
    grid = np.array(np.meshgrid(*axes, indexing="ij")).reshape(3, -1).T
    for pt in grid:
        loc = (float(pt[0]), float(pt[1]), float(pt[2]))
        try:
            if all(bool(m.contains([loc])[0]) for m in meshes if m.is_watertight):
                return loc
        except Exception:
            continue
    cx = (bmin[0] + bmax[0]) / 2.0
    cy = (bmin[1] + bmax[1]) / 2.0
    cz = (bmin[2] + bmax[2]) / 2.0
    return cx, cy, cz


def build_block_mesh_dict(
    bmin: tuple[float, float, float],
    bmax: tuple[float, float, float],
    max_cell: float,
) -> list[str]:
    """Coarse background hex mesh of the bbox, cells ~max_cell, min 3 per axis."""
    def _n(i: int) -> int:
        extent = bmax[i] - bmin[i]
        return max(3, math.ceil(extent / max_cell))
    nx, ny, nz = _n(0), _n(1), _n(2)
    x0, y0, z0 = bmin
    x1, y1, z1 = bmax
    return [
        "FoamFile { version 2.0; format ascii; class dictionary; object blockMeshDict; }",
        "scale 1;",
        "vertices (",
        f"    ({x0} {y0} {z0}) ({x1} {y0} {z0}) ({x1} {y1} {z0}) ({x0} {y1} {z0})",
        f"    ({x0} {y0} {z1}) ({x1} {y0} {z1}) ({x1} {y1} {z1}) ({x0} {y1} {z1})",
        ");",
        f"blocks ( hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading (1 1 1) );",
        "edges ();",
        "boundary (",
        "    walls",
        "    {",
        "        type wall;",
        "        faces (",
        "            (0 3 2 1) (4 5 6 7) (0 1 5 4)",
        "            (2 3 7 6) (1 2 6 5) (3 0 4 7)",
        "        );",
        "    }",
        ");",
        "mergePatchPairs ();",
    ]


def build_snappy_dict(
    mode: str,
    location: tuple[float, float, float],
    max_cell: float,
    min_cell: float,
) -> list[str]:
    if mode not in ("base", "expert"):
        raise ValueError(f"unknown snappy mode: {mode!r}")
    level = cell_size_to_level(min_cell, max_cell)
    expert = mode == "expert"
    loc = " ".join(f"{c:g}" for c in location)

    if expert:
        feature_lines = [
            "    features",
            "    (",
            f'        {{ file "surface.eMesh"; level {level + 1}; }}',
            "    );",
        ]
        resolve_angle = 60
        snap_iter, feature_snap_iter = 20, 15
        explicit_snap = "true"
        n_layers, expansion, final_t, min_t = 8, 1.2, 0.25, 0.05
        medial_ratio, layer_iter = 0.35, 80
        max_global = 4_000_000
    else:
        feature_lines = ["    features", "    (", "    );"]
        resolve_angle = 30
        snap_iter, feature_snap_iter = 10, 10
        explicit_snap = "false"
        n_layers, expansion, final_t, min_t = 5, 1.3, 0.3, 0.1
        medial_ratio, layer_iter = 0.8, 50
        max_global = 3_000_000

    return [
        "FoamFile { version 2.0; format ascii; class dictionary; object snappyHexMeshDict; }",
        "",
        "castellatedMesh true;",
        "snap true;",
        "addLayers true;",
        "",
        "geometry",
        "{",
        "    surface.stl { type triSurfaceMesh; name surface; }",
        "}",
        "",
        "castellatedMeshControls",
        "{",
        f"    maxLocalCells {max(500_000, max_global // 2)};",
        f"    maxGlobalCells {max_global};",
        "    minRefinementCells 10;",
        "    maxLoadUnbalance 0.1;",
        "    nCellsBetweenLevels 3;",
        f"    locationInMesh ({loc});",
        "",
        "    refinementSurfaces",
        "    {",
        '        "surface"',
        "        {",
        f"            level ({level} {level});",
        "        }",
        "    }",
        "",
        f"    resolveFeatureAngle {resolve_angle};",
        "    refinementRegions {};",
        "",
        *feature_lines,
        "}",
        "",
        "snapControls",
        "{",
        "    nSmoothPatch 5;",
        f"    tolerance {max(1.0, min_cell * 10):g};",
        f"    nSolveIter {snap_iter};",
        "    nRelaxIter 5;",
        f"    nFeatureSnapIter {feature_snap_iter};",
        "    implicitFeatureSnap true;",
        f"    explicitFeatureSnap {explicit_snap};",
        "    multiRegionFeatureSnap false;",
        "}",
        "",
        "addLayersControls",
        "{",
        "    relativeSizes true;",
        "    layers",
        "    {",
        '        "surface"',
        "        {",
        f"            nSurfaceLayers {n_layers};",
        "        }",
        "    }",
        f"    expansionRatio {expansion};",
        f"    finalLayerThickness {final_t};",
        f"    minThickness {min_t};",
        "    nSmoothSurfaceNormals 1;",
        "    nSmoothNormals 3;",
        "    nSmoothThickness 10;",
        "    maxFaceThicknessRatio 0.5;",
        "    maxFaceAreaRatio 0.3;",
        f"    maxThicknessToMedialRatio {medial_ratio};",
        "    minMedialAxisAngle 50;",
        "    maxFlipRatio 0.5;",
        "    nBufferCellsNoExtrude 0;",
        f"    nLayerIter {layer_iter};",
        "    nRelaxIter 5;",
        "    minVolRatio 0.1;",
        "    minArea -1;",
        "}",
        "",
        "meshQualityControls",
        "{",
        "    maxNonOrtho 65;",
        "    maxBoundarySkewness 20;",
        "    maxInternalSkewness 4;",
        "    maxConcave 80;",
        "    minVol 1e-13;",
        "    minTetQuality 1e-15;",
        "    minArea -1;",
        "    minTriangleTwist -1;",
        "    nSmoothScale 4;",
        "    errorReduction 0.75;",
        "    relaxed { maxNonOrtho 75; }",
        "}",
        "",
        "mergeTolerance 1e-6;",
        "",
    ]


def prepare_snappy_case(
    case_dir: Path,
    stl_path: Path,
    meshes: list,
    mode: str,
    max_cell: float,
    min_cell: float,
    location: tuple[float, float, float] | None = None,
) -> None:
    """Write surface + blockMeshDict + snappyHexMeshDict + solver dicts."""
    system = case_dir / "system"
    tri = case_dir / "constant" / "triSurface"
    system.mkdir(parents=True, exist_ok=True)
    tri.mkdir(parents=True, exist_ok=True)
    shutil.copy2(stl_path, tri / "surface.stl")

    loc = location or find_location_in_mesh(meshes)
    bmin, bmax = bbox_of(meshes)

    (system / "blockMeshDict").write_text(
        "\n".join(build_block_mesh_dict(bmin, bmax, max_cell)) + "\n", encoding="ascii"
    )
    (system / "snappyHexMeshDict").write_text(
        "\n".join(build_snappy_dict(mode, loc, max_cell, min_cell)) + "\n", encoding="ascii"
    )
    (system / "controlDict").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }\n"
        "application snappyHexMesh;\n"
        "startFrom startTime; startTime 0; stopAt endTime; endTime 100;\n"
        "deltaT 1; writeControl timeStep; writeInterval 50; purgeWrite 0;\n"
        "writeFormat ascii; writePrecision 6; writeCompression off;\n"
        "timeFormat general; timePrecision 6; runTimeModifiable true;\n",
        encoding="ascii",
    )
    (system / "fvSchemes").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\n"
        "ddtSchemes { default steadyState; }\n"
        "gradSchemes { default Gauss linear; }\n"
        "divSchemes { default none; }\n"
        "laplacianSchemes { default Gauss linear corrected; }\n"
        "interpolationSchemes { default linear; }\n"
        "snGradSchemes { default corrected; }\n",
        encoding="ascii",
    )
    (system / "fvSolution").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\n"
        "solvers {}\n",
        encoding="ascii",
    )
