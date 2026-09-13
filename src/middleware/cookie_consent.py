"""Middleware 5b — cookie consent (CMP) handling, a specialised sub-module of
the popup handler (sd.txt "Cookie Consent Handler")."""
from __future__ import annotations

import asyncio

import structlog

from src.core.config import load_yaml
from src.middleware.base import BaseMiddleware, ScrapingContext, StepReport

log = structlog.get_logger(__name__)

# click accept/reject for known CMPs; generic keyword search as fallback
_CONSENT_JS = """
(payload) => {
  const visible = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 20 && r.height > 12;
  };
  const clickFirst = (selectors) => {
    for (const sel of selectors) {
      try {
        for (const el of document.querySelectorAll(sel)) {
          if (visible(el)) { el.click(); return sel; }
        }
      } catch (e) {}
    }
    return null;
  };
  const actions = [];
  // 1. known CMP buttons (accept, then reject per strategy)
  const want = payload.strategy === 'reject_all' ? ['reject', 'accept'] : ['accept', 'reject'];
  for (const cmp of Object.values(payload.cmps)) {
    for (const which of want) {
      const hit = clickFirst([cmp[which]]);
      if (hit) { actions.push({cmp: true, how: which, sel: hit}); return actions; }
    }
    if (cmp.container && document.querySelector(cmp.container)) {
      actions.push({cmp: true, container: cmp.container, how: 'container-no-button'});
    }
  }
  // 2. generic: visible fixed banner containing consent keywords + a matching button
  const keywords = payload.keywords;
  for (const el of document.querySelectorAll('div, section, aside, footer')) {
    if (!visible(el)) continue;
    const s = getComputedStyle(el);
    if (s.position !== 'fixed' && s.position !== 'sticky') continue;
    const text = (el.textContent || '').toLowerCase();
    if (!text || text.length > 3000) continue;
    if (!keywords.some(k => text.includes(k))) continue;
    for (const which of want) {
      const texts = payload.texts[which];
      for (const btn of el.querySelectorAll('button, a, [role=button]')) {
        if (!visible(btn)) continue;
        const t = (btn.textContent || '').trim().toLowerCase();
        if (t && t.length < 40 && texts.some(x => t === x || t.includes(x))) {
          btn.click(); actions.push({cmp: false, how: which, text: t}); return actions;
        }
      }
    }
  }
  return actions;
}
"""

_SET_COOKIE_JS = """
(cookies) => {
  for (const c of cookies) document.cookie = c.name + '=' + encodeURIComponent(c.value)
    + ';path=/;max-age=31536000;SameSite=Lax';
  return document.cookie.length;
}
"""


class CookieConsentMiddleware(BaseMiddleware):
    name = "cookie_consent"

    def __init__(self, settings=None, consent_config: dict | None = None):
        super().__init__(settings)
        cfg = consent_config or load_yaml("known_cookies.yaml")
        self._cmps = cfg.get("cmps", {})
        self._texts = {
            "accept": [t.lower() for t in cfg.get("strategies", {}).get("accept_all", {}).get("texts", [])],
            "reject": [t.lower() for t in cfg.get("strategies", {}).get("reject_all", {}).get("texts", [])],
        }
        self._keywords = [k.lower() for k in cfg.get("generic_keywords", [])]
        self._cookie_overrides = cfg.get("cookie_overrides", [])
        self._strategy = "accept_all"
        if settings:
            self._strategy = settings.get("cookie_consent.strategy", "accept_all")

    def enabled(self, ctx: ScrapingContext) -> bool:
        if self.settings is None:
            consent_enabled = True
        else:
            consent_enabled = bool(self.settings.get("cookie_consent.enabled", True))
        return consent_enabled and bool(ctx.job.config.popup_handling) and ctx.page is not None

    async def run(self, ctx: ScrapingContext) -> StepReport:
        payload = {
            "strategy": self._strategy,
            "cmps": self._cmps,
            "texts": self._texts,
            "keywords": self._keywords,
        }
        try:
            actions = await ctx.page.evaluate(_CONSENT_JS, payload)
        except Exception as e:
            return StepReport(self.name, ok=False, detail=f"consent failed: {e}")

        if not actions:
            return StepReport(self.name, detail="no consent banner")

        # let the banner animate away, then confirm it's gone
        await asyncio.sleep(0.6)
        try:
            still = await ctx.page.evaluate(_CONSENT_JS, payload)
            if still:  # set consent cookies directly as a last resort
                await ctx.page.evaluate(_SET_COOKIE_JS, self._cookie_overrides)
                await asyncio.sleep(0.3)
        except Exception:
            pass
        ctx.content.html = await ctx.page.content()
        log.info("cookie_consent_handled", actions=actions, job_id=ctx.job.job_id)
        return StepReport(self.name, detail=f"strategy={self._strategy}")
