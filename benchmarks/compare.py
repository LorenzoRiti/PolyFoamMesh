#!/usr/bin/env python3
"""Compare two benchmark result JSON files and print metric deltas.

Usage: python benchmarks/compare.py <baseline.json> <new.json>

Exit code 1 if any hard gate regresses (true->false).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Metrics definition: (key, label, lower_is_better)
# ---------------------------------------------------------------------------
METRICS: list[tuple[str, str, bool]] = [
    ("cell_count", "Cells", False),
    ("max_non_ortho", "Max NonOrtho", True),
    ("avg_non_ortho", "Avg NonOrtho", True),
    ("max_skewness", "Max Skewness", True),
    ("max_aspect_ratio", "Max AspectRatio", True),
    ("neg_cells", "Neg Cells", True),
]

# ---------------------------------------------------------------------------
# ANSI colour helpers
# ---------------------------------------------------------------------------

def _use_color() -> bool:
    return os.environ.get("NO_COLOR") is None and os.environ.get("TERM") != "dumb" and (
        sys.stdout.isatty() or os.environ.get("FORCE_COLOR") is not None
    )


class _C:
    _enabled = _use_color()

    @classmethod
    def _esc(cls, code: str) -> str:
        return f"\033[{code}m" if cls._enabled else ""

    green = classmethod(lambda cls: cls._esc("92"))
    red = classmethod(lambda cls: cls._esc("91"))
    yellow = classmethod(lambda cls: cls._esc("93"))
    cyan = classmethod(lambda cls: cls._esc("96"))
    bold = classmethod(lambda cls: cls._esc("1"))
    dim = classmethod(lambda cls: cls._esc("2"))
    reset = classmethod(lambda cls: cls._esc("0"))


def _status_icon(status: bool) -> str:
    return "[OK]" if status else "[FAIL]"


# ---------------------------------------------------------------------------
# Load & match
# ---------------------------------------------------------------------------

def _load(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(1)
    try:
        data = json.loads(p.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON in {path}: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, list):
        print(f"ERROR: {path} does not contain a JSON array", file=sys.stderr)
        sys.exit(1)
    return data


def _index_by_geom(results: list[dict]) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for entry in results:
        g = entry.get("geometry")
        if not g or not isinstance(g, str):
            print(f"WARNING: entry missing valid 'geometry' field, skipping: {entry.get('geometry')!r}", file=sys.stderr)
            continue
        if g in idx:
            print(f"WARNING: duplicate geometry '{g}' — keeping last occurrence", file=sys.stderr)
        idx[g] = entry
    return idx


# ---------------------------------------------------------------------------
# Delta helpers
# ---------------------------------------------------------------------------

def _float_or_none(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _fmt_delta(baseline: float | int | None, new_val: float | int | None) -> str:
    if baseline is None or new_val is None:
        return "—"
    delta = new_val - baseline
    pct = f" ({delta / baseline * 100:+.1f}%)" if baseline else ""
    return f"{delta:+g}{pct}"


# ---------------------------------------------------------------------------
# Table printing
# ---------------------------------------------------------------------------

def _print_geom_table(
    geom: str,
    base_entry: dict,
    new_entry: dict,
    summary: dict[str, list[str]],
) -> int:
    """Print diffs for one geometry. Returns 1 if hard gate regression found."""
    regressions = 0

    print()
    print(f"{_C.bold()}{_C.cyan()}{'-' * 78}{_C.reset()}")
    print(f"{_C.bold()}Geometry: {geom}{_C.reset()}")
    print(f"{_C.cyan()}{'-' * 78}{_C.reset()}")

    header = f"  {'Metric':<22s} {'Baseline':>12s} {'New':>12s} {'Delta':>18s}  {'Status':>8s}"
    print(header)
    print(f"  {'-' * 22} {'-' * 12} {'-' * 12} {'-' * 18}  {'-' * 8}")

    for key, label, lower_better in METRICS:
        base_val = base_entry.get(key)
        new_val = new_entry.get(key)
        b = _float_or_none(base_val)
        n = _float_or_none(new_val)

        if b is not None and n is not None:
            if b == n:
                status = f"{_C.dim()} ~ {_C.reset()}"
                summary[key].append("same")
            else:
                delta = n - b
                worsened = (delta > 0 and lower_better) or (delta < 0 and not lower_better)
                improved = (delta < 0 and lower_better) or (delta > 0 and not lower_better)
                if worsened:
                    status = f"{_C.red()}{_status_icon(False)}{_C.reset()}"
                    summary[key].append("worse")
                elif improved:
                    status = f"{_C.green()}{_status_icon(True)}{_C.reset()}"
                    summary[key].append("better")
                else:
                    status = f"{_C.dim()} ~ {_C.reset()}"
                    summary[key].append("same")
        else:
            status = f"{_C.dim()} - {_C.reset()}"
            summary[key].append("missing")

        b_str = f"{b:>12}" if b is not None else f"{' -':>12}"
        n_str = f"{n:>12}" if n is not None else f"{' -':>12}"
        delta_str = _fmt_delta(b, n)

        print(f"  {label:<22s} {b_str} {n_str} {delta_str:>18s}  {status:>8s}")

    # Hard gates check
    base_gates = base_entry.get("hard_gates", {}) or {}
    new_gates = new_entry.get("hard_gates", {}) or {}
    all_gate_keys = sorted(set(base_gates.keys()) | set(new_gates.keys()))
    if all_gate_keys:
        print(f"  {'-' * 22} {'-' * 12} {'-' * 12} {'-' * 18}  {'-' * 8}")
        for gate in all_gate_keys:
            bv = base_gates.get(gate)
            nv = new_gates.get(gate)
            b_str = f"{'PASS' if bv else 'FAIL':>12}" if bv is not None else f"{' -':>12}"
            n_str = f"{'PASS' if nv else 'FAIL':>12}" if nv is not None else f"{' -':>12}"
            if bv is True and nv is False:
                msg = f"{_C.red()}{_status_icon(False)} REGRESSION{_C.reset()}"
                regressions = 1
            elif bv is False and nv is True:
                msg = f"{_C.green()}{_status_icon(True)} FIXED{_C.reset()}"
            elif bv == nv:
                msg = f"{_C.dim()} ~ {_C.reset()}"
            else:
                msg = f"{_C.dim()} - {_C.reset()}"
            print(f"  {gate:<22s} {b_str} {n_str} {'':>18s}  {msg:>8s}")

    return regressions


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _print_summary(
    summary: dict[str, list[str]],
    total: int,
    matched: int,
    missing_base: list[str],
    missing_new: list[str],
    total_regressions: int,
):
    print()
    print(f"{_C.bold()}{_C.cyan()}{'=' * 78}{_C.reset()}")
    print(f"{_C.bold()}OVERALL SUMMARY{_C.reset()}")
    print(f"{_C.cyan()}{'=' * 78}{_C.reset()}")
    print(f"  Geometries in baseline: {total}")
    print(f"  Geometries in new:      {len(matched) + len(missing_new)}")
    print(f"  Matched:                {len(matched)}")
    if missing_base:
        print(f"  {_C.yellow()}In baseline only:{_C.reset()} {', '.join(missing_base)}")
    if missing_new:
        print(f"  {_C.yellow()}In new only:{_C.reset()} {', '.join(missing_new)}")

    print()
    header = f"  {'Metric':<22s} {'Better':>8s} {'Worse':>8s} {'Same':>8s} {'Missing':>8s}"
    print(header)
    print(f"  {'-' * 22} {'-' * 8} {'-' * 8} {'-' * 8} {'-' * 8}")
    for key, label, lower_better in METRICS:
        counts = summary.get(key, [])
        better = sum(1 for c in counts if c == "better")
        worse = sum(1 for c in counts if c == "worse")
        same = sum(1 for c in counts if c == "same")
        missing = sum(1 for c in counts if c == "missing")
        print(f"  {label:<22s} {better:>8d} {worse:>8d} {same:>8d} {missing:>8d}")

    if total_regressions:
        print(f"\n  {_C.red()}{_status_icon(False)} Hard gate regressions: {total_regressions}{_C.reset()}")
    else:
        print(f"\n  {_C.green()}{_status_icon(True)} No hard gate regressions{_C.reset()}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if len(sys.argv) != 3:
        print(f"Usage: python {sys.argv[0]} <baseline.json> <new.json>", file=sys.stderr)
        return 1

    baseline_path, new_path = sys.argv[1], sys.argv[2]

    base_results = _load(baseline_path)
    new_results = _load(new_path)

    base_idx = _index_by_geom(base_results)
    new_idx = _index_by_geom(new_results)

    all_geoms = sorted(set(base_idx.keys()) | set(new_idx.keys()))
    missing_base = sorted(set(new_idx.keys()) - set(base_idx.keys()))
    missing_new = sorted(set(base_idx.keys()) - set(new_idx.keys()))
    matched_geoms = sorted(set(base_idx.keys()) & set(new_idx.keys()))

    summary: dict[str, list[str]] = {key: [] for key, _, _ in METRICS}
    total_regressions = 0

    for geom in matched_geoms:
        reg = _print_geom_table(geom, base_idx[geom], new_idx[geom], summary)
        total_regressions += reg

    # Warn about unmatched geometries
    for geom in missing_base:
        print(f"\n  {_C.yellow()}WARNING: '{geom}' missing from baseline (present in new only){_C.reset()}")
    for geom in missing_new:
        print(f"\n  {_C.yellow()}WARNING: '{geom}' missing from new (present in baseline only){_C.reset()}")

    _print_summary(
        summary=summary,
        total=len(base_results),
        matched=matched_geoms,
        missing_base=missing_base,
        missing_new=missing_new,
        total_regressions=total_regressions,
    )

    return 1 if total_regressions > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
