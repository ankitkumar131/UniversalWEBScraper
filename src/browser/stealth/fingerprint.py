"""Fingerprint generation: consistent identity profiles from curated pools.

A profile pairs a user-agent string with matching platform, screen, locale,
timezone, WebGL strings, and hardware hints — all sampled together so the
fingerprint is internally consistent (see config/user_agents.yaml and
config/fingerprints.yaml)."""
from __future__ import annotations

import random
from typing import Any

from src.core.config import load_yaml


def _load_profiles() -> list[dict[str, Any]]:
    data = load_yaml("user_agents.yaml")
    return data.get("profiles", [])


def _load_fp_pools() -> dict[str, Any]:
    return load_yaml("fingerprints.yaml")


def generate_fingerprint(*, country_hint: str | None = None,
                         profiles: list[dict] | None = None,
                         pools: dict[str, Any] | None = None) -> dict[str, Any]:
    profiles = profiles or _load_profiles()
    pools = pools or _load_fp_pools()
    if not profiles:
        return default_fingerprint()

    profile = random.choice(profiles)
    width, height = random.choice(profile.get("screens", [[1920, 1080]]))

    webgl_renderers = (pools.get("webgl", {}).get("renderers", {}).get(profile.get("os"), [])
                       or pools.get("webgl", {}).get("renderers", {}).get("windows", []))
    hw = pools.get("hardware", {})
    locale = profile.get("locales", ["en-US"])[0]

    return {
        "user_agent": profile["ua"],
        "platform": profile.get("platform", "Win32"),
        "os": profile.get("os", "windows"),
        "screen_width": width,
        "screen_height": height,
        "viewport_width": min(width, 1440),
        "viewport_height": min(height - 80, 900),
        "locale": locale,
        "locales": profile.get("locales", ["en-US", "en"]),
        "timezone": profile.get("timezone", "America/New_York"),
        "webgl_vendor": random.choice(pools.get("webgl", {}).get("vendors", ["Intel Inc."])),
        "webgl_renderer": random.choice(webgl_renderers) if webgl_renderers else "Intel(R) UHD Graphics 630",
        "device_memory_gb": random.choice(hw.get("device_memory_gb", [8])),
        "cpu_cores": random.choice(hw.get("cpu_cores", [8])),
        "canvas_noise": (pools.get("canvas_noise", {}) or {}).get("amplitude", 0.02),
        "country_hint": country_hint,
    }


def default_fingerprint() -> dict[str, Any]:
    return {
        "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
        "platform": "Win32", "os": "windows",
        "screen_width": 1920, "screen_height": 1080,
        "viewport_width": 1440, "viewport_height": 900,
        "locale": "en-US", "locales": ["en-US", "en"],
        "timezone": "America/New_York",
        "webgl_vendor": "Intel Inc.",
        "webgl_renderer": "Intel(R) UHD Graphics 630",
        "device_memory_gb": 8, "cpu_cores": 8, "canvas_noise": 0.02,
        "country_hint": None,
    }


def accept_language_for(locale: str, pools: dict[str, Any] | None = None) -> str:
    pools = pools or _load_fp_pools()
    return (pools.get("locales", {}) or {}).get(locale, "en-US,en;q=0.9")
