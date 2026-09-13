"""Job lifecycle management + persistence: jobs, results, domain difficulty."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import delete, func, select

from src.core.schemas import JobState, ScrapeJob
from src.storage.models import DomainRow, JobResultRow, JobRow

log = structlog.get_logger(__name__)


class JobManager:
    def __init__(self, session_factory, artifacts=None):
        self.session_factory = session_factory
        self.artifacts = artifacts

    # -- jobs ---------------------------------------------------------------
    async def save(self, job: ScrapeJob) -> None:
        async with self.session_factory() as session:
            row = await session.get(JobRow, job.job_id)
            if row is None:
                row = JobRow.from_job(job)
                session.add(row)
            else:
                row.state = job.state.value
                row.error_log = job.error_log
                row.stats = job.stats
                row.result_location = job.result_location
                row.priority = job.priority
                row.started_at = job.started_at
                row.finished_at = job.finished_at
                row.updated_at = datetime.now(timezone.utc)
            await session.commit()

    async def get(self, job_id: str) -> ScrapeJob | None:
        async with self.session_factory() as session:
            row = await session.get(JobRow, job_id)
            if row is None:
                return None
            return self._to_job(row)

    async def list_jobs(self, *, state: str | None = None, limit: int = 50,
                        offset: int = 0) -> tuple[list[ScrapeJob], int]:
        async with self.session_factory() as session:
            query = select(JobRow).order_by(JobRow.created_at.desc())
            count_q = select(func.count()).select_from(JobRow)
            if state:
                query = query.where(JobRow.state == state)
                count_q = count_q.where(JobRow.state == state)
            total = (await session.execute(count_q)).scalar() or 0
            rows = (await session.execute(query.limit(limit).offset(offset))).scalars().all()
            return [self._to_job(r) for r in rows], total

    async def delete(self, job_id: str) -> bool:
        async with self.session_factory() as session:
            await session.execute(delete(JobResultRow).where(JobResultRow.job_id == job_id))
            row = await session.get(JobRow, job_id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
        if self.artifacts:
            import shutil
            from pathlib import Path

            d = Path(self.artifacts.root) / job_id
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        return True

    # -- results --------------------------------------------------------------
    async def save_results(self, job_id: str, items: list[dict[str, Any]], *,
                           page_number: int, page_url: str, strategy: str) -> None:
        async with self.session_factory() as session:
            for item in items:
                session.add(JobResultRow(job_id=job_id, page_number=page_number,
                                         page_url=page_url, extraction_strategy=strategy,
                                         data=item))
            await session.commit()

    async def get_results(self, job_id: str, *, limit: int = 100, offset: int = 0
                          ) -> tuple[list[dict[str, Any]], int]:
        async with self.session_factory() as session:
            base = select(JobResultRow).where(JobResultRow.job_id == job_id)
            total = (await session.execute(
                select(func.count()).select_from(base.subquery()))).scalar() or 0
            rows = (await session.execute(
                base.order_by(JobResultRow.created_at, JobResultRow.id)
                .limit(limit).offset(offset))).scalars().all()
            return [{"id": r.id, "page_number": r.page_number, "page_url": r.page_url,
                     "strategy": r.extraction_strategy, "data": r.data,
                     "created_at": r.created_at.isoformat()} for r in rows], total

    async def query_results(self, *, domain: str | None = None, search: str | None = None,
                            limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
        """Query results across jobs (dashboard 'Results' tab)."""
        async with self.session_factory() as session:
            query = select(JobResultRow).join(JobRow, JobResultRow.job_id == JobRow.id)
            count_q = select(func.count()).select_from(
                JobResultRow).join(JobRow, JobResultRow.job_id == JobRow.id)
            if domain:
                query, count_q = query.where(JobRow.domain == domain), count_q.where(JobRow.domain == domain)
            if search:
                like = f"%{search}%"
                # JSON LIKE filtering (SQLite/PG compatible enough for the dashboard)
                query = query.where(JobResultRow.data.cast(str).like(like))
                count_q = count_q.where(JobResultRow.data.cast(str).like(like))
            total = (await session.execute(count_q)).scalar() or 0
            rows = (await session.execute(
                query.order_by(JobResultRow.created_at.desc()).limit(limit).offset(offset))
            ).scalars().all()
            out = []
            for r in rows:
                out.append({"job_id": r.job_id, "page_number": r.page_number,
                            "data": r.data, "created_at": r.created_at.isoformat()})
            return out, total

    # -- domain learning --------------------------------------------------------
    async def update_domain(self, domain: str, *, success: bool, challenge: bool = False,
                            duration: float | None = None,
                            rendering_used: str | None = None) -> str:
        """Difficulty scoring: easy (lightweight ok) < medium (browser needed)
        < hard (challenges present). Returns the new score."""
        async with self.session_factory() as session:
            row = (await session.execute(
                select(DomainRow).where(DomainRow.domain == domain))).scalar_one_or_none()
            if row is None:
                row = DomainRow(domain=domain, success_count=0, failure_count=0,
                                challenge_count=0, avg_duration_seconds=0.0)
                session.add(row)
            row.success_count = row.success_count or 0
            row.failure_count = row.failure_count or 0
            row.challenge_count = row.challenge_count or 0
            row.avg_duration_seconds = row.avg_duration_seconds or 0.0
            if success:
                row.success_count += 1
                if challenge:
                    row.difficulty = "hard"
                elif rendering_used == "lightweight":
                    row.difficulty = "easy" if row.difficulty != "hard" else "hard"
                elif rendering_used in ("browser", "full_browser"):
                    if row.difficulty not in ("hard",):
                        row.difficulty = "medium"
            else:
                row.failure_count += 1
                if row.failure_count >= 2 and row.difficulty != "hard":
                    row.difficulty = "hard" if row.challenge_count else "medium"
            if challenge:
                row.challenge_count += 1
            if duration is not None:
                n = row.success_count + row.failure_count
                row.avg_duration_seconds = round(
                    (row.avg_duration_seconds * (n - 1) + duration) / max(n, 1), 3)
            await session.commit()
            return row.difficulty

    async def get_domain(self, domain: str) -> dict[str, Any] | None:
        async with self.session_factory() as session:
            row = (await session.execute(
                select(DomainRow).where(DomainRow.domain == domain))).scalar_one_or_none()
            if row is None:
                return None
            return {"domain": row.domain, "difficulty": row.difficulty,
                    "success_count": row.success_count, "failure_count": row.failure_count,
                    "challenge_count": row.challenge_count,
                    "avg_duration_seconds": row.avg_duration_seconds}

    # -- helpers ------------------------------------------------------------------
    @staticmethod
    def _to_job(row: JobRow) -> ScrapeJob:
        from src.core.schemas import ScrapeJobConfig

        return ScrapeJob(
            job_id=row.id, url=row.url, domain=row.domain, priority=row.priority,
            config=ScrapeJobConfig.model_validate(row.config or {}),
            state=JobState(row.state), created_at=row.created_at, updated_at=row.updated_at,
            started_at=row.started_at, finished_at=row.finished_at,
            result_location=row.result_location, error_log=row.error_log or [],
            stats=row.stats or {},
        )
