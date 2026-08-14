"""Tests for Fase 3 (cfMesh plan): checkMesh problem-set parsing and the
surgical local-refinement-box generator built on top of it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pytest

from cfmesh_autogui.core import foam_mesh_io
from _test_helpers import load_commercial_module

_mod = load_commercial_module("quality_engine")
local_refinement_boxes_from_checkmesh_sets = _mod.local_refinement_boxes_from_checkmesh_sets


HEADER_BINARY = (
    "FoamFile\n{\n    version     2.0;\n    format      binary;\n"
    "    arch        \"LSB;label=32;scalar=64\";\n    class       cellSet;\n"
    "    location    \"constant/polyMesh/sets\";\n    object      testSet;\n}\n"
    "// end header\n\n"
)


def test_read_label_list_short_ascii_body_despite_binary_header(tmp_path):
    """checkMesh writes SHORT lists in ASCII text even inside a file whose
    header declares 'format binary;' — the naive binary-decode path used
    to interpret that ASCII text as raw int32 bytes and return garbage."""
    p = tmp_path / "nonClosedCells"
    p.write_text(HEADER_BINARY + "3\n(\n0\n1\n4\n)\n", encoding="ascii")
    ids = foam_mesh_io.read_label_list(p)
    assert ids.tolist() == [0, 1, 4]


def test_read_label_list_compact_single_line_form(tmp_path):
    """A short list can also be collapsed onto one line: 'N(a b c)'."""
    p = tmp_path / "outOfRangeFaces"
    p.write_text(HEADER_BINARY + "3(52927 52928 50056)\n", encoding="ascii")
    ids = foam_mesh_io.read_label_list(p)
    assert ids.tolist() == [52927, 52928, 50056]


def test_read_label_list_genuine_binary_still_works(tmp_path):
    """A real binary payload (large mesh index values, e.g. owner/neighbour
    on a real mesh) must still be decoded as binary, not misdetected as
    ASCII by the new short-list heuristic."""
    values = np.arange(0, 5000, dtype=np.int32)
    header = HEADER_BINARY.replace("cellSet", "labelList")
    # Real OpenFOAM binary lists have the raw payload immediately after
    # '(' with NO newline in between (unlike the ASCII form) -- matches
    # binary_header_parse's own "N\n(" match (not "N\n(\n").
    body = f"{len(values)}\n(".encode("ascii") + values.tobytes() + b")\n"
    p = tmp_path / "owner"
    p.write_bytes(header.encode("ascii") + body)
    ids = foam_mesh_io.read_label_list(p)
    assert ids.tolist() == values.tolist()


def _write_tiny_cube_polymesh(poly_dir: Path) -> None:
    """A single unit-cube cell (8 points, 6 quad faces, all boundary)."""
    pts = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
    ], dtype=np.float64)
    faces = [
        [0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4],
        [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7],
    ]
    owner = np.zeros(6, dtype=np.int32)
    neighbour = np.zeros(0, dtype=np.int32)
    patches = [{"name": "walls", "type": "wall", "nFaces": 6, "startFace": 0}]
    poly_dir.mkdir(parents=True, exist_ok=True)
    foam_mesh_io.write_points(poly_dir / "points", pts)
    foam_mesh_io.write_faces(poly_dir / "faces", faces)
    foam_mesh_io.write_labels(poly_dir / "owner", owner)
    foam_mesh_io.write_labels(poly_dir / "neighbour", neighbour)
    foam_mesh_io.write_boundary(poly_dir / "boundary", patches)


def test_local_refinement_boxes_from_cell_set(tmp_path):
    case_dir = tmp_path / "case"
    poly_dir = case_dir / "constant" / "polyMesh"
    _write_tiny_cube_polymesh(poly_dir)
    sets_dir = poly_dir / "sets"
    sets_dir.mkdir()
    (sets_dir / "nonClosedCells").write_text(
        HEADER_BINARY + "1\n(\n0\n)\n", encoding="ascii",
    )

    boxes = local_refinement_boxes_from_checkmesh_sets(case_dir, cell_size=0.1)
    assert len(boxes) == 1
    b = boxes[0]
    assert b["source_set"] == "nonClosedCells"
    assert b["n_cells"] == 1
    # Box must enclose the unit cube (0..1) with some positive padding.
    assert b["xmin"] < 0.0 and b["xmax"] > 1.0
    assert b["cell_size"] == pytest.approx(0.1)


def test_local_refinement_boxes_no_sets_dir_returns_empty(tmp_path):
    case_dir = tmp_path / "case"
    poly_dir = case_dir / "constant" / "polyMesh"
    _write_tiny_cube_polymesh(poly_dir)
    boxes = local_refinement_boxes_from_checkmesh_sets(case_dir, cell_size=0.1)
    assert boxes == []


def test_local_refinement_boxes_zero_cell_size_returns_empty(tmp_path):
    case_dir = tmp_path / "case"
    boxes = local_refinement_boxes_from_checkmesh_sets(case_dir, cell_size=0.0)
    assert boxes == []
