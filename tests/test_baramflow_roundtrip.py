"""End-to-end BaramFlow round-trip: a real case must actually LOAD after export.

The unit tests in test_baramflow_export.py check the validator's logic on
synthetic folders. This test closes the loop for real: mesh a geometry, set the
case up, export it through export_case(), then load the *exported copy* with
OpenFOAM's own tools — the same engine BaramFlow uses. If checkMesh reads the
mesh and foamDictionary parses every field's boundaryField, BaramFlow can open
it too.

WSL/OpenFOAM-gated: skips cleanly where cartesianMesh isn't available (e.g. CI).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:  # cadquery/OCP native libs need their dir on PATH on Windows
    import OCP as _ocp

    _d = os.path.dirname(_ocp.__file__)
    if _d not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
except Exception:
    pass

import pytest

from polyfoammesh.config import OFConfig

cq = pytest.importorskip("cadquery")

from polyfoammesh.core.baramflow_export import export_case, validate_case  # noqa: E402
from polyfoammesh.core.boundary_reader import parse_boundary  # noqa: E402
from polyfoammesh.core.case_setup import setup_case  # noqa: E402
from polyfoammesh.core.geometry import (  # noqa: E402
    classify_faces,
    create_test_cylinder,
    tessellate_patches,
)
from polyfoammesh.core.meshdict_gen import write_meshdict  # noqa: E402
from polyfoammesh.core.stl_writer import export_surface_file  # noqa: E402

# Space-free root: OpenFOAM/WSL reject paths with spaces (the user's TEMP has one).
WORK_ROOT = Path("C:/cfmesh_bench/bf_roundtrip")

CONTROL_DICT = """\
FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }
application cartesianMesh;
startFrom startTime; startTime 0; stopAt endTime; endTime 1000; deltaT 1;
writeControl timeStep; writeInterval 1; purgeWrite 0; writeFormat binary;
writePrecision 6; writeCompression on; timeFormat general; timePrecision 6;
runTimeModifiable true;
"""


def _wsl_available() -> bool:
    try:
        return OFConfig().validate()
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(not _wsl_available(), reason="OpenFOAM/WSL not available"),
    # wsl: also excluded by default via `-m "not wsl"` — the skipif above only
    # checks that WSL *responds*, not that cartesianMesh actually works (a
    # broken OpenFOAM install core-dumps and the test fails instead of
    # skipping). Marking it lets CI/other machines skip it explicitly.
    pytest.mark.wsl,
]


def _run_in_case(cfg: OFConfig, case: Path, cmd: str, timeout: int = 180):
    full = cfg._build_wsl_cmd(
        f"source {cfg._quoted_linux_path(cfg.env_script)} 2>/dev/null; "
        f"cd {cfg._quoted_linux_path(case)} && {cmd}"
    )
    return subprocess.run(full, capture_output=True, text=True, timeout=timeout)


def test_exported_case_loads_in_openfoam():
    cfg = OFConfig()
    case = WORK_ROOT / "case"
    shutil.rmtree(WORK_ROOT, ignore_errors=True)
    case.mkdir(parents=True)

    # 1. Mesh a small cylinder through the real pipeline.
    meshes = tessellate_patches(classify_faces(create_test_cylinder(1.0, 2.0).val()))
    names = [m.metadata.get("name", "wall") for m in meshes]
    export_surface_file(meshes, case)
    # The fixture is authored in cadquery's native mm and tessellate_patches
    # converts mm->m, so the body is ~0.002 m across. A fixed maxCellSize of
    # 0.25 was 125x the body — cells >= body collapse the octree and cfMesh
    # aborts (exit 134, the long-standing "always failed" failure). Derive the
    # cell size from the actual bbox (min_dim/20, ~20 cells across the model)
    # instead of a hardcoded value.
    import numpy as np
    # Per-mesh (max-min) per axis; ignore genuinely flat (zero) spans so a
    # 2D-ish cap doesn't collapse the cell size to 0.
    dims = np.vstack([m.bounds[1] - m.bounds[0] for m in meshes])
    positive = dims[dims > 0]
    min_dim = float(positive.min())
    max_cell = min_dim / 20
    min_cell = max_cell / 2
    write_meshdict(case, max_cell, min_cell, patch_names=names)
    (case / "system" / "controlDict").write_text(CONTROL_DICT, encoding="ascii")

    r = _run_in_case(cfg, case, "cartesianMesh > log.mesh 2>&1", timeout=300)
    assert r.returncode == 0, "cartesianMesh failed"

    # 2. Set up the case (0/ fields, fvSchemes, fvSolution) like the app does.
    patches = parse_boundary(case / "constant" / "polyMesh" / "boundary")
    setup_case(case, patches)

    # 3. Export through the validated exporter.
    validation = validate_case(case)
    assert validation.ok, f"case not BaramFlow-ready: {validation.summary()}"
    out = export_case(case, WORK_ROOT / "dest")

    # 4. Load the EXPORTED copy with OpenFOAM's own tools.
    check = _run_in_case(cfg, out, "checkMesh -constant > log.check 2>&1")
    log = (out / "log.check").read_text(encoding="utf-8", errors="replace")
    assert check.returncode == 0, f"checkMesh failed on export:\n{log[-400:]}"
    assert "Mesh OK" in log, f"checkMesh did not report Mesh OK:\n{log[-400:]}"

    # 5. Every field's boundaryField must cover every mesh patch (the exact
    #    consistency BaramFlow needs, verified through OpenFOAM's own parser).
    mesh_patches = {p.name for p in patches}
    for fname in ("p", "U"):
        fd = _run_in_case(
            cfg, out, f"foamDictionary 0/{fname} -entry boundaryField -keywords 2>/dev/null"
        )
        got = set(fd.stdout.split())
        assert mesh_patches <= got, (
            f"0/{fname} boundaryField is missing patches: {mesh_patches - got}"
        )

    shutil.rmtree(WORK_ROOT, ignore_errors=True)
