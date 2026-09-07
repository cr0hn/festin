"""festin.service.cli - Typer CLI launching FestIn Monitor FastAPI SPA."""

from __future__ import annotations

import typer
import uvicorn

app_cli = typer.Typer(help="FestIn monitoring dashboard")


@app_cli.command("serve")
def serve(
    db_path: str = "sqlite:///festin.db",
    admin_user: str = "admin",
    admin_pass: str | None = None,
    port: int = 8000,
    host: str = "0.0.0.0",
    secret_key: str = "festin-secret-change-me",
) -> None:
    """Launch the FestIn Monitor FastAPI SPA dashboard."""
    from .app import FestInApp, create_app

    if admin_pass is None:
        import secrets

        admin_pass = secrets.token_urlsafe(12)
    festin_app = FestInApp(
        db_path=db_path,
        admin_username=admin_user,
        jwt_secret=secret_key,
    )
    api_app = create_app(festin_app)
    print(f"[serve] Starting FastAPI SPA dashboard at {host}:{port}")
    print(f"[serve] Admin user: {admin_user} / password: {admin_pass}")
    uvicorn.run(api_app, host=host, port=port, log_level="info")


@app_cli.command("worker", help="Run the streaQ scan worker (FESTIN_QUEUE=streaq)")
def worker(
    db_path: str = "data/festin.db",
    redis_url: str = "redis://localhost:6379/0",
) -> None:
    """Consume scan jobs from Redis Streams and execute them."""
    import os

    from .serve import run_worker

    os.environ.setdefault("FESTIN_REDIS_URL", redis_url)
    os.environ.setdefault("FESTIN_DB_DSN", db_path)
    run_worker()


@app_cli.command("version", help="Print FestIn Monitor version")
def show_version() -> None:
    print("FestIn Monitor 0.3.0")


def main() -> None:
    """CLI entry point: python -m festin.service serve"""
    app_cli()
