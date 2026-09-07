"""Regression test for the "refines at random" adaptive-sizing bug.

Root cause (see gmsh_wrapper._configure_adaptive_sizing): GMSH's own
blanket curvature-driven sizing (Mesh.MeshSizeFromCurvature /
CharacteristicLengthFromCurvature) was left ON alongside the app's own
purpose-built, bounded sizing fields (small-curve Distance/Threshold, gap
Ball fields, and the a-priori curvature callback field). GMSH's own manual
says to turn the blanket options OFF when sizing is fully field-driven —
left on, they chase the local curvature radius on EVERY curved surface,
uncorrelated with the detail level's own min-feature floor: one small
feature anywhere on the part dragged the mesh-wide CharacteristicLengthMin
down, and this then applied that same fine size everywhere.

This test uses a minimal fake `gmsh` module (no real geometry — the two
detector helpers this function calls degrade to a no-op on an empty model)
so it runs without GMSH installed or a CAD kernel.
"""
from __future__ import annotations

from polyfoammesh.core.gmsh_wrapper import (
    _configure_adaptive_sizing,
    _scan_feature_sizes,
)


class _FakeField:
    def __init__(self):
        self.next_tag = 1
        self.numbers: dict[int, dict[str, float]] = {}
        self.number_lists: dict[int, dict[str, list]] = {}
        self.kinds: dict[int, str] = {}
        self.background: int | None = None

    def add(self, kind: str) -> int:
        tag = self.next_tag
        self.next_tag += 1
        self.numbers[tag] = {}
        self.number_lists[tag] = {}
        self.kinds[tag] = kind
        return tag

    def setNumber(self, tag, name, value):
        self.numbers[tag][name] = value

    def setNumbers(self, tag, name, values):
        self.number_lists[tag][name] = list(values)

    def setAsBackgroundMesh(self, tag):
        self.background = tag


class _FakeMesh:
    def __init__(self):
        self.field = _FakeField()


class _FakeModel:
    def __init__(self):
        self.mesh = _FakeMesh()

    def getEntities(self, dim):
        return []  # empty model: gap/curvature detectors both no-op

    def getBoundingBox(self, dim, tag):
        return (0, 0, 0, 0, 0, 0)


class _FakeOption:
    def __init__(self):
        self.values: dict[str, float] = {}

    def setNumber(self, name, value):
        self.values[name] = value


class _FakeGmsh:
    def __init__(self):
        self.model = _FakeModel()
        self.option = _FakeOption()


def _run(use_curvature_size_field: bool = True, feat: dict | None = None,
         max_extent: float = 1.0) -> _FakeGmsh:
    g = _FakeGmsh()
    if feat is None:
        feat = {"min_feature": 0.01, "small_curve_tags": [], "small_cutoff": 0.01}
    hw = {"max_cells": 1_000_000, "cpu_count": 4, "is_explicit_target": False}
    _configure_adaptive_sizing(
        g, "medium", max_extent=max_extent, feat=feat, hw=hw,
        cross_scale=max_extent, domain_volume=max_extent ** 3,
        use_curvature_size_field=use_curvature_size_field,
    )
    return g


def test_blanket_curvature_engine_is_disabled():
    """The fields this function installs are the ONLY source of sizing;
    GMSH's own unbounded curvature/points/boundary-extend options must be
    off, or they silently override the bounded fields with an unrelated,
    uncorrelated size — this is the exact bug being fixed."""
    g = _run()
    assert g.option.values["Mesh.MeshSizeFromCurvature"] == 0
    assert g.option.values["Mesh.CharacteristicLengthFromCurvature"] == 0
    assert g.option.values["Mesh.MeshSizeFromPoints"] == 0
    assert g.option.values["Mesh.MeshSizeExtendFromBoundary"] == 0


def test_min_max_bounds_still_set():
    """Disabling the blanket engine must not remove the actual size
    bounds — those still come from CharacteristicLengthMin/Max."""
    g = _run()
    assert g.option.values["Mesh.CharacteristicLengthMin"] > 0
    assert (
        g.option.values["Mesh.CharacteristicLengthMax"]
        >= g.option.values["Mesh.CharacteristicLengthMin"]
    )


