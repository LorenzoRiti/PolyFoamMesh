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

import json
import logging

import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
    from fastapi.responses import FileResponse, JSONResponse
    from pydantic import BaseModel
    _HAS_FASTAPI = True
except ImportError:
    # fastapi not installed: the API server is unavailable (optional feature)
    logger.debug("FastAPI not installed — API server disabled", exc_info=True)
    _HAS_FASTAPI = False

from cfmesh_autogui.octopoda_local import octo

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

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    @app.get("/health", response_model=HealthResponse)
    async def health():
        from cfmesh_autogui import __version__ as app_version
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

        content = await file.read()
        dest.write_bytes(content)

        octo.log_event("api", "geometry_upload", {
            "file_id": file_id,
            "name": file.filename,
            "size_bytes": len(content),
        })

        return {
            "file_id": file_id,
            "filename": file.filename,
            "size_bytes": len(content),
            "url": f"/uploads/{file_id}{ext}",
        }

    @app.post("/mesh", response_model=JobResponse)
    async def create_mesh_job(
        file_id: str = Form(...),
        params: str = Form("{}"),
        background_tasks: BackgroundTasks = None,
    ):
        """Submit a meshing job.

        Args:
            file_id: File ID from a previous ``/upload`` call.
            params: JSON string of ``MeshParams``.

        Returns:
            A ``JobResponse`` with the job ID for status polling.
        """
        # Resolve file
        uploads = list(_uploads_dir.glob(f"{file_id}.*"))
        if not uploads:
            raise HTTPException(404, f"File {file_id} not found. Upload first via POST /upload")

        geo_path = uploads[0]
        parsed_params = MeshParams(**json.loads(params)) if isinstance(params, str) else params

        job_id = uuid.uuid4().hex[:12]
        job = {
            "job_id": job_id,
            "status": JobStatus.PENDING,
            "geometry": str(geo_path),
            "params": parsed_params.model_dump() if hasattr(parsed_params, 'model_dump') else str(parsed_params),
            "created_at": datetime.now().isoformat(),
            "completed_at": "",
            "cell_count": 0,
            "error": "",
        }
        _jobs[job_id] = job

        # Save job manifest
        (_jobs_dir / f"{job_id}.json").write_text(json.dumps(job, indent=2, default=str))

        # Submit background task if available
        if background_tasks is not None:
            background_tasks.add_task(_run_mesh_job, job_id, geo_path, parsed_params, _results_dir)
            job["status"] = JobStatus.RUNNING
        else:
            # Synchronous execution
            try:
                result = _run_mesh_job_sync(geo_path, parsed_params)
                job["status"] = JobStatus.COMPLETED if result["success"] else JobStatus.FAILED
                job["cell_count"] = result.get("cell_count", 0)
                job["completed_at"] = datetime.now().isoformat()
            except Exception as exc:
                job["status"] = JobStatus.FAILED
                job["error"] = str(exc)
                job["completed_at"] = datetime.now().isoformat()

        _jobs[job_id] = job
        (_jobs_dir / f"{job_id}.json").write_text(json.dumps(job, indent=2, default=str))

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
        job = _jobs.get(job_id)
        if job is None:
            # Try loading from disk
            job_file = _jobs_dir / f"{job_id}.json"
            if job_file.exists():
                job = json.loads(job_file.read_text())
                _jobs[job_id] = job
            else:
                raise HTTPException(404, f"Job {job_id} not found")

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
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, f"Job {job_id} not found")
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
    async def batch_mesh(params: str = Form("{}")):
        """Batch mesh all uploaded geometry files.

        Processes every file in the uploads directory with the same
        parameters. Returns a batch report.
        """
        parsed_params = MeshParams(**json.loads(params))
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


def _run_mesh_job_sync(geo_path: Path, params: MeshParams) -> dict[str, Any]:
    """Run a meshing job synchronously and return results."""
    from cfmesh_autogui.commercial.watertight import WatertightWorkflow

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
) -> None:
    """Background task: run meshing and persist results."""
    import json
    from cfmesh_autogui.commercial.watertight import WatertightWorkflow

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


# ---------------------------------------------------------------------------
# Module-level app for uvicorn
# ---------------------------------------------------------------------------
app = create_app() if _HAS_FASTAPI else None
