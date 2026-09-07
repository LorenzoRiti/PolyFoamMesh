"""PyInstaller runtime hook: add casadi's DLL folder to the Windows DLL
search path before any user code runs.

Python 3.8+ removed implicit DLL-directory search (PATH/CWD-based) for
extension modules on Windows; a package must call os.add_dll_directory()
pointing at its own folder for the OS loader to find sibling DLLs. In a
normal pip install this "just works" because Windows still searches the
directory containing the .pyd itself as part of its own default search
order for that .pyd's *own* PE dependencies — but casadi ships ~100 DLLs
alongside `_casadi.pyd`, and in the frozen (PyInstaller onefile) build
that DLL load still failed ("DLL load failed while importing _casadi")
even after explicitly collecting every one of those DLLs via
collect_all('casadi') in the spec — the files were present in the
extracted bundle, just not being found by the OS loader at runtime.
Explicitly registering the directory via the modern
os.add_dll_directory() API (the actual mechanism Python 3.8+ expects)
is what should have been happening all along; casadi's own package
doesn't do this itself.
"""
import sys
import os

if sys.platform == "win32" and getattr(sys, "frozen", False):
    _meipass = getattr(sys, "_MEIPASS", None)
    if _meipass:
        _casadi_dir = os.path.join(_meipass, "casadi")
        if os.path.isdir(_casadi_dir):
            try:
                os.add_dll_directory(_casadi_dir)
            except (AttributeError, OSError):
                pass
        # Belt-and-braces: some DLL loaders still consult PATH.
        os.environ["PATH"] = _casadi_dir + os.pathsep + os.environ.get("PATH", "")
