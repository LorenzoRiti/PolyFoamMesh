from __future__ import annotations

import os
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
    _n_physical: int | None = None  # cached cpu_count for parallel cmd

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

        n_threads = os.cpu_count() or 4
        env_cmd = (
            f"export OMPI_MCA_btl=vader,self 2>/dev/null; "
            f"source {env_quoted} 2>/dev/null; "
            f"export OMP_NUM_THREADS={max(n_threads - 1, 1)}; "
            f"cd {linux_case} && {bin_quoted}"
        )
        if extra_args:
            env_cmd += " " + " ".join(shlex.quote(a) for a in extra_args)
        return self._build_wsl_cmd(env_cmd)

    def build_parallel_command(
        self, case_dir: Path | str, n_cores: int,
        method: str = "scotch",
    ) -> list[str]:
        """Build WSL command for MPI-parallel cartesianMesh.

        Writes a shell script to the case dir and executes it.  The script
        runs cartesianMesh -parallel inside WSL2's native Linux tmpfs
        (/tmp/) to avoid the Windows filesystem (/mnt/c/) which does not
        support shared memory, file locking, and mmap that OpenMPI needs
        — these cause SIGSEGV (exit 139) on WSL2.

        Using a shell script avoids quoting/glob issues with inline bash
        commands passed via ``wsl.exe bash -lc "..."``.
        """
        case_dir_resolved = Path(case_dir).resolve()
        linux_case = self.wsl_linux_case_path(case_dir_resolved)  # unquoted for script
        env_q = self.env_script
        bin_q = self.cartesian_mesh_bin
        n_threads = os.cpu_count() or 4

        script = (
            f"#!/bin/bash\n"
            f"set -o pipefail\n"
            f"source {env_q} 2>/dev/null\n"
            f"export OMP_NUM_THREADS={max(n_threads - 1, 1)}\n"
            # OpenMPI's negation operator only applies once, at the start
            # of the whole list — "^openib,^openfabric,^uct" is invalid
            # ("MCA framework parameters can only take a single negation
            # operator") and made MPI_Init fail outright, confirmed live.
            # Matches the correct single-prefix form already used
            # elsewhere in this file (build_check_mesh_cmd etc).
            f"export OMPI_MCA_btl=^openib,openfabric,uct\n"
            f"SRC=\"{linux_case}\"\n"
            f"TMPD=$(mktemp -d /tmp/cfmesh_parallel_XXXXX)\n"
            f"mkdir -p $TMPD/constant/triSurface $TMPD/system\n"
            f'cp "$SRC/system/"* "$TMPD/system/" 2>/dev/null\n'
            f'cp "$SRC/constant/triSurface/"* "$TMPD/constant/triSurface/" 2>/dev/null\n'
            f"cd $TMPD\n"
            # write decomposeParDict
            f"cat > system/decomposeParDict << 'EOF'\n"
            f"FoamFile {{ version 2.0; format ascii; class dictionary; object decomposeParDict; }}\n"
            f"numberOfSubdomains {n_cores};\n"
            f"method {method};\n"
            f"{method}Coeffs {{ preservePatches (boundary); }}\n"
            f"EOF\n"
            # cartesianMesh -parallel needs each processorN/ to already
            # exist with its own copy of system/ + constant/ (confirmed
            # multiple times this session: "cannot open case directory
            # processorN" without this) — ParallelMeshEngine's own
            # _step_create_processor_dirs() creates these, but on the
            # WINDOWS side of case_dir (/mnt/c/...), which this script
            # never looks at: it only copies system/+triSurface/ once
            # into $TMPD's ROOT, not per-rank. Confirmed live: without
            # this loop, cartesianMesh -parallel failed immediately with
            # "cannot open case directory /tmp/.../processor0" since
            # nothing had ever created it inside $TMPD.
            f"for i in $(seq 0 {n_cores - 1}); do\n"
            f"  mkdir -p $TMPD/processor$i\n"
            f"  cp -r $TMPD/system $TMPD/processor$i/\n"
            f"  cp -r $TMPD/constant $TMPD/processor$i/\n"
            f"done\n"
            # run MPI-parallel meshing
            f"mpirun --allow-run-as-root --oversubscribe "
            f"-np {n_cores} {bin_q} -parallel 2>&1 | "
            f"tee $TMPD/parallel_mesh.log | tail -30\n"
            f"RC1=${{PIPESTATUS[0]}}\n"
            # reconstruct
            f"if [ $RC1 -eq 0 ]; then\n"
            f"  reconstructParMesh -constant 2>&1 | "
            f"tee -a $TMPD/parallel_mesh.log | tail -15\n"
            f"  RC2=${{PIPESTATUS[0]}}\n"
            f"else\n"
            f"  RC2=$RC1\n"
            f"fi\n"
            # copy result back
            f'cp -r "$TMPD/constant/polyMesh" "$SRC/constant/" 2>/dev/null\n'
            f'cp "$TMPD/parallel_mesh.log" "$SRC/" 2>/dev/null\n'
            # clean up
            f"rm -rf $TMPD\n"
            f"exit $RC2\n"
        )

        # Write script to case dir and execute it. newline="" is required
        # on Windows: Path.write_text() otherwise translates every "\n" to
        # "\r\n", which corrupts the "#!/bin/bash" shebang into
        # "#!/bin/bash\r" — Linux then looks for an interpreter literally
        # named "/bin/bash\r", which doesn't exist, and the whole script
        # fails with exit 127 "command not found" (confirmed live: hex-
        # dumped the written file and found 0d0a right after the shebang).
        script_path = case_dir_resolved / "system" / "_run_parallel.sh"
        script_path.write_text(script, encoding="ascii", newline="")
        linux_script = self.wsl_linux_case_path(script_path)

        cmd = (
            f"set -o pipefail; "
            f"source {shlex.quote(self.env_script)} 2>/dev/null; "
            f"chmod +x {shlex.quote(linux_script)} && "
            f"{shlex.quote(linux_script)} 2>&1 | tail -50"
        )
        return self._build_wsl_cmd(cmd)

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

    def build_decompose_par_cmd(self, case_dir: Path | str, n_cores: int,
                                 method: str = "scotch") -> list[str]:
        """Build WSL command to decompose an existing mesh for parallel solving.
        Writes a temporary decomposeParDict, runs decomposePar -force, and
        cleans up — leaving the original mesh intact plus processorN/ dirs."""
        case_dir = Path(case_dir).resolve()
        linux_case = self._quoted_linux_path(case_dir)
        env_q = shlex.quote(self.env_script)
        cmd = (
            f"set -o pipefail; "
            f"source {env_q} 2>/dev/null; "
            f"cd {linux_case} && "
            f"echo 'FoamFile {{ version 2.0; format ascii; "
            f"class dictionary; object decomposeParDict; }}' > "
            f"system/decomposeParDict && "
            f"echo 'numberOfSubdomains {n_cores};' >> "
            f"system/decomposeParDict && "
            f"echo 'method {method};' >> "
            f"system/decomposeParDict && "
            f"echo 'scotchCoeffs {{ preservePatches (boundary); }}' >> "
            f"system/decomposeParDict && "
            f"decomposePar -force 2>&1 | tail -20"
        )
        return self._build_wsl_cmd(cmd)

    def build_poly_dual_cmd(self, case_dir: Path | str) -> list[str]:
        case_dir = Path(case_dir).resolve()
        linux_case = self._quoted_linux_path(case_dir)
        env_quoted = shlex.quote(self.env_script)
        n_threads = os.cpu_count() or 4
        cmd = (
            f"set -o pipefail; "
            f"export OMPI_MCA_btl=^openib,openfabric,uct 2>/dev/null; "
            f"source {env_quoted} 2>/dev/null; "
            f"export OMP_NUM_THREADS={max(n_threads - 1, 1)}; "
            f"cd {linux_case}; "
            f"polyDualMesh -constant 2>&1 | tail -20"
        )
        return self._build_wsl_cmd(cmd)
