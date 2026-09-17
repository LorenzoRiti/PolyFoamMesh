# -*- coding: utf-8 -*-
"""Lane B: combined most_visible + decoupled_vertex + local_height_retry
on a pristine valve_baseline copy, with checkMesh before/after.

Usage: python tools/bench_bl_valve_combined.py
"""
from __future__ import annotations

import io
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

from polyfoammesh.config import OFConfig  # noqa: E402
from polyfoammesh.core.bl_poly import PolyBoundaryLayerEngine  # noqa: E402

BASE = Path("C:/polybench2/valve_baseline")
CASE = Path("C:/polybench2/bl_valve_combined")


def checkmesh(cfg, case):
    r = subprocess.run(
        cfg._build_wsl_cmd(
            f"source {shlex.quote(cfg.env_script)} 2>/dev/null; "
            f"cd {cfg._quoted_linux_path(case)} && "
            "checkMesh -allGeometry -allTopology 2>&1"
        ),
        capture_output=True, text=True, timeout=1800,
    )
    text = r.stdout + r.stderr

    def _num(pat):
        m = re.search(pat, text)
        return float(m.group(1).rstrip(".")) if m else 0.0

    def _int(pat):
        m = re.search(pat, text)
        return int(m.group(1).replace(",", "")) if m else 0

    # patterns verified against real OpenFOAM-2512 checkMesh output
    # (NOT the guessed format this parser originally used, which silently
    # returned zeros on every field).
    m = re.search(r"severely non-orthogonal.*faces:\s*([\d,]+)", text,
                  re.IGNORECASE)
    severe_no = int(m.group(1).replace(",", "")) if m else 0
    m = re.search(r"Max skewness\s*=\s*([\d.]+),\s*([\d,]+) highly skew",
                  text, re.IGNORECASE)
    skew_max = float(m.group(1)) if m else 0.0
    skew_faces = int(m.group(2).replace(",", "")) if m else 0
    wrong = _int(r"face pyramids:\s*(\d+)")
    if not wrong:
        wrong = _int(r"incorrectly oriented faces[^\d]*(\d+)")
    return {
        "cells": _int(r"cells:\s+(\d+)"),
        "severe_non_ortho": severe_no,
        "non_ortho_max": _num(r"non-orthogonality Max:\s*([\d.]+)"),
        "non_ortho_avg": _num(r"average:\s*([\d.]+)"),
        "skew_max": skew_max if skew_max else _num(r"Max skewness\s*[:=]\s*([\d.]+)"),
        "skew_faces": skew_faces,
        "wrong_oriented": wrong,
        "face_tets": _int(r"face tets:\s*(\d+)"),
        "aspect_cells": _int(r"aspect ratio: [\d.]+, number of cells\s*(\d+)"),
        "short_edges": _int(r"number too small:\s*(\d+)"),
        "min_volume": _num(r"Min volume\s*=\s*([\d.eE+-]+)"),
        "failed": _int(r"Failed (\d+) mesh checks"),
        "mesh_ok": "Mesh OK." in text,
        "raw_tail": text[-1500:],
    }


def main() -> int:
    cfg = OFConfig()
    shutil.rmtree(CASE, ignore_errors=True)
    shutil.copytree(BASE, CASE)
    # NOTE: do NOT overwrite system/controlDict — the pristine case ships a
    # complete one (checkMesh on OF2512 requires deltaT and friends).

    print("checkMesh BEFORE (this takes a while)...", flush=True)
    before = checkmesh(cfg, CASE)
    print(f"BEFORE cells={before['cells']} severeNO={before['severe_non_ortho']} "
          f"NOmax={before['non_ortho_max']:.2f} NOavg={before['non_ortho_avg']:.4f} "
          f"skewMax={before['skew_max']:.3f} skewFaces={before['skew_faces']} "
          f"wrong={before['wrong_oriented']} failed={before['failed']} "
          f"MeshOK={before['mesh_ok']}", flush=True)

    t0 = time.monotonic()
    res = PolyBoundaryLayerEngine(CASE, log=lambda m: None).run(
        n_layers=2, first_height=1e-5, growth_rate=1.2, apply_to_all=True,
        normal_method="most_visible", local_termination="decoupled_vertex",
        local_height_retry=True,
    )
    dt = time.monotonic() - t0
    print(f"\nBL combined: success={res.success} ({dt:.0f}s)", flush=True)
    print(f"  excluded_faces={res.stats.get('local_excluded_faces')} "
          f"excluded_verts={res.stats.get('local_excluded_verts')} "
          f"scale={res.stats.get('scale')} "
          f"n_cells_after={res.stats.get('n_cells_after')} "
          f"prisms={res.n_prism_cells}", flush=True)
    print(f"  mode={res.stats.get('local_termination_mode')} "
          f"rounds={res.stats.get('local_termination_max_rounds')}", flush=True)
    if not res.success:
        print("  errors:", res.errors[:2], flush=True)

    if res.success:
        print("checkMesh AFTER (this takes a while)...", flush=True)
        after = checkmesh(cfg, CASE)
        print(f"AFTER cells={after['cells']} severeNO={after['severe_non_ortho']} "
              f"NOmax={after['non_ortho_max']:.2f} "
              f"NOavg={after['non_ortho_avg']:.4f} "
              f"skewMax={after['skew_max']:.3f} skewFaces={after['skew_faces']} "
              f"wrong={after['wrong_oriented']} failed={after['failed']} "
              f"MeshOK={after['mesh_ok']}", flush=True)
    return 0 if res.success else 1


if __name__ == "__main__":
    sys.exit(main())
