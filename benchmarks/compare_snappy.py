"""Leaderboard: geometry x runner matrix, winner per cell, aggregates
(pass rate = terrain B, medians = terrain A, exclusive pass = terrain C).

Usage: python benchmarks/compare_snappy.py --results comparison_X.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from benchmarks.corpus import ALL_RUNNERS, OUR_RUNNERS, RESULTS_DIR  # noqa: E402

_MEDIAN_METRICS = ("max_non_ortho", "max_skewness", "max_aspect_ratio", "wall_time_s")


def load_records(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text("utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} does not contain a JSON array")
    return data


def index_records(records: list[dict]) -> dict[str, dict[str, dict]]:
    idx: dict[str, dict[str, dict]] = {}
    for rec in records:
        idx.setdefault(rec["geometry"], {})[rec["runner"]] = rec
    return idx


def _viable(rec: dict) -> bool:
    return bool(rec.get("thresholds_pass")) and not rec.get("timed_out")


def pick_winner(cell: dict[str, dict]) -> str | None:
    viable = {r: rec for r, rec in cell.items() if _viable(rec)}
    if not viable:
        return None
    return min(
        viable,
        key=lambda r: (
            viable[r].get("neg_cells", 0),
            viable[r].get("max_non_ortho", 1e9),
            viable[r].get("max_skewness", 1e9),
            viable[r].get("max_aspect_ratio", 1e9),
            viable[r].get("wall_time_s", 1e9),
            r,  # deterministic tie-break
        ),
    )


def aggregate(records: list[dict]) -> dict:
    idx = index_records(records)
    runners = sorted({r["runner"] for r in records})
    geoms = sorted(idx)
    pass_rate: dict[str, float] = {}
    median: dict[str, dict[str, float]] = {}
    for runner in runners:
        recs = [idx[g][runner] for g in geoms if runner in idx[g]]
        pass_rate[runner] = (
            sum(1 for r in recs if _viable(r)) / len(recs) if recs else 0.0
        )
        med: dict[str, float] = {}
        for m in _MEDIAN_METRICS:
            vals = [
                float(r[m]) for r in recs
                if _viable(r) and isinstance(r.get(m), (int, float))
            ]
            if vals:
                med[m] = round(statistics.median(vals), 2)
        median[runner] = med
    # exclusive pass: geom where runner passes and the opposite family fails
    snappy = [r for r in runners if r.startswith("snappy")]
    exclusive: dict[str, list[str]] = {r: [] for r in runners}
    for g in geoms:
        for runner in runners:
            rec = idx[g].get(runner)
            if rec is None or not _viable(rec):
                continue
            if runner in OUR_RUNNERS:
                # exclusive when every snappy runner present fails
                present_snappy = [s for s in snappy if s in idx[g]]
                if present_snappy and all(not _viable(idx[g][s]) for s in present_snappy):
                    exclusive[runner].append(g)
            elif runner in snappy:
                # exclusive when every OUR runner present fails
                present_ours = [o for o in OUR_RUNNERS if o in idx[g]]
                if present_ours and all(not _viable(idx[g][o]) for o in present_ours):
                    exclusive[runner].append(g)
    return {"pass_rate": pass_rate, "median": median, "exclusive_pass": exclusive}


def write_leaderboard(records: list[dict], out_md: Path) -> None:
    idx = index_records(records)
    agg = aggregate(records)
    geoms = sorted(idx)
    runners = [r for r in ALL_RUNNERS if any(r in idx[g] for g in geoms)]

    lines: list[str] = ["# PolyFoamMesh vs snappyHexMesh — Leaderboard", ""]
    lines.append(f"Runners: {', '.join(runners)}")
    lines.append("")

    for g in geoms:
        lines.append(f"## {g}")
        lines.append("")
        lines.append("| metric | " + " | ".join(runners) + " |")
        lines.append("|---" * (len(runners) + 1) + "|")
        for label, key in (
            ("cells", "cell_count"), ("non-ortho max", "max_non_ortho"),
            ("skew max", "max_skewness"), ("aspect max", "max_aspect_ratio"),
            ("neg cells", "neg_cells"), ("time (s)", "wall_time_s"),
        ):
            row = [label]
            for r in runners:
                rec = idx[g].get(r)
                row.append(str(rec.get(key, "—")) if rec else "—")
            lines.append("| " + " | ".join(row) + " |")
        row = ["thresholds"]
        for r in runners:
            rec = idx[g].get(r)
            row.append("PASS" if rec and _viable(rec) else "FAIL")
        lines.append("| " + " | ".join(row) + " |")
        winner = pick_winner(idx[g])
        lines.append("")
        lines.append(f"**Winner:** {winner if winner else 'none (no runner passed thresholds)'}")
        lines.append("")

    lines.append("## Aggregates")
    lines.append("")
    lines.append("### Pass rate (terrain B — reliability)")
    lines.append("")
    lines.append("| runner | pass rate |")
    lines.append("|---|---|")
    for r in runners:
        lines.append(f"| {r} | {agg['pass_rate'].get(r, 0.0):.0%} |")
    lines.append("")
    lines.append("### Median metrics over passing runs (terrain A — quality)")
    lines.append("")
    lines.append("| runner | non-ortho | skew | aspect | time (s) |")
    lines.append("|---|---|---|---|---|")
    for r in runners:
        med = agg["median"].get(r, {})
        lines.append(
            f"| {r} | {med.get('max_non_ortho', '—')} | {med.get('max_skewness', '—')} "
            f"| {med.get('max_aspect_ratio', '—')} | {med.get('wall_time_s', '—')} |"
        )
    lines.append("")
    lines.append("### Exclusive passes (terrain C — robustness)")
    lines.append("")
    for r in runners:
        geos = agg["exclusive_pass"].get(r, [])
        lines.append(f"- **{r}**: {', '.join(geos) if geos else '(none)'}")
    lines.append("")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build comparison leaderboard")
    p.add_argument("--results", required=True, help="comparison_*.json path")
    p.add_argument("--out", default=str(RESULTS_DIR / "LEADERBOARD.md"))
    opts = p.parse_args(argv)
    records = load_records(Path(opts.results))
    write_leaderboard(records, Path(opts.out))
    print(f"Leaderboard written to {opts.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
