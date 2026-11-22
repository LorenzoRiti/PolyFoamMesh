"""Parametric geometry FAMILIES for universality testing.

Why: the fixed corpus (a few dozen shapes, many of them the very parts used to
tune the code) cannot tell whether a fix is general or fitted.  Here every
family is a parametric builder; ``build(family, seed)`` draws its parameters
(including an overall size spanning three decades (1 cm .. 10 m, built in mm), so scale invariance is
tested too) and returns a CAD solid that is the FLUID volume (or the OBJECT for
external-aero families), plus the parameters used.

Deterministic: the same (family, seed) always gives the same shape.
Split: seeds 0-99 are the TUNING split, seeds 1000+ the HELD-OUT split.  A
threshold may only be tuned on the first and must be judged on the second.

Usage:  python benchmarks/families.py OUTDIR [--seeds 0-9] [--families a,b]
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

import cadquery as cq


def _scale(rng: random.Random) -> float:
    """Overall size in CAD units (MILLIMETRES: CadQuery/STEP default),
    log-uniform over 1 cm .. 10 m of physical size, i.e. 10 .. 10000 mm.
    (A first version used metres as if they were mm: every shape was 1000x
    too small, 10 um .. 10 mm, and the first measurements were invalid.)"""
    return 1000.0 * 10 ** rng.uniform(-2.0, 1.0)


def _pipe(d: float, length: float) -> cq.Workplane:
    return cq.Workplane("XY").circle(d / 2).extrude(length)


def straight_pipe(rng):
    s = _scale(rng)
    ld = rng.uniform(2.0, 60.0)
    return _pipe(s, s * ld), {"D": s, "L_over_D": ld}


def bent_pipe(rng):
    s = _scale(rng)
    rb = rng.uniform(1.5, 5.0) * s
    ang = rng.uniform(30.0, 180.0)
    lead = s * rng.uniform(1.0, 6.0)
    path = (cq.Workplane("XZ").moveTo(0, 0).lineTo(0, lead)
            .radiusArc((rb * (1 - math.cos(math.radians(ang))) * (-1 if ang < 180 else 1),
                        lead + rb * math.sin(math.radians(ang))), -rb))
    prof = cq.Workplane("XY").circle(s / 2)
    return prof.sweep(path), {"D": s, "R_bend": rb / s, "angle": ang, "lead": lead / s}


def t_junction(rng):
    s = _scale(rng)
    ratio = rng.uniform(0.3, 1.0)
    main = _pipe(s, s * rng.uniform(4, 12)).translate((0, 0, 0))
    branch_len = s * rng.uniform(2.0, 6.0)
    branch = (cq.Workplane("XZ").circle(s * ratio / 2).extrude(-branch_len)
              .translate((0, 0, main.val().BoundingBox().zmax / 2)))
    return main.union(branch), {"D": s, "branch_ratio": ratio}


def venturi(rng):
    s = _scale(rng)
    beta = rng.uniform(0.3, 0.8)
    r0, r1 = s / 2, s * beta / 2
    l_in, l_conv = s * rng.uniform(1, 4), s * rng.uniform(1.5, 4)
    l_thr, l_div = s * rng.uniform(0.3, 2.0), s * rng.uniform(3, 8)
    pts = [(0, 0), (r0, 0), (r0, l_in), (r1, l_in + l_conv),
           (r1, l_in + l_conv + l_thr), (r0, l_in + l_conv + l_thr + l_div),
           (r0, l_in + l_conv + l_thr + l_div + s), (0, l_in + l_conv + l_thr + l_div + s)]
    solid = cq.Workplane("XY").polyline(pts).close().revolve(360, (0, 0, 0), (0, 1, 0))
    return solid, {"D": s, "beta": beta}


def orifice(rng):
    s = _scale(rng)
    beta = rng.uniform(0.3, 0.8)
    t = s * rng.uniform(0.02, 0.15)
    ln = s * rng.uniform(3, 10)
    pipe = _pipe(s, ln)
    plate = (cq.Workplane("XY").circle(s / 2).circle(s * beta / 2)
             .extrude(t).translate((0, 0, ln / 2)))
    return pipe.cut(plate), {"D": s, "beta": beta, "plate_t": t / s}


def sudden_expansion(rng):
    s = _scale(rng)
    ratio = rng.uniform(1.5, 4.0)
    a = _pipe(s, s * rng.uniform(3, 8))
    b = (cq.Workplane("XY").circle(s * ratio / 2).extrude(s * rng.uniform(5, 15))
         .translate((0, 0, a.val().BoundingBox().zmax)))
    return a.union(b), {"D": s, "expansion": ratio}


def block_with_holes(rng):
    s = _scale(rng)
    n = rng.randint(1, 6)
    blk = cq.Workplane("XY").box(s, s * rng.uniform(0.5, 1.5), s * rng.uniform(0.5, 1.5))
    bb = blk.val().BoundingBox()
    out = blk
    for _ in range(n):
        d = s * rng.uniform(0.05, 0.25)
        x = rng.uniform(bb.xmin + d, bb.xmax - d)
        y = rng.uniform(bb.ymin + d, bb.ymax - d)
        out = out.cut(cq.Workplane("XY").center(x, y).circle(d / 2).extrude(s * 3, both=True))
    return out, {"size": s, "n_holes": n}


def thin_plate_slots(rng):
    s = _scale(rng)
    tr = rng.uniform(0.005, 0.05)
    blk = cq.Workplane("XY").box(s, s * rng.uniform(0.5, 1.5), s * tr)
    n = rng.randint(1, 4)
    for i in range(n):
        w = s * rng.uniform(0.01, 0.05)
        blk = blk.cut(cq.Workplane("XY").box(s * 0.6, w, s * 3)
                      .translate((0, (i - (n - 1) / 2) * s * 0.2, 0)))
    return blk, {"size": s, "thickness_ratio": tr, "n_slots": n}


def l_bracket(rng):
    s = _scale(rng)
    t = s * rng.uniform(0.1, 0.4)
    a = cq.Workplane("XY").box(s, s * 0.6, t, centered=(False, True, False))
    b = cq.Workplane("XY").box(t, s * 0.6, s, centered=(False, True, False))
    return a.union(b), {"size": s, "t_ratio": t / s}


def manifold(rng):
    s = _scale(rng)
    n = rng.randint(2, 4)
    ln = s * (n * 2.5 + 2)
    main = _pipe(s, ln)
    out = main
    for i in range(n):
        d = s * rng.uniform(0.3, 0.7)
        z = (i + 1) * ln / (n + 1)
        out = out.union(cq.Workplane("XZ").center(0, z).circle(d / 2).extrude(-s * rng.uniform(2, 4)))
    return out, {"D": s, "n_branches": n}


def diffuser(rng):
    s = _scale(rng)
    ratio = rng.uniform(1.5, 3.0)
    ln = s * rng.uniform(2, 6)
    pts = [(0, 0), (s / 2, 0), (s * ratio / 2, ln), (0, ln)]
    return cq.Workplane("XY").polyline(pts).close().revolve(360, (0, 0, 0), (0, 1, 0)), {
        "D": s, "ratio": ratio}


def filleted_box(rng):
    s = _scale(rng)
    blk = cq.Workplane("XY").box(s, s * rng.uniform(0.4, 1.2), s * rng.uniform(0.3, 1.0))
    return blk.edges().fillet(s * rng.uniform(0.02, 0.2)), {"size": s}


def airfoil(rng):
    """NACA 4-digit (symmetric/cambered) extruded: an OBJECT for tunnel tests."""
    c = _scale(rng)
    t = rng.uniform(0.06, 0.24)
    m = rng.choice([0.0, 0.02, 0.04])
    p = 0.4
    n = 40
    xs = [(1 - math.cos(math.pi * i / n)) / 2 for i in range(n + 1)]

    def yt(x):
        return 5 * t * (0.2969 * math.sqrt(x) - 0.126 * x - 0.3516 * x ** 2
                        + 0.2843 * x ** 3 - 0.1036 * x ** 4)

    def yc(x):
        return (m / p ** 2) * (2 * p * x - x ** 2) if x < p else \
            (m / (1 - p) ** 2) * ((1 - 2 * p) + 2 * p * x - x ** 2)

    up = [(x * c, (yc(x) + yt(x)) * c) for x in xs]
    lo = [(x * c, (yc(x) - yt(x)) * c) for x in xs[::-1]]
    pts = up + lo[1:-1]
    span = c * rng.uniform(0.3, 3.0)
    return cq.Workplane("XY").polyline(pts).close().extrude(span), {
        "chord": c, "thickness": t, "camber": m, "span_over_chord": span / c}


FAMILIES = {
    f.__name__: f for f in (
        straight_pipe, bent_pipe, t_junction, venturi, orifice, sudden_expansion,
        block_with_holes, thin_plate_slots, l_bracket, manifold, diffuser,
        filleted_box, airfoil,
    )
}


def build(family: str, seed: int):
    """Return (CadQuery workplane, params dict).  Deterministic in (family, seed)."""
    rng = random.Random(f"{family}:{seed}")
    solid, params = FAMILIES[family](rng)
    params = {"family": family, "seed": seed, **params}
    return solid, params


def _parse_seeds(txt: str) -> list[int]:
    if "-" in txt:
        a, b = txt.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in txt.split(",")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--seeds", default="0-4")
    ap.add_argument("--families", default=",".join(FAMILIES))
    a = ap.parse_args()
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for fam in a.families.split(","):
        for seed in _parse_seeds(a.seeds):
            try:
                solid, params = build(fam, seed)
                path = out / f"{fam}_{seed}.step"
                cq.exporters.export(solid, str(path))
                params["file"] = str(path)
                params["volume"] = float(solid.val().Volume())
                manifest.append(params)
            except Exception as exc:  # noqa: BLE001 - report, keep going
                manifest.append({"family": fam, "seed": seed, "error": str(exc)[:200]})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    bad = [m for m in manifest if "error" in m]
    print(f"{len(manifest) - len(bad)} built, {len(bad)} failed")
    for m in bad:
        print("  FAIL", m["family"], m["seed"], m["error"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
