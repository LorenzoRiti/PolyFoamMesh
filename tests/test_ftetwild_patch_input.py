"""fTetWild on a STEP must read the patches already loaded in the GUI, not
go through GMSH's surface mesher first (DN1000: 6+ min then failure via
GMSH, 30 s from the loaded patches).  The patch STL is already scaled, so
the worker must not scale it again."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core.stl_writer import validate_stl_solids  # noqa: E402
from polyfoammesh.gui import main_window as mw  # noqa: E402


def _window(meshes):
    logs: list[str] = []
    win = SimpleNamespace(
        _meshes=meshes,
        _log=SimpleNamespace(append_log=logs.append),
        _ftw_stl_fallback_done=False,
    )
    return win, logs


def _patches():
    wall = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
    wall.metadata["name"] = "wall"
    inlet = trimesh.creation.box(extents=(0.2, 0.2, 0.2))
    inlet.apply_translation((2.0, 0.0, 0.0))
    inlet.metadata["name"] = "inlet"
    return [wall, inlet]


def _choose(win, step, tmp_path, zones=None):
    return mw.MainWindow._ftetwild_input_path(
        win, str(step), tmp_path / "case" / "mesh.msh", zones,
    )


def test_step_input_uses_the_loaded_patches(tmp_path):
    (tmp_path / "case").mkdir()
    win, logs = _window(_patches())

    out = _choose(win, tmp_path / "valve.stp", tmp_path)

    assert Path(out).name == mw._FTW_PATCH_STL_NAME
    assert Path(out).parent == tmp_path / "case"
    # patch names survive: fTetWild recovers them from the ASCII solids
    assert set(validate_stl_solids(out)) == {"wall", "inlet"}
    assert win._ftw_stl_fallback_done is True
    assert any("GMSH surface stage skipped" in m for m in logs)


def test_refinement_zones_keep_the_gmsh_surface(tmp_path):
    win, logs = _window(_patches())
    step = tmp_path / "valve.step"
    assert _choose(win, step, tmp_path, zones=[{"type": "box"}]) == str(step)
    assert any("Refinement zones" in m for m in logs)


def test_stl_input_and_empty_memory_are_left_alone(tmp_path):
    win, _ = _window(_patches())
    stl = tmp_path / "part.stl"
    assert _choose(win, stl, tmp_path) == str(stl)

    empty, _ = _window([])
    step = tmp_path / "valve.stp"
    assert _choose(empty, step, tmp_path) == str(step)


def test_patch_stl_is_not_scaled_twice():
    src = inspect.getsource(mw.MainWindow._start_gmsh_volume_worker)
    assert "_ftetwild_input_path(" in src
    assert "1.0 if Path(step_path).name == _FTW_PATCH_STL_NAME" in src
