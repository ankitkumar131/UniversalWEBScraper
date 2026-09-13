"""Data-pipeline deduplication (final pass after all pages collected)."""
from __future__ import annotations

from typing import Any

from src.pagination.deduplicator import item_hash


class DataDeduplicator:
    """Cross-page + cross-run dedupe; keeps first occurrence, counts repeats."""

    def __init__(self, known_hashes: set[str] | None = None):
        self.seen: set[str] = known_hashes or set()
        self.duplicates: int = 0

    def process(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in items:
            h = item_hash(item)
            if h in self.seen:
                self.duplicates += 1
                continue
            self.seen.add(h)
            out.append(item)
        return out
