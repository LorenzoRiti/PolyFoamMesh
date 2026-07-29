"""OpenMP multi-core acceleration engine for cfMesh.

Provides intelligent OpenMP thread configuration based on:
- Physical vs. logical core detection
- Problem size (estimated cell count)
- Mesh type (serial / parallel MPI ranks)
- User preference (aggressive / balanced / conservative)

Usage::

    accel = OpenMPAccel()
    cfg = accel.recommend(cell_estimate=500_000, mode="balanced")
    env_cmd = cfg.to_bash_export()  # "export OMP_NUM_THREADS=4; ..."

    # Or use the convenience method to build a full env prefix
    prefix = OpenMPAccel.build_env_prefix(cell_estimate=1_000_000)
"""

from __future__ import annotations

import logging
import os
import platform
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ThreadMode(str, Enum):
    """OpenMP thread count strategy."""
    CONSERVATIVE = "conservative"   # Use physical cores / 2, safe for shared env
    BALANCED = "balanced"           # Use physical cores (default, safe)
    AGGRESSIVE = "aggressive"       # Use all logical cores (incl. hyperthreads)
    CUSTOM = "custom"               # User-specified thread count


@dataclass
class OMPConfig:
    """OpenMP configuration for a meshing operation.

    Parameters
    ----------
    n_threads : int
        Number of OpenMP threads to use (OMP_NUM_THREADS).
    n_physical : int
        Number of physical CPU cores detected on the host.
    n_logical : int
        Number of logical CPUs (incl. hyperthreads) detected on the host.
    mode : ThreadMode
        Strategy used to determine thread count.
    cell_estimate : int
        Estimated cell count (0 = unknown).
    bind_strategy : str
        OMP_PROC_BIND value: "spread", "close", or "false".
    max_threads : int
        Absolute upper limit for OMP_NUM_THREADS (prevents oversubscription).
    """

    n_threads: int = 1
    n_physical: int = 1
    n_logical: int = 1
    mode: ThreadMode = ThreadMode.CONSERVATIVE
    cell_estimate: int = 0
    bind_strategy: str = "spread"
    max_threads: int = 64

    def to_env(self) -> dict[str, str]:
        """Build environment variable overrides for this configuration."""
        return {
            "OMP_NUM_THREADS": str(self.n_threads),
            "OMP_PROC_BIND": self.bind_strategy,
            "OMP_PLACES": "cores",
        }

    def to_bash_export(self) -> str:
        """Build a bash export command string for inline use in WSL commands."""
        return (
            f"export OMP_NUM_THREADS={self.n_threads}; "
            f"export OMP_PROC_BIND={self.bind_strategy}; "
            f"export OMP_PLACES=cores;"
        )

    def speedup_estimate(self) -> float:
        """Estimate expected speedup from Amdahl's law.
        
        cfMesh's OpenMP parallel regions have ~85% parallel fraction
        on typical hex-dominant meshing workloads.
        """
        from math import log as _log
        if self.n_threads <= 1:
            return 1.0
        parallel_frac = 0.85
        serial_frac = 1.0 - parallel_frac
        return 1.0 / (serial_frac + parallel_frac / self.n_threads)

    def summary(self) -> str:
        """Human-readable summary of the OpenMP configuration."""
        ht = " (incl. hyperthreads)" if self.n_logical > self.n_physical else ""
        return (
            f"OpenMP: {self.n_threads} thread(s) on {self.n_physical} physical "
            f"/ {self.n_logical} logical cores{ht}, "
            f"mode={self.mode.value}, bind={self.bind_strategy}, "
            f"est. speedup={self.speedup_estimate():.2f}x"
        )


