from __future__ import annotations

import logging
from pathlib import Path

import trimesh

logger = logging.getLogger(__name__)

__all__ = [
    "heal_mesh", "write_binary_stl", "write_ascii_stl",
    "export_multisolid_stl", "export_surface_file",
    "estimate_stl_size",
]


# V1.1: heal/repair a mesh before export (F8)
def heal_mesh(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Apply trimesh repair operations to clean geometry before STL export.

    Returns the mesh (mutated in-place) and logs any changes made.
    """
    before_faces = len(mesh.faces)
    before_verts = len(mesh.vertices)
    name = mesh.metadata.get("name", "?")

    try:
        mesh.process(validate=True)
    except Exception as exc:
        logger.debug("mesh.process failed for '%s': %s", name, exc)

    try:
        mask = mesh.nondegenerate_faces()
        mesh.update_faces(mask)
    except (AttributeError, IndexError, TypeError) as exc:
        logger.debug("nondegenerate_faces/update_faces failed for '%s': %s", name, exc)
    except Exception as exc:
        logger.debug("remove_degenerate_faces failed for '%s': %s", name, exc)

    try:
        mesh.fill_holes()
    except (ImportError, ModuleNotFoundError) as exc:
        logger.debug("fill_holes skipped for '%s': missing dependency (%s)", name, exc)
    except Exception as exc:
        logger.debug("fill_holes failed for '%s': %s", name, exc)

    try:
        mesh.merge_vertices()
    except Exception as exc:
        logger.debug("merge_vertices failed for '%s': %s", name, exc)

    try:
        mesh.fix_normals()
    except Exception as exc:
        logger.debug("fix_normals failed for '%s': %s", name, exc)

    after_faces = len(mesh.faces)
    after_verts = len(mesh.vertices)
    if before_faces != after_faces or before_verts != after_verts:
        logger.info(
            "Healing patch '%s': faces %d\u2192%d, verts %d\u2192%d",
            name, before_faces, after_faces, before_verts, after_verts,
        )
    return mesh


def write_binary_stl(patches: dict[str, trimesh.Trimesh], filepath: Path | str) -> Path:
    """Write multi-solid STL in binary format (~5x faster, ~4x smaller)."""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    combined = trimesh.util.concatenate(
        list(patches.values()) if len(patches) > 1 else [next(iter(patches.values()))]
    )
    combined.export(str(filepath), file_type="stl")
    return filepath


def write_ascii_stl(patches: dict[str, trimesh.Trimesh], filepath: Path | str) -> Path:
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="ascii") as fh:
        for name, mesh in patches.items():
            # SECURITY: sanitize patch name — strip newlines/replace unsafe chars
            safe_name = name.replace("\n", " ").replace("\r", " ").strip()
            fh.write(f"solid {safe_name}\n")
            face_normals = mesh.face_normals
            for i, face in enumerate(mesh.faces):
                n = face_normals[i]
                fh.write(f"  facet normal {n[0]:.6e} {n[1]:.6e} {n[2]:.6e}\n")
                fh.write("    outer loop\n")
                for v_idx in face:
                    v = mesh.vertices[v_idx]
                    fh.write(f"      vertex {v[0]:.6e} {v[1]:.6e} {v[2]:.6e}\n")
                fh.write("    endloop\n")
                fh.write("  endfacet\n")
            fh.write(f"endsolid {safe_name}\n")
    return filepath


def estimate_stl_size(meshes: list[trimesh.Trimesh]) -> int:
    """Rough estimate of ASCII STL size in bytes for a set of meshes."""
    total_faces = sum(len(m.faces) for m in meshes)
    return total_faces * 180


def export_multisolid_stl(meshes: list[trimesh.Trimesh], output_path: Path | str) -> Path:
    patches: dict[str, trimesh.Trimesh] = {}
    for mesh in meshes:
        mesh = heal_mesh(mesh)
        name = mesh.metadata.get("name", "patch")
        if name in patches:
            existing = patches[name]
            patches[name] = existing + mesh
        else:
            patches[name] = mesh
    # Auto-select binary for large models (>10 MB estimated ASCII size)
    est_size = estimate_stl_size(meshes)
    if est_size > 10_000_000:
        return write_binary_stl(patches, output_path)
    return write_ascii_stl(patches, output_path)


def validate_stl_solids(path: Path | str) -> dict[str, int]:
    """Parse an ASCII STL and return {solid_name: face_count}.

    Useful for verifying multi-solid STL exports.
    """
    path = Path(path)
    text = path.read_text(encoding="ascii", errors="replace")
    import re
    solids: dict[str, int] = {}
    current = None
    count = 0
    for line in text.splitlines():
        m = re.match(r"\s*solid\s+(\S+)", line, re.IGNORECASE)
        if m:
            current = m.group(1)
            count = 0
            continue
        if re.match(r"\s*endsolid", line, re.IGNORECASE):
            if current is not None:
                solids[current] = count
            current = None
            continue
        if current is not None and re.match(r".*facet", line, re.IGNORECASE):
            count += 1
    return solids


def export_surface_file(
    meshes: list[trimesh.Trimesh],
    case_dir: Path | str,
    filename: str = "surface.stl",
) -> Path:
    case_dir = Path(case_dir)
    tri_surface_dir = case_dir / "constant" / "triSurface"
    return export_multisolid_stl(meshes, tri_surface_dir / filename)
