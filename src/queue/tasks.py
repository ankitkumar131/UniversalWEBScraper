"""Celery task definitions: thin wrappers around the async engine."""
from __future__ import annotations

import asyncio

import structlog

log = structlog.get_logger(__name__)

try:
    from src.queue.celery_app import celery_app
except ImportError:  # celery not installed — module unused in local mode
    celery_app = None


if celery_app is not None:

    @celery_app.task(name="uws.scrape.run", bind=True, max_retries=3)
    def run_scrape_job(self, job_dict: dict) -> dict:
        """Execute a scrape job (serialized ScrapeJob.model_dump())."""
        import asyncio

        from src.core.config import get_settings
        from src.core.engine import ScrapeEngine
        from src.core.schemas import ScrapeJob
        from src.storage.database import create_tables, init_engine

        async def _inner() -> dict:
            settings = get_settings()
            settings.ensure_dirs()
            init_engine(settings.database_url)
            await create_tables()
            job = ScrapeJob.model_validate(job_dict)
            engine = ScrapeEngine.build(settings)
            try:
                await engine.run(job)
            finally:
                await engine.close()
            return {"job_id": job.job_id, "state": job.state.value,
                    "stats": job.stats}

        result = asyncio.new_event_loop().run_until_complete(_inner())
        log.info("celery_job_done", **result)
        return result

    @celery_app.task(name="uws.scrape.lightweight")
    def run_lightweight(job_dict: dict) -> dict:
        return run_scrape_job.apply_async(args=[job_dict], queue="lightweight").get()
