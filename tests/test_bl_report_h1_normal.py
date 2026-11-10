"""The BL report's first-layer height is a WALL distance: a first-layer cell
whose centroid sits sideways of the wall-face centre (sheared prism on a
large polyhedral face) must not read as a thicker layer (DN1000: reported
3.01 mm / y+ 87 for 0.44 mm layers)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core import foam_mesh_io  # noqa: E402
from polyfoammesh.core.openfoam_runner import boundary_layer_report  # noqa: E402


def _sheared_cell(tmp_path, h=0.001, shift=0.004):
    # unit-ish square base on z=0, top at z=h shifted sideways by `shift`
    base = [(0, 0, 0), (0.01, 0, 0), (0.01, 0.01, 0), (0, 0.01, 0)]
    top = [(x + shift, y, h) for x, y, _ in base]
    pts = np.array(base + top, dtype=float)
    faces = [
        [0, 3, 2, 1],          # wall (outward -z)
        [4, 5, 6, 7],          # top
        [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7],
    ]
    owner = np.zeros(6, dtype=np.int64)
    neigh = np.zeros(0, dtype=np.int64)
    patches = [
        {"name": "wall", "type": "wall", "nFaces": 1, "startFace": 0},
        {"name": "other", "type": "patch", "nFaces": 5, "startFace": 1},
    ]
    poly = tmp_path / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    foam_mesh_io.write_polymesh(poly, pts, faces, owner, neigh, patches)
    return tmp_path


def test_first_layer_is_measured_along_the_wall_normal(tmp_path):
    case = _sheared_cell(tmp_path)
    rep = boundary_layer_report(case, requested_h1=0.001, bl_patch_names=["wall"])
    assert rep["n_layered"] == 1
    # euclidean centre distance would give 2*sqrt(0.0005^2+0.002^2) = 4.1 mm
    assert abs(rep["h1_median"] - 0.001) < 1e-9
