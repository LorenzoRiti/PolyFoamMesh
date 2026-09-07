"""Regression tests for checkMesh output parsing.

The parser was written against an older/idealised checkMesh format and silently
failed on what OpenFOAM v2512 actually prints: non-orthogonality uses a colon
("Mesh non-orthogonality Max: ... average: ..."), skewness and aspect ratio come
with no average at all, and "Min volume = 6.3e-06." ends in a sentence period.
The result was a Quality panel that reported 0/perfect for every metric — or
crashed with ValueError on the min-volume line. These tests pin the real format.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core.openfoam_runner import parse_checkmesh_output

# Verbatim excerpt of real `checkMesh` output, OpenFOAM v2512, cfMesh cylinder.
REAL_V2512 = """\
Mesh stats
    points:           8903
    faces:            24287
    internal faces:   22041
    cells:            7694

Checking geometry...
    Max aspect ratio = 39.5564 OK.
    Minimum face area = 1.2e-05. Maximum face area = 0.06.  Face area magnitudes OK.
    Min volume = 6.30328e-06. Max volume = 0.0162677.  Total volume = 6.2685.  Cell volumes OK.
    Mesh non-orthogonality Max: 61.5453 average: 15.0436
    Non-orthogonality check OK.
    Max skewness = 0.657556 OK.

Mesh OK.
"""

# The older phrasing the parser was originally written for; still accepted.
LEGACY = """\
    cells:            5000
    Max non-orthogonality = 85.0 average = 18.3
    Max skewness = 8.5 average = 1.5
    Max aspect ratio = 2000
    There are 3 cells with negative volume
    Min volume = -5.3e-10
"""


def test_parses_real_v2512_metrics():
    r = parse_checkmesh_output(REAL_V2512)
    assert r.cells == 7694
    assert r.faces == 24287
    assert r.points == 8903
    # These four were all silently 0 before: the regexes never matched.
    assert r.max_non_ortho == 61.5453
    assert r.avg_non_ortho == 15.0436
    assert r.max_skewness == 0.657556
    assert r.max_aspect_ratio == 39.5564
    assert r.neg_cells == 0
    assert not r.has_fatal


def test_min_volume_does_not_swallow_trailing_period():
    """'Min volume = 6.30328e-06.' used to parse as '6.30328e-06.' -> ValueError."""
    r = parse_checkmesh_output(REAL_V2512)
    assert r.min_volume == 6.30328e-06


def test_missing_average_does_not_borrow_a_later_number():
    """Skewness/aspect have no average in v2512; the value must stay 0, not pick
    up an unrelated number from a following line."""
    r = parse_checkmesh_output(REAL_V2512)
    assert r.avg_skewness == 0.0


def test_legacy_format_still_parses():
    r = parse_checkmesh_output(LEGACY)
    assert r.cells == 5000
    assert r.max_non_ortho == 85.0
    assert r.avg_non_ortho == 18.3
    assert r.max_skewness == 8.5
    assert r.avg_skewness == 1.5
    assert r.max_aspect_ratio == 2000.0
    assert r.neg_cells == 3
    assert r.min_volume == -5.3e-10


def test_negative_volume_marks_report_failed():
    r = parse_checkmesh_output(LEGACY)
    assert not r.passed


def test_clean_mesh_passes():
    r = parse_checkmesh_output(REAL_V2512)
    assert r.passed
