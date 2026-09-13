"""Inject a CAPTCHA solution token into the page."""
from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

_INJECT_JS = """
(token) => {
  let injected = false;
  // standard textarea targets
  for (const id of ['g-recaptcha-response', 'h-captcha-response', 'cf-turnstile-response']) {
    const el = document.getElementById(id) || document.querySelector(`textarea[name="${id}"]`);
    if (el) { el.style.display = 'block'; el.value = token; injected = true; }
  }
  // hidden fallbacks inside widgets
  for (const sel of ['[class*=g-recaptcha] textarea', '[class*=h-captcha] textarea',
                     'iframe[src*=recaptcha]', 'iframe[src*=hcaptcha]']) {
    const holder = document.querySelector(sel);
    if (holder) {
      const ta = holder.tagName === 'TEXTAREA' ? holder : holder.querySelector('textarea');
      if (ta) { ta.value = token; injected = true; }
    }
  }
  // fire common callbacks
  try { if (window.onRecaptchaSuccess) window.onRecaptchaSuccess(token); } catch (e) {}
  return injected;
}
"""


async def inject_solution(page, token: str) -> bool:
    try:
        injected = await page.evaluate(_INJECT_JS, token)
        if injected:
            # try to submit the enclosing form or click a verify button
            try:
                verify = page.locator("button:has-text('Verify'), button:has-text('Submit'), [class*=verify]").first
                if await verify.count():
                    await verify.click(timeout=2000)
            except Exception:
                pass
        log.info("captcha_token_injected", ok=injected)
        return injected
    except Exception as e:
        log.warning("captcha_injection_failed", error=str(e))
        return False
