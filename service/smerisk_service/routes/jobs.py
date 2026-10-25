"""GET /v1/jobs/{job_id}: track asynchronous jobs (§2.3)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from smerisk_service.auth import caller
from smerisk_service.errors import ERROR_RESPONSES
from smerisk_service.jobs import JobStore, JobView

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


@router.get("/{job_id}", name="get_job", responses={401: ERROR_RESPONSES[401], 404: {"description": "Unknown job"}})
def get_job(job_id: str, request: Request, who: Annotated[str, Depends(caller)]) -> JobView:
    """Status of a job and, once it has succeeded, the full response envelope. Results are kept 24 hours."""
    jobs: JobStore = request.app.state.jobs
    view = jobs.get(who, job_id)
    if view is None:
        raise HTTPException(404, "unknown or expired job")
    return view
