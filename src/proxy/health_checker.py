"""Proxy health monitoring: periodic checks, latency + success tracking,
cooldown on ban detection."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
import structlog

log = structlog.get_logger(__name__)


@dataclass
class ProxyHealth:
    url: str
    healthy: bool = True
    success: int = 0
    failure: int = 0
    avg_latency_ms: float = 0.0
    last_checked: float = 0.0
    cooldown_until: float = 0.0
    history: list[tuple[bool, float]] = field(default_factory=list)

    def record(self, ok: bool, latency_ms: float = 0.0) -> None:
        self.history.append((ok, latency_ms))
        self.history = self.history[-20:]
        if ok:
            self.success += 1
        else:
            self.failure += 1
        total = self.success + self.failure
        self.avg_latency_ms = (
            (self.avg_latency_ms * (total - 1) + latency_ms) / total if total else 0.0
        )
        self.healthy = self.failure_rate < 0.7 or self.success == 0
        self.last_checked = time.time()

    @property
    def failure_rate(self) -> float:
        total = self.success + self.failure
        return (self.failure / total) if total else 0.0

    def mark_banned(self, cooldown_seconds: float = 300.0) -> None:
        self.healthy = False
        self.cooldown_until = time.time() + cooldown_seconds
        self.failure += 1

    @property
    def available(self) -> bool:
        return self.healthy and time.time() >= self.cooldown_until


class ProxyHealthChecker:
    def __init__(self, check_url: str = "https://httpbin.org/ip", timeout: float = 10.0):
        self.check_url = check_url
        self.timeout = timeout

    async def check(self, proxy_url: str) -> ProxyHealth:
        health = ProxyHealth(url=proxy_url)
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=self.timeout,
                                         follow_redirects=True) as client:
                resp = await client.get(self.check_url)
                latency = (time.perf_counter() - start) * 1000
                health.record(resp.status_code < 400, latency)
        except Exception:
            health.record(False, (time.perf_counter() - start) * 1000)
        return health
