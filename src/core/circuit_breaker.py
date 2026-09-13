"""Per-domain circuit breaker (sd.txt "CIRCUIT BREAKER").

CLOSED -> failure rate >70% in last N requests -> OPEN (fail fast for cooldown)
-> HALF-OPEN (one test request) -> CLOSED / back to OPEN with longer cooldown."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from enum import Enum

import structlog

log = structlog.get_logger(__name__)


class BreakerState(str, Enum):
    closed = "closed"
    open = "open"
    half_open = "half_open"


class CircuitBreaker:
    def __init__(self, failure_threshold: float = 0.7, window: int = 10,
                 cooldown: float = 300.0, cooldown_growth: float = 2.0):
        self.failure_threshold = failure_threshold
        self.window = window
        self.cooldown = cooldown
        self.cooldown_growth = cooldown_growth
        self._events: dict[str, deque] = defaultdict(lambda: deque(maxlen=window))
        self._state: dict[str, BreakerState] = defaultdict(lambda: BreakerState.closed)
        self._opened_at: dict[str, float] = {}
        self._cooldown: dict[str, float] = defaultdict(lambda: cooldown)

    def check(self, domain: str) -> BreakerState:
        state = self._state[domain]
        if state == BreakerState.open:
            elapsed = time.time() - self._opened_at[domain]
            if elapsed >= self._cooldown[domain]:
                self._state[domain] = BreakerState.half_open
                log.info("circuit_half_open", domain=domain)
                return BreakerState.half_open
        return state

    def record_success(self, domain: str) -> None:
        self._events[domain].append(True)
        if self._state[domain] != BreakerState.closed:
            log.info("circuit_closed", domain=domain)
        self._state[domain] = BreakerState.closed
        self._cooldown[domain] = self.cooldown

    def record_failure(self, domain: str) -> None:
        events = self._events[domain]
        events.append(False)
        state = self._state[domain]
        if state == BreakerState.half_open:
            self._trip(domain)
            return
        if state == BreakerState.closed and len(events) >= 3:
            failures = sum(1 for ok in events if not ok)
            if failures / len(events) > self.failure_threshold:
                self._trip(domain)

    def _trip(self, domain: str) -> None:
        self._state[domain] = BreakerState.open
        self._opened_at[domain] = time.time()
        self._cooldown[domain] *= self.cooldown_growth
        log.warning("circuit_open", domain=domain,
                    cooldown=round(self._cooldown[domain], 1))
