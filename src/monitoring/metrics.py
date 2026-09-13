"""Prometheus metrics (sd.txt "Monitoring & Observability").

Uses prometheus_client when available; otherwise falls back to in-memory
counters so the system still runs. Exposed at /metrics."""
from __future__ import annotations

from typing import Any

try:
    from prometheus_client import (Counter, Gauge, Histogram, generate_latest,
                                   CONTENT_TYPE_LATEST)

    _PROM = True
except ImportError:  # pragma: no cover
    _PROM = False

_REGISTRY: dict[str, Any] = {}


def _get(kind: str, name: str, desc: str, labels: tuple[str, ...] = ()):  # noqa: ANN001
    key = f"{kind}:{name}"
    if key in _REGISTRY:
        return _REGISTRY[key]
    cls = {"counter": Counter, "gauge": Gauge, "histogram": Histogram}[kind]
    obj = cls(name, desc, list(labels)) if _PROM else _Fallback(name, kind)
    _REGISTRY[key] = obj
    return obj


class _Fallback:
    """Minimal metric shim without prometheus_client."""

    def __init__(self, name: str, kind: str):
        self.name, self.kind = name, kind
        self.value = 0.0

    def labels(self, **_kw):
        return self

    def inc(self, amount: float = 1):
        self.value += amount

    def observe(self, value: float):
        self.value = value

    def set(self, value: float):
        self.value = value


def pages_scraped(domain: str, status: str) -> None:
    _get("counter", "uws_pages_scraped_total", "Pages scraped", ("domain", "status")
         ).labels(domain=domain, status=status).inc()


def scrape_duration(seconds: float) -> None:
    _get("histogram", "uws_scrape_duration_seconds", "Job duration").observe(seconds)


def items_extracted(count: int = 1) -> None:
    _get("counter", "uws_items_extracted_total", "Items extracted").inc(count)


def challenges(kind: str) -> None:
    _get("counter", "uws_challenges_total", "Challenges encountered", ("kind",)
         ).labels(kind=kind).inc()


def blocks(domain: str, kind: str) -> None:
    _get("counter", "uws_blocks_total", "Blocks detected", ("domain", "kind")
         ).labels(domain=domain, kind=kind).inc()


def queue_depth(depth: int) -> None:
    _get("gauge", "uws_queue_depth", "Local queue depth").set(depth)


def active_sessions(count: int) -> None:
    _get("gauge", "uws_active_browser_sessions", "Active browser contexts").set(count)


def render_metrics() -> tuple[bytes, str] | None:
    if not _PROM:
        return None
    return generate_latest(), CONTENT_TYPE_LATEST
