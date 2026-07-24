"""Validate and export a case so BaramFlow can open it without errors.

BaramFlow (NextFOAM's OpenFOAM GUI) opens native OpenFOAM cases directly, so
"export" is a copy — but only of a case that is actually *complete and internally
consistent*. The failure that bites users is not a missing file (easy to spot)
but a mesh/field mismatch: a patch present in constant/polyMesh/boundary that has
no entry in a 0/ field's boundaryField. OpenFOAM (and therefore BaramFlow) then
aborts on load with a "cannot find patchField entry" error that says nothing
about the real cause. This module checks for that before the user ships the case.
"""
from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from cfmesh_autogui.core.boundary_reader import parse_boundary

logger = logging.getLogger(__name__)

REQUIRED_POLYMESH = ("points", "faces", "owner", "neighbour", "boundary")
REQUIRED_SYSTEM = ("controlDict", "fvSchemes", "fvSolution")
# p and U are always written; other fields (k, omega, ...) are checked too if
# present, but only these two are mandatory.
REQUIRED_FIELDS = ("p", "U")

_VALID_PATCH_TYPES = {
    "patch", "wall", "symmetry", "symmetryPlane", "empty", "wedge",
    "cyclic", "cyclicAMI", "processor",
}


@dataclass
class BaramflowValidation:
    ok: bool = False
    missing_files: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    patches: dict[str, str] = field(default_factory=dict)  # name -> type

    def summary(self) -> str:
        if self.ok:
            return f"Case ready for BaramFlow ({len(self.patches)} patches)."
        parts = []
        if self.missing_files:
            parts.append("Missing: " + ", ".join(self.missing_files))
        parts.extend(self.issues)
        return " | ".join(parts)


def _field_boundary_patches(field_path: Path) -> set[str]:
    """Patch names that have a boundaryField entry in an OpenFOAM field file."""
    text = field_path.read_text(encoding="ascii", errors="replace")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)

    m = re.search(r"boundaryField\s*\{", text)
    if not m:
        return set()
    # Walk from the opening brace to its matching close, tracking depth.
    start = m.end() - 1
    depth = 0
    end = len(text)
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    body = text[start + 1:end]

    # Direct children of boundaryField: "<name> {" at depth 0 within body.
    names: set[str] = set()
    depth = 0
    for tok in re.finditer(r"([A-Za-z_][\w.\"|]*)\s*\{|\}", body):
        if tok.group(0) == "}":
            depth -= 1
            continue
        if depth == 0 and tok.group(1):
            names.add(tok.group(1).strip('"'))
        depth += 1
    return names


def validate_case(case_dir: Path | str) -> BaramflowValidation:
    """Check a case is complete and mesh/field-consistent for BaramFlow."""
    case_dir = Path(case_dir)
    result = BaramflowValidation()

    poly = case_dir / "constant" / "polyMesh"
    for name in REQUIRED_POLYMESH:
        if not (poly / name).is_file():
            result.missing_files.append(f"constant/polyMesh/{name}")
    for name in REQUIRED_SYSTEM:
        if not (case_dir / "system" / name).is_file():
            result.missing_files.append(f"system/{name}")
    for name in REQUIRED_FIELDS:
        if not (case_dir / "0" / name).is_file():
            result.missing_files.append(f"0/{name}")

    if result.missing_files:
        # Can't check consistency without the files; report structure first.
        return result

    try:
        patches = parse_boundary(poly / "boundary")
    except Exception as exc:
        result.issues.append(f"boundary file unreadable: {exc}")
        return result

    if not patches:
        result.issues.append("boundary file defines no patches")
        return result

    mesh_patch_names = set()
    for p in patches:
        result.patches[p.name] = p.patch_type
        mesh_patch_names.add(p.name)
        if p.patch_type not in _VALID_PATCH_TYPES:
            result.issues.append(f"patch '{p.name}' has unknown type '{p.patch_type}'")

    # The real integration check: every mesh patch must have a boundary
    # condition in every initial field, or OpenFOAM/BaramFlow won't load.
    zero = case_dir / "0"
    for field_file in sorted(zero.glob("*")):
        if not field_file.is_file():
            continue
        field_patches = _field_boundary_patches(field_file)
        if not field_patches:
            result.issues.append(f"0/{field_file.name}: no boundaryField block")
            continue
        missing = mesh_patch_names - field_patches
        if missing:
            result.issues.append(
                f"0/{field_file.name}: no boundary condition for patch(es) "
                + ", ".join(sorted(missing))
            )

    result.ok = not result.issues and not result.missing_files
    return result


def export_case(case_dir: Path | str, dest_parent: Path | str) -> Path:
    """Copy a validated case into *dest_parent*/<case name>.

    Raises ValueError if the case does not validate (never ship a case that
    will fail to open) or if the destination collides with the source.
    """
    case_dir = Path(case_dir).resolve()
    validation = validate_case(case_dir)
    if not validation.ok:
        raise ValueError(f"Case is not BaramFlow-ready: {validation.summary()}")

    dest = Path(dest_parent).resolve() / case_dir.name
    if dest == case_dir:
        raise ValueError("Destination must differ from the source case directory.")

    shutil.copytree(case_dir, dest, dirs_exist_ok=True)
    logger.info("Exported BaramFlow case to %s", dest)
    return dest
