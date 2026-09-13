"""LLM-based intelligent extraction (Mode 3) with heuristic fallback.

Uses any OpenAI-compatible chat-completions endpoint (OPENAI_API_KEY /
OPENAI_BASE_URL) — including local Llama/Mistral servers. When no key is
configured, falls back to a deterministic heuristic extractor so the system
is fully functional offline.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx
import structlog

from src.core.schemas import PageContent
from src.extraction.base import BaseExtractor, ExtractionResult

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are a web data extraction engine. You receive cleaned markdown-like text "
    "from a web page and extract structured data as JSON. Respond ONLY with valid "
    "JSON: an object {\"items\": [...], \"page\": {...}} where 'items' is a list of "
    "records found on the page (products, articles, listings, rows, etc.; empty list "
    "if none) and 'page' holds page-level fields (title, description, ...). "
    "Use concise, predictable field names (title, price, url, date, author, ...). "
    "Never wrap the JSON in markdown fences."
)


def clean_html_to_text(html: str, max_chars: int = 24000) -> str:
    """Strip scripts/styles/nav/footer and reduce token noise."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "nav", "footer", "header", "form"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:max_chars]


class LLMExtractor(BaseExtractor):
    name = "llm_auto"

    def __init__(self, config: Any | None = None, *, llm_settings: dict[str, Any] | None = None,
                 api_key: str | None = None):
        super().__init__(config)
        self._settings = llm_settings or {}
        self._api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self._api_key) and self._settings.get("provider") == "openai_compatible"

    async def extract(self, content: PageContent) -> ExtractionResult:
        if not self.enabled:
            result = await HeuristicExtractor(self.config).extract(content)
            result.strategy = "heuristic"
            return result
        return await self._extract_with_llm(content)

    async def _extract_with_llm(self, content: PageContent) -> ExtractionResult:
        result = ExtractionResult(strategy=self.name)
        text = clean_html_to_text(content.html)
        cfg = self.config or {}
        user_prompt = cfg.get("llm_prompt") or "Extract all structured data from this page."
        user_prompt += f"\n\nPage URL: {content.effective_url}\n\nPage content:\n{text}"
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(
                    f"{self._settings.get('base_url', 'https://api.openai.com/v1').rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": self._settings.get("model", "gpt-4o-mini"),
                        "messages": [
                            {"role": "system", "content": _SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0,
                        "max_tokens": int(self._settings.get("max_tokens", 4000)),
                    },
                )
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"].strip()
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.S)
            parsed = json.loads(raw)
            result.items = parsed.get("items") or []
            result.page_fields = parsed.get("page") or {}
        except Exception as e:
            log.warning("llm_extraction_failed", error=str(e))
            result.errors.append(f"llm failed: {e}")
            fallback = await HeuristicExtractor(self.config).extract(content)
            fallback.strategy = "heuristic"
            return fallback
        return result


class HeuristicExtractor(BaseExtractor):
    """Deterministic fallback: readable text + headings + links summary."""

    name = "heuristic"

    async def extract(self, content: PageContent) -> ExtractionResult:
        from bs4 import BeautifulSoup

        result = ExtractionResult(strategy=self.name)
        soup = BeautifulSoup(content.html, "lxml")
        for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
            tag.decompose()

        page: dict[str, Any] = {}
        if soup.title and soup.title.get_text(strip=True):
            page["title"] = soup.title.get_text(strip=True)
        desc = soup.find("meta", attrs={"name": "description"})
        if desc and desc.get("content"):
            page["description"] = desc["content"]

        # main content: largest text-dense block
        candidates = soup.find_all(["article", "main", "div", "section"])
        best, best_len = None, 0
        for el in candidates:
            txt = el.get_text(" ", strip=True)
            if len(txt) > best_len and el.find(["h1", "h2", "p"]):
                best, best_len = el, len(txt)
        scope = best or soup.body or soup
        page["text"] = scope.get_text("\n", strip=True)[:20000]
        page["word_count"] = len(page["text"].split())

        headings = [h.get_text(" ", strip=True) for h in scope.find_all(["h1", "h2", "h3"])[:50]]
        if headings:
            page["headings"] = headings

        links = [
            {"text": a.get_text(" ", strip=True)[:100], "href": a.get("href", "")}
            for a in scope.find_all("a", href=True)[:200]
            if a.get_text(strip=True)
        ]
        if links:
            page["links"] = links

        result.page_fields = page
        result.items = [page]
        return result
