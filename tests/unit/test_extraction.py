"""Unit tests: extraction pipeline (selectors, structured data, API payloads)."""
import pytest

from src.core.schemas import PageContent
from src.extraction.api_extractor import ApiExtractor
from src.extraction.hybrid_extractor import HybridExtractor
from src.extraction.selector_extractor import SelectorExtractor
from src.extraction.structured_data import StructuredDataExtractor

PRODUCT_HTML = """
<html><head><title>Shop</title>
<meta property="og:title" content="Best Products">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"ItemList",
 "itemListElement":[
   {"@type":"ListItem","item":{"@type":"Product","name":"Widget","offers":{"price":"9.99"}}},
   {"@type":"ListItem","item":{"@type":"Product","name":"Gadget","offers":{"price":"19.99"}}}]}
</script></head>
<body>
  <div class="product"><h3 class="name">Widget</h3><span class="price">$9.99</span>
     <a href="/p/1">view</a></div>
  <div class="product"><h3 class="name">Gadget</h3><span class="price">$19.99</span>
     <a href="/p/2">view</a></div>
</body></html>
"""


def _content(html=PRODUCT_HTML, url="https://shop.example.com/"):
    return PageContent(url=url, html=html)


async def test_selector_list_extraction():
    ext = SelectorExtractor({"schema": {"items": ".product",
                                        "fields": {"name": "h3.name", "price": ".price",
                                                   "link": "a @href"}}})
    result = await ext.extract(_content())
    assert len(result.items) == 2
    assert result.items[0]["name"] == "Widget"
    assert result.items[0]["link"] == "/p/1"


async def test_selector_compound_fallback():
    ext = SelectorExtractor({"schema": {"price": [".nope", ".price"]}})
    result = await ext.extract(_content())
    assert result.page_fields["price"] == "$9.99"


async def test_selector_page_fields():
    ext = SelectorExtractor({"schema": {"title": "title"}})
    result = await ext.extract(_content())
    assert result.page_fields["title"] == "Shop"


async def test_structured_data_jsonld_items():
    ext = StructuredDataExtractor()
    result = await ext.extract(_content())
    names = [i.get("name") for i in result.items]
    assert "Widget" in names and "Gadget" in names
    assert result.page_fields.get("og_title") == "Best Products"


async def test_microdata_extraction():
    html = """<div itemscope itemtype="https://schema.org/Product">
      <span itemprop="name">Thing</span>
      <span itemprop="price" content="5.00">five</span>
    </div>"""
    result = await StructuredDataExtractor().extract(_content(html))
    assert any(i.get("name") == "Thing" for i in result.items)


async def test_api_extractor_finds_lists():
    content = _content(html="<html></html>")
    content.captured_api = [
        {"url": "https://x.com/api/v2/products?page=1", "status": 200,
         "json": {"data": {"items": [{"id": 1}, {"id": 2}]}}},
        {"url": "https://x.com/api/tracking", "status": 200, "json": {"event": "view"}},
    ]
    result = await ApiExtractor().extract(content)
    assert len(result.items) == 2


async def test_hybrid_prefers_structured_data():
    ext = HybridExtractor({"mode": "hybrid"})
    result = await ext.extract(_content())
    assert result.ok
    assert "structured_data" in result.strategy


async def test_hybrid_falls_back_to_heuristic_without_llm():
    content = _content("<html><body><article><h1>Story</h1><p>Long text here</p></article></body></html>")
    result = await HybridExtractor({"mode": "hybrid"}).extract(content)
    assert result.ok
    assert any("heuristic" in s for s in result.strategy.split("+"))


async def test_xpath_extraction():
    ext = SelectorExtractor({"schema": {"title": "xpath://head/title"}})
    result = await ext.extract(_content())
    assert result.page_fields["title"] == "Shop"
