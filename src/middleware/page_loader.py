"""Middleware 3 — page loading: navigate + wait strategy.

Wait strategies: network_idle | domcontentloaded | selector | timeout | custom
(custom = network_idle then DOM-stability check)."""
from __future__ import annotations

import asyncio

import structlog

from src.core.exceptions import TransientScrapeError
from src.core.schemas import PageContent
from src.middleware.base import BaseMiddleware, ScrapingContext, StepReport

log = structlog.get_logger(__name__)


class PageLoaderMiddleware(BaseMiddleware):
    name = "page_loading"

    async def run(self, ctx: ScrapingContext) -> StepReport:
        if ctx.page is None:
            return StepReport(self.name, detail="no page (lightweight mode)")
        wait = ctx.job.config.wait_strategy
        timeout = wait.timeout_ms
        try:
            response = await ctx.page.goto(ctx.job.url, wait_until="domcontentloaded",
                                           timeout=timeout)
            status = response.status if response else 0
            await self._apply_wait(ctx, wait, timeout)
        except Exception as e:
            raise TransientScrapeError(f"navigation failed: {e}", step=self.name,
                                       url=ctx.job.url) from e

        headers = dict(response.headers) if response else {}
        try:
            cookies = {c["name"]: c["value"] for c in await ctx.page.context.cookies()}
        except Exception:
            cookies = {}
        ctx.content = PageContent(
            url=ctx.job.url, html=await ctx.page.content(), status=status,
            final_url=ctx.page.url, headers=headers, cookies=cookies,
        )
        if ctx.network:
            ctx.content.captured_api = ctx.network.captures
        return StepReport(self.name, detail=f"status={status} url={ctx.page.url[:90]}")

    async def _apply_wait(self, ctx, wait, timeout: int) -> None:
        wtype = wait.type
        if wtype in ("network_idle", "custom"):
            try:
                await ctx.page.wait_for_load_state("networkidle", timeout=min(timeout, 15000))
            except Exception:
                pass  # networkidle is best-effort; long-polling pages never idle
            if wtype == "custom":
                await self._wait_dom_stable(ctx)
        elif wtype == "selector":
            if wait.selector:
                try:
                    await ctx.page.wait_for_selector(wait.selector, timeout=timeout)
                except Exception:
                    pass
        elif wtype == "timeout":
            await asyncio.sleep(min(timeout, 5000) / 1000)
        # domcontentloaded: nothing more to do

    async def _wait_dom_stable(self, ctx, quiet_ms: int = 1000, max_wait: float = 8.0) -> None:
        js = (
            "() => new Promise(resolve => {"
            "  let last = document.documentElement.outerHTML.length;"
            "  const t0 = Date.now();"
            "  const iv = setInterval(() => {"
            "    const now = document.documentElement.outerHTML.length;"
            "    if (now !== last) { last = now; return; }"
            "    clearInterval(iv); resolve(Date.now() - t0);"
            "  }, 200);"
            "})"
        )
        try:
            await asyncio.wait_for(ctx.page.evaluate(js), timeout=max_wait)
            await asyncio.sleep(quiet_ms / 1000)
        except Exception:
            pass