class OpenMPAccel:
    """Detects hardware and recommends optimal OpenMP configuration for cfMesh.

    The recommendation logic handles:
    - Physical vs. logical core detection (cross-platform)
    - Problem-size-aware thread scaling
    - MPI rank coexistence (total threads <= logical cores)
    - Mode presets for different user preferences
    """

    def __init__(self) -> None:
        self._n_physical = self._detect_physical_cores()
        self._n_logical = (os.cpu_count() or 1)
        self._n_logical = max(self._n_logical, 1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def n_physical(self) -> int:
        return self._n_physical

    @property
    def n_logical(self) -> int:
        return self._n_logical

    def recommend(
        self,
        cell_estimate: int = 0,
        mode: str | ThreadMode = ThreadMode.BALANCED,
        mpi_ranks: int = 0,
        user_threads: int | None = None,
    ) -> OMPConfig:
        """Recommend OpenMP configuration for the given problem size.

        Parameters
        ----------
        cell_estimate : int
            Estimated number of mesh cells (0 = unknown).
        mode : str or ThreadMode
            Thread count strategy.
        mpi_ranks : int
            Number of MPI ranks if running in parallel mesh mode.
            Each MPI rank gets its own OpenMP threads; total
            (ranks * threads) should not exceed logical cores.
        user_threads : int, optional
            Explicit thread count for CUSTOM mode.
        """
        if isinstance(mode, str):
            mode = ThreadMode(mode)

        n_threads = self._compute_thread_count(cell_estimate, mode, mpi_ranks, user_threads)
        bind = self._choose_bind_strategy(n_threads, mpi_ranks)

        return OMPConfig(
            n_threads=n_threads,
            n_physical=self._n_physical,
            n_logical=self._n_logical,
            mode=mode,
            cell_estimate=cell_estimate,
            bind_strategy=bind,
            max_threads=self._n_logical,
        )

    @staticmethod
    def build_env_prefix(
        cell_estimate: int = 0,
        mode: str | ThreadMode = ThreadMode.BALANCED,
        mpi_ranks: int = 0,
        user_threads: int | None = None,
    ) -> str:
        """One-liner: create an instance, get a config, produce export string.
        
        Convenience for call sites that just want the bash env prefix without
        creating and keeping an OpenMPAccel instance around.
        """
        accel = OpenMPAccel()
        cfg = accel.recommend(cell_estimate, mode, mpi_ranks, user_threads)
        return cfg.to_bash_export()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_physical_cores() -> int:
        """Detect the number of physical CPU cores, not hyperthreads.
        
        Uses platform-specific methods; falls back to cpu_count() / 2
        if detection fails (conservative assumption).
        """
        logical = os.cpu_count() or 1
        try:
            system = platform.system()
            if system == "Linux":
                result = set()
                for path in ("/sys/devices/system/cpu/cpu*/topology/core_id",
                             "/sys/devices/system/cpu/cpu*/topology/physical_package_id"):
                    import glob
                    for f in glob.glob(path):
                        try:
                            result.add((int(open(f).read().strip()),))
                        except (OSError, ValueError):
                            pass
                if result:
                    return max(len(result), 1)
            elif system == "Windows":
                import subprocess
                cmd = ["wmic", "cpu", "get", "NumberOfCores"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                for line in result.stdout.splitlines():
                    cleaned = line.strip()
                    if cleaned.isdigit():
                        cores = int(cleaned)
                        return max(cores, 1)
            elif system == "Darwin":
                import subprocess
                cmd = ["sysctl", "-n", "hw.physicalcpu"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                val = result.stdout.strip()
                if val.isdigit():
                    cores = int(val)
                    return max(cores, 1)
        except Exception as exc:
            logger.debug("Physical core detection failed: %s", exc)
        return max(logical // 2, 1)

    def _compute_thread_count(
        self,
        cell_estimate: int,
        mode: ThreadMode,
        mpi_ranks: int,
        user_threads: int | None,
    ) -> int:
        """Determine the recommended OMP_NUM_THREADS."""
        if mode == ThreadMode.CUSTOM and user_threads is not None:
            return max(1, min(user_threads, self._n_logical))

        if mpi_ranks > 0:
            return self._compute_mpi_threads(cell_estimate, mode, mpi_ranks)

        if mode == ThreadMode.CONSERVATIVE:
            base = max(self._n_physical // 2, 1)
        elif mode == ThreadMode.BALANCED:
            base = self._n_physical
        elif mode == ThreadMode.AGGRESSIVE:
            base = self._n_logical
        else:
            base = self._n_physical

        return self._scale_by_problem_size(base, cell_estimate)

    def _compute_mpi_threads(
        self,
        cell_estimate: int,
        mode: ThreadMode,
        mpi_ranks: int,
    ) -> int:
        """When MPI ranks are active, each rank should get 1-2 threads
        to avoid oversubscription. Total threads = ranks * threads <= logical.
        """
        if mode == ThreadMode.CONSERVATIVE:
            threads_per_rank = 1
        elif mode == ThreadMode.BALANCED:
            threads_per_rank = max(1, min(2, self._n_logical // mpi_ranks))
        else:
            threads_per_rank = max(1, self._n_logical // mpi_ranks)

        scaled = self._scale_by_problem_size(threads_per_rank, cell_estimate)
        return max(1, min(scaled, self._n_logical // max(mpi_ranks, 1)))

    @staticmethod
    def _scale_by_problem_size(base: int, cell_estimate: int) -> int:
        """Scale thread count by problem size.
        
        Small meshes (< 100K cells) don't benefit from many threads:
        the OpenMP overhead of spawning/joining parallel regions
        outweighs the parallel work.
        """
        if cell_estimate <= 0 or cell_estimate >= 100_000:
            return base
        if cell_estimate >= 50_000:
            return max(1, base - 1)
        if cell_estimate >= 10_000:
            return max(1, base // 2)
        return 1  # < 10K cells: serial is faster

    @staticmethod
    def _choose_bind_strategy(n_threads: int, mpi_ranks: int) -> str:
        """Choose OMP_PROC_BIND strategy.
        
        'spread' distributes threads across cores for best memory bandwidth.
        'close' keeps threads on adjacent cores for best cache sharing.
        For MPI + OpenMP hybrid, 'spread' is preferred to avoid resource
        contention between ranks.
        """
        if mpi_ranks > 1:
            return "spread"
        if n_threads <= 2:
            return "close"
        return "spread"
