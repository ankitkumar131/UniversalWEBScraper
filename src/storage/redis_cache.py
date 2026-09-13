"""Optional Redis layer: distributed rate limiting, robots.txt cache, cookie cache.

Every operation degrades gracefully to a local in-memory implementation when
Redis is not configured, so single-node deployments need no Redis.
"""
from __future__ import annotations

import json
import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class RedisCache:
    """Thin async wrapper with in-memory fallback."""

    def __init__(self, redis_url: str | None):
        self.url = redis_url
        self._redis = None
        self._mem: dict[str, str] = {}
        self._exp: dict[str, float] = {}
        self._last_error: str | None = None
        if redis_url:
            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(redis_url, decode_responses=True)
                log.info("redis_enabled", url=redis_url)
            except Exception as e:
                self._last_error = str(e)
                log.warning("redis_unavailable_falling_back", error=str(e))

    @property
    def available(self) -> bool:
        return self._redis is not None

    async def get_json(self, key: str) -> Any | None:
        raw = await self.get(key)
        return json.loads(raw) if raw is not None else None

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        await self.set(key, json.dumps(value), ttl=ttl)

    async def get(self, key: str) -> str | None:
        if self._redis is None:
            if key in self._mem and (self._exp.get(key) or 0) > time.time():
                return self._mem[key]
            self._mem.pop(key, None)
            return None
        try:
            return await self._redis.get(key)
        except Exception as e:
            log.warning("redis_get_failed", error=str(e))
            return None

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        if self._redis is None:
            self._mem[key] = value
            self._exp[key] = time.time() + (ttl or 10**9)
            return
        try:
            await self._redis.set(key, value, ex=ttl)
        except Exception as e:
            log.warning("redis_set_failed", error=str(e))

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
