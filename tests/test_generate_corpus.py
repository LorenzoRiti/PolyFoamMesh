"""Every corpus STL exists; watertightness matches the spec (dirty_cad is not)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import trimesh

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks.corpus import CORPUS, geometry_path  # noqa: E402


def _regenerate_into(out_dir: Path) -> subprocess.CompletedProcess:
    """Run the corpus generator with its output REDIRECTED to *out_dir*.

    Regression (2026-10-11): the generator used to write straight into
    ``benchmarks/geometries`` - running the test suite silently rewrote
    the tracked corpus files (they no longer match the generator
    byte-for-byte since a couple of geometries were rebuilt by hand), so
    every suite run left the checkout dirty and could invalidate
    benchmark baselines.  The ``POLYFOAMMESH_CORPUS_OUT`` override keeps
    regeneration inside a temporary directory.
    """
    env = dict(os.environ)
    env["POLYFOAMMESH_CORPUS_OUT"] = str(out_dir)
    return subprocess.run(
        [sys.executable, str(_REPO / "benchmarks" / "generate_corpus.py")],
        cwd=_REPO, capture_output=True, text=True, env=env,
    )


@pytest.fixture(scope="module", autouse=True)
def _generate_once(tmp_path_factory) -> None:
    """Run the generator once for this module (deterministic, ~seconds)."""
    missing = [s for s in CORPUS if not geometry_path(s).exists()]
    if missing:
        out = tmp_path_factory.mktemp("corpus_regen")
        _regenerate_into(out)


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


def test_geometry_definitions_match_generator_output(tmp_path) -> None:
    # Regenerating must succeed and produce every file - WITHOUT touching
    # the tracked corpus (output goes to tmp_path).
    r = _regenerate_into(tmp_path)
    assert r.returncode == 0, r.stderr
    produced = sorted(p.name for p in tmp_path.glob("*"))
    assert produced, "the generator produced no files"
    for spec in CORPUS:
        if spec.name == "thin_gap":
            continue  # pre-existing geometry, not generated
        assert any(p.startswith(spec.name) for p in produced), (
            f"generator did not produce {spec.name}: {produced}"
        )
