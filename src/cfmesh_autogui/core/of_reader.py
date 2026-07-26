"""Transparent reader for OpenFOAM files — handles both plain ASCII
and gzip-compressed files (written when ``writeCompression on``).

All functions accept either a plain .txt file or a .gz file and return
the same parsed result, so callers don't need to know which format is
on disk.
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path


def read_of_text(path: Path | str, errors: str = "replace") -> str:
    """Read an OpenFOAM file, decompressing on-the-fly if gzipped.

    Args:
        path: Path to the OpenFOAM file (may be ``.gz`` or plain).
        errors: Error handling for decode (default ``replace``).

    Returns:
        The file contents as a single string.
    """
    path = Path(path)
    raw = _read_of_bytes(path)
    return raw.decode("ascii", errors=errors)


def _read_of_bytes(path: Path) -> bytes:
    """Read raw bytes, decompressing if the file is gzipped."""
    try:
        with gzip.open(path, "rb") as f:
            header = f.read(2)
            f.seek(0)
            if header == b"\x1f\x8b":
                return f.read()
    except (OSError, gzip.BadGzipFile):
        pass
    # Plain file or gzip-open failed → read normally
    return path.read_bytes()


def of_list_count(path: Path) -> int:
    """Return the declared entry count of a top-level OpenFOAM list file
    (``points``, ``faces``, ``owner``, …).

    Reads only the header to avoid loading the full data section — works
    with both plain and gzip-compressed files.

    Example input::

        FoamFile { … }
        N
        (
        …

    Returns ``N`` (the bare integer before the opening paren).  Returns 0
    if the file doesn't exist or cannot be parsed.
    """
    if not path.exists():
        return 0
    try:
        raw = _read_of_bytes(path)
    except Exception:
        return 0
    text = raw.decode("ascii", errors="replace")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    header_end = text.find("}")
    body = text[header_end + 1:] if header_end != -1 else text
    m = re.search(r"(\d+)\s*\n\s*\(", body)
    return int(m.group(1)) if m else 0


def of_label_list(path: Path) -> list[int]:
    """Return the integer values of a flat OpenFOAM ``labelList`` file
    (``owner``, ``neighbour``).

    Handles both plain and gzip-compressed files.
    """
    if not path.exists():
        return []
    try:
        raw = _read_of_bytes(path)
    except Exception:
        return []
    text = raw.decode("ascii", errors="replace")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    header_end = text.find("}")
    body = text[header_end + 1:] if header_end != -1 else text
    m = re.search(r"\d+\s*\n\s*\(", body)
    if not m:
        return []
    start = m.end()
    end = body.find(")", start)
    if end == -1:
        return []
    return [int(tok) for tok in body[start:end].split()]


def of_ncells_from_header(path: Path) -> int | None:
    """Try to read ``nCells: N`` from the FoamFile header comment.

    cfMesh's cartesianMesh writes ``nPoints:X nCells:Y nFaces:Z`` into
    the ``owner`` file's header.  Returns ``None`` when absent (fall
    back to ``of_label_list``-based counting).
    """
    if not path.exists():
        return None
    try:
        raw = _read_of_bytes(path)
    except Exception:
        return None
    text = raw.decode("ascii", errors="replace")
    m = re.search(r"nCells:\s*(\d+)", text)
    return int(m.group(1)) if m else None
