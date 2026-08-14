"""Shared RAM-reading helpers for hardware-aware mesh sizing.

Extracted so both the GMSH path (``gmsh_wrapper._hardware_budget``, tet
meshes) and the cfMesh path (``geometry.cfmesh_cell_budget``,
hex-dominant meshes) cap their cell count against the machine actually
running the mesher, instead of each guessing independently or (for
cfMesh, until now) not guessing at all.
"""
from __future__ import annotations

import os
import sys


def fmt_bytes(n: int) -> str:
    """Human-readable byte count (e.g. "14.2 GB"), best-effort."""
    try:
        val = float(n)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if val < 1024 or unit == "TB":
                return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
            val /= 1024
    except Exception:
        pass
    return str(n)


def available_ram_bytes() -> int:
    """Free physical RAM, best-effort. No third-party dependency (ctypes
    is stdlib) so this works the same in a frozen/PyInstaller build."""
    if sys.platform == "win32":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            if stat.ullAvailPhys:
                return int(stat.ullAvailPhys)
        except Exception:
            pass
    try:
        pages = os.sysconf("SC_AVPHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * page_size)
    except Exception:
        pass
    return 4 * 1024**3  # unknown platform/failure: assume 4 GB free, conservative


def total_ram_bytes() -> int:
    """Total physical RAM, best-effort.

    Used as a floor under the cell budget so a machine that merely
    happens to be busy right now (browser open, previous mesh still in
    the viewer) doesn't silently produce a much coarser mesh than the
    same machine would when idle.
    """
    if sys.platform == "win32":
        try:
            import ctypes

            class _MEMSTAT(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMSTAT()
            stat.dwLength = ctypes.sizeof(_MEMSTAT)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            if stat.ullTotalPhys:
                return int(stat.ullTotalPhys)
        except Exception:
            pass
    try:
        return int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except Exception:
        pass
    return 8 * 1024**3


def wsl_ram_bytes(wsl_distro: str = "Ubuntu") -> tuple[int, int] | None:
    """(total, available) bytes actually allocated to the WSL2 VM
    cartesianMesh/checkMesh run inside — NOT the same as the host
    Windows numbers above.

    WSL2 defaults to a memory cap of 50% of the host's RAM (or whatever
    ``.wslconfig`` sets), independent of how much RAM the host machine
    actually has. Capping cfMesh's cell budget against the HOST'S RAM
    (as this module's own ``available_ram_bytes``/``total_ram_bytes``
    do) can therefore let a request through that the host has room for
    but the WSL2 VM cartesianMesh actually runs in does not — measured
    live: a 32 GB host with WSL2 capped at ~15 GB total / ~9 GB free
    let an 8-11M cell request past a host-based budget, then failed
    inside WSL2 during parallel decomposition/reconstruction.

    Returns ``None`` (never raises) when WSL isn't available or the
    call fails/times out — callers should fall back to the host-only
    budget in that case, not treat it as a hard error.
    """
    import re
    import subprocess

    try:
        result = subprocess.run(
            ["wsl.exe", "-d", wsl_distro, "--", "bash", "-lc", "free -b"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        m = re.search(r"^Mem:\s+(\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+(\d+)", result.stdout, re.MULTILINE)
        if not m:
            return None
        total, available = int(m.group(1)), int(m.group(2))
        if total <= 0:
            return None
        return total, available
    except Exception:
        return None
