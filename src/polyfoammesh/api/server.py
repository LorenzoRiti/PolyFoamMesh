"""FastAPI server for headless meshing CI/CD integration.

Provides REST endpoints for:
  - Health check & version
  - Geometry upload (STEP/STL)
  - Mesh parameter configuration
  - Job submission (synchronous & asynchronous)
  - Job status polling
  - Result download (mesh files + quality report)
  - Batch processing

All endpoints return JSON. File uploads use multipart/form-data.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Server-generated ids are 12 hex chars; anything else never reaches a path.
_ID_RE = re.compile(r"[0-9a-f]{12}")
_UPLOAD_CHUNK = 1024 * 1024
_DEFAULT_MAX_UPLOAD = 2 * 1024 ** 3

try:
    from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
    from fastapi.responses import FileResponse, JSONResponse
    from pydantic import BaseModel
    _HAS_FASTAPI = True
except ImportError:
    # fastapi not installed: the API server is unavailable (optional feature)
    logger.debug("FastAPI not installed — API server disabled", exc_info=True)
    _HAS_FASTAPI = False

from polyfoammesh.octopoda_local import octo

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class MeshParams(BaseModel):
    max_cell: float = 0.05
    min_cell: float = 0.01
    detail: str = "medium"
    bl_enabled: bool = False
    bl_n_layers: int = 3
    bl_thickness_ratio: float = 0.005
    bl_expansion_ratio: float = 1.2
    n_cores: int = 1
    fault_tolerant: bool = False

class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    created_at: str = ""
    completed_at: str = ""
    cell_count: int = 0
    error: str = ""
    result_url: str = ""

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = ""
    api_version: str = "1.0.0"
    fastapi_available: bool = True

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(data_dir: str | None = None) -> Any:
    """Create and configure the FastAPI application.

    Args:
        data_dir: Directory for uploaded files and results.
            Defaults to ``./api_data``.

    Returns:
        Configured FastAPI app instance.
    """
    if not _HAS_FASTAPI:
        raise ImportError(
            "FastAPI is required for the API server. "
            "Install with: pip install fastapi uvicorn"
        )

    app = FastAPI(
        title="PolyFoamMesh API",
        description="Headless meshing API for CI/CD integration",
        version="1.0.0",
    )

    # Data directory
    _data_dir = Path(data_dir or "api_data").resolve()
    _uploads_dir = _data_dir / "uploads"
    _results_dir = _data_dir / "results"
    _jobs_dir = _data_dir / "jobs"
    for d in [_uploads_dir, _results_dir, _jobs_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # In-memory job store
    _jobs: dict[str, dict[str, Any]] = {}

    api_key = os.environ.get("POLYFOAMMESH_API_KEY", "")
    max_upload = int(os.environ.get("POLYFOAMMESH_API_MAX_UPLOAD", _DEFAULT_MAX_UPLOAD))

    @app.middleware("http")
    async def require_api_key(request, call_next):
        """Enforce X-API-Key when POLYFOAMMESH_API_KEY is set (/health stays open)."""
        if api_key and request.url.path != "/health":
            given = request.headers.get("x-api-key", "")
            if not hmac.compare_digest(given.encode(), api_key.encode()):
                return JSONResponse(status_code=401, content={"detail": "Invalid API key"})
        return await call_next(request)

    def _save_job(job: dict[str, Any]) -> None:
        path = _jobs_dir / f"{job['job_id']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)

    def _load_job(job_id: str) -> dict[str, Any]:
        """Return a job from memory or disk, or raise 404."""
        if not _ID_RE.fullmatch(job_id):
            raise HTTPException(404, f"Job {job_id} not found")
        job = _jobs.get(job_id)
        if job is not None:
            return job
        job_file = _jobs_dir / f"{job_id}.json"
        if not job_file.exists():
            raise HTTPException(404, f"Job {job_id} not found")
        job = json.loads(job_file.read_text(encoding="utf-8"))
        # Not in memory yet unfinished on disk: the process running it died.
        if job.get("status") in (JobStatus.PENDING, JobStatus.RUNNING):
            job["status"] = JobStatus.FAILED
            job["error"] = "Server restarted while the job was running"
            job["completed_at"] = datetime.now().isoformat()
            _save_job(job)
        _jobs[job_id] = job
        return job

    def _execute_job(job_id: str, geo_path: Path, params: MeshParams) -> None:
        """Background task: run the job and publish its final state."""
        job = _jobs[job_id]
        report = _run_mesh_job(job_id, geo_path, params, _results_dir)
        job["status"] = JobStatus.COMPLETED if report.get("success") else JobStatus.FAILED
        job["cell_count"] = int(report.get("cell_count") or 0)
        job["error"] = str(report.get("error") or "; ".join(report.get("errors") or []))
        job["completed_at"] = datetime.now().isoformat()
        _save_job(job)

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    @app.get("/health", response_model=HealthResponse)
    async def health():
        from polyfoammesh import __version__ as app_version
        return HealthResponse(
            status="ok",
            version=app_version if hasattr(app_version, '__str__') else "2.0.1",
        )

    @app.post("/upload", response_model=dict[str, Any])
    async def upload_geometry(file: UploadFile = File(...)):
        """Upload a STEP or STL geometry file.

        Returns a ``file_id`` that can be used in subsequent job submissions.
        """
        if not file.filename:
            raise HTTPException(400, "No filename provided")

        ext = Path(file.filename).suffix.lower()
        if ext not in (".step", ".stp", ".stl"):
            raise HTTPException(400, f"Unsupported format: {ext}. Use .step, .stp, or .stl")

        file_id = uuid.uuid4().hex[:12]
        dest = _uploads_dir / f"{file_id}{ext}"

        # Stream to a temp name so a rejected upload never stays visible.
        part = dest.with_name(dest.name + ".part")
        size = 0
        try:
            with open(part, "wb") as fh:
                while chunk := await file.read(_UPLOAD_CHUNK):
                    size += len(chunk)
                    if size > max_upload:
                        raise HTTPException(413, f"File larger than {max_upload} bytes")
                    fh.write(chunk)
            part.replace(dest)
        finally:
            part.unlink(missing_ok=True)

        octo.log_event("api", "geometry_upload", {
            "file_id": file_id,
            "name": file.filename,
            "size_bytes": size,
        })

        return {
            "file_id": file_id,
            "filename": file.filename,
            "size_bytes": size,
            "url": f"/uploads/{file_id}{ext}",
        }

    @app.post("/mesh", response_model=JobResponse)
    async def create_mesh_job(
        background_tasks: BackgroundTasks,
        file_id: str = Form(...),
        params: str = Form("{}"),
    ):
        """Submit a meshing job.

        Args:
            file_id: File ID from a previous ``/upload`` call.
            params: JSON string of ``MeshParams``.

        Returns:
            A ``JobResponse`` with the job ID for status polling.
        """
        # Validate before globbing: file_id must never act as a path/pattern.
        uploads = (
            [p for p in _uploads_dir.glob(f"{file_id}.*") if not p.name.endswith(".part")]
            if _ID_RE.fullmatch(file_id) else []
        )
        if not uploads:
            raise HTTPException(404, f"File {file_id} not found. Upload first via POST /upload")

        geo_path = uploads[0]
        parsed_params = _parse_params(params)

        job_id = uuid.uuid4().hex[:12]
        job = {
            "job_id": job_id,
            "status": JobStatus.RUNNING,
            "geometry": str(geo_path),
            "params": parsed_params.model_dump(),
            "created_at": datetime.now().isoformat(),
            "completed_at": "",
            "cell_count": 0,
            "error": "",
        }
        _jobs[job_id] = job
        _save_job(job)
        background_tasks.add_task(_execute_job, job_id, geo_path, parsed_params)

        return JobResponse(
            job_id=job_id,
            status=job["status"],
            created_at=job["created_at"],
            completed_at=job.get("completed_at", ""),
            cell_count=job.get("cell_count", 0),
            error=job.get("error", ""),
            result_url=f"/results/{job_id}",
        )

    @app.get("/jobs/{job_id}", response_model=JobResponse)
    async def get_job_status(job_id: str):
        """Poll the status of a submitted job."""
        job = _load_job(job_id)

        return JobResponse(
            job_id=job["job_id"],
            status=job["status"],
            created_at=job.get("created_at", ""),
            completed_at=job.get("completed_at", ""),
            cell_count=job.get("cell_count", 0),
            error=job.get("error", ""),
            result_url=f"/results/{job_id}",
        )

    @app.get("/results/{job_id}")
    async def get_job_result(job_id: str):
        """Download the result archive or JSON report for a job."""
        job = _load_job(job_id)
        if job["status"] != JobStatus.COMPLETED:
            raise HTTPException(400, f"Job {job_id} is {job['status']}, not completed")

        # Look for result file
        result_files = list(_results_dir.glob(f"{job_id}*"))
        if result_files:
            return FileResponse(
                str(result_files[0]),
                filename=result_files[0].name,
            )

        # Return JSON summary
        return JSONResponse(content=job)

    @app.get("/jobs", response_model=list[JobResponse])
    async def list_jobs(limit: int = 20, offset: int = 0):
        """List recent jobs."""
        all_jobs = list(_jobs.values())
        all_jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
        page = all_jobs[offset:offset + limit]
        return [
            JobResponse(
                job_id=j["job_id"],
                status=j["status"],
                created_at=j.get("created_at", ""),
                cell_count=j.get("cell_count", 0),
                error=j.get("error", ""),
            )
            for j in page
        ]

    @app.post("/batch", response_model=dict[str, Any])
    def batch_mesh(params: str = Form("{}")):
        """Batch mesh all uploaded geometry files.

        Processes every file in the uploads directory with the same
        parameters. Returns a batch report.  Plain ``def``: FastAPI runs it
        in a worker thread, so long meshing does not block the event loop.
        """
        parsed_params = _parse_params(params)
        geo_files = list(_uploads_dir.glob("*.step")) + list(_uploads_dir.glob("*.stp")) + list(_uploads_dir.glob("*.stl"))

        if not geo_files:
            return {"success": False, "error": "No geometry files uploaded", "total": 0}

        results = []
        for geo in geo_files:
            try:
                result = _run_mesh_job_sync(geo, parsed_params)
                results.append({
                    "file": geo.name,
                    "success": result["success"],
                    "cell_count": result.get("cell_count", 0),
                })
            except Exception as exc:
                results.append({"file": geo.name, "success": False, "error": str(exc)})

        succeeded = sum(1 for r in results if r["success"])
        return {
            "success": True,
            "total": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
            "results": results,
        }

    logger.info("API server created. Data dir: %s", _data_dir)
    return app


def _parse_params(params: str) -> MeshParams:
    """Parse the ``params`` form field; bad input is the client's error."""
    try:
        return MeshParams(**json.loads(params))
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"Invalid params: {exc}") from exc


