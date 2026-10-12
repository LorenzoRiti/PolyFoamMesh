"""Keep every child process from opening a console window (Windows GUI).

The frozen app is built with ``console=False``: it has no console of its
own, so every console-subsystem child it spawns (``wsl.exe``, ``bash``,
``python.exe``, ``gmsh``, ``checkMesh``, ...) makes Windows allocate a
NEW console window - empty, flickering, and stealing focus while the user
works (user report: "empty terminal windows pop up in front").

The robust fix is central instead of per-call: on Windows, wrap
``subprocess.Popen.__init__`` (which ``subprocess.run`` also goes
through) and OR in ``CREATE_NO_WINDOW`` whenever the caller did not pass
creation flags.  ``QProcess`` instances get the same treatment through
:func:`quiet_qprocess`, which installs Qt's CreateProcess argument
modifier (Qt >= 6.0; older bindings simply keep the previous behaviour).

Everything is a no-op on POSIX (``creationflags`` is Windows-only and the
patch is skipped), and harmless when a parent console exists (dev runs):
``CREATE_NO_WINDOW`` only suppresses the window, never the process.
"""
from __future__ import annotations

import os
import subprocess

CREATE_NO_WINDOW = 0x08000000

_INSTALLED = False


def install() -> None:
    """Patch subprocess so every child spawns without a console window.

    Idempotent and Windows-only.  Call once, before any spawn (app.main
    does this before anything else).
    """
    global _INSTALLED
    if _INSTALLED or os.name != "nt":
        return
    _orig_init = subprocess.Popen.__init__

    def _init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["creationflags"] = (
            int(kwargs.get("creationflags", 0) or 0) | CREATE_NO_WINDOW
        )
        return _orig_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _init  # type: ignore[method-assign]
    _INSTALLED = True


def quiet_qprocess(proc) -> None:
    """Make a QProcess spawn without a console window (Windows, Qt >= 6).

    Best effort: on bindings/platforms that do not expose the modifier
    the call is skipped - the Popen patch still covers subprocess spawns.
    """
    if os.name != "nt" or proc is None:
        return
    modifier = getattr(proc, "setCreateProcessArgumentsModifier", None)
    if modifier is None:
        return
    try:
        def _modify(args):  # pragma: no cover - exercised on Windows only
            args.flags |= CREATE_NO_WINDOW
            return args

        modifier(_modify)
    except Exception:  # noqa: BLE001 - cosmetic; never break the caller
        pass
