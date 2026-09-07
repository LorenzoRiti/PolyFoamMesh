r"""_read_of_block_binary() must locate the data body at the byte offset it
actually sits at in the raw file, not at an offset computed on a
comment-stripped (and therefore shorter) copy of the text.

Bug: header_end was found via `text_part[:512].rfind("}")`, where text_part
had already had every /* ... */ and // comment DELETED (not blanked). That
shortens the string, so any position found in it no longer matches the same
byte in `raw`, which still has the comments in place. It only "worked" by
luck: the trailing regex search `re.search(rb"(\d+)\s*\(", body)` skips
forward past whatever the wrong body prefix contains, and a standard
OpenFOAM header banner happens to contain no digit-run immediately followed
by "(" before the real data section — so the search always landed on the
real count by accident.

A `note` entry containing a digit-paren pattern (legal FoamFile syntax,
e.g. an annotation like "nPatches:16(cyl)") breaks that luck: the search
matches that fragment instead of the real count, and the parser silently
returns garbage doubles instead of raising — exactly the failure mode this
whole codebase has spent this session hardening against.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from polyfoammesh.gui.viewer_widget import _read_of_block_binary  # noqa: E402

_BANNER = (
    "/*--------------------------------*- C++ -*----------------------------------*\\\n"
    "| =========                 |                                                 |\n"
    "\\*---------------------------------------------------------------------------*/\n"
)
_TRAILER = (
    "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n"
    "\n\n\n"
)


def _make_binary_points(extra_header_field: str = "") -> tuple[bytes, list[tuple[float, float, float]]]:
    header = (
        _BANNER
        + "FoamFile\n{\n    format      binary;\n    class       vectorField;\n"
        + extra_header_field
        + "}\n"
        + _TRAILER
    )
    pts = [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0), (-7.5, 8.25, 0.0)]
    body = f"{len(pts)}\n(".encode("ascii")
    body += b"".join(struct.pack("<3d", *p) for p in pts)
    body += b")\n"
    return header.encode("ascii") + body, pts


def _flat(pts: list[tuple[float, float, float]]) -> list[float]:
    return [c for p in pts for c in p]


def test_plain_header_parses_correctly():
    raw, pts = _make_binary_points()
    out = _read_of_block_binary(Path("fake_points"), raw)
    assert [float(x) for x in out.split()] == _flat(pts)


def test_digit_paren_in_header_note_does_not_corrupt_the_body():
    """The case that silently returned garbage before the fix."""
    raw, pts = _make_binary_points('    note        "nPatches:16(cyl)";\n')
    out = _read_of_block_binary(Path("fake_points"), raw)
    assert [float(x) for x in out.split()] == _flat(pts)


def test_multiple_digit_paren_fragments_in_header():
    raw, pts = _make_binary_points(
        '    note        "built 2026(rev3) from 4(patches)";\n'
    )
    out = _read_of_block_binary(Path("fake_points"), raw)
    assert [float(x) for x in out.split()] == _flat(pts)
