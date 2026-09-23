"""Unit tests for the circuit breaker (fake clock, no network)."""

import pytest

from src.claimbridge.resilience import CircuitBreaker, CircuitOpen


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def boom():
    raise TimeoutError("down")


def test_opens_after_threshold_then_fails_fast():
    clock = Clock()
    b = CircuitBreaker("x", failure_threshold=3, reset_timeout=30, clock=clock)
    for _ in range(3):
        with pytest.raises(TimeoutError):
            b.call(boom)
    assert b.state == "open"
    calls = []
    with pytest.raises(CircuitOpen):
        b.call(lambda: calls.append(1))
    assert calls == []                     # dependency not touched while open


def test_half_open_trial_success_closes():
    clock = Clock()
    b = CircuitBreaker("x", failure_threshold=1, reset_timeout=30, clock=clock)
    with pytest.raises(TimeoutError):
        b.call(boom)
    clock.t = 31
    assert b.state == "half_open"
    assert b.call(lambda: "ok") == "ok"
    assert b.state == "closed"


def test_half_open_trial_failure_reopens():
    clock = Clock()
    b = CircuitBreaker("x", failure_threshold=1, reset_timeout=30, clock=clock)
    with pytest.raises(TimeoutError):
        b.call(boom)
    clock.t = 31
    with pytest.raises(TimeoutError):
        b.call(boom)
    assert b.state == "open"


def test_success_resets_failure_count():
    b = CircuitBreaker("x", failure_threshold=2, reset_timeout=30, clock=Clock())
    with pytest.raises(TimeoutError):
        b.call(boom)
    b.call(lambda: None)
    with pytest.raises(TimeoutError):
        b.call(boom)
    assert b.state == "closed"


def test_only_listed_failures_count():
    b = CircuitBreaker("x", failure_threshold=1, reset_timeout=30, clock=Clock())
    with pytest.raises(ValueError):
        b.call(lambda: (_ for _ in ()).throw(ValueError("bad input")), failure_types=(TimeoutError,))
    assert b.state == "closed"
