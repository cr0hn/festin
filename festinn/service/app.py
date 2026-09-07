"""FestIn service FastAPI application factory with SPA serving."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Optional


SERVICE_VERSION = "0.2.0"


class FestInApp:
    """Application factory managing lifecycle of all service components."""

    def __init__(self, db_path: str = "sqlite:///festin.db",
                 admin_username: str = "admin", admin_password: str | None = None,
                 jwt_secret: str = "change-me-in-production") -> None:
        self._db_path = db_path
        self._admin_username = admin_username
        self._admin_password = admin_password or "admin"
        self._jwt_secret = jwt_secret
        self._db: Any = None
        self._scheduler: Any = None

    @property
    def db(self) -> Any:
        return self._db

    @property
    def scheduler(self) -> Any:
        return self._scheduler

    async def startup(self) -> None:
        """Initialize all service components."""
        from .database import Database
        self._db = Database(self._db_path)
        await self._db.connect()
        await self._db.migrate()

        from .auth import AuthService
        auth_svc = AuthService(self._db, secret_key=self._jwt_secret)
        await auth_svc.init_admin(self._admin_username, self._admin_password)

        from .scheduler import FestInScheduler, SchedulerConfig
        cfg = SchedulerConfig(max_concurrent_scans=3, check_interval=10.0, scan_timeout=600)
        self._scheduler = FestInScheduler(self._db, config=cfg)
        await self._scheduler.start()

        print(f"[festin] Service started: db={self._db_path}")

    async def shutdown(self) -> None:
        """Gracefully stop all components."""
        if self._scheduler is not None:
            await self._scheduler.stop()
        if self._db is not None:
            await self._db.disconnect()
        print("[festin] Service shut down.")


def create_app(festin_app: FestInApp | None = None, static_dir: str = "static") -> Any:
    """Create FastAPI app with lifecycle + SPA serving."""
    if festin_app is None:
        festin_app = FestInApp()

    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="FestIn Monitor", version=SERVICE_VERSION)

    @app.on_event("startup")
    async def _startup() -> None:
        await festin_app.startup()
        # Inject DB and scheduler into API routers
        from .api import set_db, set_scheduler
        set_db(festin_app._db)
        set_scheduler(festin_app._scheduler)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await festin_app.shutdown()

    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    # Health endpoint (public, no auth needed)
    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": SERVICE_VERSION}

    # Mount static SPA files
    base_dir = os.path.dirname(os.path.abspath(__file__))
    spa_path = os.path.join(base_dir, static_dir)
    app.mount("/", StaticFiles(directory=spa_path, html=True), name="static")

    return app
