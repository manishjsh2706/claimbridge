"""
Circuit breaker - ClaimBridge (Iteration 2)
===========================================

Wraps the two remote dependencies of summary generation: the LLM and the
Weaviate policy search.

    CLOSED     calls go through; consecutive failures are counted
    OPEN       after `failure_threshold` failures in a row: calls fail
               immediately (no network wait) for `reset_timeout` seconds
    HALF_OPEN  after the timeout, one trial call is let through; success
               closes the circuit, failure opens it again

WHY
Without it, an OpenAI outage makes every request wait for the full timeout
and retries (~30s+) before falling back to the template. With it, after 3
failures the next requests get the grounded template in milliseconds, the
dependency gets breathing room, and /health reports "open" so monitoring sees
it. The fallback paths already existed (template summary, no-policy
retrieval); the breaker just makes reaching them fast.

Process-local on purpose: each API worker protects itself. A shared breaker
(Redis) is the next step when there are many workers.
"""

import threading
import time
from typing import Any, Callable, Dict, Tuple, Type


class CircuitOpen(Exception):
    """Raised instead of calling a dependency whose circuit is open."""


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 3, reset_timeout: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at = None
        self._trial_in_flight = False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state_unlocked()

    def _state_unlocked(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.reset_timeout:
            return "half_open"
        return "open"

    def call(self, fn: Callable, *args, failure_types: Tuple[Type[BaseException], ...] = (Exception,), **kwargs) -> Any:
        with self._lock:
            state = self._state_unlocked()
            if state == "open" or (state == "half_open" and self._trial_in_flight):
                raise CircuitOpen(f"{self.name} circuit is open (after {self._failures} consecutive failures)")
            if state == "half_open":
                self._trial_in_flight = True
        try:
            result = fn(*args, **kwargs)
        except failure_types:
            with self._lock:
                self._failures += 1
                self._trial_in_flight = False
                if self._failures >= self.failure_threshold or self._opened_at is not None:
                    self._opened_at = self._clock()
            raise
        with self._lock:
            self._failures, self._opened_at, self._trial_in_flight = 0, None, False
        return result

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {"state": self._state_unlocked(), "consecutive_failures": self._failures}


LLM_BREAKER = CircuitBreaker("llm", failure_threshold=3, reset_timeout=30.0)
VECTOR_BREAKER = CircuitBreaker("vector_store", failure_threshold=3, reset_timeout=30.0)


def breaker_states() -> Dict[str, Dict[str, Any]]:
    return {b.name: b.snapshot() for b in (LLM_BREAKER, VECTOR_BREAKER)}
