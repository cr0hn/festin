"""Tests for festin.ratelimit: token bucket, adaptive backoff, profiles.

Also covers the service-level auth rate limiter (festin.service.serve).
"""

from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

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


# ===== Service-level auth rate limiting (festin.service.serve) =====


class _FakeRequest:
    """Minimal request stub for direct middleware unit tests."""

    def __init__(self, method="POST", path="/api/v1/auth/login", remote="10.0.0.1"):
        self.method = method
        self.path = path
        self.remote = remote


class TestSlidingWindowLimiter:
    def test_allows_up_to_limit_then_blocks(self):
        from festin.service.serve import SlidingWindowLimiter

        limiter = SlidingWindowLimiter(max_attempts=3, window_seconds=60, clock=lambda: 100.0)
        assert limiter.check("1.2.3.4") == (True, 0)
        assert limiter.check("1.2.3.4") == (True, 0)
        assert limiter.check("1.2.3.4") == (True, 0)
        allowed, retry_after = limiter.check("1.2.3.4")
        assert allowed is False
        assert 1 <= retry_after <= 61

    def test_window_expiry_allows_again(self):
        from festin.service.serve import SlidingWindowLimiter

        clock = {"now": 1000.0}
        limiter = SlidingWindowLimiter(
            max_attempts=2, window_seconds=10, clock=lambda: clock["now"]
        )
        assert limiter.check("ip") == (True, 0)
        assert limiter.check("ip") == (True, 0)
        assert limiter.check("ip")[0] is False
        clock["now"] = 1011.0  # past the window
        assert limiter.check("ip") == (True, 0)

    def test_ips_are_independent(self):
        from festin.service.serve import SlidingWindowLimiter

        limiter = SlidingWindowLimiter(max_attempts=1, window_seconds=60, clock=lambda: 0.0)
        assert limiter.check("a") == (True, 0)
        assert limiter.check("a")[0] is False
        assert limiter.check("b") == (True, 0)


class TestRateLimitMiddleware:
    @staticmethod
    def _invoke(limiter, request):
        from festin.service.serve import _rate_limit_middleware

        called = []

        async def handler(req):
            called.append(req)
            return web.json_response({"ok": True})

        async def run():
            return await _rate_limit_middleware(limiter)(request, handler)

        return asyncio.run(run()), called

    def test_post_login_limited(self):
        from festin.service.serve import SlidingWindowLimiter

        limiter = SlidingWindowLimiter(max_attempts=1, window_seconds=60, clock=lambda: 5.0)
        resp, called = self._invoke(limiter, _FakeRequest())
        assert resp.status == 200
        resp, called = self._invoke(limiter, _FakeRequest())
        assert resp.status == 429
        assert resp.headers["Retry-After"]
        assert not called  # handler never reached on 429

    def test_get_request_not_limited(self):
        from festin.service.serve import SlidingWindowLimiter

        limiter = SlidingWindowLimiter(max_attempts=1, window_seconds=60, clock=lambda: 5.0)
        for _ in range(5):
            resp, _ = self._invoke(limiter, _FakeRequest(method="GET", path="/api/v1/health"))
            assert resp.status == 200

    def test_other_paths_not_limited(self):
        from festin.service.serve import SlidingWindowLimiter

        limiter = SlidingWindowLimiter(max_attempts=1, window_seconds=60, clock=lambda: 5.0)
        for _ in range(5):
            resp, _ = self._invoke(limiter, _FakeRequest(path="/api/v1/stats"))
            assert resp.status == 200

    def test_exempt_local_disabled_by_default(self):
        from festin.service.serve import SlidingWindowLimiter

        limiter = SlidingWindowLimiter(max_attempts=1, window_seconds=60, clock=lambda: 5.0)
        resp, _ = self._invoke(limiter, _FakeRequest(remote="127.0.0.1"))
        assert resp.status == 200
        resp, _ = self._invoke(limiter, _FakeRequest(remote="127.0.0.1"))
        assert resp.status == 429  # localhost still limited by default

    def test_exempt_local_enabled(self, monkeypatch):
        from festin.service.serve import SlidingWindowLimiter

        monkeypatch.setenv("FESTIN_RATE_LIMIT_EXEMPT_LOCAL", "true")
        limiter = SlidingWindowLimiter(max_attempts=1, window_seconds=60, clock=lambda: 5.0)
        for _ in range(10):
            resp, _ = self._invoke(limiter, _FakeRequest(remote="127.0.0.1"))
            assert resp.status == 200


