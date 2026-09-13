"""Middleware 5 — pop-up & overlay dismissal.

Layers (sd.txt "Pop-up & Overlay Handler"):
  1. known pattern database (config/known_popups.yaml)
  2. generic overlay detection (fixed + z-index + dialog roles + token classes)
  3. dismiss: click close buttons -> Escape -> hide via JS
  4. CSS injection (nuclear) + remove body scroll locks
  5. permission prompts + JS dialogs are neutralized by the stealth init script
"""
from __future__ import annotations

import asyncio

import structlog

from src.core.config import load_yaml
from src.middleware.base import BaseMiddleware, ScrapingContext, StepReport

log = structlog.get_logger(__name__)

# JS executed in the page: find visible overlay-ish containers and their close buttons,
# click them, unlock scrolling. Runs in ONE round-trip per attempt for speed.
_DISMISS_JS = """
(payload) => {
  const cfg = payload;
  const visible = (el, minW = 30, minH = 20) => {
    if (!el || !el.getBoundingClientRect) return false;
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity || '1') > 0.05
      && r.width > minW && r.height > minH;
  };
  const tokenMatch = (el) => {
    const id = ((el.id || '') + ' ' + (el.className && el.className.baseVal !== undefined
      ? el.className.baseVal : el.className || '') + ' ' + (el.getAttribute('role') || '')).toString().toLowerCase();
    return cfg.tokens.some(t => id.includes(t));
  };
  const actions = [];
  const candidates = new Set();

  // known containers first
  for (const sel of cfg.knownSelectors) {
    document.querySelectorAll(sel).forEach(el => { if (visible(el)) candidates.add(el); });
  }
  // generic: role=dialog / alertdialog
  document.querySelectorAll('[role="dialog"], [role="alertdialog"], [aria-modal="true"]').forEach(
    el => { if (visible(el)) candidates.add(el); });
  // generic: token-matched elements that look like overlays
  document.querySelectorAll('div, section, aside').forEach(el => {
    if (!candidates.has(el) && visible(el) && tokenMatch(el)) {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      const covering = (s.position === 'fixed' || s.position === 'absolute')
        && r.width >= innerWidth * 0.5 && r.height >= innerHeight * 0.25;
      if (covering) candidates.add(el);
    }
  });

  const tryClickClose = (container) => {
    const scope = container || document;
    // 1. selector-based close buttons (small "x" buttons are fine if rendered)
    for (const sel of cfg.dismissSelectors) {
      try {
        for (const btn of scope.querySelectorAll(sel)) {
          if (visible(btn, 0, 0)) { btn.click(); return sel; }
        }
      } catch (e) {}
    }
    // 2. text-based close buttons
    const all = (container || document).querySelectorAll('button, a, [role="button"], span[class*=btn]');
    for (const btn of all) {
      if (!visible(btn, 0, 0)) continue;
      const t = (btn.textContent || '').trim().toLowerCase();
      if (t && t.length < 30 && cfg.dismissTexts.some(d => t === d || t.includes(d))) {
        btn.click(); return 'text:' + t;
      }
      const aria = ((btn.getAttribute('aria-label') || '') + '').toLowerCase();
      if (aria && cfg.dismissTexts.some(d => aria.includes(d))) { btn.click(); return 'aria'; }
    }
    return null;
  };

  // dismiss each candidate (known containers first)
  const ordered = [...candidates];
  for (const c of ordered) {
    const how = tryClickClose(c);
    if (how) actions.push({what: 'clicked', how, target: (c.id || c.className || '').toString().slice(0, 60)});
  }
  // also try a document-level close (banners whose close button is outside the container)
  if (!actions.length) {
    const how = tryClickClose(null);
    if (how) actions.push({what: 'clicked-doc', how});
  }
  // unlock body scrolling (sites lock scroll behind modals)
  for (const el of [document.body, document.documentElement]) {
    if (el && getComputedStyle(el).overflow === 'hidden') {
      el.style.overflow = 'auto';
      actions.push({what: 'scroll-unlock'});
    }
  }
  return actions;
}
"""

_FORCE_HIDE_JS = """
(css) => {
  const style = document.createElement('style');
  style.id = '__uws_hide_overlays__';
  style.textContent = css + ' body { overflow: auto !important; }';
  document.head.appendChild(style);
  return true;
}
"""


class PopupHandlerMiddleware(BaseMiddleware):
    name = "popup_handler"

    def __init__(self, settings=None, popup_config: dict | None = None):
        super().__init__(settings)
        cfg = popup_config or load_yaml("known_popups.yaml")
        self._known: list[str] = []
        for selectors in (cfg.get("containers") or {}).values():
            self._known.extend(selectors)
        self._tokens: list[str] = cfg.get("generic_tokens", [])
        self._dismiss_selectors: list[str] = cfg.get("dismiss_selectors", [])
        self._dismiss_texts: list[str] = [t.lower() for t in cfg.get("dismiss_texts", [])]
        self._force_css: str = cfg.get("force_hide_css", "")

    def enabled(self, ctx: ScrapingContext) -> bool:
        return bool(ctx.job.config.popup_handling)

    async def run(self, ctx: ScrapingContext) -> StepReport:
        if ctx.page is None:
            return StepReport(self.name, detail="no page (lightweight mode)")
        payload = {
            "knownSelectors": self._known,
            "tokens": self._tokens,
            "dismissSelectors": self._dismiss_selectors,
            "dismissTexts": self._dismiss_texts,
        }
        dismissed: list[dict] = []
        for attempt in range(3):  # re-run: dismissing one popup may reveal another
            try:
                actions = await ctx.page.evaluate(_DISMISS_JS, payload)
            except Exception as e:
                return StepReport(self.name, ok=False, detail=f"dismiss failed: {e}")
            if not actions:
                break
            dismissed.extend(actions)
            await asyncio.sleep(0.4)

        if not dismissed:
            try:  # Escape as a gentle nudge, then re-check
                await ctx.page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
                actions = await ctx.page.evaluate(_DISMISS_JS, payload)
                dismissed.extend(actions or [])
            except Exception:
                pass

        # nuclear option only if something still blocks: force-hide overlay classes
        if dismissed and self._force_css:
            try:
                await ctx.page.evaluate(_FORCE_HIDE_JS, self._force_css)
            except Exception:
                pass

        if dismissed:
            ctx.stats.popups_dismissed += len(dismissed)
            ctx.content.html = await ctx.page.content()  # refresh after DOM changes
            log.info("popups_dismissed", count=len(dismissed), job_id=ctx.job.job_id)
        return StepReport(self.name, detail=f"{len(dismissed)} dismissal action(s)")
