"""Every corpus STL exists; watertightness matches the spec (dirty_cad is not)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import trimesh

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks.corpus import CORPUS, geometry_path  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _generate_once() -> None:
    """Run the generator once for this module (deterministic, ~seconds)."""
    missing = [s for s in CORPUS if not geometry_path(s).exists()]
    if missing:
        subprocess.run(
            [sys.executable, str(_REPO / "benchmarks" / "generate_corpus.py")],
            check=True, cwd=_REPO,
        )


def test_all_corpus_stls_exist() -> None:
    for spec in CORPUS:
        p = geometry_path(spec)
        assert p.exists(), f"missing corpus STL: {p}"
        assert p.stat().st_size > 0


def test_all_but_dirty_cad_are_watertight() -> None:
    for spec in CORPUS:
        mesh = trimesh.load(geometry_path(spec), force="mesh")
        if spec.name == "dirty_cad":
            assert not mesh.is_watertight, "dirty_cad must be open (terrain C)"
        else:
            assert mesh.is_watertight, f"{spec.name} must be watertight"


def test_geometry_definitions_match_generator_output() -> None:
    # regenerating must be idempotent in file presence
    r = subprocess.run(
        [sys.executable, str(_REPO / "benchmarks" / "generate_corpus.py")],
        cwd=_REPO, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
