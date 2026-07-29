"""Transparent reader for OpenFOAM files — handles both plain ASCII
and gzip-compressed files (written when ``writeCompression on``).

All functions accept either a plain .txt file or a .gz file and return
the same parsed result, so callers don't need to know which format is
on disk.
"""

from __future__ import annotations

import gzip
import re
import struct
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


_HEADER_READ_SIZE = 16_384  # 16 KB is enough for any OpenFOAM header block


def _read_header_bytes(path: Path, max_bytes: int = _HEADER_READ_SIZE) -> bytes:
    """Read only the header portion of an OpenFOAM file.

    Handles both plain and gzip-compressed files.  For gzip files the
    entire stream must be decompressed to find the header, but for plain
    files only the first ``max_bytes`` are read — avoids loading a
    100+ MB faces file just to extract ``N`` from the header.
    """
    # Try gzip first: open and decompress up to max_bytes
    gz_data = None
    try:
        with gzip.open(path, "rb") as f:
            gz_data = f.read(max_bytes)
    except (OSError, gzip.BadGzipFile):
        pass
    if gz_data and gz_data[:2] == b"\x1f\x8b":
        # File was gzip but we only got compressed bytes from gzip.open
        # (can happen when gzip.open fails to decompress)
        pass
    elif gz_data:
        return gz_data
    # Plain file: read only first max_bytes
    try:
        with open(path, "rb") as f:
            return f.read(max_bytes)
    except OSError:
        return b""


def _read_of_bytes(path: Path) -> bytes:
    """Read full file content, decompressing if the file is gzipped.

    WARNING: For large binary files (100 MB+ faces) prefer
    ``_read_header_bytes()`` instead.
    """
    try:
        with gzip.open(path, "rb") as f:
            header = f.read(2)
            f.seek(0)
            if header == b"\x1f\x8b":
                return f.read()
    except (OSError, gzip.BadGzipFile):
        pass
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
    raw = _read_header_bytes(path)
    if not raw:
        return 0
    text = raw.decode("ascii", errors="replace")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    header_end = text.find("}")
    body = text[header_end + 1:] if header_end != -1 else text
    m = re.search(r"(\d+)\s*\n\s*\(", body)
    return int(m.group(1)) if m else 0


def _is_binary_format(path: Path) -> bool:
    """Quick check if the OpenFOAM file is in binary format."""
    try:
        header_bytes = path.read_bytes()[:1024]
    except Exception:
        return False
    try:
        text = header_bytes.decode("ascii", errors="replace")
    except Exception:
        return False
    return bool(re.search(r'format\s+ binary\s*;', text))


def of_label_list(path: Path) -> list[int]:
    """Return the integer values of a flat OpenFOAM ``labelList`` file
    (``owner``, ``neighbour``).

    Handles ASCII, binary, and gzip-compressed files of both formats.
    """
    if not path.exists():
        return []
    try:
        raw = _read_of_bytes(path)
    except Exception:
        return []
    is_binary = _is_binary_format(path)
    if is_binary:
        # Binary format: header is ASCII until '}', then N ints as 4-byte LE
        try:
            text_part = raw.decode("ascii", errors="replace")
        except Exception:
            return []
        text_part = re.sub(r"/\*.*?\*/", "", text_part, flags=re.DOTALL)
        text_part = re.sub(r"//[^\n]*", "", text_part)
        header_end = text_part.find("}")
        binary_body = raw[header_end + 1:] if header_end != -1 else raw
        m = _re_search_bytes(rb"(\d+)\s*\(", binary_body)
        if not m:
            return []
        count = int(m.group(1))
        start = m.end()
        # Skip '(' byte, read count * 4 bytes as int32
        data_start = start
        if data_start < len(binary_body) and binary_body[data_start:data_start + 1] == b'(':
            data_start += 1
        data = binary_body[data_start:data_start + count * 4]
        return list(struct.unpack(f"<{count}i", data[:count * 4]))
    # ASCII format — handle gracefully if binary data leaks through
    try:
        text = raw.decode("ascii", errors="replace")
    except Exception:
        return []
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    header_end = text.find("}")
    body = text[header_end + 1:] if header_end != -1 else text
    m = _re_search_str(r"(\d+)\s*\n\s*\(", body)
    if not m:
        return []
    start = m.end()
    end = body.find(")", start)
    if end == -1:
        return []
    try:
        return [int(tok) for tok in body[start:end].split()]
    except ValueError:
        return []


def _re_search_bytes(pattern: bytes, data: bytes):
    return re.search(pattern, data)


def _re_search_str(pattern: str, data: str):
    return re.search(pattern, data)


def of_ncells_from_header(path: Path) -> int | None:
    """Try to read ``nCells: N`` from the FoamFile header comment.

    cfMesh's cartesianMesh writes ``nPoints:X nCells:Y nFaces:Z`` into
    the ``owner`` file's header — also handles ``nCells = N`` and
    ``nCells=N`` variants from different OpenFOAM versions.
    Returns ``None`` when absent (fall back to ``of_label_list``-based
    counting).
    """
    if not path.exists():
        return None
    raw = _read_header_bytes(path)
    if not raw:
        return None
    text = raw.decode("ascii", errors="replace")
    m = re.search(r"nCells\s*[:=]\s*(\d+)", text)
    return int(m.group(1)) if m else None
