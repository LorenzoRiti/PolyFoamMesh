"""CAD-face grouping by the angle between adjacent faces (STEP patch split)."""
import cadquery as cq

from polyfoammesh.core.geometry import group_cad_faces_by_angle


def test_box_keeps_six_patches():
    box = cq.Workplane("XY").box(10, 10, 10).val()
    assert len(group_cad_faces_by_angle(box, 30.0)) == 6


def test_cylinder_wall_is_not_chopped():
    # side + two caps: the wall is ONE CAD face, no matter how fine the angle
    cyl = cq.Workplane("XY").circle(5).extrude(20).val()
    assert len(group_cad_faces_by_angle(cyl, 5.0)) == 3


def test_tangent_fillet_merges_with_plane_only_above_zero_angle():
    blk = cq.Workplane("XY").box(20, 20, 20).edges("|Z").fillet(3).val()
    n_fine = len(group_cad_faces_by_angle(blk, 5.0))
    n_coarse = len(group_cad_faces_by_angle(blk, 100.0))
    assert n_coarse < n_fine  # coarse angle merges sides; 90-deg box edges stay <100


def test_flat_sides_not_fused_with_tangent_fillets():
    # all-edges fillet: every face is tangent to its neighbours (dihedral 0),
    # yet the six flat sides must stay apart from the rounded edges/corners.
    blk = cq.Workplane("XY").box(20, 20, 20).edges().fillet(3).val()
    assert len(group_cad_faces_by_angle(blk, 30.0)) == 7  # 6 sides + 1 rounded body


def test_cylinder_and_bend_stay_one_wall():
    pipe = (cq.Workplane("XY").circle(2).extrude(10)
            .faces(">Z").workplane().circle(2).revolve(90, (-8, 0, 0), (-8, 1, 0))).val()
    n = len(group_cad_faces_by_angle(pipe, 30.0))
    assert n <= 3  # wall(s) merge; only the two end caps remain separate
