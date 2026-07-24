"""Regressions found while running the new headless full-auto CLI
(one_click_run.py's main_cli / FullAutoPipeline._step_report) end-to-end
against real WSL/OpenFOAM for the first time.

FullAutoResult.summary() embeds unicode emoji (checkmark/cross). Windows'
console and default file encoding is cp1252, which cannot encode them —
every real run crashed with UnicodeEncodeError, both when writing
full_auto_status.txt and when the CLI printed the summary to stdout.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _test_helpers import load_commercial_module


def test_step_report_writes_status_file_with_emoji_as_utf8(tmp_path):
    mod = load_commercial_module("one_click_run")

    case_dir = tmp_path / "case"
    case_dir.mkdir()

    pipeline = mod.FullAutoPipeline.__new__(mod.FullAutoPipeline)
    pipeline._result = mod.FullAutoResult(success=True, cell_count=100, n_patches=2)

    import types
    mesh_result = types.SimpleNamespace(cell_count=100, algorithm="CartesianHex")
    quality_report = types.SimpleNamespace(
        passed=True,
        metrics=types.SimpleNamespace(
            max_skewness=0.1, max_non_orthogonality=10.0,
            max_aspect_ratio=5.0, neg_cells=0,
        ),
    )

    paths = pipeline._step_report(case_dir, mesh_result, quality_report)

    status_text = (case_dir / "full_auto_status.txt").read_text(encoding="utf-8")
    assert "✅" in status_text  # checkmark survived the round trip
    assert paths
