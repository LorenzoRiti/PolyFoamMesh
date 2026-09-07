"""Collaborative / Cloud meshing — submit mesh jobs to remote clusters.

Provides:
  - REST API client for remote meshing service (Slurm / AWS Batch / generic)
  - Job submission, polling, and result download
  - Session sharing via share-link export/import
  - Local job queuing with remote dispatch

Usage::

    client = CloudMesher(api_url="https://mesh.example.com/api")
    job = client.submit(case_dir="/path/to/case", n_cores=16)
    print(f"Job {job.id} submitted, status: {job.status}")
    job.wait()
    client.download_result(job, output_dir="./results")
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from cfmesh_autogui.octopoda_local import octo

logger = logging.getLogger(__name__)


class JobStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NOT_SUBMITTED = "not_submitted"


@dataclass
class MeshJob:
    """A remote meshing job."""
    id: str = ""
    status: JobStatus = JobStatus.PENDING
    case_dir: str = ""
    n_cores: int = 1
    max_cell: float = 0.05
    min_cell: float = 0.01
    description: str = ""
    created_at: str = ""
    completed_at: str = ""
    cell_count: int = 0
    error: str = ""
    result_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status.value,
            "case_dir": self.case_dir,
            "n_cores": self.n_cores,
            "max_cell": self.max_cell,
            "min_cell": self.min_cell,
            "description": self.description,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "cell_count": self.cell_count,
            "error": self.error,
            "result_url": self.result_url,
        }


@dataclass
class ShareLink:
    """A shareable link to a meshing session."""
    session_id: str = ""
    created_at: str = ""
    expires_at: str = ""
    case_dir: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    url: str = ""


class CloudMesher:
    """Remote/cluster meshing client.

    Connects to a REST API endpoint that dispatches mesh jobs to
    Slurm, PBS, AWS Batch, or a generic worker pool.

    Usage::

        cm = CloudMesher(api_url="https://mesh.example.com/api")
        cm.authenticate("api-key-123")

        job = cm.submit(
            case_dir="/data/case1",
            n_cores=8,
            description="Pipe junction mesh",
        )
        job = cm.poll(job.id)
        if job.status == JobStatus.COMPLETED:
            cm.download_result(job, "./results")
    """

    def __init__(self, api_url: str = "", api_key: str = "") -> None:
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    def authenticate(self, api_key: str) -> None:
        """Set the API key for authenticated requests."""
        self._api_key = api_key
        self._headers["Authorization"] = f"Bearer {api_key}"

    # ------------------------------------------------------------------
    # Job submission & management
    # ------------------------------------------------------------------
    def submit(
        self,
        case_dir: Path | str = "",
        n_cores: int = 1,
        max_cell: float = 0.05,
        min_cell: float = 0.01,
        description: str = "",
    ) -> MeshJob:
        """Submit a meshing job to the remote service.

        If no API URL is configured, creates a local-only job record
        (offline mode).
        """
        job = MeshJob(
            id=self._generate_id(),
            status=JobStatus.PENDING,
            case_dir=str(case_dir) if case_dir else "",
            n_cores=n_cores,
            max_cell=max_cell,
            min_cell=min_cell,
            description=description or f"Mesh job {datetime.now():%Y%m%d_%H%M%S}",
            created_at=datetime.now().isoformat(),
        )

        if self._api_url:
            try:
                payload = job.to_dict()
                resp = self._request("POST", "/jobs", payload)
                if resp:
                    job.id = resp.get("id", job.id)
                    job.status = JobStatus(resp.get("status", "pending"))
                    logger.info("Job submitted: %s -> %s", job.id, self._api_url)
            except Exception as exc:
                job.status = JobStatus.FAILED
                job.error = str(exc)
                logger.error("Job submission failed: %s", exc)
        else:
            # No backend configured: the job was never sent anywhere.
            # Mark it unambiguously — PENDING would imply it may still
            # run, which is a lie for work that was never dispatched.
            job.status = JobStatus.NOT_SUBMITTED
            job.error = (
                "No cloud backend configured (api_url empty) — job was "
                "not submitted to any remote service."
            )
            logger.info(
                "Job %s created (offline mode). Set api_url for remote submission.",
                job.id,
            )

        octo.log_event("cloud_mesh", "job_submit", {
            "job_id": job.id, "n_cores": n_cores,
            "remote": bool(self._api_url),
        })
        return job

    def poll(self, job_id: str) -> MeshJob:
        """Poll a remote job for status update.

        Falls back to returning a local status when no API is configured.
        """
        if not self._api_url:
            return MeshJob(id=job_id, status=JobStatus.PENDING)

        try:
            data = self._request("GET", f"/jobs/{job_id}")
            if data:
                return MeshJob(
                    id=data.get("id", job_id),
                    status=JobStatus(data.get("status", "pending")),
                    n_cores=data.get("n_cores", 1),
                    cell_count=data.get("cell_count", 0),
                    error=data.get("error", ""),
                    result_url=data.get("result_url", ""),
                    completed_at=data.get("completed_at", ""),
                )
        except Exception as exc:
            logger.warning("Poll failed for %s: %s", job_id, exc)

        return MeshJob(id=job_id, status=JobStatus.PENDING)

    def cancel(self, job_id: str) -> bool:
        """Cancel a running job."""
        if not self._api_url:
            return False
        try:
            self._request("DELETE", f"/jobs/{job_id}")
            logger.info("Job %s cancelled.", job_id)
            return True
        except Exception as exc:
            logger.error("Cancel failed for %s: %s", job_id, exc)
            return False

    def wait(self, job: MeshJob, poll_interval: float = 5.0,
             timeout: float = 3600.0) -> MeshJob:
        """Block until the job completes, fails, or is cancelled.

        Args:
            job: The job to wait for.
            poll_interval: Seconds between status checks.
            timeout: Maximum seconds to wait.

        Returns:
            Updated ``MeshJob`` with final status.
        """
        if not self._api_url:
            return job

        start = time.time()
        terminal = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}

        while job.status not in terminal:
            if time.time() - start > timeout:
                job.status = JobStatus.FAILED
                job.error = f"Timed out after {timeout:.0f}s"
                break
            time.sleep(poll_interval)
            job = self.poll(job.id)

        return job

    def download_result(self, job: MeshJob, output_dir: Path | str) -> Path:
        """Download the meshing result archive for a completed job.

        Args:
            job: Completed job with a ``result_url``.
            output_dir: Local directory to write the result.

        Returns:
            Path to the downloaded archive.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if not self._api_url or not job.result_url:
            # Offline mode (or a job with no result URL yet): there is no
            # result archive to download. Write a placeholder that is
            # unambiguous — a caller must never mistake it for a real
            # downloaded result.
            placeholder = job.to_dict()
            placeholder["message"] = (
                "No result archive was downloaded — no cloud backend is "
                "configured (or the job has no result URL yet)."
            )
            result_path = output_dir / f"job_{job.id}_result.json"
            result_path.write_text(json.dumps(placeholder, indent=2))
            logger.info("Offline placeholder written (no remote result): %s", result_path)
            return result_path

        try:
            import requests
            resp = requests.get(
                job.result_url,
                headers=self._headers,
                timeout=300,
                stream=True,
            )
            resp.raise_for_status()

            ext = ".zip" if "zip" in resp.headers.get("content-type", "") else ".tar.gz"
            result_path = output_dir / f"mesh_result_{job.id}{ext}"
            with open(result_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            logger.info("Result downloaded: %s (%d bytes)", result_path, result_path.stat().st_size)
            return result_path
        except Exception as exc:
            raise RuntimeError(f"Failed to download result for {job.id}: {exc}") from exc

    # ------------------------------------------------------------------
    # Session sharing
    # ------------------------------------------------------------------
    def create_share_link(self, case_dir: Path | str,
                          params: dict[str, Any] | None = None) -> ShareLink:
        """Create a shareable link for a meshing session.

        Exports the current case configuration as a share link that
        can be imported by another user.
        """
        session_id = self._generate_id()
        link = ShareLink(
            session_id=session_id,
            created_at=datetime.now().isoformat(),
            expires_at=datetime.now().isoformat(),  # no expiry by default
            case_dir=str(case_dir),
            params=params or {},
            url=f"{self._api_url}/share/{session_id}" if self._api_url else f"cfmesh://share/{session_id}",
        )

        octo.log_event("cloud_mesh", "share_link_created", {
            "session_id": session_id,
        })
        return link

    def export_share_data(self, link: ShareLink) -> str:
        """Export a share link as a JSON string (for clipboard/file)."""
        return json.dumps(link.to_dict() if hasattr(link, 'to_dict') else {
            "session_id": link.session_id,
            "case_dir": link.case_dir,
            "params": link.params,
            "url": link.url,
        }, indent=2)

    @staticmethod
    def import_share_data(json_str: str) -> ShareLink:
        """Import a share link from a JSON string."""
        data = json.loads(json_str)
        return ShareLink(
            session_id=data.get("session_id", ""),
            created_at=data.get("created_at", ""),
            expires_at=data.get("expires_at", ""),
            case_dir=data.get("case_dir", ""),
            params=data.get("params", {}),
            url=data.get("url", ""),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _request(self, method: str, path: str,
                 payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Make an HTTP request to the API."""
        try:
            import requests as req
            url = f"{self._api_url}{path}"
            resp = req.request(
                method, url, headers=self._headers,
                json=payload, timeout=30,
            )
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        except ImportError:
            logger.warning("requests library not available. Install with: pip install requests")
            return None
        except Exception as exc:
            logger.warning("API request failed: %s %s: %s", method, path, exc)
            return None

    @staticmethod
    def _generate_id() -> str:
        return uuid.uuid4().hex[:12]
