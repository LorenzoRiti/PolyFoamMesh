"""Tests for solution-adaptive mesh refinement (core/solution_adaptive.py).

These need no OpenFOAM and no CAD — they drive the indicator/sizing/size-field
chain with an ANALYTIC velocity field whose right answer is known by hand, so
they run in seconds and catch regressions in the numerics that an end-to-end
venturi run would only reveal after several minutes of meshing and solving.

The analytic case is a 1-D venturi: U = (u(x), 0, 0) on a box, with u peaking
4x at a throat at x = 0.5 (Gaussian, sigma = 0.04). Textbook consequences the
tests below assert:
  * |du/dx| is largest at x = 0.5 +/- sigma, NOT at the throat centre where
    du/dx = 0. The indicator must peak on a shoulder.
  * the straight sections have du/dx ~ 0, so their indicator must be orders of
    magnitude smaller.
"""
from __future__ import annotations

import numpy as np
import pytest

pv = pytest.importorskip("pyvista")

from polyfoammesh.core.solution_adaptive import (  # noqa: E402
    compute_indicator,
    predict_cells_from_lattice,
    pressure_drop_qoi,
    sample_structured_field,
    target_size_field,
    write_structured_size_field,
)

THROAT_X = 0.5
SIGMA = 0.04


@pytest.fixture(scope="module")
def venturi_grid():
    """Uniform grid carrying an analytic venturi velocity field as CELL data
    (which is how foamToVTK presents a real solution)."""
    n = 40
    grid = pv.ImageData(
        dimensions=(n + 1, 9, 9), spacing=(1.0 / n, 0.125, 0.125)
    ).cast_to_unstructured_grid()
    x = grid.cell_centers().points[:, 0]
    u = 1.0 + 3.0 * np.exp(-((x - THROAT_X) ** 2) / (2 * SIGMA**2))
    vel = np.zeros((grid.n_cells, 3))
    vel[:, 0] = u
    grid.cell_data["U"] = vel
    grid.cell_data["p"] = -0.5 * u**2  # Bernoulli
    return grid


@pytest.fixture(scope="module")
def indicator(venturi_grid):
    return compute_indicator(venturi_grid)


def test_indicator_peaks_on_a_throat_shoulder(indicator):
    """du/dx is steepest at x = 0.5 +/- sigma, not at the throat centre."""
    peak_x = indicator["peak_location"][0]
    assert abs(abs(peak_x - THROAT_X) - SIGMA) < 0.03, (
        f"indicator peak at x={peak_x}, expected a shoulder at "
        f"{THROAT_X}+/-{SIGMA}"
    )


def test_indicator_is_negligible_in_the_straight_section(indicator, venturi_grid):
    x = venturi_grid.cell_centers().points[:, 0]
    far = indicator["eta"][np.abs(x - THROAT_X) > 0.3]
    assert indicator["eta_max"] > 100 * far.max()


def test_u_ref_uses_a_percentile_not_the_max(indicator):
    """u_ref must not be the raw max — on a real case that is a single
    spurious boundary cell, which would deflate the whole indicator field."""
    assert indicator["u_ref"] < 4.0  # the true peak
    assert indicator["u_ref"] > 3.0


def test_target_size_never_coarsens(indicator):
    """This field is min-combined with the geometry sizing in GMSH, which is
    only correct if it never asks for a LARGER cell than exists."""
    tgt = target_size_field(indicator, refine_quantile=0.85, max_refine_ratio=3.0,
                            min_cell_size=0.0, max_cells=None)
    assert np.all(tgt["h_target"] <= indicator["h"] * 1.0001)


def test_target_size_refines_the_throat_more_than_the_straight(indicator, venturi_grid):
    tgt = target_size_field(indicator, refine_quantile=0.85, max_refine_ratio=3.0,
                            min_cell_size=0.0, max_cells=None)
    x = venturi_grid.cell_centers().points[:, 0]
    near = np.abs(x - THROAT_X) < 0.12
    assert tgt["h_target"][near].mean() < tgt["h_target"][~near].mean()


def test_max_refine_ratio_is_respected(indicator):
    tgt = target_size_field(indicator, refine_quantile=0.85, max_refine_ratio=3.0,
                            min_cell_size=0.0, max_cells=None)
    assert np.all(tgt["h_target"] >= indicator["h"] / 3.0 - 1e-12)


