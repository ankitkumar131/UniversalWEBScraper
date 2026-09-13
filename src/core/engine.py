"""The main scraping engine orchestrator (sd.txt "Request Lifecycle").

Tiered rendering ladder:
  Tier 0  lightweight (httpx, direct)
  Tier 1  lightweight + proxy
  Tier 2  stealth browser (Playwright + patches)
  Tier 3  stealth browser + proxy + CAPTCHA service

Rendering 'auto' starts at the cheapest tier the domain difficulty allows and
escalates on challenge/block/empty signals. Every attempt runs the middleware
pipeline; results flow through the data pipeline (dedupe -> transform ->
validate -> enrich) into storage.
"""
from __future__ import annotations

import asyncio
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
import structlog

from src.monitoring import metrics
from src.browser.interceptors.api_detector import best_api_capture
from src.browser.interceptors.network import NetworkInterceptor
from src.browser.pool import BrowserPool
from src.captcha.detector import detect_challenge
from src.captcha.solvers.two_captcha import TwoCaptchaSolver
from src.core.circuit_breaker import BreakerState, CircuitBreaker
from src.core.config import Settings
from src.core.exceptions import (BlockedScrapeError, CaptchaUnsolvedError,
                                 CircuitOpenError, EscalateScrapeError,
                                 FatalScrapeError, TransientScrapeError)
from src.core.job_manager import JobManager
from src.core.politeness import RobotsCache
from src.core.schemas import (JobState, JobStats, PageContent, ScrapeJob)
from src.extraction.hybrid_extractor import HybridExtractor
from src.middleware.base import ScrapingContext
from src.middleware.challenge_solver import ChallengeSolverMiddleware
from src.middleware.content_readiness import ContentReadinessMiddleware
from src.middleware.cookie_consent import CookieConsentMiddleware
from src.middleware.page_loader import PageLoaderMiddleware
from src.middleware.pipeline import MiddlewarePipeline
from src.middleware.popup_handler import PopupHandlerMiddleware
from src.middleware.stealth_patcher import StealthPatcherMiddleware
from src.pagination.detector import detect_pagination
from src.pagination.deduplicator import Deduplicator
from src.pagination.strategies.api_pagination import ApiPaginationStrategy
from src.pagination.strategies.base import StrategyContext
from src.pagination.strategies.click_next import ClickNextStrategy
from src.pagination.strategies.infinite_scroll import InfiniteScrollStrategy
from src.pagination.strategies.load_more import LoadMoreStrategy
from src.pagination.strategies.url_pattern import UrlPatternStrategy
from src.pipeline.deduplicator import DataDeduplicator
from src.pipeline.enricher import Enricher
from src.pipeline.transformer import Transformer
from src.pipeline.validator import Validator
from src.proxy.identity import IdentityStore
from src.proxy.pool_manager import ProxyPoolManager
from src.queue.rate_limiter import RateLimiter
from src.storage.artifacts import ArtifactStore

log = structlog.get_logger(__name__)

_TIERS = [
    {"rendering": "lightweight", "proxy": False},
    {"rendering": "lightweight", "proxy": True},
    {"rendering": "browser", "proxy": False},
    {"rendering": "browser", "proxy": True},
]


@dataclass
class AttemptOutcome:
    items: list[dict[str, Any]] = field(default_factory=list)
    pages: int = 0
    strategy: str = ""
    rendering: str = ""
    challenge_seen: bool = False
    bytes_downloaded: int = 0
    popups_dismissed: int = 0
    duplicates: int = 0
    pagination_plan: dict[str, Any] = field(default_factory=dict)
    partial: bool = False


