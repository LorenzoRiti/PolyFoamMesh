"""Tests for the collaborative/cloud meshing module."""
from __future__ import annotations

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _test_helpers import load_commercial_module

_mod = load_commercial_module("cloud_mesh")
JobStatus = _mod.JobStatus
MeshJob = _mod.MeshJob
ShareLink = _mod.ShareLink
CloudMesher = _mod.CloudMesher


def test_job_status_enum():
    assert JobStatus.PENDING.value == "pending"
    assert JobStatus.RUNNING.value == "running"
    assert JobStatus.COMPLETED.value == "completed"
    assert JobStatus.FAILED.value == "failed"
    assert JobStatus.CANCELLED.value == "cancelled"
    assert JobStatus.NOT_SUBMITTED.value == "not_submitted"


def test_mesh_job_defaults():
    j = MeshJob()
    assert j.status == JobStatus.PENDING
    assert j.n_cores == 1
    assert j.cell_count == 0
    assert j.error == ""


def test_mesh_job_with_data():
    j = MeshJob(id="abc123", status=JobStatus.RUNNING, n_cores=8, cell_count=50000)
    assert j.id == "abc123"
    assert j.status == JobStatus.RUNNING
    assert j.n_cores == 8
    assert j.cell_count == 50000


def test_mesh_job_to_dict():
    j = MeshJob(id="test", status=JobStatus.COMPLETED, cell_count=1000)
    d = j.to_dict()
    assert d["id"] == "test"
    assert d["status"] == "completed"
    assert d["cell_count"] == 1000


def test_share_link_defaults():
    s = ShareLink()
    assert s.session_id == ""
    assert s.params == {}


def test_cloud_mesher_init():
    cm = CloudMesher()
    assert cm._api_url == ""
    assert cm._api_key == ""


def test_cloud_mesher_init_with_url():
    cm = CloudMesher(api_url="https://mesh.example.com/api", api_key="key123")
    assert "example.com" in cm._api_url
    assert "key123" in cm._headers.get("Authorization", "")


def test_cloud_mesher_authenticate():
    cm = CloudMesher()
    cm.authenticate("new-key")
    assert cm._api_key == "new-key"
    assert "new-key" in cm._headers["Authorization"]


def test_submit_offline():
    cm = CloudMesher()
    job = cm.submit(case_dir="/data/case1", n_cores=4, description="test")
    assert job.id is not None
    assert job.n_cores == 4
    assert job.description == "test"
    # Offline jobs were never dispatched — PENDING would imply they may
    # still run, which is a lie for work that was never submitted.
    assert job.status == JobStatus.NOT_SUBMITTED
    assert "not submitted" in job.error


def test_submit_offline_with_defaults():
    cm = CloudMesher()
    job = cm.submit()
    assert job.max_cell == 0.05
    assert job.min_cell == 0.01


def test_poll_offline():
    cm = CloudMesher()
    job = cm.poll("nonexistent")
    assert job.status == JobStatus.PENDING


def test_cancel_offline():
    cm = CloudMesher()
    result = cm.cancel("test-job")
    assert not result, "Cancel should return False in offline mode"


def test_wait_offline():
    cm = CloudMesher()
    job = MeshJob(id="test", status=JobStatus.PENDING)
    result = cm.wait(job, poll_interval=0.1, timeout=1.0)
    assert result.status == JobStatus.PENDING


def test_download_result_offline():
    import os as _os
    cm = CloudMesher()
    job = MeshJob(id="offline-test", status=JobStatus.NOT_SUBMITTED)
    tmp = Path(_os.environ.get("TEMP", "/tmp"))
    result = cm.download_result(job, tmp)
    assert result.exists()
    data = json.loads(result.read_text())
    assert data["id"] == "offline-test"
    # The placeholder must be unambiguous: it must not read as a real
    # downloaded result archive.
    assert data["status"] == "not_submitted"
    assert "No result archive was downloaded" in data["message"]
    result.unlink(missing_ok=True)


def test_create_share_link():
    cm = CloudMesher()
    link = cm.create_share_link("/data/case1", {"max_cell": 0.05})
    assert link.session_id is not None
    assert "cfmesh://" in link.url


def test_create_share_link_with_api():
    cm = CloudMesher(api_url="https://mesh.example.com")
    link = cm.create_share_link("/data/case1")
    assert "example.com" in link.url


def test_export_import_share():
    cm = CloudMesher()
    link = cm.create_share_link("/data/case", {"max": 0.05})
    exported = cm.export_share_data(link)
    imported = cm.import_share_data(exported)
    assert imported.case_dir == "/data/case"
    assert imported.params.get("max") == 0.05


def test_mesh_job_to_dict_roundtrip():
    j = MeshJob(id="r1", status=JobStatus.COMPLETED, cell_count=5000)
    d = j.to_dict()
    assert d["status"] == "completed"
    assert d["cell_count"] == 5000


def test_generate_id():
    id1 = CloudMesher._generate_id()
    id2 = CloudMesher._generate_id()
    assert len(id1) == 12
    assert id1 != id2


def test_submit_with_all_params():
    cm = CloudMesher()
    job = cm.submit(
        case_dir="/project/mesh",
        n_cores=16,
        max_cell=0.02,
        min_cell=0.005,
        description="Production mesh",
    )
    assert job.case_dir == "/project/mesh"
    assert job.max_cell == 0.02
    assert job.min_cell == 0.005


if __name__ == "__main__":
    test_job_status_enum()
    test_mesh_job_defaults()
    test_mesh_job_with_data()
    test_mesh_job_to_dict()
    test_share_link_defaults()
    test_cloud_mesher_init()
    test_cloud_mesher_init_with_url()
    test_cloud_mesher_authenticate()
    test_submit_offline()
    test_submit_offline_with_defaults()
    test_poll_offline()
    test_cancel_offline()
    test_wait_offline()
    test_download_result_offline()
    test_create_share_link()
    test_create_share_link_with_api()
    test_export_import_share()
    test_mesh_job_to_dict_roundtrip()
    test_generate_id()
    test_submit_with_all_params()
    print("ALL PASS")
