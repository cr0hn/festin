"""REST API router for the FestIn monitoring dashboard."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from ..models import ScanResult
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
        raise web.HTTPBadRequest(reason="Invalid JSON body") from None


def _scan_response(scan_id: str, result: ScanResult | None, status: str = "done") -> dict[str, Any]:
    """Build a scan response dict from a ScanResult."""
    if result is None:
        return {"scan_id": scan_id, "status": status}
    data = result.to_dict()
    data["scan_id"] = scan_id
    data["status"] = status
    return data


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


async def health_check(request: web.Request) -> web.Response:
    """Simple health check that responds with 200 OK."""
    return web.json_response(
        {
            "status": "ok",
            "version": "0.2.0",
        }
    )


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
    return web.json_response(
        {
            "scan_id": "batch-1",
            "tasks": results,
        }
    )


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
    database: Database,
    scheduler: Scheduler | None = None,
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


class FestinRouter:
    """HTTP router exposing the FestIn monitoring dashboard API."""

    def __init__(
        self,
        database: Database,
        queue_manager: QueueManager | None = None,
        scheduler: Scheduler | None = None,
        scan_callback: Callable[[list[str]], Awaitable[ScanResult]] | None = None,
        auth: Any | None = None,
    ) -> None:
        self._db = database
        self._queues = queue_manager
        self._scheduler = scheduler
        self._scan_callback = scan_callback
        self._auth = auth

    def add_routes(self, app: web.Application, prefix: str = "/api/v1") -> None:
        """Register all API routes on the given aiohttp application."""

        # --------------------------------------------------------------
        # Handlers
        # --------------------------------------------------------------

        async def _handle_health(request: web.Request) -> web.Response:
            pending = 0
            if self._scheduler is not None and hasattr(self._scheduler, "stats"):
                pending = (await self._scheduler.stats()).get("pending", 0)
            return web.json_response({"status": "ok", "pending": pending})

        async def _handle_login(request: web.Request) -> web.Response:
            if self._auth is None:
                raise web.HTTPServiceUnavailable(reason="Auth not configured")
            body = await _json_body(request)
            try:
                result = await self._auth.login(body.get("username", ""), body.get("password", ""))
            except ValueError as exc:
                return web.json_response({"error": str(exc)}, status=401)
            return web.json_response(result)

        async def _handle_register(request: web.Request) -> web.Response:
            if self._auth is None:
                raise web.HTTPServiceUnavailable(reason="Auth not configured")
            body = await _json_body(request)
            username = body.get("username", "")
            password = body.get("password", "")
            first_user = await self._db.user_count() == 0
            if not first_user:
                # Only an authenticated admin may create further users.
                if request.get("user") is None:
                    return web.json_response({"error": "unauthorized"}, status=401)
                current = await self._auth.verify(
                    request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                )
                if current is None or current.get("role") != "admin":
                    return web.json_response({"error": "forbidden"}, status=403)
            role = "admin" if first_user else "viewer"
            try:
                user = await self._auth.register(username, password, role)
            except ValueError as exc:
                return web.json_response({"error": str(exc)}, status=400)
            return web.json_response({"id": user["id"], "username": user["username"]}, status=201)

        async def _admin_guard(request: web.Request) -> None:
            """Raise 401/403 unless the caller is an authenticated admin."""
            if self._auth is None:
                return
            if request.get("user") is None:
                raise web.HTTPUnauthorized(
                    text='{"error": "unauthorized"}',
                    content_type="application/json",
                )
            current = await self._auth.verify(
                request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            )
            if current is None or current.get("role") != "admin":
                raise web.HTTPForbidden(
                    text='{"error": "forbidden"}',
                    content_type="application/json",
                )

        async def _handle_list_projects(request: web.Request) -> web.Response:
            data = await self._db.list_projects()
            return web.json_response(data)

        async def _handle_create_project(request: web.Request) -> web.Response:
            await _admin_guard(request)
            body = await _json_body(request)
            name = body.get("name", "")
            if not name:
                raise web.HTTPBadRequest(reason="Missing 'name'")
            try:
                project_id = await self._db.create_project(name, body.get("description", "") or "")
            except ValueError as exc:
                return web.json_response({"error": str(exc)}, status=409)
            project = await self._db.get_project(project_id)
            return web.json_response(project, status=201)

        async def _handle_get_project(request: web.Request) -> web.Response:
            project_id = int(request.match_info["id"])
            project = await self._db.get_project(project_id)
            if project is None:
                return web.json_response({"error": "not found"}, status=404)
            domains = await self._db.list_domains(project_id=project_id)
            return web.json_response(
                {
                    "project": project,
                    "domains": domains["domains"],
                }
            )

        async def _handle_patch_project(request: web.Request) -> web.Response:
            await _admin_guard(request)
            project_id = int(request.match_info["id"])
            body = await _json_body(request)
            project = await self._db.update_project(
                project_id,
                name=body.get("name"),
                description=body.get("description"),
            )
            if project is None:
                return web.json_response({"error": "not found"}, status=404)
            return web.json_response(project)

        async def _handle_delete_project(request: web.Request) -> web.Response:
            await _admin_guard(request)
            project_id = int(request.match_info["id"])
            if project_id == 1:
                return web.json_response({"error": "cannot delete default project"}, status=400)
            deleted = await self._db.delete_project(project_id)
            if not deleted:
                return web.json_response({"error": "not found"}, status=404)
            return web.json_response({"deleted": project_id})

        async def _handle_create_domain(request: web.Request) -> web.Response:
            await _admin_guard(request)
            project_id = int(request.match_info["id"])
            if await self._db.get_project(project_id) is None:
                return web.json_response({"error": "not found"}, status=404)
            body = await _json_body(request)
            domain_name = body.get("domain_name", "")
            if not domain_name:
                raise web.HTTPBadRequest(reason="Missing 'domain_name'")
            if await self._db.find_domain(domain_name) is not None:
                return web.json_response(
                    {"error": f"Domain '{domain_name}' already exists"},
                    status=409,
                )
            domain_id = await self._db.create_domain(domain_name, project_id)
            return web.json_response(
                {"id": domain_id, "domain_name": domain_name, "project_id": project_id},
                status=201,
            )

        async def _handle_list_project_domains(
            request: web.Request,
        ) -> web.Response:
            project_id = int(request.match_info["id"])
            data = await self._db.list_domains(project_id=project_id)
            return web.json_response(data)

        async def _handle_list_domains(request: web.Request) -> web.Response:
            project_raw = request.query.get("project_id")
            try:
                project_id = int(project_raw) if project_raw else None
            except ValueError:
                raise web.HTTPBadRequest(reason="Invalid 'project_id'") from None
            data = await self._db.list_domains(project_id=project_id)
            return web.json_response(data)

        async def _handle_delete_domain(request: web.Request) -> web.Response:
            await _admin_guard(request)
            domain_id = int(request.match_info["id"])
            deleted = await self._db.delete_domain(domain_id)
            if not deleted:
                return web.json_response({"error": "not found"}, status=404)
            return web.json_response({"deleted": domain_id})

        async def _handle_list_users(request: web.Request) -> web.Response:
            await _admin_guard(request)
            users = await self._db.list_users()
            return web.json_response({"users": users})

        async def _handle_create_user(request: web.Request) -> web.Response:
            await _admin_guard(request)
            body = await _json_body(request)
            try:
                user = await self._auth.register(
                    body.get("username", ""),
                    body.get("password", ""),
                    body.get("role", "viewer"),
                )
            except ValueError as exc:
                return web.json_response({"error": str(exc)}, status=400)
            return web.json_response(user, status=201)

        async def _handle_patch_user(request: web.Request) -> web.Response:
            await _admin_guard(request)
            user_id = int(request.match_info["id"])
            body = await _json_body(request)
            role = body.get("role", "")
            if role not in ("admin", "viewer"):
                return web.json_response({"error": "invalid role"}, status=400)
            updated = await self._db.set_user_role(user_id, role)
            if not updated:
                return web.json_response({"error": "not found"}, status=404)
            user = await self._db.get_user(user_id)
            return web.json_response(user)

        async def _handle_delete_user(request: web.Request) -> web.Response:
            await _admin_guard(request)
            user_id = int(request.match_info["id"])
            if request.get("user") is not None:
                target = await self._db.get_user(user_id)
                if target is not None and target["username"] == request["user"]:
                    return web.json_response({"error": "cannot delete yourself"}, status=400)
            if await self._db.count_admins() <= 1:
                target = await self._db.get_user(user_id)
                if target is not None and target["role"] == "admin":
                    return web.json_response({"error": "cannot delete the last admin"}, status=400)
            deleted = await self._db.delete_user(user_id)
            if not deleted:
                return web.json_response({"error": "not found"}, status=404)
            return web.json_response({"deleted": user_id})

        async def _handle_get_scan(request: web.Request) -> web.Response:
            scan_id = int(request.match_info["id"])
            detail = await self._db.get_scan_detail(scan_id)
            if detail is None:
                return web.json_response({"error": "not found"}, status=404)
            return web.json_response(detail)

        async def _handle_stats(request: web.Request) -> web.Response:
            return web.json_response(await self._db.get_stats())

        async def _handle_list_scans(request: web.Request) -> web.Response:
            limit = request.query.get("limit")
            project_raw = request.query.get("project_id")
            status = request.query.get("status")
            try:
                limit_int = int(limit) if limit else 100
                project_id = int(project_raw) if project_raw else None
            except ValueError:
                raise web.HTTPBadRequest(reason="Invalid query parameter") from None
            data = await self._db.list_scans(project_id=project_id, status=status, limit=limit_int)
            return web.json_response(data)

        async def _handle_delete_scan(request: web.Request) -> web.Response:
            scan_id = int(request.match_info["id"])
            deleted = await self._db.delete_scan(scan_id)
            if not deleted:
                return web.json_response({"error": "not found"}, status=404)
            return web.json_response({"deleted": scan_id})

        async def _handle_list_findings(request: web.Request) -> web.Response:
            severity = request.query.get("severity")
            limit_raw = request.query.get("limit")
            try:
                limit = int(limit_raw) if limit_raw else 50
            except ValueError:
                raise web.HTTPBadRequest(reason="Invalid 'limit'") from None
            data = await self._db.list_findings(severity=severity, limit=limit)
            return web.json_response(data)

        async def _handle_list_buckets(request: web.Request) -> web.Response:
            limit_raw = request.query.get("limit")
            try:
                limit = int(limit_raw) if limit_raw else 30
            except ValueError:
                raise web.HTTPBadRequest(reason="Invalid 'limit'") from None
            data = await self._db.list_buckets(limit=limit)
            return web.json_response(data)

        async def _handle_list_schedule(request: web.Request) -> web.Response:
            schedules = await self._db.list_scheduled_scans()
            return web.json_response({"scheduled": schedules, "total": len(schedules)})

        async def _handle_create_schedule(request: web.Request) -> web.Response:
            body = await _json_body(request)
            domain = body.get("domain", "")
            if not domain:
                raise web.HTTPBadRequest(reason="Missing 'domain'")
            interval = body.get("interval_minutes", 60)
            entry = await self._db.add_scheduled_scan(domain, interval)
            return web.json_response(entry, status=201)

        async def _handle_delete_schedule(request: web.Request) -> web.Response:
            schedule_id = int(request.match_info["id"])
            removed = await self._db.remove_scheduled_scan(schedule_id)
            if not removed:
                return web.json_response({"error": "not found"}, status=404)
            return web.json_response({"removed": schedule_id})

        async def _handle_run_scan(request: web.Request) -> web.Response:
            body = await _json_body(request)
            domains = body.get("domains", [])
            if not domains:
                raise web.HTTPBadRequest(reason="Missing 'domains' list")
            project_id = body.get("project_id", 1)
            if not isinstance(project_id, int) or project_id < 1:
                raise web.HTTPBadRequest(reason="'project_id' must be a positive integer")

            async def _persist_scan(domain_name: str, pid: int) -> int:
                """Create (or reuse) the domain row and open a scan record."""
                existing = await self._db.find_domain(domain_name)
                domain_id = (
                    existing["domain_id"]
                    if existing
                    else await self._db.create_domain(domain_name, pid)
                )
                return await self._db.create_scan(domain_id)

            async def _execute_scan(scan_id: int, domain_list: list[str]) -> None:
                """Run festin's scan pipeline and persist the outcome."""
                status = "completed"
                buckets_found = findings_count = 0
                await self._db.update_scan_status(scan_id, status="running")
                try:
                    from festin.scan_runner import build_namespace, run_scan

                    cli_args = build_namespace(
                        quiet=True,
                        no_print=True,
                        scan_id=f"svc-{scan_id}",
                    )
                    result_obj = await run_scan(cli_args, domain_list)
                    if result_obj is not None:
                        await self._db.persist_scan_results(
                            scan_id, result_obj.buckets, result_obj.findings
                        )
                    buckets_found = len(getattr(result_obj, "buckets", []) or [])
                    findings_count = len(getattr(result_obj, "findings", []) or [])
                except Exception:
                    logger.exception("Scan %s failed", scan_id)
                    status = "failed"
                await self._db.update_scan_status(
                    scan_id,
                    status=status,
                    buckets_found=buckets_found,
                    findings_count=findings_count,
                )

            if self._scan_callback is not None:
                await self._scan_callback(domains)
                scan_id = await _persist_scan(domains[0], project_id)
                return web.json_response({"scan_id": scan_id, "status": "accepted"}, status=202)

            if self._scheduler is None or not hasattr(self._scheduler, "enqueue"):
                raise web.HTTPServiceUnavailable(reason="No scan backend configured")

            job = await self._scheduler.enqueue(domains)
            scan_id = await _persist_scan(domains[0], project_id)
            asyncio.create_task(_execute_scan(scan_id, domains))
            return web.json_response(
                {"scan_id": scan_id, "job_id": job.get("job_id"), "status": "accepted"},
                status=202,
            )

        # --------------------------------------------------------------
        # Route registration
        # --------------------------------------------------------------

        app.router.add_get(f"{prefix}/health", _handle_health)
        app.router.add_post(f"{prefix}/auth/login", _handle_login)
        app.router.add_post(f"{prefix}/auth/register", _handle_register)
        app.router.add_get(f"{prefix}/stats", _handle_stats)
        app.router.add_get(f"{prefix}/scans", _handle_list_scans)
        app.router.add_get(f"{prefix}/scans/{{id}}", _handle_get_scan)
        app.router.add_delete(f"{prefix}/scans/{{id}}", _handle_delete_scan)
        app.router.add_get(f"{prefix}/findings", _handle_list_findings)
        app.router.add_get(f"{prefix}/buckets", _handle_list_buckets)
        app.router.add_get(f"{prefix}/queues/schedule", _handle_list_schedule)
        app.router.add_post(f"{prefix}/queues/schedule", _handle_create_schedule)
        app.router.add_delete(f"{prefix}/queues/schedule/{{id}}", _handle_delete_schedule)
        app.router.add_post(f"{prefix}/scans/run-scan", _handle_run_scan)
        app.router.add_get(f"{prefix}/projects", _handle_list_projects)
        app.router.add_post(f"{prefix}/projects", _handle_create_project)
        app.router.add_get(f"{prefix}/projects/{{id}}", _handle_get_project)
        app.router.add_patch(f"{prefix}/projects/{{id}}", _handle_patch_project)
        app.router.add_delete(f"{prefix}/projects/{{id}}", _handle_delete_project)
        app.router.add_post(f"{prefix}/projects/{{id}}/domains", _handle_create_domain)
        app.router.add_get(f"{prefix}/projects/{{id}}/domains", _handle_list_project_domains)
        app.router.add_get(f"{prefix}/domains", _handle_list_domains)
        app.router.add_delete(f"{prefix}/domains/{{id}}", _handle_delete_domain)
        app.router.add_get(f"{prefix}/users", _handle_list_users)
        app.router.add_post(f"{prefix}/users", _handle_create_user)
        app.router.add_patch(f"{prefix}/users/{{id}}", _handle_patch_user)
        app.router.add_delete(f"{prefix}/users/{{id}}", _handle_delete_user)
