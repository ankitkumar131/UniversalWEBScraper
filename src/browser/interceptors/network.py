"""Network request/response interception: capture JSON API payloads, track bytes."""
from __future__ import annotations

import json
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_MAX_BODY = 2_000_000  # 2 MB per captured response
_API_HINTS = ("api", "graphql", "search", "list", "items", "products", "feed",
              "data", "catalog", "results", "page")


class NetworkInterceptor:
    """Attach to a Playwright page; records JSON responses + request bodies."""

    def __init__(self, max_captures: int = 50):
        self.max_captures = max_captures
        self.captures: list[dict[str, Any]] = []
        self.bytes_downloaded: int = 0
        self._page = None
        self._on_response = None

    def attach(self, page) -> None:
        self._page = page
        self._on_response = lambda response: self._safe_task(self._handle(response))
        page.on("response", self._on_response)

    def detach(self) -> None:
        if self._page and self._on_response:
            try:
                self._page.remove_listener("response", self._on_response)
            except Exception:
                pass

    def _safe_task(self, coro) -> None:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            loop.create_task(coro)
        except RuntimeError:
            pass

    async def _handle(self, response) -> None:
        try:
            headers = response.headers
            length = int(headers.get("content-length", 0) or 0)
            self.bytes_downloaded += length
            ctype = (headers.get("content-type") or "").lower()
            if "json" not in ctype:
                return
            if len(self.captures) >= self.max_captures:
                return
            body = await response.text()
            if not body or len(body) > _MAX_BODY:
                return
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                return
            self.captures.append({
                "url": response.url,
                "status": response.status,
                "json": parsed,
                "content_type": ctype,
            })
        except Exception:
            pass  # interception must never break navigation

    def api_requests(self) -> list[dict[str, Any]]:
        """Likely data-API requests (for API pagination replay)."""
        out = []
        for cap in self.captures:
            url = cap["url"].lower()
            if any(h in url for h in _API_HINTS) and isinstance(cap["json"], (dict, list)):
                out.append(cap)
        return out
