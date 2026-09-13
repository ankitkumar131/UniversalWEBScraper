"""Pagination strategy interface.

A strategy is an async iterator driver: the engine calls `advance()` after each
extraction; it returns True when it moved to a new page. Strategies receive the
live Playwright page (browser mode) — except UrlPatternStrategy which also
works in lightweight (httpx) mode.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AdvanceResult:
    changed: bool = False
    new_url: str | None = None
    detail: str = ""


@dataclass
class StrategyContext:
    page: Any = None                  # playwright page or None in lightweight mode
    current_url: str = ""
    items_so_far: int = 0
    page_number: int = 1
    html_getter: Any = None           # async () -> str, lightweight mode refetch helper
    meta: dict[str, Any] = field(default_factory=dict)


class PaginationStrategy(ABC):
    name: str = "base"

    def __init__(self, config: Any):
        self.config = config

    @abstractmethod
    async def advance(self, ctx: StrategyContext) -> AdvanceResult:
        """Move to the next page. Returns changed=False when there is no next page."""

    async def cleanup(self) -> None:
        pass
