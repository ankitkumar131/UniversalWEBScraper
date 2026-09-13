"""Generic API response models."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str
    components: dict[str, Any]


class MessageResponse(BaseModel):
    message: str
    job_id: str | None = None


class DomainInfoResponse(BaseModel):
    domain: str
    difficulty: str
    success_count: int
    failure_count: int
    challenge_count: int
    avg_duration_seconds: float
