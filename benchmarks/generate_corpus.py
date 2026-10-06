#!/usr/bin/env python3
"""Generate the 9 comparison-corpus STLs (thin_gap.stl already exists).

Deterministic cadquery builds; output: benchmarks/geometries/*.stl.
Shapes mirror the spec table (docs/superpowers/specs/2026-09-22-...):
cylinder, cube_duct, groove1, slot1, venturi, mini_valve,
concave_bracket, dirty_cad (OPEN on purpose), multi_inlet.

Usage: python benchmarks/generate_corpus.py
"""

from __future__ import annotations

import os
import sys

import cadquery as cq
import numpy as np
import trimesh

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_geometries import _cq_to_trimesh, _export, _export_step  # noqa: E402


def gen_cylinder() -> None:
    """r=0.05, L=0.2 solid — sanity geometry."""
    mesh = _cq_to_trimesh(cq.Workplane("XY").circle(0.05).extrude(0.2))
    _export(mesh, "cylinder.stl", name="cylinder")


def gen_cube_duct() -> None:
    """0.1 cube with a straight through-duct (r=0.015, along Z)."""
    outer = cq.Workplane("XY").box(0.1, 0.1, 0.1)
    bore = cq.Workplane("XY").circle(0.015).extrude(0.1, both=True)
    mesh = _cq_to_trimesh(outer.cut(bore))
    _export(mesh, "cube_duct.stl", name="cube_duct")


def gen_groove1() -> None:
    """0.1 x 0.05 x 0.05 block with a transverse top groove (0.06 x 0.008 x 0.01)."""
    block = cq.Workplane("XY").box(0.1, 0.05, 0.05)
    groove = cq.Workplane("XY").box(0.06, 0.008, 0.01).translate((0, 0, 0.02))
    mesh = _cq_to_trimesh(block.cut(groove))
    _export(mesh, "groove1.stl", name="groove1")


def gen_slot1() -> None:
    """0.1 x 0.06 x 0.02 plate with a 1.5 mm slot through its long axis."""
    plate = cq.Workplane("XY").box(0.1, 0.06, 0.02)
    slot = cq.Workplane("XY").box(0.08, 0.0015, 0.03)
    mesh = _cq_to_trimesh(plate.cut(slot))
    _export(mesh, "slot1.stl", name="slot1")


def gen_venturi() -> None:
    """Axisymmetric venturi fluid volume: R=0.04 -> throat r=0.012 -> R=0.04."""
    R, r, L = 0.04, 0.012, 0.15
    wp = cq.Workplane("XY")
    parts = [wp.circle(R).extrude(L * 0.35)]
    h = L * 0.35
    parts.append(wp.workplane(offset=h).circle(R).workplane(offset=L * 0.15).circle(r).loft())
    h += L * 0.15
    parts.append(wp.workplane(offset=h).circle(r).workplane(offset=L * 0.15).circle(R).loft())
    h += L * 0.15
    parts.append(wp.workplane(offset=h).circle(R).extrude(L - h))
    combined = parts[0]
    for p in parts[1:]:
        combined = combined.union(p)
    mesh = _cq_to_trimesh(combined, tolerance=0.0005)
    _export(mesh, "venturi.stl", name="venturi")
    _export_step(combined, "venturi.step")


def gen_mini_valve() -> None:
    """Valve-like fluid volume within budget: main bore with a concentric
    disc creating an annular gap + re-entrant rims (the concave class the
    historical valve exhibited, scaled to run <=25 min).

    body: cylinder r=0.03 h=0.08
    cut:  disc cylinder r=0.024 h=0.006 at z=0.037  (annular gap 6 mm)
          + two side ports (r=0.008) at z=0.01 / z=0.07 for asymmetry
    """
    body = cq.Workplane("XY").circle(0.03).extrude(0.08)
    disc = cq.Workplane("XY").circle(0.024).extrude(0.006).translate((0, 0, 0.037))
    port1 = cq.Workplane("XY").circle(0.008).extrude(0.05).translate((0.02, 0, 0.0))
    port2 = cq.Workplane("XY").circle(0.008).extrude(0.05).translate((-0.02, 0, 0.03))
    valve = body.cut(disc).cut(port1).cut(port2)
    mesh = _cq_to_trimesh(valve, tolerance=0.0005)
    _export(mesh, "mini_valve.stl", name="mini_valve")
    _export_step(valve, "mini_valve.step")


