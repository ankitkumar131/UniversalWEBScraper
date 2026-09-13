"""Proxy pool manager: ties providers, rotation, and health together."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

from src.proxy.health_checker import ProxyHealth, ProxyHealthChecker
from src.proxy.providers.generic import classify_tier, load_proxies
from src.proxy.rotator import ProxyRotator

log = structlog.get_logger(__name__)


@dataclass
class ProxyEntry:
    url: str
    tier: str
    country: str | None
    health: ProxyHealth = field(default_factory=lambda: ProxyHealth(url=""))


class ProxyPoolManager:
    def __init__(self, settings, health_checker: ProxyHealthChecker | None = None):
        self.settings = settings
        enabled = bool(settings.get("proxy.enabled", False))
        proxies = load_proxies(
            explicit=settings.get("proxy.list") or None,
            list_file=settings.get("proxy.list_file"),
        )
        self.entries: list[ProxyEntry] = [
            ProxyEntry(url=url, tier=classify_tier(url), country=None,
                       health=ProxyHealth(url=url))
            for url in proxies
        ]
        self.rotator = ProxyRotator(
            self.entries, policy=settings.get("proxy.rotation", "per_session")
        ) if self.entries else None
        self.health_checker = health_checker or ProxyHealthChecker(
            check_url=settings.get("proxy.check_url", "https://httpbin.org/ip"))
        self._check_task: asyncio.Task | None = None
        self.active = enabled and bool(self.entries)
        if self.active:
            log.info("proxy_pool_ready", size=len(self.entries), policy=self.rotator.policy)
        elif enabled and not self.entries:
            log.warning("proxy_enabled_but_empty")

    async def acquire(self, domain: str, country: str | None = None,
                      tier_preference: str | None = None) -> ProxyEntry | None:
        if not self.active:
            return None
        entry = self.rotator.pick(domain, tier_preference=tier_preference, country=country)
        return entry

    def report_success(self, entry: ProxyEntry, latency_ms: float = 0.0) -> None:
        entry.health.record(True, latency_ms)

    def report_failure(self, entry: ProxyEntry, banned: bool = False) -> None:
        if banned:
            entry.health.mark_banned()
        else:
            entry.health.record(False)

    async def check_all(self) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for entry in self.entries:
            health = await self.health_checker.check(entry.url)
            entry.health = health
            results[entry.url] = health.healthy
        log.info("proxy_health_checked", healthy=sum(results.values()), total=len(results))
        return results

    def start_periodic_checks(self, interval: float | None = None) -> None:
        interval = interval or float(self.settings.get("proxy.check_interval_seconds", 300))

        async def _loop() -> None:
            while True:
                await asyncio.sleep(interval)
                try:
                    await self.check_all()
                except Exception as e:  # pragma: no cover
                    log.warning("proxy_check_failed", error=str(e))

        if self.active and self._check_task is None:
            self._check_task = asyncio.get_event_loop().create_task(_loop())

    async def stop(self) -> None:
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except (asyncio.CancelledError, Exception):
                pass
            self._check_task = None
