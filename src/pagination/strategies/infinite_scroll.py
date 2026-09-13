"""Infinite-scroll pagination (TYPE_D): scroll, wait for new items, repeat."""
from __future__ import annotations

import asyncio

from src.pagination.strategies.base import AdvanceResult, PaginationStrategy, StrategyContext

_COUNT_JS = """
() => {
  const sels = ['article', '[class*="item"]', '[class*="card"]', '[class*="product"]',
                '[class*="post"]', '[class*="result"]', 'li'];
  let best = 0;
  for (const sel of sels) {
    const n = document.querySelectorAll(sel).length;
    if (n > best && n < 2000) best = n;
  }
  return best;
}
"""


class InfiniteScrollStrategy(PaginationStrategy):
    name = "infinite_scroll"

    def __init__(self, config, max_rounds: int = 30):
        super().__init__(config)
        self._max_rounds = max_rounds
        self._round = 0

    async def advance(self, ctx: StrategyContext) -> AdvanceResult:
        if ctx.page is None or self._round >= self._max_rounds:
            return AdvanceResult(False, detail="max scroll rounds reached")
        self._round += 1

        before = await ctx.page.evaluate(_COUNT_JS)
        prev_height = await ctx.page.evaluate("() => document.body.scrollHeight")

        for _ in range(6):  # scroll down in increments like a human
            await ctx.page.mouse.wheel(0, 900)
            await asyncio.sleep(0.3)
        await ctx.page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.5)

        # wait for new content to settle (max ~8s)
        for _ in range(16):
            new_height = await ctx.page.evaluate("() => document.body.scrollHeight")
            if new_height > prev_height:
                break
            await asyncio.sleep(0.5)

        after = await ctx.page.evaluate(_COUNT_JS)
        if after > before or new_height > prev_height:
            return AdvanceResult(True, new_url=ctx.page.url,
                                 detail=f"scrolled: items {before}->{after}")
        return AdvanceResult(False, detail="no new content after scroll")
