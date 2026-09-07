"""FASE 1 gate — partial-patch BL verified with REAL checkMesh.

BL only on the wall patch(es) of two geometries with named inlet/outlet,
then checkMesh (WSL):

  A. cylinder (trimesh -> GMSH -> gmshToFoam): patches renamed by geometry
     into inlet/outlet/wall; BL on wall only.  Gate: checkMesh Mesh OK and
     n_prism_cells == n_layers x wall_faces (derived from the input mesh,
     never hardcoded).
  B. cube duct (in-memory tet, patches inlet/outlet/wall): BL on wall only.
     Gate: checkMesh Mesh OK and the same prism-count invariant.

Run:  python tools/bench_bl_poly_partial.py [--n-layers 3] [--skip-wsl]
"""
from __future__ import annotations

import argparse
import io
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import numpy as np  # noqa: E402

from cfmesh_autogui.config import OFConfig  # noqa: E402
from cfmesh_autogui.core.bl_poly import PolyBoundaryLayerEngine  # noqa: E402
from cfmesh_autogui.core import foam_mesh_io as fio  # noqa: E402
from cfmesh_autogui.core.tet_poly_dual import TetPolyDualConverter  # noqa: E402

_WORK = Path(os.environ.get("CFMESH_WORK", "C:/polybench2"))
CASE_A = _WORK / "bl_partial_cylinder"
CASE_B = _WORK / "bl_partial_cube"
NL = 3


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _checkmesh(cfg: OFConfig, case: Path) -> dict:
    linux = cfg._quoted_linux_path(case)
    env_q = shlex.quote(cfg.env_script)
    r = subprocess.run(
        cfg._build_wsl_cmd(
            f"source {env_q} 2>/dev/null; cd {linux} && checkMesh 2>&1"
        ),
        capture_output=True, text=True, timeout=900,
    )
    text = r.stdout + r.stderr

    def _num(pat):
        m = re.search(pat, text)
        return float(m.group(1).rstrip(".")) if m else 0.0

    def _int(pat):
        m = re.search(pat, text)
        return int(m.group(1).replace(",", "")) if m else 0

    return {
        "cells": _int(r"cells:\s+(\d+)"),
        "hexahedra": _int(r"hexahedra:\s+(\d+)"),
        "polyhedra": _int(r"polyhedra:\s+(\d+)"),
        "max_skewness": _num(r"Max skewness\s*[:=]\s*([\d.]+)"),
        "max_non_orth": _num(r"non-orthogonality\s+Max:\s*([\d.]+)"),
        "max_aspect_ratio": _num(r"Max aspect ratio\s*[:=]\s*([\d.]+)"),
        "total_volume": _num(r"Total volume = ([\d.eE+-]+)"),
        "wrong_oriented": _int(r"incorrectly oriented"),
        "passed": "Mesh OK." in text,
        "raw_tail": text[-1400:],
    }


def _run_bl_wall(case: Path, n_layers: int, log) -> tuple[bool, dict]:
    """BL on wall-type/wall-named patches only; returns (ok, metrics)."""
    from cfmesh_autogui.core.boundary_reader import parse_boundary

    patches = parse_boundary(case / "constant" / "polyMesh" / "boundary")
    walls = [p.name for p in patches
             if p.patch_type == "wall" or "wall" in p.name.lower()]
    if not walls:
        return False, {"error": f"no wall patches in {case}"}
    n_wall_faces = sum(p.n_faces for p in patches if p.name in walls)
    t0 = time.monotonic()
    res = PolyBoundaryLayerEngine(case, log=log).run(
        n_layers=n_layers, first_height=0.005, growth_rate=1.2,
        patch_names=walls, apply_to_all=False,
    )
    dt = time.monotonic() - t0
    metrics = {
        "wall_patches": res.wall_patches,
        "wall_faces_in": n_wall_faces,
        "n_prism_cells": res.n_prism_cells,
        "expected_prisms": n_layers * n_wall_faces,
        "prism_count_ok": res.n_prism_cells == n_layers * n_wall_faces,
        "n_terminator_faces": res.stats.get("n_terminator_faces", 0),
        "total_thickness": res.total_thickness,
        "bl_s": round(dt, 2),
        "errors": res.errors,
    }
    return res.success, metrics


# ---------------------------------------------------------------------------
# case A: cylinder with patches renamed inlet/outlet/wall
# ---------------------------------------------------------------------------

def build_cylinder(cfg: OFConfig) -> Path:
    import trimesh

    from cfmesh_autogui.core.gmsh_subprocess import (
        run_gmsh_to_foam, run_gmsh_volume, write_case_skeleton,
    )

    shutil.rmtree(CASE_A, ignore_errors=True)
    for d in ("constant", "system"):
        (CASE_A / d).mkdir(parents=True, exist_ok=True)
    stl = CASE_A / "cylinder.stl"
    cyl = trimesh.creation.cylinder(radius=0.5, height=2.0, sections=48)
    cyl.export(stl)
    msh = CASE_A / "mesh.msh"
    run_gmsh_volume(stl, msh, detail="medium", user_lc=0.0, min_lc=0.0)
    write_case_skeleton(CASE_A)
    run_gmsh_to_foam(CASE_A, "mesh.msh")
    _rename_cylinder_patches(CASE_A)
    return CASE_A


