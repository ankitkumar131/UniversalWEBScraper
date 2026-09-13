"""API-based pagination (TYPE_F/G): replay the detected XHR/fetch endpoint that
returns list data, incrementing page/offset/cursor parameters. Bypasses the
DOM entirely — much faster than rendering."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from src.pagination.strategies.base import AdvanceResult, PaginationStrategy, StrategyContext

_PAGEY = re.compile(r"page|offset|start|skip|cursor|from|index", re.I)


def _mutate_url(url: str, page_number: int) -> str | None:
    parts = urlsplit(url)
    qs = parse_qs(parts.query, keep_blank_values=True)
    for key in list(qs):
        if _PAGEY.search(key) and qs[key][0].isdigit():
            qs[key] = [str(int(qs[key][0]) + (page_number - 1) * (int(qs[key][0]) or 1))]
            return urlunsplit(parts._replace(query=urlencode(qs, doseq=True)))
    # no page param: add one
    qs["page"] = [str(page_number)]
    return urlunsplit(parts._replace(query=urlencode(qs, doseq=True)))


class ApiPaginationStrategy(PaginationStrategy):
    """Replays a captured API request. Requires ctx.meta['api_request'] with
    {url, method, headers, json_body} (provided by the network interceptor)."""

    name = "api_pagination"

    def __init__(self, config, api_request: dict[str, Any] | None = None,
                 fetch_json: Any = None):
        super().__init__(config)
        self._request = api_request
        self._fetch_json = fetch_json  # async (url, method, headers, body) -> (json, url)
        self._page = 1

    async def advance(self, ctx: StrategyContext) -> AdvanceResult:
        if not self._request or not self._fetch_json:
            return AdvanceResult(False, detail="no captured API request")
        self._page += 1
        next_url = _mutate_url(self._request["url"], self._page)
        try:
            data, final_url = await self._fetch_json(
                next_url, self._request.get("method", "GET"),
                self._request.get("headers", {}), self._request.get("json"),
            )
        except Exception as e:
            return AdvanceResult(False, detail=f"api replay failed: {e}")
        if not data:
            return AdvanceResult(False, detail="empty api response")
        ctx.meta["api_response"] = data
        return AdvanceResult(True, new_url=final_url, detail="api page fetched")
