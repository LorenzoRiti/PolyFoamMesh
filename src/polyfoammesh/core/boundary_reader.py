from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass

from polyfoammesh.core.of_reader import read_of_text, of_list_count, of_label_list, of_ncells_from_header


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

    text = read_of_text(path)

    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)

    list_start = re.search(r"\d+\s*\n?\s*\(", text)
    if not list_start:
        raise ValueError("Could not find patch list in boundary file")
    open_idx = list_start.end() - 1
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


def count_cells(case_dir: Path | str) -> int:
    poly_dir = Path(case_dir) / "constant" / "polyMesh"
    owner = poly_dir / "owner"
    if not owner.exists():
        return 0

    n_from_header = of_ncells_from_header(owner)
    if n_from_header is not None:
        return n_from_header

    try:
        indices = of_label_list(owner)
        neighbour = poly_dir / "neighbour"
        if neighbour.exists():
            indices += of_label_list(neighbour)
        return (max(indices) + 1) if indices else 0
    except Exception:
        return 0


def count_points(case_dir: Path | str) -> int:
    path = Path(case_dir) / "constant" / "polyMesh" / "points"
    if not path.exists():
        return 0
    try:
        return of_list_count(path)
    except Exception:
        return 0


def count_faces(case_dir: Path | str) -> int:
    path = Path(case_dir) / "constant" / "polyMesh" / "faces"
    if not path.exists():
        return 0
    try:
        return of_list_count(path)
    except Exception:
        return 0
