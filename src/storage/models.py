"""SQLAlchemy ORM models: jobs, results, domains, proxies, identities, schedules."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    url: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    state: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    error_log: Mapped[list] = mapped_column(JSON, default=list)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    result_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @staticmethod
    def from_job(job) -> "JobRow":
        from src.core.schemas import ScrapeJob  # avoid circular import
        assert isinstance(job, ScrapeJob)
        return JobRow(
            id=job.job_id, url=job.url, domain=job.domain, priority=job.priority,
            state=job.state.value, config=job.config.model_dump(),
            error_log=job.error_log, stats=job.stats,
            created_at=job.created_at, updated_at=job.updated_at,
            started_at=job.started_at, finished_at=job.finished_at,
        )


class JobResultRow(Base):
    __tablename__ = "job_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer, default=1)
    page_url: Mapped[str] = mapped_column(Text, default="")
    extraction_strategy: Mapped[str] = mapped_column(String(48), default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DomainRow(Base):
    __tablename__ = "domains"

    domain: Mapped[str] = mapped_column(String(255), primary_key=True)
    difficulty: Mapped[str] = mapped_column(String(16), default="unknown")  # easy|medium|hard|unknown
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    challenge_count: Mapped[int] = mapped_column(Integer, default=0)
    known_selectors: Mapped[dict] = mapped_column(JSON, default=dict)
    robots_cache: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    robots_cached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    avg_duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ProxyRow(Base):
    __tablename__ = "proxies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    url: Mapped[str] = mapped_column(Text, unique=True)
    tier: Mapped[str] = mapped_column(String(16), default="datacenter")  # residential|isp|datacenter|mobile
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    healthy: Mapped[bool] = mapped_column(Boolean, default=True)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BrowserProfileRow(Base):
    __tablename__ = "browser_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    fingerprint: Mapped[dict] = mapped_column(JSON, default=dict)
    cookies: Mapped[dict] = mapped_column(JSON, default=dict)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ScheduleRow(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=86400)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
