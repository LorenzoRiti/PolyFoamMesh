"""Shared OpenFOAM polyMesh I/O (ASCII and binary).

Single home for reading and writing OpenFOAM mesh files, used by every
tet->poly converter.  The readers handle both formats gmshToFoam writes:

- points / owner / neighbour: ASCII ``vectorField`` / ``labelList`` and binary
  (with the binary-header parsing fix for spurious ``}\\n`` matches inside
  large binary payloads)
- faces: ASCII and binary, ``faceList`` (legacy) AND ``faceCompactList``
  (default since ~OpenFOAM v1712, used by 2512)

All this logic was battle-tested over a full session on meshes up to ~2M rows
(see terminal_face.py, from which the readers are extracted unchanged, and the
bulk writers from tet_poly_dual.py).
"""

from __future__ import annotations

import logging
import re
import struct
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Binary header parsing
# ---------------------------------------------------------------------------

def binary_header_parse(raw: bytes) -> tuple[int, int]:
    """Parse a binary OpenFOAM header: return (data_start_byte, item_count).

    Binary format: ...header... }\\n [comments] N\\n(\\n <data>.
    We take the FIRST '}\\n' in the first 4000 bytes as the header's real
    closing brace: the header itself is always well-formed pure-ASCII
    dictionary text and cannot contain a stray '}\\n' before its own real
    closing brace, whereas the binary DATA right after it can coincidentally
    contain the same 2-byte sequence in large files (confirmed live on a
    ~2.1M-row 'neighbour' file).  Taking the LAST match instead landed
    header_end deep inside the binary payload and broke parsing.
    """
    all_closes = list(re.finditer(rb'\}\n', raw[:4000]))
    if not all_closes:
        raise ValueError("Cannot find '}' in binary OpenFOAM header")
    header_end = all_closes[0].end()
    m = re.search(rb'(\d+)\n\(', raw[header_end:])
    if not m:
        raise ValueError("Cannot find count N and '(' after header end")
    n_items = int(m.group(1))
    data_start = header_end + m.end()
    return data_start, n_items


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def read_points(path: Path) -> np.ndarray:
    """Read an OpenFOAM points file -> (N, 3) float64 array (ASCII or binary)."""
    raw = path.read_bytes()
    is_binary = bool(re.search(rb'format\s+binary\s*;', raw[:2000]))
    if is_binary:
        data_start, n_items = binary_header_parse(raw)
        expected = n_items * 3 * 8
        data = raw[data_start:data_start + expected]
        if len(data) < expected:
            raise ValueError(f"Binary points: need {expected} bytes, got {len(data)}")
        return np.frombuffer(data, dtype=np.float64).reshape(n_items, 3).copy()
    text = raw.decode("ascii", errors="replace")
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("("):
            start = i + 1
            break
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].strip() == ")":
            end = i
            break
    data_lines = [l.strip() for l in lines[start:end] if l.strip()]
    result = np.zeros((len(data_lines), 3), dtype=np.float64)
    for i, line in enumerate(data_lines):
        if i % 65536 == 0:
            time.sleep(0)  # release the GIL — 12M-point meshes take minutes
        parts = line.replace("(", "").replace(")", "").split()
        result[i] = [float(parts[0]), float(parts[1]), float(parts[2])]
    return result


def read_faces(path: Path) -> list[list[int]]:
    """Read an OpenFOAM faces file -> list of vertex-index lists.

    Handles the legacy ``faceList`` layout and the ``faceCompactList`` layout
    (offsets array + flat labels array).
    """
    raw = path.read_bytes()
    is_binary = bool(re.search(rb'format\s+binary\s*;', raw[:2000]))
    is_compact = bool(re.search(rb'class\s+faceCompactList\s*;', raw[:2000]))
    if is_compact:
        return _read_face_compact_list(raw, is_binary)
    if is_binary:
        data_start, n_faces = binary_header_parse(raw)
        result: list[list[int]] = []
        pos = data_start
        for _ in range(n_faces):
            if pos + 4 > len(raw):
                break
            n_verts = struct.unpack_from("<i", raw, pos)[0]
            if n_verts <= 0 or n_verts > 1000:
                break
            pos += 4
            if pos + n_verts * 4 > len(raw):
                break
            verts = list(struct.unpack_from(f"<{n_verts}i", raw, pos))
            pos += n_verts * 4
            result.append(verts)
        return result
    text = raw.decode("ascii", errors="replace")
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("("):
            start = i + 1
            break
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].strip() == ")":
            end = i
            break
    result = []
    for i, line in enumerate(lines[start:end]):
        if i % 262144 == 0:
            time.sleep(0)  # release the GIL during multi-million-face reads
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        inner = stripped
        if "(" in inner:
            inner = inner[inner.index("(") + 1:]
        if ")" in inner:
            inner = inner[:inner.rindex(")")]
        parts = inner.split()
        if parts:
            result.append([int(p) for p in parts])
    return result


