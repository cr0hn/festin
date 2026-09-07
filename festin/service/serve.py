"""Festin service application factory and server launcher."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
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
    ) -> None:
        self.host = host
        self.port = port
        self.db_path = db_path or Path("data", "festin.db")
        self.static_dir = static_dir or (Path(__file__).resolve().parent / "static")
        self.state_file = state_file
        self.auth_users_file = auth_users_file
        self.scan_callback = scan_callback


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
            return web.FileResponse(index)
        return web.json_response({"error": "SPA not built"}, status=404)

    app.router.add_get("/", _index)

    # -- Static files (SPA frontend) --
    if config.static_dir.exists():
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
            config.host, config.port,
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


async def start_background_scans(config: ServiceConfig | None = None, callback: Callable | None = None) -> Scheduler:
    """Start the scheduler for background scans (non-blocking).

    Returns the Scheduler instance; call ``scheduler.stop()`` to shut down.
    """
    config = config or ServiceConfig()
    app = await create_app(config)

    if callback is not None:
        async def _wrap_callback(domains: list[str]) -> ScanResult:
            from datetime import datetime, timezone
            return ScanResult(
                scan_id="bg",
                started_at=datetime.now(timezone.utc).isoformat(),
                finished_at=datetime.now(timezone.utc).isoformat(),
                domains=domains,
            )

        config.scan_callback = _wrap_callback

    return Scheduler()


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
