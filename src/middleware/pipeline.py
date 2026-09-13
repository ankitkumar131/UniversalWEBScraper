"""Middleware pipeline executor: ordered steps, per-step timing, structured logs."""
from __future__ import annotations

import time

import structlog

from src.middleware.base import BaseMiddleware, ScrapingContext, StepReport

log = structlog.get_logger(__name__)


class MiddlewarePipeline:
    def __init__(self, middlewares: list[BaseMiddleware]):
        self.middlewares = middlewares

    async def run(self, ctx: ScrapingContext) -> list[StepReport]:
        for middleware in self.middlewares:
            if not middleware.enabled(ctx):
                ctx.report(StepReport(middleware.name, ok=True, detail="skipped (disabled)"))
                continue
            start = time.perf_counter()
            try:
                report = await middleware.run(ctx)
                if not isinstance(report, StepReport):
                    report = StepReport(middleware.name, detail=str(report))
                report.duration_ms = (time.perf_counter() - start) * 1000
            except Exception as e:
                report = StepReport(
                    middleware.name, ok=False,
                    detail=f"{type(e).__name__}: {e}",
                    duration_ms=(time.perf_counter() - start) * 1000,
                )
                ctx.report(report)
                log.warning("middleware_failed", step=middleware.name, error=str(e),
                            job_id=ctx.job.job_id)
                raise
            ctx.report(report)
            log.debug("middleware_step", step=middleware.name, ok=report.ok,
                      detail=report.detail, ms=round(report.duration_ms),
                      job_id=ctx.job.job_id)
        return ctx.steps
