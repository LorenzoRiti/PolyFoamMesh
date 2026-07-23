"""Test helpers for modules that depend on cadquery (DLL-load issues)."""
from __future__ import annotations

import sys
import types
from pathlib import Path


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
        ("cfmesh_autogui.octopoda_local", "cfmesh_autogui/octopoda_local.py"),
        ("cfmesh_autogui.config", "cfmesh_autogui/config.py"),
        ("cfmesh_autogui.core.validation", "cfmesh_autogui/core/validation.py"),
        ("cfmesh_autogui.core.session", "cfmesh_autogui/core/session.py"),
        ("cfmesh_autogui.core.meshdict_gen", "cfmesh_autogui/core/meshdict_gen.py"),
        ("cfmesh_autogui.core.stl_writer", "cfmesh_autogui/core/stl_writer.py"),
        ("cfmesh_autogui.core.boundary_reader", "cfmesh_autogui/core/boundary_reader.py"),
    ]:
        pkg = ".".join(mod_name.split(".")[:-1])
        _load_module_from_file(mod_name, src_dir / rel_path, pkg)

    # 1b. Pre-load commercial submodules needed by other commercial modules
    for mod_name, rel_path in [
        ("cfmesh_autogui.commercial.solver_setup", "cfmesh_autogui/commercial/solver_setup.py"),
    ]:
        _load_module_from_file(mod_name, src_dir / rel_path, "cfmesh_autogui.commercial")

    # 2. Stub the heavy packages (saving originals so they can be restored —
    #    otherwise later test modules that import the real cfmesh_autogui.core
    #    / cfmesh_autogui.gui packages fail with ModuleNotFoundError, since the
    #    stub's __path__ is empty).
    saved = {}
    for pkg_name in ("cfmesh_autogui.core", "cfmesh_autogui.gui"):
        saved[pkg_name] = sys.modules.get(pkg_name)
        _stub_pkg(pkg_name)

    try:
        # 3. Load the commercial module
        src_path = src_dir / "cfmesh_autogui" / "commercial" / f"{module_name}.py"
        return _load_module_from_file(
            f"cfmesh_autogui.commercial.{module_name}",
            src_path,
            "cfmesh_autogui.commercial",
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
