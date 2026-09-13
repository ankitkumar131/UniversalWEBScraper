"""Middleware 2 — stealth verification.

The stealth init-script itself is applied by the context factory (it must run
before any page script). This step verifies the patches took effect and can
re-apply them for late-binding cases."""
from __future__ import annotations

from src.middleware.base import BaseMiddleware, ScrapingContext, StepReport

_CHECK_JS = """
() => ({
  webdriver: navigator.webdriver === undefined ? 'patched' : String(navigator.webdriver),
  languages: (navigator.languages || []).join(','),
  chrome: typeof window.chrome === 'object' ? 'present' : 'missing',
})
"""


class StealthPatcherMiddleware(BaseMiddleware):
    name = "stealth_patching"

    async def run(self, ctx: ScrapingContext) -> StepReport:
        if ctx.page is None:
            return StepReport(self.name, detail="no page (lightweight mode)")
        try:
            result = await ctx.page.evaluate(_CHECK_JS)
            patched = result.get("webdriver") == "patched" and result.get("chrome") == "present"
            return StepReport(self.name, ok=patched, detail=str(result))
        except Exception as e:
            return StepReport(self.name, ok=False, detail=f"verify failed: {e}")