def test_disabling_curvature_field_does_not_reenable_blanket_engine():
    """Even with the a-priori curvature field turned off entirely (no
    bounded source of curvature-adaptive sizing at all), the blanket
    engine must stay off — the two are independent switches."""
    g = _run(use_curvature_size_field=False)
    assert g.option.values["Mesh.MeshSizeFromCurvature"] == 0
    assert g.option.values["Mesh.CharacteristicLengthFromCurvature"] == 0


class _FakeOcc:
    def __init__(self, lengths: dict[int, float]):
        self._lengths = lengths

    def getMass(self, dim, tag):
        return self._lengths[tag]


class _FeatureScanGmsh:
    """Minimal fake for _scan_feature_sizes: curves of given lengths."""

    def __init__(self, lengths: list[float]):
        self._lengths = {i + 1: length for i, length in enumerate(lengths)}
        self.model = type("M", (), {})()
        self.model.occ = _FakeOcc(self._lengths)
        self.model.getEntities = lambda dim: (
            [(1, tag) for tag in self._lengths] if dim == 1 else []
        )


def test_uniform_geometry_flags_no_small_curves():
    """A plain pipe/duct — every curve within a factor of 2 of the
    others, nothing genuinely small relative to the part — must NOT have
    its bottom quartile flagged as a 'small feature' needing local
    refinement. This was the second source of 'refines at random on
    simple geometries': the old quartile-only cutoff always flagged
    *something*, purely because a bottom 25% always exists."""
    max_extent = 1.0
    lengths = [0.9, 0.95, 1.0, 1.05, 1.1, 0.85, 1.15, 0.8]  # all >= 80% of max_extent
    g = _FeatureScanGmsh(lengths)
    feat = _scan_feature_sizes(g, max_extent)
    assert feat["small_curve_tags"] == []


def test_dist_max_never_exceeds_a_local_neighbourhood_of_the_part():
    """Regression: DistMax (the radius over which the small-curve field
    ramps back up to coarse_max) used to be small_cutoff * 20 with no
    absolute cap. small_cutoff can be as large as max_extent * 0.08 (see
    _scan_feature_sizes), so DistMax could reach 1.6x the whole part's
    extent — the mesh then never actually ramps back to coarse ANYWHERE,
    producing a uniformly fine mesh with no visible grading even though
    the total cell count matches the budget. DistMax must stay within a
    genuinely local neighbourhood of the part."""
    max_extent = 2.0
    feat = {
        "min_feature": 0.001,
        "small_cutoff": max_extent * 0.08,  # the worst case: at the ceiling
        "small_curve_tags": [1],
    }
    g = _run(feat=feat, max_extent=max_extent)
    thresh_tag = next(t for t, k in g.model.mesh.field.kinds.items() if k == "Threshold")
    dist_max = g.model.mesh.field.numbers[thresh_tag]["DistMax"]
    dist_min = g.model.mesh.field.numbers[thresh_tag]["DistMin"]
    assert dist_max <= max_extent * 0.15 + 1e-12
    assert dist_min < dist_max


def test_genuine_small_feature_is_still_flagged():
    """The reference case this detector exists for (a 3m valve body with
    ~0.25mm fillets) must still work: a curve genuinely tiny relative to
    the part is flagged regardless of the new absolute ceiling."""
    max_extent = 3.0
    lengths = [3.0, 2.8, 3.1, 2.9, 0.00025, 0.0003]  # two ~0.25-0.3mm fillets
    g = _FeatureScanGmsh(lengths)
    feat = _scan_feature_sizes(g, max_extent)
    flagged_lengths = sorted(
        length for tag, length in
        [(t, g.model.occ.getMass(1, t)) for t in feat["small_curve_tags"]]
    )
    assert flagged_lengths == [0.00025, 0.0003]
