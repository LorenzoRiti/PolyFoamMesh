"""Tests for CAD defeaturing & healing module."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module

_mod = load_commercial_module("cad_healer")
CADHealer = _mod.CADHealer
HealReport = _mod.HealReport

import numpy as np
import trimesh


def _make_test_mesh() -> trimesh.Trimesh:
    return trimesh.creation.box(extents=[1.0, 1.0, 1.0])


def _make_non_watertight_mesh() -> trimesh.Trimesh:
    verts = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
    ], dtype=np.float64)
    faces = np.array([
        [0, 1, 2], [0, 2, 3],
        [1, 5, 6], [1, 6, 2],
        [5, 4, 7], [5, 7, 6],
        [4, 0, 3], [4, 3, 7],
        [3, 2, 6], [3, 6, 7],
    ], dtype=np.int64)
    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    return mesh


def test_heal_report_defaults():
    r = HealReport()
    assert r.original_faces == 0
    assert r.holes_filled == 0
    assert not r.watertight
    assert r.face_reduction_pct == 0.0


def test_heal_mesh_watertight():
    healer = CADHealer()
    mesh = _make_test_mesh()
    report = healer.heal_mesh(mesh)
    assert report.watertight, "Box mesh should be watertight"
    assert report.original_faces > 0


def test_heal_mesh_non_watertight():
    healer = CADHealer()
    mesh = _make_non_watertight_mesh()
    report = healer.heal_mesh(mesh)
    assert report.original_faces > 0
    assert "fixed face normals" in report.operations


def test_detect_small_features():
    healer = CADHealer()
    mesh = _make_test_mesh()
    features = healer.detect_small_features(mesh)
    assert features["n_faces"] > 0
    assert features["n_vertices"] > 0
    assert features["watertight"]


def test_detect_small_features_empty():
    healer = CADHealer()
    empty = trimesh.Trimesh()
    features = healer.detect_small_features(empty)
    assert "error" in features


def test_summarise_empty():
    healer = CADHealer()
    summary = healer.summarise([])
    assert summary["patches"] == 0
    assert summary["total_original_faces"] == 0


def test_summarise_one_report():
    healer = CADHealer()
    mesh = _make_test_mesh()
    report = healer.heal_mesh(mesh)
    summary = healer.summarise([report])
    assert summary["patches"] == 1
    assert summary["total_original_faces"] > 0


def test_heal_meshes_batch():
    healer = CADHealer()
    meshes = [_make_test_mesh(), _make_non_watertight_mesh()]
    reports = healer.heal_meshes(meshes)
    assert len(reports) == 2
    assert reports[0].watertight


def test_custom_thresholds():
    healer = CADHealer(thresholds={"hole_diameter": 0.01})
    assert healer._thresholds["hole_diameter"] == 0.01
    assert healer._thresholds["sliver_area"] == 1e-6


if __name__ == "__main__":
    test_heal_report_defaults()
    test_heal_mesh_watertight()
    test_heal_mesh_non_watertight()
    test_detect_small_features()
    test_detect_small_features_empty()
    test_summarise_empty()
    test_summarise_one_report()
    test_heal_meshes_batch()
    test_custom_thresholds()
    print("ALL PASS")
