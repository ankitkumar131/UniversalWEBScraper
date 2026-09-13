"""Integration test: full engine run in lightweight mode against a local
HTTP server with paginated product listings, JSON-LD, cookie banner, and a
looping pagination edge case."""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.core.config import Settings, load_settings
from src.core.engine import ScrapeEngine
from src.core.schemas import (ExtractionConfig, PaginationConfig, ScrapeJob,
                              ScrapeJobConfig)
from src.storage.artifacts import ArtifactStore
from src.storage.database import (create_tables, dispose_engine, init_engine,
                                  session_factory)

PRODUCTS = {
    1: [("Widget", "9.99"), ("Gadget", "19.99"), ("Thing", "4.50")],
    2: [("Doohickey", "14.00"), ("Contraption", "29.99"), ("Apparatus", "7.25")],
    3: [("Gizmo", "3.10"), ("Doodad", "8.80")],
}

COOKIE_BANNER = """
<div id="cookie-banner" class="cookie-consent">
  <p>We use cookies</p>
  <button id="accept">Accept All</button>
</div>"""


def page_html(page: int, base: str) -> str:
    products = PRODUCTS[page]
    next_link = (f'<a rel="next" href="{base}/page/{page + 1}">Next</a>'
                 if page < 3 else "")
    items = "".join(
        f'<div class="product"><h3 class="name">{n}</h3>'
        f'<span class="price">${p}</span><a href="{base}/item/{i}">view</a></div>'
        for i, (n, p) in enumerate(products, start=100 * page))
    ld = {
        "@context": "https://schema.org", "@type": "ItemList",
        "itemListElement": [{"@type": "ListItem",
                             "item": {"@type": "Product", "name": n, "offers": {"price": p}}}
                            for n, p in products],
    }
    return f"""<html><head><title>Shop page {page}</title>
<script type="application/ld+json">{json.dumps(ld)}</script></head>
<body>{COOKIE_BANNER}<div class="list">{items}</div>
<nav class="pagination">{next_link}</nav></body></html>"""


