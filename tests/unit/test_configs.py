"""Config integrity: knowledge-base YAMLs load and have required structure."""
from src.core.config import get_settings, load_settings, load_yaml


def test_default_settings_load():
    s = get_settings()
    assert s.get("engine.rendering") == "auto"
    assert s.get("politeness.requests_per_second_per_domain") == 1.0
    assert s.database_url.startswith("sqlite")


def test_settings_env_override():
    import os

    os.environ["RESPECT_ROBOTS"] = "false"
    try:
        s = load_settings()
        assert s.respect_robots is False
    finally:
        os.environ.pop("RESPECT_ROBOTS", None)


def test_known_popups_structure():
    cfg = load_yaml("known_popups.yaml")
    assert "cookie_banner" in cfg["containers"]
    assert any("#onetrust" not in s for s in cfg["containers"]["cookie_banner"])  # sanity
    assert cfg["dismiss_selectors"] and cfg["dismiss_texts"]
    assert "close" in [t.lower() for t in cfg["dismiss_texts"]]


def test_known_cookies_cmps():
    cfg = load_yaml("known_cookies.yaml")
    cmps = cfg["cmps"]
    for name in ("onetrust", "cookiebot", "didomi"):
        assert name in cmps, name
        assert cmps[name]["accept"], name
    assert cfg["strategies"]["accept_all"]["texts"]


def test_user_agents_profiles():
    cfg = load_yaml("user_agents.yaml")
    profiles = cfg["profiles"]
    assert len(profiles) >= 5
    for p in profiles:
        assert p["ua"].startswith("Mozilla/5.0")
        assert p["platform"] and p["os"] in ("windows", "macos", "linux")
        assert p["screens"] and p["timezone"]


def test_fingerprint_pools():
    cfg = load_yaml("fingerprints.yaml")
    assert cfg["webgl"]["vendors"]
    assert cfg["webgl"]["renderers"]["windows"]
    assert cfg["hardware"]["cpu_cores"]
