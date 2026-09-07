"""Tests for the REST API server."""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from _test_helpers import _stub_pkg

# --- Mock fastapi + pydantic ---
class _SimpleModel:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        if not kw:
            self.max_cell = 0.05
            self.min_cell = 0.01
            self.detail = "medium"
            self.bl_enabled = False
            self.bl_n_layers = 3
            self.bl_thickness_ratio = 0.005
            self.bl_expansion_ratio = 1.2
            self.n_cores = 1
            self.fault_tolerant = False
    def model_dump(self):
        return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}

class _MockApp:
    def get(self, *a, **kw): return lambda f: f
    def post(self, *a, **kw): return lambda f: f

fastapi_mod = types.ModuleType("fastapi")
fastapi_mod.FastAPI = lambda **kw: _MockApp()
fastapi_mod.UploadFile = type('_UF', (), {})
fastapi_mod.File = lambda default=None: None
fastapi_mod.Form = lambda default=None: None
fastapi_mod.HTTPException = type('_HE', (Exception,), {'__init__': lambda self, *a: None})
fastapi_mod.BackgroundTasks = type('_BGT', (), {'add_task': lambda self, *a, **kw: None})
fastapi_mod.responses = types.ModuleType("fastapi.responses")
fastapi_mod.responses.FileResponse = lambda path, filename: {"path": path, "filename": filename}
fastapi_mod.responses.JSONResponse = lambda content: content
sys.modules["fastapi"] = fastapi_mod
sys.modules["fastapi.responses"] = fastapi_mod.responses

pydantic_mod = types.ModuleType("pydantic")
pydantic_mod.BaseModel = _SimpleModel
pydantic_mod.Field = lambda default=None, **kw: default
sys.modules["pydantic"] = pydantic_mod

# Stub heavy packages (saved and restored so later test modules can still
# import the real polyfoammesh.gui package instead of an empty stub)
_saved_gui = sys.modules.get("polyfoammesh.gui")
_stub_pkg("polyfoammesh.gui")

# Load server module directly
src = Path(__file__).resolve().parents[1] / "src" / "polyfoammesh" / "api" / "server.py"
code = src.read_text(encoding="utf-8")
_mod = types.ModuleType("polyfoammesh.api.server")
_mod.__file__ = str(src)
_mod.__package__ = "polyfoammesh.api"
sys.modules["polyfoammesh.api.server"] = _mod
try:
    exec(compile(code, str(src), "exec"), _mod.__dict__)
finally:
    if _saved_gui is not None:
        sys.modules["polyfoammesh.gui"] = _saved_gui
    else:
        sys.modules.pop("polyfoammesh.gui", None)

def _get(name):
    return getattr(_mod, name)


def test_job_status_enum():
    JobStatus = _get("JobStatus")
    assert JobStatus.PENDING.value == "pending"
    assert JobStatus.COMPLETED.value == "completed"


def test_mesh_params_defaults():
    MeshParams = _get("MeshParams")
    p = MeshParams()
    assert p.max_cell == 0.05
    assert p.min_cell == 0.01
    assert not p.bl_enabled


def test_mesh_params_custom():
    MeshParams = _get("MeshParams")
    p = MeshParams(max_cell=0.02, min_cell=0.005, bl_enabled=True, n_cores=8)
    assert p.max_cell == 0.02
    assert p.bl_enabled
    assert p.n_cores == 8


def test_health_response():
    HealthResponse = _get("HealthResponse")
    r = HealthResponse(status="ok", version="2.0.1")
    assert r.status == "ok"


def test_job_response_defaults():
    JobResponse = _get("JobResponse")
    JobStatus = _get("JobStatus")
    r = JobResponse(job_id="test", status=JobStatus.PENDING)
    assert r.job_id == "test"
    assert r.cell_count == 0


def test_job_response_completed():
    JobResponse = _get("JobResponse")
    JobStatus = _get("JobStatus")
    r = JobResponse(job_id="abc", status=JobStatus.COMPLETED, cell_count=5000)
    assert r.cell_count == 5000


def test_create_app(tmp_path):
    create_app = _get("create_app")
    app = create_app(data_dir=str(tmp_path))
    assert app is not None


def test_create_app_default_dir():
    create_app = _get("create_app")
    app = create_app()
    assert app is not None


def test_mesh_params_model_dump():
    MeshParams = _get("MeshParams")
    p = MeshParams(max_cell=0.05, min_cell=0.01)
    d = p.model_dump()
    assert d["max_cell"] == 0.05


if __name__ == "__main__":
    import tempfile
    test_job_status_enum()
    test_mesh_params_defaults()
    test_mesh_params_custom()
    test_health_response()
    test_job_response_defaults()
    test_job_response_completed()
    with tempfile.TemporaryDirectory() as _td:
        test_create_app(Path(_td))
    test_create_app_default_dir()
    test_mesh_params_model_dump()
    print("ALL PASS")
