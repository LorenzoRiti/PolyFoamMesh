"""Tests for automatic watertight-repair (core/geometry_repair.py)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pytest
import trimesh

from cfmesh_autogui.core.geometry_repair import (
    attempt_auto_repair, snap_close_gaps, repair_with_meshfix,
)


def _box_missing_top_face() -> trimesh.Trimesh:
    """A box with its top face's two triangles omitted — a genuine open
    hole that vertex-snapping cannot close (there's no near-duplicate
    vertex pair to merge, just an actually missing face)."""
    verts = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
    ], dtype=np.float64)
    faces = np.array([
        [0, 1, 2], [0, 2, 3],       # bottom
        [1, 5, 6], [1, 6, 2],       # +x side
        [0, 4, 5], [0, 5, 1],       # -y side
        [2, 6, 7], [2, 7, 3],       # +y side
        [3, 7, 4], [3, 4, 0],       # -x side
        # top face (4,5,6,7) intentionally omitted -> open hole
    ])
    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    mesh.metadata["name"] = "wall"
    return mesh


def test_snap_close_gaps_cannot_fix_a_real_missing_face():
    mesh = _box_missing_top_face()
    assert not mesh.is_watertight

    repaired, report = snap_close_gaps([mesh], bbox_dim=1.0)

    assert report.attempted
    assert not report.watertight_before
    assert report.method == "snap"
    assert report.snap_tolerance == pytest.approx(1e-4)
    # A genuinely missing face has no near-duplicate vertices to merge —
    # snapping alone must not claim success here.
    assert not report.watertight_after
    assert report.open_edges_after == report.open_edges_before
    assert len(repaired) == 1


def test_snap_close_gaps_noop_when_already_watertight():
    box = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
    box.metadata["name"] = "wall"
    repaired, report = snap_close_gaps([box], bbox_dim=1.0)

    assert report.watertight_before is True
    assert report.method == ""  # never needed to attempt anything
    assert repaired is not None


def test_repair_with_meshfix_closes_real_gap():
    """A box with two triangles removed from one face — genuinely open,
    the kind of defect snapping alone can't fix."""
    mesh = _box_missing_top_face()
    assert not mesh.is_watertight

    repaired, report = repair_with_meshfix([mesh])

    assert report.attempted
    assert report.method == "meshfix"
    assert report.open_edges_before > 0
    # MeshFix should be able to close a single missing quad face.
    assert report.watertight_after
    assert report.open_edges_after == 0
    combined = trimesh.util.concatenate(repaired)
    combined.merge_vertices()
    assert combined.is_watertight


def test_attempt_auto_repair_escalates_to_meshfix_when_snap_insufficient():
    mesh = _box_missing_top_face()

    repaired, reports = attempt_auto_repair([mesh], bbox_dim=1.0)

    assert len(reports) == 2  # snap tried first, then meshfix
    assert reports[0].method == "snap"
    assert reports[1].method == "meshfix"
    assert reports[-1].watertight_after


def test_attempt_auto_repair_stops_after_snap_if_already_fixed():
    box = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
    box.metadata["name"] = "wall"
    repaired, reports = attempt_auto_repair([box], bbox_dim=1.0)

    assert len(reports) == 1  # already watertight — no need to escalate
    assert reports[0].watertight_before is True
