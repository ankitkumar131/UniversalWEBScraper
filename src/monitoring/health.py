"""Component health checks for /api/health."""
from __future__ import annotations

from typing import Any


async def check_database(session_factory) -> dict[str, Any]:
    from sqlalchemy import text

    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "up"}
    except Exception as e:
        return {"status": "down", "error": str(e)}


def check_browser(pool) -> dict[str, Any]:
    if pool is None:
        return {"status": "unknown", "detail": "no pool"}
    if not pool.available:
        return {"status": "down", "detail": "playwright not installed"}
    return {
        "status": "up" if (pool._browser is None or pool._browser.is_connected()) else "down",
        "pages_served": pool.pages_served,
        "max_contexts": pool._max_contexts,
    }


def check_redis(cache) -> dict[str, Any]:
    if cache is None:
        return {"status": "unknown", "detail": "not configured"}
    return {"status": "up" if cache.available else "down"}
