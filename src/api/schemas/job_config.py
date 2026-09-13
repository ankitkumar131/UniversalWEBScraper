"""API schemas: request/response models (canonical job config lives in
src/core/schemas.py and is re-exported here per the sd.txt folder layout)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.core.schemas import (ExtractionConfig, OutputConfig, PaginationConfig,
                              ProxyConfig, RetryConfig, ScrapeJob,
                              ScrapeJobConfig, WaitStrategy)

__all__ = ["ScrapeRequest", "ScrapeJobResponse", "ScrapeJobConfig", "ScrapeJob",
           "ExtractionConfig", "PaginationConfig", "WaitStrategy", "ProxyConfig",
           "RetryConfig", "OutputConfig", "ResultsResponse", "ResultItem",
           "JobListResponse", "StatsResponse"]


class ScrapeRequest(BaseModel):
    """POST /api/jobs body — everything except url is optional."""
    url: str
    priority: int = Field(default=5, ge=1, le=10)
    rendering: ScrapeJobConfig.model_fields["rendering"].annotation | None = None
    browser: ScrapeJobConfig.model_fields["browser"].annotation | None = None
    headless: bool | None = None
    wait_strategy: WaitStrategy | None = None
    extraction: ExtractionConfig | None = None
    pagination: PaginationConfig | None = None
    popup_handling: bool | None = None
    respect_robots: bool | None = None
    proxy: ProxyConfig | None = None
    retry: RetryConfig | None = None
    output: OutputConfig | None = None

    def to_job(self) -> ScrapeJob:
        base = ScrapeJobConfig()
        data = base.model_dump()
        for field in ("rendering", "browser", "headless", "popup_handling"):
            value = getattr(self, field)
            if value is not None:
                data[field] = value
        for field in ("wait_strategy", "extraction", "pagination", "proxy", "retry", "output"):
            value = getattr(self, field)
            if value is not None:
                data[field] = value.model_dump()
        if self.respect_robots is not None:
            data["respect_robots"] = self.respect_robots
        config = ScrapeJobConfig.model_validate(data)
        return ScrapeJob(url=self.url, priority=self.priority, config=config)


class ScrapeJobResponse(BaseModel):
    job_id: str
    url: str
    domain: str
    state: str
    priority: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    config: dict[str, Any]
    stats: dict[str, Any] = {}
    error_log: list[dict[str, Any]] = []
    result_location: str | None = None

    @classmethod
    def from_job(cls, job: ScrapeJob) -> "ScrapeJobResponse":
        return cls(
            job_id=job.job_id, url=job.url, domain=job.domain, state=job.state.value,
            priority=job.priority, created_at=job.created_at, updated_at=job.updated_at,
            started_at=job.started_at, finished_at=job.finished_at,
            config=job.config.model_dump(), stats=job.stats, error_log=job.error_log,
            result_location=job.result_location,
        )


class ResultItem(BaseModel):
    id: str
    page_number: int
    page_url: str
    strategy: str
    data: dict[str, Any]
    created_at: str


class ResultsResponse(BaseModel):
    job_id: str
    total: int
    items: list[ResultItem]


class JobListResponse(BaseModel):
    total: int
    jobs: list[ScrapeJobResponse]


class StatsResponse(BaseModel):
    jobs_total: int
    jobs_completed: int
    jobs_failed: int
    jobs_running: int
    jobs_queued: int
    items_extracted: int
    domains_tracked: int
