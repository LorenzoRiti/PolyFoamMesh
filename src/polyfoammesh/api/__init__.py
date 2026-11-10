"""REST API for headless CI/CD integration.

Provides a FastAPI-based HTTP API that exposes all meshing
functionality: geometry upload, parameter configuration, job
submission, status polling, and result download.

Usage (dev server)::

    uvicorn polyfoammesh.api.server:app --host 127.0.0.1 --port 8000

Before exposing it beyond localhost, set ``POLYFOAMMESH_API_KEY``: requests
must then send it as ``X-API-Key``.  ``POLYFOAMMESH_API_MAX_UPLOAD`` caps the
upload size in bytes (default 2 GiB).

Usage (production)::

    pip install gunicorn uvicorn
    gunicorn -k uvicorn.workers.UvicornWorker polyfoammesh.api.server:app
"""

from __future__ import annotations
