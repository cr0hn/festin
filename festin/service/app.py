"""FestIn service application factory: database, auth, scheduler, and FastAPI wiring."""

from __future__ import annotations

import os
from typing import Any


SERVICE_VERSION = "0.2.0"


class FestInApp:
    """Application factory managing lifecycle of all service components."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path
        self._db: Any = None
        self._scheduler: Any = None

    @property
    def db(self) -> Any:
        """The Database instance, or None when not connected."""
        return self._db

    @property
    def scheduler(self) -> Any:
        """The FestInScheduler instance, or None when not started."""
        return self._scheduler

    async def startup(self) -> None:
        """Initialize all service components in order."""
        from .database import Database
        from .auth import AuthService
        from .scheduler import FestInScheduler, SchedulerConfig

        # Connect database
        db = Database(self._db_path or "data/festin.db")
        await db.connect()
        await db.migrate()
        self._db = db

        # Create admin user (first call wins)
        auth_svc = AuthService(db, secret_key="festin-secret")
        await auth_svc.init_admin("admin", "admin")

        # Start scheduler
        cfg = SchedulerConfig(
            max_concurrent_scans=1,
            check_interval=0.1,
            scan_timeout=600,
        )
        scheduler = FestInScheduler(db, config=cfg)
        await scheduler.start()
        self._scheduler = scheduler

    async def shutdown(self) -> None:
        """Gracefully stop all components."""
        if self._scheduler is not None:
            await self._scheduler.stop()
            self._scheduler = None
        if self._db is not None:
            await self._db.disconnect()
            self._db = None


def create_app(festin_app: FestInApp | None = None) -> Any:
    """Create a FastAPI application wired to the FestInApp lifecycle."""
    if festin_app is None:
        festin_app = FestInApp()

    from fastapi import FastAPI

    app = FastAPI(title="FestIn Monitor", version=SERVICE_VERSION)

    @app.on_event("startup")
    async def _startup() -> None:
        await festin_app.startup()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await festin_app.shutdown()

    @app.get("/api/v1/health")
    async def health_check() -> dict[str, str]:
        return {"status": "ok", "version": SERVICE_VERSION}

    # Mount static SPA directory if it exists
    base_dir = os.path.dirname(os.path.abspath(__file__))
    static_dir = os.path.join(base_dir, "static")
    if os.path.isdir(static_dir):
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app