def _read_face_compact_list(raw: bytes, is_binary: bool) -> list[list[int]]:
    """Read the faceCompactList layout: offsets array + flat labels array."""
    if is_binary:
        data_start, n_offsets = binary_header_parse(raw)
        offsets_bytes = n_offsets * 4
        offsets_end = data_start + offsets_bytes
        if offsets_end > len(raw):
            raise ValueError(
                f"Binary face offsets: need {offsets_bytes} bytes, "
                f"got {len(raw) - data_start}"
            )
        offsets = np.frombuffer(raw[data_start:offsets_end], dtype=np.int32)
        m = re.search(rb'\)\s*\n(\d+)\s*\n\(', raw[offsets_end:offsets_end + 128])
        if not m:
            raise ValueError(
                "Cannot find flat vertex-label list after face offsets "
                "in faceCompactList"
            )
        n_labels = int(m.group(1))
        labels_start = offsets_end + m.end()
        labels_bytes = n_labels * 4
        labels_end = labels_start + labels_bytes
        if labels_end > len(raw):
            raise ValueError(
                f"Binary face labels: need {labels_bytes} bytes, "
                f"got {len(raw) - labels_start}"
            )
        labels = np.frombuffer(raw[labels_start:labels_end], dtype=np.int32)
    else:
        text = raw.decode("ascii", errors="replace")
        offsets_list, next_pos = _parse_ascii_int_list(text, 0)
        labels_list, _ = _parse_ascii_int_list(text, next_pos)
        offsets = np.array(offsets_list, dtype=np.int64)
        labels = np.array(labels_list, dtype=np.int64)
    n_faces = len(offsets) - 1
    return [labels[offsets[i]:offsets[i + 1]].tolist() for i in range(n_faces)]


def _parse_ascii_int_list(text: str, pos: int) -> tuple[list[int], int]:
    """Parse one ``N\\n(\\n ...values... \\n)`` OpenFOAM ASCII list from `pos`.

    Returns (values, end_pos) where end_pos is the char offset just past the
    closing ')', so callers can chain a second parse for formats (like
    faceCompactList) that write two lists back to back.
    """
    m = re.search(r'(\d+)\s*\n\s*\(', text[pos:])
    if not m:
        raise ValueError("Cannot find count and '(' for ASCII int list")
    count = int(m.group(1))
    data_start = pos + m.end()
    close = text.index(")", data_start)
    body = text[data_start:close]
    # map(int, ...) iterates in C and releases the GIL between tokens —
    # much friendlier than a comprehension for multi-million-entry lists
    values = list(map(int, body.split()))
    if len(values) != count:
        raise ValueError(
            f"ASCII int list: expected {count} values, parsed {len(values)}"
        )
    return values, close + 1


