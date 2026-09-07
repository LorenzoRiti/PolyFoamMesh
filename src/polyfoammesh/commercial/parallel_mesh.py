"""Parallel meshing engine using MPI + cartesianMesh's own -parallel mode.

Runs cartesianMesh across N MPI ranks and reconstructs the result — real
wall-clock speedup on multi-core machines.

This is NOT decomposePar-based domain decomposition: decomposePar splits an
EXISTING mesh/fields for parallel SOLVING, and requires a mesh to already
exist — at this point in the pipeline there is no mesh yet, since generating
one is the whole point. cartesianMesh's own `-parallel` flag does the
geometry-based decomposition internally; it only needs each processorN/
directory to exist with its own copy of the case's system/ and constant/
input files (confirmed live: without them, cartesianMesh -parallel fails
with "cannot open case directory processorN").

Pipeline:
   1. Write meshDict (cell sizes, BL, patch names) to the Windows case dir
   2. Clean residual /tmp/cfmesh_parallel_* from previous killed runs
   3. Clean residual processorN/ dirs from previous runs
   4. build_parallel_command() copies system/+constant/ into a native WSL
      tmpfs (/tmp/cfmesh_parallel_XXXXX) and runs mpirun there — this
      avoids SIGSEGV from mmap on /mnt/c (Windows filesystem).
   5. reconstructParMesh inside tmpfs, then copies back constant/polyMesh
      and per_rank_cells.txt
   6. Collect per-rank cell counts from per_rank_cells.txt
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from polyfoammesh.config import OFConfig
from polyfoammesh.core.validation import validate_cell_size, validate_case_dir
from polyfoammesh.octopoda_local import octo

logger = logging.getLogger(__name__)

# Decomposition methods supported by OpenFOAM
DECOMP_METHODS = ("scotch", "metis", "simple", "hierarchical")


class CancelledError(RuntimeError):
    """Raised inside ParallelMeshEngine when cancel() is called mid-run."""


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

    MAX_CORES = 128
    MIN_CORES = 2
    # 4 hour ceiling — allows ~25M cells at ~1800 cells/sec/core with 4 cores.
    # cartesianMesh parallel scaling is sub-linear (geometry decomposition
    # overhead + I/O), so 4 hours is a safe upper bound for 25M cells.
    _TIMEOUT_S = 14400
    # Reconstruct timeout for 25M cells: merging N subdomain meshes is
    # I/O-bound and scales with the largest per-rank mesh size.
    _RECONSTRUCT_TIMEOUT_S = 3600

    def __init__(self, of_config: OFConfig | None = None) -> None:
        self._of_config = of_config or OFConfig()
        self._case_dir: Path | None = None
        self._params = DecomposeParams()
        self._max_cell: float = 0.05
        self._min_cell: float = 0.01
        self._patch_names: list[str] | None = None
        self._bl_params: dict | None = None
        self._result = ParallelMeshResult()
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation of a running meshing operation.
        Sets an internal event that is polled between steps and during
        the blocking subprocess call (via Popen + polling loop).
        Also cleans up stray WSL processes and orphaned tmp dirs.
        """
        self._cancel_event.set()
        self._kill_stray_processes()
        self._clean_old_tmp_dirs()

    def setup_case(
        self, case_dir: Path | str, n_cores: int = 4,
        method: str = "scotch",
    ) -> None:
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
        self._patch_names = patch_names

    def set_bl_params(self, bl_params: dict | None) -> None:
        self._bl_params = bl_params

    def _kill_stray_processes(self) -> None:
        try:
            cmd = self._of_config._build_wsl_cmd(
                "pkill -9 -f cartesianMesh 2>/dev/null; "
                "pkill -9 -f mpirun 2>/dev/null; "
                "pkill -9 -f reconstructParMesh 2>/dev/null; true"
            )
            subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except Exception as exc:
            logger.debug("Stray-process cleanup skipped: %s", exc)

    def _measure_actual_per_rank_mb(self) -> int | None:
        """Measure actual per-rank memory by running a quick WSL test.

        Launches a single cartesianMesh rank in a throwaway tmp dir,
        measures memory delta with psutil, and kills it.
        Returns None if measurement fails (falls back to heuristic).
        """
        if not self._case_dir:
            return None
        try:
            import psutil
            proc = subprocess.Popen(
                self._of_config.build_command(self._case_dir),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            mem_before = psutil.Process(proc.pid).memory_info().rss
            import time
            time.sleep(3)
            proc.kill()
            proc.wait(timeout=5)
            mem_after = psutil.Process(proc.pid).memory_info().rss
            used_mb = max(mem_after - mem_before, 0) / (1024 * 1024)
            if used_mb > 10:
                return int(used_mb * 1.2)
        except Exception as exc:
            logger.debug("Actual memory measurement failed: %s", exc)
        return None

    def _estimate_per_rank_mb(self) -> int:
        base_mb = 300
        tri_dir = (self._case_dir / "constant" / "triSurface") if self._case_dir else None
        if not tri_dir or not tri_dir.is_dir():
            return base_mb

        measured = self._measure_actual_per_rank_mb()
        if measured is not None:
            logger.info("Measured per-rank memory: %d MB", measured)
            return measured

        total_bytes = sum(f.stat().st_size for f in tri_dir.glob("*") if f.is_file())
        surface_mb = total_bytes / (1024 * 1024)
        return int(base_mb + surface_mb * 5)

    def _clamp_cores_to_available_memory(self) -> int:
        """Clamp core count to available WSL2 memory.

        Uses a conservative fraction of available WSL2 RAM to avoid
        OOM kills on large meshes — for big surface files (>100 MB)
        the margin is tightened further.

        Returns the actual number of cores to use (may be less than requested).
        Returns 1 (serial) when there isn't enough memory even for 2 ranks.
        """
        try:
            cmd = self._of_config._build_wsl_cmd("free -m")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            available_mb = None
            for line in result.stdout.splitlines():
                if line.startswith("Mem:"):
                    fields = line.split()
                    available_mb = int(fields[6])
                    break
            if available_mb is None:
                raise ValueError(f"Could not parse 'free -m' output: {result.stdout!r}")
        except Exception as exc:
            logger.warning(
                "Could not determine WSL2 available memory (%s) — "
                "leaving core count at %d as requested.",
                exc, self._params.n_cores,
            )
            return self._params.n_cores

        per_rank_mb = self._estimate_per_rank_mb()
        surface_mb = 0.0
        tri_dir = (self._case_dir / "constant" / "triSurface") if self._case_dir else None
        if tri_dir and tri_dir.is_dir():
            surface_mb = sum(f.stat().st_size for f in tri_dir.glob("*") if f.is_file()) / (1024 * 1024)
        # Tighten margin for large geometries: 40 % for surface > 100 MB, else 45 %
        margin = 0.40 if surface_mb > 100 else 0.45
        safe_n = int((available_mb * margin) // per_rank_mb)
        if safe_n < 2:
            msg = (
                f"Parallel disabled: WSL2 has ~{available_mb} MB available, "
                f"each rank needs ~{per_rank_mb} MB — not enough for 2 ranks. "
                "Falling back to serial."
            )
            logger.warning(msg)
            self._result.warnings.append(msg)
            return 1
        if safe_n < self._params.n_cores:
            msg = (
                f"Reduced parallel cores from {self._params.n_cores} to "
                f"{safe_n}: WSL2 has ~{available_mb} MB available, each "
                f"rank needs ~{per_rank_mb} MB (surface={surface_mb:.0f} MB). "
                f"Using {margin*100:.0f}% of available RAM."
            )
            logger.warning(msg)
            self._result.warnings.append(msg)
        return min(safe_n, self._params.n_cores)

    def run(self) -> ParallelMeshResult:
        """Execute the parallel meshing workflow.

        Steps:
           1. Kill stray MPI processes from previous runs
           2. Clamp core count to available WSL2 memory
           3. Clean orphaned /tmp/cfmesh_parallel_* from killed runs
           4. Clean residual processorN/ dirs from previous runs
           5. Write meshDict (cell sizes, BL, patch names)
           6. Run cartesianMesh -parallel via tmpfs bash script
           7. Collect per-rank cell counts from per_rank_cells.txt
           8. Standalone reconstruct fallback (if bundled one didn't run)
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
            if self._cancel_event.is_set():
                raise CancelledError("Cancelled before start")

            self._kill_stray_processes()
            clamped = self._clamp_cores_to_available_memory()
            if clamped < self.MIN_CORES:
                raise RuntimeError(
                    f"Parallel meshing disabled: WSL2 available memory too low "
                    f"for {self.MIN_CORES}+ ranks."
                )
            self._params.n_cores = clamped
            self._result.n_cores = clamped

            self._clean_old_tmp_dirs()
            self._clean_processor_dirs()
            if self._cancel_event.is_set():
                raise CancelledError("Cancelled before parallel mesh")
            self._step_parallel_mesh()
            if self._cancel_event.is_set():
                raise CancelledError("Cancelled after parallel mesh step")
            self._step_reconstruct()

            from polyfoammesh.config import extract_poly_mesh_archive
            extract_poly_mesh_archive(self._case_dir)
            from polyfoammesh.core.boundary_reader import count_cells
            self._result.cell_count = count_cells(self._case_dir)

            self._result.success = True
            octo.log_event("parallel_mesh", "workflow_complete", {
                "cell_count": self._result.cell_count,
            })
        except subprocess.TimeoutExpired:
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
        if not self._case_dir:
            return
        from polyfoammesh.core.meshdict_gen import write_meshdict
        # Detect FMS feature edges from earlier feature-detect step
        fms_path = self._case_dir / "constant" / "triSurface" / "surface.fms"
        surface_file = "constant/triSurface/surface.fms" if fms_path.exists() else "constant/triSurface/surface.stl"
        write_meshdict(
            self._case_dir, self._max_cell, self._min_cell,
            patch_names=self._patch_names,
            bl_params=self._bl_params,
            surface_file=surface_file,
        )
        logger.info(
            "meshDict written: max=%s min=%s bl=%s surface=%s",
            self._max_cell, self._min_cell,
            "yes" if self._bl_params else "no",
            surface_file,
        )

    def _clean_old_tmp_dirs(self) -> None:
        """Clean up any stray /tmp/cfmesh_parallel_* left by killed runs."""
        cmd = self._of_config._build_wsl_cmd(
            "rm -rf /tmp/cfmesh_parallel_* 2>/dev/null; true"
        )
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except Exception as exc:
            logger.debug("Tmpdir cleanup skipped: %s", exc)

    def _clean_processor_dirs(self) -> None:
        """Remove any leftover processorN/ dirs from previous runs so a
        serial fallback (after parallel failure) starts clean."""
        if not self._case_dir:
            return
        for d in list(self._case_dir.glob("processor*")):
            if d.is_dir():
                shutil.rmtree(d)

    def _run_subprocess_with_cancel(
        self, cmd: list[str], timeout: int,
    ) -> subprocess.CompletedProcess:
        """Run *cmd* as a subprocess with cancellation support.
        Polls the cancel event every 0.5s; kills the full WSL process tree on
        cancel using both taskkill /t (Windows-side) and pkill (WSL-side)."""
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=4096,
        )
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if self._cancel_event.is_set():
                self._kill_stray_processes()
                self._kill_process_tree(process.pid)
                raise CancelledError("Cancelled by user during subprocess")
            if time.monotonic() > deadline:
                self._kill_process_tree(process.pid)
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)
            time.sleep(0.5)
        stdout, stderr = process.communicate()
        return subprocess.CompletedProcess(
            cmd, process.returncode, stdout=stdout, stderr=stderr,
        )

    @staticmethod
    def _kill_process_tree(pid: int) -> None:
        """Kill the full process tree (wsl.exe + children inside WSL).
        On Windows, Popen.kill() only terminates the one wsl.exe process,
        leaving mpirun/cartesianMesh orphaned inside WSL.  taskkill /t walks
        the entire tree."""
        try:
            subprocess.run(
                ["taskkill", "/f", "/t", "/pid", str(pid)],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:
            # kill is best-effort: a dead/already-exited process must not
            # raise out of the cancel path
            logger.debug("parallel_mesh: taskkill failed for pid %s", pid, exc_info=True)

    def _step_parallel_mesh(self) -> None:
        """Run cartesianMesh in parallel via MPI on all subdomains."""
        if not self._case_dir:
            return
        if self._cancel_event.is_set():
            raise CancelledError("Cancelled before parallel mesh")

        case_dir = self._case_dir
        n = self._params.n_cores

        cmd = self._of_config.build_parallel_command(
            case_dir, n, method=self._params.method,
        )

        logger.info("Parallel mesh on %d cores (method=%s)...", n, self._params.method)
        result = self._run_subprocess_with_cancel(cmd, self._TIMEOUT_S)

        # Unpack the compressed copy-back (constant/polyMesh.tar.gz) into
        # constant/polyMesh — the tmpfs script now ships the mesh as one
        # archive instead of `cp -r` over the slow 9P bridge.
        from polyfoammesh.config import extract_poly_mesh_archive
        extract_poly_mesh_archive(case_dir)

        if result.returncode == 0:
            logger.info("Parallel meshing + reconstruct OK")
            self._collect_per_rank_counts()
            return

        self._clean_processor_dirs()
        poly_points = self._case_dir / "constant" / "polyMesh" / "points"
        if poly_points.exists():
            logger.warning(
                "Parallel meshing exit=%d but polyMesh exists (reconstruct may "
                "have partially succeeded after meshing failure)",
                result.returncode,
            )
            self._result.warnings.append(
                f"cartesianMesh returned exit code {result.returncode}"
            )
            return

        log_path = self._case_dir / "parallel_mesh.log"
        detail = ""
        if log_path.exists():
            detail = log_path.read_text(encoding="ascii", errors="replace")[-1000:]
        raise RuntimeError(
            f"Parallel meshing failed (exit {result.returncode}). "
            f"No polyMesh found.  Full log: {log_path}\n"
            f"{detail or result.stderr[-500:]}"
        )

    def _collect_per_rank_counts(self) -> None:
        """Read per-rank cell counts saved by the bash script before cleanup.

        The bash script in build_parallel_command() extracts nCells from
        each processorN/constant/polyMesh/owner's FoamFile header BEFORE
        deleting $TMPD and writes one integer per line to per_rank_cells.txt.
        This avoids both: (a) reading stale data from Windows-side
        processorN/ dirs that never receive mesh data, and (b) needing to
        copy back the entire processorN/ tree just for one integer per rank.
        """
        if not self._case_dir:
            return
        counts_path = self._case_dir / "per_rank_cells.txt"
        if counts_path.exists():
            try:
                lines = counts_path.read_text(encoding="ascii").strip().splitlines()
                counts = [int(line.strip()) for line in lines if line.strip()]
                counts_path.unlink()
            except Exception as exc:
                logger.warning("Failed to read per_rank_cells.txt: %s", exc)
                counts = [0] * self._params.n_cores
        else:
            logger.debug("per_rank_cells.txt not found (parallel mesh may have failed)")
            counts = [0] * self._params.n_cores

        self._result.cell_count_per_rank = counts
        total = sum(counts)
        avg = total / max(len(counts), 1)
        logger.info(
            "Per-rank cells: %s (total=%d, avg=%d)",
            counts, total, avg,
        )

    def _step_reconstruct(self) -> None:
        """Run reconstructParMesh to merge subdomain meshes.

        The bash script in build_parallel_command bundles reconstruct into
        the same invocation.  This standalone fallback is only reached when
        the bundled reconstruct fails or when run() is called directly
        without _step_parallel_mesh.
        """
        if not self._case_dir:
            return
        if self._cancel_event.is_set():
            raise CancelledError("Cancelled before reconstruct")

        poly_points = self._case_dir / "constant" / "polyMesh" / "points"
        if poly_points.exists():
            logger.info("Reconstruct already done (polyMesh/points exists)")
            return

        case_dir = self._case_dir
        linux_case = self._of_config._quoted_linux_path(case_dir)
        env_q = self._of_config._quoted_linux_path(self._of_config.env_script)

        cmd = self._of_config._build_wsl_cmd(
            f"source {env_q} 2>/dev/null; cd {linux_case} && "
            f"reconstructParMesh -constant 2>&1 | tail -15"
        )
        logger.info("Reconstructing parallel mesh (standalone fallback)...")
        result = self._run_subprocess_with_cancel(cmd, self._RECONSTRUCT_TIMEOUT_S)
        if result.returncode != 0:
            raise RuntimeError(
                f"reconstructParMesh failed (exit {result.returncode}):\n{result.stderr[-300:]}"
            )
        logger.info("reconstructParMesh OK")

    @staticmethod
    def estimate_speedup(n_cores: int, efficiency: float = 0.85) -> dict[str, float]:
        serial_frac = 0.05
        speedup = 1.0 / (serial_frac + (1 - serial_frac) / n_cores * (1 / efficiency))
        return {
            "speedup": round(speedup, 2),
            "efficiency": round(speedup / n_cores, 3),
            "serial_fraction": serial_frac,
        }
