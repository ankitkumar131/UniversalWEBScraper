"""Unit tests: pagination auto-detection."""
from src.pagination.detector import PaginationPlan, build_url_for_page, detect_pagination


def _html(body: str, head: str = "") -> str:
    return f"<html><head>{head}</head><body>{body}</body></html>"


def test_rel_next_link_wins():
    html = _html('<a href="/page/2">next</a>', '<link rel="next" href="/page/2">')
    plan = detect_pagination(html, "https://x.com/page/1")
    assert plan.strategy == "next_button"
    assert plan.confidence >= 0.9


def test_next_button_in_nav():
    html = _html("""
      <nav class="pagination">
        <a href="/p/1">1</a>
        <a href="/p/2" class="next">Next ›</a>
      </nav>""")
    plan = detect_pagination(html, "https://x.com/p/1")
    assert plan.strategy == "next_button"
    assert plan.next_selector


def test_load_more_button():
    html = _html('<div class="list"></div><button class="btn-more">Load More</button>')
    plan = detect_pagination(html, "https://x.com/list")
    assert plan.strategy == "load_more"
    assert plan.load_more_selector


def test_url_pattern_from_current_url():
    plan = detect_pagination(_html("<p>no links</p>"), "https://x.com/catalog?page=3")
    assert plan.strategy == "url_pattern"
    assert build_url_for_page("https://x.com/catalog?page=3", 4) == "https://x.com/catalog?page=4"


def test_path_based_url_pattern():
    assert build_url_for_page("https://x.com/blog/page/2", 3) == "https://x.com/blog/page/3"


def test_offset_pattern():
    assert build_url_for_page("https://x.com/items?offset=20", 2) == "https://x.com/items?offset=40"


def test_no_pagination():
    plan = detect_pagination(_html("<h1>hello</h1><p>text</p>"), "https://x.com/about")
    assert plan.strategy == "none"


def test_infinite_scroll_marker():
    html = _html('<div class="infinite-scroll"></div><script src="/wp-content/plugins/jetpack-infinite-scroll/js/ias.js"></script>')
    plan = detect_pagination(html, "https://x.com/feed")
    assert plan.strategy == "infinite_scroll"


def test_build_url_template():
    assert build_url_for_page("https://x.com", 7, template="/page/{n}") == "/page/7"


def test_plan_to_dict_shape():
    assert PaginationPlan("none").to_dict()["strategy"] == "none"
