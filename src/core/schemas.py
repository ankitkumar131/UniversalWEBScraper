"""Pydantic schemas — the canonical ScrapeJob configuration model
(sd.txt section "Job Configuration Schema")."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobState(str, Enum):
    queued = "queued"
    running = "running"
    retrying = "retrying"
    completed = "completed"
    partially_completed = "partially_completed"
    failed = "failed"
    cancelled = "cancelled"


class WaitStrategy(BaseModel):
    type: Literal["network_idle", "domcontentloaded", "selector", "timeout", "custom"] = "network_idle"
    selector: str | None = None
    timeout_ms: int = Field(default=30000, ge=1000, le=180000)


class ExtractionConfig(BaseModel):
    mode: Literal["css_selectors", "xpath", "llm_auto", "hybrid"] = "hybrid"
    """Field -> selector mapping, or an items/fields list schema.

    Simple:  {"title": "h1", "price": ".price-tag"}
    List:    {"items": ".product", "fields": {"name": "h3", "price": ".price"}}
    Selectors may reference attributes with "@href" / "@src"; compound fallbacks
    are lists: {"price": [".price", "[itemprop='price']"]}
    """
    schema: dict[str, Any] | None = None
    list_selector: str | None = None
    fields: dict[str, Any] | None = None
    llm_prompt: str | None = None


class PaginationConfig(BaseModel):
    strategy: Literal[
        "auto_detect", "next_button", "url_pattern", "infinite_scroll", "load_more", "none"
    ] = "auto_detect"
    max_pages: int = Field(default=10, ge=1, le=1000)
    max_items: int | None = Field(default=None, ge=1)
    next_selector: str | None = None
    load_more_selector: str | None = None
    url_template: str | None = None  # e.g. "/page/{n}"
    max_scroll_rounds: int = Field(default=30, ge=1, le=500)


class ProxyConfig(BaseModel):
    type: Literal["residential", "datacenter", "mobile", "auto", "none"] = "auto"
    country: str | None = None


class RetryConfig(BaseModel):
    max_retries: int = Field(default=2, ge=0, le=10)
    backoff: Literal["exponential", "fixed"] = "exponential"
    retry_on: list[str] = Field(
        default_factory=lambda: ["timeout", "blocked", "captcha_failed", "empty"]
    )


class OutputConfig(BaseModel):
    format: Literal["json", "csv"] = "json"
    include_raw_html: bool = False
    screenshot: bool = False


class ScrapeJobConfig(BaseModel):
    rendering: Literal["full_browser", "lightweight", "auto"] = "auto"
    browser: Literal["chromium", "firefox", "webkit"] = "chromium"
    headless: bool = True
    wait_strategy: WaitStrategy = Field(default_factory=WaitStrategy)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    pagination: PaginationConfig = Field(default_factory=PaginationConfig)
    popup_handling: bool = True
    respect_robots: bool | None = None  # None -> use global setting
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)


class ScrapeJob(BaseModel):
    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    url: str
    domain: str = ""
    priority: int = Field(default=5, ge=1, le=10)
    config: ScrapeJobConfig = Field(default_factory=ScrapeJobConfig)
    state: JobState = JobState.queued
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result_location: str | None = None
    error_log: list[dict[str, Any]] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def _valid_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"Invalid URL: {v!r} (must be absolute http/https)")
        return v

    def model_post_init(self, __context: Any) -> None:
        if not self.domain:
            self.domain = urlparse(self.url).netloc.lower()


class PageContent(BaseModel):
    """Browser-independent snapshot of a loaded page.

    Extraction and pagination *detection* operate on this, so they are unit
    testable without a browser.
    """
    url: str
    html: str
    status: int = 200
    final_url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    cookies: dict[str, str] = Field(default_factory=dict)
    captured_api: list[dict[str, Any]] = Field(default_factory=list)  # intercepted JSON bodies

    @property
    def effective_url(self) -> str:
        return self.final_url or self.url


class ExtractedItem(BaseModel):
    data: dict[str, Any]
    source_url: str = ""
    page_number: int = 1
    extraction_strategy: str = ""


class JobStats(BaseModel):
    pages_scraped: int = 0
    items_extracted: int = 0
    duplicates_skipped: int = 0
    popups_dismissed: int = 0
    challenges_encountered: int = 0
    rendering_mode: str = ""
    duration_seconds: float = 0.0
    bytes_downloaded: int = 0
