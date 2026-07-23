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
