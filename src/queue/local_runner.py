"""Local (in-process) job runner: the orchestration layer for single-node mode.

Each submitted job becomes a tracked asyncio.Task (cancellable), bounded by a
concurrency semaphore. For horizontal scaling the same engine is driven by
Celery workers instead (src/queue/tasks.py)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog

from src.core.schemas import JobState, ScrapeJob

log = structlog.get_logger(__name__)


class LocalRunner:
    def __init__(self, engine, job_manager, rate_limiter, max_concurrent: int = 4):
        self.engine = engine
        self.jobs = job_manager
        self.rate_limiter = rate_limiter
        self.max_concurrent = max_concurrent
        self._tasks: dict[str, asyncio.Task] = {}
        self._sem = asyncio.Semaphore(max_concurrent)

    async def start(self, workers: int = 2) -> None:
        log.info("local_runner_started", max_concurrent=self.max_concurrent)

    async def submit(self, job: ScrapeJob) -> None:
        await self.jobs.save(job)
        task = asyncio.get_event_loop().create_task(self._run_job(job))
        self._tasks[job.job_id] = task

    async def _run_job(self, job: ScrapeJob) -> None:
        async with self._sem:
            if job.state == JobState.cancelled:
                return
            job.state = JobState.running
            await self.jobs.save(job)
            try:
                await self.engine.run(job)
            except asyncio.CancelledError:
                job.state = JobState.cancelled
                job.error_log.append({"ts": _now(), "step": "runner", "error": "cancelled"})
                await self.jobs.save(job)
                log.info("job_cancelled", job_id=job.job_id)
            except Exception as e:
                log.error("job_crashed", job_id=job.job_id, error=str(e))
                job.state = JobState.failed
                job.error_log.append({"ts": _now(), "step": "runner", "error": str(e)})
                await self.jobs.save(job)
            finally:
                self._tasks.pop(job.job_id, None)

    async def cancel(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    @property
    def queue_depth(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done())

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
