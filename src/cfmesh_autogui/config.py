from __future__ import annotations

import logging
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_poly_mesh_archive(case_dir: Path | str) -> bool:
    """Extract ``constant/polyMesh.tar.gz`` → ``constant/polyMesh`` (Windows side).

    The tmpfs bash scripts (``build_parallel_command`` and friends) now copy
    the mesh back as a single compressed archive (``tar -czf``) instead of
    ``cp -r`` of thousands of small files over the slow 9P ``/mnt/c`` bridge.
    This helper is where that archive is unpacked, replacing the old direct
    read of ``constant/polyMesh``.

    Returns True if an archive was found and extracted; False when there was
    nothing to do (no archive present — e.g. a non-tmpfs path).
    """
    import shutil
    import tarfile

    case_dir = Path(case_dir)
    archive = case_dir / "constant" / "polyMesh.tar.gz"
    if not archive.exists():
        return False
    dest = case_dir / "constant"
    poly_dir = dest / "polyMesh"
    # The archive is authoritative (fresh tmpfs output): drop any stale
    # polyMesh so a previous run's files cannot linger and merge.
    shutil.rmtree(poly_dir, ignore_errors=True)
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf.getmembers():
            name = member.name.lstrip("./")
            target = (dest / name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                logger.warning(
                    "extract_poly_mesh_archive: skipping unsafe member %r",
                    member.name,
                )
                continue
            member.name = name
            tf.extract(member, dest)
    archive.unlink(missing_ok=True)
    return True


@dataclass
class OFConfig:
    wsl_distro: str = "Ubuntu"
    env_script: str = "/usr/lib/openfoam/openfoam2512/etc/bashrc"
    cartesian_mesh_bin: str = "cartesianMesh"
    check_mesh_bin: str = "checkMesh"
    _validated: bool = False
    _n_physical: int | None = None  # cached cpu_count for parallel cmd
    _openmp_threads: int | None = None  # override OpenMP thread count (None = auto)

    def set_openmp_threads(self, n: int | None) -> None:
        """Override the OpenMP thread count used in commands.
        Pass None to restore auto-detection via OpenMPAccel."""
        self._openmp_threads = n

    def _openmp_env_prefix(self, cell_estimate: int = 0, mpi_ranks: int = 0) -> str:
        if self._openmp_threads is not None:
            return (
                f"export OMP_NUM_THREADS={self._openmp_threads}; "
                f"export OMP_PROC_BIND=spread; "
                f"export OMP_PLACES=cores;"
            )
        from cfmesh_autogui.commercial.openmp_accel import OpenMPAccel
        return OpenMPAccel.build_env_prefix(
            cell_estimate=cell_estimate, mode="balanced", mpi_ranks=mpi_ranks,
        )

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
        drive_letter = case_dir.drive[:1].lower() if case_dir.drive else ""
        if not drive_letter or not case_dir.drive.endswith(":"):
            # UNC or relative path without drive — use full path translation
            return f"/mnt/wsl/{raw.replace(chr(92), '/')}".replace("'", "'\\''")
        rel = str(case_dir).split(":", 1)[1].replace("\\", "/")
        return f"/mnt/{drive_letter}{rel}".replace("'", "'\\''")

    def _build_wsl_cmd(self, bash_code: str) -> list[str]:
        return [
            "wsl.exe", "-d", self.wsl_distro, "--",
            "bash", "-lc", bash_code,
        ]

    def _quoted_linux_path(self, case_dir: Path | str) -> str:
        return shlex.quote(self.wsl_linux_case_path(case_dir))

    def build_command(self, case_dir: Path | str, extra_args: list[str] | None = None,
                      cell_estimate: int = 0) -> list[str]:
        case_dir = Path(case_dir).resolve()
        linux_case = self._quoted_linux_path(case_dir)
        env_quoted = shlex.quote(self.env_script)
        bin_quoted = shlex.quote(self.cartesian_mesh_bin)

        env_cmd = (
            f"export OMPI_MCA_btl=vader,self 2>/dev/null; "
            f"source {env_quoted} 2>/dev/null; "
            f"{self._openmp_env_prefix(cell_estimate=cell_estimate)} "
            f"stdbuf -oL -eL bash -c 'cd {linux_case} && exec {bin_quoted}'"
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

        script = (
            f"#!/bin/bash\n"
            f"set -o pipefail\n"
            f"source {env_q} 2>/dev/null\n"
            f"{self._openmp_env_prefix(mpi_ranks=n_cores)}\n"
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
            # live in this session: "cannot open case directory
            # processorN" without this). The old _step_create_processor_dirs()
            # created these on the WINDOWS side (/mnt/c/...) which this
            # script never reads, so the per-rank creation was moved here
            # inside the native tmpfs where the actual MPI run happens.
            f"for i in $(seq 0 {n_cores - 1}); do\n"
            f"  mkdir -p $TMPD/processor$i\n"
            f"  cp -r $TMPD/system $TMPD/processor$i/\n"
            f"  cp -r $TMPD/constant $TMPD/processor$i/\n"
            f"done\n"
            # run MPI-parallel meshing — stdbuf -oL forces line-buffering so
            # every cartesianMesh line appears in the GUI log immediately
            # instead of being held until the 4K pipe buffer fills (the old
            # `| tail -200` did exactly that: tail buffers until EOF).
            f"stdbuf -oL -eL mpirun --allow-run-as-root --oversubscribe "
            f"-np {n_cores} {bin_q} -parallel 2>&1 | "
            f"stdbuf -oL tee $TMPD/parallel_mesh.log\n"
            f"RC1=${{PIPESTATUS[0]}}\n"
            # reconstruct
            f"if [ $RC1 -eq 0 ]; then\n"
            f"  stdbuf -oL -eL reconstructParMesh -constant 2>&1 | "
            f"stdbuf -oL tee -a $TMPD/parallel_mesh.log\n"
            f"  RC2=${{PIPESTATUS[0]}}\n"
            f"else\n"
            f"  RC2=$RC1\n"
            f"fi\n"
            # copy result back
            # Save per-rank cell counts from processorN/ dirs before cleanup.
            # The per-rank owner files have NO "note" with nCells (confirmed
            # live: only the merged polyMesh has it), so we count cells by
            # scanning the owner data: skip header+N+( lines, then find the
            # max cell index and print +1.
            f'for i in $(seq 0 {n_cores - 1}); do\n'
            f'  f="$TMPD/processor$i/constant/polyMesh/owner"\n'
            f'  if [ -f "$f" ]; then\n'
            f'    awk \'BEGIN{{p=0}} /^\\($/ {{p=1; next}} p && /^[0-9]+$/ && $1+0>m {{m=$1+0}} END {{print m+1}}\' "$f"\n'
            f'  else\n'
            f'    echo "0"\n'
            f'  fi\n'
            f'done > "$SRC/per_rank_cells.txt" 2>/dev/null\n'
            # Copy the result back as ONE compressed archive instead of
            # `cp -r` of thousands of small polyMesh files over the slow 9P
            # /mnt/c bridge (measured 5-10x faster). The Windows side unpacks
            # it via config.extract_poly_mesh_archive().
            f'if [ -d "$TMPD/constant/polyMesh" ]; then\n'
            f'  tar -C "$TMPD/constant" -czf "$SRC/constant/polyMesh.tar.gz" polyMesh\n'
            f'fi\n'
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

        # Use "bash script.sh" instead of "chmod +x && ./script.sh"
        # because chmod +x doesn't work on WSL2's /mnt/c/ filesystem
        # (Windows filesystem has no executable bit support).
        # No `| tail -200` here — that buffers until EOF and kills live
        # log streaming. The full output is streamed line-by-line to the
        # GUI via MeshWorker/TaskManager; the log file is preserved separately.
        cmd = (
            f"set -o pipefail; "
            f"source {shlex.quote(self.env_script)} 2>/dev/null; "
            f"stdbuf -oL -eL bash {shlex.quote(linux_script)} 2>&1"
        )

        return self._build_wsl_cmd(cmd)

    def build_staged_pipeline_command(
        self, case_dir: Path | str,
        steps: list[str],
        stage_root: str = "~/cfmesh_cases",
    ) -> list[str]:
        """Run a sequence of OpenFOAM/cfMesh steps inside WSL native storage.

        Today only cartesianMesh runs on tmpfs; STL export, gmshToFoam,
        polyDual and checkMesh still hammer the slow 9P ``/mnt/c`` bridge
        (~3-5x slower than native Linux storage). This stages the case under
        ``~/cfmesh_cases/<ts>/``, runs the requested steps sequentially THERE,
        then copies back ONLY the final artifacts as one compressed archive
        (``tar -czf``), unpacked on the Windows side via
        ``config.extract_poly_mesh_archive()``.

        Args:
            case_dir: Windows case directory (source of inputs / target of
                outputs).
            steps: shell commands to run sequentially in the staged case
                (e.g. ``["cartesianMesh", "polyDualMesh 90 -overwrite"]``).
            stage_root: WSL directory under which timestamped cases are made.

        Returns:
            The ``wsl.exe`` command list (run via subprocess).
        """
        case_dir_resolved = Path(case_dir).resolve()
        linux_case = self.wsl_linux_case_path(case_dir_resolved)
        env_q = self.env_script

        step_lines: list[str] = []
        for i, step in enumerate(steps):
            step_lines.append(f"echo '=== step {i}: {step} ==='\n")
            step_lines.append(
                f"stdbuf -oL -eL {step} 2>&1 | stdbuf -oL tee -a $STAGE/pipeline.log\n"
            )
            step_lines.append("RC=${PIPESTATUS[0]}\n")
            step_lines.append(
                "if [ $RC -ne 0 ]; then echo 'STEP FAILED rc='$RC; break; fi\n"
            )

        script = (
            f"#!/bin/bash\n"
            f"set -o pipefail\n"
            f"source {env_q} 2>/dev/null\n"
            f"{self._openmp_env_prefix()}\n"
            f"export OMPI_MCA_btl=^openib,openfabric,uct\n"
            f'SRC="{linux_case}"\n'
            f'STAGE_ROOT="{stage_root}"\n'
            f"TS=$(date +%Y%m%d_%H%M%S)\n"
            f"STAGE=$STAGE_ROOT/$TS\n"
            f"mkdir -p $STAGE/constant $STAGE/system\n"
            # 1. stage inputs (whole constant/ so polyDual/checkMesh see the
            #    existing polyMesh too; triSurface is the small common case)
            f'rsync -a "$SRC/system/"* "$STAGE/system/" 2>/dev/null\n'
            f'rsync -a "$SRC/constant/"* "$STAGE/constant/" 2>/dev/null\n'
            f"cd $STAGE\n"
            # 2. run requested steps sequentially
            + "".join(step_lines)
            # 3. copy back ONLY final artifacts, compressed
            + (
                'if [ -d "$STAGE/constant/polyMesh" ]; then\n'
                '  tar -C "$STAGE/constant" -czf "$SRC/constant/polyMesh.tar.gz" polyMesh\n'
                'fi\n'
                'cp "$STAGE/pipeline.log" "$SRC/" 2>/dev/null\n'
                "rm -rf $STAGE\n"
                "exit $RC\n"
            )
        )

        script_path = case_dir_resolved / "system" / "_run_staged_pipeline.sh"
        script_path.write_text(script, encoding="ascii", newline="")
        linux_script = self.wsl_linux_case_path(script_path)

        cmd = (
            f"set -o pipefail; "
            f"source {shlex.quote(self.env_script)} 2>/dev/null; "
            f"stdbuf -oL -eL bash {shlex.quote(linux_script)} 2>&1"
        )
        return self._build_wsl_cmd(cmd)

    def build_serial_tmpfs_command(
        self, case_dir: Path | str, extra_args: list[str] | None = None,
        cell_estimate: int = 0,
    ) -> list[str]:
        """Build WSL command for serial cartesianMesh on native tmpfs.

        Like build_parallel_command but for serial runs: copies the case
        to /tmp/ (native Linux tmpfs) to avoid the ~3-5× I/O penalty of
        /mnt/c/ (Windows filesystem), runs cartesianMesh there, then
        copies the polyMesh result back.
        """
        case_dir_resolved = Path(case_dir).resolve()
        linux_case = self.wsl_linux_case_path(case_dir_resolved)
        env_q = self.env_script
        bin_q = self.cartesian_mesh_bin

        extra = ""
        if extra_args:
            extra = " " + " ".join(shlex.quote(a) for a in extra_args)

        script = (
            f"#!/bin/bash\n"
            f"set -o pipefail\n"
            f"source {env_q} 2>/dev/null\n"
            f"{self._openmp_env_prefix(cell_estimate=cell_estimate)}\n"
            f'SRC="{linux_case}"\n'
            f"TMPD=$(mktemp -d /tmp/cfmesh_serial_XXXXX)\n"
            f"mkdir -p $TMPD/constant/triSurface $TMPD/system\n"
            f'cp "$SRC/system/"* "$TMPD/system/" 2>/dev/null\n'
            f'cp "$SRC/constant/triSurface/"* "$TMPD/constant/triSurface/" 2>/dev/null\n'
            f"cd $TMPD\n"
            f"stdbuf -oL -eL {bin_q}{extra} 2>&1 | stdbuf -oL tee $TMPD/serial_mesh.log\n"
            f"RC=$?\n"
            # copy result back
            f'if [ -d "$TMPD/constant/polyMesh" ]; then\n'
            f'  cp -r "$TMPD/constant/polyMesh" "$SRC/constant/" 2>/dev/null\n'
            f'fi\n'
            f'cp "$TMPD/serial_mesh.log" "$SRC/" 2>/dev/null\n'
            f"rm -rf $TMPD\n"
            f"exit $RC\n"
        )

        script_path = case_dir_resolved / "system" / "_run_serial_tmpfs.sh"
        script_path.write_text(script, encoding="ascii", newline="")
        linux_script = self.wsl_linux_case_path(script_path)

        cmd = (
            f"set -o pipefail; "
            f"source {shlex.quote(self.env_script)} 2>/dev/null; "
            f"stdbuf -oL -eL bash {shlex.quote(linux_script)} 2>&1"
        )

        return self._build_wsl_cmd(cmd)

    def build_check_mesh_cmd(self, case_dir: Path | str) -> list[str]:
        case_dir = Path(case_dir).resolve()
        linux_case = self._quoted_linux_path(case_dir)
        env_quoted = shlex.quote(self.env_script)
        cmd = (
            f"export OMPI_MCA_btl=^openib,openfabric,uct 2>/dev/null; "
            f"source {env_quoted} 2>/dev/null; "
            f"{self._openmp_env_prefix()} "
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

    def build_poly_dual_cmd(
        self, case_dir: Path | str,
        feature_angle: float = 90,
        split_all_faces: bool = False,
    ) -> list[str]:
        """Build WSL command to run polyDualMesh.

        polyDualMesh REQUIRES a featureAngle (positional arg, 0-180).
        The old ``-constant`` flag does NOT exist — it was silently ignored
        (exit 0, but did nothing), so every poly conversion before this fix
        was a no-op.

        polyDualMesh creates the DUAL of the existing hex mesh — this
        INCREASES cell count (each hex vertex becomes a poly cell centre).
        This is NOT the same as STAR-CCM+ direct polyhedral generation.

        For STAR-CCM+ style quality, the key is:
        - Higher featureAngle (90°) produces smoother polyhedral cells
          with lower skewness (1.29 vs 1.44 at 45°)
        - Better meshDict settings (boundaryCellSize, maxNumIterations)
        - Proper boundary layer preservation (already handled by -overwrite)

        Args:
            case_dir: Case directory
            feature_angle: Feature angle in degrees [0-180].
                Higher = smoother cells, better orthogonality.
                90 recommended for best polyhedral quality.
            split_all_faces: Have multiple faces between cells
                (increases cell count, use only for specific needs).
        """
        case_dir = Path(case_dir).resolve()
        linux_case_q = self._quoted_linux_path(case_dir)
        env_quoted = shlex.quote(self.env_script)
        n_threads = os.cpu_count() or 4

        extra = " -splitAllFaces" if split_all_faces else ""

        cmd = (
            f"set -o pipefail; "
            f"export OMPI_MCA_btl=^openib,openfabric,uct 2>/dev/null; "
            f"source {env_quoted} 2>/dev/null; "
            f"{self._openmp_env_prefix()} "
            f"cd {linux_case_q}; "
            f"polyDualMesh {feature_angle} -overwrite{extra} 2>&1 | tail -20"
        )
        return self._build_wsl_cmd(cmd)

    def build_gmsh_to_foam_cmd(self, case_dir: Path | str, msh_filename: str) -> list[str]:
        """Build WSL command to convert a GMSH .msh file to OpenFOAM polyMesh
        via OpenFOAM's own gmshToFoam utility.

        Replaces the app's custom Python meshio-based converter
        (mesh_converter.msh_to_of_polymesh), which turned out to have
        several correctness bugs once real tetrahedra started flowing
        through it (owner/neighbour indexing offset by the surface-element
        count, boundary patches tagged from the wrong entity, and — never
        fully resolved — face-winding/orientation errors producing
        negative-volume cells). gmshToFoam is OpenFOAM's own mature,
        already-shipped converter and produces a clean mesh from the same
        .msh file (verified: 8665/8665 real tetrahedra, only a 0.5%
        residual quality warning, vs. ~40% negative-volume cells from the
        custom converter on the same input).
        """
        case_dir = Path(case_dir).resolve()
        linux_case_q = self._quoted_linux_path(case_dir)
        env_quoted = shlex.quote(self.env_script)
        msh_quoted = shlex.quote(msh_filename)
        cmd = (
            f"source {env_quoted} 2>/dev/null; "
            f"cd {linux_case_q} && gmshToFoam {msh_quoted} 2>&1 | tail -40"
        )
        return self._build_wsl_cmd(cmd)

    def build_surface_orient_cmd(self, case_dir: Path | str, stl_filename: str) -> list[str]:
        """Build WSL command to run surfaceOrient on a triSurface STL,
        making its normals consistently point outward from a point known
        to be inside the volume — foamyHexMesh requires this ("space to
        be meshed always on the inside of all surfaces"); GMSH's own STL
        export doesn't guarantee it. Point (0,0,0) is a placeholder — the
        real inside point is substituted by the caller.
        """
        case_dir = Path(case_dir).resolve()
        linux_case_q = self._quoted_linux_path(case_dir)
        env_quoted = shlex.quote(self.env_script)
        stl_quoted = shlex.quote(f"constant/triSurface/{stl_filename}")
        cmd = (
            f"source {env_quoted} 2>/dev/null; "
            f"cd {linux_case_q} && surfaceOrient {stl_quoted} "
            f"'({{INSIDE_POINT}})' {stl_quoted} -outside 2>&1 | tail -20"
        )
        return self._build_wsl_cmd(cmd)

    def build_foamy_hex_mesh_cmd(self, case_dir: Path | str) -> list[str]:
        """Build WSL command to run foamyHexMesh — OpenFOAM's native
        conformal-Voronoi mesher. Generates genuine polyhedra directly
        from the surface geometry with no tet-mesh + dual-conversion
        step at all, sidestepping polyDualMesh's face-orientation defect
        entirely (confirmed on a real complex part: persists across
        every tet-generation strategy tried — curvature, feature-size,
        gap-aware, budget-driven sizing, both HXT and classic Delaunay —
        so it's a limitation of polyDualMesh itself, not of the input
        tet mesh's quality).
        """
        case_dir = Path(case_dir).resolve()
        linux_case_q = self._quoted_linux_path(case_dir)
        env_quoted = shlex.quote(self.env_script)
        n_threads = os.cpu_count() or 4
        cmd = (
            f"set -o pipefail; "
            f"export OMPI_MCA_btl=^openib,openfabric,uct 2>/dev/null; "
            f"source {env_quoted} 2>/dev/null; "
            f"{self._openmp_env_prefix()} "
            f"cd {linux_case_q} && foamyHexMesh 2>&1 | tail -60"
        )
        return self._build_wsl_cmd(cmd)
