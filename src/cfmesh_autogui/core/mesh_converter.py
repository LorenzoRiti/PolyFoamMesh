"""Convert MSH (GMSH) files to OpenFOAM polyMesh format.

Uses meshio to read .msh files and write OpenFOAM polyMesh files.

The conversion maps GMSH physical groups → OpenFOAM boundary patches
by reading cell_data from the .msh file. Each cell type (tetra, hex,
prism/wedge, pyramid) has its face topology encoded.

Face extraction strategy:
  1. Read all cells and their physical group tags from meshio.
  2. Extract faces from each cell, tracking whether each face is
     internal (two owners) or boundary (one owner + a physical group).
  3. Write OpenFOAM files (points, faces, owner, neighbour, boundary).
"""
from __future__ import annotations

import logging
from pathlib import Path

import meshio
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cell topology: for each GMSH element type, list the vertex-index lists
# of its faces. Vertex indices are LOCAL to the cell (0..N-1).
# ---------------------------------------------------------------------------
# Every template below is wound so that every face normal points OUT of the
# cell, verified numerically on reference cells (right-hand-rule normal with
# dot(normal, centroid - face_center) < 0 on every face, plus closure of the
# area vectors: sum over faces == 0). OpenFOAM computes cell volume via the
# divergence theorem over the stored face normals, so inward faces directly
# produce negative-volume cells; the old templates had 2 of 4 tet faces,
# 2 of 6 hex faces, 3 of 5 wedge faces and 2 of 5 pyramid faces wound
# inward. An earlier attempt that flipped two tet faces in isolation made
# things dramatically worse (45% -> 99.7% negative-volume cells) because it
# reversed the two faces that were ALREADY outward and left the genuinely
# inward ones untouched. Both halves of the fix are here: the outward
# templates below + owner-winding storage of internal faces in
# msh_to_of_polymesh().
_TETRA_FACES = [[0, 2, 1], [0, 1, 3], [1, 2, 3], [0, 3, 2]]
# Hexahedron (GMSH type 5): 6 faces, each a quad
_HEX_FACES = [
    [0, 3, 2, 1], [4, 5, 6, 7],  # bottom, top
    [0, 1, 5, 4], [2, 3, 7, 6],  # front, back
    [1, 2, 6, 5], [0, 4, 7, 3],  # right, left
]
# Wedge / Prism (GMSH type 6): 5 faces — 2 triangles + 3 quads
_WEDGE_FACES = [
    [0, 2, 1],              # top triangle
    [3, 4, 5],              # bottom triangle (opposite winding)
    [0, 1, 4, 3],           # quad side 1
    [1, 2, 5, 4],           # quad side 2
    [0, 3, 5, 2],           # quad side 3
]
# Pyramid (GMSH type 7): 5 faces — 1 quad + 4 triangles
_PYRAMID_FACES = [
    [0, 3, 2, 1],           # base quad
    [0, 1, 4],              # tri side 1
    [1, 2, 4],              # tri side 2
    [2, 3, 4],              # tri side 3
    [0, 4, 3],              # tri side 4
]

# Map: GMSH element_type_number → list_of_faces (local vertex indices)
_CELL_FACE_MAP = {
    4: _TETRA_FACES,    # tetra
    5: _HEX_FACES,      # hex
    6: _WEDGE_FACES,    # wedge/prism
    7: _PYRAMID_FACES,  # pyramid
    2: [[0, 1, 2]],     # triangle (surface)
    3: [[0, 1, 2, 3]],  # quad (surface)
}

# GMSH element types that are 3D cells (not surface elements)
_3D_CELL_TYPES = {4, 5, 6, 7}

# meshio cell type → our topology key
_MESHIO_CELL_MAP = {
    "tetra": 4,
    "hexahedron": 5,
    "wedge": 6,
    "pyramid": 7,
    "triangle": 2,
    "quad": 3,
}


