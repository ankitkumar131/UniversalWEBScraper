"""Result routes: query scraped data, serve artifacts, domain info."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response

from src.api.schemas.job_config import ResultItem, ResultsResponse
from src.api.schemas.responses import DomainInfoResponse
from src.api.routes.jobs import _runner

router = APIRouter(prefix="/api", tags=["results"])


@router.get("/jobs/{job_id}/results", response_model=ResultsResponse)
async def job_results(job_id: str, limit: int = Query(default=100, ge=1, le=1000),
                      offset: int = Query(default=0, ge=0)) -> ResultsResponse:
    state = _runner()
    if await state.jobs.get(job_id) is None:
        raise HTTPException(404, "job not found")
    items, total = await state.jobs.get_results(job_id, limit=limit, offset=offset)
    return ResultsResponse(job_id=job_id, total=total,
                           items=[ResultItem(**i) for i in items])


@router.get("/results")
async def query_results(domain: str | None = None, search: str | None = None,
                        limit: int = Query(default=100, ge=1, le=1000),
                        offset: int = Query(default=0, ge=0)) -> dict:
    state = _runner()
    items, total = await state.jobs.query_results(domain=domain, search=search,
                                                  limit=limit, offset=offset)
    return {"total": total, "items": items}


@router.get("/jobs/{job_id}/artifacts/{filename}")
async def get_artifact(job_id: str, filename: str) -> Response:
    state = _runner()
    try:
        data = await state.artifacts.read(job_id, filename)
    except FileNotFoundError:
        raise HTTPException(404, "artifact not found") from None
    media = "text/html" if filename.endswith(".html") else (
        "image/png" if filename.endswith(".png") else "application/octet-stream")
    return Response(content=data, media_type=media)


@router.get("/jobs/{job_id}/artifacts")
async def list_artifacts(job_id: str) -> dict:
    state = _runner()
    return {"job_id": job_id, "artifacts": await state.artifacts.list_artifacts(job_id)}


@router.get("/domains/{domain}", response_model=DomainInfoResponse)
async def domain_info(domain: str) -> DomainInfoResponse:
    state = _runner()
    info = await state.jobs.get_domain(domain)
    if info is None:
        raise HTTPException(404, "domain not seen yet")
    return DomainInfoResponse(**info)


@router.get("/stats")
async def stats() -> dict:
    from sqlalchemy import func, select

    from src.storage.models import DomainRow, JobResultRow, JobRow

    state = _runner()
    async with state.session_factory() as session:
        total = (await session.execute(select(func.count()).select_from(JobRow))).scalar() or 0
        by_state = dict((await session.execute(
            select(JobRow.state, func.count()).group_by(JobRow.state))).all())
        items = (await session.execute(
            select(func.count()).select_from(JobResultRow))).scalar() or 0
        domains = (await session.execute(
            select(func.count()).select_from(DomainRow))).scalar() or 0
    return {
        "jobs_total": total,
        "jobs_completed": by_state.get("completed", 0),
        "jobs_failed": by_state.get("failed", 0),
        "jobs_running": by_state.get("running", 0),
        "jobs_queued": by_state.get("queued", 0),
        "items_extracted": items,
        "domains_tracked": domains,
    }
