"""FASE 4/5 — varianti dell'ordine/modo di terminazione locale del BL.

Tre modalita' sullo stesso dual, per geometria:
  - THICK (engine legacy, `local_termination=True`): scale 1.0 -> 0.1,
    esclusioni TRASPORTATE tra le scale;
  - THIN (monkey-patch): scale invertite 0.1 -> 1.0, carry — misura della
    scoperta FASE 3/4, non e' una modalita' del motore;
  - DECOUPLED (engine, `local_termination="decoupled"`): prova la scala
    RICHIESTA per prima; scendendo di scala il set di esclusioni viene
    AZZERATO cosi' una scala grossa fallita non avvelena le piu' sottili.

Geometrie di default: cilindro GMSH, cubo duct, groove1, slot1 (come
FASE 3/4). Con `--valve` aggiunge la fixture valvola
(tests/fixtures/valve_dual.npz, protocollo n_layers=2, h1=1e-5,
dual_convexity) — attenzione, il giro completo sulla valvola costa
~25 min per modalita'.

Stesso protocollo di FASE 3/4: checkMesh REALE dopo ogni run riuscito.

Uso:
  python tools/bench_bl_thinfirst.py                     # 4 geometrie, 3 modi
  python tools/bench_bl_thinfirst.py --modes decoupled   # solo decoupled
  python tools/bench_bl_thinfirst.py --valve --modes decoupled
"""
from __future__ import annotations

import argparse
import io
import json
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
sys.path.insert(0, str(SRC.parent / "tests"))

import types  # noqa: E402

import numpy as np  # noqa: E402

import polyfoammesh.core.foam_mesh_io as fio  # noqa: E402
from polyfoammesh.config import OFConfig  # noqa: E402
from polyfoammesh.core.bl_poly import PolyBoundaryLayerEngine  # noqa: E402
from polyfoammesh.core.tet_poly_dual import TetPolyDualConverter  # noqa: E402

WORK = Path("C:/polybench2/fase4_thinfirst")
N_LAYERS = 3
H1 = 0.005

SYS_CTRL = (
    "FoamFile { version 2.0; format ascii; class dictionary; "
    "object controlDict; }\n"
    "application checkMesh;\nstartFrom startTime;\nstartTime 0;\n"
    "stopAt endTime;\nendTime 1;\ndeltaT 1;\nwriteControl timeStep;\n"
    "writeInterval 1;\npurgeWrite 0;\nwriteFormat ascii;\n"
    "writePrecision 16;\nwriteCompression off;\ntimeFormat general;\n"
    "timePrecision 6;\nrunTimeModifiable false;\n"
)
SYS_SCHEMES = (
    "FoamFile { version 2.0; format ascii; class dictionary; "
    "object fvSchemes; }\nddtSchemes { default steadyState; }\n"
    "gradSchemes { default Gauss linear; }\n"
    "divSchemes { default none; }\n"
    "laplacianSchemes { default Gauss linear corrected; }\n"
    "interpolationSchemes { default linear; }\n"
    "snGradSchemes { default corrected; }\n"
)
SYS_SOLUTION = (
    "FoamFile { version 2.0; format ascii; class dictionary; "
    "object fvSolution; }\nsolvers { }\nSIMPLE { }\n"
)
SCALES_THIN = ((0.1, 0.05), (0.2, 0.1), (0.35, 0.2),
               (0.6, 0.35), (1.0, 0.5))
SCALES_THICK = ((1.0, 0.5), (0.6, 0.35), (0.35, 0.2),
                (0.2, 0.1), (0.1, 0.05))


def write_skeleton(case: Path) -> None:
    (case / "system").mkdir(parents=True, exist_ok=True)
    (case / "system" / "controlDict").write_text(SYS_CTRL, encoding="ascii")
    (case / "system" / "fvSchemes").write_text(SYS_SCHEMES, encoding="ascii")
    (case / "system" / "fvSolution").write_text(SYS_SOLUTION, encoding="ascii")


