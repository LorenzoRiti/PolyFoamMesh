"""A failed GMSH run must not poison the next one: the in-process session
is closed on failure (one bad STL used to turn every later geometry into a
silent fTetWild fallback)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core import gmsh_wrapper, tet_source  # noqa: E402


def test_gmsh_session_is_closed_when_the_run_fails(monkeypatch, tmp_path):
    closed = []

    def boom(*_args, **_kwargs):
        raise RuntimeError("Error loading bad.stl")

    monkeypatch.setattr(gmsh_wrapper, "generate_volume_mesh", boom)
    monkeypatch.setattr(gmsh_wrapper, "gmsh_shutdown", lambda: closed.append(1))

    with pytest.raises(RuntimeError, match="Error loading"):
        tet_source._default_gmsh_runner(
            tet_source.TetSourceSettings(source="gmsh"),
            tmp_path / "bad.stl", tmp_path / "out.msh",
        )
    assert closed == [1]


def test_gmsh_session_is_kept_when_the_run_succeeds(monkeypatch, tmp_path):
    closed = []
    out = tmp_path / "out.msh"
    monkeypatch.setattr(
        gmsh_wrapper, "generate_volume_mesh",
        lambda *_a, **_k: (out, ["wall"]),
    )
    monkeypatch.setattr(gmsh_wrapper, "gmsh_shutdown", lambda: closed.append(1))

    path, names, _stats, _stage = tet_source._default_gmsh_runner(
        tet_source.TetSourceSettings(source="gmsh"),
        tmp_path / "ok.stl", out,
    )
    assert (path, names) == (str(out), ["wall"])
    assert closed == []