def _rename_cylinder_patches(case: Path) -> None:
    """gmshToFoam names the cylinder surfaces surface_N; classify by
    geometry: flat caps (normal parallel to z) = inlet/outlet, curved side
    = wall — so the case has EXPLICIT named patches (the FASE 1 gate)."""
    points, faces, owner, neigh, patches = fio.read_polymesh(
        case / "constant" / "polyMesh")
    n_int = len(neigh)

    def classify(pi: int) -> str:
        s, k = patches[pi]["startFace"], patches[pi]["nFaces"]
        nz = 0.0
        area = 0.0
        for fi in range(s, s + k):
            f = faces[fi]
            p = points[f]
            c0 = p.mean(0)
            a = p - c0
            b = np.roll(p, -1, axis=0) - c0
            sf = 0.5 * np.cross(a, b).sum(0)
            nz += abs(sf[2])
            area += np.linalg.norm(sf)
        return "wall" if area and nz / area < 0.9 else "cap"

    caps = [pi for pi in range(len(patches)) if classify(pi) == "cap"]
    assert len(caps) >= 2, f"expected >=2 caps, found {caps}"
    cap_names = {pi: (["inlet", "outlet"] + [f"cap{i}" for i in range(len(caps) - 2)])
                 [k] for k, pi in enumerate(caps)}
    new_patches = []
    for pi, p in enumerate(patches):
        if pi in caps:
            nm, tp = cap_names[pi], "patch"
        else:
            nm, tp = "wall", "wall"
        new_patches.append({**p, "name": nm, "type": tp})
    fio.write_polymesh(
        case / "constant" / "polyMesh", points, faces,
        np.asarray(owner, dtype=np.int64), neigh, new_patches,
    )
    print(f"   renamed {len(caps)} caps -> inlet/outlet, side -> wall")


# ---------------------------------------------------------------------------
# case B: cube duct, in-memory tet with named patches
# ---------------------------------------------------------------------------