def read_label_list(path: Path) -> np.ndarray:
    """Read an OpenFOAM labelList file -> 1D int32 array (ASCII or binary)."""
    raw = path.read_bytes()
    is_binary = bool(re.search(rb'format\s+binary\s*;', raw[:2000]))
    if is_binary:
        try:
            data_start, count = binary_header_parse(raw)
        except ValueError:
            # A SHORT list collapsed onto one line, e.g. "8(52927 ... 50056)"
            # (seen on checkMesh's own faceSets) has no "N\n(" to match at
            # all — the count and '(' are adjacent with no newline between
            # them. Skip straight to the ASCII/compact-form parser below
            # instead of failing outright; a genuinely truncated/corrupt
            # file still fails there with a clear "no data found" result.
            data_start = count = None
        if data_start is not None:
            # OpenFOAM writes SHORT lists in ASCII even inside a "format
            # binary" IOstream — confirmed on checkMesh's own diagnostic
            # sets (e.g. a 49-entry cellSet like "nonClosedCells"): the
            # header says binary, but the body is one-int-per-line text.
            # Trusting the header unconditionally decoded that ASCII text
            # as raw int32 bytes and returned garbage (values in the
            # hundreds of millions for a 22K-cell mesh). Probe the first
            # bytes after '(': real binary int32 data for any realistic
            # mesh index almost certainly contains a byte outside the
            # printable digit/whitespace/minus range within a handful of
            # int32s, so this reliably tells short-ASCII-despite-binary-
            # header apart from genuinely binary payloads (owner/
            # neighbour on a real mesh) without needing a size threshold.
            probe_len = min(64, count * 4, len(raw) - data_start)
            probe = raw[data_start:data_start + probe_len] if probe_len > 0 else b""
            # A ')' inside the probe is decisive: the binary payload of a
            # count-N list occupies exactly count*4 bytes, and probe_len is
            # capped at count*4 — so a ')' can only appear there in the
            # ASCII form ("... 4\n)\n"), never in genuine binary data. This
            # matters for very short lists (3-4 entries) where the 64-byte
            # probe would otherwise swallow the terminator. (On Windows this
            # bug was masked because CRLF line endings made "N\n(" fail to
            # match at all; on LF-only files short sets crashed with
            # "Binary labels: need 12 bytes, got 9".)
            if b")" in probe:
                looks_ascii = True
            else:
                looks_ascii = bool(re.match(rb'^[\s\-\d]*$', probe))
            if not (looks_ascii and probe):
                data = raw[data_start:data_start + count * 4]
                if len(data) < count * 4:
                    raise ValueError(f"Binary labels: need {count * 4} bytes, got {len(data)}")
                return np.frombuffer(data, dtype=np.int32).copy()
            # Falls through to the ASCII body parser below, which finds the
            # same '(' line and reads one int per line from there.
    text = raw.decode("ascii", errors="replace")

    # OpenFOAM also collapses a SHORT list onto one line, e.g.
    # "8(52927 52928 ... 50056)" (seen on checkMesh's own faceSets) instead
    # of "8\n(\n52927\n...\n)" — the line-per-entry parser below finds no
    # standalone "(" line for this form at all and would silently return
    # an empty array. Try the compact form first; fall through to the
    # per-line parser when it isn't present.
    m = re.search(r"^\s*\d+\s*\(([^()]*)\)\s*$", text, re.MULTILINE)
    if m:
        nums = re.findall(r"-?\d+", m.group(1))
        if nums:
            return np.array([int(n) for n in nums], dtype=np.int32)

    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("("):
            start = i + 1
            break
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].strip() == ")":
            end = i
            break
    data = []
    data = []
    for i, line in enumerate(lines[start:end]):
        if i % 262144 == 0:
            time.sleep(0)  # release the GIL during multi-million-entry reads
        stripped = line.strip()
        if stripped and not stripped.startswith("//"):
            try:
                data.append(int(stripped))
            except ValueError:
                # non-numeric line in an int list: skip it (robust parse of
                # hand-edited/legacy OpenFOAM files), never abort the read
                logger.debug("_read_int_list: skipping non-int line %r", stripped)
    return np.array(data, dtype=np.int32)


def read_boundary(path: Path) -> list[dict]:
    """Read an OpenFOAM boundary file -> list of patch dicts.

    Real format: <N>\\n(\\n <name>\\n {\\n type ...;\\n nFaces ...;\\n
    startFace ...;\\n }\\n ... )\\n — the patch name and its opening '{' are on
    separate lines and there is no '(' near an individual patch entry.
    """
    text = path.read_text(encoding="ascii", errors="replace")
    lines = [l.strip() for l in text.splitlines()]
    n = len(lines)
    i = 0
    while i < n and lines[i] != "}":
        i += 1
    i += 1
    while i < n and (lines[i] == "" or lines[i].startswith("//")):
        i += 1
    i += 1
    while i < n and lines[i] != "(":
        i += 1
    i += 1
    patches: list[dict] = []
    while i < n:
        line = lines[i]
        if line == ")":
            break
        if not line:
            i += 1
            continue
        name = line.strip('"')
        i += 1
        while i < n and lines[i] != "{":
            i += 1
        i += 1
        patch_info: dict[str, str | int] = {"name": name}
        while i < n and lines[i] != "}":
            pline = lines[i]
            if pline.startswith("nFaces"):
                patch_info["nFaces"] = int(pline.split()[1].rstrip(";"))
            elif pline.startswith("startFace"):
                patch_info["startFace"] = int(pline.split()[1].rstrip(";"))
            elif pline.startswith("type"):
                patch_info["type"] = pline.split()[1].rstrip(";").strip('"')
            i += 1
        i += 1
        if "nFaces" in patch_info and "startFace" in patch_info:
            patches.append(patch_info)
    return patches


