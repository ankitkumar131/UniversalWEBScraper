"""Middleware 6 — content readiness: wait for loaders/spinners/skeletons to
disappear and the DOM to settle before extraction."""
from __future__ import annotations

import asyncio

import structlog

from src.middleware.base import BaseMiddleware, ScrapingContext, StepReport

log = structlog.get_logger(__name__)

_SPINNER_SELECTORS = [
    ".spinner:not([hidden])", ".loader", ".loading", "[class*='spinner' i]",
    "[class*='loading' i]", "[aria-busy='true']", "[class*='skeleton' i]",
    ".sk-spinner", ".preloader",
]

_COUNT_ITEMS_JS = """
() => {
  const sels = ['article', '[class*="item"]', '[class*="card"]', '[class*="product"]',
                '[class*="post"]', '[class*="result"]', 'li', 'tr'];
  let best = 0;
  for (const sel of sels) {
    const n = document.querySelectorAll(sel).length;
    if (n > best && n < 3000) best = n;
  }
  return best;
}
"""


class ContentReadinessMiddleware(BaseMiddleware):
    name = "content_readiness"

    async def run(self, ctx: ScrapingContext) -> StepReport:
        if ctx.page is None:
            return StepReport(self.name, detail="no page (lightweight mode)")
        spinner_ms = int(self.settings.get("timeouts.spinner_wait_ms", 8000)) if self.settings else 8000
        notes: list[str] = []

        # custom readiness selector from job config
        custom_sel = ctx.job.config.wait_strategy.selector
        if ctx.job.config.wait_strategy.type == "selector" and custom_sel:
            try:
                await ctx.page.wait_for_selector(custom_sel, timeout=spinner_ms)
                notes.append(f"selector ready: {custom_sel}")
            except Exception:
                notes.append(f"custom selector never appeared: {custom_sel}")

        # spinners / skeletons gone
        deadline = asyncio.get_event_loop().time() + spinner_ms / 1000
        while asyncio.get_event_loop().time() < deadline:
            visible = 0
            for sel in _SPINNER_SELECTORS:
                try:
                    visible += await ctx.page.locator(sel).first.is_visible()
                except Exception:
                    pass
            if visible == 0:
                break
            await asyncio.sleep(0.4)
        else:
            notes.append("spinners still visible after timeout")

        # item count stability: two consecutive identical counts
        stable_needed = 2
        counts: list[int] = []
        for _ in range(10):
            counts.append(await ctx.page.evaluate(_COUNT_ITEMS_JS))
            if len(counts) >= stable_needed + 1 and len(set(counts[-stable_needed:])) == 1 and counts[-1] > 0:
                break
            await asyncio.sleep(0.5)
        notes.append(f"items~{counts[-1] if counts else 0}")

        ctx.content.html = await ctx.page.content()
        return StepReport(self.name, detail="; ".join(notes))
