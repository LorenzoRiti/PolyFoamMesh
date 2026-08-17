"""Pre-flight guard: refuse a maxCellSize at or above the geometry scale.

The thresholds asserted here are not taste. They come from a sweep of the
bf_roundtrip fixture (a 0.002 m cylinder) run against real cartesianMesh:

    maxCellSize  vs bbox   exit  cells      maxCellSize  vs bbox  exit  cells
    0.25         125x      134   8          0.0025       1.25x    0     8
    0.20         100x      134   8          0.002        1.0x     0     32
    0.15          75x        0   8          0.001        0.5x     0     120
    0.12          60x      134   8          0.0005       0.25x    0     640
    0.10          50x      134   8          0.0002       0.1x     0     5336
    0.05          25x      134   8          0.0001       0.05x    0     26768

Every size at or above the bounding box collapsed to the same degenerate
8-cell octree; whether cfMesh aborted (exit 134) or returned 0 with a useless
mesh was the only thing that varied. That is why the guard keys on geometry
scale and not on the crash message.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfmesh_autogui.core.openfoam_runner import (  # noqa: E402
    _stl_bbox,
    preflight_cell_size,
)

# A cube of side `size` centred on the origin is enough: the guard only ever
# looks at the bounding box, never at the topology.
_CUBE_TRIS = [
    ((0, 0, 0), (1, 0, 0), (1, 1, 0)),
    ((0, 0, 0), (1, 1, 0), (0, 1, 0)),
    ((0, 0, 1), (1, 0, 1), (1, 1, 1)),
    ((0, 0, 1), (1, 1, 1), (0, 1, 1)),
]


def _write_ascii_stl(path: Path, size: float) -> None:
    lines = ["solid wall"]
    for tri in _CUBE_TRIS:
        lines.append("facet normal 0 0 1")
        lines.append("  outer loop")
        for v in tri:
            lines.append(f"    vertex {v[0] * size:.9g} {v[1] * size:.9g} {v[2] * size:.9g}")
        lines.append("  endloop")
        lines.append("endfacet")
    lines.append("endsolid wall")
    path.write_text("\n".join(lines) + "\n")


def _write_binary_stl(path: Path, size: float) -> None:
    payload = bytearray(b"\0" * 80)
    payload += struct.pack("<I", len(_CUBE_TRIS))
    for tri in _CUBE_TRIS:
        payload += struct.pack("<3f", 0.0, 0.0, 1.0)
        for v in tri:
            payload += struct.pack("<3f", v[0] * size, v[1] * size, v[2] * size)
        payload += struct.pack("<H", 0)
    path.write_bytes(bytes(payload))


def _make_case(tmp_path: Path, max_cell: float, size: float = 0.002,
               binary: bool = False) -> Path:
    case = tmp_path / "case"
    (case / "system").mkdir(parents=True)
    (case / "constant" / "triSurface").mkdir(parents=True)
    stl = case / "constant" / "triSurface" / "surface.stl"
    if binary:
        _write_binary_stl(stl, size)
    else:
        _write_ascii_stl(stl, size)
    (case / "system" / "meshDict").write_text(
        'FoamFile { version 2.0; format ascii; class dictionary; object meshDict; }\n'
        'surfaceFile "constant/triSurface/surface.stl";\n'
        f"maxCellSize {max_cell:g};\n"
        f"minCellSize {max_cell / 2:g};\n"
    )
    return case


# ---------------------------------------------------------------- bbox reader

@pytest.mark.parametrize("binary", [False, True], ids=["ascii", "binary"])
def test_stl_bbox_reads_both_formats(tmp_path, binary):
    stl = tmp_path / "s.stl"
    (_write_binary_stl if binary else _write_ascii_stl)(stl, 0.002)
    bbox = _stl_bbox(stl)
    assert bbox is not None
    for span in bbox:
        assert span == pytest.approx(0.002, rel=1e-4)


def test_stl_bbox_returns_none_on_garbage(tmp_path):
    """Unreadable input must fail OPEN — never block a run we can't parse."""
    junk = tmp_path / "s.stl"
    junk.write_bytes(b"not an stl at all")
    assert _stl_bbox(junk) is None


# ------------------------------------------------------------------- the guard

@pytest.mark.parametrize("max_cell", [0.25, 0.20, 0.15, 0.12, 0.10, 0.05, 0.0025])
def test_sizes_that_produced_a_degenerate_mesh_are_refused(tmp_path, max_cell):
    """Every one of these collapsed to 8 cells against real cartesianMesh.

    0.15 is in this list deliberately: it exits 0, which is exactly why the
    crash message alone was never a sufficient signal.
    """
    issue = preflight_cell_size(_make_case(tmp_path, max_cell))
    assert issue is not None and issue.fatal
    assert "larger than the geometry" in issue.message
    assert "mm" in issue.suggestion  # points at the unit mismatch


@pytest.mark.parametrize("max_cell", [0.0002, 0.0001, 5e-5])
def test_sizes_that_meshed_cleanly_are_allowed(tmp_path, max_cell):
    assert preflight_cell_size(_make_case(tmp_path, max_cell)) is None


def test_measured_cliff_is_where_the_guard_switches(tmp_path):
    """<=1.0x bbox always meshed; >=1.25x bbox never did."""
    at_cliff = preflight_cell_size(_make_case(tmp_path / "a", 0.002))
    past_cliff = preflight_cell_size(_make_case(tmp_path / "b", 0.0025))
    assert at_cliff is not None and not at_cliff.fatal  # coarse, but allowed
    assert past_cliff is not None and past_cliff.fatal


def test_coarse_but_runnable_only_warns(tmp_path):
    """Between the fatal and warn thresholds the run proceeds."""
    issue = preflight_cell_size(_make_case(tmp_path, 0.001))
    assert issue is not None
    assert not issue.fatal
    assert "very coarse" in issue.message


def test_binary_stl_is_guarded_too(tmp_path):
    issue = preflight_cell_size(_make_case(tmp_path, 0.25, binary=True))
    assert issue is not None and issue.fatal


# ------------------------------------------------------- must never block a run

def test_missing_meshdict_is_silent(tmp_path):
    (tmp_path / "system").mkdir()
    assert preflight_cell_size(tmp_path) is None


def test_non_stl_surface_is_silent(tmp_path):
    case = _make_case(tmp_path, 0.25)
    (case / "system" / "meshDict").write_text(
        'surfaceFile "constant/triSurface/surface.fms";\nmaxCellSize 0.25;\n'
    )
    assert preflight_cell_size(case) is None


def test_missing_stl_is_silent(tmp_path):
    case = _make_case(tmp_path, 0.25)
    (case / "constant" / "triSurface" / "surface.stl").unlink()
    assert preflight_cell_size(case) is None


def test_flat_dimension_does_not_trigger_a_false_positive(tmp_path):
    """A 2D-ish extrusion has a zero span; that must not read as '0 cells'."""
    case = _make_case(tmp_path, 0.0001)
    stl = case / "constant" / "triSurface" / "surface.stl"
    text = stl.read_text().replace("vertex 0.002 0.002 0.002", "vertex 0.002 0.002 0")
    # flatten z entirely
    out = []
    for line in text.splitlines():
        if line.strip().startswith("vertex"):
            parts = line.split()
            out.append(f"    vertex {parts[1]} {parts[2]} 0")
        else:
            out.append(line)
    stl.write_text("\n".join(out) + "\n")
    assert preflight_cell_size(case) is None
