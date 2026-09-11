"""Split-rounds re-measurement on the regenerated valve fixture (FASE 6
follow-up).  A/B comparison: ``split_rounds=0`` vs ``split_rounds=1`` (and
optional ``=2``) on the production converter's current code with the
2026-09-09 fixture.  Writes the result table to
``C:/polybench2/split_rounds_rebench/result.json`` and prints a summary.

No WSL: this is the converter's in-process keep-best re-measurement
(pyramid / non_ortho / skew counts).  Engine gate only.

This is a one-shot benchmark; not part of the regular test suite.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from polyfoammesh.core.tet_poly_dual import TetPolyDualConverter  # noqa: E402

TET_BACKUP = Path("C:/polybench/valve1/constant/polyMesh_tet_backup")
WORK = Path("C:/polybench2/split_rounds_rebench")


def _prepare_case(src: Path, dst: Path) -> None:
    shutil.rmtree(dst, ignore_errors=True)
    (dst / "constant").mkdir(parents=True)
    shutil.copytree(src, dst / "constant" / "polyMesh")


def _one_run(split_rounds: int) -> dict:
    case = WORK / f"case_r{split_rounds}"
    _prepare_case(TET_BACKUP, case)
    t0 = time.perf_counter()
    res = TetPolyDualConverter(
        case, log=lambda m: None, split_rounds=split_rounds,
    ).run()
    dt = time.perf_counter() - t0
    drift = (abs(res.volume_after - res.volume_before)
             / abs(res.volume_before)) if res.volume_before else None
    return {
        "split_rounds": int(split_rounds),
        "success": bool(res.success),
        "residual_defects": int(res.residual_defects),
        "defect_breakdown": dict(res.defect_breakdown or {}),
        "total_defects": (int(res.defect_breakdown.get("pyramid", 0))
                          + int(res.defect_breakdown.get("non_ortho", 0))
                          + int(res.defect_breakdown.get("skew", 0))),
        "volume_drift": drift,
        "wall_time_s": round(dt, 1),
    }


def main() -> int:
    if not TET_BACKUP.exists():
        print(f"valve tet backup not present: {TET_BACKUP}")
        return 2
    WORK.mkdir(parents=True, exist_ok=True)
    rounds = [0, 1]
    results = {f"r{r}": _one_run(r) for r in rounds}
    print(f"{'split_rounds':>14s} {'success':>8s} {'total':>7s} {'pyr':>5s} "
          f"{'n_ortho':>7s} {'skew':>5s} {'vol_drift':>10s} {'wall_s':>7s}")
    for k, v in results.items():
        db = v["defect_breakdown"]
        print(f"{k:>14s} {str(v['success']):>8s} {v['total_defects']:>7d} "
              f"{db.get('pyramid', 0):>5d} {db.get('non_ortho', 0):>7d} "
              f"{db.get('skew', 0):>5d} "
              f"{(v['volume_drift'] if v['volume_drift'] is not None else 0):>10.2e} "
              f"{v['wall_time_s']:>7.1f}")
    out = WORK / "result.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
