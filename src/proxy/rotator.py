"""Proxy rotation policies: per_request | per_session | sticky_domain
(sd.txt "Rotation Strategy"). Operates on ProxyEntry objects."""
from __future__ import annotations

import random
import time
from typing import Any

from src.proxy.health_checker import ProxyHealth

_ENTRY_ATTRS = ("url", "tier", "country", "health")


def _coerce_entry(entry: Any):
    """Accept ProxyEntry dataclasses or plain dicts."""
    if isinstance(entry, dict):
        class _Shim:
            def __getattr__(self, item):
                return entry.get(item)

            @property
            def health(self) -> ProxyHealth:
                return entry.get("health") or ProxyHealth(url=entry.get("url", ""))

        return _Shim()
    return entry


class ProxyRotator:
    def __init__(self, entries: list[Any], policy: str = "per_session",
                 sticky_window: float = 600.0):
        self.entries = entries
        self.policy = policy if entries else "none"
        self._sticky: dict[str, tuple[str, float]] = {}
        self._sticky_window = sticky_window

    def pick(self, domain: str, tier_preference: str | None = None,
             country: str | None = None) -> Any | None:
        if not self.entries:
            return None
        entries = [_coerce_entry(e) for e in self.entries]
        pool = [e for e in entries if e.health.available]
        if not pool:
            pool = entries  # all degraded — better than nothing
        if country:
            matched = [e for e in pool if getattr(e, "country", None) == country]
            pool = matched or pool
        if tier_preference and tier_preference != "auto":
            tiered = [e for e in pool if getattr(e, "tier", "datacenter") == tier_preference]
            pool = tiered or pool

        if self.policy == "sticky_domain":
            cached = self._sticky.get(domain)
            if cached and time.time() - cached[1] < self._sticky_window:
                for e in pool:
                    if e.url == cached[0]:
                        return e
        entry = random.choice(pool)
        if self.policy == "sticky_domain":
            self._sticky[domain] = (entry.url, time.time())
        return entry

    def escalate_tier(self, current_tier: str) -> str | None:
        """datacenter -> isp -> residential -> mobile."""
        ladder = ["datacenter", "isp", "residential", "mobile"]
        try:
            return ladder[min(ladder.index(current_tier) + 1, len(ladder) - 1)]
        except ValueError:
            return "residential"
