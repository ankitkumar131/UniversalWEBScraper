"""Extractor base classes. Extraction operates on PageContent (browser-independent),
so every extractor is unit-testable with plain HTML."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from src.core.schemas import PageContent


@dataclass
class ExtractionResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    # single-record extraction (page-level fields), merged into each item
    page_fields: dict[str, Any] = field(default_factory=dict)
    strategy: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.items) or bool(self.page_fields)


class BaseExtractor(ABC):
    name: str = "base"

    def __init__(self, config: Any | None = None):
        self.config = config

    @abstractmethod
    async def extract(self, content: PageContent) -> ExtractionResult:
        """Extract items/fields from a loaded page."""
