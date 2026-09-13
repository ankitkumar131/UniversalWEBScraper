"""Structured-data extraction (Mode 2): JSON-LD, microdata, Open Graph, Twitter cards."""
from __future__ import annotations

import json
from typing import Any

from bs4 import BeautifulSoup

from src.core.schemas import PageContent
from src.extraction.base import BaseExtractor, ExtractionResult

# JSON-LD @type values considered "item lists" -> explode into items
_LIST_TYPES = {"itemlist", "breadcrumblist", "offer catalog"}


def _flatten_jsonld(node: Any, out: list[dict[str, Any]]) -> None:
    """Collect meaningful JSON-LD nodes: top-level dicts and @graph entries.
    ItemList elements are exploded by the caller; ListItem wrappers skipped."""
    if isinstance(node, list):
        for n in node:
            _flatten_jsonld(n, out)
        return
    if not isinstance(node, dict):
        return
    out.append(node)
    for graph_entry in node.get("@graph", []) if isinstance(node.get("@graph"), list) else []:
        if isinstance(graph_entry, dict):
            out.append(graph_entry)


def _list_elements(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Items of an ItemList node (unwrap ListItem wrappers)."""
    elements = node.get("itemListElement")
    if not isinstance(elements, list):
        return []
    out = []
    for el in elements:
        if isinstance(el, dict) and isinstance(el.get("item"), dict):
            out.append(el["item"])
        elif isinstance(el, dict):
            out.append(el)
    return out


def _compact(node: dict[str, Any]) -> dict[str, Any]:
    """Keep the human-useful scalars of a JSON-LD node."""
    keep: dict[str, Any] = {}
    for key, value in node.items():
        if key.startswith("@"):
            if key == "@type":
                keep["type"] = value
            continue
        if isinstance(value, (str, int, float, bool)):
            keep[key] = value
        elif isinstance(value, list) and all(isinstance(v, (str, int, float)) for v in value):
            keep[key] = value
        elif isinstance(value, dict):
            simple = {k: v for k, v in value.items() if isinstance(v, (str, int, float))}
            if simple:
                keep[key] = simple
    return keep


class StructuredDataExtractor(BaseExtractor):
    name = "structured_data"

    async def extract(self, content: PageContent) -> ExtractionResult:
        result = ExtractionResult(strategy=self.name)
        soup = BeautifulSoup(content.html, "lxml")

        # --- JSON-LD -----------------------------------------------------
        ld_nodes: list[dict[str, Any]] = []
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "")
                _flatten_jsonld(data, ld_nodes)
            except (json.JSONDecodeError, TypeError):
                continue
        main: dict[str, Any] = {}
        for node in ld_nodes:
            ntype = str(node.get("@type", "")).lower()
            if ntype in _LIST_TYPES:
                for el in _list_elements(node):
                    compacted = _compact(el)
                    if compacted:
                        result.items.append(compacted)
            elif ntype == "listitem":
                continue  # wrapper noise
            else:
                compacted = _compact(node)
                if not main:
                    main = compacted
                elif len(compacted) > 1:
                    result.items.append(compacted)
        if main:
            result.page_fields = main

        # --- microdata ---------------------------------------------------
        for scope in soup.find_all(attrs={"itemscope": True}):
            item: dict[str, Any] = {"type": scope.get("itemtype", "").split("/")[-1]}
            for el in scope.find_all(attrs={"itemprop": True}):
                prop = el.get("itemprop")
                if el.get("content"):
                    item[prop] = el["content"]
                elif el.name == "a" and el.get("href"):
                    item[prop] = el["href"]
                elif el.name == "img" and el.get("src"):
                    item[prop] = el["src"]
                else:
                    item[prop] = el.get_text(" ", strip=True)
            if len(item) > 1:
                result.items.append(item)

        # --- Open Graph / Twitter meta ------------------------------------
        meta: dict[str, Any] = {}
        for tag in soup.find_all("meta"):
            key = tag.get("property") or tag.get("name") or ""
            content_val = tag.get("content")
            if not content_val:
                continue
            kl = key.lower()
            if kl.startswith("og:"):
                meta[f"og_{kl[3:].replace(':', '_')}"] = content_val
            elif kl.startswith("twitter:"):
                meta[f"twitter_{kl[8:].replace(':', '_')}"] = content_val
        if meta:
            result.page_fields = {**meta, **result.page_fields}
            if not result.items:  # meta alone is a valid single-item result
                result.items.append(meta)

        if not result.ok:
            result.errors.append("no structured data found")
        return result
