"""Run the GMSH tet stage (GUI adaptive path) over the parametric families.

Per case: success, wall time, tet count, and how far the count is from the
requested budget.  Then a per-family table, so a change is judged on CLASSES of
geometry (and on a held-out seed range), never on one file.

Usage: python benchmarks/run_families.py MANIFEST.json OUT.json [--target 100000]
                                         [--timeout 150] [--env KEY=VAL ...]
Resumable: cases already in OUT.json are skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "src" / "polyfoammesh" / "core" / "gmsh_wrapper.py"


def run_case(case: dict, target: int, timeout: float, env_extra: dict, work: Path) -> dict:
    msh = work / f"{case['family']}_{case['seed']}.msh"
    env = dict(os.environ)
    env.update(env_extra)
    t0 = time.perf_counter()
    status, tets, err = "fail", None, ""
    try:
        p = subprocess.run(
            [sys.executable, str(WRAPPER), "volume", f"--step={case['file']}",
             f"--msh={msh}", "--detail=medium", "--max-cell=0", "--min-cell=0",
             f"--max-cells-target={target}"],
            capture_output=True, text=True, env=env, timeout=timeout)
        out = p.stdout + p.stderr
        m = re.findall(r"Final tet\. mesh contains (\d+) tetrahedra", out)
        tets = int(m[-1]) if m else None
        if '"success": true' in out and tets:
            status = "ok"
        else:
            status = "crash" if p.returncode not in (0, 1) else "fail"
            tail = [ln for ln in out.splitlines() if "rror" in ln][-1:] or [""]
            err = tail[0][:160]
    except subprocess.TimeoutExpired:
        status = "timeout"
    return {"family": case["family"], "seed": case["seed"], "status": status,
            "wall": round(time.perf_counter() - t0, 1), "tets": tets,
            "target": target, "err": err}


def table(rows: list[dict]) -> str:
    by = defaultdict(list)
    for r in rows:
        by[r["family"]].append(r)
    lines = [f"{'family':20s} {'n':>3s} {'ok':>3s} {'crash':>5s} {'tmo':>3s} "
             f"{'med s':>7s} {'max s':>7s} {'tets/target med':>16s}"]
    for fam, rs in sorted(by.items()):
        ok = [r for r in rs if r["status"] == "ok"]
        walls = sorted(r["wall"] for r in rs)
        ratios = sorted(r["tets"] / r["target"] for r in ok if r["tets"])
        med = lambda a: a[len(a) // 2] if a else float("nan")  # noqa: E731
        lines.append(
            f"{fam:20s} {len(rs):3d} {len(ok):3d} "
            f"{sum(r['status'] == 'crash' for r in rs):5d} "
            f"{sum(r['status'] == 'timeout' for r in rs):3d} "
            f"{med(walls):7.1f} {max(walls):7.1f} {med(ratios):16.2f}")
    tot = len(rows)
    ok_all = sum(r["status"] == "ok" for r in rows)
    lines.append(f"{'TOTAL':20s} {tot:3d} {ok_all:3d}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("out")
    ap.add_argument("--target", type=int, default=100_000)
    ap.add_argument("--timeout", type=float, default=150.0)
    ap.add_argument("--env", nargs="*", default=[])
    a = ap.parse_args()
    env_extra = dict(kv.split("=", 1) for kv in a.env)
    cases = [c for c in json.loads(Path(a.manifest).read_text()) if "file" in c]
    out = Path(a.out)
    rows = json.loads(out.read_text()) if out.is_file() else []
    done = {(r["family"], r["seed"]) for r in rows}
    work = out.with_suffix("")
    work.mkdir(parents=True, exist_ok=True)
    for c in cases:
        if (c["family"], c["seed"]) in done:
            continue
        r = run_case(c, a.target, a.timeout, env_extra, work)
        rows.append(r)
        out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print(r["family"], r["seed"], r["status"], r["wall"], r["tets"], flush=True)
    print()
    print(table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
