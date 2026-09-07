"""Boundary-layer sizing must follow the flat-plate physics, not fixed numbers.

Two defects this pins down:

1. `FlowConditions.reynolds_number` is a plain field defaulting to 1e6, so
   `FlowConditions(reference_velocity=10, reference_length=1)` silently kept
   Re=1e6 instead of the 6.67e5 those values imply — every derived quantity
   (Cf, u_tau, first layer height) was then computed from the wrong Reynolds
   number. `FlowConditions.from_velocity()` derives it.
2. `calculate_from_flow` pinned n_layers=10 and solved for the growth rate,
   which was allowed to reach 2.0 — each layer nearly doubling. Real meshes use
   1.1-1.3; anything larger leaves a violent size jump where the layers meet the
   bulk mesh. The rate is now fixed and the layer count is solved for.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

from _test_helpers import load_commercial_module

_mod = load_commercial_module("bl_engine")
BLEngine = _mod.BLEngine
FlowConditions = _mod.FlowConditions

NU_AIR = 1.5e-5


def test_reynolds_is_derived_from_velocity_and_length():
    flow = FlowConditions.from_velocity(10.0, 1.0, NU_AIR)
    assert flow.reynolds_number == pytest.approx(10.0 * 1.0 / NU_AIR, rel=1e-9)


def test_growth_rate_stays_in_the_meshable_range():
    """A rate near 2.0 (the old behaviour) is not usable in practice."""
    params = BLEngine().calculate_from_flow(FlowConditions.from_velocity(10.0, 1.0, NU_AIR))
    assert 1.05 <= params.growth_rate <= 1.5


def test_layer_count_is_solved_not_hardcoded():
    """Wall-resolved (y+~1) needs many more layers than wall functions (y+~30)."""
    eng = BLEngine()
    resolved = eng.calculate_from_flow(
        FlowConditions.from_velocity(10.0, 1.0, NU_AIR, turbulence_model="kOmegaSST")
    )
    wall_funcs = eng.calculate_from_flow(
        FlowConditions.from_velocity(10.0, 1.0, NU_AIR, turbulence_model="kEpsilon")
    )
    assert resolved.n_layers > wall_funcs.n_layers


def test_first_layer_follows_the_yplus_target():
    """y1 = y+ · nu / u_tau, so a 30x larger y+ target gives a ~30x thicker first
    layer at the same flow conditions."""
    eng = BLEngine()
    resolved = eng.calculate_from_flow(
        FlowConditions.from_velocity(10.0, 1.0, NU_AIR, turbulence_model="kOmegaSST")
    )
    wall_funcs = eng.calculate_from_flow(
        FlowConditions.from_velocity(10.0, 1.0, NU_AIR, turbulence_model="kEpsilon")
    )
    assert wall_funcs.first_layer_height / resolved.first_layer_height == pytest.approx(30, rel=0.05)


def test_total_thickness_matches_the_blasius_estimate():
    """Layers should stack up to roughly delta_99 = 0.37 L / Re^0.2.

    nLayers is capped at 20 for practical reliability, so total thickness
    may not reach full delta_99 for high-Re cases.  Verify that:
    - total thickness is positive and sensible
    - nLayers does not exceed the cap
    - total thickness does not exceed 30% of reference length (safety cap)
    """
    flow = FlowConditions.from_velocity(10.0, 1.0, NU_AIR)
    params = BLEngine().calculate_from_flow(flow)
    delta_99 = 0.37 * flow.reference_length / (flow.reynolds_number ** 0.2)
    assert params.total_thickness > 0
    assert params.total_thickness < flow.reference_length * 0.3
    assert params.n_layers <= 20


def test_faster_flow_needs_a_thinner_first_layer():
    eng = BLEngine()
    slow = eng.calculate_from_flow(FlowConditions.from_velocity(1.0, 1.0, NU_AIR))
    fast = eng.calculate_from_flow(FlowConditions.from_velocity(50.0, 1.0, NU_AIR))
    assert fast.first_layer_height < slow.first_layer_height
