"""Rate limiting primitives and profile presets.

Provides a monotonic token-bucket limiter, an adaptive backoff controller
that reacts to throttling responses (429/403/5xx), and CLI profile presets
(fast/deep/stealth) applied onto an ``argparse.Namespace``.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import time
from typing import Any

BACKOFF_FACTOR = 2.0
BACKOFF_DECAY = 1.5
MAX_BACKOFF = 60.0

PROFILES: dict[str, dict[str, Any]] = {
    "fast": {"concurrency": 20, "rate": 50.0},
    "deep": {"concurrency": 10, "rate": 10.0},
    "stealth": {"concurrency": 2, "rate": 1.0, "jitter": True},
}


class AsyncRateLimiter:
    """Token-bucket rate limiter driven by ``time.monotonic``.

    Allows an initial burst up to ``max_per_second`` operations, then
    throttles to that steady-state rate. When ``jitter`` is positive every
    wait is inflated by a random uniform fraction of itself, which avoids
    synchronized retry storms across concurrent workers.
    """

    def __init__(self, max_per_second: float, jitter: float = 0.0) -> None:
        if max_per_second <= 0:
            raise ValueError("max_per_second must be positive")
        if jitter < 0:
            raise ValueError("jitter must be >= 0")
        self.rate = float(max_per_second)
        self.jitter = float(jitter)
        self.capacity = float(max_per_second)
        self._tokens = self.capacity
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._last_refill) * self.rate)
        self._last_refill = now

    def _wait_time(self, scale: float) -> float:
        self._refill()
        if self._tokens >= 1.0:
            return 0.0
        wait = (1.0 - self._tokens) / self.rate * scale
        if self.jitter > 0.0:
            wait *= 1.0 + random.uniform(0.0, self.jitter)
        return wait

    async def acquire(self, scale: float = 1.0) -> None:
        """Wait until one token is available; ``scale`` multiplies the wait."""
        while True:
            wait = self._wait_time(scale)
            if wait <= 0.0:
                self._tokens -= 1.0
                return
            await asyncio.sleep(wait)


class AdaptiveController:
    """Wraps a limiter with request-outcome driven backoff.

    ``record_error`` on 429/403/5xx multiplies the effective delay by
    ``BACKOFF_FACTOR`` (capped at ``MAX_BACKOFF``); ``record_success`` decays
    it back toward the base by ``BACKOFF_DECAY``. Single event loop only.
    """

    def __init__(self, limiter: AsyncRateLimiter) -> None:
        self.limiter = limiter
        self._backoff = 1.0

    @property
    def backoff(self) -> float:
        """Current delay multiplier applied on top of the base limiter wait."""
        return self._backoff

    @property
    def throttled(self) -> bool:
        """True while the effective delay is inflated above the base."""
        return self._backoff > 1.0

    def record_error(self, status: int | None) -> None:
        if status is None or status in (429, 403) or 500 <= status <= 599:
            self._backoff = min(MAX_BACKOFF, self._backoff * BACKOFF_FACTOR)

    def record_success(self) -> None:
        self._backoff = max(1.0, self._backoff / BACKOFF_DECAY)

    async def acquire(self) -> None:
        await self.limiter.acquire(scale=self._backoff)


def get_profile(name: str) -> dict:
    """Return a copy of the named profile preset; ValueError on unknown."""
    profile = PROFILES.get(name)
    if profile is None:
        known = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown profile {name!r}; available profiles: {known}")
    return dict(profile)


def apply_profile(cli_args: argparse.Namespace, name: str) -> None:
    """Mutate ``cli_args`` in place with the named profile preset.

    Sets ``concurrency`` and ``rate``; jitter profiles also set
    ``rate_jitter`` to 0.3 (0.0 otherwise). Unknown profile -> ValueError.
    """
    profile = get_profile(name)

    cli_args.concurrency = profile["concurrency"]
    cli_args.rate = profile["rate"]
    cli_args.rate_jitter = 0.3 if profile.get("jitter") else 0.0


__all__ = (
    "BACKOFF_DECAY",
    "BACKOFF_FACTOR",
    "MAX_BACKOFF",
    "PROFILES",
    "AdaptiveController",
    "AsyncRateLimiter",
    "apply_profile",
    "get_profile",
)
