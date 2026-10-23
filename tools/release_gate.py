#!/usr/bin/env python3
"""PolyFoamMesh release gate (W17): an executable verification, not a list.

Runs, in order: version sync -> spec static checks -> smoke -> (optional)
E2E tet+dual flow on a tracked STL -> (optional) real checkMesh in WSL/OF
-> (optional, --build) PyInstaller bundle -> (optional, --assemble) Inno
Setup installer. Every stage prints PASS/FAIL/SKIP with timings; the
process exits non-zero iff any stage FAILs. SKIP is always accompanied
by its reason — a skipped stage is a declared limit, never a silent pass.

Always-printed LIMITS (what this gate does NOT do):
- No clean-machine install: the packaged app is verified here by module
  equivalence (the frozen bundle contains these same modules), not by a
  second machine. A true clean-room install remains manual.
- No ISCC unless installed: --assemble SKIPped with reason on machines
  without Inno Setup (this dev machine included).
- No R1-173334 full-BL E2E: the recorded truth mesh.msh (2026-10-20) no
  longer exists on this machine; the automated E2E runs on tracked STLs.
- No boundary-layer stage: BL is covered by the lane suites; the gate E2E
  runs tet -> dual -> convert -> checkMesh (checkMesh only with WSL+OF).
- checkMesh needs a space-free case path (OpenFOAM rejects spaces); the
  E2E workdir defaults to C:\\polyfoammesh_gate for that reason.

Usage (from the repository root):
    python tools/release_gate.py [--e2e] [--build] [--assemble]
                                 [--e2e-case venturi] [--workdir DIR]
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(TOOLS))

# Transitive native deps that static analysis historically missed (W17 D6:
# casadi via cadquery.occ_impl, nlopt via sketch_solver). They must NEVER
# appear in a spec's excludes list — this rule is executable, not lore.
KNOWN_TRANSITIVE = {"casadi", "nlopt"}

# Tracked files allowed to be "large"/binary (W17 B4). Everything else
# over _SIZE_LIMIT bytes or with a banned suffix fails the repo-clean test.
_SIZE_LIMIT = 1_000_000
_ALLOWED_SUFFIXES = {".ico", ".stl", ".png", ".npz"}
_ALLOWED_LARGE = {
    Path("tests/fixtures/valve_dual.npz"),  # test fixture, skip-guarded refs
    Path("docs/assets/poly-mesh-closeup.png"),  # README/USER_GUIDE asset
}

# Installer directory allowlist: the one canonical script + the WSL setup
# + assets. The three stale installer.iss/installer.nsi/nsis_installer.nsi
# were removed in W17 (git rm); this list keeps them from coming back.
INSTALLER_ALLOWLIST = {
    Path("installer/inno_setup.iss"),
    Path("installer/setup_wsl_openfoam.ps1"),
    Path("installer/assets/icon.ico"),
}


@dataclass
class StageResult:
    name: str
    status: str  # PASS / FAIL / SKIP
    detail: str = ""
    seconds: float = 0.0


@dataclass
class GateReport:
    stages: list[StageResult] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "", seconds: float = 0.0) -> StageResult:
        r = StageResult(name, status, detail, seconds)
        self.stages.append(r)
        print(f"[{status:4s}] {name} ({seconds:6.1f}s) {detail}", flush=True)
        return r

    @property
    def failed(self) -> bool:
        return any(s.status == "FAIL" for s in self.stages)

    def summary(self) -> str:
        lines = ["", "== release gate summary =="]
        for s in self.stages:
            lines.append(f"  {s.status:4s}  {s.name}  {s.detail}")
        lines.append("")
        lines.append("LIMITS (declared, not hidden):")
        lines.append("  - no clean-machine install check (module equivalence only);")
        lines.append("  - ISCC/Inno Setup assembly only with --assemble and ISCC present;")
        lines.append("  - R1-173334 truth mesh missing: E2E runs on tracked STLs;")
        lines.append("  - no BL stage in gate E2E (covered by lane suites);")
        lines.append("  - checkMesh only when WSL2+OpenFOAM is detected;")
        lines.append("  - checkMesh verdict is REPORTED (Mesh OK or Failed with cause),")
        lines.append("    not gated: release quality acceptance stays with the lane")
        lines.append("    suites + docs/residual_risks.md.")
        return "\n".join(lines)


def _timed(report: GateReport, name: str, fn, *args, **kwargs) -> StageResult:
    t0 = time.monotonic()
    try:
        status, detail = fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — gate must report, not crash
        status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
    return report.add(name, status, detail, time.monotonic() - t0)


# ---------------------------------------------------------------- versions
def stage_versions() -> tuple[str, str]:
    """All three version sources agree (reuses check_version_sync)."""
    import check_version_sync as cvs

    rc = cvs.main()
    if rc == 0:
        return "PASS", "pyproject/_version.py/inno_setup.iss agree"
    return "FAIL", "version drift detected (see output above)"


# -------------------------------------------------------------- spec static
def _spec_excludes(spec_text: str) -> list[str]:
    m = re.search(r"excludes\s*=\s*\[(.*?)\]", spec_text, re.S)
    if not m:
        return []
    return re.findall(r"""['"]([^'"]+)['"]""", m.group(1))


def _direct_imports() -> set[str]:
    mods: set[str] = set()
    for py in SRC.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError):
            continue
        for node in tree.body:
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods.add(node.module.split(".")[0])
    return mods


def stage_spec_static() -> tuple[str, str]:
    """Both PyInstaller specs are consistent, complete and path-clean."""
    problems: list[str] = []
    for spec_name in ("PolyFoamMesh.spec", "PolyFoamMesh-linux.spec"):
        text = (ROOT / spec_name).read_text(encoding="utf-8", errors="replace")
        if "src" not in text or "polyfoammesh" not in text or "app.py" not in text:
            problems.append(f"{spec_name}: entry point not found")
        if "rthook_casadi_dlls.py" not in text:
            problems.append(f"{spec_name}: casadi runtime hook missing")
        if not (ROOT / "rthook_casadi_dlls.py").is_file():
            problems.append("rthook_casadi_dlls.py missing on disk")
        for data_dir in ("templates", "plugins"):
            d = ROOT / data_dir
            if not d.is_dir() or not any(d.iterdir()):
                problems.append(f"{spec_name}: datas dir {data_dir}/ missing/empty")
        if re.search(r"[A-Za-z]:[\\/]|/home/|/Users/", text):
            problems.append(f"{spec_name}: absolute path detected")
        if "pytetwild" not in text or "find_spec" not in text:
            problems.append(f"{spec_name}: pytetwild guarded-collect missing (W17 D5)")
        if "gmsh" not in text.lower():
            problems.append(f"{spec_name}: no gmsh handling")
    win_text = (ROOT / "PolyFoamMesh.spec").read_text(encoding="utf-8", errors="replace")
    excludes = set(_spec_excludes(win_text)) | set(
        _spec_excludes((ROOT / "PolyFoamMesh-linux.spec").read_text(encoding="utf-8", errors="replace"))
    )
    direct = _direct_imports()
    amputated = sorted((set(excludes) | set()) & direct)
    if amputated:
        problems.append(f"excludes amputate direct imports: {amputated}")
    forbidden = sorted((set(excludes)) & KNOWN_TRANSITIVE)
    if forbidden:
        problems.append(f"excludes contain known-transitive deps: {forbidden}")
    if "src/polyfoammesh/assets/app.ico" not in win_text or not (
        ROOT / "src/polyfoammesh/assets/app.ico"
    ).is_file():
        problems.append("app icon missing (spec references it)")
    if problems:
        return "FAIL", "; ".join(problems)
    return "PASS", f"2 specs ok; {len(excludes)} excludes, none imported"


# ------------------------------------------------------------------- smoke
def stage_smoke() -> tuple[str, str]:
    """The app entry imports, versions match, data dirs resolve."""
    import polyfoammesh.app as appmod
    from polyfoammesh import __version__

    if not callable(getattr(appmod, "main", None)):
        return "FAIL", "polyfoammesh.app.main not callable"
    import pyvista  # noqa: F401  (frozen bundle needs the 3D stack)

    try:
        import gmsh  # noqa: F401
        gmsh_state = "gmsh ok"
    except Exception as exc:  # noqa: BLE001
        gmsh_state = f"gmsh MISSING ({exc})"
    try:
        import pytetwild  # noqa: F401
        ft_state = "pytetwild ok"
    except Exception:  # noqa: BLE001
        ft_state = "pytetwild absent (fTetWild path shows install hint)"
    templates = list((ROOT / "templates").glob("*.json"))
    if not templates:
        return "FAIL", "templates/*.json empty"
    return "PASS", f"v{__version__}; {len(templates)} templates; {gmsh_state}; {ft_state}"


# --------------------------------------------------------------------- E2E
def _of_available() -> tuple[bool, str]:
    """WSL2 + OpenFOAM toolchain reachable (cartesianMesh as proxy)."""
    from polyfoammesh.config import OFConfig

    cfg = OFConfig()
    try:
        ok = cfg.validate()
    except Exception as exc:  # noqa: BLE001
        return False, f"validate raised {exc}"
    if ok:
        return True, "OF toolchain detected in WSL2"
    wsl = shutil.which("wsl.exe") or shutil.which("wsl")
    if not wsl:
        return False, "no wsl.exe on PATH"
    return False, "WSL present but OF cartesianMesh not found (run installer/setup_wsl_openfoam.ps1)"


def run_e2e_flow(stl_name: str, workdir: Path) -> dict:
    """Tet -> dual flow on a tracked STL; returns measured numbers+timings.

    Raises on any failure (the gate/test translates to FAIL). Writes
    e2e_result.json into workdir. checkMesh intentionally NOT here —
    see run_checkmesh_stage (needs WSL+OF + space-free path).
    """
    import numpy as np

    from polyfoammesh.core import ftetwild_wrapper as ftw
    from polyfoammesh.core.mesh_converter import msh_to_of_polymesh
    from polyfoammesh.core.tet_poly_dual import TetPolyDualConverter

    stl = ROOT / "benchmarks" / "geometries" / stl_name
    if stl.suffix != ".stl":
        stl = stl.with_suffix(".stl") if stl.suffix else stl.parent / (stl.name + ".stl")
    if not stl.is_file():
        raise FileNotFoundError(f"E2E STL missing: {stl}")
    workdir.mkdir(parents=True, exist_ok=True)
    out: dict = {"stl": stl_name, "stages": {}}

    t0 = time.monotonic()
    V, T, _info = ftw.tetrahedralize(stl, lr=0.1, opt_iter=2)
    V = np.asarray(V)
    T = np.asarray(T)
    out["stages"]["tetrahedralize"] = {"seconds": round(time.monotonic() - t0, 1)}
    out["n_tets"] = int(len(T))
    if len(V) == 0 or len(T) == 0:
        raise RuntimeError("tetrahedralize produced an empty mesh")

    t0 = time.monotonic()
    faces, owner = ftw.boundary_faces(V, T)
    faces = ftw.orient_faces_outward(V, T, faces, owner)
    msh = workdir / "e2e.msh"
    ftw.write_msh22(msh, V, T, np.asarray(faces), np.ones(len(faces), dtype=np.int64), ["wall"])
    case = workdir / "case"
    if case.exists():
        shutil.rmtree(case)
    msh_to_of_polymesh(msh, case)
    # checkMesh needs a real case skeleton. Reuse the app's own writers:
    # core.case_setup.write_control_dict (single shared writer) plus the
    # fvSchemes/fvSolution skeletons from the GUI direct-GMSH export path.
    from polyfoammesh.core.case_setup import write_control_dict

    write_control_dict(case)
    (case / "system" / "fvSchemes").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }\n"
        "ddtSchemes { default steadyState; }\n"
        "gradSchemes { default Gauss linear; }\n"
        "divSchemes { default Gauss linear; }\n"
        "laplacianSchemes { default Gauss linear corrected; }\n"
        "interpolationSchemes { default linear; }\n"
        "snGradSchemes { default corrected; }\n",
        encoding="ascii",
    )
    (case / "system" / "fvSolution").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }\n"
        "solvers { p { solver PCG; preconditioner DIC; tolerance 1e-6; relTol 0.1; } }\n",
        encoding="ascii",
    )
    out["stages"]["convert"] = {"seconds": round(time.monotonic() - t0, 1)}

    t0 = time.monotonic()
    res = TetPolyDualConverter(case, log=lambda m: None).run()
    out["stages"]["dual"] = {"seconds": round(time.monotonic() - t0, 1)}
    out["dual_success"] = bool(res.success)
    out["n_cells_after"] = int(res.n_cells_after)
    out["n_poly_cells"] = int(res.n_poly_cells)
    out["min_cell_volume"] = float(res.min_cell_volume)
    out["volume_before"] = float(res.volume_before)
    out["volume_after"] = float(res.volume_after)
    out["residual_defects"] = int(res.residual_defects)
    if not res.success:
        raise RuntimeError(f"dual converter failed: {res.errors}")
    if res.min_cell_volume <= 0:
        raise RuntimeError("non-positive cell volume in dual mesh")
    vol_err = abs(res.volume_after - res.volume_before) / max(res.volume_before, 1e-300)
    out["volume_rel_err"] = float(vol_err)
    if vol_err > 1e-6:
        raise RuntimeError(f"dual volume not conserved: rel err {vol_err:.2e}")

    (workdir / "e2e_result.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def stage_e2e(case: str, workdir: Path) -> tuple[str, str]:
    try:
        out = run_e2e_flow(case, workdir)
    except ImportError as exc:
        return "SKIP", f"optional dep missing: {exc}"
    detail = (
        f"{out['n_tets']} tets -> {out['n_poly_cells']} polys, "
        f"vol-err {out['volume_rel_err']:.1e}, "
        f"tet {out['stages']['tetrahedralize']['seconds']}s + "
        f"dual {out['stages']['dual']['seconds']}s"
    )
    return "PASS", detail


def stage_checkmesh(workdir: Path) -> tuple[str, str]:
    """Real checkMesh on the E2E dual case (WSL+OF only, space-free path)."""
    ok, reason = _of_available()
    if not ok:
        return "SKIP", reason
    if " " in str(workdir.resolve()):
        return "SKIP", f"workdir has spaces (OF refuses): {workdir}"
    from polyfoammesh.config import OFConfig

    case = workdir / "case"
    if not (case / "constant" / "polyMesh").exists():
        return "SKIP", "no E2E case built (run --e2e first)"
    cfg = OFConfig()
    cmd = cfg.build_check_mesh_cmd(case)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except Exception as exc:  # noqa: BLE001
        return "FAIL", f"checkMesh launch failed: {exc}"
    text = (proc.stdout or "") + (proc.stderr or "")
    verdict = "Mesh OK" if "Mesh OK" in text else ("Failed" if "Failed" in text else f"rc={proc.returncode}")
    (workdir / "checkmesh.log").write_text(text[-20000:], encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return "FAIL", f"checkMesh rc={proc.returncode} ({verdict})"
    # The verdict is REPORTED, not gated, by design: the coarse E2E case may
    # fail quality on a known, declared wall (e.g. venturi dual-only =
    # WALL-01). Release quality acceptance lives in the lane suites plus
    # docs/residual_risks.md; this stage proves the OF toolchain runs
    # end-to-end from the packaged pipeline and reports honestly.
    detail = f"checkMesh ran (verdict={verdict})"
    if verdict != "Mesh OK":
        detail += "; quality gated by lane suites + residual_risks (see LIMITS)"
    return "PASS", detail


# ------------------------------------------------------------- build/asset
def _find_iscc() -> Path | None:
    which = shutil.which("ISCC.exe") or shutil.which("ISCC")
    if which:
        return Path(which)
    for cand in (
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ):
        if cand.is_file():
            return cand
    return None


def stage_build() -> tuple[str, str]:
    """Real PyInstaller bundle (slow: minutes, ~500MB)."""
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "PyInstaller", "PolyFoamMesh.spec"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=3600,
        )
    except Exception as exc:  # noqa: BLE001
        return "FAIL", f"pyinstaller launch failed: {exc}"
    exe = ROOT / "dist" / "PolyFoamMesh" / "PolyFoamMesh.exe"
    if proc.returncode != 0 or not exe.is_file():
        tail = ((proc.stdout or "") + (proc.stderr or ""))[-3000:]
        (ROOT / "build_gate_error.log").write_text(tail, encoding="utf-8", errors="replace")
        return "FAIL", f"pyinstaller rc={proc.returncode} (tail in build_gate_error.log)"
    return "PASS", f"bundle ok ({time.monotonic() - t0:.0f}s): {exe}"


def stage_assemble() -> tuple[str, str]:
    """Inno Setup installer from dist/ (needs ISCC + dist/ bundle)."""
    iscc = _find_iscc()
    if iscc is None:
        return "SKIP", "ISCC.exe not found (install Inno Setup 6 to assemble)"
    if not (ROOT / "dist" / "PolyFoamMesh").is_dir():
        return "SKIP", "dist/PolyFoamMesh missing (run --build first)"
    try:
        proc = subprocess.run(
            [str(iscc), r"installer\inno_setup.iss"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=1200,
        )
    except Exception as exc:  # noqa: BLE001
        return "FAIL", f"ISCC launch failed: {exc}"
    outs = sorted((ROOT / "installer" / "output").glob("PolyFoamMesh-*-Setup.exe"))
    if proc.returncode != 0 or not outs:
        return "FAIL", f"ISCC rc={proc.returncode}"
    newest = max(outs, key=lambda p: p.stat().st_mtime)
    return "PASS", f"installer ok: {newest.name} ({newest.stat().st_size // 1_000_000}MB)"


# ------------------------------------------------------------------- repo
def _tracked_sizes() -> dict[str, int]:
    """{path: bytes} for every tracked file, via two git calls (batch)."""
    ls = subprocess.run(
        ["git", "ls-files", "-s"], capture_output=True, text=True, cwd=str(ROOT)
    )
    entries: list[tuple[str, str]] = []
    for line in ls.stdout.splitlines():
        meta, _, path = line.partition("\t")
        if path:
            entries.append((meta.split()[1], path))
    if not entries:
        return {}
    bc = subprocess.run(
        ["git", "cat-file", "--batch-check=%(objectname) %(objectsize)"],
        input="\n".join(h for h, _ in entries),
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    sizes = {h: 0 for h, _ in entries}
    for line in bc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            sizes[parts[0]] = int(parts[1])
    return {path: sizes.get(h, 0) for h, path in entries}


def tracked_binaries() -> list[str]:
    """Tracked paths violating the no-binaries rule (allowlist excluded)."""
    bad: list[str] = []
    try:
        sizes = _tracked_sizes()
    except Exception:  # noqa: BLE001 — fail open with a clear message
        return ["could not enumerate tracked files (git failed)"]
    for path, size in sorted(sizes.items()):
        p = Path(path)
        if p.suffix.lower() in {".exe", ".dll", ".msi", ".bin", ".so", ".pyd"}:
            bad.append(f"{path} (banned suffix)")
        elif size > _SIZE_LIMIT and p not in _ALLOWED_LARGE:
            bad.append(f"{path} ({size} bytes, {'allowed suffix but ' if p.suffix.lower() in _ALLOWED_SUFFIXES else ''}not in allowlist)")
    return bad


def stage_repo_clean() -> tuple[str, str]:
    bad = tracked_binaries()
    if bad:
        return "FAIL", "; ".join(bad)
    # Allowlist applies to TRACKED installer files only: installer/output/
    # holds gitignored build artifacts (*-Setup.exe) that legitimately sit
    # on disk after a local build — they are not repo content.
    proc = subprocess.run(
        ["git", "ls-files", "installer/"], capture_output=True, text=True, cwd=str(ROOT)
    )
    inst = {Path(line) for line in proc.stdout.splitlines() if line.strip()}
    extra = sorted(str(p) for p in inst if p not in INSTALLER_ALLOWLIST)
    if extra:
        return "FAIL", f"installer/ has non-allowlisted tracked files: {extra}"
    return "PASS", "no tracked binaries; installer/ == allowlist"


# -------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PolyFoamMesh executable release gate.")
    ap.add_argument("--e2e", action="store_true", help="run tet->dual E2E flow + checkMesh probe")
    ap.add_argument("--build", action="store_true", help="run the real PyInstaller build (minutes)")
    ap.add_argument("--assemble", action="store_true", help="assemble the Inno Setup installer (needs ISCC)")
    ap.add_argument("--e2e-case", default="venturi", help="tracked STL basename without .stl")
    ap.add_argument("--workdir", default=r"C:\polyfoammesh_gate", help="E2E workdir (must be space-free for OF)")
    args = ap.parse_args(argv)

    report = GateReport()
    print("== PolyFoamMesh release gate ==", flush=True)
    _timed(report, "versions", stage_versions)
    _timed(report, "spec-static", stage_spec_static)
    _timed(report, "smoke", stage_smoke)
    _timed(report, "repo-clean", stage_repo_clean)
    if args.e2e:
        wd = Path(args.workdir)
        _timed(report, "e2e-flow", stage_e2e, args.e2e_case, wd)
        _timed(report, "checkmesh", stage_checkmesh, wd)
    else:
        report.add("e2e-flow", "SKIP", "pass --e2e to run the tet->dual flow")
        report.add("checkmesh", "SKIP", "pass --e2e to probe WSL+OF checkMesh")
    if args.build:
        _timed(report, "build", stage_build)
    else:
        report.add("build", "SKIP", "pass --build for the real PyInstaller bundle (minutes)")
    if args.assemble:
        _timed(report, "assemble", stage_assemble)
    else:
        report.add("assemble", "SKIP", "pass --assemble with ISCC installed")
    print(report.summary(), flush=True)
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
