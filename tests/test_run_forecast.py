from polyfoammesh.core.run_forecast import forecast_text


def test_forecast_mentions_stages_time_and_silence():
    t = forecast_text(100_000, engine="ftetwild_poly", boundary_layer=True)
    assert "1) tet mesh" in t and "boundary layer" in t and "checkMesh" in t
    assert "100,000 cells" in t and "min" in t
    assert "not frozen" in t


def test_forecast_without_estimate_and_without_bl():
    t = forecast_text(None)
    assert "boundary layer" not in t and "cells:" not in t


def test_forecast_suggests_coarser_when_long():
    assert "Max cell size" in forecast_text(2_000_000)
    assert "Max cell size" not in forecast_text(20_000)
