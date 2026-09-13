"""Click-based pagination: numbered pages or Next/Previous buttons (TYPE_A/TYPE_B)."""
from __future__ import annotations

import asyncio

from src.pagination.strategies.base import AdvanceResult, PaginationStrategy, StrategyContext


class ClickNextStrategy(PaginationStrategy):
    name = "next_button"

    def __init__(self, config, next_selector: str | None = None):
        super().__init__(config)
        self._selector = next_selector
        self._last_html_hash: str | None = None
        self._exhausted = False

    def _resolve_selector(self, ctx: StrategyContext) -> str:
        return self._selector or ctx.meta.get("next_selector") or "a[rel='next']"

    async def advance(self, ctx: StrategyContext) -> AdvanceResult:
        if self._exhausted or ctx.page is None:
            return AdvanceResult(False, detail="no live page")
        selector = self._resolve_selector(ctx)
        locator = ctx.page.locator(selector).first
        try:
            await locator.wait_for(state="attached", timeout=1500)
        except Exception:
            self._exhausted = True
            return AdvanceResult(False, detail="next control not found")

        if not await locator.is_visible():
            disabled = await locator.get_attribute("disabled")
            aria = (await locator.get_attribute("aria-disabled") or "").lower()
            if disabled is not None or aria == "true":
                self._exhausted = True
                return AdvanceResult(False, detail="next control disabled")
            try:
                await locator.scroll_into_view_if_needed(timeout=1000)
            except Exception:
                pass

        prev_html = await ctx.page.content()
        try:
            async with ctx.page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
                await locator.click(timeout=3000)
        except Exception:
            # SPA-style: navigation event may not fire, click still updates DOM
            try:
                await locator.click(timeout=3000)
            except Exception as e:
                self._exhausted = True
                return AdvanceResult(False, detail=f"click failed: {e}")
            await asyncio.sleep(1.0)

        # wait until content actually changed
        for _ in range(20):
            await asyncio.sleep(0.4)
            html = await ctx.page.content()
            if html != prev_html:
                return AdvanceResult(True, new_url=ctx.page.url, detail="clicked next")
        self._exhausted = True
        return AdvanceResult(False, detail="content unchanged after click")
