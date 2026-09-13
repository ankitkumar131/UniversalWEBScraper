"""Middleware 1 — proxy & identity assignment (pre-context).

Runs before the browser context exists: resolves a proxy (with rotation
policy) and a consistent identity profile for this session."""
from __future__ import annotations

from src.middleware.base import BaseMiddleware, ScrapingContext, StepReport


class ProxyInjectorMiddleware(BaseMiddleware):
    name = "proxy_injection"

    def __init__(self, settings=None, proxy_manager=None, identity_store=None):
        super().__init__(settings)
        self.proxy_manager = proxy_manager
        self.identity_store = identity_store

    async def run(self, ctx: ScrapingContext) -> StepReport:
        proxy_url = None
        if ctx.job.config.proxy.type != "none" and self.proxy_manager:
            entry = await self.proxy_manager.acquire(
                domain=ctx.job.domain, country=ctx.job.config.proxy.country)
            if entry:
                proxy_url = entry.url
        ctx.proxy_url = proxy_url
        return StepReport(self.name, detail=f"proxy={'set' if proxy_url else 'direct'}")
