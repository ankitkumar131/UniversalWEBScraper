"""Hybrid extraction (Mode 4, the recommended default): structured data ->
configured selectors -> intercepted API payloads -> LLM/heuristic. Merges
results, preferring structured sources."""
from __future__ import annotations

from typing import Any

import structlog

from src.core.schemas import PageContent
from src.extraction.api_extractor import ApiExtractor
from src.extraction.base import BaseExtractor, ExtractionResult
from src.extraction.llm_extractor import HeuristicExtractor, LLMExtractor
from src.extraction.selector_extractor import SelectorExtractor
from src.extraction.structured_data import StructuredDataExtractor

log = structlog.get_logger(__name__)


def _merge_items(primary: list[dict], secondary: list[dict]) -> list[dict]:
    """Zip secondary onto primary positionally, filling missing keys only."""
    if not primary:
        return secondary
    if not secondary:
        return primary
    merged = [dict(item) for item in primary]
    for i, extra in enumerate(secondary[: len(merged)]):
        for k, v in extra.items():
            if k not in merged[i] or merged[i][k] in (None, ""):
                merged[i][k] = v
    return merged


class HybridExtractor(BaseExtractor):
    name = "hybrid"

    def __init__(self, config: Any | None = None, *, llm_settings: dict[str, Any] | None = None,
                 llm_api_key: str | None = None):
        super().__init__(config)
        cfg = config if isinstance(config, dict) else {}
        mode = cfg.get("mode", "hybrid")
        has_selectors = bool(cfg.get("schema") or cfg.get("fields") or cfg.get("list_selector"))
        self._llm = LLMExtractor(config, llm_settings=llm_settings, api_key=llm_api_key)
        self._chain: list[BaseExtractor] = []
        if mode in ("css_selectors", "xpath") and has_selectors:
            self._chain.append(SelectorExtractor(config))
        elif mode == "llm_auto":
            self._chain.append(self._llm)
        else:  # hybrid (also the fallback when selectors are unconfigured)
            if has_selectors:
                # explicit selectors win: run first and short-circuit on success
                self._chain.append(SelectorExtractor(config))
                self._chain.append(StructuredDataExtractor(config))
            else:
                self._chain.append(StructuredDataExtractor(config))
            self._chain.append(ApiExtractor(config))
            self._chain.append(self._llm)

    async def extract(self, content: PageContent) -> ExtractionResult:
        final = ExtractionResult(strategy="hybrid")
        tried: list[str] = []
        for extractor in self._chain:
            try:
                res = await extractor.extract(content)
            except Exception as e:  # a broken extractor must not kill the pipeline
                log.warning("extractor_failed", extractor=extractor.name, error=str(e))
                continue
            if res.ok:
                tried.append(res.strategy)
                final.items = _merge_items(final.items, res.items)
                for k, v in res.page_fields.items():
                    final.page_fields.setdefault(k, v)
                # a confident selector/structured result short-circuits the chain
                if extractor.name in ("css_selectors", "structured_data") and res.items:
                    break
            final.errors.extend(res.errors)
        final.strategy = "+".join(tried) or "none"
        return final


__all__ = ["HybridExtractor", "HeuristicExtractor", "SelectorExtractor",
           "StructuredDataExtractor", "ApiExtractor", "LLMExtractor"]
