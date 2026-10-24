"""W18 log-tag contract: tet-stage logs are engine-neutral.

The tet stage is shared by GMSH and fTetWild (auto picks per geometry),
so its log lines carry the family tag with the engine as attribute
([tet:gmsh], [tet:ftetwild], [tet]) instead of crediting one engine.
Convert has its own stage tag ([convert]). Raw engine names must not
appear as bare log prefixes anymore.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "polyfoammesh"

TAGGED_FILES = [
    SRC / "core" / "openfoam_runner.py",
    SRC / "core" / "gmsh_wrapper.py",
    SRC / "core" / "ftetwild_wrapper.py",
    SRC / "core" / "gmsh_subprocess.py",
    SRC / "gui" / "main_window.py",
]


def test_no_bare_engine_tags_in_log_strings():
    """No `"[gmsh]"` / `"[fTetWild]"` string literals left in the pipeline."""
    bad: list[str] = []
    for path in TAGGED_FILES:
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'''["'][ ]*\[gmsh\]|\["fTetWild"\]|\['fTetWild'\]''', text):
            line = text.count("\n", 0, m.start()) + 1
            bad.append(f"{path.name}:{line}")
    # also catch double-quoted [fTetWild]
    for path in TAGGED_FILES:
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'"\[fTetWild\]', text):
            line = text.count("\n", 0, m.start()) + 1
            bad.append(f"{path.name}:{line}")
    assert bad == [], f"bare engine log tags: {bad}"


def test_family_tags_present_where_logs_are_emitted():
    """The neutral family tags actually exist in the worker modules."""
    workers = (SRC / "core" / "openfoam_runner.py").read_text(encoding="utf-8")
    assert "[tet:gmsh]" in workers
    assert "[tet:ftetwild]" in workers
    main = (SRC / "gui" / "main_window.py").read_text(encoding="utf-8")
    assert "[tet]" in main
    assert "[convert]" in main


def test_coalescer_still_matches_neutral_heartbeat():
    """The log-panel coalescer regex is engine-agnostic (both tags collapse)."""
    import re as _re

    from polyfoammesh.gui.log_panel import _STILL_RUNNING_RE

    assert _STILL_RUNNING_RE.search("[tet:gmsh] ... still running (12s elapsed)")
    assert _STILL_RUNNING_RE.search("[tet:ftetwild] ... still running (3s elapsed)")
