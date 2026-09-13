"""Data cleaning & normalization: whitespace, entities, relative URL
resolution, best-effort type coercion (numbers / dates -> ISO)."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

_WS = re.compile(r"\s+")
_NUM = re.compile(r"^-?[\d.,]+\s*(?:%|k|m|bn|billion|million)?$|^[+-]?\d+(\.\d+)?$", re.I)
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d %B %Y", "%B %d, %Y", "%d %b %Y",
                 "%b %d, %Y", "%Y/%m/%d %H:%M", "%d.%m.%Y")


def clean_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return _WS.sub(" ", value).strip()


def resolve_url(value: str, base_url: str) -> str:
    return urljoin(base_url, value)


def coerce(value: str) -> Any:
    """Convert number-like strings to numbers; date-like strings to ISO dates.
    Leading zeros (e.g. '007', zip codes) stay strings."""
    v = value.strip()
    digits = v.lstrip("+-")
    if re.fullmatch(r"[+-]?\d+", v) and not (len(digits) > 1 and digits.startswith("0")):
        return int(v)
    if re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[+-]?\d+\.\d+", v):
        return float(v.replace(",", ""))
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            continue
    return v


_URL_KEYS = {"url", "link", "href", "image", "img", "thumbnail", "src", "logo"}
_PRICE_KEYS = {"price", "amount", "cost", "value"}


class Transformer:
    def process(self, items: list[dict[str, Any]], base_url: str) -> list[dict[str, Any]]:
        return [self._transform(item, base_url) for item in items]

    def _transform(self, item: dict[str, Any], base_url: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in item.items():
            key_l = key.lower().strip()
            if isinstance(value, str):
                value = clean_text(value)
                if not value:
                    continue
                if key_l in _URL_KEYS or key_l.endswith("_url") or key_l.endswith("_link"):
                    value = resolve_url(value, base_url)
                elif key_l in _PRICE_KEYS or key_l.endswith("_price"):
                    value = self._price(value)
                else:
                    value = coerce(value)
            elif isinstance(value, dict):
                value = {k: clean_text(v) if isinstance(v, str) else v for k, v in value.items()}
            out[key] = value
        return out

    @staticmethod
    def _price(value: str) -> Any:
        match = re.search(r"\d[\d.,]*", value.replace("\xa0", " ").replace(" ", ""))
        if not match:
            return value
        raw = match.group(0).rstrip(".,")
        if "," in raw and "." in raw:
            raw = raw.replace(",", "")            # 1,299.50 -> 1299.50
        elif "," in raw:
            head, _, tail = raw.rpartition(",")
            raw = f"{head}.{tail}" if len(tail) in (1, 2) else raw.replace(",", "")
        try:
            return float(raw)
        except ValueError:
            return value
