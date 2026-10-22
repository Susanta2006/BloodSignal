"""Circuit breaker + bounded retry for the source adapter."""
from __future__ import annotations

import asyncio
import random
import time
from typing import Awaitable, Callable, TypeVar

from app.services.stock_source import SourceError, SourceNotConfigured, SourceUnavailable

T = TypeVar("T")


class CircuitBreaker:
    """Opens after `threshold` consecutive failures; after `cooldown_s` lets ONE trial call through."""

    def __init__(self, threshold: int, cooldown_s: float, clock: Callable[[], float] = time.monotonic):
        self._threshold, self._cooldown, self._clock = threshold, cooldown_s, clock
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_out = False
        self.last_error: SourceError | None = None

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        return "half_open" if self._clock() - self._opened_at >= self._cooldown else "open"

    def allow(self) -> bool:
        s = self.state
        if s == "closed":
            return True
        if s == "half_open" and not self._trial_out:
            self._trial_out = True
            return True
        return False

    def success(self) -> None:
        self._failures, self._opened_at, self._trial_out, self.last_error = 0, None, False, None

    def failure(self, err: SourceError) -> None:
        self.last_error, self._trial_out = err, False
        self._failures += 1
        if self._failures >= self._threshold or self._opened_at is not None:
            self._opened_at = self._clock()


async def call_with_retry(fn: Callable[[], Awaitable[T]], *, breaker: CircuitBreaker, attempts: int,
                          base_delay_s: float, timeout_s: float,
                          sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                          rng: Callable[[], float] = random.random) -> T:
    """Retries ONLY retryable errors (timeouts/network/5xx). Auth, rate-limit, malformed and
    rejected-request errors are never retried. Every outcome feeds the breaker."""
    if not breaker.allow():
        cls = type(breaker.last_error) if breaker.last_error else SourceUnavailable
        raise cls("circuit open: source calls suspended after repeated failures")
    last: SourceError | None = None
    for i in range(attempts):
        try:
            out = await asyncio.wait_for(fn(), timeout=timeout_s)
            breaker.success()
            return out
        except asyncio.TimeoutError:
            last = SourceUnavailable("source call timed out")
        except SourceNotConfigured:
            raise                                   # a config state, not a source failure
        except SourceError as e:
            last = e
            if not e.retryable:
                break
        if i < attempts - 1:
            await sleep(base_delay_s * (2 ** i) * (0.5 + rng()))     # exponential backoff + jitter
    assert last is not None
    breaker.failure(last)
    raise last
