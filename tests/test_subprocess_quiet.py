"""subprocess_quiet: console-window suppression is installed centrally.

Regression (2026-10-11, user report): the frozen GUI has no console, so
every console-subsystem child (wsl.exe, python.exe, gmsh, checkMesh...)
opened an empty terminal window that stole focus.  One Popen patch plus a
QProcess helper cover every spawn site; both must be safe no-ops on POSIX
and idempotent on Windows.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polyfoammesh.core import subprocess_quiet as sq  # noqa: E402


def test_install_is_idempotent_and_posix_safe():
    sq.install()
    sq.install()  # second call must not double-wrap
    if os.name != "nt":
        # POSIX: install() is a deliberate no-op; creationflags stays unused.
        assert sq._INSTALLED is False


def test_create_no_window_value():
    # 0x08000000 is CREATE_NO_WINDOW per the Windows API.
    assert sq.CREATE_NO_WINDOW == 0x08000000


def test_quiet_qprocess_tolerates_objects_without_the_modifier():
    class _Fake:
        pass

    sq.quiet_qprocess(_Fake())  # must not raise
    sq.quiet_qprocess(None)


def test_quiet_qprocess_calls_the_modifier_when_present():
    calls = []

    class _Fake:
        def setCreateProcessArgumentsModifier(self, fn):
            calls.append(fn)

    sq.quiet_qprocess(_Fake())
    if os.name == "nt":
        assert calls, "modifier must be installed on Windows"
    else:
        assert not calls, "POSIX must not touch QProcess"