def checkmesh(cfg: OFConfig, case: Path) -> dict:
    r = subprocess.run(
        cfg._build_wsl_cmd(
            f"source {shlex.quote(cfg.env_script)} 2>/dev/null; "
            f"cd {cfg._quoted_linux_path(case)} && checkMesh 2>&1"
        ),
        capture_output=True, text=True, timeout=1800,
    )
    text = r.stdout + r.stderr

    def _int(pat):
        m = re.search(pat, text)
        return int(m.group(1).replace(",", "")) if m else 0

    def _num(pat):
        m = re.search(pat, text)
        return float(m.group(1).rstrip(".")) if m else 0.0

    return {
        "cells": _int(r"cells:\s+(\d+)"),
        "wrong_oriented": _int(r"(\d+)\s+faces are incorrectly oriented"),
        "nonortho_errors": _int(r"non-orthogonality errors:\s*(\d+)"),
        "max_non_orth": _num(r"non-orthogonality\s+Max:\s*([\d.]+)"),
        "max_skewness": _num(r"Max skewness\s*[:=]\s*([\d.]+)"),
        "max_aspect_ratio": _num(r"Max aspect ratio\s*[:=]\s*([\d.]+)"),
        "neg_volume_cells": _int(r"negative volume cells:\s*(\d+)"),
        "mesh_ok": "Mesh OK." in text,
        "failed_checks": _int(r"Failed\s+(\d+)\s+mesh checks"),
    }


# ---------------------------------------------------------------------------
# Builders (duplicati in sintesi — i gate della lane restano in
# tools/bench_bl_poly_partial.py; qui servono casi aggiuntivi groove/slot)
# ---------------------------------------------------------------------------

def build_cylinder(case: Path) -> Path:
    import trimesh  # noqa: E402
    from polyfoammesh.core.gmsh_subprocess import (  # noqa: E402
        run_gmsh_to_foam, run_gmsh_volume, write_case_skeleton,
    )
    shutil.rmtree(case, ignore_errors=True)
    for d in ("constant", "system"):
        (case / d).mkdir(parents=True, exist_ok=True)
    stl = case / "cylinder.stl"
    cyl = trimesh.creation.cylinder(radius=0.5, height=2.0, sections=48)
    cyl.export(stl)
    msh = case / "mesh.msh"
    run_gmsh_volume(stl, msh, detail="medium", user_lc=0.0, min_lc=0.0)
    write_case_skeleton(case)
    run_gmsh_to_foam(case, "mesh.msh")
    points, faces, owner, neigh, patches = fio.read_polymesh(
        case / "constant" / "polyMesh")
    n_int = len(neigh)

    def classify(pi):
        s, k = patches[pi]["startFace"], patches[pi]["nFaces"]
        nz, area = 0.0, 0.0
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
    return case


def build_cube_duct(case: Path) -> Path:
    shutil.rmtree(case, ignore_errors=True)
    from test_tet_poly_dual import build_tet_case  # noqa: E402
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
    build_tet_case(case, pts, tets, boundary_patch="all")
    poly = case / "constant" / "polyMesh"
    points, faces, owner, neigh, patches = fio.read_polymesh(poly)
    n_int = len(neigh)

    def which(bi):
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
    return case


def case_from_tet_backup(src: Path, case: Path) -> Path:
    shutil.rmtree(case, ignore_errors=True)
    (case / "constant").mkdir(parents=True)
    shutil.copytree(src, case / "constant" / "polyMesh")
    write_skeleton(case)
    return case


def dual_convert(case: Path) -> dict:
    t0 = time.monotonic()
    dres = TetPolyDualConverter(case, log=lambda m: None).run()
    return {"dual_ok": bool(dres.success), "dual_cells": int(dres.n_cells_after),
            "dual_s": round(time.monotonic() - t0, 1)}


def copy_dual(src_case: Path, dst_case: Path) -> Path:
    shutil.rmtree(dst_case, ignore_errors=True)
    (dst_case / "constant").mkdir(parents=True)
    shutil.copytree(src_case / "constant" / "polyMesh",
                    dst_case / "constant" / "polyMesh")
    write_skeleton(dst_case)
    return dst_case


# ---------------------------------------------------------------------------
# Valvola: fixture dual (stesso input dei run FASE 2/3)
# ---------------------------------------------------------------------------

VALVE_FIX = SRC.parent / "tests" / "fixtures" / "valve_dual.npz"
VALVE_N_LAYERS = 2
VALVE_H1 = 1e-5


def build_valve_case(case: Path) -> tuple[Path, dict]:
    """Scrive la fixture valvola come caso polyMesh (patch unica 'wall')."""
    d = np.load(VALVE_FIX)
    points = d["points"].astype(np.float64)
    n_int = int(d["n_int"])
    fv = d["face_verts"]
    fs = d["face_sizes"].tolist()
    fo = d["face_offsets"].tolist()
    faces = [fv[fo[i]:fo[i] + fs[i]].tolist() for i in range(len(fs))]
    owner = d["owner"].astype(np.int64)
    neigh = d["neighbour"].astype(np.int64)
    patches = [{"name": "wall", "type": "patch",
                "nFaces": len(faces) - n_int, "startFace": n_int}]
    shutil.rmtree(case, ignore_errors=True)
    (case / "constant").mkdir(parents=True)
    fio.write_polymesh(case / "constant" / "polyMesh", points, faces,
                       owner, neigh, patches)
    write_skeleton(case)
    n_cells = int(max(owner.max(), neigh.max())) + 1
    return case, {"dual_ok": True, "dual_cells": n_cells, "dual_s": 0.0}


