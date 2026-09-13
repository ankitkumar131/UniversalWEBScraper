"""'Load More' button pagination (TYPE_E): click, wait for new items, repeat."""
from __future__ import annotations

import asyncio

from src.pagination.strategies.base import AdvanceResult, PaginationStrategy, StrategyContext
from src.pagination.strategies.infinite_scroll import _COUNT_JS


class LoadMoreStrategy(PaginationStrategy):
    name = "load_more"

    def __init__(self, config, selector: str | None = None):
        super().__init__(config)
        self._selector = selector

    async def advance(self, ctx: StrategyContext) -> AdvanceResult:
        if ctx.page is None:
            return AdvanceResult(False, detail="no live page")
        selector = self._selector or ctx.meta.get("load_more_selector")
        if not selector:
            # generic discovery
            found = await ctx.page.evaluate(
                """() => {
                    const texts = ['load more','show more','view more','see more','more results','load additional'];
                    const norm = (t) => (t || '').toLowerCase().replace(/…|\\.\\.\\./g, '').trim();
                    for (const el of document.querySelectorAll('button, a')) {
                        const t = norm(el.textContent);
                        if (texts.some(x => t === x || t.startsWith(x)) && el.offsetParent !== null) {
                            return el.id ? '#' + el.id : el.tagName.toLowerCase() + (el.className ? '.' + el.className.trim().split(/\\s+/)[0] : '');
                        }
                    }
                    return null;
                }"""
            )
            if not found:
                return AdvanceResult(False, detail="no load-more button")
            selector = found

        locator = ctx.page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=2000)
        except Exception:
            return AdvanceResult(False, detail="load-more button gone")

        before = await ctx.page.evaluate(_COUNT_JS)
        try:
            await locator.click(timeout=3000)
        except Exception as e:
            return AdvanceResult(False, detail=f"click failed: {e}")

        for _ in range(20):  # wait up to 10s for new items
            await asyncio.sleep(0.5)
            after = await ctx.page.evaluate(_COUNT_JS)
            if after > before:
                return AdvanceResult(True, new_url=ctx.page.url,
                                     detail=f"items {before}->{after}")
        return AdvanceResult(False, detail="no new items after load-more")
