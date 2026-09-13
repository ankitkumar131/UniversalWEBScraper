"""Extract data from intercepted API/XHR responses (Mode 5 in sd.txt).

The browser network interceptor (src/browser/interceptors/network.py) records
JSON responses; this extractor digs list-shaped payloads out of them.
"""
from __future__ import annotations

from typing import Any

from src.core.schemas import PageContent
from src.extraction.base import BaseExtractor, ExtractionResult

_LIST_KEYS = ("items", "results", "data", "records", "products", "list", "rows",
              "hits", "documents", "entries", "elements")


def _find_lists(node: Any, depth: int = 0) -> list[list[dict[str, Any]]]:
    """Find list-of-objects arrays inside a parsed JSON payload."""
    found: list[list[dict[str, Any]]] = []
    if depth > 4:
        return found
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
                found.append(value)
            else:
                found.extend(_find_lists(value, depth + 1))
    elif isinstance(node, list) and node and all(isinstance(v, dict) for v in node):
        found.append(node)
    return found


class ApiExtractor(BaseExtractor):
    name = "api_interception"

    async def extract(self, content: PageContent) -> ExtractionResult:
        result = ExtractionResult(strategy=self.name)
        for capture in content.captured_api:
            body = capture.get("json")
            if body is None:
                continue
            for lst in _find_lists(body):
                if len(lst) >= 2 or not result.items:  # prefer clearly-list payloads
                    result.items.extend(lst)
        if not result.ok:
            result.errors.append("no list-shaped API payloads captured")
        return result
