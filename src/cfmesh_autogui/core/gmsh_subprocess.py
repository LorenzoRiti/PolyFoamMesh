"""Out-of-process GMSH helpers shared by the GUI SAMR worker and CLI tools.

The GUI intentionally runs GMSH in a fresh short-lived subprocess (its bundled
OpenCASCADE clashes with cadquery/OCP in the same process), so the solution-
adaptive loop's ``remesh_fn`` needs a small, agreed set of launchers instead of
a hard dependency on a particular worker class. These wrap the app's own
``gmsh_wrapper.py`` CLI (via ``_gmsh_wrapper_script_cmd``), stream every line
through *on_line* so nothing is hidden from the visible log, and parse the JSON
tail gmsh_wrapper prints on completion.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

LogFn = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


def _gmsh_cmd(args: list[str], frozen_flag: str) -> tuple[list[str], str | None]:
    from cfmesh_autogui.core.openfoam_runner import _gmsh_wrapper_script_cmd
    return _gmsh_wrapper_script_cmd(args, frozen_flag, script_name="gmsh_wrapper.py")


def run_gmsh_volume(
    step_path: Path | str,
    msh_path: Path | str,
    detail: str = "medium",
    user_lc: float = 0.0,
    min_lc: float = 0.0,
    max_cells_target: int = 0,
    size_field_file: Path | str | None = None,
    on_line: LogFn = _noop,
    timeout_s: int = 3600,
    threads: int = 0,
) -> dict:
    """Mesh *step_path* via gmsh_wrapper's ``volume`` CLI, returning its JSON.

    *user_lc*/*min_lc* <= 0 select the adaptive path (cross-section-aware
    bulk sizing). *size_field_file* is passed via the
    ``GMSH_SOLUTION_SIZE_FIELD`` env var the CLI reads — this is how the
    SAMR loop tells GMSH where to refine. *threads* > 0 enables GMSH
    multi-threaded meshing (``GMSH_NUM_THREADS``), the main lever for the
    large remeshes near the end of the loop.
    """
    from cfmesh_autogui.core.openfoam_runner import _stream_subprocess

    args = [
        "volume", str(Path(step_path).resolve()), str(Path(msh_path).resolve()),
        detail, "0", "0", "1.2",
        repr(float(user_lc)), repr(float(min_lc)), str(int(max_cells_target)),
    ]
    cmd, run_cwd = _gmsh_cmd(args, "--gmsh-volume")
    env = dict(os.environ)
    env["GMSH_SOLUTION_SIZE_FIELD"] = str(size_field_file) if size_field_file else ""
    if int(threads) > 0:
        env["GMSH_NUM_THREADS"] = str(int(threads))

    last_err = ""
    for attempt in (1, 2):  # GMSH's OCC mesher intermittently dies natively
        t0 = time.monotonic()
        rc, stdout, stderr, timed_out = _stream_subprocess(
            cmd, run_cwd, timeout_s, on_line, env=env, heartbeat_s=30,
        )
        if timed_out:
            last_err = f"GMSH volume meshing timed out after {timeout_s}s"
        elif rc != 0:
            tail = "\n".join((stdout + stderr)[-40:])
            last_err = f"GMSH volume meshing failed (exit {rc}):\n{tail}"
        else:
            payload = None
            for line in reversed(stdout):
                if line.strip().startswith("{"):
                    try:
                        payload = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
            if payload is not None and payload.get("success"):
                payload["wall_time_s"] = time.monotonic() - t0
                return payload
            tail = "\n".join(stdout[-10:])
            last_err = f"GMSH subprocess produced no JSON result:\n{tail}"
        if attempt == 1:
            on_line(f"[gmsh] attempt {attempt} failed, retrying: {last_err.splitlines()[0]}")

    raise RuntimeError(last_err)


def run_gmsh_to_foam(
    case_dir: Path | str,
    msh_name: str,
    on_line: LogFn = _noop,
    timeout_s: int = 900,
) -> None:
    """Convert *.msh* in *case_dir* to OpenFOAM polyMesh via gmshToFoam."""
    from cfmesh_autogui.core.openfoam_runner import _stream_subprocess

    args = ["convert_to_foam", str(Path(case_dir).resolve()), msh_name]
    cmd, run_cwd = _gmsh_cmd(args, "--gmsh-convert-to-foam")
    rc, stdout, stderr, timed_out = _stream_subprocess(
        cmd, run_cwd, timeout_s, on_line, heartbeat_s=30,
    )
    if timed_out:
        raise RuntimeError(f"gmshToFoam timed out after {timeout_s}s")
    payload = None
    for line in reversed(stdout):
        if line.strip().startswith("{"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if rc != 0 or payload is None or not payload.get("success"):
        tail = "\n".join((stdout + stderr)[-30:])
        raise RuntimeError(f"gmshToFoam failed (rc={rc}):\n{tail}")


def write_case_skeleton(case_dir: Path | str) -> None:
    """Minimal OpenFOAM case skeleton gmshToFoam needs to run (controlDict,
    fvSchemes, fvSolution). setup_case() overwrites them properly later."""
    case_dir = Path(case_dir)
    (case_dir / "system").mkdir(parents=True, exist_ok=True)
    (case_dir / "constant").mkdir(parents=True, exist_ok=True)
    (case_dir / "system" / "controlDict").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; "
        "object controlDict; }\n"
        "application cartesianMesh;\n"
        "startFrom startTime; startTime 0;\n"
        "stopAt endTime; endTime 1000;\n"
        "deltaT 1;\n"
        "writeControl timeStep; writeInterval 1;\n"
        "writeFrequency 1;\n"
        "purgeWrite 0; writeFormat binary; writePrecision 6;\n"
        "writeCompression on; timeFormat general; timePrecision 6;\n"
        "runTimeModifiable true;\n",
        encoding="ascii",
    )
    from cfmesh_autogui.core import case_setup as _cs
    (case_dir / "system" / "fvSchemes").write_text(_cs.FV_SCHEMES, encoding="ascii")
    (case_dir / "system" / "fvSolution").write_text(_cs.FV_SOLUTION, encoding="ascii")


def assemble_runnable_case(
    case_dir: Path | str,
    inlet_velocity: tuple[float, float, float] = (1.0, 0.0, 0.0),
    end_time: int = 400,
    residual_control: float = 1e-4,
    write_interval: int = 1000,
    flow_direction: tuple[float, float, float] | None = None,
    on_line: LogFn = _noop,
) -> dict:
    """After gmshToFoam: classify GMSH ``surface_N`` patches geometrically,
    type walls ``wall`` (wall-function BCs abort otherwise), and write a
    runnable simpleFoam case. Returns ``{roles, cells}``.
    """
    from cfmesh_autogui.core.boundary_reader import (
        count_cells, parse_boundary,
    )
    from cfmesh_autogui.core.case_setup import (
        infer_patch_roles, set_wall_patch_types, setup_case,
    )

    def _tri(v) -> tuple[float, float, float]:
        v = tuple(float(x) for x in v)
        return (v[0], v[1], v[2])

    case_dir = Path(case_dir)
    fd = _tri(flow_direction) if flow_direction is not None else _tri(inlet_velocity)
    patches = parse_boundary(case_dir / "constant" / "polyMesh" / "boundary")
    roles = infer_patch_roles(case_dir, patches, flow_direction=fd)
    on_line(f"[adaptive] patch roles ({case_dir.name}): {roles}")
    set_wall_patch_types(case_dir, [n for n, r in roles.items() if r == "wall"])
    setup_case(
        case_dir, patches, inlet_velocity=_tri(inlet_velocity),
        end_time=end_time, residual_control=residual_control,
        write_interval=write_interval, patch_roles=roles,
    )
    return {"roles": roles, "cells": count_cells(case_dir)}


def remesh_from_cad(
    step_path: Path | str,
    case_dir: Path | str,
    detail: str,
    size_field_file: Path | str | None,
    inlet_velocity: tuple[float, float, float],
    end_time: int,
    user_lc: float = 0.0,
    min_lc: float = 0.0,
    threads: int = 0,
    on_line: LogFn = _noop,
) -> tuple[str, int]:
    """One SAMR remesh cycle: GMSH mesh from the ORIGINAL CAD with the new
    size field, convert, and assemble a runnable solve case.

    Returns (case_dir, n_cells). Raises on failure.
    """
    case_dir = Path(case_dir)
    msh = case_dir / f"mesh_{case_dir.name}.msh"
    payload = run_gmsh_volume(
        step_path, msh, detail=detail, user_lc=user_lc, min_lc=min_lc,
        size_field_file=size_field_file, on_line=on_line, threads=threads,
    )
    write_case_skeleton(case_dir)
    run_gmsh_to_foam(case_dir, msh.name, on_line=on_line)
    info = assemble_runnable_case(
        case_dir, inlet_velocity=inlet_velocity, end_time=end_time,
        on_line=on_line,
    )
    return str(case_dir), int(info["cells"])
