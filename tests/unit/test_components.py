"""Unit tests: challenge detection, circuit breaker, rate limiter, fingerprints,
behavior math, popup/cookie config integrity."""
import asyncio

from src.browser.stealth.behavior import bezier_point, gaussian_delay_ms
from src.browser.stealth.fingerprint import (accept_language_for,
                                             default_fingerprint,
                                             generate_fingerprint)
from src.captcha.detector import detect_challenge, extract_sitekey
from src.core.circuit_breaker import BreakerState, CircuitBreaker
from src.queue.rate_limiter import RateLimiter, TokenBucket


# --- challenge detection -------------------------------------------------
def test_cloudflare_js_challenge_text():
    info = detect_challenge("<html><title>Just a moment...</title></html>", 503)
    assert info.detected and info.kind == "cloudflare_js"


def test_recaptcha_iframe():
    html = '<iframe src="https://www.google.com/recaptcha/api2/anchor?sitekey=abc"></iframe>'
    info = detect_challenge(html, 200)
    assert info.detected and "recaptcha" in info.kind


def test_hcaptcha_iframe_and_sitekey():
    html = ('<iframe src="https://newassets.hcaptcha.com/captcha/v1/abc/static/hcaptcha.html'
            '#sitekey=aaaaaaaaaaaaaaaaaaaaaa"></iframe>')
    assert detect_challenge(html).detected
    assert extract_sitekey("x data-sitekey='aaaaaaaaaaaaaaaaaaaaaa'") == "aaaaaaaaaaaaaaaaaaaaaa"


def test_403_block():
    assert detect_challenge("<html>Access denied</html>", 403).detected


def test_clean_page_not_flagged():
    assert not detect_challenge("<html><body><h1>Welcome</h1><p>Content here</p></body></html>", 200).detected


def test_noscript_enable_javascript_not_flagged():
    """Most sites carry a <noscript>'please enable JavaScript' — not a challenge."""
    html = """<html><head><title>Great Products Shop</title></head><body>
    <noscript><p>Please enable JavaScript to get the best experience on our site
    with all the interactive features you love and enjoy</p></noscript>
    <h1>Products</h1><p>Lots and lots of real page content here which makes this
    clearly not a challenge page at all whatsoever in any way shape or form.</p>
    </body></html>"""
    assert not detect_challenge(html, 200).detected


def test_js_required_title_page_flagged():
    html = "<html><head><title>Please enable JavaScript</title></head><body></body></html>"
    info = detect_challenge(html, 200)
    assert info.detected and info.kind == "js_required"


# --- circuit breaker -----------------------------------------------------
def test_circuit_opens_on_failure_rate():
    cb = CircuitBreaker(window=10, failure_threshold=0.7, cooldown=60)
    for _ in range(2):
        cb.record_success("x.com")
    for _ in range(8):
        cb.record_failure("x.com")
    assert cb.check("x.com") == BreakerState.open


def test_circuit_stays_closed_when_healthy():
    cb = CircuitBreaker(window=10, failure_threshold=0.7)
    for _ in range(8):
        cb.record_success("ok.com")
    cb.record_failure("ok.com")
    assert cb.check("ok.com") == BreakerState.closed


def test_circuit_half_open_after_cooldown():
    cb = CircuitBreaker(window=5, failure_threshold=0.5, cooldown=0.01, cooldown_growth=1)
    for _ in range(3):
        cb.record_failure("y.com")
    assert cb.check("y.com") == BreakerState.open
    import time
    time.sleep(0.05)
    assert cb.check("y.com") == BreakerState.half_open
    cb.record_success("y.com")
    assert cb.check("y.com") == BreakerState.closed


# --- rate limiter ----------------------------------------------------------
def test_token_bucket_burst_then_throttle():
    bucket = TokenBucket(rate=10.0, capacity=2)
    assert bucket.take() and bucket.take()  # burst
    assert not bucket.take()                # exhausted
    assert 0 < bucket.wait_seconds <= 0.15


async def test_rate_limiter_acquire_returns_quickly_first_time():
    rl = RateLimiter(rate_per_domain=50, burst=2, jitter_ms=(1, 2))
    waited = await rl.acquire("z.com")
    assert waited >= 0.001  # jitter applied


async def test_rate_limiter_respects_crawl_delay():
    rl = RateLimiter(rate_per_domain=100, burst=1, jitter_ms=(1, 2))
    rl.set_crawl_delay("z.com", 0.05)  # 20 req/s cap
    await rl.acquire("z.com")
    bucket = rl._buckets["z.com"]
    assert bucket.rate == 20.0


# --- fingerprints ------------------------------------------------------------
def test_fingerprint_consistency():
    fp = generate_fingerprint()
    assert fp["user_agent"] and fp["platform"]
    assert fp["viewport_width"] <= fp["screen_width"]
    assert isinstance(fp["locales"], list) and fp["locales"]
    # UA/platform pairing
    if "Windows NT 10.0" in fp["user_agent"]:
        assert fp["platform"] == "Win32"
    if "Macintosh" in fp["user_agent"]:
        assert fp["platform"] == "MacIntel"


def test_default_fingerprint_valid():
    fp = default_fingerprint()
    assert "Mozilla/5.0" in fp["user_agent"]


def test_accept_language():
    assert "en-US" in accept_language_for("en-US")


# --- behavior math -------------------------------------------------------------
def test_bezier_endpoints():
    p0, p1, p2 = (0, 0), (50, 50), (100, 100)
    assert bezier_point(0.0, p0, p1, p2) == (0, 0)
    assert bezier_point(1.0, p0, p1, p2) == (100, 100)


def test_gaussian_delay_bounds():
    for _ in range(200):
        v = gaussian_delay_ms(1000, 500, 200, 2000)
        assert 200 <= v <= 2000
