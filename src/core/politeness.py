"""robots.txt compliance + per-domain politeness (sd.txt "Legal & Ethical
Considerations"). Cached per domain for 24h; Crawl-delay feeds the rate limiter."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
import structlog

log = structlog.get_logger(__name__)


@dataclass
class RobotsPolicy:
    allowed: bool = True
    crawl_delay: float | None = None
    fetched: bool = False
    raw: str | None = None
    sitemaps: list[str] = field(default_factory=list)


class RobotsCache:
    def __init__(self, cache=None, ttl_seconds: float = 86400.0, user_agent: str = "*"):
        self._cache = cache  # optional RedisCache
        self._ttl = ttl_seconds
        self._mem: dict[str, RobotsPolicy] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._user_agent = user_agent

    async def can_fetch(self, url: str, user_agent: str | None = None) -> RobotsPolicy:
        parts = urlparse(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        lock = self._locks.setdefault(robots_url, asyncio.Lock())
        async with lock:
            policy = self._mem.get(robots_url)
            if policy is None and self._cache is not None:
                cached = await self._cache.get_json(f"robots:{robots_url}")
                if cached:
                    policy = RobotsPolicy(**cached)
                    self._mem[robots_url] = policy
            if policy is None:
                policy = await self._fetch(robots_url, user_agent or self._user_agent)
                self._mem[robots_url] = policy
                if self._cache is not None:
                    await self._cache.set_json(f"robots:{robots_url}",
                                               {"allowed": policy.allowed,
                                                "crawl_delay": policy.crawl_delay,
                                                "fetched": policy.fetched}, ttl=int(self._ttl))
        if not policy.fetched:
            return policy
        parser = RobotFileParser()
        parser.parse(policy.raw.splitlines())
        ua = user_agent or self._user_agent
        return RobotsPolicy(
            allowed=parser.can_fetch(ua, url),
            crawl_delay=parser.crawl_delay(ua) or policy.crawl_delay,
            fetched=True,
            sitemaps=[s for s in parser.site_maps() or []],
        )

    async def _fetch(self, robots_url: str, user_agent: str) -> RobotsPolicy:
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=10,
                                         headers={"User-Agent": user_agent}) as client:
                resp = await client.get(robots_url)
        except Exception as e:
            log.debug("robots_fetch_failed", url=robots_url, error=str(e))
            return RobotsPolicy(allowed=True, fetched=False)
        if resp.status_code >= 400:
            return RobotsPolicy(allowed=True, fetched=False)  # no robots.txt -> allowed
        log.debug("robots_fetched", url=robots_url, bytes=len(resp.text))
        return RobotsPolicy(allowed=True, fetched=True, raw=resp.text)
