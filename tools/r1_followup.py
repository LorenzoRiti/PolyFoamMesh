#!/usr/bin/env python3
"""R1 follow-ups on an existing tet case (no re-tet).

--mode dual: production dual -> if valid, BL 5L -> checkMesh.
--mode direct-lt: direct BL 5L with decoupled local termination
   (exclude invalid faces, build the rest) -> checkMesh.
Copies the case aside first; never mutates the input.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import argparse

ap = argparse.ArgumentParser()
ap.add_argument("--case", required=True)
ap.add_argument("--mode", choices=["dual", "direct-lt"], required=True)
ap.add_argument("--outdir", required=True)
a = ap.parse_args()

SRC = Path(a.case)
DST = Path(a.outdir)
WORK = DST / "case"
OUT = DST / "result.json"
out: dict = {"mode": a.mode, "stages": {}}


def stage(name: str):
    print(f"[r1fu] stage: {name}", flush=True)
    out["stages"][name] = {"t0": time.monotonic()}


def done(name: str, **kw):
    out["stages"][name].update(kw)
    out["stages"][name]["seconds"] = round(time.monotonic() - out["stages"][name].pop("t0"), 1)
    print(f"   -> {name}: {kw} ({out['stages'][name]['seconds']}s)", flush=True)


def save():
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")


def checkmesh(case: Path, tag: str):
    from polyfoammesh.config import OFConfig

    cmd = OFConfig().build_check_mesh_cmd(case)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    text = (proc.stdout or "") + (proc.stderr or "")
    verdict = "Mesh OK" if "Mesh OK" in text else ("Failed" if "Failed" in text else f"rc={proc.returncode}")
    (DST / f"checkmesh_{tag}.log").write_text(text[-30000:], encoding="utf-8", errors="replace")
    import re

    m = re.search(r"non-orthogonality Max:\s*([\d.]+)", text)
    s = re.search(r"Max skewness\s*=\s*([\d.]+)", text)
    f = re.search(r"Failed (\d+) mesh checks", text)
    p = re.search(r"(\d+) +incorrectly oriented", text)
    return {
        "rc": int(proc.returncode),
        "verdict": verdict,
        "nomax": float(m.group(1)) if m else None,
        "skewmax": float(s.group(1)) if s else None,
        "failed_checks": int(f.group(1)) if f else (0 if verdict == "Mesh OK" else None),
        "bad_pyramids": int(p.group(1)) if p else 0,
    }


def main() -> int:
    from polyfoammesh.core.tet_poly_dual import TetPolyDualConverter

    DST.mkdir(parents=True, exist_ok=True)
    try:
        stage("copy")
        if WORK.exists():
            shutil.rmtree(WORK)
        shutil.copytree(SRC, WORK)
        done("copy")
        case = WORK

        if a.mode == "dual":
            stage("dual")
            dres = TetPolyDualConverter(
                case, log=lambda m: None,
                collapse_smooth_edges=True,
                boundary_feature_angle=40.0,
                collapse_volume_tolerance=0.10,
                smooth_free_boundary=True,
            ).run()
            out["dual"] = {
                "success": bool(dres.success),
                "n_cells_after": int(dres.n_cells_after),
                "residual_defects": int(dres.residual_defects),
                "breakdown": {k: int(v) for k, v in dres.defect_breakdown.items()},
                "errors": list(dres.errors),
            }
            done("dual", **{k: out["dual"][k] for k in ("success", "n_cells_after", "residual_defects")})
            if not dres.success:
                raise RuntimeError(f"dual failed: {dres.errors}")
            stage("checkmesh_dual")
            out["checkmesh_dual"] = checkmesh(case, "dual")
            done("checkmesh_dual", **out["checkmesh_dual"])
            out["status"] = "COMPLETE-dual_only"
            save()
            print(json.dumps(out, indent=1), flush=True)
            return 0

        # direct-lt
        from polyfoammesh.core.bl_poly import PolyBoundaryLayerEngine

        stage("bl_5layer_lt")
        r = PolyBoundaryLayerEngine(case, log=lambda m: None).run(
            n_layers=5, first_height=0.00126, growth_rate=1.2,
            apply_to_all=False, local_termination="decoupled",
        )
        out["bl"] = {
            "success": bool(r.success),
            "prisms": int(getattr(r, "n_prism_cells", getattr(r, "bl_prism_cells", 0))),
            "errors": list(getattr(r, "errors", [])),
            "warnings": list(getattr(r, "warnings", [])),
        }
        done("bl_5layer_lt", success=out["bl"]["success"], prisms=out["bl"]["prisms"])
        if not r.success:
            raise RuntimeError(f"direct BL+LT failed: {r.errors}")
        stage("checkmesh")
        out["checkmesh"] = checkmesh(case, "direct_lt")
        done("checkmesh", **out["checkmesh"])
        out["status"] = "COMPLETE"
    except Exception as exc:  # noqa: BLE001
        out["status"] = f"STOPPED: {type(exc).__name__}: {exc}"
        print(out["status"], flush=True)
    save()
    print(json.dumps({k: v for k, v in out.items() if k != "stages"}, indent=1), flush=True)
    return 0 if str(out.get("status", "")).startswith("COMPLETE") else 1


if __name__ == "__main__":
    raise SystemExit(main())
