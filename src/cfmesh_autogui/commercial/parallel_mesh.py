"""Parallel meshing engine using MPI + cartesianMesh's own -parallel mode.

Runs cartesianMesh across N MPI ranks and reconstructs the result — real
wall-clock speedup on multi-core machines, verified live (a case that takes
~7s serial completed in ~18s of wall time across 4 ranks doing ~52s of
combined CPU work).

This is NOT decomposePar-based domain decomposition: decomposePar splits an
EXISTING mesh/fields for parallel SOLVING, and requires a mesh to already
exist — at this point in the pipeline there is no mesh yet, since generating
one is the whole point. cartesianMesh's own `-parallel` flag does the
geometry-based decomposition internally; it only needs each processorN/
directory to exist with its own copy of the case's system/ and constant/
input files (confirmed live: without them, cartesianMesh -parallel fails
with "cannot open case directory processorN").

Pipeline:
  1. Write decomposeParDict (cartesianMesh -parallel reads
     numberOfSubdomains from it)
  2. Create processorN/ directories, each with system/ + constant/ copied in
  3. mpirun -np N cartesianMesh -parallel
  4. reconstructParMesh -constant to merge the per-rank meshes back into
     constant/polyMesh
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.validation import validate_cell_size, validate_case_dir
from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)

# Decomposition methods supported by OpenFOAM
DECOMP_METHODS = ("scotch", "metis", "simple", "hierarchical")


@dataclass
class DecomposeParams:
    """Parameters for domain decomposition."""
    method: str = "scotch"
    n_cores: int = 4
    preserve_patches: list[str] = field(default_factory=lambda: ["boundary"])
    overlap: int = 0  # Overlap for parallel mesh stitching


@dataclass
class ParallelMeshResult:
    success: bool = False
    case_dir: str = ""
    n_cores: int = 0
    cell_count: int = 0
    cell_count_per_rank: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    wall_time_seconds: float = 0.0


class ParallelMeshEngine:
    """Parallel meshing engine using MPI + cartesianMesh's own -parallel mode.

    Usage::

        pe = ParallelMeshEngine(of_config)
        pe.setup_case(case_dir, n_cores=8)
        result = pe.run()
    """

    # Max cores supported (practical limit for WSL2)
    MAX_CORES = 128
    MIN_CORES = 2
    # 2 hours used to be the ceiling before this failed with an exception at
    # all — a real hang (see _kill_stray_processes) looked exactly like an
    # indefinite freeze/crash for that whole time. 30 minutes is still
    # generous for interactive use; a genuinely large mesh that needs
    # longer should go through the serial path instead.
    _TIMEOUT_S = 1800

    def __init__(self, of_config: OFConfig | None = None) -> None:
        self._of_config = of_config or OFConfig()
        self._case_dir: Path | None = None
        self._params = DecomposeParams()
        self._max_cell: float = 0.05
        self._min_cell: float = 0.01
        self._patch_names: list[str] | None = None
        self._result = ParallelMeshResult()

    def setup_case(
        self, case_dir: Path | str, n_cores: int = 4,
        method: str = "scotch",
    ) -> None:
        """Configure the case and decomposition parameters.

        Args:
            case_dir: OpenFOAM case directory (must exist).
            n_cores: Number of subdomains (2–128).
            method: Decomposition method (scotch/metis/simple/hierarchical).
        """
        result = validate_case_dir(case_dir)
        if not result.valid:
            raise ValueError(result.message)

        self._case_dir = Path(case_dir)
        if not self._case_dir.exists():
            raise FileNotFoundError(f"Case directory not found: {case_dir}")

        if n_cores < self.MIN_CORES or n_cores > self.MAX_CORES:
            raise ValueError(f"n_cores must be between {self.MIN_CORES} and {self.MAX_CORES}")

        if method not in DECOMP_METHODS:
            raise ValueError(f"Method must be one of {DECOMP_METHODS}, got {method!r}")

        self._params.n_cores = n_cores
        self._params.method = method

    def set_cell_sizes(self, max_cell: float, min_cell: float) -> None:
        result = validate_cell_size(max_cell, min_cell)
        if not result.valid:
            raise ValueError(result.message)
        self._max_cell = max_cell
        self._min_cell = min_cell

    def set_patch_names(self, patch_names: list[str] | None) -> None:
        """Patch names for renameBoundary — without these, every patch
        reverts to cfMesh's default `wall` type (same bug class fixed
        across write_meshdict() callers elsewhere this session)."""
        self._patch_names = patch_names

    def _kill_stray_processes(self) -> None:
        """Best-effort: kill any mpirun/cartesianMesh still running inside
        the WSL2 VM from a previous timed-out/hung attempt.

        Killing the Windows-side wsl.exe process on a Python-level timeout
        does not kill what it spawned inside WSL2 — the VM is a persistent
        session, not a 1:1 child of that invocation — so a run that hung
        once leaves orphaned ranks consuming CPU/RAM, and the *next*
        attempt starts even more starved than the first.
        """
        try:
            cmd = self._of_config._build_wsl_cmd(
                "pkill -9 -f cartesianMesh 2>/dev/null; "
                "pkill -9 -f mpirun 2>/dev/null; true"
            )
            subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except Exception as exc:
            logger.debug("Stray-process cleanup skipped: %s", exc)

    def _estimate_per_rank_mb(self) -> int:
        """Rough per-rank memory estimate: each MPI rank loads and octree-
        processes the *full* surface independently before discarding
        non-owned cells, so memory scales with both surface complexity and
        core count. There's no exact formula for this without actually
        running it, so this is a deliberately conservative heuristic (20x
        the on-disk triSurface size, which tends to undershoot rather than
        overshoot real octree memory use, plus a flat base overhead) rather
        than a precise prediction — good enough to catch "this will
        obviously starve WSL2" before committing to it, not a hard guarantee.
        """
        base_mb = 200
        tri_dir = (self._case_dir / "constant" / "triSurface") if self._case_dir else None
        if not tri_dir or not tri_dir.is_dir():
            return base_mb
        total_bytes = sum(f.stat().st_size for f in tri_dir.glob("*") if f.is_file())
        surface_mb = total_bytes / (1024 * 1024)
        return int(base_mb + surface_mb * 20)

    def _clamp_cores_to_available_memory(self) -> None:
        """Query WSL2's currently free memory and reduce n_cores if the
        requested count would very likely exhaust it — this is the fix for
        the actual root cause behind repeated "parallel meshing hangs/
        crashes" reports: each rank redundantly loads the full geometry,
        WSL2 caps its own memory well below the host's, and running out
        mid-mesh reads as an indefinite freeze rather than a clean error.
        Runs *before* committing to a core count rather than discovering
        the problem 20 minutes into a run.
        """
        try:
            # awk's `$7` through the Windows -> wsl.exe -> bash -lc bridge
            # is not reliable — confirmed live: even a trivial
            # `awk '{print $7}'` came back with the whole input line
            # instead of one field, silently (int() on that line then
            # raised ValueError, caught below, and the clamp just never
            # applied — the exact bug this method exists to prevent kept
            # happening because the safety check itself was silently
            # broken). Parsing plain `free -m` text in Python sidesteps
            # the quoting problem entirely.
            cmd = self._of_config._build_wsl_cmd("free -m")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            available_mb = None
            for line in result.stdout.splitlines():
                if line.startswith("Mem:"):
                    fields = line.split()
                    # total used free shared buff/cache available
                    available_mb = int(fields[6])
                    break
            if available_mb is None:
                raise ValueError(f"Could not parse 'free -m' output: {result.stdout!r}")
        except Exception as exc:
            logger.warning(
                "Could not determine WSL2 available memory (%s) — "
                "leaving core count at %d as requested; if that starves "
                "WSL2's memory this run may hang.",
                exc, self._params.n_cores,
            )
            return

        per_rank_mb = self._estimate_per_rank_mb()
        # Leave a 30% safety margin rather than using every last free MB.
        safe_n = max(self.MIN_CORES, int((available_mb * 0.7) // per_rank_mb))
        if safe_n < self._params.n_cores:
            msg = (
                f"Reduced parallel cores from {self._params.n_cores} to "
                f"{safe_n}: WSL2 has ~{available_mb} MB available and each "
                f"rank is estimated at ~{per_rank_mb} MB for this geometry — "
                f"running the requested count would likely have exhausted "
                f"WSL2's memory and hung."
            )
            logger.warning(msg)
            self._result.warnings.append(msg)
            self._params.n_cores = safe_n

    def run(self) -> ParallelMeshResult:
        """Execute the parallel meshing workflow.

        Steps:
          1. Create processorN/ directories (system/ + constant/ per rank)
          2. Run cartesianMesh -parallel across n_cores MPI ranks
          3. Reconstruct with reconstructParMesh into constant/polyMesh
        """
        start = datetime.now()
        self._result = ParallelMeshResult(n_cores=self._params.n_cores)
        octo.log_event("parallel_mesh", "workflow_start", {
            "case_dir": str(self._case_dir),
            "n_cores": self._params.n_cores,
            "method": self._params.method,
        })

        try:
            if not self._case_dir:
                raise RuntimeError("No case directory. Call setup_case() first.")

            # A previous run that hung or timed out may have left mpirun/
            # cartesianMesh processes alive *inside* the WSL2 VM: killing
            # the Windows-side wsl.exe launcher on a timeout does not
            # reliably kill what it spawned in the (persistent) Linux VM.
            # Starting a new run on top of orphaned ones compounds the
            # memory pressure that likely caused the original hang —
            # clean up first.
            self._kill_stray_processes()
            self._clamp_cores_to_available_memory()
            self._result.n_cores = self._params.n_cores

            self._write_meshdict()
            self._write_decompose_par_dict()
            self._step_create_processor_dirs()
            self._step_parallel_mesh()
            self._step_reconstruct()

            from cfmesh_autogui.core.boundary_reader import count_cells
            self._result.cell_count = count_cells(self._case_dir)

            self._result.success = True
            octo.log_event("parallel_mesh", "workflow_complete", {
                "cell_count": self._result.cell_count,
            })
        except subprocess.TimeoutExpired:
            # Each MPI rank redundantly loads the full surface and does its
            # own octree pass over it before discarding non-owned cells —
            # so memory/CPU use scales with n_cores, and WSL2's VM (often
            # capped well below host RAM) can start thrashing or hit its
            # OOM killer on a big real geometry with many ranks, which
            # reads as an indefinite hang rather than a clean failure.
            self._kill_stray_processes()
            msg = (
                f"Parallel meshing timed out after {self._TIMEOUT_S // 60} "
                f"minutes across {self._params.n_cores} cores. This usually "
                "means WSL2 ran out of memory for that many ranks on this "
                "geometry — try fewer cores, or disable parallel meshing "
                "for this case."
            )
            self._result.errors.append(msg)
            logger.error(msg)
            octo.log_event("parallel_mesh", "workflow_timeout", {"n_cores": self._params.n_cores})
        except Exception as exc:
            self._result.errors.append(str(exc))
            logger.exception("Parallel meshing failed")
            octo.log_event("parallel_mesh", "workflow_failed", {"error": str(exc)})

        elapsed = (datetime.now() - start).total_seconds()
        self._result.wall_time_seconds = elapsed
        return self._result

    # ------------------------------------------------------------------
    # Internal steps
    # ------------------------------------------------------------------
    def _write_meshdict(self) -> None:
        """Write system/meshDict from set_cell_sizes()'s values.

        This class used to validate and store cell sizes via
        set_cell_sizes() but never actually wrote them into a meshDict at
        all — every call ran cartesianMesh -parallel against whatever (or
        no) meshDict happened to already be on disk. Verified live: on a
        case with no pre-existing meshDict, run() silently "succeeded" in
        ~1s with 0 cells produced.
        """
        if not self._case_dir:
            return
        from cfmesh_autogui.core.meshdict_gen import write_meshdict
        write_meshdict(
            self._case_dir, self._max_cell, self._min_cell,
            patch_names=self._patch_names,
        )
        logger.info(
            "meshDict written: max=%s min=%s", self._max_cell, self._min_cell,
        )

    def _write_decompose_par_dict(self) -> None:
        """Write system/decomposeParDict with the selected method."""
        if not self._case_dir:
            return
        system_dir = self._case_dir / "system"
        system_dir.mkdir(parents=True, exist_ok=True)

        preserve = " ".join(
            f'"{p}"' for p in self._params.preserve_patches
        ) if self._params.preserve_patches else '"boundary"'

        content = (
            "FoamFile { version 2.0; format ascii; "
            "class dictionary; object decomposeParDict; }\n"
            f"\nnumberOfSubdomains {self._params.n_cores};\n"
            f"\nmethod          {self._params.method};\n"
            f"\n{self._params.method}Coeffs {{\n"
            f"    preservePatches ({preserve});\n"
            f"}}\n"
        )
        (system_dir / "decomposeParDict").write_text(content, encoding="ascii")
        logger.info(
            "decomposeParDict: %d cores, method=%s",
            self._params.n_cores, self._params.method,
        )

    def _step_create_processor_dirs(self) -> None:
        """Create processorN/ directories for parallel MESH GENERATION.

        This used to run `decomposePar -force` here — wrong tool for the
        job. decomposePar decomposes an EXISTING mesh/fields for parallel
        SOLVING; at this point in the pipeline there is no mesh yet (that's
        the whole point of running cartesianMesh next), so it always failed:
        "FOAM FATAL ERROR: Cannot find file 'points' in directory 'polyMesh'"
        — confirmed live against real OpenFOAM 2512.

        cartesianMesh's own `-parallel` mode does the geometry-based domain
        decomposition internally; the only prerequisite (also verified
        live — cartesianMesh -parallel fails with "cannot open case
        directory processorN" without this) is that each processorN/
        directory exists with its own copy of system/ and constant/
        (controlDict, meshDict, decomposeParDict, triSurface/*) — the same
        input files a serial run would read from the case root, just
        replicated per rank.
        """
        if not self._case_dir:
            return
        import shutil

        for i in range(self._params.n_cores):
            proc_dir = self._case_dir / f"processor{i}"
            if proc_dir.exists():
                shutil.rmtree(proc_dir)
            proc_dir.mkdir(parents=True)
            shutil.copytree(self._case_dir / "system", proc_dir / "system")
            shutil.copytree(self._case_dir / "constant", proc_dir / "constant")
        logger.info("Created %d processor directories", self._params.n_cores)

    def _step_parallel_mesh(self) -> None:
        """Run cartesianMesh in parallel via MPI on all subdomains."""
        if not self._case_dir:
            return
        import shlex

        case_dir = self._case_dir
        linux_case = self._of_config._quoted_linux_path(case_dir)
        env_q = self._of_config._quoted_linux_path(self._of_config.env_script)
        # _quoted_linux_path() is for FILE PATHS — it treats any string not
        # starting with "/" as a relative Windows path and resolves it
        # against the current working directory. cartesian_mesh_bin is a
        # plain command name ("cartesianMesh"), not a path: this silently
        # turned it into a bogus absolute path
        # (".../cfmesh-autogui/cartesianMesh"), so every mpirun invocation
        # tried to exec a file that doesn't exist — verified live: "success"
        # reported in <1s with 0 cells produced, no exception raised because
        # the nonzero exit code fell through the "partial mesh" tolerance
        # path. config.py's own build_command() already does this correctly
        # with a plain shlex.quote(); matching that here.
        bin_q = shlex.quote(self._of_config.cartesian_mesh_bin)
        n = self._params.n_cores

        # MPI command: mpirun -np N cartesianMesh -parallel
        cmd = self._of_config._build_wsl_cmd(
            f"source {env_q} 2>/dev/null; cd {linux_case} && "
            f"mpirun --allow-run-as-root -np {n} {bin_q} -parallel 2>&1 | tail -20"
        )
        logger.info("Parallel mesh on %d cores...", n)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=self._TIMEOUT_S)

        if result.returncode == 0:
            logger.info("Parallel meshing OK")
        else:
            # Exit code 15 may still produce a partial mesh
            points_exist = (
                self._case_dir / "constant" / "polyMesh" / "points"
            ).exists()
            if not points_exist:
                for i in range(n):
                    proc_points = (
                        self._case_dir / f"processor{i}" / "constant" / "polyMesh" / "points"
                    )
                    if proc_points.exists():
                        points_exist = True
                        break
            if not points_exist:
                raise RuntimeError(
                    f"Parallel cartesianMesh failed (exit {result.returncode}). "
                    f"No polyMesh found.\n{result.stderr[-500:]}"
                )
            logger.warning(
                "Parallel meshing exit=%d but partial mesh may exist",
                result.returncode,
            )
            self._result.warnings.append(
                f"cartesianMesh returned exit code {result.returncode}"
            )

    def _step_reconstruct(self) -> None:
        """Run reconstructParMesh to merge subdomain meshes."""
        if not self._case_dir:
            return
        case_dir = self._case_dir
        linux_case = self._of_config._quoted_linux_path(case_dir)
        env_q = self._of_config._quoted_linux_path(self._of_config.env_script)

        # -merge is not a real reconstructParMesh option (confirmed via
        # `reconstructParMesh -help-full`: "Invalid option: -merge") — it
        # always failed before even attempting reconstruction.
        cmd = self._of_config._build_wsl_cmd(
            f"source {env_q} 2>/dev/null; cd {linux_case} && "
            f"reconstructParMesh -constant 2>&1 | tail -10"
        )
        logger.info("Reconstructing parallel mesh...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(
                f"reconstructParMesh failed (exit {result.returncode}):\n{result.stderr[-300:]}"
            )
        logger.info("reconstructParMesh OK")

    @staticmethod
    def estimate_speedup(n_cores: int, efficiency: float = 0.85) -> dict[str, float]:
        """Estimate parallel speedup using Amdahl's law.

        Args:
            n_cores: Number of parallel cores.
            efficiency: Parallel efficiency (0.0–1.0), default 0.85.

        Returns:
            Dict with ``speedup``, ``efficiency``, ``serial_fraction``.
        """
        serial_frac = 0.05  # 5% serial (I/O, decomposition, stitching)
        speedup = 1.0 / (serial_frac + (1 - serial_frac) / n_cores * (1 / efficiency))
        return {
            "speedup": round(speedup, 2),
            "efficiency": round(speedup / n_cores, 3),
            "serial_fraction": serial_frac,
        }