def read_polymesh(poly_dir: Path) -> tuple:
    """Read a whole polyMesh directory.

    Returns (points (N,3), faces list, owner int32, neighbour int32,
    boundary patches list).  boundary faces are simply those with no
    neighbour entry (OpenFOAM's ``neighbour`` file is shorter than ``owner``);
    the returned neighbour array has length == number of INTERNAL faces.
    """
    points = read_points(poly_dir / "points")
    faces = read_faces(poly_dir / "faces")
    owner = read_label_list(poly_dir / "owner")
    neighbour = read_label_list(poly_dir / "neighbour")
    patches = read_boundary(poly_dir / "boundary")
    return points, faces, owner, neighbour, patches


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def _header(cls: str, obj: str) -> str:
    return (
        "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
        f"    class       {cls};\n    location    \"constant/polyMesh\";\n"
        f"    object      {obj};\n}}\n"
    )


def write_points(path: Path, points: np.ndarray) -> None:
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write(_header("vectorField", "points"))
        f.write(f"{len(points)}\n(\n")
        for i in range(0, len(points), 65536):
            time.sleep(0)  # release the GIL between write chunks
            f.write("".join(
                f"({x:.12e} {y:.12e} {z:.12e})\n" for x, y, z in points[i:i + 65536]
            ))
        f.write(")\n")


def write_faces(path: Path, faces: list[list[int]]) -> None:
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write(_header("faceList", "faces"))
        f.write(f"{len(faces)}\n(\n")
        for i in range(0, len(faces), 65536):
            time.sleep(0)  # release the GIL between write chunks
            f.write("".join(
                f"{len(v)}({' '.join(map(str, v))})\n" for v in faces[i:i + 65536]
            ))
        f.write(")\n")


def write_labels(path: Path, data: np.ndarray) -> None:
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write(_header("labelList", path.name))
        f.write(f"{len(data)}\n(\n")
        for i in range(0, len(data), 262144):
            time.sleep(0)  # release the GIL between write chunks
            f.write("\n".join(map(str, data[i:i + 262144].tolist())))
            f.write("\n")
        f.write(")\n")


def write_boundary(path: Path, patches: list[dict]) -> None:
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write(_header("polyBoundaryMesh", "boundary"))
        f.write(f"{len(patches)}\n(\n")
        for p in patches:
            f.write(f"    {p['name']}\n    {{\n")
            f.write(f"        type            {p.get('type', 'patch')};\n")
            f.write(f"        nFaces          {p['nFaces']};\n")
            f.write(f"        startFace       {p['startFace']};\n    }}\n")
        f.write(")\n")


def write_polymesh(
    poly_dir: Path,
    points: np.ndarray,
    faces: list[list[int]],
    owner: np.ndarray,
    neighbour: np.ndarray,
    patches: list[dict],
) -> None:
    """Write a complete polyMesh atomically: temp dir first, then replace.

    Prevents corruption of the original polyMesh if the write fails partway.
    """
    import shutil
    import tempfile

    tmp_dir = Path(tempfile.mkdtemp(prefix="foam_mesh_io_"))
    tmp_poly = tmp_dir / "polyMesh"
    tmp_poly.mkdir(parents=True, exist_ok=True)
    try:
        write_points(tmp_poly / "points", points)
        write_faces(tmp_poly / "faces", faces)
        write_labels(tmp_poly / "owner", owner)
        write_labels(tmp_poly / "neighbour", neighbour)
        write_boundary(tmp_poly / "boundary", patches)
        for name in ("points", "faces", "owner", "neighbour", "boundary"):
            p = tmp_poly / name
            if not p.exists() or p.stat().st_size == 0:
                raise RuntimeError(f"Verification failed: {name} is empty or missing")
        backup = poly_dir.with_suffix(".bak")
        if poly_dir.exists():
            if backup.exists():
                shutil.rmtree(backup)
            shutil.move(str(poly_dir), str(backup))
        try:
            shutil.move(str(tmp_poly), str(poly_dir))
        except Exception:
            # Restore from backup if move fails
            if backup.exists():
                shutil.move(str(backup), str(poly_dir))
            raise
        else:
            # Clean up backup on success
            if backup.exists():
                shutil.rmtree(backup)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
