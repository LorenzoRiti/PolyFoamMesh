"""Test helpers for modules that depend on cadquery (DLL-load issues)."""
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path


def space_free_tmp_root() -> Path:
    """Scratch root for OpenFOAM case dirs, portable across hosts.

    %TEMP% on Windows often contains spaces (the username), and MeshWorker /
    validate_case_dir legitimately refuse case paths with spaces — that is
    why several tests historically hardcoded ``C:/``. Use the OS temp dir
    when it is space-free (Linux CI: /tmp), otherwise the drive root of the
    temp dir (Windows: ``C:/``).
    """
    tmp = Path(tempfile.gettempdir()).resolve()
    if " " not in str(tmp):
        return tmp
    return Path(tmp.drive + "/") if tmp.drive else Path("/")


def _load_module_from_file(module_name: str, file_path: Path, package: str) -> types.ModuleType:
    """Load a Python module directly from a file path, bypassing normal imports."""
    code = file_path.read_text(encoding="utf-8")
    mod = types.ModuleType(module_name)
    mod.__file__ = str(file_path)
    mod.__package__ = package
    sys.modules[module_name] = mod
    exec(compile(code, str(file_path), "exec"), mod.__dict__)
    return mod


def load_commercial_module(module_name: str) -> types.ModuleType:
    """Load a commercial/ module directly, bypassing cadquery DLL chain."""
    src_dir = Path(__file__).resolve().parent.parent / "src"

    # 1. Load lightweight dependencies directly (NOT through core/__init__)
    for mod_name, rel_path in [
        ("polyfoammesh.octopoda_local", "polyfoammesh/octopoda_local.py"),
        ("polyfoammesh.config", "polyfoammesh/config.py"),
        ("polyfoammesh.core.quality_thresholds", "polyfoammesh/core/quality_thresholds.py"),
        ("polyfoammesh.core.validation", "polyfoammesh/core/validation.py"),
        ("polyfoammesh.core.session", "polyfoammesh/core/session.py"),
        ("polyfoammesh.core.meshdict_gen", "polyfoammesh/core/meshdict_gen.py"),
        ("polyfoammesh.core.stl_writer", "polyfoammesh/core/stl_writer.py"),
        ("polyfoammesh.core.of_reader", "polyfoammesh/core/of_reader.py"),
        ("polyfoammesh.core.boundary_reader", "polyfoammesh/core/boundary_reader.py"),
        # Single polyMesh writer — delegated to by mesh_converter /
        # poly_aggregator._write_poly_mesh (stdlib-only imports).
        ("polyfoammesh.core.foam_mesh_io", "polyfoammesh/core/foam_mesh_io.py"),
        # Single checkMesh parser — delegated to by quality_engine._parse_metrics
        # and mesh_engine/poly_aggregator (stdlib-only imports + PySide6).
        ("polyfoammesh.core.openfoam_runner", "polyfoammesh/core/openfoam_runner.py"),
        # Canonical patch-role classifier — delegated to by bl_engine,
        # bc_editor and watertight (stdlib-only imports).
        ("polyfoammesh.core.patch_roles", "polyfoammesh/core/patch_roles.py"),
        # mesh_engine imports write_control_dict from here at module scope.
        # Without this pre-load, tests/test_mesh_engine.py only collected when
        # some alphabetically-earlier test happened to import case_setup first
        # — i.e. it passed in the full suite and failed in isolation.
        ("polyfoammesh.core.case_setup", "polyfoammesh/core/case_setup.py"),
    ]:
        # Never REPLACE a module that is already in sys.modules: re-executing
        # it here creates a second instance, and any earlier importer (e.g.
        # tet_poly_dual, loaded through the real package) keeps a reference
        # to the first one. tests/test_tet_poly_dual.py::test_write_failure_
        # rollback monkeypatches foam_mesh_io.write_faces and would patch the
        # wrong instance, silently not failing (Fase 3 regression found when
        # the harness gained the foam_mesh_io/openfoam_runner pre-loads).
        if mod_name in sys.modules:
            continue
        pkg = ".".join(mod_name.split(".")[:-1])
        _load_module_from_file(mod_name, src_dir / rel_path, pkg)

    # 1b. Pre-load commercial submodules needed by other commercial modules
    for mod_name, rel_path in [
        ("polyfoammesh.commercial.solver_setup", "polyfoammesh/commercial/solver_setup.py"),
    ]:
        _load_module_from_file(mod_name, src_dir / rel_path, "polyfoammesh.commercial")

    # 2. Stub the heavy packages (saving originals so they can be restored —
    #    otherwise later test modules that import the real polyfoammesh.core
    #    / polyfoammesh.gui packages fail with ModuleNotFoundError, since the
    #    stub's __path__ is empty).
    saved = {}
    for pkg_name in ("polyfoammesh.core", "polyfoammesh.gui"):
        saved[pkg_name] = sys.modules.get(pkg_name)
        _stub_pkg(pkg_name)

    try:
        # 3. Load the commercial module
        src_path = src_dir / "polyfoammesh" / "commercial" / f"{module_name}.py"
        return _load_module_from_file(
            f"polyfoammesh.commercial.{module_name}",
            src_path,
            "polyfoammesh.commercial",
        )
    finally:
        for pkg_name, original in saved.items():
            if original is not None:
                sys.modules[pkg_name] = original
            else:
                sys.modules.pop(pkg_name, None)


def _stub_pkg(name: str):
    if name not in sys.modules:
        mod = types.ModuleType(name)
        mod.__path__ = []
        mod.__package__ = name
        sys.modules[name] = mod
