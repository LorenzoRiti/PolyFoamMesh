"""Tests for OFConfig: path conversion, validation, and command security."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.config import OFConfig


def test_wsl_linux_path_conversion():
    cfg = OFConfig()
    linux = cfg.wsl_linux_case_path("C:\\Users\\test\\case1")
    assert linux == "/mnt/c/Users/test/case1", f"Got: {linux}"


def test_validate_case_path_no_spaces():
    ok, msg = OFConfig.validate_case_path("C:\\Users\\test\\case1")
    assert ok, f"Expected valid path, got error: {msg}"


def test_validate_case_path_with_spaces():
    ok, msg = OFConfig.validate_case_path("C:\\Users\\test\\my case")
    assert not ok, "Expected path with spaces to be invalid"
    assert "spaces" in msg.lower()


def test_quoted_linux_path_no_injection():
    cfg = OFConfig()
    quoted = cfg._quoted_linux_path(Path("C:\\test\\case; rm -rf /"))
    assert "'" in quoted, "Path with special chars should be quoted"


def test_build_command_returns_list():
    cfg = OFConfig()
    cmd = cfg.build_command("C:\\test\\case")
    assert isinstance(cmd, list), f"Expected list, got {type(cmd)}"
    if sys.platform == "win32":
        # Windows keeps the exact wsl.exe invocation.
        assert len(cmd) >= 6, f"Expected at least 6 elements, got {len(cmd)}"
        assert "wsl.exe" in cmd[0] or cmd[0].endswith("wsl.exe")
    else:
        # POSIX runs OpenFOAM natively: bash -lc, no wsl.exe anywhere.
        assert cmd[:2] == ["bash", "-lc"], f"Expected bash -lc, got {cmd[:2]}"
        assert not any("wsl" in str(part).lower() for part in cmd), cmd


def test_build_wsl_cmd_posix_dispatch(monkeypatch):
    """Simulate POSIX: _build_wsl_cmd must return plain bash -lc with no
    wsl.exe, while keeping the same bash payload."""
    import polyfoammesh.config as cfgmod

    monkeypatch.setattr(cfgmod.sys, "platform", "linux")
    cfg = OFConfig()
    cmd = cfg._build_wsl_cmd("echo hi")
    assert cmd == ["bash", "-lc", "echo hi"], cmd
    assert not any("wsl" in str(part).lower() for part in cmd), cmd


def test_validate_posix_dispatch_no_wsl(monkeypatch):
    """validate() on POSIX must probe via bash -lc (no wsl.exe) and keep the
    same boolean semantics (rc == 0 AND non-empty stdout)."""
    import subprocess
    import polyfoammesh.config as cfgmod

    monkeypatch.setattr(cfgmod.sys, "platform", "linux")
    captured = {}

    class _Result:
        returncode = 0
        stdout = "/usr/bin/cartesianMesh\n"

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    cfg = OFConfig()
    assert cfg.validate() is True
    assert captured["cmd"][:2] == ["bash", "-lc"], captured["cmd"]
    assert not any("wsl" in str(p).lower() for p in captured["cmd"]), captured["cmd"]


def test_build_command_contains_quoted_path():
    cfg = OFConfig()
    cmd = cfg.build_command("C:\\test\\case1")
    bash_code = cmd[-1]
    assert "/mnt/c/test/case1" in bash_code


def test_build_check_mesh_cmd():
    cfg = OFConfig()
    cmd = cfg.build_check_mesh_cmd("C:\\test\\case")
    bash_code = cmd[-1]
    assert "checkMesh" in bash_code


def test_build_poly_dual_cmd():
    cfg = OFConfig()
    cmd = cfg.build_poly_dual_cmd("C:\\test\\case")
    bash_code = cmd[-1]
    assert "polyDualMesh" in bash_code


def test_wsl_linux_path_escapes_single_quotes():
    cfg = OFConfig()
    path = "C:\\test\\d'angelo"
    linux = cfg.wsl_linux_case_path(path)
    assert "'\\''" in linux, f"Single quote not escaped in: {linux}"


def test_validate_case_path_empty():
    ok, msg = OFConfig.validate_case_path("C:\\")
    assert ok, f"Root path should be valid: {msg}"


if __name__ == "__main__":
    test_wsl_linux_path_conversion()
    test_validate_case_path_no_spaces()
    test_validate_case_path_with_spaces()
    test_quoted_linux_path_no_injection()
    test_build_command_returns_list()
    test_build_command_contains_quoted_path()
    test_build_check_mesh_cmd()
    test_build_poly_dual_cmd()
    test_wsl_linux_path_escapes_single_quotes()
    test_validate_case_path_empty()
    print("ALL PASS")

def test_build_staged_pipeline_command_contains_steps_and_tar():
    """The staged pipeline script must run the steps and copy back the
    polyMesh as one compressed archive (T1.2)."""
    import tempfile
    cfg = OFConfig()
    with tempfile.TemporaryDirectory() as td:
        case = Path(td) / "case"
        (case / "system").mkdir(parents=True)
        cmd = cfg.build_staged_pipeline_command(
            case, steps=["cartesianMesh", "polyDualMesh 90 -overwrite"],
        )
        assert isinstance(cmd, list) and cmd
        script = (case / "system" / "_run_staged_pipeline.sh").read_text(encoding="ascii")
        assert "cartesianMesh" in script
        assert "polyDualMesh 90 -overwrite" in script
        assert "polyMesh.tar.gz" in script
        assert "rsync" in script
        assert "pipeline.log" in script
