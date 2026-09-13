"""Browser-mode integration test (requires a working Playwright Chromium —
run scripts/setup_browser.py first when Playwright's CDN is unreachable).

Runs the full middleware pipeline (stealth, page load, popup dismissal, cookie
consent, content readiness) against a local page with a modal, a cookie
banner, JSON-LD, and a Load More button."""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.core.config import load_settings
from src.core.engine import ScrapeEngine
from src.core.schemas import ExtractionConfig, PaginationConfig, ScrapeJob, ScrapeJobConfig
from src.storage.artifacts import ArtifactStore
from src.storage.database import create_tables, dispose_engine, init_engine, session_factory

pytestmark = pytest.mark.browser

PAGE_1 = """<html><head><title>Browser Test</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"ItemList","itemListElement":[
 {"@type":"ListItem","item":{"@type":"Product","name":"Alpha"}},
 {"@type":"ListItem","item":{"@type":"Product","name":"Beta"}}]}
</script>
<style>.modal{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;
align-items:center;justify-content:center}.modal .box{background:#fff;padding:20px}</style>
</head><body>
<div class="modal" id="promo"><div class="box"><p>Subscribe!</p>
  <button class="modal-close" aria-label="Close">✕</button></div></div>
<div id="cookie-banner"><p>We use cookies</p><button id="accept">Accept All</button></div>
<div class="products">
  <div class="product"><h3 class="name">Alpha</h3></div>
  <div class="product"><h3 class="name">Beta</h3></div>
</div>
<button class="btn-more">Load More</button>
<div class="products more hidden-products" style="display:none">
  <div class="product"><h3 class="name">Gamma</h3></div>
</div>
<script>
document.querySelector('.btn-more').addEventListener('click', () => {
  document.querySelector('.more').style.display = 'block';
});
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
            ctype = "text/plain"
        else:
            body = PAGE_1.encode()
            ctype = "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
async def browser_engine(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("browser_engine")
    settings = load_settings()
    settings._data.setdefault("storage", {})["database_url"] = \
        f"sqlite+aiosqlite:///{(tmp / 'b.db').as_posix()}"
    settings._data.setdefault("app", {})["data_dir"] = str(tmp)
    init_engine(settings.database_url)
    await create_tables()
    from src.core.job_manager import JobManager

    jobs = JobManager(session_factory(), ArtifactStore(tmp / "artifacts"))
    engine = ScrapeEngine.build(settings, job_manager=jobs)
    if not engine.browser.available:
        pytest.skip("playwright not installed")
    ok = await engine.browser.start()
    if not ok:
        pytest.skip("browser could not launch (run scripts/setup_browser.py)")
    yield engine, jobs, tmp
    await engine.close()
    await dispose_engine()


@pytest.fixture(scope="module")
def local_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


async def test_browser_pipeline_popups_loadmore_extraction(browser_engine, local_site):
    engine, jobs, tmp = browser_engine
    job = ScrapeJob(url=local_site, config=ScrapeJobConfig(
        rendering="full_browser",
        extraction=ExtractionConfig(schema={"items": ".product", "fields": {"name": "h3.name"}}),
        pagination=PaginationConfig(strategy="auto_detect", max_pages=3),
        output={"format": "json", "include_raw_html": True, "screenshot": True},
    ))
    result = await engine.run(job)
    assert result.state.value == "completed", result.error_log
    stats = result.stats
    assert stats["rendering_mode"] == "browser"
    assert stats["popups_dismissed"] >= 1  # modal and/or cookie banner dismissed
    # Load More button clicked -> Gamma appears
    items, total = await jobs.get_results(job.job_id)
    names = [i["data"]["name"] for i in items]
    assert "Gamma" in names, names
    # artifacts saved
    artifacts = await engine.artifacts.list_artifacts(job.job_id)
    assert any(a.endswith(".html") for a in artifacts)
    assert any(a.endswith(".png") for a in artifacts)