def _run_mesh_job_sync(geo_path: Path, params: MeshParams) -> dict[str, Any]:
    """Run a meshing job synchronously and return results."""
    from polyfoammesh.commercial.watertight import WatertightWorkflow

    wf = WatertightWorkflow()
    wf.set_geometry(str(geo_path))
    wf.set_cell_sizes(params.max_cell, params.min_cell)

    if params.bl_enabled:
        wf.set_boundary_layers(
            n_layers=params.bl_n_layers,
            thickness_ratio=params.bl_thickness_ratio,
            expansion_ratio=params.bl_expansion_ratio,
        )

    result = wf.run()
    return {
        "success": result.success,
        "cell_count": result.cell_count,
        "wall_time_s": result.wall_time_seconds,
        "errors": result.errors,
        "steps": result.steps_completed,
    }


def _run_mesh_job(
    job_id: str, geo_path: Path, params: MeshParams,
    results_dir: Path,
) -> dict[str, Any]:
    """Run meshing, persist the report and return it."""
    from polyfoammesh.commercial.watertight import WatertightWorkflow

    try:
        wf = WatertightWorkflow()
        wf.set_geometry(str(geo_path))
        wf.set_cell_sizes(params.max_cell, params.min_cell)
        if params.bl_enabled:
            wf.set_boundary_layers(
                n_layers=params.bl_n_layers,
                thickness_ratio=params.bl_thickness_ratio,
                expansion_ratio=params.bl_expansion_ratio,
            )

        result = wf.run()
        report = {
            "success": result.success,
            "cell_count": result.cell_count,
            "wall_time_s": result.wall_time_seconds,
            "steps": result.steps_completed,
            "errors": result.errors,
        }
    except Exception as exc:
        report = {"success": False, "error": str(exc)}

    result_path = results_dir / f"{job_id}_result.json"
    result_path.write_text(json.dumps(report, indent=2, default=str))
    logger.info("Background job %s completed: success=%s", job_id, report.get("success"))
    return report


# ---------------------------------------------------------------------------
# Module-level app for uvicorn, built on first access so that importing this
# module never creates ./api_data as a side effect.
# ---------------------------------------------------------------------------
def __getattr__(name: str) -> Any:
    if name == "app":
        if not _HAS_FASTAPI:
            return None
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