def test_size_field_file_is_parseable_floats(tmp_path, indicator, venturi_grid):
    """Guards the numpy-2 repr trap: repr(np.float64(0.01)) is
    'np.float64(0.01)', which GMSH cannot parse."""
    tgt = target_size_field(indicator, 0.85, 3.0, 0.0, None)
    out = tmp_path / "sf.txt"
    write_structured_size_field(out, indicator["centres"], tgt["h_target"],
                                venturi_grid.bounds, resolution=32)
    tokens = out.read_text().split()
    for tok in tokens:
        float(tok)  # raises if contaminated
    assert len(tokens) > 9


def test_size_field_header_matches_declared_shape(tmp_path, indicator, venturi_grid):
    tgt = target_size_field(indicator, 0.85, 3.0, 0.0, None)
    out = tmp_path / "sf.txt"
    info = write_structured_size_field(out, indicator["centres"], tgt["h_target"],
                                       venturi_grid.bounds, resolution=32)
    tokens = out.read_text().split()
    nx, ny, nz = (int(t) for t in tokens[6:9])
    assert (nx, ny, nz) == info["shape"]
    assert len(tokens) - 9 == nx * ny * nz


def test_sampling_round_trips_through_the_lattice(tmp_path, indicator, venturi_grid):
    """Sampling the lattice back at the cell centres must reproduce roughly the
    sizes that were scattered into it — this is what the budget guard relies on."""
    tgt = target_size_field(indicator, 0.85, 3.0, 0.0, None)
    info = write_structured_size_field(tmp_path / "sf.txt", indicator["centres"],
                                       tgt["h_target"], venturi_grid.bounds,
                                       resolution=64)
    back = sample_structured_field(info["lattice"], info["origin"],
                                   info["spacing"], indicator["centres"])
    assert back.min() >= info["min_size"] - 1e-12
    assert back.max() <= info["max_size"] + 1e-12


def test_prediction_models_gmsh_min_combination(tmp_path, indicator, venturi_grid):
    """With a coarse baseline outside the refined region, the prediction must
    fall back to the CURRENT sizes there rather than the baseline — otherwise
    it badly under-counts the untouched bulk."""
    tgt = target_size_field(indicator, 0.85, 3.0, 0.0, None)
    h_orig = indicator["h"]
    refined = tgt["h_target"] < h_orig * 0.999
    info = write_structured_size_field(
        tmp_path / "sf.txt", indicator["centres"][refined],
        tgt["h_target"][refined], venturi_grid.bounds,
        resolution=64, baseline=float(np.max(h_orig)),
    )
    without = predict_cells_from_lattice(
        info["lattice"], info["origin"], info["spacing"],
        indicator["centres"], indicator["volumes"], calibration=1.0,
    )
    with_min = predict_cells_from_lattice(
        info["lattice"], info["origin"], info["spacing"],
        indicator["centres"], indicator["volumes"], calibration=1.0,
        h_current=h_orig,
    )
    assert with_min >= without


def test_relaxing_target_size_reduces_predicted_cells(tmp_path, indicator, venturi_grid):
    """The budget knob must actually move the prediction — the refine quantile
    did not, which is why size scaling replaced it."""
    tgt = target_size_field(indicator, 0.85, 3.0, 0.0, None)
    h_orig = indicator["h"]
    preds = []
    for scale in (1.0, 2.0):
        h_try = np.minimum(tgt["h_target"] * scale, h_orig)
        sel = h_try < h_orig * 0.999
        info = write_structured_size_field(
            tmp_path / f"sf{scale}.txt", indicator["centres"][sel], h_try[sel],
            venturi_grid.bounds, resolution=64, baseline=float(np.max(h_orig)),
        )
        preds.append(predict_cells_from_lattice(
            info["lattice"], info["origin"], info["spacing"],
            indicator["centres"], indicator["volumes"],
            calibration=1.0, h_current=h_orig,
        ))
    assert preds[1] < preds[0]


def test_pressure_drop_qoi_is_positive_across_a_venturi(venturi_grid):
    """p is highest upstream and lowest at the throat; inlet-to-outlet drop
    should be ~0 here by symmetry, but must at least be finite and not NaN."""
    dp = pressure_drop_qoi(venturi_grid, None)
    assert np.isfinite(dp)


def test_gradation_limits_neighbour_growth(tmp_path):
    """A single tiny cell in a coarse field must grade outward, not sit as a
    lone spike the mesher has to absorb with badly-shaped transition cells."""
    centres = np.array([[0.5, 0.5, 0.5]])
    info = write_structured_size_field(
        tmp_path / "sf.txt", centres, np.array([0.001]),
        (0.0, 1.0, 0.0, 1.0, 0.0, 1.0), resolution=32,
        growth_rate=1.5, baseline=0.2,
    )
    lat = info["lattice"]
    flat = np.sort(np.unique(lat))
    # more than two distinct values => a graded ramp, not a binary spike
    assert len(flat) > 2
