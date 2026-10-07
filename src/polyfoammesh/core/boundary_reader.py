from __future__ import annotations

import re
import shutil
from pathlib import Path
from dataclasses import dataclass

from polyfoammesh.core.of_reader import read_of_text, of_list_count, of_label_list, of_ncells_from_header


@dataclass
class PatchInfo:
    name: str
    patch_type: str
    n_faces: int
    start_face: int


#: Characters an OpenFOAM patch name may not contain (structural, quotes or
#: whitespace). Shared by the GUI rename validator and the writer below.
_INVALID_PATCH_NAME_RE = re.compile(r'[;{}"/\\\s]')


def rename_boundary_patch(
    case_dir: Path | str, old_name: str, new_name: str,
) -> Path:
    """Rename a patch in ``constant/polyMesh/boundary`` and back the file up.

    The generated-mesh rename path (the mandate's "rinominare dopo il meshing"):
    validates the new name (non-empty, no structural/quote/whitespace chars,
    not already in use), copies ``boundary`` to ``boundary.bak`` (only if no
    backup exists yet, so the ORIGINAL is never overwritten), then rewrites the
    single standalone patch-name line. Everything else in the file — nFaces,
    startFace, type, physicalType, inGroups — is preserved byte-for-byte.

    Raises ``FileNotFoundError`` when the case has no boundary file and
    ``ValueError`` on an invalid/missing/duplicate name.
    """
    case = Path(case_dir)
    bpath = case / "constant" / "polyMesh" / "boundary"
    if not bpath.exists():
        raise FileNotFoundError(f"Boundary file not found: {bpath}")

    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("patch name cannot be empty")
    if _INVALID_PATCH_NAME_RE.search(new_name):
        raise ValueError(
            "patch name must not contain ; { } \" / \\ or whitespace"
        )

    patches = parse_boundary(bpath)
    names = [p.name for p in patches]
    if old_name not in names:
        raise ValueError(f"patch not found: {old_name}")
    if new_name != old_name and new_name in names:
        raise ValueError(f"patch name already in use: {new_name}")
    if new_name == old_name:
        return bpath

    bak = bpath.with_name("boundary.bak")
    if not bak.exists():
        shutil.copy2(bpath, bak)

    # latin-1 round-trips every byte of the ASCII/binary-ish file verbatim.
    text = bpath.read_bytes().decode("latin-1")
    # Tolerate CRLF: capture the optional carriage return so the rewrite keeps
    # the file's line endings intact.
    pattern = re.compile(
        rf"(?m)^([ \t]*){re.escape(old_name)}([ \t]*)(\r?)$"
    )
    new_text, n = pattern.subn(
        lambda m: f"{m.group(1)}{new_name}{m.group(2)}{m.group(3)}", text, count=1
    )
    if n != 1:
        raise ValueError(
            f"could not locate the boundary entry for '{old_name}' to rename"
        )
    bpath.write_bytes(new_text.encode("latin-1"))
    return bpath



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