class Handler(BaseHTTPRequestHandler):
    base: str = "http://127.0.0.1:1"

    def log_message(self, *args):  # silence
        pass

    def do_GET(self):
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\nCrawl-delay: 0\n"
            self._send(200, body, "text/plain")
        elif self.path.startswith("/items"):
            self._send(200, json.dumps({"items": [{"id": 1, "name": "api-item"}]}).encode(),
                       "application/json")
        elif self.path.startswith("/page/"):
            try:
                page = int(self.path.split("/")[-1])
            except ValueError:
                self._send(404, b"not found", "text/html")
                return
            if page in PRODUCTS:
                self._send(200, page_html(page, self.base).encode(), "text/html")
            elif page == 99:  # loop back to page 1 (misconfigured site)
                self._send(200, page_html(1, self.base).encode(), "text/html")
            else:
                self._send(404, b"not found", "text/html")
        elif self.path == "/":
            self._send(200, page_html(1, self.base).encode(), "text/html")
        else:
            self._send(404, b"not found", "text/html")

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def local_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    Handler.base = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture(scope="module")
async def engine(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("engine")
    settings = load_settings()
    settings._data.setdefault("storage", {})["database_url"] = \
        f"sqlite+aiosqlite:///{(tmp / 'engine.db').as_posix()}"
    settings._data.setdefault("app", {})["data_dir"] = str(tmp)
    init_engine(settings.database_url)
    await create_tables()
    from src.core.job_manager import JobManager

    jobs = JobManager(session_factory(), ArtifactStore(tmp / "artifacts"))
    eng = ScrapeEngine.build(settings, job_manager=jobs)
    yield eng, jobs, tmp
    await eng.close()
    await dispose_engine()


def _job(url: str, **kw) -> ScrapeJob:
    config = ScrapeJobConfig(
        rendering=kw.pop("rendering", "lightweight"),
        extraction=ExtractionConfig(**kw.pop("extraction", {})),
        pagination=PaginationConfig(**kw.pop("pagination", {})),
    )
    return ScrapeJob(url=url, config=config)


@pytest.fixture(autouse=True)
def _fresh_politeness_state(engine):
    """Isolate robots cache + rate limiter state between tests."""
    eng = engine[0]
    eng.robots._mem.clear()
    eng.rate_limiter._buckets.clear()
    yield
    eng.robots._mem.clear()
    eng.rate_limiter._buckets.clear()


async def test_engine_scrapes_all_pages_and_dedupes(engine, local_site):
    eng, jobs, _ = engine
    job = _job(f"{local_site}/page/1",
               extraction={"schema": {"items": ".product",
                                      "fields": {"name": "h3.name", "price": ".price",
                                                 "link": "a @href"}}},
               pagination={"max_pages": 10})
    result = await eng.run(job)
    assert result.state.value == "completed"
    assert result.stats["pages_scraped"] == 3
    assert result.stats["items_extracted"] == 8  # 3 + 3 + 2
    assert result.stats["rendering_mode"] == "lightweight"

    items, total = await jobs.get_results(job.job_id)
    assert total == 8
    names = [i["data"]["name"] for i in items]
    assert "Widget" in names and "Doodad" in names
    # transformer resolved relative links + coerced prices
    by_name = {i["data"]["name"]: i["data"] for i in items}
    assert by_name["Widget"]["link"].startswith(local_site)
    assert by_name["Widget"]["price"] == 9.99
    # enricher provenance
    assert by_name["Widget"]["_job_id"] == job.job_id


async def test_engine_robots_disallow(engine, local_site, monkeypatch):
    eng, _, _ = engine
    eng.robots._mem.clear()  # bypass the per-domain robots cache

    original = Handler.do_GET

    def disallow(self):
        if self.path == "/robots.txt":
            self._send(200, b"User-agent: *\nDisallow: /\n", "text/plain")
            return None
        return original(self)

    monkeypatch.setattr(Handler, "do_GET", disallow)
    job = _job(f"{local_site}/page/1")
    result = await eng.run(job)
    assert result.state.value == "failed"
    assert any("robots" in str(e).lower() for e in result.error_log)


async def test_engine_pagination_loop_detected(engine, local_site):
    """Page 99 links to itself forever — dedupe must stop the loop."""
    eng, jobs, _ = engine
    loop_html = page_html(1, local_site).replace(
        f'href="{local_site}/page/2"',
        f'href="{local_site}/page/99"', 1)
    original = Handler.do_GET

    def looped(self):
        if self.path in ("/page/1", "/page/99"):
            self._send(200, loop_html.encode(), "text/html")
            return None
        return original(self)

    Handler.do_GET = looped  # permanent for this test
    try:
        job = _job(f"{local_site}/page/1",
                   extraction={"schema": {"items": ".product", "fields": {"name": "h3.name"}}},
                   pagination={"max_pages": 10})
        result = await eng.run(job)
        assert result.state.value == "completed"
        # page 1 (3 fresh) -> page 99 (dupes) -> page 99 again (dupes) -> stop
        assert result.stats["items_extracted"] == 3
        assert result.stats["duplicates_skipped"] >= 3
    finally:
        Handler.do_GET = original


async def test_engine_404_is_fatal(engine, local_site):
    eng, _, _ = engine
    job = _job(f"{local_site}/nope")
    result = await eng.run(job)
    assert result.state.value == "failed"
    assert any("404" in str(e) for e in result.error_log)


async def test_engine_jsonld_fallback_extraction(engine, local_site):
    """No selectors configured -> structured data extraction kicks in."""
    eng, jobs, _ = engine
    job = _job(f"{local_site}/page/2", pagination={"max_pages": 1})
    result = await eng.run(job)
    assert result.state.value == "completed"
    items, total = await jobs.get_results(job.job_id)
    assert total >= 3
    assert "structured_data" in result.stats.get("extraction_strategy", "")
