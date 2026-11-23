"""W16: documentation numbers are verified against their sources.

Every figure the docs cite must exist in a named in-repo artifact (JSON,
code constant, or session note) — no invented numbers. Fast tests only:
JSON/markdown parsing and imports, no meshing, no WSL, no clock.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _section(md: str, heading: str) -> str:
    """Markdown section body for a '## heading' (to end or next ##)."""
    m = re.search(rf"^## {re.escape(heading)}\s*$", md, re.M)
    assert m, f"heading {heading!r} not found"
    rest = md[m.end():]
    n = re.search(r"^## ", rest, re.M)
    return rest[: n.start()] if n else rest


def test_lane_a_minivalve_record_matches_json():
    """Lane A mini_valve PASS (8070 cells, NOmax 64.33) is the recorded JSON."""
    rows = json.loads(
        (ROOT / "benchmarks/results/comparison_20261006T170414.json").read_text(encoding="utf-8", errors="replace")
    )
    rec = next(
        r for r in rows if r["geometry"] == "mini_valve" and r["runner"] == "poly"
    )
    assert rec["thresholds_pass"] is True
    assert rec["cell_count"] == 8070
    assert rec["max_non_ortho"] == pytest.approx(64.33, abs=0.01)
    assert "BL failed" in " ".join(rec["warnings"])


def test_gap_analysis_dirty_shared_failure_is_recorded():
    """dirty_cad: all runners 0 cells, snappy FATAL — the shared failure."""
    gap = (ROOT / "benchmarks/results/GAP_ANALYSIS.md").read_text(encoding="utf-8", errors="replace")
    for n in ("1 | dirty_cad", "2 | dirty_cad", "3 | dirty_cad"):
        assert n in gap
    assert "FATAL" in gap and "is not inside the mesh" in gap
    lb = (ROOT / "benchmarks/results/LEADERBOARD.md").read_text(encoding="utf-8", errors="replace")
    dirty = _section(lb, "dirty_cad")
    assert "| cells | 0 | 0 | 0 | 0 | 0 |" in dirty
    assert "none (no runner passed thresholds)" in dirty


def test_leaderboard_minivalve_hex_matches_gap_analysis():
    """mini_valve hex 611958 cells / NOmax 72.58 FAIL; snappy timed out."""
    lb = (ROOT / "benchmarks/results/LEADERBOARD.md").read_text(encoding="utf-8", errors="replace")
    mini = _section(lb, "mini_valve")
    assert "| cells | 611958 | 0 | 0 | 0 | 0 |" in mini
    assert "| non-ortho max | 72.58 |" in mini
    assert "1204.07" in mini and "1200.91" in mini
    gap = (ROOT / "benchmarks/results/GAP_ANALYSIS.md").read_text(encoding="utf-8", errors="replace")
    assert "1204.07" in gap and "1200.91" in gap


def test_user_guide_presets_match_code_evidence():
    """USER_GUIDE preset numbers are the mesh_presets.py evidence strings."""
    from polyfoammesh.core.mesh_presets import BUILTIN_MESH_PRESETS

    guide = (ROOT / "docs" / "USER_GUIDE.md").read_text(encoding="utf-8", errors="replace")
    for name, figures in (
        ("Fast", ["256,767", "334"]),
        ("Balanced", ["253,681", "110"]),
        ("Fine", ["2.37M", "3738"]),
    ):
        ev = BUILTIN_MESH_PRESETS[name].evidence
        for fig in figures:
            assert fig in ev, f"{name}: {fig} missing from code evidence"
            assert fig in guide, f"{name}: {fig} missing from USER_GUIDE"


def test_user_guide_keeps_the_measured_figures():
    """The measured figures cited in the user guide (boundary layer on tets and
    the dirty-CAD case) must not be edited away by accident."""
    guide = (ROOT / "docs" / "USER_GUIDE.md").read_text(encoding="utf-8", errors="replace")
    for fig in ("0.999", "0.474", "90.5", "221.4", "448,864", "114,269",
                "3,515", "67.80", "64.68", "3.92", "3.93", "36,949", "36,514"):
        assert fig in guide, f"measured figure {fig} missing from USER_GUIDE"


def test_readme_describes_two_ctas():
    """README must present the two meshing paths (Polymesh auto + cfMesh)."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8", errors="replace")
    assert "**Polymesh**" in readme and "**cfMesh**" in readme
    assert "Cartesian cfMesh (via WSL2) or the native polyhedral mesher" not in readme


def test_report_v2_append_marker_and_new_appendices():
    """v2 keeps the append marker before the build; G+H are appended."""
    src = (ROOT / "tools" / "gen_report_pdf_v2.py").read_text(encoding="utf-8", errors="replace")
    marker = src.index("# === APPEND HERE ===")
    build = src.index("doc.multiBuild(story)")
    assert marker < build
    head = src[:marker]
    assert "Appendix G. fTetWild experimental path" in head
    assert "Appendix H. Engineering status update" in head
    tail = src[marker:]
    # nothing but the marker, comments, blank lines and the build block
    assert "H1(" not in tail and "story += [" not in tail


def test_report_v1_deprecated_and_unreferenced():
    """v1 refuses to run (it would clobber the v2 PDF); nothing uses it."""
    v1 = (ROOT / "tools" / "gen_report_pdf.py").read_text(encoding="utf-8", errors="replace")
    assert "POLYFOAMMESH_ALLOW_LEGACY_REPORT" in v1
    assert "deprecated" in v1.lower()
    hits = []
    here = Path(__file__).resolve()
    candidates = list((ROOT / "src").rglob("*.py")) + [
        p for p in (ROOT / "tests").rglob("*.py") if p.resolve() != here
    ] + list((ROOT / "benchmarks").rglob("*.py"))
    for py in candidates:
        text = py.read_text(encoding="utf-8", errors="replace")
        if re.search(r"gen_report_pdf(?!_v2)\b", text):
            hits.append(str(py.relative_to(ROOT)))
    assert hits == [], f"v1 still referenced by: {hits}"


def test_report_v1_guard_refuses_to_build(monkeypatch):
    """Executing v1 raises SystemExit instead of overwriting the v2 PDF."""
    import runpy

    monkeypatch.delenv("POLYFOAMMESH_ALLOW_LEGACY_REPORT", raising=False)
    with pytest.raises(SystemExit, match="deprecated"):
        runpy.run_path(str(ROOT / "tools" / "gen_report_pdf.py"), run_name="__main__")
