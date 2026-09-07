#!/usr/bin/env python3
"""Generate benchmark STL geometries for CFD meshing quality testing.

Each geometry is exported as a binary STL file to benchmarks/geometries/.
Standalone script — uses cadquery and trimesh only.

Usage:
    python benchmarks/generate_geometries.py
"""

import math
import os
import sys

import cadquery as cq
import numpy as np
import trimesh

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "geometries")


def _cq_to_trimesh(obj, tolerance=0.001):
    shape = obj.val() if isinstance(obj, cq.CQ) else obj
    verts, faces = shape.tessellate(tolerance)
    verts = np.array([(v.x, v.y, v.z) for v in verts], dtype=np.float64)
    faces = np.array(faces, dtype=np.int64)
    return trimesh.Trimesh(vertices=verts, faces=faces)


def _export(mesh, filename, name=""):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, filename)
    if name:
        mesh.metadata["name"] = name
    mesh.export(path, file_type="stl")
    nv = mesh.vertices.shape[0]
    nf = mesh.faces.shape[0]
    print(f"  {filename:30s}  {nv:6,} verts  {nf:6,} faces")
    return path


# ---------------------------------------------------------------------------
# Geometry generators
# ---------------------------------------------------------------------------


def gen_pipe():
    """Simple pipe: radius=0.1m, length=1.0m."""
    result = cq.Workplane("XY").circle(0.1).extrude(1.0)
    mesh = _cq_to_trimesh(result)
    _export(mesh, "pipe.stl", name="pipe")


def gen_pipe_constriction():
    """Pipe with sharp inward constriction at mid-length.

    Constructed by stacking three cylinders — two full-radius sections
    sandwiching a narrow-radius waist — then fusing them.
    """
    lower = cq.Workplane("XY").circle(0.1).extrude(0.45)
    waist_r = cq.Workplane("XY").circle(0.03).extrude(0.1).translate((0, 0, 0.45))
    upper = cq.Workplane("XY").circle(0.1).extrude(0.45).translate((0, 0, 0.55))
    result = lower.union(waist_r).union(upper)
    mesh = _cq_to_trimesh(result)
    _export(mesh, "pipe_constriction.stl", name="pipe_constriction")


def gen_box_obstacle():
    """1 m cube with a 0.05 m cube obstacle at centre."""
    outer = cq.Workplane("XY").box(1, 1, 1)
    inner = cq.Workplane("XY").box(0.05, 0.05, 0.05)
    result = outer.cut(inner)
    mesh = _cq_to_trimesh(result)
    _export(mesh, "box_obstacle.stl", name="box_obstacle")


def _naca_0012_points(n=20):
    """Return closed list of (x, y) points for a NACA 0012 airfoil."""
    beta = np.linspace(0, math.pi, n)
    x = (1.0 - np.cos(beta)) / 2.0
    t = 0.12
    y = (t / 0.2) * (
        0.2969 * np.sqrt(x)
        - 0.1260 * x
        - 0.3516 * x ** 2
        + 0.2843 * x ** 3
        - 0.1015 * x ** 4
    )
    y[0] = 0.0
    y[-1] = 0.0

    pts = [(float(x[i]), float(y[i])) for i in range(n)]
    pts.extend((float(x[i]), -float(y[i])) for i in range(n - 2, 0, -1))
    return pts


def gen_external_aero():
    """NACA 0012 wing inside a 5x3x3 m farfield box."""
    box = cq.Workplane("XY").box(5, 3, 3)
    pts = _naca_0012_points(20)
    wing = cq.Workplane("XZ").polyline(pts).close().extrude(3.0, both=True)
    result = box.cut(wing)
    mesh = _cq_to_trimesh(result)
    _export(mesh, "external_aero.stl", name="external_aero")


def gen_thin_gap():
    """Fluid domain squeezed into a 1 mm gap between two plates.

    The point of this case is *proximity refinement*: cells must fit inside the
    narrow passage or the channel gets meshed shut. That needs the gap to be
    part of the fluid volume being meshed.

    The previous version was a single solid slab `box(0.1, 0.1, 0.001)` — the
    mesher was filling the inside of a 1 mm plate, not resolving a gap between
    two bodies, which is a different (and much less interesting) problem and
    exploded to ~435k cells. Here the two plates are cut OUT of the domain, so
    the remaining fluid includes the thin passage between them.
    """
    domain = cq.Workplane("XY").box(0.1, 0.1, 0.05)
    gap = 0.001
    plate_h = 0.02
    z_off = gap / 2 + plate_h / 2
    upper = cq.Workplane("XY").box(0.08, 0.08, plate_h).translate((0, 0, z_off))
    lower = cq.Workplane("XY").box(0.08, 0.08, plate_h).translate((0, 0, -z_off))
    result = domain.cut(upper).cut(lower)
    mesh = _cq_to_trimesh(result, tolerance=0.0001)
    _export(mesh, "thin_gap.stl", name="thin_gap")


def gen_non_watertight():
    """Cylinder with one cap removed — deliberately non-watertight."""
    cyl = cq.Workplane("XY").circle(0.1).extrude(1.0)
    mesh = _cq_to_trimesh(cyl)
    z_min = mesh.vertices[:, 2].min()
    face_z = mesh.vertices[mesh.faces][:, :, 2]
    is_bottom = np.all(np.isclose(face_z, z_min, atol=1e-6), axis=1)
    mesh = trimesh.Trimesh(
        vertices=mesh.vertices,
        faces=mesh.faces[~is_bottom],
        process=False,
    )
    _export(mesh, "non_watertight.stl", name="non_watertight")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main():
    print("Generating benchmark STL geometries for CFD meshing quality testing")
    print(f"Output directory: {OUTPUT_DIR}")
    print()

    gen_pipe()
    gen_pipe_constriction()
    gen_box_obstacle()
    gen_external_aero()
    gen_thin_gap()
    gen_non_watertight()

    print()
    print("Done \u2014 all 6 geometries generated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
