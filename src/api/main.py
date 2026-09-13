"""FastAPI application: dashboard + REST API + metrics.

Single-node mode: an in-process runner executes jobs (queue.mode=local).
Distributed mode: set queue.mode=celery; workers run src/queue/tasks.py.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src import __version__
from src.core.config import get_settings
from src.core.engine import ScrapeEngine
from src.core.job_manager import JobManager
from src.monitoring.logging import setup_logging
from src.queue.local_runner import LocalRunner
from src.storage.artifacts import ArtifactStore
from src.storage.database import create_tables, dispose_engine, init_engine, session_factory

log = structlog.get_logger(__name__)

_STATE = None  # AppState singleton


@dataclass
class AppState:
    settings: object
    engine: ScrapeEngine
    jobs: JobManager
    artifacts: ArtifactStore
    runner: LocalRunner
    session_factory: object


def app_state() -> AppState:
    assert _STATE is not None, "app not initialized"
    return _STATE


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _STATE
    settings = get_settings()
    settings.ensure_dirs()
    setup_logging(settings.get("monitoring.log_level", "INFO"))

    init_engine(settings.database_url)
    await create_tables()

    artifacts = ArtifactStore(settings.artifacts_dir, settings.get("storage.s3"))
    jobs = JobManager(session_factory(), artifacts)
    engine = ScrapeEngine.build(settings, job_manager=jobs, artifacts=artifacts)
    runner = LocalRunner(engine, jobs, engine.rate_limiter,
                         max_concurrent=int(settings.get("engine.max_contexts", 4)))
    await runner.start(workers=2)
    if engine.proxies.active:
        engine.proxies.start_periodic_checks()

    _STATE = AppState(settings=settings, engine=engine, jobs=jobs, artifacts=artifacts,
                      runner=runner, session_factory=session_factory())
    log.info("app_started", version=__version__, db=settings.database_url.split("///")[-1])
    try:
        yield
    finally:
        await runner.stop()
        await engine.close()
        await dispose_engine()
        log.info("app_stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Universal Web Scraper",
        version=__version__,
        description="Resilient, scalable, intelligent web scraping platform",
        lifespan=lifespan,
    )
    from src.api.routes import health, jobs, results

    app.include_router(jobs.router)
    app.include_router(results.router)
    app.include_router(health.router)

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(static_dir / "index.html")

    return app


app = create_app()


def main() -> None:  # python -m src.api.main
    import uvicorn

    settings = get_settings()
    uvicorn.run("src.api.main:app", host=settings.get("app.host", "0.0.0.0"),
                port=int(settings.get("app.port", 8000)), reload=False)


if __name__ == "__main__":
    main()
