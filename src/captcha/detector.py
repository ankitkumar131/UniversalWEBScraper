"""CAPTCHA / bot-challenge detection (sd.txt "Challenge Detection & Bypass").

Pure functions over HTML/status so they work in both lightweight and browser
modes and are unit-testable."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# content markers -> (kind, label)
CONTENT_MARKERS: list[tuple[str, str, str]] = [
    ("just a moment", "cloudflare_js", "Cloudflare JS challenge"),
    ("checking your browser", "cloudflare_js", "Cloudflare browser check"),
    ("attention required! | cloudflare", "cloudflare_block", "Cloudflare WAF block"),
    ("access denied", "waf_block", "WAF block"),
    ("pardon our interruption", "datadome", "DataDome challenge"),
    ("please verify you are a human", "captcha", "human verification"),
    ("verify you are human", "captcha", "human verification"),
    ("are you a robot", "captcha", "robot check"),
    ("enable javascript", "js_required", "JavaScript required"),
    ("unusual traffic", "rate_limit", "unusual traffic / rate limit"),
    ("request blocked", "waf_block", "request blocked"),
    ("ddos protection by", "cloudflare_js", "DDoS protection interstitial"),
]

# markers that only count when the page is essentially empty of real content
# (nearly every site says "please enable JavaScript" inside a <noscript> block)
_WEAK_MARKERS = {"enable javascript"}

ELEMENT_MARKERS = [
    (r"id=[\"']?cf-challenge-running", "cloudflare_js"),
    (r"cdn-cgi/challenge-platform", "cloudflare_js"),
    (r"iframe[^>]+hcaptcha\.com", "hcaptcha"),
    (r"iframe[^>]+recaptcha", "recaptcha"),
    (r"data-sitekey", "recaptcha_or_hcaptcha"),
    (r"id=[\"']?px-captcha", "perimeterx"),
    (r"class=[\"'][^\"']*geetest", "geetest"),
    (r"challenges\.cloudflare\.com/turnstile", "turnstile"),
]

STATUS_KINDS = {403: "block", 429: "rate_limit", 503: "challenge"}

_STRIP_TAGS_RE = re.compile(r"<(script|noscript|style|svg|template)[^>]*>.*?</\1>",
                            re.IGNORECASE | re.DOTALL)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class ChallengeInfo:
    detected: bool = False
    kind: str = ""                 # cloudflare_js|cloudflare_block|captcha|hcaptcha|recaptcha|turnstile|perimeterx|datadome|waf_block|rate_limit|js_required
    label: str = ""
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"detected": self.detected, "kind": self.kind, "label": self.label,
                "evidence": self.evidence[:3]}


def _clean_text(html: str) -> str:
    """Markup minus script/noscript/style/template content (noscript fallbacks
    like 'please enable JavaScript' must not look like challenges)."""
    return _TAG_RE.sub(" ", _STRIP_TAGS_RE.sub(" ", html or "")).lower()


def _title_text(html: str) -> str:
    match = _TITLE_RE.search(html or "")
    return _TAG_RE.sub(" ", match.group(1)).strip().lower() if match else ""


def detect_challenge(html: str, status: int = 200) -> ChallengeInfo:
    """Detect bot walls / challenges from response status + page HTML."""
    info = ChallengeInfo()
    cleaned = _clean_text(html)
    title = _title_text(html)

    kind = STATUS_KINDS.get(status)
    if kind:
        info.detected = True
        info.kind = kind
        info.evidence.append(f"HTTP {status}")

    for marker, mkind, label in CONTENT_MARKERS:
        if marker not in cleaned:
            continue
        if marker in _WEAK_MARKERS:
            # only a real challenge if it's the page's whole message:
            # in the title, or the page has almost no other content
            if marker not in title and len(cleaned) > 600:
                continue
        info.detected = True
        if not info.kind or info.kind in ("block", "challenge"):
            info.kind = mkind
        info.label = label
        info.evidence.append(f"text: {marker!r}")
        break

    if not info.detected:
        raw = (html or "").lower()
        for pattern, mkind in ELEMENT_MARKERS:
            if re.search(pattern, raw):
                info.detected = True
                info.kind = mkind
                info.evidence.append(f"element: {pattern[:40]}")
                break

    # short challenge pages with no real content are challenge pages
    if not info.detected and status in (403, 503, 429):
        info.detected = True
        info.kind = info.kind or "block"
    return info


def extract_sitekey(html: str) -> str | None:
    """Pull the CAPTCHA sitekey out of a page (data-sitekey or iframe src)."""
    match = re.search(r'data-sitekey=[["\']]?([\w-]{20,})', html or "")
    if match:
        return match.group(1)
    match = re.search(r"[?&]sitekey=([\w-]{20,})", html or "")
    if match:
        return match.group(1)
    return None
