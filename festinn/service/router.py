"""REST API router for the FestIn monitoring dashboard."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aiohttp import web

from ..models import SEVERITY_ORDER, ScanResult
from .database import Database
from .queues import QueueManager
from .scheduler import Scheduler

logger = logging.getLogger("festin.service.router")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _json_body(request: web.Request) -> dict[str, Any]:
    """Parse request body as JSON."""
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("Expected JSON object")
        return data
    except (ValueError, TypeError):
        raise web.HTTPBadRequest(reason="Invalid JSON body")


def _scan_response(
    scan_id: str, result: ScanResult | None, status: str = "done"
) -> dict[str, Any]:
    """Build a scan response dict from a ScanResult."""
    if result is None:
        return {"scan_id": scan_id, "status": status}
    return {**result.to_dict(), "scan_id": scan_id, "status": status}


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

async def health_check(request: web.Request) -> web.Response:
    """Simple health check that responds with 200 OK."""
    return web.json_response({"status": "ok", "version": "0.2.0"})


# ---------------------------------------------------------------------------
# Domain management endpoints
# ---------------------------------------------------------------------------

async def domain_list(request: web.Request) -> web.Response:
    """List all domains in the database."""
    db = request.app["db"]
    domains = await db.list_domains()
    return web.json_response({"domains": domains})


async def domain_add(request: web.Request) -> web.Response:
    """Add a new domain for scanning."""
    data = await _json_body(request)
    domain_name = data.get("domain")
    if not domain_name:
        raise web.HTTPBadRequest(reason="Missing 'domain' field")
    db = request.app["db"]
    await db.add_domain(domain_name)
    return web.json_response({"added": domain_name}, status=201)


async def domain_remove(request: web.Request) -> web.Response:
    """Remove a domain from scanning."""
    data = await _json_body(request)
    domain_name = data.get("domain")
    if not domain_name:
        raise web.HTTPBadRequest(reason="Missing 'domain' field")
    db = request.app["db"]
    await db.remove_domain(domain_name)
    return web.json_response({"removed": domain_name})


# ---------------------------------------------------------------------------
# Scan execution endpoints
# ---------------------------------------------------------------------------

async def scan_start(request: web.Request) -> web.Response:
    """Trigger a new scan for all domains."""
    qm = request.app["queue"]
    db = request.app["db"]
    domains = await db.list_domains()
    results = []
    for dom in domains:
        res = await qm.queue().push_scan_job({"domain": dom})
        results.append(res)
    return web.json_response({"scan_id": "batch-1", "tasks": results})


async def scan_status(request: web.Request) -> web.Response:
    """Check the status of a scan."""
    data = await _json_body(request)
    task_id = data.get("task_id")
    if not task_id:
        raise web.HTTPBadRequest(reason="Missing 'task_id'")
    return web.json_response({"task_id": task_id, "status": "running"})


# ---------------------------------------------------------------------------
# Scheduler endpoints
# ---------------------------------------------------------------------------

async def scheduler_status(request: web.Request) -> web.Response:
    """Get the current scheduler status."""
    sched = request.app.get("scheduler")
    if sched is None:
        return web.json_response({"error": "Scheduler not configured"})
    running = getattr(sched, "_running", False)
    return web.json_response({"running": running})


async def scheduler_start(request: web.Request) -> web.Response:
    """Start the scheduler."""
    sched = request.app.get("scheduler")
    if sched is None:
        return web.json_response({"error": "Scheduler not configured"})
    await sched.start()
    return web.json_response({"started": True})


async def scheduler_stop(request: web.Request) -> web.Response:
    """Stop the scheduler."""
    sched = request.app.get("scheduler")
    if sched is None:
        return web.json_response({"error": "Scheduler not configured"})
    await sched.stop()
    return web.json_response({"stopped": True})


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_router(
    database: Database,
    queue_manager: QueueManager,
    scheduler: Scheduler | None = None,
) -> web.RouteTableDef:
    """Create the full API router with all endpoints."""
    routes = web.RouteTableDef()

    # Health
    routes.get("/api/v1/health")(health_check)

    # Domain management
    routes.get("/api/v1/domains")(domain_list)
    routes.post("/api/v1/domains")(domain_add)
    routes.delete("/api/v1/domains")(domain_remove)

    # Scan execution  
    routes.post("/api/v1/scans")(scan_start)
    routes.post("/api/v1/scans/status")(scan_status)

    # Scheduler control
    routes.get("/api/v1/scheduler")(scheduler_status)
    routes.post("/api/v1/scheduler/start")(scheduler_start)
    routes.delete("/api/v1/scheduler/stop")(scheduler_stop)

    return routes


async def setup_app(
    database: Database, scheduler: Scheduler | None = None
) -> web.AppRunner:
    """Set up and start the FestIn monitoring dashboard app."""
    qm = QueueManager()
    await qm.start()

    app = web.Application()
    app["db"] = database
    app["queue"] = qm
    if scheduler is not None:
        app["scheduler"] = scheduler

    router = create_router(database, qm, scheduler)
    app.add_routes(router)

    return web.AppRunner(app)
