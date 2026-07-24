from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass


@dataclass
class PatchInfo:
    name: str
    patch_type: str
    n_faces: int
    start_face: int


def parse_boundary(boundary_path: Path | str) -> list[PatchInfo]:
    path = Path(boundary_path)
    if not path.exists():
        raise FileNotFoundError(f"Boundary file not found: {path}")

    text = path.read_text(encoding="ascii", errors="replace")

    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)

    # Locate the top-level "<count>\n(...)" OpenFOAM list that holds the
    # patches. A naive non-greedy `\(.*?\)` regex stops at the FIRST
    # closing paren it finds — which is wrong whenever a patch contains a
    # nested parenthesised field like `inGroups 1(wall);` (emitted by
    # cfMesh/OpenFOAM for every patch), truncating the match and losing
    # all patches. Instead, find the list's opening paren and scan forward
    # tracking bracket depth to find its true matching close.
    list_start = re.search(r"\d+\s*\n?\s*\(", text)
    if not list_start:
        raise ValueError("Could not find patch list in boundary file")
    open_idx = list_start.end() - 1  # index of the '(' itself
    depth = 0
    close_idx = None
    for i in range(open_idx, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                close_idx = i
                break
    if close_idx is None:
        raise ValueError("Could not find patch list in boundary file")
    body = text[open_idx + 1:close_idx]

    patches: list[PatchInfo] = []
    patch_re = re.compile(
        r"(\S+)\s*\{([^}]+)\}",
        re.DOTALL,
    )

    for match in patch_re.finditer(body):
        name = match.group(1)
        block = match.group(2)

        ptype = _extract_word(block, "type")
        nfaces = _extract_int(block, "nFaces")
        startface = _extract_int(block, "startFace")

        patches.append(PatchInfo(
            name=name,
            patch_type=ptype,
            n_faces=nfaces,
            start_face=startface,
        ))

    return patches


def _extract_word(text: str, key: str) -> str:
    m = re.search(rf"{key}\s+(\S+)\s*;", text)
    return m.group(1) if m else ""


def _extract_int(text: str, key: str) -> int:
    m = re.search(rf"{key}\s+(\d+)\s*;", text)
    return int(m.group(1)) if m else 0


def _read_of_list_count(path: Path) -> int:
    """Return the declared entry count of a top-level OpenFOAM ascii list
    file (points, faces, owner, ...): the bare integer that precedes the
    list's opening paren, e.g. the "N" in "N\\n(\\n...\\n)".

    Not the same as counting lines — the FoamFile header block varies in
    length (comment banner, arch/note lines, etc.), so `len(lines) - k` for
    any fixed k silently returns the wrong count.
    """
    text = path.read_text(encoding="ascii", errors="replace")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    header_end = text.find("}")  # end of the FoamFile {...} block
    body = text[header_end + 1:] if header_end != -1 else text
    m = re.search(r"(\d+)\s*\n\s*\(", body)
    return int(m.group(1)) if m else 0


def read_label_list(path: Path) -> list[int]:
    """Return the integer values of a flat OpenFOAM scalar labelList file
    (owner, neighbour: one plain integer per line, no nested parens)."""
    text = path.read_text(encoding="ascii", errors="replace")
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


def count_cells(case_dir: Path | str) -> int:
    """Return the real cell count from constant/polyMesh.

    The owner file lists one entry per FACE (which cell owns it), so its
    line count is a face-count proxy, not a cell count — for a typical hex
    mesh nFaces is roughly 3x nCells, so `len(lines) - 2` (used, before this
    fix, in mesh_engine.py/exporter.py/mosaic.py/amr.py and the GUI's own
    status bar) silently reported the wrong number on every real mesh, not
    just an off-by-a-few undercount.

    cfMesh's cartesianMesh writes the real counts into the owner file's own
    FoamFile header comment (`note "nPoints:X nCells:Y nFaces:Z ..."`), but
    other mesh-writing utilities (verified: polyDualMesh's polyhedral
    conversion) don't include that note at all. When it's absent, fall back
    to the actual OpenFOAM definition of cell count: cell indices are 0-based
    and contiguous, so the highest index referenced across owner+neighbour,
    plus 1, is nCells — verified against checkMesh's own reported cell count
    on real cfMesh/polyDualMesh output.
    """
    poly_dir = Path(case_dir) / "constant" / "polyMesh"
    owner = poly_dir / "owner"
    if not owner.exists():
        return 0
    try:
        text = owner.read_text(encoding="ascii", errors="replace")
    except Exception:
        return 0

    m = re.search(r"nCells:\s*(\d+)", text)
    if m:
        return int(m.group(1))

    try:
        indices = read_label_list(owner)
        neighbour = poly_dir / "neighbour"
        if neighbour.exists():
            indices += read_label_list(neighbour)
        return (max(indices) + 1) if indices else 0
    except Exception:
        return 0


def count_points(case_dir: Path | str) -> int:
    """Return the real point count from constant/polyMesh/points."""
    path = Path(case_dir) / "constant" / "polyMesh" / "points"
    if not path.exists():
        return 0
    try:
        return _read_of_list_count(path)
    except Exception:
        return 0


def count_faces(case_dir: Path | str) -> int:
    """Return the real face count from constant/polyMesh/faces."""
    path = Path(case_dir) / "constant" / "polyMesh" / "faces"
    if not path.exists():
        return 0
    try:
        return _read_of_list_count(path)
    except Exception:
        return 0
