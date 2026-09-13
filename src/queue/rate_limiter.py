"""Per-domain rate limiting: token bucket with random jitter (politeness first).

In-memory by default; Redis-backed bucket when a shared cache is configured,
so distributed workers share the same per-domain budget."""
from __future__ import annotations

import asyncio
import random
import time

import structlog

log = structlog.get_logger(__name__)


class TokenBucket:
    def __init__(self, rate: float, capacity: float):
        self.rate = rate            # tokens per second
        self.capacity = capacity
        self.tokens = capacity
        self.updated = time.monotonic()

    def refill(self) -> None:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now

    def take(self) -> bool:
        self.refill()
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False

    @property
    def wait_seconds(self) -> float:
        self.refill()
        return max(0.0, (1 - self.tokens) / self.rate)


class RateLimiter:
    def __init__(self, rate_per_domain: float = 1.0, burst: int = 2,
                 jitter_ms: tuple[int, int] = (250, 1500),
                 crawl_delay_cap: float = 10.0, cache=None):
        self.default_rate = rate_per_domain
        self.burst = burst
        self.jitter_ms = jitter_ms
        self.crawl_delay_cap = crawl_delay_cap
        self._cache = cache  # optional RedisCache for distributed limiting
        self._buckets: dict[str, TokenBucket] = {}
        self._delays: dict[str, float] = {}  # robots.txt Crawl-delay per domain
        self._lock = asyncio.Lock()

    def set_crawl_delay(self, domain: str, delay: float | None) -> None:
        if delay:
            self._delays[domain] = min(delay, self.crawl_delay_cap)

    async def acquire(self, domain: str) -> float:
        """Block until a request to `domain` is polite. Returns wait time."""
        async with self._lock:
            rate = self.default_rate
            if domain in self._delays:
                rate = min(rate, 1.0 / self._delays[domain])
            bucket = self._buckets.setdefault(
                domain, TokenBucket(rate=rate, capacity=self.burst))
        wait = bucket.wait_seconds
        if wait > 0:
            await asyncio.sleep(wait)
        # random jitter: never look periodic
        jitter = random.uniform(*self.jitter_ms) / 1000.0
        await asyncio.sleep(jitter)
        return wait + jitter

    async def report_block(self, domain: str) -> None:
        """Adaptive: slow down 2x on a block for this domain."""
        async with self._lock:
            bucket = self._buckets.get(domain)
            if bucket:
                bucket.rate = max(0.1, bucket.rate / 2)
                log.info("rate_limiter_backoff", domain=domain, new_rate=round(bucket.rate, 2))