def build_cube_duct() -> Path:
    shutil.rmtree(CASE_B, ignore_errors=True)
    sys.path.insert(0, str(SRC.parent / "tests"))
    from test_tet_poly_dual import build_tet_case

    c = np.array([
        [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
    ], dtype=float)
    pts = np.vstack([c, [[0.5, 0.5, 0.5]]])
    faces2d = [
        [0, 1, 2, 3], [0, 4, 5, 1], [1, 5, 6, 2],
        [2, 6, 7, 3], [3, 7, 4, 0], [4, 7, 6, 5],
    ]
    tets = []
    for f in faces2d:
        for tri in ((f[0], f[1], f[2]), (f[0], f[2], f[3])):
            tets.append([tri[0], tri[1], tri[2], 8])
    case = CASE_B
    build_tet_case(case, pts, tets, boundary_patch="all")
    # split into inlet (x=0), outlet (x=1), wall (rest)
    poly = case / "constant" / "polyMesh"
    points, faces, owner, neigh, patches = fio.read_polymesh(poly)
    n_int = len(neigh)

    def which(bi: int) -> str:
        f = faces[n_int + bi]
        xs = points[f][:, 0]
        if abs(xs.max()) < 1e-9:
            return "inlet"
        if abs(xs.min() - 1.0) < 1e-9:
            return "outlet"
        return "wall"

    order = sorted(range(len(faces) - n_int), key=which)
    nf = {"inlet": 0, "outlet": 0, "wall": 0}
    for bi in order:
        nf[which(bi)] += 1
    faces2 = faces[:n_int] + [faces[n_int + bi] for bi in order]
    owner2 = np.concatenate([owner[:n_int],
                             np.array([owner[n_int + bi] for bi in order])])
    start = n_int
    new_patches = []
    for nm in ("inlet", "outlet", "wall"):
        new_patches.append({"name": nm, "type": "wall" if nm == "wall" else "patch",
                            "nFaces": nf[nm], "startFace": start})
        start += nf[nm]
    fio.write_polymesh(poly, points, faces2, owner2, neigh, new_patches)
    # checkMesh needs system/controlDict
    sysd = case / "system"
    sysd.mkdir(parents=True, exist_ok=True)
    (sysd / "controlDict").write_text(
        "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
        "    class       dictionary;\n    object      controlDict;\n}\n"
        "application     checkMesh;\nstartFrom       startTime;\n"
        "startTime       0;\nstopAt          endTime;\nendTime         1;\n"
        "deltaT          1;\nwriteControl    timeStep;\nwriteInterval   1;\n"
        "purgeWrite      0;\nwriteFormat     ascii;\nwritePrecision   8;\n"
        "timeFormat      general;\ntimePrecision   6;\n"
        "runTimeModifiable true;\n", encoding="ascii")
    (sysd / "fvSchemes").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\n"
        "ddtSchemes { default steadyState; }\n"
        "gradSchemes { default Gauss linear; }\n"
        "divSchemes { default Gauss linear; }\n"
        "laplacianSchemes { default Gauss linear corrected; }\n"
        "interpolationSchemes { default linear; }\n"
        "snGradSchemes { default corrected; }\n", encoding="ascii")
    (sysd / "fvSolution").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\n"
        "solvers { p { solver PCG; preconditioner DIC; tolerance 1e-6; relTol 0.1; } }\n",
        encoding="ascii")
    return case


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-layers", type=int, default=NL)
    ap.add_argument("--skip-wsl", action="store_true",
                    help="engine only, no checkMesh")
    args = ap.parse_args()

    cfg = OFConfig()
    if not cfg.validate():
        print("WSL/OpenFOAM not available — cannot run checkMesh.")
        return 2

    def log(m): print(f"   {m}")

    results = {}

    # ---- case A: cylinder -------------------------------------------------
    print(f"\n=== A. cylinder, BL on wall only, {args.n_layers} layers ===")
    t0 = time.monotonic()
    ca = build_cylinder(cfg)
    print(f"   cylinder built ({time.monotonic() - t0:.1f}s)")
    t0 = time.monotonic()
    dres = TetPolyDualConverter(ca, log=log).run()
    print(f"   dual: {dres.n_cells_after:,} cells ({time.monotonic() - t0:.1f}s)")
    ok, m = _run_bl_wall(ca, args.n_layers, log)
    print(f"   BL wall-only: success={ok} prism={m['n_prism_cells']} "
          f"(expected {m['expected_prisms']}) term={m['n_terminator_faces']} "
          f"thick={m['total_thickness']:.4g} ({m['bl_s']}s)")
    if not ok:
        print("   BL FAILED:", m["errors"])
        results["A"] = {"bl_ok": False, "errors": m["errors"]}
    else:
        cm = _checkmesh(cfg, ca) if not args.skip_wsl else {}
        print(f"   checkMesh: cells={cm.get('cells')} prisms/hex={cm.get('hexahedra')} "
              f"poly={cm.get('polyhedra')} skew={cm.get('max_skewness'):.3f} "
              f"NOmax={cm.get('max_non_orth'):.1f} Mesh OK={cm.get('passed')}")
        results["A"] = {"bl_ok": True, "prism_ok": m["prism_count_ok"],
                        "checkmesh_ok": cm.get("passed"), **cm, **m}

    # ---- case B: cube duct ------------------------------------------------
    print(f"\n=== B. cube duct, BL on wall only, {args.n_layers} layers ===")
    t0 = time.monotonic()
    cb = build_cube_duct()
    dres = TetPolyDualConverter(cb, log=log).run()
    print(f"   dual: {dres.n_cells_after:,} cells ({time.monotonic() - t0:.1f}s)")
    ok, m = _run_bl_wall(cb, args.n_layers, log)
    print(f"   BL wall-only: success={ok} prism={m['n_prism_cells']} "
          f"(expected {m['expected_prisms']}) term={m['n_terminator_faces']} "
          f"thick={m['total_thickness']:.4g} ({m['bl_s']}s)")
    if not ok:
        print("   BL FAILED:", m["errors"])
        results["B"] = {"bl_ok": False, "errors": m["errors"]}
    else:
        cm = _checkmesh(cfg, cb) if not args.skip_wsl else {}
        print(f"   checkMesh: cells={cm.get('cells')} prisms/hex={cm.get('hexahedra')} "
              f"poly={cm.get('polyhedra')} skew={cm.get('max_skewness'):.3f} "
              f"NOmax={cm.get('max_non_orth'):.1f} Mesh OK={cm.get('passed')}")
        results["B"] = {"bl_ok": True, "prism_ok": m["prism_count_ok"],
                        "checkmesh_ok": cm.get("passed"), **cm, **m}

    # ---- verdict ----------------------------------------------------------
    print("\n=== FASE 1 GATE ===")
    gate_ok = True
    for k in ("A", "B"):
        r = results.get(k, {})
        bl = r.get("bl_ok")
        po = r.get("prism_ok")
        cmo = r.get("checkmesh_ok")
        line = f"{k}: BL={bl} prism_count_ok={po} checkMesh_Mesh_OK={cmo}"
        print(line)
        gate_ok = gate_ok and bool(bl and po and (cmo if not args.skip_wsl else True))
    print("VERDICT:", "PASS — partial-patch BL valid on 2 geometries"
          if gate_ok else "FAIL")
    return 0 if gate_ok else 1


if __name__ == "__main__":
    sys.exit(main())
