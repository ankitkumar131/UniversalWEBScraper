"""Middleware base: every concern (page loading, challenge solving, popup
dismissal, cookie consent, content readiness) is a pluggable step in the
pipeline (sd.txt "Core Design Principles" — Modularity)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.browser.interceptors.network import NetworkInterceptor
from src.core.schemas import JobStats, PageContent, ScrapeJob


@dataclass
class StepReport:
    name: str
    ok: bool = True
    detail: str = ""
    duration_ms: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail,
                "duration_ms": round(self.duration_ms, 1), **self.extra}


@dataclass
class ScrapingContext:
    """State carried through the middleware pipeline for one page load."""
    job: ScrapeJob
    settings: Any
    page: Any = None                     # live Playwright page (browser mode)
    content: PageContent | None = None
    fingerprint: dict[str, Any] = field(default_factory=dict)
    proxy_url: str | None = None
    network: NetworkInterceptor | None = None
    stats: JobStats = field(default_factory=JobStats)
    flags: dict[str, Any] = field(default_factory=dict)
    steps: list[StepReport] = field(default_factory=list)
    page_number: int = 1

    def report(self, step: StepReport) -> None:
        self.steps.append(step)


class BaseMiddleware:
    """A pipeline step. Subclasses override `run()`; return False from
    `enabled()` to skip (e.g. popup_handling turned off)."""

    name: str = "middleware"

    def __init__(self, settings: Any | None = None):
        self.settings = settings

    def enabled(self, ctx: ScrapingContext) -> bool:
        return True

    async def run(self, ctx: ScrapingContext) -> StepReport:
        raise NotImplementedError
