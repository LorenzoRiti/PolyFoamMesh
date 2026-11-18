"""Size lattice: the adaptive size field is evaluated by GMSH in C++ (a
Structured field) instead of a Python callback called millions of times."""
import types

import numpy as np
from scipy.spatial import cKDTree

from polyfoammesh.core import gmsh_wrapper as gw


def _fake_gmsh(box):
    mod = types.SimpleNamespace()
    mod.model = types.SimpleNamespace(getBoundingBox=lambda d, t: box)
    return mod


def test_lattice_file_header_and_value_count(tmp_path):
    pts = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])
    sizes = np.array([0.1, 0.4])
    out = tmp_path / "lat.txt"
    info = gw._write_size_lattice(
        _fake_gmsh((0, 0, 0, 1, 1, 1)), cKDTree(pts), sizes, 0.1, out)
    assert info is not None
    toks = out.read_text(encoding="ascii").split()
    n = [int(t) for t in toks[6:9]]
    assert len(toks) == 9 + n[0] * n[1] * n[2] == 9 + info["n_points"]
    vals = np.array(toks[9:], dtype=float)
    assert vals.min() == 0.1 and vals.max() == 0.4
    assert info["spacing"] <= 0.1


def test_lattice_gives_up_when_too_coarse(tmp_path):
    pts = np.array([[0.0, 0.0, 0.0]])
    # a 1000 m box with h_min 1 mm would need > 6M points at spacing <= h_min
    info = gw._write_size_lattice(
        _fake_gmsh((0, 0, 0, 1000, 1000, 1000)), cKDTree(pts),
        np.array([0.001]), 0.001, tmp_path / "x.txt")
    assert info is None


def test_values_follow_nearest_sample(tmp_path):
    pts = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    sizes = np.array([0.05, 0.5])
    out = tmp_path / "lat.txt"
    gw._write_size_lattice(_fake_gmsh((0, 0, 0, 1, 0.2, 0.2)),
                           cKDTree(pts), sizes, 0.05, out)
    toks = out.read_text(encoding="ascii").split()
    n = [int(t) for t in toks[6:9]]
    vals = np.array(toks[9:], dtype=float).reshape(n)
    assert vals[0, 0, 0] == 0.05 and vals[-1, 0, 0] == 0.5