def msh_to_of_polymesh(
    msh_path: Path | str,
    case_dir: Path | str,
    boundary_patches: dict[int, str] | None = None,
) -> Path:
    """Convert a GMSH .msh file to OpenFOAM polyMesh.

    Args:
        msh_path: Path to the .msh file.
        case_dir: Case directory; polyMesh/ will be created inside constant/.
        boundary_patches: Optional dict {physical_group_tag: patch_name}.
            If None, patch names are extracted from the .msh field_data.

    Returns:
        Path to constant/polyMesh/ directory.
    """
    msh_path = Path(msh_path).resolve()
    case_dir = Path(case_dir).resolve()
    poly_dir = case_dir / "constant" / "polyMesh"
    poly_dir.mkdir(parents=True, exist_ok=True)

    mesh = meshio.read(str(msh_path))
    points = mesh.points
    cells = mesh.cells
    cell_data = mesh.cell_data
    field_data = mesh.field_data

    # ------------------------------------------------------------------
    # Phase 1: Extract per-element physical group tags from cell_data
    # ------------------------------------------------------------------
    # meshio stores cell_data["gmsh:physical"] as a NumPy array per cell
    # block. Each entry is the physical group tag for that element.
    #
    # Two maps, keyed differently on purpose:
    #  - surface_face_tag_map[sorted-vertex-tuple] = physical_tag, for 2D
    #    (surface) elements — this is what actually carries the named
    #    patch (inlet/outlet/wall/surface_N) a boundary face belongs to.
    #  - cell_phys_map[3D-cell-local-index] = physical_tag, for 3D
    #    (volume) elements — only meaningful for multi-region meshes
    #    with more than one named volume; kept as a fallback.
    # A single running counter across ALL blocks (2D and 3D mixed) was
    # used as the cell_phys_map key before, and Phase 2 below re-used
    # that same counter as the OpenFOAM owner/neighbour cell index for
    # 3D cells — but GMSH lists surface elements before volume elements,
    # so every 3D cell's index came out offset by the total surface
    # element count. checkMesh then believed the mesh had that many
    # extra cells, all with zero faces ("illegal cells"), and crashed.
    # Root-caused via a GMSH-generated STEP tet mesh: 8665 real
    # tetrahedra plus exactly 2956 phantom empty cells — 2956 being the
    # exact total triangle count across the boundary surface blocks.
    surface_face_tag_map: dict[tuple[int, ...], int] = {}
    cell_phys_map: dict[int, int] = {}
    phys_tags_seen: set[int] = set()
    cell_3d_counter = 0
    for block_idx, cell_block in enumerate(cells):
        n_cells = len(cell_block.data)
        phys_data = None
        if cell_data is not None and "gmsh:physical" in cell_data:
            data_array = cell_data["gmsh:physical"]
            # data_array can be a list of arrays or a single array
            if isinstance(data_array, list) and block_idx < len(data_array):
                phys_data = data_array[block_idx]
            elif isinstance(data_array, np.ndarray) and data_array.ndim == 1:
                phys_data = data_array
        is_3d = _MESHIO_CELL_MAP.get(cell_block.type) in _3D_CELL_TYPES
        for ci, verts in enumerate(cell_block.data):
            tag = int(phys_data[ci]) if phys_data is not None else 0
            if tag > 0:
                phys_tags_seen.add(tag)
            if is_3d:
                cell_phys_map[cell_3d_counter] = tag
                cell_3d_counter += 1
            else:
                surface_face_tag_map[tuple(sorted(int(v) for v in verts))] = tag

    # ------------------------------------------------------------------
    # Phase 2: Extract faces from 3D cells
    # ------------------------------------------------------------------
    # For each cell, for each face, we compute a canonical face key
    # (sorted vertex tuple). If we've seen the key before, the face is
    # INTERNAL (owned by two cells). If not, it's a BOUNDARY face.
    # We also track the physical tag of each boundary face's owner cell.
    all_faces: list[list[int]] = []       # vertex indices per face
    all_owners: list[int] = []            # owner cell index per face
    all_neighbours: list[int] = []        # neighbour (-1 for boundary)
    face_owner_tags: list[int] = []       # physical tag of owner cell

    face_cell_map: dict[tuple[int, ...], tuple[int, int]] = {}

    # 3D-cell-local counter — do NOT increment for skipped 2D/unsupported
    # blocks (see Phase 1 comment: that was the bug that produced 2956
    # phantom zero-face cells on a real STEP-derived tet mesh).
    cell_counter = 0
    for cell_block in cells:
        cell_type_str = cell_block.type
        type_id = _MESHIO_CELL_MAP.get(cell_type_str)
        if type_id not in _3D_CELL_TYPES:
            continue  # surface/line/point elements — not OpenFOAM cells
        face_template = _CELL_FACE_MAP.get(type_id)
        if face_template is None:
            logger.warning("Unknown cell type '%s', skipping %d cells.",
                           cell_type_str, len(cell_block.data))
            continue

        for ci, verts in enumerate(cell_block.data):
            for fv_local in face_template:
                face_verts = [int(verts[v]) for v in fv_local]
                key = tuple(sorted(face_verts))
                if key in face_cell_map:
                    other_owner, owner_face = face_cell_map.pop(key)
                    # Store the OWNER's winding, not the current (second)
                    # cell's: with outward templates the owner's winding
                    # points out of the owner — i.e. FROM the owner TO the
                    # neighbour, exactly what OpenFOAM's "incorrectly
                    # oriented face" check requires for an internal face
                    # (normal from owner to neighbour). Storing the second
                    # cell's outward winding pointed the stored normal the
                    # wrong way (neighbour -> owner) on every internal face,
                    # which together with the inward face templates produced
                    # the mass of negative-volume cells this module used to
                    # emit on real GMSH tet meshes.
                    all_faces.append(owner_face)
                    all_owners.append(other_owner)
                    all_neighbours.append(cell_counter)
                    face_owner_tags.append(0)  # internal
                else:
                    # Keep the ORIGINAL (unsorted) vertex order, not the
                    # sorted lookup key — a face's winding determines
                    # which way its normal points, and OpenFOAM requires
                    # that normal to point out of the owner cell. Boundary
                    # faces used to be reconstructed from the sorted key
                    # (arbitrary winding), which silently produced
                    # negative-volume cells and "incorrectly oriented"
                    # faces on essentially every geometry — the 3D face
                    # template's vertex order already comes out right,
                    # this just has to not get thrown away.
                    face_cell_map[key] = (cell_counter, face_verts)
            cell_counter += 1

    # Remaining entries in face_cell_map = boundary faces
    boundary_faces_by_tag: dict[int, list[list[int]]] = {}
    for key, (owner_cell, face_verts) in face_cell_map.items():
        # A boundary face's patch comes from the named 2D surface element
        # that coincides with it (inlet/outlet/wall/surface_N) — NOT from
        # the owning 3D cell's tag, which is only ever meaningful for
        # multi-region meshes with more than one named volume. Using the
        # cell tag here meant every boundary face fell into the same
        # single (or untagged) bucket, losing the actual patch split.
        tag = surface_face_tag_map.get(key, cell_phys_map.get(owner_cell, 0))
        if tag not in boundary_faces_by_tag:
            boundary_faces_by_tag[tag] = []
        boundary_faces_by_tag[tag].append(face_verts)

    # Append boundary faces to the main arrays
    start_face_idx = len(all_faces)
    boundary_start_faces: dict[int, int] = {}
    for tag in sorted(boundary_faces_by_tag.keys()):
        boundary_start_faces[tag] = start_face_idx
        for fv in boundary_faces_by_tag[tag]:
            all_faces.append(fv)
            owner = face_cell_map.get(tuple(sorted(fv)), (0, 0))[0]
            all_owners.append(owner)
            all_neighbours.append(-1)
            face_owner_tags.append(tag)
            start_face_idx += 1

    # ------------------------------------------------------------------
    # Phase 3: Build boundary patches from physical groups
    # ------------------------------------------------------------------
    # Build a tag → name map from field_data (physical group definitions)
    tag_to_name: dict[int, str] = {}
    if field_data:
        for name, (tag, dim) in field_data.items():
            if dim == 2 or dim == 3:  # surface or volume
                tag_to_name[int(tag)] = name

    # If boundary_patches was provided (explicit mapping), use it
    if boundary_patches:
        tag_to_name.update(boundary_patches)

    patches: list[dict] = []
    for tag in sorted(boundary_faces_by_tag.keys()):
        name = tag_to_name.get(tag, f"surface_{tag}")
        n_faces = len(boundary_faces_by_tag[tag])
        start = boundary_start_faces[tag]
        patches.append({
            "name": name,
            "type": "patch",
            "nFaces": n_faces,
            "startFace": start,
        })

    # If no boundary faces at all, create a placeholder
    if not patches:
        patches.append({
            "name": "default",
            "type": "patch",
            "nFaces": 0,
            "startFace": 0,
        })

    # ------------------------------------------------------------------
    # Phase 4: Write polyMesh files
    # ------------------------------------------------------------------
    _write_points(poly_dir, points)
    _write_faces(poly_dir, all_faces)
    _write_owner(poly_dir, all_owners)
    _write_neighbour(poly_dir, all_neighbours)
    _write_boundary(poly_dir, patches)

    # Count 3D cells
    n_3d = cell_counter
    logger.info(
        "polyMesh written: %d points, %d cells, %d faces (%d boundary patches)",
        len(points), n_3d, len(all_faces), len(patches),
    )
    return poly_dir


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------

