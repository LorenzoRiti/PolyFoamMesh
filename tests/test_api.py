"""HTTP tests of the REST API (real FastAPI, fake mesher: no meshing runs)."""
from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from polyfoammesh.api import server  # noqa: E402


class _FakeWorkflow:
    success = True

    def set_geometry(self, path):
        pass

    def set_cell_sizes(self, max_cell, min_cell):
        pass

    def set_boundary_layers(self, **kw):
        pass

    def run(self):
        return SimpleNamespace(
            success=self.success,
            cell_count=123 if self.success else 0,
            wall_time_seconds=0.1,
            steps_completed=["mesh"],
            errors=[] if self.success else ["boom"],
        )


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("POLYFOAMMESH_API_KEY", raising=False)
    monkeypatch.delenv("POLYFOAMMESH_API_MAX_UPLOAD", raising=False)
    # Patch the module the server's lazy import resolves (sys.modules), not
    # the package attribute: other tests may swap sys.modules entries.
    module = importlib.import_module("polyfoammesh.commercial.watertight")
    monkeypatch.setattr(module, "WatertightWorkflow", _FakeWorkflow)
    return TestClient(server.create_app(data_dir=str(tmp_path)))


def _upload(client, name="part.stl", body=b"solid x"):
    return client.post("/upload", files={"file": (name, body)})


def _submit(client):
    file_id = _upload(client).json()["file_id"]
    return client.post("/mesh", data={"file_id": file_id}).json()


def test_job_reaches_completed_and_result_is_downloadable(client):
    job = _submit(client)
    status = client.get(f"/jobs/{job['job_id']}").json()
    assert status["status"] == "completed"
    assert status["cell_count"] == 123
    result = client.get(f"/results/{job['job_id']}")
    assert result.status_code == 200
    assert result.json()["success"] is True


def test_failed_mesh_reports_failed_with_error(client, monkeypatch):
    monkeypatch.setattr(_FakeWorkflow, "success", False)
    status = client.get(f"/jobs/{_submit(client)['job_id']}").json()
    assert status["status"] == "failed"
    assert status["error"] == "boom"


@pytest.mark.parametrize("file_id", ["../../secret", "*", "abc", "0123456789a?"])
def test_malformed_file_id_is_never_globbed(client, tmp_path, file_id):
    (tmp_path / "secret.txt").write_text("x")
    resp = client.post("/mesh", data={"file_id": file_id})
    assert resp.status_code == 404


def test_invalid_params_is_client_error(client):
    file_id = _upload(client).json()["file_id"]
    for bad in ("{not json", "[1]", json.dumps({"max_cell": "big"})):
        resp = client.post("/mesh", data={"file_id": file_id, "params": bad})
        assert resp.status_code == 422, bad


def test_unsupported_extension_rejected(client):
    assert _upload(client, name="part.exe").status_code == 400


def test_upload_limit_leaves_no_file(tmp_path, monkeypatch):
    monkeypatch.delenv("POLYFOAMMESH_API_KEY", raising=False)
    monkeypatch.setenv("POLYFOAMMESH_API_MAX_UPLOAD", "4")
    client = TestClient(server.create_app(data_dir=str(tmp_path)))
    assert _upload(client, body=b"12345").status_code == 413
    assert not list((tmp_path / "uploads").iterdir())


def test_api_key_enforced_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYFOAMMESH_API_KEY", "test-key")
    client = TestClient(server.create_app(data_dir=str(tmp_path)))
    assert client.get("/health").status_code == 200
    assert client.get("/jobs").status_code == 401
    wrong = client.get("/jobs", headers={"X-API-Key": "nope"})
    assert wrong.status_code == 401
    assert client.get("/jobs", headers={"X-API-Key": "test-key"}).status_code == 200


def test_finished_job_survives_restart(client, tmp_path):
    job_id = _submit(client)["job_id"]
    fresh = TestClient(server.create_app(data_dir=str(tmp_path)))
    assert fresh.get(f"/jobs/{job_id}").json()["status"] == "completed"
    assert fresh.get(f"/results/{job_id}").status_code == 200


def test_job_interrupted_by_restart_is_failed_not_running(tmp_path, monkeypatch):
    monkeypatch.delenv("POLYFOAMMESH_API_KEY", raising=False)
    client = TestClient(server.create_app(data_dir=str(tmp_path)))
    job_id = "0123456789ab"
    (tmp_path / "jobs" / f"{job_id}.json").write_text(
        json.dumps({"job_id": job_id, "status": "running"}), encoding="utf-8"
    )
    status = client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "failed"
    assert "restarted" in status["error"]


def test_import_does_not_build_the_app():
    # Building it would create ./api_data just by importing the module.
    assert "app" not in vars(server)
