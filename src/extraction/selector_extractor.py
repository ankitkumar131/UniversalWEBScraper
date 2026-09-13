"""CSS / XPath selector-based extraction (Mode 1 in sd.txt).

Schema forms:
  {"title": "h1", "price": ".price"}                      page-level fields
  {"items": ".product", "fields": {"name": "h3"}}         list extraction
  compound fallbacks: {"price": [".price", "[itemprop=price]"]}
  attribute refs:  "a.link @href"  or  ".img @src"  (default: text)
  xpath:           "xpath://div[@class='x']//span"
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from src.core.schemas import PageContent
from src.extraction.base import BaseExtractor, ExtractionResult

_XPATH_RE = re.compile(r"^xpath:", re.IGNORECASE)


def select_elements(soup: BeautifulSoup, selector: str):
    """Run a CSS or XPath selector against a BeautifulSoup tree."""
    if _XPATH_RE.match(selector):
        from lxml import html as lxml_html  # deferred: lxml C dependency

        xp = _XPATH_RE.sub("", selector).strip()
        tree = lxml_html.fromstring(str(soup))
        for el in tree.xpath(xp):
            if isinstance(el, str):
                yield el
            else:
                yield BeautifulSoup(el, "html.parser")  # rare, deep xpath nodes
        return
    yield from soup.select(selector)


def _selector_lists(schema: dict[str, Any]) -> list[tuple[str, list[str]]]:
    out = []
    for field_name, sel in schema.items():
        sels = sel if isinstance(sel, list) else [sel]
        out.append((field_name, sels))
    return out


def _extract_value(element: Any, ref: str) -> Any:
    """Resolve 'sel @attr' style references against an element."""
    sel, sep, attr = ref.strip().partition("@")
    sel = sel.strip() or None
    target = element.select_one(sel) if sel else element
    if target is None:
        return None
    if sep and attr:
        if attr == "text":
            return target.get_text(" ", strip=True)
        if attr == "html":
            return target.decode_contents().strip()
        return target.get(attr)
    # default: text of the matched node
    return target.get_text(" ", strip=True)


class SelectorExtractor(BaseExtractor):
    name = "css_selectors"

    async def extract(self, content: PageContent) -> ExtractionResult:
        result = ExtractionResult(strategy=self.name)
        cfg = self.config or {}
        schema: dict[str, Any] | None = cfg.get("schema") if isinstance(cfg, dict) else None
        if not schema and cfg.get("fields"):
            schema = dict(cfg["fields"])
            if cfg.get("list_selector"):
                schema = {"items": cfg["list_selector"], "fields": schema}

        if not schema:
            result.errors.append("no selector schema configured")
            return result

        soup = BeautifulSoup(content.html, "lxml")

        # list mode
        items_selector = schema.get("items")
        fields = schema.get("fields") or {k: v for k, v in schema.items() if k != "items"}
        if items_selector:
            containers = list(soup.select(items_selector))
            for container in containers:
                item: dict[str, Any] = {}
                for field_name, sels in _selector_lists(fields):
                    item[field_name] = self._first_match(container, sels)
                if any(v not in (None, "") for v in item.values()):
                    result.items.append(item)
            return result

        # page-level mode
        for field_name, sels in _selector_lists(schema):
            val = self._first_match(soup, sels)
            if val is not None:
                result.page_fields[field_name] = val
        if result.page_fields:
            result.items.append(result.page_fields)
        return result

    @staticmethod
    def _first_match(scope: Any, sels: list[str]) -> Any:
        for sel in sels:
            try:
                if _XPATH_RE.match(sel):
                    from lxml import html as lxml_html

                    xp = _XPATH_RE.sub("", sel).strip()
                    tree = lxml_html.fromstring(str(scope))
                    found = tree.xpath(xp)
                    if found:
                        first = found[0]
                        return first if isinstance(first, str) else str(first.text_content()).strip()
                    continue
                value = _extract_value(scope, sel)
                if value not in (None, ""):
                    return value
            except Exception:
                continue  # invalid selector -> try the fallback
        return None