def _of_header(class_type: str, object_name: str) -> str:
    """OpenFOAM ASCII file header.

    The banner must stay inside a single C-style comment (opened on the
    first line with '*\\', closed only on the last banner line with
    '*/') — closing it early on line 1 leaves the '|'-boxed lines as
    raw tokens, which OpenFOAM's parser rejects with "First token could
    not be read or is not 'FoamFile'" before it ever reaches the actual
    data (found via a broken GMSH-tet-mesh conversion: checkMesh,
    polyDualMesh and ParaView all failed to open the resulting mesh).
    """
    hdr = (
        "/*--------------------------------*- C++ -*----------------------------------*\\\n"
        "| =========                 |                                                 |\n"
        "| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |\n"
        "|  \\\\    /   O peration     | Version:  v2512                                 |\n"
        "|   \\\\  /    A nd           | Website:  www.openfoam.com                      |\n"
        "|    \\\\/     M anipulation  |                                                 |\n"
        "\\*---------------------------------------------------------------------------*/\n"
        "FoamFile\n{{\n"
        "    version     2.0;\n"
        "    format      ascii;\n"
        "    class       {cs};\n"
        "    location    \"constant/polyMesh\";\n"
        "    object      {obj};\n"
        "}}\n"
        "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n"
    )
    return hdr.format(cs=class_type, obj=object_name)


