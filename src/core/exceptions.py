"""Error taxonomy driving the retry / escalation engine (see sd.txt
"Failure Recovery & Retry Strategy").

- TransientScrapeError  -> auto-retry, same strategy
- EscalateScrapeError   -> retry with a higher-tier strategy
- FatalScrapeError      -> no retry, job fails
"""
from __future__ import annotations


class ScraperError(Exception):
    """Base class for all scraper errors."""

    def __init__(self, message: str, *, step: str = "", url: str = ""):
        super().__init__(message)
        self.message = message
        self.step = step
        self.url = url


class TransientScrapeError(ScraperError):
    """Network timeouts, connection resets, 502/503/504, browser crashes."""


class EscalateScrapeError(ScraperError):
    """Challenge / captcha / block / empty content: escalate the rendering tier."""

    def __init__(self, message: str, *, reason: str = "", **kw):
        super().__init__(message, **kw)
        self.reason = reason


class BlockedScrapeError(EscalateScrapeError):
    """403/429 or WAF block: retry with a higher tier (better proxy / browser)."""


class CaptchaUnsolvedError(EscalateScrapeError):
    """A CAPTCHA was detected but could not be solved."""


class FatalScrapeError(ScraperError):
    """404, invalid URL, robots.txt disallow, retries exhausted."""


class CircuitOpenError(TransientScrapeError):
    """Domain circuit breaker is open; retry after cooldown."""