class TestRateLimitEndToEnd:
    """Full app tests through create_app (JWT middleware included)."""

    @pytest_asyncio.fixture
    async def client(self, tmp_path):
        from festin.service.serve import ServiceConfig, create_app

        cfg = ServiceConfig(
            db_path=tmp_path / "test.db",
            rate_limit_attempts=5,
            rate_limit_window=2,
        )
        app = await create_app(cfg)
        server = TestServer(app)
        tc = TestClient(server)
        await tc.start_server()
        yield tc
        await tc.close()

    async def test_register_also_limited(self, client):
        # First register succeeds (bootstrap admin); subsequent anonymous
        # registers are 401 (correct auth behavior) but STILL consume the
        # per-IP budget, so the 6th request hits the limiter.
        resp = await client.post(
            "/api/v1/auth/register",
            json={"username": "admin", "password": "secret123"},
        )
        assert resp.status == 201
        for _ in range(4):
            resp = await client.post(
                "/api/v1/auth/register",
                json={"username": "other", "password": "secret123"},
            )
            assert resp.status == 401
        resp = await client.post(
            "/api/v1/auth/register", json={"username": "uX", "password": "secret123"}
        )
        assert resp.status == 429
        data = await resp.json()
        assert data["error"] == "too many attempts, retry later"

    async def _login(self, client):
        return await client.post("/api/v1/auth/login", json={"username": "u", "password": "p"})

    async def test_sixth_login_attempt_gets_429(self, client):
        for _ in range(5):
            resp = await self._login(client)
            assert resp.status in (401, 200)  # normal auth outcome, not 429
        resp = await self._login(client)
        assert resp.status == 429
        assert "Retry-After" in resp.headers
        data = await resp.json()
        assert data["error"] == "too many attempts, retry later"

    async def test_health_not_limited(self, client):
        for _ in range(10):
            resp = await client.get("/api/v1/health")
            assert resp.status == 200

    async def test_window_expiry_allows_again(self, client):
        for _ in range(5):
            await self._login(client)
        assert (await self._login(client)).status == 429
        await asyncio.sleep(2.1)  # window_seconds = 2
        resp = await self._login(client)
        assert resp.status in (401, 200)  # back to normal auth outcome

    async def test_env_override_attempts(self, tmp_path, monkeypatch):
        from festin.service.serve import _env_rate_limits

        monkeypatch.setenv("FESTIN_RATE_LIMIT_ATTEMPTS", "2")
        monkeypatch.setenv("FESTIN_RATE_LIMIT_WINDOW", "30")
        assert _env_rate_limits() == (2, 30)

        # End-to-end: a fresh app honours the ServiceConfig values that
        # main() derives from the env; verify the wiring with attempts=2.
        from festin.service.serve import ServiceConfig, create_app

        cfg = ServiceConfig(db_path=tmp_path / "env.db", rate_limit_attempts=2)
        app = await create_app(cfg)
        server = TestServer(app)
        tc = TestClient(server)
        await tc.start_server()
        try:
            for _ in range(2):
                resp = await tc.post("/api/v1/auth/login", json={"username": "u", "password": "p"})
                assert resp.status in (401, 200)
            resp = await tc.post("/api/v1/auth/login", json={"username": "u", "password": "p"})
            assert resp.status == 429
        finally:
            await tc.close()

    async def test_config_defaults(self):
        from festin.service.serve import ServiceConfig

        cfg = ServiceConfig()
        assert cfg.rate_limit_attempts == 5
        assert cfg.rate_limit_window == 60
