"""Generic proxy provider: loads proxies from env (PROXY_LIST), a file
(PROXY_LIST_FILE), or an explicit list. Works with any provider (BrightData,
Oxylabs, SmartProxy, ...) — they all expose http(s) proxy URLs."""
from __future__ import annotations

from pathlib import Path


def load_proxies(explicit: list[str] | None = None, list_file: str | None = None) -> list[str]:
    proxies: list[str] = list(explicit or [])
    if list_file and Path(list_file).exists():
        proxies.extend(
            line.strip() for line in Path(list_file).read_text().splitlines()
            if line.strip() and not line.startswith("#")
        )
    seen, unique = set(), []
    for p in proxies:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def classify_tier(proxy_url: str) -> str:
    """Best-effort tier classification from URL structure."""
    lowered = proxy_url.lower()
    if any(k in lowered for k in ("mobile", "lte", "gsm")):
        return "mobile"
    if any(k in lowered for k in ("residential", "rlp", "peer")):
        return "residential"
    if any(k in lowered for k in ("isp", "static-res")):
        return "isp"
    return "datacenter"
