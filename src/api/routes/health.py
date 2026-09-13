"""Health & metrics endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Response

from src import __version__
from src.monitoring import metrics
from src.api.schemas.responses import HealthResponse
from src.api.routes.jobs import _runner
from src.monitoring import health as health_checks

router = APIRouter(tags=["health"])


@router.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    state = _runner()
    components = {
        "database": await health_checks.check_database(state.session_factory),
        "browser": health_checks.check_browser(state.engine.browser),
        "redis": health_checks.check_redis(state.engine.cache),
        "runner": {"status": "up", "queue_depth": state.runner.queue_depth},
    }
    overall = ("degraded" if any(
        c.get("status") == "down" for c in components.values()) else "up")
    return HealthResponse(status=overall, version=__version__, components=components)


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    rendered = metrics.render_metrics()
    if rendered is None:
        return Response(content="# prometheus_client not installed\n", media_type="text/plain")
    body, content_type = rendered
    return Response(content=body, media_type=content_type)
