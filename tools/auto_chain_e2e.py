#!/usr/bin/env python3
"""Auto chain E2E (headless mirror of the GUI auto orchestration).

Resolves the W18 policy, then walks the chain exactly like MainWindow:
tet failure -> next engine; dual invalid -> next engine;
unaccepted checkMesh FAIL -> next engine; weak-valid (Mesh OK yet dual
residual defects) -> one challenge attempt with the next engine,
keep-best; chain exhausted -> one direct-BL leg on the last validated
tet mesh (BL always enabled here, mirroring a user who turned BL on).
First strong-valid wins; a weak-valid winner is compared, never blind.

Usage: python tools/auto_chain_e2e.py venturi [--workdir DIR]
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from w18_auto_table import (  # noqa: E402
    convert_case,
    reference_first_height,
    run_bl,
    run_checkmesh,
    run_dual,
    run_tet,
)

GEO = ROOT / "benchmarks" / "geometries"


def accepted(check: dict) -> bool:
    # checkMesh verdict Mesh OK counts as accepted; anything else retries.
    return check.get("verdict") == "Mesh OK"


def attempt(engine: str, tag: str, wd: Path, geom: Path, direct: bool = False) -> dict:
    wd_tag = wd / tag
    wd_tag.mkdir(parents=True, exist_ok=True)
    try:
        r = run_tet(engine, geom, wd_tag / "e2e.msh")
        if not r.get("success"):
            print(f"[auto] {tag}: tet FAILED ({r.get('failure_reason')})", flush=True)
            return {"engine": engine, "direct": direct, "ok": False,
                    "stage": "tet", "detail": r.get("failure_reason")}
        # 3D-element gate: parity with the GUI (a surface-only MSH is a
        # tet-stage failure, not a conversion failure).
        try:
            from polyfoammesh.core.tet_source import _has_volume_elements
            has_3d = bool(_has_volume_elements(wd_tag / "e2e.msh"))
        except Exception:
            has_3d = True
        if not has_3d:
            print(f"[auto] {tag}: tet produced NO 3D elements "
                  f"(surface-only) -> tet failure", flush=True)
            return {"engine": engine, "direct": direct, "ok": False,
                    "stage": "tet",
                    "detail": "MSH has no 3D elements (surface-only)"}
        if not direct:
            case_dir = wd_tag / "case"
            try:
                convert_case(wd_tag / "e2e.msh", case_dir)
            except Exception as exc:  # noqa: BLE001 - parity with the GUI
                # A convert failure is engine-dependent: the chain advances
                # to the next engine (GUI: _advance_auto_chain).
                print(f"[auto] {tag}: CONVERT FAILED "
                      f"({type(exc).__name__}: {str(exc)[:120]})", flush=True)
                return {"engine": engine, "direct": direct, "ok": False,
                        "stage": "convert",
                        "detail": f"{type(exc).__name__}: {str(exc)[:200]}",
                        "tet_engine": engine}
            d = run_dual(case_dir)
            if not d.get("success"):
                print(f"[auto] {tag}: dual INVALID ({d.get('errors')})", flush=True)
                return {"engine": engine, "direct": direct, "ok": False,
                        "stage": "dual", "detail": str(d.get("errors"))[:200],
                        "tet_engine": engine}
            # BL on the dual, as the GUI worker does before checkMesh.  The
            # GUI's BL retries are not mirrored here: a failure means no BL.
            b = run_bl(case_dir, first_height=reference_first_height(geom))
            prisms =int(b.get("prisms") or 0) if b.get("success") else 0
            print(f"[auto] {tag}: dual BL prisms={prisms} "
                  f"cov={b.get('coverage')}", flush=True)
            c = run_checkmesh(case_dir)
            print(f"[auto] {tag}: dual checkMesh={c.get('verdict')} "
                  f"NO={c.get('nomax')} skew={c.get('skewmax')}", flush=True)
            if not accepted(c):
                return {"engine": engine, "direct": direct, "ok": False,
                        "stage": "checkmesh",
                        "detail": f"{c.get('verdict')} NO={c.get('nomax')} skew={c.get('skewmax')}",
                        "case": str(case_dir), "tet_engine": engine}
            from polyfoammesh.core.tet_source import (
                classify_mesh_outcome,
                is_weak_auto_result,
            )
            score ={"passed": True,
                     "failed_checks": c.get("failed_checks") or 0,
                     "residual": int(d.get("defects") or 0),
                     "skew": c.get("skewmax"),
                     "non_ortho": c.get("nomax"),
                     "has_bl": prisms > 0}
            weak = is_weak_auto_result(True, score["residual"], prisms <= 0)
            print(f"[auto] {tag}: VALID ({c.get('verdict')}, "
                  f"dual defects={score['residual']}"
                  f"{', WEAK' if weak else ''})", flush=True)
            return {"engine": engine, "direct": direct, "ok": True,
                    "stage": "checkmesh", "detail": c.get("verdict"),
                    "case": str(case_dir), "weak": weak, "score": score,
                    "outcome": classify_mesh_outcome(True, True, True, prisms)}
        case_dir = wd_tag / "case_direct"
        if case_dir.exists():
            shutil.rmtree(case_dir)
        convert_case(wd_tag / "e2e.msh", case_dir)
        b = run_bl(case_dir, first_height=reference_first_height(geom))
        if not b.get("success"):
            print(f"[auto] {tag}: direct BL FAILED ({b.get('errors')})", flush=True)
            return {"engine": engine, "direct": True, "ok": False,
                    "stage": "direct-bl", "detail": str(b.get("errors"))[:200],
                    "tet_engine": engine}
        c = run_checkmesh(case_dir)
        print(f"[auto] {tag}: direct BL checkMesh={c.get('verdict')} "
              f"NO={c.get('nomax')} skew={c.get('skewmax')} "
              f"cov={b.get('coverage')}", flush=True)
        return {"engine": engine, "direct": True, "ok": accepted(c),
                "stage": "checkmesh",
                "detail": f"{c.get('verdict')} NO={c.get('nomax')} skew={c.get('skewmax')} cov={b.get('coverage')}",
                "case": str(case_dir), "tet_engine": engine,
                "outcome": "TET_BL" if accepted(c) else "FAILED"}
    except Exception as exc:  # noqa: BLE001 - one attempt must not kill the chain
        print(f"[auto] {tag}: EXCEPTION {type(exc).__name__}: {exc}", flush=True)
        return {"engine": engine, "direct": direct, "ok": False,
                "stage": "exception",
                "detail": f"{type(exc).__name__}: {str(exc)[:300]}"}


def main() -> int:
    from polyfoammesh.core.tet_source import choose_polymesh_engine, preflight_tet_source

    case = sys.argv[1] if len(sys.argv) > 1 else "venturi"
    wd = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(f"C:/polyfoammesh_auto_{case}")
    geom = GEO / f"{case}.stl"
    assert geom.is_file(), geom
    out: dict = {"case": case, "attempts": []}
    t0 = time.monotonic()

    pre = preflight_tet_source(geom)
    plan = choose_polymesh_engine(pre)
    chain = plan.chain()
    print(f"[auto] {case}: plan={chain} ({plan.reason})", flush=True)
    last_tet_engine: str | None = None
    result: dict | None = None

    for i, engine in enumerate(chain):
        result = attempt(engine, f"a{i}_{engine}", wd, geom)
        out["attempts"].append(result)
        if result.get("tet_engine"):
            last_tet_engine = result["tet_engine"]
        if result["ok"] and result.get("weak") and i + 1 < len(chain):
            # Weak-valid primary: keep it as the candidate and challenge
            # with the next engine (keep-best, GUI compare-leg mirror).
            from polyfoammesh.core.tet_source import compare_auto_results
            nxt = chain[i + 1]
            chal = attempt(nxt, f"a{i + 1}_{nxt}", wd, geom)
            out["attempts"].append(chal)
            if chal.get("tet_engine"):
                last_tet_engine = chal["tet_engine"]
            if chal["ok"]:
                winner = compare_auto_results(result["score"], chal["score"])
                result = chal if winner == "challenger" else result
                print(f"[auto] compare: incumbent {result['engine']} kept"
                      if winner == "candidate"
                      else f"[auto] compare: challenger {nxt} wins",
                      flush=True)
            else:
                print(f"[auto] compare: challenger {nxt} failed, "
                      f"keeping {engine}", flush=True)
            out["winner"] = result
            break
        if result["ok"]:
            out["winner"] = result
            break
    else:
        # chain exhausted -> direct BL leg on the last validated tet mesh
        if last_tet_engine is not None:
            result = attempt(last_tet_engine, "direct", wd, geom, direct=True)
            result["direct_leg"] = True
            out["attempts"].append(result)
            if result["ok"]:
                out["winner"] = result
    out["seconds"] = round(time.monotonic() - t0, 1)
    out["verdict"] = (
        f"AUTO VALID {out['winner'].get('outcome', '')}".strip()
        if out.get("winner") else "AUTO FAILED (chain exhausted)"
    )
    (wd / "auto_result.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"[auto] {case}: {out['verdict']} in {out['seconds']}s", flush=True)
    for attempt_rec in out["attempts"]:
        print("   -", attempt_rec.get("engine"),
              "(direct)" if attempt_rec.get("direct") else "",
              attempt_rec.get("stage"), "->", attempt_rec.get("ok"),
              attempt_rec.get("detail"), flush=True)
    return 0 if out.get("winner") else 1


if __name__ == "__main__":
    raise SystemExit(main())
