"""pytest configuration — fixes OCP DLL loading for all test files."""
import os as _os

# Add OCP DLL directory to PATH so cadquery/OCP native libraries load
try:
    import OCP as _ocp
    _d = _os.path.dirname(_ocp.__file__)
    if _d not in _os.environ.get("PATH", ""):
        _os.environ["PATH"] = _d + _os.pathsep + _os.environ.get("PATH", "")
except Exception:
    pass
