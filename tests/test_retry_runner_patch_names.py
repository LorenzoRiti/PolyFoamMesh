"""RetryRunner's BL-failure fallback must not silently un-type patches.

`_regenerate_meshdict_without_bl()` is the automatic retry that fires whenever
boundary layers fail — common on complex or thin geometry — and it used to
call write_meshdict() without `patch_names=`, so `renameBoundary` never got
emitted on that retry. Since RetryRunner is the SAME class the main "Generate
Mesh" button uses (not just an unwired workflow), any real case whose BL
retried would silently revert every inlet/outlet patch to cfMesh's default
`wall` type — invisible to the user, and fatal to the physics of the case.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

pytest.importorskip("PySide6")

from polyfoammesh.config import OFConfig  # noqa: E402
from polyfoammesh.core.openfoam_runner import RetryRunner  # noqa: E402


@pytest.fixture
def runner():
    return RetryRunner(OFConfig())


def _prep(runner, patch_names):
    runner._case_dir = Path(tempfile.mkdtemp())
    (runner._case_dir / "system").mkdir(parents=True, exist_ok=True)
    runner._max_cell = 0.25
    runner._min_cell = 0.125
    runner._patch_cell_size = None
    runner._patch_names = patch_names


def test_run_stores_patch_names(runner, monkeypatch):
    """run() must retain patch_names for a later fallback retry — verified
    without actually starting a QThread/subprocess."""
    monkeypatch.setattr(RetryRunner, "_do_attempt", lambda self: None)
    runner.run(
        case_dir=tempfile.mkdtemp(),
        patch_names=["inlet", "outlet", "wall"],
    )
    assert runner._patch_names == ["inlet", "outlet", "wall"]


def test_bl_fallback_regeneration_still_types_patches(runner):
    _prep(runner, ["inlet", "outlet", "wall"])
    runner._regenerate_meshdict_without_bl()

    content = (runner._case_dir / "system" / "meshDict").read_text()
    assert "renameBoundary" in content
    assert '"inlet"' in content
    assert "type        patch;" in content


def test_bl_fallback_without_patch_names_does_not_crash(runner):
    """patch_names is optional — omitting it must degrade gracefully (no
    renameBoundary, not an exception), not break the fallback entirely."""
    _prep(runner, None)
    runner._regenerate_meshdict_without_bl()
    content = (runner._case_dir / "system" / "meshDict").read_text()
    assert "maxCellSize" in content
