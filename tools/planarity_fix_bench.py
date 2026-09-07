"""Real-case before/after for the non-planar face fix (ref1 reference case).

Prepares three fresh working cases from C:\\polybench\\ref1's pristine tet
backup (one input, three converters), converts each, prints the planarity
report summary, and runs checkMesh via WSL on each output.
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from polyfoammesh.core import foam_mesh_io as fio  # noqa: E402
from polyfoammesh.core.tet_poly_dual import (  # noqa: E402
    TetPolyDualConverter, planarity_report,
)

POLYBENCH = Path("C:/polybench/ref1/constant/polyMesh_tet_backup")
WORK = Path("C:/polybench2/planarity_fix")
CONTROL_DICT = (
    "FoamFile\n{\n    version     2.0;\n    format      ascii;\n"
    "    class       dictionary;\n    object      controlDict;\n}\n"
    "application  checkMesh;\nstartFrom startTime;\nstartTime 0;\n"
    "stopAt endTime;\nendTime 1;\ndeltaT 1;\nwriteControl timeStep;\n"
    "writeInterval 1;\npurgeWrite 0;\nwriteFormat ascii;\nwritePrecision 8;\n"
    "timeFormat general;\ntimePrecision 6;\nrunTimeModifiable true;\n"
)
FVSCHEMES = (
    "FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\n"
    "ddtSchemes { default steadyState; }\n"
    "gradSchemes { default Gauss linear; }\n"
    "divSchemes { default Gauss linear; }\n"
    "laplacianSchemes { default Gauss linear corrected; }\n"
    "interpolationSchemes { default linear; }\n"
    "snGradSchemes { default corrected; }\n"
)
FVSOLUTION = (
    "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\n"
    "solvers { p { solver PCG; preconditioner DIC; tolerance 1e-6; relTol 0.1; } }\n"
)


def prepare(tag: str) -> Path:
    case = WORK / f"ref1_{tag}"
    if case.exists():
        shutil.rmtree(case)
    poly = case / "constant" / "polyMesh"
    sysd = case / "system"
    poly.mkdir(parents=True)
    sysd.mkdir(parents=True)
    for f in ("points", "faces", "owner", "neighbour", "boundary"):
        shutil.copy2(POLYBENCH / f, poly / f)
    (sysd / "controlDict").write_text(CONTROL_DICT, encoding="ascii")
    (sysd / "fvSchemes").write_text(FVSCHEMES, encoding="ascii")
    (sysd / "fvSolution").write_text(FVSOLUTION, encoding="ascii")
    return case


def checkmesh(case: Path) -> str:
    lp = "/mnt/c/polybench2/planarity_fix/" + case.name
    cmd = (
        "source /usr/lib/openfoam/openfoam2512/etc/bashrc 2>/dev/null && "
        f"cd '{lp}' && checkMesh 2>&1"
    )
    r = subprocess.run(["wsl.exe", "-d", "Ubuntu", "--", "bash", "-lc", cmd],
                       capture_output=True, text=True, errors="replace", timeout=1800)
    return r.stdout + r.stderr


def summarize(tag: str, fix: str) -> None:
    case = prepare(tag)
    t0 = time.monotonic()
    res = TetPolyDualConverter(
        case, log=lambda m: None, fix_nonplanar_faces=fix, planarize_iters=20,
    ).run()
    dt = time.monotonic() - t0
    pts, faces, owner, neigh, _ = fio.read_polymesh(case / "constant" / "polyMesh")
    rep = planarity_report(pts, faces, owner, neigh, len(neigh))
    print(f"\n=== ref1 fix={fix!r} ({dt:.1f}s) ===")
    print(f"cells {res.n_cells_after:,}  faces {rep['n_faces']:,}  "
          f"points {len(pts):,}")
    print(f"non-planar {rep['n_nonplanar']:,}/{rep['n_faces']:,} "
          f"({rep['pct_nonplanar']:.2f}%)  "
          f"internal {rep.get('n_nonplanar_internal', 0):,}")
    print(f"max dev {rep['max_dev']:.4e}  mean {rep['mean_dev']:.4e}  "
          f"sum {rep['sum_dev']:.4e}  (bbox {rep['bbox_diag']:.4e})")
    print(f"predicted defects: {res.defect_breakdown}  volume drift "
          f"{abs(res.volume_after - res.volume_before) / max(abs(res.volume_before), 1e-300):.2e}")
    cm = checkmesh(case)
    keep = [l for l in cm.splitlines()
            if ("Mesh OK" in l or "Failed" in l or "incorrectly oriented" in l
                or "skewness = " in l or "non-orthogonality Max" in l
                or "aspect ratio" in l or "open cells" in l
                or "negative volume" in l)]
    for l in keep:
        print("  checkMesh:", l.strip())


if __name__ == "__main__":
    for fix in ("none", "triangulate", "planarize"):
        summarize(f"npfix_{fix}", fix)
    print("\nDONE")
