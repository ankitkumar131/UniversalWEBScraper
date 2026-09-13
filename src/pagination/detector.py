"""Pagination auto-detection (pure HTML analysis — browser independent).

Classifies pages into the sd.txt taxonomy:
  next_button | url_pattern | infinite_scroll | load_more | none
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

NEXT_TEXTS = {"next", "›", "»", ">", "older", "following", "forward", "próximo"}
LOAD_MORE_TEXTS = {"load more", "show more", "view more", "see more", "load additional", "more results",
                   "show additional", "afficher plus", "mehr anzeigen"}
PAGE_PARAM_NAMES = {"page", "p", "pg", "pagenum", "page_num", "pageno", "page_number",
                    "offset", "start", "from", "skip", "begin"}
# params holding absolute page numbers vs relative item offsets
ABSOLUTE_PAGE_PARAMS = {"page", "p", "pg", "pagenum", "page_num", "pageno", "page_number"}
_PATH_PAGE_RE = re.compile(r"^(.*?/)(page|p|pg)(/|=)(\d+)(.*)$", re.IGNORECASE)
_NEXT_PREFIX_RE = re.compile(r"^(next|older|forward|following|load more|show more)\b", re.I)


def _is_next_text(text: str) -> bool:
    return text in NEXT_TEXTS or bool(_NEXT_PREFIX_RE.match(text))


def _is_load_more_text(text: str) -> bool:
    return any(text.startswith(t) or text == t for t in LOAD_MORE_TEXTS)


@dataclass
class PaginationPlan:
    strategy: str = "none"                      # next_button|url_pattern|infinite_scroll|load_more|none
    next_selector: str | None = None
    next_href: str | None = None                # resolved href of the detected next link
    load_more_selector: str | None = None
    url_template: str | None = None             # absolute or /path/{n}
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy, "next_selector": self.next_selector,
            "next_href": self.next_href, "load_more_selector": self.load_more_selector,
            "url_template": self.url_template,
            "confidence": round(self.confidence, 2), "evidence": self.evidence[:5],
        }


def _norm_text(text: str) -> str:
    """Normalize button/link text: collapse whitespace, drop ellipses/dots."""
    return re.sub(r"\s+", " ", (text or "").replace("…", "").replace("...", "").strip()).lower()


def _css_selector_for(el) -> str | None:
    """Best-effort unique-ish CSS selector for a found element."""
    if el.get("id"):
        return f"#{el['id']}"
    classes = el.get("class")
    if classes:
        return f"{el.name}.{'.'.join(classes[:2])}"
    if el.get("rel"):
        return f"{el.name}[rel='{el['rel']}']"
    if el.get("href"):
        href = el["href"].replace("'", "\\'")
        return f"{el.name}[href='{href}']"
    return el.name


def _next_plan(el, confidence: float, evidence: list[str]) -> PaginationPlan:
    """Build a next_button plan from a next-ish link/button."""
    plan = PaginationPlan("next_button", next_selector=_css_selector_for(el),
                          confidence=confidence, evidence=evidence)
    href = el.get("href")
    if href and href not in ("#", "javascript:void(0)", ""):
        plan.next_href = href
    return plan


def build_url_for_page(url: str, page_number: int, template: str | None = None) -> str | None:
    """Generate the URL for page_number.

    Semantics: page-number params (page, p, ...) are set ABSOLUTELY;
    offset-style params (offset, start, skip, ...) advance by their own value
    per page (offset=20 -> page 2 = offset=40).
    """
    if template:
        return template.format(n=page_number)
    parts = urlsplit(url)
    qs = parse_qs(parts.query, keep_blank_values=True)
    for key in qs:
        kl = key.lower()
        if kl in PAGE_PARAM_NAMES:
            val = qs[key][0]
            if val.isdigit() or val == "":
                if val == "":
                    new_val = page_number
                elif kl in ABSOLUTE_PAGE_PARAMS:
                    new_val = page_number
                else:  # offset-style: relative step
                    new_val = int(val) * page_number
                qs[key] = [str(new_val)]
                return urlunsplit(parts._replace(query=urlencode(qs, doseq=True)))
    match = _PATH_PAGE_RE.match(parts.path)
    if match:
        prefix, word, sep, _num, suffix = match.groups()
        return urlunsplit(parts._replace(path=f"{prefix}{word}{sep}{page_number}{suffix}"))
    return None


def next_page_url(url: str) -> str | None:
    """Advance a paginated URL by exactly ONE page (page=3 -> page=4,
    offset=20 -> offset=40). None when no pagination param is present."""
    parts = urlsplit(url)
    qs = parse_qs(parts.query, keep_blank_values=True)
    for key in qs:
        kl = key.lower()
        if kl in PAGE_PARAM_NAMES:
            val = qs[key][0]
            if val.isdigit():
                new_val = (int(val) + 1) if kl in ABSOLUTE_PAGE_PARAMS else int(val) * 2
                qs[key] = [str(new_val)]
                return urlunsplit(parts._replace(query=urlencode(qs, doseq=True)))
    match = _PATH_PAGE_RE.match(parts.path)
    if match:
        prefix, word, sep, num, suffix = match.groups()
        return urlunsplit(parts._replace(path=f"{prefix}{word}{sep}{int(num) + 1}{suffix}"))
    return None


def detect_pagination(html: str, url: str) -> PaginationPlan:
    soup = BeautifulSoup(html, "lxml")
    evidence: list[str] = []

    # 1. rel="next" link in head/body — strongest signal
    rel_next = soup.find("link", rel="next") or soup.find("a", rel="next")
    if rel_next and rel_next.get("href"):
        plan = _next_plan(rel_next, 0.95, ["rel=next link"])
        plan.evidence.append(f"next href: {plan.next_href or ''}"[:90])
        return plan

    # 2. "Load More" style buttons
    for el in soup.find_all(["button", "a", "div[role=button]".replace("[role=button]", "")]):
        txt = _norm_text(el.get_text())
        if el.name in ("button", "a") and _is_load_more_text(txt):
            return PaginationPlan(
                "load_more", load_more_selector=_css_selector_for(el),
                confidence=0.9, evidence=[f"load-more text '{txt}'"],
            )

    # 3. pagination/nav containers with a "next"-ish control
    nav = soup.find(["nav", "ul", "div"], class_=re.compile(
        r"pagination|pager|page-nav|paging|pages", re.I))
    if nav:
        for el in nav.find_all(["a", "button"]):
            txt = _norm_text(el.get_text())
            aria = (el.get("aria-label") or "").lower()
            if _is_next_text(txt) or _is_next_text(aria):
                if el.name == "a" and el.get("href") and el.get("href") != "#":
                    return _next_plan(el, 0.85, [f"nav next link '{txt or aria}'"])
                return _next_plan(el, 0.8, [f"nav next button '{txt or aria}'"])
        evidence.append("pagination container found but no next control")

    # 4. standalone next-ish links/buttons
    for el in soup.find_all(["a", "button"]):
        txt = _norm_text(el.get_text())
        aria = (el.get("aria-label") or "").lower()
        if (_is_next_text(txt) or _is_next_text(aria)) and el.name == "a" and el.get("href", "#") != "#":
            return _next_plan(el, 0.7, [f"next link '{txt or aria}'"])

    # 5. URL pattern: current URL already paginated
    if build_url_for_page(url, 2) and build_url_for_page(url, 2) != url:
        return PaginationPlan(
            "url_pattern", url_template=None, confidence=0.6,
            evidence=["page parameter in current URL"],
        )

    # 6. infinite scroll indicators
    markers = 0
    for script in soup.find_all("script"):
        src = (script.get("src") or "").lower()
        if any(m in src for m in ("infinite", "jetpack-infinite-scroll", "ias")):
            markers += 1
    body_classes = " ".join(soup.body.get("class", []) if soup.body else [])
    if "infinite-scroll" in body_classes or "neverending" in body_classes:
        markers += 1
    if soup.select("[class*=infinite-scroll i], [id*=infinite-scroll i], .load-more-spinner"):
        markers += 1
    if markers:
        return PaginationPlan("infinite_scroll", confidence=0.5,
                              evidence=["infinite-scroll markers on page"])

    return PaginationPlan("none", confidence=0.3, evidence=evidence or ["no pagination signals"])
