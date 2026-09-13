"""Item deduplication across pages (see sd.txt "DEDUPLICATION").

Hashes items on stable fields; detects pagination loops (page 1 repeating)."""
from __future__ import annotations

import hashlib
import json
from typing import Any

_STABLE_KEYS = ("url", "href", "id", "sku", "link", "title", "name", "price")


def item_hash(item: dict[str, Any]) -> str:
    stable = {k: str(v).strip().lower() for k, v in item.items()
              if k in _STABLE_KEYS and v not in (None, "")}
    payload = stable if stable else {k: str(v) for k, v in item.items() if v not in (None, "")}
    return hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Deduplicator:
    def __init__(self):
        self.seen: set[str] = set()
        self.duplicates: int = 0

    def filter(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        fresh: list[dict[str, Any]] = []
        for item in items:
            h = item_hash(item)
            if h in self.seen:
                self.duplicates += 1
                continue
            self.seen.add(h)
            fresh.append(item)
        return fresh

    @property
    def total_seen(self) -> int:
        return len(self.seen)
