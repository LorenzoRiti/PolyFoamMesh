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
  1. Write decomposeParDict (cartesianMesh -parallel reads
     numberOfSubdomains from it)
  2. Create processorN/ directories with hard-linked triSurface
     (avoids copying large STL files N times)
  3. Write meshDict + controlDict in each processor dir
  4. mpirun -np N cartesianMesh -parallel
  5. reconstructParMesh -constant to merge the per-rank meshes back into
     constant/polyMesh
  6. Collect per-rank cell counts for diagnostics
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

from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.validation import validate_cell_size, validate_case_dir
from cfmesh_autogui.octopoda_local import octo

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
    # 30 min ceiling (generous for interactive use; very large meshes
    # that genuinely need longer should use the serial path).
    _TIMEOUT_S = 1800
    # Reconstruct timeout: merging N subdomain meshes is I/O-bound and
    # scales with the largest per-rank mesh size.  10 minutes should
    # cover even multi-million-cell cases.
    _RECONSTRUCT_TIMEOUT_S = 600

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
        """
        self._cancel_event.set()
        self._kill_stray_processes()

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

    def _estimate_per_rank_mb(self) -> int:
        base_mb = 200
        tri_dir = (self._case_dir / "constant" / "triSurface") if self._case_dir else None
        if not tri_dir or not tri_dir.is_dir():
            return base_mb
        total_bytes = sum(f.stat().st_size for f in tri_dir.glob("*") if f.is_file())
        surface_mb = total_bytes / (1024 * 1024)
        return int(base_mb + surface_mb * 20)

    def _clamp_cores_to_available_memory(self) -> None:
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
            return

        per_rank_mb = self._estimate_per_rank_mb()
        safe_n = max(self.MIN_CORES, int((available_mb * 0.7) // per_rank_mb))
        if safe_n < self._params.n_cores:
            msg = (
                f"Reduced parallel cores from {self._params.n_cores} to "
                f"{safe_n}: WSL2 has ~{available_mb} MB available and each "
                f"rank is estimated at ~{per_rank_mb} MB for this geometry."
            )
            logger.warning(msg)
            self._result.warnings.append(msg)
            self._params.n_cores = safe_n

    def run(self) -> ParallelMeshResult:
        """Execute the parallel meshing workflow.

        Steps:
          1. Kill stray MPI processes from previous runs
          2. Clamp core count to available WSL2 memory
          3. Write meshDict (cell sizes, BL, patch names)
          4. Write decomposeParDict
          5. Create processorN/ dirs with hard-linked triSurface
          6. Run cartesianMesh -parallel across n_cores MPI ranks
          7. Collect per-rank cell counts
          8. Reconstruct with reconstructParMesh into constant/polyMesh
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
            self._clamp_cores_to_available_memory()
            self._result.n_cores = self._params.n_cores

            self._write_meshdict()
            if self._cancel_event.is_set():
                raise CancelledError("Cancelled after meshDict")
            self._write_decompose_par_dict()
            if self._cancel_event.is_set():
                raise CancelledError("Cancelled after decomposeParDict")
            self._step_create_processor_dirs()
            if self._cancel_event.is_set():
                raise CancelledError("Cancelled after processor dirs")
            self._step_parallel_mesh()
            if self._cancel_event.is_set():
                raise CancelledError("Cancelled after parallel mesh step")
            self._step_reconstruct()

            from cfmesh_autogui.core.boundary_reader import count_cells
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
        from cfmesh_autogui.core.meshdict_gen import write_meshdict
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

    def _write_decompose_par_dict(self) -> None:
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
        """Create processorN/ directories with hard-linked triSurface.

        Uses hardlinks for triSurface STL files to avoid copying
        potentially hundreds of MB N times.  Each rank reads the STL
        independently; a hardlinked copy is indistinguishable from a
        regular one at the file-descriptor level.

        Cleans up ANY leftover processorN/ dirs (not just the ones for
        this core count) so a partial previous run can't leave stale
        data that confuses cartesianMesh -parallel.
        """
        if not self._case_dir:
            return

        for d in list(self._case_dir.glob("processor*")):
            if d.is_dir():
                shutil.rmtree(d)

        for i in range(self._params.n_cores):
            proc_dir = self._case_dir / f"processor{i}"
            proc_dir.mkdir(parents=True)

            # Copy system/ (small text files — plain copy is fast enough)
            shutil.copytree(
                self._case_dir / "system",
                proc_dir / "system",
            )

            # Copy constant/ with hardlinks for triSurface/
            src_const = self._case_dir / "constant"
            dst_const = proc_dir / "constant"
            dst_const.mkdir(parents=True)
            for item in src_const.iterdir():
                if item.is_dir() and item.name == "triSurface":
                    dst_ts = dst_const / "triSurface"
                    dst_ts.mkdir(parents=True)
                    for f in item.iterdir():
                        if f.is_file():
                            dst_file = dst_ts / f.name
                            try:
                                dst_file.hardlink_to(f)
                            except (OSError, NotImplementedError):
                                shutil.copy2(f, dst_file)
                elif item.is_dir():
                    shutil.copytree(item, dst_const / item.name)
                elif item.is_file():
                    shutil.copy2(item, dst_const / item.name)

        logger.info(
            "Created %d processor directories (hardlinked triSurface)",
            self._params.n_cores,
        )

    def _run_subprocess_with_cancel(
        self, cmd: list[str], timeout: int,
    ) -> subprocess.CompletedProcess:
        """Run *cmd* as a subprocess with cancellation support.
        Polls the cancel event every 0.5s; kills the process tree on cancel."""
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=4096,
        )
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if self._cancel_event.is_set():
                self._kill_stray_processes()
                process.kill()
                try:
                    process.wait(timeout=5)
                except Exception:
                    pass
                raise CancelledError("Cancelled by user during subprocess")
            if time.monotonic() > deadline:
                process.kill()
                try:
                    process.wait(timeout=5)
                except Exception:
                    pass
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)
            time.sleep(0.5)
        stdout, stderr = process.communicate()
        return subprocess.CompletedProcess(
            cmd, process.returncode, stdout=stdout, stderr=stderr,
        )

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

        if result.returncode == 0:
            logger.info("Parallel meshing + reconstruct OK")
            self._collect_per_rank_counts()
        else:
            points_exist = (
                self._case_dir / "constant" / "polyMesh" / "points"
            ).exists()
            if not points_exist:
                for i in range(n):
                    proc_points = (
                        self._case_dir / f"processor{i}"
                        / "constant" / "polyMesh" / "points"
                    )
                    if proc_points.exists():
                        points_exist = True
                        break
            if not points_exist:
                log_path = self._case_dir / "parallel_mesh.log"
                detail = ""
                if log_path.exists():
                    detail = log_path.read_text(encoding="ascii", errors="replace")[-1000:]
                raise RuntimeError(
                    f"Parallel meshing failed (exit {result.returncode}). "
                    f"No polyMesh found.  Full log: {log_path}\n"
                    f"{detail or result.stderr[-500:]}"
                )
            logger.warning(
                "Parallel meshing exit=%d but partial mesh may exist",
                result.returncode,
            )
            self._result.warnings.append(
                f"cartesianMesh returned exit code {result.returncode}"
            )

    def _collect_per_rank_counts(self) -> None:
        """Read cell counts from each processorN/ directory."""
        if not self._case_dir:
            return
        from cfmesh_autogui.core.boundary_reader import count_cells
        counts: list[int] = []
        for i in range(self._params.n_cores):
            proc_dir = self._case_dir / f"processor{i}"
            if proc_dir.exists():
                counts.append(count_cells(proc_dir))
            else:
                counts.append(0)
        self._result.cell_count_per_rank = counts
        total = sum(counts)
        avg = total / max(len(counts), 1)
        logger.info(
            "Per-rank cells: %s (total=%d, avg=%d)",
            counts, total, avg,
        )

    def _step_reconstruct(self) -> None:
        """Run reconstructParMesh to merge subdomain meshes.

        build_parallel_command now bundles reconstruct into the same
        mpirun invocation (fewer WSL2 round-trips, better error atomicity).
        This method is kept as a standalone fallback when the bundled
        reconstruct inside build_parallel_command fails — it calls
        reconstructParMesh explicitly on the merged result.
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
