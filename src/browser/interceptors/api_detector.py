"""API detector: identify the XHR/fetch endpoint most likely returning the
page's list data, so pagination/extraction can bypass the DOM."""
from __future__ import annotations

from typing import Any


def score_capture(capture: dict[str, Any]) -> float:
    """Heuristic score: how much does this JSON response look like list data?"""
    score = 0.0
    body = capture.get("json")
    if isinstance(body, list):
        score += 3.0
        if body and isinstance(body[0], dict):
            score += 1.0
    elif isinstance(body, dict):
        for key in ("items", "results", "data", "products", "records", "list", "rows", "hits"):
            value = body.get(key)
            if isinstance(value, list):
                score += 3.0
                if value and isinstance(value[0], dict):
                    score += 1.0
                break
    url = (capture.get("url") or "").lower()
    if any(h in url for h in ("api", "graphql", "search", "list", "products", "catalog", "feed")):
        score += 1.5
    if capture.get("status") == 200:
        score += 0.5
    return score


def best_api_capture(captures: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not captures:
        return None
    scored = sorted(captures, key=score_capture, reverse=True)
    top = scored[0]
    return top if score_capture(top) >= 3.0 else None


def replay_request_template(capture: dict[str, Any], page_headers: dict[str, str]) -> dict[str, Any]:
    """Build an httpx-replayable request descriptor from a captured response."""
    return {
        "url": capture["url"],
        "method": "GET",
        "headers": {k: v for k, v in page_headers.items()
                    if k.lower() in ("user-agent", "accept", "accept-language", "cookie")},
    }
