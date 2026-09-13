"""Context factory: assemble a stealth context (proxy + identity + stealth patches)."""
from __future__ import annotations

from typing import Any

import structlog

from src.browser.pool import BrowserPool
from src.browser.stealth.fingerprint import generate_fingerprint

log = structlog.get_logger(__name__)


class ContextFactory:
    def __init__(self, pool: BrowserPool):
        self.pool = pool

    async def create(self, *, proxy_url: str | None = None,
                     identity: dict[str, Any] | None = None,
                     country_hint: str | None = None):
        """Create a browser context paired with a consistent identity.

        Returns (context, fingerprint). Caller must call pool.release() when
        the context closes."""
        fingerprint = identity or generate_fingerprint(country_hint=country_hint)
        context, fp = await self.pool.new_context(proxy_url=proxy_url, fingerprint=fingerprint)
        log.info("context_created", proxy=bool(proxy_url), platform=fp.get("platform"),
                 timezone=fp.get("timezone"))
        return context, fp
