"""Middleware 4 — challenge detection & solving.

Escalation ladder (sd.txt): detect -> wait out JS challenges -> solve CAPTCHA
via configured service -> escalate tier (proxy/browser change) -> fail."""
from __future__ import annotations

import asyncio

import structlog

from src.captcha.detector import detect_challenge, extract_sitekey
from src.captcha.injector import inject_solution
from src.core.exceptions import BlockedScrapeError, CaptchaUnsolvedError
from src.middleware.base import BaseMiddleware, ScrapingContext, StepReport

log = structlog.get_logger(__name__)


class ChallengeSolverMiddleware(BaseMiddleware):
    name = "challenge_solver"

    def __init__(self, settings=None, solver=None):
        super().__init__(settings)
        self.solver = solver  # e.g. TwoCaptchaSolver or None

    async def run(self, ctx: ScrapingContext) -> StepReport:
        assert ctx.content is not None
        info = detect_challenge(ctx.content.html, ctx.content.status)
        if not info.detected:
            return StepReport(self.name, detail="no challenge")

        ctx.stats.challenges_encountered += 1
        ctx.flags["challenge"] = info.to_dict()
        log.info("challenge_detected", kind=info.kind, job_id=ctx.job.job_id)

        if info.kind in ("cloudflare_js", "challenge", "js_required"):
            solved = await self._wait_out_js_challenge(ctx)
            if solved:
                return StepReport(self.name, detail=f"cleared {info.kind} by waiting")
            if info.kind == "js_required" and ctx.page is None:
                raise BlockedScrapeError("site requires JavaScript — browser rendering needed",
                                         reason="js_required", step=self.name)

        if info.kind in ("hcaptcha", "recaptcha", "turnstile", "captcha",
                         "recaptcha_or_hcaptcha") and ctx.page is not None:
            token = await self._solve_captcha(ctx, info.kind)
            if token:
                return StepReport(self.name, detail=f"solved {info.kind} via solver service")

        # nothing worked — escalate (better proxy / next tier) or fail cleanly
        if info.kind in ("rate_limit",):
            raise BlockedScrapeError(f"rate limited ({info.evidence})", reason="rate_limit",
                                     step=self.name)
        raise CaptchaUnsolvedError(
            f"challenge not solved: {info.kind} ({info.evidence})",
            reason=info.kind, step=self.name)

    async def _wait_out_js_challenge(self, ctx: ScrapingContext) -> bool:
        """Cloudflare-style JS challenges auto-solve in a real browser; wait."""
        if ctx.page is None:
            return False
        wait_ms = int(self.settings.get("timeouts.challenge_wait_ms", 15000)) if self.settings else 15000
        deadline = asyncio.get_event_loop().time() + wait_ms / 1000
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1.0)
            try:
                html = await ctx.page.content()
                status = ctx.content.status if ctx.content.status < 400 else 200
                if not detect_challenge(html, status).detected:
                    ctx.content.html = html
                    ctx.content.final_url = ctx.page.url
                    try:
                        ctx.content.cookies = {c["name"]: c["value"]
                                               for c in await ctx.page.context.cookies()}
                    except Exception:
                        pass
                    return True
            except Exception:
                pass
        return False

    async def _solve_captcha(self, ctx: ScrapingContext, kind: str) -> str | None:
        if not self.solver:
            log.info("no_captcha_solver_configured", kind=kind)
            return None
        sitekey = extract_sitekey(ctx.content.html)
        if not sitekey:
            log.info("no_sitekey_found", kind=kind)
            return None
        # normalize kind for the solver
        solver_kind = "hcaptcha" if "hcap" in kind else (
            "turnstile" if "turnstile" in kind else "recaptcha")
        try:
            token = await self.solver.solve(kind=solver_kind, sitekey=sitekey,
                                            page_url=ctx.content.effective_url)
        except Exception as e:
            log.warning("captcha_solver_failed", error=str(e))
            return None
        await inject_solution(ctx.page, token)
        await asyncio.sleep(2.0)
        html = await ctx.page.content()
        if not detect_challenge(html, 200).detected:
            ctx.content.html = html
            return token
        return None
