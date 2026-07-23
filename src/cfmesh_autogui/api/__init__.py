"""REST API for headless CI/CD integration.

Provides a FastAPI-based HTTP API that exposes all meshing
functionality: geometry upload, parameter configuration, job
submission, status polling, and result download.

Usage (dev server)::

    uvicorn cfmesh_autogui.api.server:app --host 0.0.0.0 --port 8000

Usage (production)::

    pip install gunicorn uvicorn
    gunicorn -k uvicorn.workers.UvicornWorker cfmesh_autogui.api.server:app
"""

from __future__ import annotations
