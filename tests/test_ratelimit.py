"""Tests for festin.ratelimit: token bucket, adaptive backoff, profiles."""

import asyncio
import time

import pytest

from festin.ratelimit import (
    BACKOFF_FACTOR,
    MAX_BACKOFF,
    PROFILES,
    AdaptiveController,
    AsyncRateLimiter,
    apply_profile,
)

make_cli_args = None  # conftest exposes the fixture via argument injection


@pytest.fixture
def args(cli_args):
    return cli_args


class TestAsyncRateLimiter:
    async def test_burst_allowed_then_throttles(self):
        # rate 20/s, capacity 20: burst of 20 is instant, 21st must wait.
        limiter = AsyncRateLimiter(max_per_second=20.0)
        start = time.monotonic()
        for _ in range(20):
            await limiter.acquire()
        assert time.monotonic() - start < 0.25

        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.03  # waited ~1/20s for a token
        assert elapsed < 0.5

    async def test_steady_state_rate_respected(self):
        limiter = AsyncRateLimiter(max_per_second=50.0, jitter=0.0)
        start = time.monotonic()
        for _ in range(50):
            await limiter.acquire()
        elapsed = time.monotonic() - start
        # Full burst: first 50 free, next 50 take ~1s total; be lenient.
        assert elapsed < 2.0

    async def test_jitter_inflates_wait(self):
        limiter = AsyncRateLimiter(max_per_second=20.0, jitter=0.5)
        for _ in range(20):
            await limiter.acquire()
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        # Base wait ~0.05s; with jitter it may be up to 1.5x, and random.
        assert elapsed >= 0.04
        assert elapsed < 0.5

    def test_invalid_params(self):
        with pytest.raises(ValueError):
            AsyncRateLimiter(max_per_second=0.0)
        with pytest.raises(ValueError):
            AsyncRateLimiter(max_per_second=10.0, jitter=-1.0)

    async def test_refill_recovers_tokens(self):
        limiter = AsyncRateLimiter(max_per_second=100.0, jitter=0.0)
        for _ in range(100):
            await limiter.acquire()
        # drain then let tokens recover
        await limiter.acquire()
        await asyncio.sleep(0.15)
        start = time.monotonic()
        for _ in range(10):
            assert time.monotonic() - start < 0.05


class TestAdaptiveController:
    def test_backoff_grows_on_429(self):
        limiter = AsyncRateLimiter(max_per_second=10.0)
        controller = AdaptiveController(limiter)
        controller.record_error(429)
        assert controller.backoff == pytest.approx(BACKOFF_FACTOR)

    def test_backoff_ignores_2xx_and_4xx(self):
        controller = AdaptiveController(AsyncRateLimiter(max_per_second=10.0))
        for status in (200, 201, 301, 404):
            controller.record_error(status)
        assert not controller.throttled

    def test_backoff_on_none_status_treated_as_failure(self):
        controller = AdaptiveController(AsyncRateLimiter(max_per_second=10.0))
        controller.record_error(None)
        assert controller.throttled

    def test_backoff_grows_on_403_and_5xx(self):
        controller = AdaptiveController(AsyncRateLimiter(max_per_second=10.0))
        for status in (403, 500, 503):
            controller.record_error(status)
            assert controller.throttled

    def test_backoff_capped_at_max(self):
        controller = AdaptiveController(AsyncRateLimiter(max_per_second=10.0))
        for _ in range(20):
            controller.record_error(429)
        assert controller.backoff == MAX_BACKOFF

    def test_backoff_decays_on_success(self):
        controller = AdaptiveController(AsyncRateLimiter(max_per_second=10.0))
        controller.record_error(429)
        controller.record_success()
        assert controller.backoff == pytest.approx(BACKOFF_FACTOR / 1.5)
        controller.record_success()
        assert controller.backoff == pytest.approx(1.0)

    async def test_acquire_applies_backoff_scale(self):
        limiter = AsyncRateLimiter(max_per_second=20.0, jitter=0.0)
        controller = AdaptiveController(limiter)
        controller.record_error(429)
        for _ in range(20):
            await limiter.acquire()
        start = time.monotonic()
        await controller.acquire()
        elapsed = time.monotonic() - start
        # base wait 1/20s scaled by 2.0 -> ~0.1s
        assert elapsed >= 0.08

    async def test_acquire_no_backoff_when_healthy(self):
        limiter = AsyncRateLimiter(max_per_second=20.0, jitter=0.0)
        controller = AdaptiveController(limiter)
        for _ in range(20):
            await controller.acquire()
        start = time.monotonic()
        await controller.acquire()
        assert time.monotonic() - start < 0.08


class TestProfiles:
    def test_fast_profile(self, args):
        apply_profile(args, "fast")
        assert args.concurrency == PROFILES["fast"]["concurrency"]
        assert args.rate == PROFILES["fast"]["rate"]
        assert args.rate_jitter == 0.0

    def test_deep_profile(self, args):
        apply_profile(args, "deep")
        assert args.concurrency == 10
        assert args.rate == 10.0
        assert args.rate_jitter == 0.0

    def test_stealth_profile_sets_jitter(self, args):
        apply_profile(args, "stealth")
        assert args.concurrency == 2
        assert args.rate == 1.0
        assert args.rate_jitter == 0.3

    def test_unknown_profile_raises(self, args):
        with pytest.raises(ValueError, match="Unknown profile"):
            apply_profile(args, "nope")

    def test_profile_is_idempotent(self, args):
        apply_profile(args, "stealth")
        first = (args.concurrency, args.rate, args.rate_jitter)
        apply_profile(args, "stealth")
        assert (args.concurrency, args.rate, args.rate_jitter) == first
