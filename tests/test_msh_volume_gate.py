"""`_has_volume_elements` must read only the $Elements section.

Regression (2026-11-11, Venturi DN200 / Parte4 STEPs): GMSH wrote a
surface-only mesh; a $PhysicalNames row like ``2 4 "surface_4"`` read as
"element type 4 = tet", the gate said "has volume" and the GUI never fell
back to fTetWild - the dual then died on a 0-cell mesh.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core.tet_source import _has_volume_elements  # noqa: E402

V2_HEAD = (
    "$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$PhysicalNames\n3\n"
    '2 2 "surface_2"\n2 4 "surface_4"\n2 11 "surface_11"\n$EndPhysicalNames\n'
    "$Nodes\n4\n1 4 5 6\n2 7 0 0\n3 0 7 0\n4 0 0 7\n$EndNodes\n"
)


def _v2(tmp_path, elements: list[str]) -> Path:
    body = f"$Elements\n{len(elements)}\n" + "".join(e + "\n" for e in elements)
    p = tmp_path / "m.msh"
    p.write_text(V2_HEAD + body + "$EndElements\n")
    return p


def test_v2_surface_only_with_tricky_physical_names_is_not_volume(tmp_path):
    tri = ["1 2 2 2 2 1 2 3", "2 2 2 4 4 1 2 4"]
    assert _has_volume_elements(_v2(tmp_path, tri)) is False


def test_v2_with_tetrahedra_is_volume(tmp_path):
    rows = ["1 2 2 2 2 1 2 3", "2 4 2 9 9 1 2 3 4"]
    assert _has_volume_elements(_v2(tmp_path, rows)) is True


def _v4(tmp_path, blocks: list[tuple[int, int, int, list[str]]]) -> Path:
    n = sum(len(b[3]) for b in blocks)
    txt = "$MeshFormat\n4.1 0 8\n$EndMeshFormat\n$Nodes\n1 4 1 4\n"
    txt += "$EndNodes\n$Elements\n"
    txt += f"{len(blocks)} {n} 1 {n}\n"
    for dim, tag, etype, rows in blocks:
        txt += f"{dim} {tag} {etype} {len(rows)}\n" + "".join(r + "\n" for r in rows)
    p = tmp_path / "m4.msh"
    p.write_text(txt + "$EndElements\n")
    return p


def test_v4_triangle_with_element_tag_3_is_not_volume(tmp_path):
    # the old test matched "3 <n1> <n2> <n3>" = a triangle whose tag is 3
    rows = ["1 1 2 3", "2 2 3 4", "3 3 4 1"]
    assert _has_volume_elements(_v4(tmp_path, [(2, 1, 2, rows)])) is False


def test_v4_volume_block_after_surface_blocks_is_volume(tmp_path):
    surf = (2, 1, 2, ["1 1 2 3", "2 2 3 4"])
    vol = (3, 1, 4, ["3 1 2 3 4"])
    assert _has_volume_elements(_v4(tmp_path, [surf, vol])) is True


def test_unknown_or_binary_formats_fail_open(tmp_path):
    p = tmp_path / "b.msh"
    p.write_text("$MeshFormat\n4.1 1 8\n$EndMeshFormat\n")
    assert _has_volume_elements(p) is True
    q = tmp_path / "x.msh"
    q.write_text("not a gmsh file\n")
    assert _has_volume_elements(q) is True