class ScrapeEngine:
    def __init__(self, settings: Settings, *, browser_pool: BrowserPool,
                 proxy_manager: ProxyPoolManager, identity_store: IdentityStore,
                 rate_limiter: RateLimiter, robots: RobotsCache, breaker: CircuitBreaker,
                 job_manager: JobManager, artifacts: ArtifactStore,
                 captcha_solver=None, cache=None):
        self.settings = settings
        self.browser = browser_pool
        self.proxies = proxy_manager
        self.identities = identity_store
        self.rate_limiter = rate_limiter
        self.robots = robots
        self.breaker = breaker
        self.jobs = job_manager
        self.artifacts = artifacts
        self.captcha_solver = captcha_solver
        self.cache = cache
        self.llm_settings = settings.get("llm", {})
        self.llm_api_key = os.environ.get(self.llm_settings.get("api_key_env", "OPENAI_API_KEY"), "")
        if self.llm_settings.get("provider") != "openai_compatible":
            self.llm_api_key = ""

    @classmethod
    def build(cls, settings: Settings, job_manager: JobManager | None = None,
              artifacts: ArtifactStore | None = None) -> "ScrapeEngine":
        from src.storage.database import session_factory

        artifacts = artifacts or ArtifactStore(
            settings.artifacts_dir, settings.get("storage.s3"))
        if job_manager is None:
            job_manager = JobManager(session_factory(), artifacts)
        captcha_solver = None
        if settings.get("captcha.provider") == "twocaptcha":
            key = os.environ.get(settings.get("captcha.api_key_env", "TWOCAPTCHA_API_KEY"), "")
            if key:
                captcha_solver = TwoCaptchaSolver(key)
        cache = None
        redis_url = settings.get("queue.redis_url")
        if redis_url:
            from src.storage.redis_cache import RedisCache

            cache = RedisCache(redis_url)
        return cls(
            settings,
            browser_pool=BrowserPool(settings),
            proxy_manager=ProxyPoolManager(settings),
            identity_store=IdentityStore(),
            rate_limiter=RateLimiter(
                rate_per_domain=float(settings.get("politeness.requests_per_second_per_domain", 1.0)),
                burst=int(settings.get("politeness.burst", 2)),
                jitter_ms=tuple(settings.get("politeness.jitter_ms", [250, 1500])),
                crawl_delay_cap=float(settings.get("politeness.crawl_delay_cap", 10.0)),
                cache=cache,
            ),
            robots=RobotsCache(cache=cache),
            breaker=CircuitBreaker(),
            job_manager=job_manager,
            artifacts=artifacts,
            captcha_solver=captcha_solver,
            cache=cache,
        )

    # ------------------------------------------------------------------ run
    async def run(self, job: ScrapeJob) -> ScrapeJob:
        started = time.perf_counter()
        job.started_at = datetime.now(timezone.utc)
        job.state = JobState.running
        await self.jobs.save(job)

        # circuit breaker
        breaker_state = self.breaker.check(job.domain)
        if breaker_state == BreakerState.open:
            raise CircuitOpenError(f"circuit open for {job.domain}; retry after cooldown",
                                   step="circuit_breaker")

        # robots.txt politeness
        respect = job.config.respect_robots
        if respect is None:
            respect = self.settings.respect_robots
        if respect:
            try:
                policy = await self.robots.can_fetch(job.url)
            except Exception as e:  # robots fetch must never kill a job
                log.warning("robots_check_failed", error=str(e))
            else:
                self.rate_limiter.set_crawl_delay(job.domain, policy.crawl_delay)
                if policy.fetched and not policy.allowed:
                    job.state = JobState.failed
                    job.error_log.append({
                        "ts": _iso(), "step": "robots",
                        "error": f"robots.txt disallows scraping {job.url} "
                                 "(override with respect_robots=false in the job config)"})
                    job.finished_at = datetime.now(timezone.utc)
                    await self.jobs.save(job)
                    return job

        tier_index = await self._starting_tier(job)
        max_retries = job.config.retry.max_retries
        outcome: AttemptOutcome | None = None
        last_error: Exception | None = None
        challenge_seen = False

        for attempt in range(max_retries + 1):
            tier = _TIERS[tier_index]
            try:
                log.info("attempt_start", job_id=job.job_id, attempt=attempt + 1,
                         tier=tier_index, rendering=tier["rendering"], proxy=tier["proxy"])
                outcome = await self._run_attempt(job, tier, attempt)
                break
            except EscalateScrapeError as e:
                challenge_seen = challenge_seen or "challenge" in (e.reason or "") \
                    or isinstance(e, CaptchaUnsolvedError)
                last_error = e
                job.error_log.append({"ts": _iso(), "attempt": attempt + 1,
                                      "step": e.step or "engine", "error": e.message,
                                      "action": "escalate"})
                if isinstance(e, BlockedScrapeError):
                    metrics.blocks(job.domain, "block")
                    await self.rate_limiter.report_block(job.domain)
                if tier_index < len(_TIERS) - 1:
                    tier_index += 1
                elif attempt < max_retries:
                    tier_index = min(3, tier_index)  # already at top: retry same tier
                else:
                    break
                await self._backoff(attempt)
            except TransientScrapeError as e:
                last_error = e
                job.error_log.append({"ts": _iso(), "attempt": attempt + 1,
                                      "step": e.step or "engine", "error": e.message,
                                      "action": "retry"})
                # auto-recovery: intercepted/broken TLS -> tolerate it on retry
                if "ERR_CERT" in e.message and not self.browser.ignore_https_errors:
                    self.browser.ignore_https_errors = True
                    log.warning("cert_error_enabling_ignore_https_errors")
                if attempt >= max_retries:
                    break
                await self._backoff(attempt)
            except FatalScrapeError as e:
                last_error = e
                job.error_log.append({"ts": _iso(), "attempt": attempt + 1,
                                      "step": e.step or "engine", "error": e.message,
                                      "action": "fatal"})
                break

        duration = time.perf_counter() - started
        if outcome is not None:
            self._finalize(job, outcome, duration)
            await self.persist_results(job)
        else:
            job.state = JobState.failed
            job.error_log.append({"ts": _iso(), "step": "engine",
                                  "error": f"all attempts failed: {last_error}"})
            self.breaker.record_failure(job.domain)
            await self.jobs.update_domain(job.domain, success=False, duration=duration)

        job.stats.update(duration_seconds=round(duration, 2))
        job.finished_at = datetime.now(timezone.utc)
        metrics.scrape_duration(duration)
        await self.jobs.save(job)
        await self.browser.maybe_recycle()
        log.info("job_finished", job_id=job.job_id, state=job.state.value,
                 items=job.stats.get("items_extracted", 0), seconds=round(duration, 1))
        return job

    async def _starting_tier(self, job: ScrapeJob) -> int:
        rendering = job.config.rendering
        if rendering == "lightweight":
            return 0 if not self.proxies.active else 0
        if rendering == "full_browser":
            return 2 if not self.proxies.active else 3
        # auto: use learned domain difficulty
        domain_info = await self.jobs.get_domain(job.domain)
        difficulty = (domain_info or {}).get("difficulty", "unknown")
        if difficulty == "easy":
            return 0
        if difficulty == "medium":
            return 2
        if difficulty == "hard":
            return 3 if self.proxies.active else 2
        return 0  # unknown: cheapest first

    async def _backoff(self, attempt: int) -> None:
        base = float(self.settings.get("retry.backoff_base_seconds", 5))
        cap = float(self.settings.get("retry.backoff_max_seconds", 120))
        delay = min(cap, base * (2 ** attempt)) + random.uniform(0.5, 2.0)
        log.info("retry_backoff", seconds=round(delay, 1))
        await asyncio.sleep(delay)

    # ------------------------------------------------------------- attempts
    async def _run_attempt(self, job: ScrapeJob, tier: dict, attempt: int) -> AttemptOutcome:
        await self.rate_limiter.acquire(job.domain)
        proxy_entry = None
        if tier["proxy"] and self.proxies.active:
            proxy_entry = await self.proxies.acquire(job.domain,
                                                     country=job.config.proxy.country)
        proxy_url = proxy_entry.url if proxy_entry else None
        try:
            if tier["rendering"] == "lightweight":
                outcome = await self._run_lightweight(job, proxy_url)
            else:
                outcome = await self._run_browser(job, proxy_url)
            if proxy_entry is not None:
                self.proxies.report_success(proxy_entry)
            return outcome
        except Exception as e:
            if proxy_entry is not None:
                self.proxies.report_failure(proxy_entry,
                                            banned=isinstance(e, BlockedScrapeError))
            raise

    async def _run_lightweight(self, job: ScrapeJob, proxy_url: str | None) -> AttemptOutcome:
        """httpx-based fast path. Only url-pattern / next-href pagination."""
        outcome = AttemptOutcome(rendering="lightweight")
        extractor = self._extractor(job)
        deduper = Deduplicator()
        from src.browser.stealth.fingerprint import generate_fingerprint

        identity = generate_fingerprint()
        headers = {
            "User-Agent": identity["user_agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": f"{identity['locale']},en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        url = job.url
        page_number = 1
        consecutive_empty = 0
        max_pages = min(job.config.pagination.max_pages,
                        int(self.settings.get("pagination.max_pages", 10)))
        timeout = job.config.wait_strategy.timeout_ms / 1000

        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout,
                                     proxy=proxy_url, headers=headers) as client:
            while True:
                resp = await self._fetch(client, url)
                content = PageContent(url=url, html=resp.text, status=resp.status_code,
                                      final_url=str(resp.url),
                                      headers=dict(resp.headers))
                challenge = detect_challenge(content.html, content.status)
                if challenge.detected:
                    outcome.challenge_seen = True
                    metrics.challenges(challenge.kind)
                    if challenge.kind in ("rate_limit",):
                        raise BlockedScrapeError(
                            f"rate limited ({resp.status_code})", reason="rate_limit",
                            step="lightweight")
                    raise EscalateScrapeError(
                        f"challenge detected: {challenge.kind}", reason="challenge",
                        step="lightweight")
                if resp.status_code in (404,):
                    if page_number == 1:
                        raise FatalScrapeError(f"404 not found: {url}", step="lightweight")
                    break  # pagination walked past the last page
                if resp.status_code >= 500:
                    raise TransientScrapeError(f"HTTP {resp.status_code} from {url}",
                                               step="lightweight")

                result = await extractor.extract(content)
                fresh = deduper.filter(result.items)
                outcome.duplicates = deduper.duplicates
                metrics.pages_scraped(job.domain, "ok")
                metrics.items_extracted(len(fresh))
                outcome.pages += 1
                outcome.items.extend(fresh)
                outcome.strategy = result.strategy
                if job.config.output.include_raw_html:
                    await self.artifacts.save_html(job.job_id, page_number, resp.text)

                # --- pagination (lightweight-safe kinds only) ---
                if page_number >= max_pages:
                    outcome.pagination_plan = {"strategy": "capped", "pages": page_number}
                    break
                plan = detect_pagination(content.html, content.effective_url)
                if page_number == 1:
                    outcome.pagination_plan = plan.to_dict()
                if job.config.pagination.strategy == "none":
                    break
                next_url = self._lightweight_next_url(job, plan, content, page_number)
                if not next_url:
                    if plan.strategy in ("infinite_scroll", "load_more") and page_number == 1:
                        raise EscalateScrapeError(
                            f"page uses {plan.strategy} — browser rendering needed",
                            reason="challenge", step="lightweight")
                    break
                if not fresh:
                    consecutive_empty += 1
                    if consecutive_empty >= int(
                            self.settings.get("pagination.consecutive_empty_pages", 2)):
                        break
                else:
                    consecutive_empty = 0
                url = next_url
                page_number += 1
                await self.rate_limiter.acquire(job.domain)
        return outcome

    def _lightweight_next_url(self, job: ScrapeJob, plan, content: PageContent,
                              page_number: int) -> str | None:
        cfg = job.config.pagination
        if cfg.strategy == "url_pattern" and cfg.url_template:
            return cfg.url_template.format(n=page_number + 1)
        if plan.strategy == "url_pattern" or cfg.strategy == "url_pattern":
            from src.pagination.detector import next_page_url

            return next_page_url(content.effective_url)
        if plan.strategy == "next_button":
            href = plan.next_href
            if not href:  # selector-only plan: resolve the element in-page
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(content.html, "lxml")
                el = None
                if plan.next_selector:
                    try:
                        el = soup.select_one(plan.next_selector)
                    except Exception:
                        el = None
                if el is None:
                    el = soup.find("a", rel="next")
                href = el.get("href") if el else None
            if href:
                return urljoin(content.effective_url, href)
        return None

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        try:
            return await client.get(url)
        except httpx.HTTPError as e:
            raise TransientScrapeError(f"fetch failed: {e}", step="lightweight") from e

    # ---------------------------------------------------------------- browser
    async def _run_browser(self, job: ScrapeJob, proxy_url: str | None) -> AttemptOutcome:
        outcome = AttemptOutcome(rendering="browser")
        if not self.browser.available:
            raise FatalScrapeError(
                "browser rendering required but Playwright is not installed "
                "(pip install playwright && playwright install chromium, or run "
                "scripts/setup_browser.py)", step="browser")
        started_ok = await self.browser.start()
        if not started_ok:
            raise TransientScrapeError("browser failed to launch", step="browser")

        identity = await self.identities.identity_for(job.domain, proxy_url=proxy_url)
        context, fp = await self.browser.new_context(proxy_url=proxy_url, fingerprint=identity)
        metrics.active_sessions(1)
        page = await context.new_page()
        network = NetworkInterceptor()
        network.attach(page)
        try:
            outcome = await self._browser_flow(job, page, network, outcome)
        finally:
            metrics.active_sessions(0)
            network.detach()
            try:
                await context.close()
            except Exception:
                pass
            self.browser.release()
        return outcome

    async def _browser_flow(self, job: ScrapeJob, page, network: NetworkInterceptor,
                            outcome: AttemptOutcome) -> AttemptOutcome:
        extractor = self._extractor(job)
        deduper = Deduplicator()
        stats = JobStats(rendering_mode="browser")
        ctx = ScrapingContext(job=job, settings=self.settings, page=page, network=network,
                              stats=stats, proxy_url=None)
        pipeline = MiddlewarePipeline([
            PageLoaderMiddleware(self.settings),
            ChallengeSolverMiddleware(self.settings, self.captcha_solver),
            PopupHandlerMiddleware(self.settings),
            CookieConsentMiddleware(self.settings),
            ContentReadinessMiddleware(self.settings),
            StealthPatcherMiddleware(self.settings),
        ])

        # first page: full pipeline
        await pipeline.run(ctx)
        if ctx.content is None:
            raise TransientScrapeError("pipeline produced no content", step="browser")
        if ctx.content.status == 404:
            raise FatalScrapeError(f"404 not found: {job.url}", step="browser")
        result = await extractor.extract(ctx.content)
        fresh = deduper.filter(result.items)
        outcome.pages = 1
        outcome.items.extend(fresh)
        outcome.strategy = result.strategy
        outcome.challenge_seen = bool(ctx.flags.get("challenge"))
        outcome.popups_dismissed = stats.popups_dismissed
        outcome.bytes_downloaded = network.bytes_downloaded
        metrics.pages_scraped(job.domain, "ok")
        metrics.items_extracted(len(fresh))
        if job.config.output.include_raw_html:
            await self.artifacts.save_html(job.job_id, 1, ctx.content.html)
        if job.config.output.screenshot:
            png = await page.screenshot(full_page=True)
            await self.artifacts.save_screenshot(job.job_id, 1, png)

        # pagination
        cfg = job.config.pagination
        if cfg.strategy == "none":
            return outcome
        plan = detect_pagination(ctx.content.html, ctx.content.effective_url)
        strategy_name = cfg.strategy
        if strategy_name == "auto_detect":
            strategy_name = plan.strategy if plan.strategy != "none" else "none"
        outcome.pagination_plan = plan.to_dict() | {"chosen": strategy_name}

        strategy = self._build_strategy(job, strategy_name, plan, ctx, network)
        if strategy is None:
            return outcome

        strat_ctx = StrategyContext(page=page, current_url=ctx.content.effective_url,
                                    items_so_far=len(fresh), page_number=1,
                                    meta={"next_selector": cfg.next_selector or plan.next_selector,
                                          "load_more_selector": cfg.load_more_selector
                                          or plan.load_more_selector})
        max_pages = min(cfg.max_pages, int(self.settings.get("pagination.max_pages", 10)))
        max_items = cfg.max_items or int(self.settings.get("pagination.max_items", 1000))
        deadline = time.time() + float(self.settings.get("pagination.max_time_seconds", 300))
        consecutive_empty = 0
        quick_pipeline = MiddlewarePipeline([  # popups may reappear on later pages
            PopupHandlerMiddleware(self.settings),
            ContentReadinessMiddleware(self.settings),
        ])

        while outcome.pages < max_pages and len(outcome.items) < max_items and time.time() < deadline:
            await self.rate_limiter.acquire(job.domain)
            strat_ctx.page_number = outcome.pages
            strat_ctx.items_so_far = len(outcome.items)
            advance = await strategy.advance(strat_ctx)
            if not advance.changed:
                log.debug("pagination_done", detail=advance.detail)
                break
            if advance.new_url:
                strat_ctx.current_url = advance.new_url
            # after navigation re-run cheap middlewares + refresh content
            ctx.page_number = outcome.pages + 1
            ctx.content.html = await page.content()
            ctx.content.url = advance.new_url or page.url
            ctx.content.final_url = page.url
            ctx.content.captured_api = network.captures
            await quick_pipeline.run(ctx)
            result = await extractor.extract(ctx.content)
            fresh = deduper.filter(result.items)
            outcome.duplicates = deduper.duplicates
            outcome.pages += 1
            if fresh:
                consecutive_empty = 0
                outcome.items.extend(fresh)
                metrics.pages_scraped(job.domain, "ok")
                metrics.items_extracted(len(fresh))
            else:
                consecutive_empty += 1
                if consecutive_empty >= int(
                        self.settings.get("pagination.consecutive_empty_pages", 2)):
                    break
            if job.config.output.include_raw_html:
                await self.artifacts.save_html(job.job_id, outcome.pages, ctx.content.html)
            if job.config.output.screenshot:
                try:
                    png = await page.screenshot(full_page=True)
                    await self.artifacts.save_screenshot(job.job_id, outcome.pages, png)
                except Exception:
                    pass
        await strategy.cleanup()
        return outcome

    def _build_strategy(self, job: ScrapeJob, name: str, plan, ctx, network):
        cfg = job.config.pagination
        if name == "next_button":
            return ClickNextStrategy(cfg, next_selector=cfg.next_selector or plan.next_selector)
        if name == "url_pattern":
            return UrlPatternStrategy(cfg, url_template=cfg.url_template,
                                      base_url=ctx.content.effective_url)
        if name == "infinite_scroll":
            return InfiniteScrollStrategy(cfg, max_rounds=cfg.max_scroll_rounds)
        if name == "load_more":
            return LoadMoreStrategy(cfg, selector=cfg.load_more_selector or plan.load_more_selector)
        if name == "api":
            capture = best_api_capture(network.captures)
            if capture:
                return ApiPaginationStrategy(cfg, api_request=capture)
        return None

    # --------------------------------------------------------------- finalize
    def _extractor(self, job: ScrapeJob) -> HybridExtractor:
        cfg = job.config.extraction.model_dump()
        return HybridExtractor(cfg, llm_settings=self.llm_settings,
                               llm_api_key=self.llm_api_key)

    def _finalize(self, job: ScrapeJob, outcome: AttemptOutcome, duration: float) -> None:
        base_url = job.url
        final_dedup = DataDeduplicator()
        items = final_dedup.process(outcome.items)
        items = Transformer().process(items, base_url)
        items, validation_errors = Validator().process(items)
        if validation_errors:
            job.error_log.append({"ts": _iso(), "step": "validator",
                                  "error": f"{len(validation_errors)} items dropped"})
        items = Enricher().process(items, job_id=job.job_id, page_url=base_url,
                                   page_number=1, strategy=outcome.strategy)

        job.stats.update({
            "pages_scraped": outcome.pages,
            "items_extracted": len(items),
            "duplicates_skipped": outcome.duplicates + final_dedup.duplicates,
            "popups_dismissed": outcome.popups_dismissed,
            "challenges_encountered": 1 if outcome.challenge_seen else 0,
            "rendering_mode": outcome.rendering,
            "duration_seconds": round(duration, 2),
            "bytes_downloaded": outcome.bytes_downloaded,
            "pagination": outcome.pagination_plan,
            "extraction_strategy": outcome.strategy,
        })
        job.state = (JobState.partially_completed if outcome.partial
                     else JobState.completed)
        if not items and not outcome.partial:
            job.state = JobState.completed  # completed with zero items is still a result
            job.error_log.append({"ts": _iso(), "step": "extraction",
                                  "error": "no items extracted"})
        job.result_location = f"db://job_results/{job.job_id}"
        self.breaker.record_success(job.domain)
        self._pending_persist = (items, outcome, duration)

    async def persist_results(self, job: ScrapeJob) -> None:
        """Flush finalized items to storage (called by run() before returning)."""
        pending = getattr(self, "_pending_persist", None)
        if not pending:
            return
        items, outcome, duration = pending
        self._pending_persist = None
        try:
            if items:
                await self.jobs.save_results(job.job_id, items, page_number=1,
                                             page_url=job.url, strategy=outcome.strategy)
            await self.jobs.update_domain(
                job.domain, success=True, challenge=outcome.challenge_seen,
                duration=duration, rendering_used=outcome.rendering)
        except Exception as e:
            log.error("persist_failed", job_id=job.job_id, error=str(e))

    async def close(self) -> None:
        await self.browser.stop()
        await self.proxies.stop()
        if self.cache is not None:
            await self.cache.close()


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()
