from __future__ import annotations

import sys

import pytest

from polyfoammesh.core.feature_detector import (
    FeatureDetector, FeatureMap, SharpEdge, GapRegion, FeatureDetectWorker,
)


class TestFeatureDetector:
    def test_sharp_edge_dataclass(self):
        edge = SharpEdge(
            point_a=(0, 0, 0),
            point_b=(1, 0, 0),
            angle=90.0,
            length=1.0,
        )
        assert edge.point_a == (0, 0, 0)
        assert edge.point_b == (1, 0, 0)
        assert edge.angle == 90.0
        assert edge.length == 1.0

    def test_gap_region_dataclass(self):
        gap = GapRegion(
            center=(0, 0, 0),
            gap_width=0.001,
            normal=(0, 0, 1),
        )
        assert gap.center == (0, 0, 0)
        assert gap.gap_width == 0.001

    def test_feature_map_defaults(self):
        fm = FeatureMap()
        assert fm.sharp_edges == []
        assert fm.gap_regions == []
        assert fm.curvature_radius == 1.0
        assert fm.suggested_min_cell == 0.01
        assert fm.suggested_max_cell == 0.1

    def test_feature_map_with_data(self):
        fm = FeatureMap(
            sharp_edges=[SharpEdge((0,0,0), (1,0,0), 90, 1.0)],
            gap_regions=[GapRegion((0,0,0), 0.001, (0,0,1))],
            curvature_radius=0.5,
            suggested_min_cell=0.005,
            suggested_max_cell=0.05,
        )
        assert len(fm.sharp_edges) == 1
        assert len(fm.gap_regions) == 1
        assert fm.curvature_radius == 0.5

    def test_suggest_cell_sizes_bbox_only(self):
        detector = FeatureDetector()
        fm = FeatureMap()
        bbox_dim = 10.0
        s_min, s_max = detector.suggest_cell_sizes(fm, bbox_dim)
        assert s_min > 0
        assert s_max > s_min
        assert abs(s_min - 10.0 * 0.005) < 1e-6
        assert abs(s_max - 10.0 * 0.05) < 1e-6

    def test_suggest_cell_sizes_with_sharp_edges(self):
        detector = FeatureDetector()
        fm = FeatureMap(
            sharp_edges=[SharpEdge((0,0,0), (0.01, 0, 0), 90, 0.01)],
        )
        bbox_dim = 10.0
        s_min, s_max = detector.suggest_cell_sizes(fm, bbox_dim)
        assert s_min < 0.05
        assert s_max > s_min

    def test_suggest_cell_sizes_with_gaps(self):
        detector = FeatureDetector()
        fm = FeatureMap(
            gap_regions=[GapRegion((0,0,0), 0.001, (0,0,1))],
        )
        bbox_dim = 10.0
        s_min, s_max = detector.suggest_cell_sizes(fm, bbox_dim)
        assert s_min <= 0.001 * 0.2 + 1e-9

    def test_analyze_step_file_not_found(self):
        detector = FeatureDetector()
        with pytest.raises(FileNotFoundError):
            detector.analyze_step("/nonexistent/file.step")

    @pytest.mark.skipif(True, reason="Requires gmsh runtime mock setup")
    def test_analyze_step_with_mock(self):
        pass

    def test_shutdown_not_initialized(self):
        detector = FeatureDetector()
        detector.shutdown()

    def test_edge_with_zero_length(self):
        edge = SharpEdge((0,0,0), (0,0,0), 0, 0)
        assert edge.length == 0
        assert edge.angle == 0


class TestFeatureDetectorIntegration:
    def test_feature_map_string_repr(self):
        fm = FeatureMap(
            sharp_edges=[SharpEdge((0,0,0), (1,0,0), 90, 1.0)],
        )
        assert len(fm.sharp_edges) == 1
        assert fm.sharp_edges[0].angle == 90.0

    def test_multiple_edges_min_cell_size(self):
        detector = FeatureDetector()
        fm = FeatureMap(
            sharp_edges=[
                SharpEdge((0,0,0), (1,0,0), 90, 1.0),
                SharpEdge((0,0,0), (0.005,0,0), 45, 0.005),
            ],
        )
        s_min, s_max = detector.suggest_cell_sizes(fm, bbox_dim=10.0)
        assert s_min <= 0.005 * 0.3


class TestFeatureDetectWorker:
    """analyze_step() runs gmsh.model.mesh.generate(2) — a real 2D remesh
    — which used to run directly on MainWindow's GUI thread and could
    freeze the whole app for a long time on complex geometry (confirmed
    live: "Not Responding" for the entire duration). FeatureDetectWorker
    wraps it for use on a background QThread instead; these tests exercise
    its signal contract without needing a real STEP file or a live GUI."""

    def test_emits_error_for_missing_file(self, qtbot):
        worker = FeatureDetectWorker("/nonexistent/path/does_not_exist.step", "medium")
        results = []
        worker.finished.connect(lambda fm, err: results.append((fm, err)))
        worker.run()
        assert len(results) == 1
        feature_map, error = results[0]
        assert feature_map is None
        # The real reason must survive, not be flattened to "exit code 1".
        assert "not found" in error.lower() or "no usable output" in error.lower()

    def test_run_never_raises(self, qtbot):
        # run() must always emit via the signal, never propagate an
        # exception — it's meant to execute on a bare QThread with no
        # surrounding try/except at the call site.
        worker = FeatureDetectWorker("", "medium")
        worker.run()  # should not raise

    def test_native_child_crash_is_contained(self, qtbot, monkeypatch):
        """gmsh bundles its own OpenCASCADE while the app loads cadquery/
        OCP's — driving gmsh's OCC STEP reader in-process took the whole
        application down with a hard native crash (no traceback) on a real
        user geometry, on BOTH the serial and parallel meshing paths. That
        is why detection runs out-of-process: a segfault must surface as a
        handled error, never kill the app."""
        import subprocess as _subprocess

        # Capture the real run() BEFORE patching, otherwise the replacement
        # calls itself.
        _real_run = _subprocess.run

        def _crashing_run(cmd, **kwargs):
            return _real_run(
                [sys.executable, "-c", "import ctypes; ctypes.string_at(0)"],
                **kwargs
            )

        monkeypatch.setattr(
            "polyfoammesh.core.feature_detector.subprocess.run", _crashing_run,
            raising=False,
        )
        worker = FeatureDetectWorker("whatever.step", "medium")
        results = []
        worker.finished.connect(lambda fm, err: results.append((fm, err)))
        worker.run()  # must not crash the test process

        assert len(results) == 1
        feature_map, error = results[0]
        assert feature_map is None
        assert "crash" in error.lower()

    def test_timeout_is_reported_not_hung(self, qtbot, monkeypatch):
        import subprocess as _subprocess

        def _timeout_run(cmd, **kwargs):
            raise _subprocess.TimeoutExpired(cmd=cmd, timeout=1)

        monkeypatch.setattr(
            "polyfoammesh.core.feature_detector.subprocess.run", _timeout_run,
            raising=False,
        )
        worker = FeatureDetectWorker("whatever.step", "medium")
        results = []
        worker.finished.connect(lambda fm, err: results.append((fm, err)))
        worker.run()

        assert results[0][0] is None
        assert "exceeded" in results[0][1]
