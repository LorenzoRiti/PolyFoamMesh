from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class OFConfig:
    wsl_distro: str = "Ubuntu"
    env_script: str = "/usr/lib/openfoam/openfoam2512/etc/bashrc"
    cartesian_mesh_bin: str = "cartesianMesh"
    check_mesh_bin: str = "checkMesh"
    _validated: bool = False

    def validate(self) -> bool:
        try:
            env_quoted = shlex.quote(self.env_script)
            bin_quoted = shlex.quote(self.cartesian_mesh_bin)
            result = subprocess.run(
                [
                    "wsl.exe", "-d", self.wsl_distro, "--",
                    "bash", "-lc",
                    f"source {env_quoted} 2>/dev/null && which {bin_quoted}"
                ],
                capture_output=True, text=True, timeout=10,
            )
            self._validated = result.returncode == 0 and result.stdout.strip() != ""
            return self._validated
        except Exception:
            self._validated = False
            return False

    @staticmethod
    def validate_case_path(case_dir: Path | str) -> tuple[bool, str]:
        case_dir = Path(case_dir).resolve()
        if " " in str(case_dir):
            return False, f"OpenFOAM does not support spaces in paths.\nPath: {case_dir}\nChoose a path without spaces."
        return True, ""

    def wsl_linux_case_path(self, case_dir: Path | str) -> str:
        raw = str(case_dir)
        # Already a POSIX path (e.g. the OpenFOAM env script at
        # /usr/lib/openfoam/...): leave it alone. Running it through the
        # Windows->WSL translation turned it into /mnt/c/usr/lib/... , which
        # does not exist — `source` then failed silently behind 2>/dev/null and
        # every OpenFOAM command came back "command not found" (rc=127).
        if raw.startswith("/"):
            return raw.replace("'", "'\\''")
        case_dir = Path(case_dir).resolve()
        drive = case_dir.drive[0].lower()
        rel = str(case_dir).split(":", 1)[1].replace("\\", "/")
        return f"/mnt/{drive}{rel}".replace("'", "'\\''")

    def _build_wsl_cmd(self, bash_code: str) -> list[str]:
        return [
            "wsl.exe", "-d", self.wsl_distro, "--",
            "bash", "-lc", bash_code,
        ]

    def _quoted_linux_path(self, case_dir: Path | str) -> str:
        return shlex.quote(self.wsl_linux_case_path(case_dir))

    def build_command(self, case_dir: Path | str, extra_args: list[str] | None = None) -> list[str]:
        case_dir = Path(case_dir).resolve()
        linux_case = self._quoted_linux_path(case_dir)
        env_quoted = shlex.quote(self.env_script)
        bin_quoted = shlex.quote(self.cartesian_mesh_bin)

        import os as _os
        n_threads = _os.cpu_count() or 4
        env_cmd = (
            f"export OMPI_MCA_btl=vader,self 2>/dev/null; "
            f"source {env_quoted} 2>/dev/null; "
            f"export OMP_NUM_THREADS={max(n_threads - 1, 1)}; "
            f"cd {linux_case} && {bin_quoted}"
        )
        if extra_args:
            env_cmd += " " + " ".join(shlex.quote(a) for a in extra_args)
        return self._build_wsl_cmd(env_cmd)

    def build_check_mesh_cmd(self, case_dir: Path | str) -> list[str]:
        case_dir = Path(case_dir).resolve()
        linux_case = self._quoted_linux_path(case_dir)
        env_quoted = shlex.quote(self.env_script)
        import os as _os
        n_threads = _os.cpu_count() or 4
        cmd = (
            f"export OMPI_MCA_btl=^openib,openfabric,uct 2>/dev/null; "
            f"source {env_quoted} 2>/dev/null; "
            f"export OMP_NUM_THREADS={max(n_threads - 1, 1)}; "
            f"cd {linux_case} && checkMesh"
        )
        return self._build_wsl_cmd(cmd)

    def build_poly_dual_cmd(self, case_dir: Path | str) -> list[str]:
        case_dir = Path(case_dir).resolve()
        linux_case = self._quoted_linux_path(case_dir)
        env_quoted = shlex.quote(self.env_script)
        import os as _os
        n_threads = _os.cpu_count() or 4
        cmd = (
            f"export OMPI_MCA_btl=^openib,openfabric,uct 2>/dev/null; "
            f"source {env_quoted} 2>/dev/null; "
            f"export OMP_NUM_THREADS={max(n_threads - 1, 1)}; "
            f"cd {linux_case}; "
            f"polyDualMesh -constant 2>&1 | tail -20"
        )
        return self._build_wsl_cmd(cmd)
