"""Unit tests: data pipeline (dedupe, transform, validate, enrich) + pagination dedupe."""
from src.pagination.deduplicator import item_hash
from src.pipeline.deduplicator import DataDeduplicator
from src.pipeline.enricher import Enricher
from src.pipeline.transformer import Transformer
from src.pipeline.validator import Validator


def test_item_hash_stable_and_case_insensitive():
    assert item_hash({"url": "https://a.com", "title": "Hello"}) == \
           item_hash({"url": "https://a.com", "title": "hello  "})


def test_item_hash_differs_on_content():
    assert item_hash({"url": "https://a.com", "title": "A"}) != \
           item_hash({"url": "https://a.com", "title": "B"})


def test_deduplicator_drops_repeats_and_counts():
    dd = DataDeduplicator()
    out = dd.process([{"url": "a", "title": "x"}, {"url": "a", "title": "x"},
                      {"url": "b", "title": "y"}])
    assert len(out) == 2
    assert dd.duplicates == 1


def test_transformer_cleans_whitespace_and_urls():
    items = [{"name": "  Hello   World ", "link": "/p/1"}]
    out = Transformer().process(items, "https://x.com/list")
    assert out[0]["name"] == "Hello World"
    assert out[0]["link"] == "https://x.com/p/1"


def test_transformer_coerces_numbers_and_dates():
    out = Transformer().process([{"count": "42", "price": "1,299.50",
                                  "date": "2024-03-01", "code": "007"}], "https://x.com")
    assert out[0]["count"] == 42
    assert out[0]["price"] == 1299.5
    assert out[0]["date"] == "2024-03-01"
    assert out[0]["code"] == "007"  # leading zero stays a string


def test_transformer_price_with_currency_symbol():
    out = Transformer().process([{"price": "€ 12,99"}], "https://x.com")
    assert out[0]["price"] == 12.99


def test_validator_required_fields():
    v = Validator(required_fields=["title", "url"])
    valid, errors = v.process([
        {"title": "ok", "url": "https://a"}, {"title": None, "url": "https://b"},
        {"title": "x"},  # missing url
    ])
    assert len(valid) == 1
    assert len(errors) == 2


def test_validator_drops_empty_items():
    valid, _ = Validator(min_fields=1).process([{"a": None, "b": ""}, {"a": "val"}])
    assert len(valid) == 1


def test_enricher_adds_provenance():
    items = Enricher().process([{"title": "t"}], job_id="j1",
                               page_url="https://x.com/p/2", page_number=2, strategy="hybrid")
    assert items[0]["_source_url"] == "https://x.com/p/2"
    assert items[0]["_domain"] == "x.com"
    assert items[0]["_job_id"] == "j1"
    assert items[0]["_page"] == 2
    assert items[0]["_scraped_at"]
