"""Identity profiles: pair each proxy with a consistent browser identity
(navigator, screen, timezone, locale) and persist per-domain for reuse."""
from __future__ import annotations

from typing import Any

import structlog

from src.browser.stealth.fingerprint import generate_fingerprint

log = structlog.get_logger(__name__)


class IdentityStore:
    """In-memory identity cache (persisted profiles land in the DB via
    job_manager; Redis backing plugs in here in production)."""

    def __init__(self):
        self._by_domain: dict[str, dict[str, Any]] = {}
        self._by_proxy: dict[str, dict[str, Any]] = {}

    async def identity_for(self, domain: str, proxy_url: str | None = None,
                           rotate: bool = False) -> dict[str, Any]:
        """Reuse the domain's identity when sticky; generate a new one on rotation."""
        if not rotate and domain in self._by_domain:
            identity = self._by_domain[domain]
            if proxy_url and proxy_url in self._by_proxy:
                # keep identity and proxy paired
                return identity
        identity = generate_fingerprint()
        self._by_domain[domain] = identity
        if proxy_url:
            self._by_proxy[proxy_url] = identity
        log.debug("identity_assigned", domain=domain, rotate=rotate,
                  platform=identity.get("platform"))
        return identity

    async def persist(self, domain: str, identity: dict[str, Any]) -> None:
        """Hook for DB/Redis persistence (job_manager records it)."""
        self._by_domain[domain] = identity
