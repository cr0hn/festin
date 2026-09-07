"""Festin service application factory and server launcher."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from aiohttp import web

from ..models import ScanResult

logger = logging.getLogger("festin.service.serve")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


class ServiceConfig:
    """Configuration for the FestIn service."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8420,
        db_path: Path | None = None,
        static_dir: Path | None = None,
        state_file: Path | None = None,
        auth_users_file: Path | None = None,
        scan_callback: Callable[[list[str]], Awaitable[ScanResult]] | None = None,
        rate_limit_attempts: int = 5,
        rate_limit_window: int = 60,
    ) -> None:
        self.host = host
        self.port = port
        self.db_path = db_path or Path("data", "festin.db")
        self.static_dir = static_dir or (Path(__file__).resolve().parent / "static")
        self.state_file = state_file
        self.auth_users_file = auth_users_file
        self.scan_callback = scan_callback
        self.rate_limit_attempts = rate_limit_attempts
        self.rate_limit_window = rate_limit_window


# -- In-app rate limiting for auth endpoints (login + register). --

_RATE_LIMITED_PATHS = {"/api/v1/auth/login", "/api/v1/auth/register"}


class SlidingWindowLimiter:
    """Per-IP sliding window counter for auth endpoints.

    Timestamps are ``time.monotonic()`` values stored per client IP in a
    plain dict of deques — no external dependencies, no locks needed
    (single event loop). Time is injected so tests can freeze it.
    """

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._clock = clock
        self._attempts: dict[str, deque[float]] = {}

    def _prune(self, ip: str, now: float) -> None:
        """Drop timestamps outside the window for this IP."""
        window = self._attempts.get(ip)
        if window is None:
            return
        cutoff = now - self.window_seconds
        while window and window[0] <= cutoff:
            window.popleft()

    def check(self, ip: str) -> tuple[bool, int]:
        """Return ``(allowed, seconds_until_next_slot)`` for this IP."""
        now = self._clock()
        self._prune(ip, now)
        window = self._attempts.setdefault(ip, deque())
        if len(window) >= self.max_attempts:
            oldest = window[0]
            retry_after = max(1, int(self.window_seconds - (now - oldest)) + 1)
            return False, retry_after
        window.append(now)
        return True, 0


def _client_ip(request: web.Request) -> str:
    return request.remote or "unknown"


def _is_exempt_ip(ip: str) -> bool:
    """Local loopback is exempt only when explicitly enabled via env."""
    return (
        os.environ.get("FESTIN_RATE_LIMIT_EXEMPT_LOCAL", "").lower() == "true"
        and ip in ("127.0.0.1", "::1")
    )


def _rate_limit_middleware(limiter: SlidingWindowLimiter) -> Any:
    """Wrap the limiter in a new-style aiohttp middleware (see
    ``_wrap_middleware`` below: aiohttp 3.14 does not dispatch instance
    middlewares without this pattern)."""

    @web.middleware
    async def _rate_limiter(request: web.Request, handler: Any) -> web.StreamResponse:
        if request.method != "POST" or request.path not in _RATE_LIMITED_PATHS:
            return await handler(request)
        ip = _client_ip(request)
        if _is_exempt_ip(ip):
            return await handler(request)
        allowed, retry_after = limiter.check(ip)
        if not allowed:
            return web.json_response(
                {"error": "too many attempts, retry later"},
                status=429,
                headers={"Retry-After": str(retry_after)},
            )
        return await handler(request)

    return _rate_limiter