def gen_concave_bracket() -> None:
    """L-bracket with an internal concave fillet (re-entrant corner + fillet).

    fillet interno = vertical cylinder unioned into the inner corner
    (deterministic; no fragile cadquery edge selection).
    """
    base = cq.Workplane("XY").box(0.08, 0.04, 0.012)
    upright = cq.Workplane("XY").box(0.012, 0.04, 0.06).translate((-0.034, 0, 0.03))
    body = base.union(upright)
    fillet = cq.Workplane("XY").circle(0.004).extrude(0.06).translate((-0.028, -0.018, 0.006))
    mesh = _cq_to_trimesh(body.union(fillet), tolerance=0.0005)
    _export(mesh, "concave_bracket.stl", name="concave_bracket")


def gen_dirty_cad() -> None:
    """0.06 cube with a circular hole punched through the top face
    (OPEN surface on purpose — terrain C, declared snappy-hostile)."""
    mesh = _cq_to_trimesh(cq.Workplane("XY").box(0.06, 0.06, 0.06))
    # drop faces whose centre lies inside a circle on the top face
    z_max = mesh.vertices[:, 2].max()
    centres = mesh.triangles_center
    on_top = np.isclose(centres[:, 2], z_max, atol=1e-6)
    radial = np.hypot(centres[:, 0], centres[:, 1])
    hole = on_top & (radial < 0.015)
    mesh = trimesh.Trimesh(
        vertices=mesh.vertices,
        faces=mesh.faces[~hole],
        process=False,
    )
    _export(mesh, "dirty_cad.stl", name="dirty_cad")


def gen_multi_inlet() -> None:
    """Manifold: main header (r=0.012, L=0.1 along X) + 3 branch inlets
    (r=0.006, L=0.03 along Z) + 1 outlet stub (r=0.010, L=0.02 along X).

    Fixed 2026-10-06: the header/outlet used Workplane("XZ") (extruded along
    -Y) and the branches sat at x=-0.03/0/0.03 — all five solids were
    DISJOINT (trimesh body_count=5), which is why GMSH reported "some
    volumes of the BRep were not found" and HXT failed.  "YZ" extrudes
    along +X; the branches now sit inside the header span x∈[0, 0.1].
    """
    header = cq.Workplane("YZ").circle(0.012).extrude(0.1)
    b1 = cq.Workplane("XY").circle(0.006).extrude(0.03).translate((0.02, 0, 0.006))
    b2 = cq.Workplane("XY").circle(0.006).extrude(0.03).translate((0.05, 0, 0.006))
    b3 = cq.Workplane("XY").circle(0.006).extrude(0.03).translate((0.08, 0, 0.006))
    outlet = cq.Workplane("YZ").circle(0.010).extrude(0.02).translate((0.1, 0, 0))
    body = header.union(b1).union(b2).union(b3).union(outlet)
    mesh = _cq_to_trimesh(body, tolerance=0.0005)
    mesh.merge_vertices()
    assert mesh.body_count == 1, f"multi_inlet union left {mesh.body_count} disjoint bodies"
    _export(mesh, "multi_inlet.stl", name="multi_inlet")
    _export_step(body, "multi_inlet.step")


def main() -> int:
    print("Generating comparison-corpus STLs")
    gen_cylinder()
    gen_cube_duct()
    gen_groove1()
    gen_slot1()
    gen_venturi()
    gen_mini_valve()
    gen_concave_bracket()
    gen_dirty_cad()
    gen_multi_inlet()
    print("Done - 9 geometries generated (thin_gap.stl pre-exists).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
