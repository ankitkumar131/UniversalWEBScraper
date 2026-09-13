"""Metadata enrichment: provenance (source URL, timestamp, job id, domain)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

_META_PREFIX = "_"


class Enricher:
    def process(self, items: list[dict[str, Any]], *, job_id: str, page_url: str,
                page_number: int, strategy: str) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        domain = urlparse(page_url).netloc
        for item in items:
            item[f"{_META_PREFIX}source_url"] = page_url
            item[f"{_META_PREFIX}domain"] = domain
            item[f"{_META_PREFIX}scraped_at"] = now
            item[f"{_META_PREFIX}job_id"] = job_id
            item[f"{_META_PREFIX}page"] = page_number
            item.setdefault(f"{_META_PREFIX}extracted_with", strategy)
        return items