async def create_app(config: ServiceConfig | None = None) -> web.Application:
    """Create the FestIn monitoring dashboard aiohttp application."""
    from .database import Database
    from .queues import QueueManager
    from .router import FestinRouter
    from .scheduler import Scheduler

    config = config or ServiceConfig()
    # Ensure DB directory exists
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(config.db_path)
    queue_mgr = QueueManager()
    scheduler = Scheduler(database=db)

    # -- Auth middleware: JWT when available (festin.service.auth), falling
    #    back to the legacy AuthMiddleware so startup never blocks. --
    # Paths that must be reachable without a token: the SPA itself, its
    # static assets, and the auth bootstrap endpoints.
    _exempt_paths = {
        "/",
        "/api/v1/auth/login",
        "/api/v1/auth/register",  # first-user bootstrap; admin case in handler
        "/api/v1/health",
    }
    _exempt_prefixes = ("/static",)

    # Both middlewares are instances whose ``__call__`` is decorated with
    # ``@web.middleware``. aiohttp >= 3.9 only honors the new-style contract
    # when the marker sits on the *instance*, so wrap it in a plain
    # new-style middleware function here.
    def _wrap_middleware(instance: Any) -> Any:
        @web.middleware
        async def _mw(request: web.Request, handler: Any) -> web.StreamResponse:
            path = request.path
            if path.startswith(_exempt_prefixes):
                return await handler(request)
            if path in _exempt_paths and path != "/api/v1/auth/register":
                return await handler(request)
            if path == "/api/v1/auth/register":
                # Verify the token if present so the handler can tell an
                # admin apart; but let anonymous requests through too —
                # the first-user bootstrap decides there.
                auth_header = request.headers.get("Authorization", "")
                if auth_header:
                    return await instance.__call__(request, handler)
                return await handler(request)
            return await instance.__call__(request, handler)

        return _mw

    middlewares: list[Any] = []
    secret = os.environ.get("FESTIN_JWT_SECRET", "festin-secret-key-change-in-production")
    try:
        from .auth import AuthService, JWTMiddleware

        auth_service = AuthService(db, secret_key=secret)
        # register NOT exempt: middleware identifies the admin Bearer so the
        # handler can authorize; anonymous bootstrap passes through via the
        # no-Authorization path inside JWTMiddleware.
        jwt_mw = JWTMiddleware.from_secret(
            secret, exempt_paths=set(_exempt_paths) - {"/api/v1/auth/register"}
        )
        middlewares.append(_wrap_middleware(jwt_mw))
        logger.info("JWT authentication middleware enabled")
    except ImportError:
        auth_service = None
        from .auth import AuthMiddleware

        if config.auth_users_file is not None and config.auth_users_file.exists():
            auth_mw = AuthMiddleware.from_file(config.auth_users_file)
        else:
            auth_mw = AuthMiddleware()  # No auth by default
        middlewares.append(_wrap_middleware(auth_mw))
        logger.info("JWT middleware unavailable; using legacy AuthMiddleware")

    # -- scan_callback intentionally None by default: the router's run-scan
    #    handler persists the scan record in the DB and executes it through
    #    the scheduler branch. A custom callback (config.scan_callback)
    #    bypasses that path entirely. --

    # -- Router setup --
    app = web.Application(middlewares=middlewares)
    app["db"] = db
    app["scheduler"] = scheduler
    app["queues"] = queue_mgr
    router = FestinRouter(
        database=db,
        queue_manager=queue_mgr,
        scheduler=scheduler,
        scan_callback=config.scan_callback,
        auth=auth_service,
    )
    router.add_routes(app)

    # -- Root route: serve the SPA entry point --
    async def _index(request: web.Request) -> web.StreamResponse:
        index = config.static_dir / "index.html"
        if index.exists():
            resp = web.FileResponse(index)
            resp.headers["Cache-Control"] = "no-store, must-revalidate"
            return resp
        return web.json_response({"error": "SPA not built"}, status=404)

    app.router.add_get("/", _index)

    # -- Static files (SPA frontend); JS/CSS revalidate so deploys land --
    if config.static_dir.exists():

        @web.middleware
        async def _static_no_store(request: web.Request, handler: Any) -> web.StreamResponse:
            response = await handler(request)
            if request.path.startswith("/static/"):
                response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return response

        app.middlewares.append(_static_no_store)
        app.router.add_static(
            "/static",
            path=str(config.static_dir),
            name="static",
        )
        logger.info("Static files served from %s", config.static_dir)
    else:
        logger.warning("Static directory not found at %s — SPA unavailable", config.static_dir)

    # -- Startup handler (connect DB, migrate schema, start scheduler) --
    async def _on_startup(app: web.Application) -> None:
        await db.connect()
        await db.migrate()
        await scheduler.start()
        logger.info(
            "Festin service starting on %s:%d",
            config.host,
            config.port,
        )

    async def _on_shutdown(app: web.Application) -> None:
        await scheduler.stop()
        await db.disconnect()
        logger.info("Festin service shutting down")

    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)

    return app


async def run_server(config: ServiceConfig | None = None) -> None:
    """Start the FestIn monitoring dashboard in a blocking manner."""
    config = config or ServiceConfig()
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    logger.addHandler(handler)

    app = await create_app(config)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.host, config.port)
    logger.info("Festin service listening on http://%s:%d", config.host, config.port)
    try:
        await site.start()
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        await runner.cleanup()
        logger.info("Service stopped")


# -- CLI entry point --


def main() -> None:
    """CLI entry point: ``python -m festin.service``."""
    import argparse

    parser = argparse.ArgumentParser(description="Festin monitoring dashboard server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    parser.add_argument("--port", type=int, default=8420, help="Bind port")
    parser.add_argument("--db", help="SQLite database path")
    parser.add_argument("--auth-file", help="Auth users file (username:password per line)")
    args = parser.parse_args()

    config = ServiceConfig(
        host=args.host,
        port=args.port,
        db_path=Path(args.db) if args.db else None,
        auth_users_file=Path(args.auth_file) if args.auth_file else None,
    )
    asyncio.run(run_server(config))


if __name__ == "__main__":
    main()
