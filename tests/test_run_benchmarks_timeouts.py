"""Legacy defaults of run_benchmarks stay intact; new timeout kwargs exist."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks import run_benchmarks as rb  # noqa: E402


def test_legacy_timeouts_unchanged() -> None:
    assert rb.TIMEOUT_S == 180


def test_mesh_and_check_accept_timeout_kwarg() -> None:
    assert inspect.signature(rb._run_cartesian_mesh).parameters["timeout"].default == rb.TIMEOUT_S
    assert inspect.signature(rb._run_check_mesh).parameters["timeout"].default == rb.TIMEOUT_S


def test_benchmark_one_accepts_timeout_kwargs_with_legacy_defaults() -> None:
    sig = inspect.signature(rb._benchmark_one)
    assert sig.parameters["mesh_timeout_s"].default == rb.TIMEOUT_S
    assert sig.parameters["check_timeout_s"].default == rb.TIMEOUT_S


def test_benchmark_poly_one_accepts_stage_timeouts_with_legacy_defaults() -> None:
    sig = inspect.signature(rb._benchmark_poly_one)
    assert sig.parameters["gmsh_timeout_s"].default == 600
    assert sig.parameters["gmsh_to_foam_timeout_s"].default == 600
    assert sig.parameters["check_timeout_s"].default == rb.TIMEOUT_S


def test_prepare_geometry_can_skip_watertight_gate() -> None:
    sig = inspect.signature(rb._prepare_geometry)
    assert sig.parameters["require_watertight"].default is True
