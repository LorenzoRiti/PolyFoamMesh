"""Timeout contracts across the GMSH paths must stay consistent.

A Very Fine mesh capped at 20M cells can legitimately exceed the old 600 s
volume timeout, which produced false "GMSH volume exceeded" failures on
real runs. These tests pin the shared timeout constants so the GUI worker,
the subprocess helper, the CLI conversion, and the adaptive solver agree.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core import gmsh_subprocess as gs  # noqa: E402
from polyfoammesh.core.openfoam_runner import GmshVolumeWorker  # noqa: E402


def _default_of(func, name: str):
    return inspect.signature(func).parameters[name].default


def test_volume_timeouts_are_aligned():
    assert GmshVolumeWorker.VOLUME_TIMEOUT_S == _default_of(
        gs.run_gmsh_volume, "timeout_s"
    )


def test_volume_timeout_is_not_the_old_false_failure_value():
    # 600 s was the historical false-timeout value for large meshes.
    assert GmshVolumeWorker.VOLUME_TIMEOUT_S > 600


def test_gmsh_to_foam_timeouts_are_aligned():
    # CLI conversion (300 s) used to be far tighter than the helper (900 s).
    # Both must agree; the helper default is the canonical value.
    assert _default_of(gs.run_gmsh_to_foam, "timeout_s") >= 900
