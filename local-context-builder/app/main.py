"""FastAPI application entry point.

The admin router (plan §14) is mounted here; ``/admin/health`` stays
unauthenticated so docker/k8s liveness probes keep working, while
every other ``/admin/*`` route requires the ``X-Admin-Key`` header.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.admin import router as admin_router

app = FastAPI(
    title="local-context-builder",
    description="Batch service that builds spot context datasets.",
    version="0.1.0",
)

# Admin router owns ``/admin/*`` — including the unauthenticated
# ``/admin/health`` probe. Do NOT re-add an ``@app.get('/admin/health')``
# handler here; the router supplies it.
app.include_router(admin_router)
