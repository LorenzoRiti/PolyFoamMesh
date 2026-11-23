"""Environment and input fingerprint capture for reproducible benchmarks.

The benchmark JSON envelope embeds this metadata so a run on another machine
or after a commit is self-describing. Every probe is best-effort: a missing
tool or a slow WSL hop degrades to "unknown" and never blocks the run.
"""

from __future__ import annotations

import datetime
import hashlib
import importlib.metadata
import json
import os
import platform
import socket
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PACKAGES = ("polyfoammesh", "pytetwild", "trimesh", "gmsh", "numpy", "scipy")
_PROBE_TIMEOUT_S = 10


def _safe_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except Exception:
        return "unknown"


def _run_cmd(cmd: list[str], cwd: Path | None = None, timeout: int = 5) -> str:
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return "unknown"
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _git_info() -> dict:
    commit = _run_cmd(["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT)
    dirty_raw = _run_cmd(["git", "status", "--porcelain"], cwd=_REPO_ROOT)
    dirty = dirty_raw not in ("unknown", "")
    dirty_entries = (
        len([line for line in dirty_raw.splitlines() if line.strip()])
        if dirty_raw != "unknown"
        else None
    )
    return {
        "commit": commit if commit != "unknown" else "unknown",
        "dirty": dirty,
        "dirty_entries": dirty_entries,
    }


def _default_bashrc() -> str:
    try:
        from benchmarks.run_benchmarks import OF_BASHRC

        return OF_BASHRC
    except Exception:
        return "/usr/lib/openfoam/openfoam2512/etc/bashrc"


def _openfoam_version() -> str:
    bashrc = os.environ.get("POLYFOAMMESH_OF_BASHRC", _default_bashrc())
    command = f'. "{bashrc}" >/dev/null 2>&1 && echo $WM_PROJECT_VERSION'
    try:
        if os.name == "nt":
            argv = ["wsl.exe", "-e", "bash", "-lc", command]
        else:
            argv = ["bash", "-lc", command]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S)
        if result.returncode != 0:
            return "unknown"
        version = result.stdout.strip()
        return version or "unknown"
    except Exception:
        return "unknown"


def _hash_file(path: Path) -> dict:
    try:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        return {"path": str(path), "sha256": digest.hexdigest(), "size_bytes": size}
    except FileNotFoundError:
        return {"path": str(path), "missing": True}
    except Exception as exc:
        return {"path": str(path), "error": str(exc)}


def _normalise_stl_inputs(stl_inputs: list[dict]) -> list[dict]:
    """Drop absolute paths so the fingerprint stays content-based."""
    normalised = []
    for entry in stl_inputs:
        item = {key: value for key, value in entry.items() if key != "path"}
        normalised.append(item)
    return normalised


def _stable_fingerprint(environment: dict) -> str:
    stable = {
        key: value
        for key, value in environment.items()
        if key not in {"timestamp", "stl_inputs", "fingerprint"}
    }
    stable["stl_inputs"] = _normalise_stl_inputs(environment.get("stl_inputs", []))
    blob = json.dumps(stable, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def capture_environment(
    paths: list[Path] | None = None,
    parameters: dict | None = None,
) -> dict:
    """Capture host, toolchain and input hashes for a benchmark run.

    ``paths`` are the STL inputs actually selected for the run. ``parameters``
    are the effective run parameters (tier, runners, timeouts, poly config).
    """
    stl_inputs = [_hash_file(Path(path)) for path in (paths or [])]
    environment = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "os": os.name,
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "hostname": socket.gethostname(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_executable": sys.executable,
        "git": _git_info(),
        "packages": {name: _safe_version(name) for name in _PACKAGES},
        "openfoam_version": _openfoam_version(),
        "parameters": parameters or {},
        "stl_inputs": stl_inputs,
    }
    environment["fingerprint"] = _stable_fingerprint(environment)
    return environment