# ---------------------------------------------------------------------------
# Variante thin-first di _run_local_termination
# ---------------------------------------------------------------------------

def _make_run_with_scales(SCALES, label):
    """Restituisce una funzione che sostituisce _run_local_termination con
    la stessa logica ma la tupla SCALES al posto di quella hardcoded.
    'self' arriva a runtime via types.MethodType.
    """
    def _run_local_termination(self, res, points, faces, owner, neighbour,
                               patches, n_int, sel, drop_bnd, pyr_before,
                               total_vol0, max_core_volume_ratio,
                               concavity_criterion, growth, poly,
                               decoupled: bool = False):
        excluded = set()
        max_per_scale = 3
        for scale, clamp in SCALES:
            for _round in range(max_per_scale):
                if self._cancel():
                    res.errors.append("cancelled")
                    return res
                sel_cur = [b for b in sel if b not in excluded]
                if len(sel_cur) < 16:
                    res.errors.append(
                        "local termination excluded too many faces "
                        f"({len(excluded)} of {len(sel)})"
                    )
                    return res
                pre = self._build_precompute(
                    points, faces, owner, neighbour, patches, n_int,
                    sel_cur, drop_bnd,
                    concavity_criterion=concavity_criterion,
                )
                for k, v in pre.get("dual_stats", {}).items():
                    res.stats[k] = v
                try:
                    built = self._build(
                        points, faces, owner, neighbour, patches, n_int,
                        sel_cur, res.n_layers, res.first_height * scale,
                        growth, clamp, pyr_before=pyr_before,
                        drop_bnd=drop_bnd, pre=pre, zero_concave=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._log(f"[bl] local attempt scale={scale}: {exc}")
                    res.warnings.append(
                        f"local attempt at scale={scale} failed: {exc}"
                    )
                    break
                self._log(
                    f"[bl] local termination attempt scale={scale} "
                    f"round={_round} excluded={len(excluded)}"
                )
                ok, msg = self._validate(built, total_vol0,
                                         max_core_volume_ratio)
                if ok:
                    res.stats["local_excluded_faces"] = len(excluded)
                    res.stats["scale_order"] = label
                    res.warnings.append(
                        f"local termination: excluded {len(excluded)} of "
                        f"{len(sel)} wall faces (concave/invalid prisms)"
                    )
                    return self._accept_built(res, built, scale, clamp, poly)
                res.warnings.append(
                    f"local validation failed at scale={scale} "
                    f"round={_round}: {msg}"
                )
                self._log(
                    f"[bl] local validation failed at scale={scale} "
                    f"round={_round}: {msg}"
                )
                added = self._collect_exclusions(built, sel_cur, owner, n_int)
                if not added:
                    break
                excluded |= added
        res.stats["scale_order"] = label
        res.errors.append(
            "could not insert a valid boundary layer with local "
            f"termination ({len(excluded)} faces excluded); mesh left "
            "unchanged"
        )
        return res
    return _run_local_termination


def run_case(case: Path, mode: str, n_layers: int, h1: float) -> dict:
    """Run one local-termination mode on a case copy.

    mode: "thick"        = engine legacy carry order 1.0 -> 0.1
          "thin"         = monkey-patched reversed order 0.1 -> 1.0 (carry)
          "decoupled"    = engine mode: requested scale first, exclusions
                           reset when descending
          "early_exit"   = engine mode: decoupled + early_exit_intermediates
                           (skip 0.6/0.35/0.2 if 1.0 round 0 fails)
    """
    eng = PolyBoundaryLayerEngine(case, log=lambda m: None)
    if mode == "thin":
        eng._run_local_termination = types.MethodType(
            _make_run_with_scales(SCALES_THIN, "thin"), eng)
        lt: bool | str = True
    elif mode == "thick":
        lt = True
    elif mode == "decoupled":
        lt = "decoupled"
    elif mode == "early_exit":
        lt = "decoupled"
    else:
        raise ValueError(f"unknown mode {mode!r}")
    early_exit = mode == "early_exit"
    t0 = time.monotonic()
    res = eng.run(
        n_layers=n_layers, first_height=h1, growth_rate=1.2,
        apply_to_all=True, local_termination=lt,
        concavity_criterion="dual_convexity",
        early_exit_intermediates=early_exit,
    )
    dt = time.monotonic() - t0
    return {
        "success": bool(res.success),
        "n_prism_cells": int(res.n_prism_cells),
        "time_s": round(dt, 1),
        "scale": res.stats.get("scale"),
        "mode": mode,
        "lt_mode": res.stats.get("local_termination_mode"),
        "early_exit": res.stats.get("local_termination_early_exit_intermediates"),
        "excluded": res.stats.get("local_excluded_faces"),
        "min_cell_volume": res.stats.get("min_cell_volume"),
        "total_volume": res.stats.get("total_volume"),
        "warnings": list(res.warnings),
        "errors": list(res.errors),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-wsl", action="store_true",
                    help="engine only, no checkMesh")
    ap.add_argument("--valve", action="store_true",
                    help="aggiungi la fixture valvola (protocollo n_layers=2, "
                         "h1=1e-5; ~25 min per modalita')")
    ap.add_argument("--modes", default="thick,thin,decoupled",
                    help="modi da eseguire, separati da virgola "
                         "(thick, thin, decoupled, early_exit)")
    args = ap.parse_args()
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    for m in modes:
        if m not in ("thick", "thin", "decoupled", "early_exit"):
            print(f"modo sconosciuto: {m!r}")
            return 2

    cfg = OFConfig()
    if not cfg.validate() and not args.skip_wsl:
        print("WSL/OpenFOAM non disponibile — usa --skip-wsl per testare solo l'engine")
        return 2
    WORK.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}
    out_name = "fase5_decoupled_result.json"

    # Costruisci i dual una volta sola; ogni run parte da una copia fresca.
    print("=== costruzione dual ===")
    cases = []  # (name, build_case, dual_info, n_layers, h1)
    c = build_cylinder(WORK / "build_cyl")
    d = dual_convert(c); print(f"[cyl] dual: {d}")
    cases.append(("cylinder", c, d, N_LAYERS, H1))
    c = build_cube_duct(WORK / "build_cube")
    d = dual_convert(c); print(f"[cube] dual: {d}")
    cases.append(("cube_duct", c, d, N_LAYERS, H1))
    for name, src in (
        ("groove1", Path(r"C:/polybench/groove1/constant/polyMesh_tet_backup")),
        ("slot1", Path(r"C:/polybench/slot1/constant/polyMesh_tet_backup")),
    ):
        if not src.exists():
            print(f"[{name}] backup mancante"); continue
        c = case_from_tet_backup(src, WORK / f"build_{name}")
        d = dual_convert(c); print(f"[{name}] dual: {d}")
        cases.append((name, c, d, N_LAYERS, H1))
    if args.valve:
        c, d = build_valve_case(WORK / "build_valve")
        print(f"[valve] fixture: {d}")
        cases.append(("valve", c, d, VALVE_N_LAYERS, VALVE_H1))

    for name, build_case, dual_info, n_layers, h1 in cases:
        print(f"\n===== {name} =====")
        entry: dict = {"dual": dual_info}
        results[name] = entry
        for mode in modes:
            case_dir = copy_dual(build_case, WORK / f"{name}_{mode}")
            r = run_case(case_dir, mode, n_layers, h1)
            entry[mode] = r
            print(f"  {mode}: success={r['success']} prisms={r['n_prism_cells']} "
                  f"excl={r['excluded']} scale={r['scale']} {r['time_s']}s "
                  f"err={r['errors'][:1]}")
            if r["success"] and not args.skip_wsl:
                cm = checkmesh(cfg, case_dir)
                entry[f"{mode}_checkmesh"] = cm
                print(f"    checkMesh: {json.dumps(cm)}")

    out = WORK / out_name
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nrisultati in {out}")

    print("\n=== RIEPILOGO ===")
    for name, e in results.items():
        parts = []
        for mode in modes:
            r = e.get(mode, {})
            cmo = e.get(f"{mode}_checkmesh", {}).get("mesh_ok")
            parts.append(
                f"{mode.upper()} ok={r.get('success')} "
                f"prisms={r.get('n_prism_cells')} scale={r.get('scale')} "
                f"excl={r.get('excluded')} {r.get('time_s')}s meshOK={cmo}")
        print(f"{name}: " + " | ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
