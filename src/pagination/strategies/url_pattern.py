"""URL-pattern pagination (TYPE_C): generate/increment page URLs directly.

Works in both lightweight (httpx — and parallelizable) and browser modes."""
from __future__ import annotations

from src.pagination.detector import next_page_url
from src.pagination.strategies.base import AdvanceResult, PaginationStrategy, StrategyContext


class UrlPatternStrategy(PaginationStrategy):
    name = "url_pattern"

    def __init__(self, config, url_template: str | None = None, base_url: str = ""):
        super().__init__(config)
        self._template = url_template
        self._base_url = base_url
        self._exhausted = False

    async def advance(self, ctx: StrategyContext) -> AdvanceResult:
        if self._exhausted:
            return AdvanceResult(False)
        next_page = ctx.page_number + 1
        candidate = None
        if self._template:
            candidate = self._template.format(n=next_page)
        else:
            candidate = next_page_url(ctx.current_url or self._base_url)
        if not candidate:
            self._exhausted = True
            return AdvanceResult(False, detail="no URL pattern detected")

        if ctx.page is not None:
            prev_len = len(await ctx.page.content())
            try:
                await ctx.page.goto(candidate, wait_until="domcontentloaded",
                                    timeout=30000)
            except Exception as e:
                self._exhausted = True
                return AdvanceResult(False, detail=f"goto failed: {e}")
            new_len = len(await ctx.page.content())
            return AdvanceResult(True, new_url=candidate,
                                 detail="navigated (pattern)" if new_len != prev_len else "navigated")
        # lightweight mode: engine refetches ctx.current_url
        return AdvanceResult(True, new_url=candidate, detail="url advanced")