def _write_points(poly_dir: Path, points: np.ndarray) -> None:
    n = len(points)
    lines = [str(n), "("]
    for p in points:
        lines.append(f"    ({p[0]:.10e} {p[1]:.10e} {p[2]:.10e})")
    lines.append(")")
    (poly_dir / "points").write_text(
        _of_header("vectorField", "points") + "\n".join(lines) + "\n", encoding="ascii",
    )


def _write_faces(poly_dir: Path, faces: list[list[int]]) -> None:
    n = len(faces)
    lines = [str(n), "("]
    for fv in faces:
        lines.append(f"{len(fv)}({' '.join(str(v) for v in fv)})")
    lines.append(")")
    (poly_dir / "faces").write_text(
        _of_header("faceList", "faces") + "\n".join(lines) + "\n", encoding="ascii",
    )


def _write_owner(poly_dir: Path, owners: list[int]) -> None:
    n = len(owners)
    lines = [str(n), "("]
    for o in owners:
        lines.append(str(o))
    lines.append(")")
    (poly_dir / "owner").write_text(
        _of_header("labelList", "owner") + "\n".join(lines) + "\n", encoding="ascii",
    )


def _write_neighbour(poly_dir: Path, neighbours: list[int]) -> None:
    n = len(neighbours)
    lines = [str(n), "("]
    for nb in neighbours:
        lines.append(str(nb))
    lines.append(")")
    (poly_dir / "neighbour").write_text(
        _of_header("labelList", "neighbour") + "\n".join(lines) + "\n", encoding="ascii",
    )


def _write_boundary(poly_dir: Path, patches: list[dict]) -> None:
    lines = [str(len(patches)), "("]
    for p in patches:
        lines.append(f"    {p['name']}")
        lines.append("    {")
        lines.append(f"        type            {p['type']};")
        lines.append(f"        nFaces          {p['nFaces']};")
        lines.append(f"        startFace       {p['startFace']};")
        lines.append("    }")
    lines.append(")")
    (poly_dir / "boundary").write_text(
        _of_header("polyBoundaryMesh", "boundary") + "\n".join(lines) + "\n", encoding="ascii",
    )
