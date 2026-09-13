"""Job routes: submit, list, inspect, cancel, delete, artifacts."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from src.monitoring import metrics
from src.api.schemas.job_config import JobListResponse, ScrapeJobResponse, ScrapeRequest
from src.api.schemas.responses import MessageResponse
from src.core.schemas import JobState, ScrapeJob

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _runner():
    from src.api.main import app_state

    return app_state()


@router.post("", response_model=ScrapeJobResponse, status_code=202)
async def submit_job(request: ScrapeRequest) -> ScrapeJobResponse:
    """Submit a scrape job. Returns immediately; poll GET /api/jobs/{id}."""
    state = _runner()
    try:
        job = request.to_job()
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"invalid job config: {e}") from e
    await state.runner.submit(job)
    metrics.queue_depth(state.runner.queue_depth)
    return ScrapeJobResponse.from_job(job)


@router.get("", response_model=JobListResponse)
async def list_jobs(
    state_filter: str | None = Query(default=None, alias="state"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> JobListResponse:
    state = _runner()
    if state_filter and state_filter not in JobState.__members__:
        raise HTTPException(422, f"unknown state '{state_filter}'")
    jobs, total = await state.jobs.list_jobs(state=state_filter, limit=limit, offset=offset)
    return JobListResponse(total=total, jobs=[ScrapeJobResponse.from_job(j) for j in jobs])


@router.get("/{job_id}", response_model=ScrapeJobResponse)
async def get_job(job_id: str) -> ScrapeJobResponse:
    state = _runner()
    job = await state.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return ScrapeJobResponse.from_job(job)


@router.delete("/{job_id}", response_model=MessageResponse)
async def delete_job(job_id: str) -> MessageResponse:
    state = _runner()
    if not await state.jobs.delete(job_id):
        raise HTTPException(404, "job not found")
    return MessageResponse(message="deleted", job_id=job_id)


@router.post("/{job_id}/cancel", response_model=MessageResponse)
async def cancel_job(job_id: str) -> MessageResponse:
    state = _runner()
    job = await state.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.state in (JobState.completed, JobState.failed, JobState.cancelled):
        return MessageResponse(message=f"job already {job.state.value}", job_id=job_id)
    cancelled = await state.runner.cancel(job_id)
    return MessageResponse(
        message="cancellation requested" if cancelled else "job not running (will not start)",
        job_id=job_id)
