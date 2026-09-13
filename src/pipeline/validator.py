"""Schema validation: required fields, empty-item filtering."""
from __future__ import annotations

from typing import Any


class Validator:
    def __init__(self, required_fields: list[str] | None = None, min_fields: int = 1):
        self.required_fields = required_fields or []
        self.min_fields = min_fields

    def process(self, items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        errors: list[str] = []
        valid: list[dict[str, Any]] = []
        for i, item in enumerate(items):
            non_empty = {k: v for k, v in item.items() if v not in (None, "", [], {})}
            if len(non_empty) < self.min_fields:
                errors.append(f"item {i}: empty")
                continue
            missing = [f for f in self.required_fields if not non_empty.get(f)]
            if missing:
                errors.append(f"item {i}: missing required {missing}")
                continue
            valid.append(non_empty)
        return valid, errors
