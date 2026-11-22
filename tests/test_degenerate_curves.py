"""Zero-length (tolerance-sized) edges must not count as small features.

Regression: a filleted box has degenerate edges (~2e-7) at its sphere corners;
they became "min_feature" and forced the minimum mesh size around them, which
stalled the 2D mesher (4/4 timeouts on the filleted_box family).
"""
import pytest

gmsh = pytest.importorskip("gmsh")


def test_degenerate_edges_not_small_features():
    from polyfoammesh.core import gmsh_wrapper as gw

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("dg")
        gmsh.model.occ.addSphere(0, 0, 0, 1.0)  # sphere: degenerate pole edges
        gmsh.model.occ.synchronize()
        feat = gw._scan_feature_sizes(gmsh, 2.0)
        assert feat["min_feature"] > 2.0 * 1e-5
        assert feat["small_cutoff"] > 2.0 * 1e-5
    finally:
        gmsh.finalize()
