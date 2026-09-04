"""REST API server mode for festin.

Exposes scan results over an aiohttp JSON API under ``/api/v1``.
``festin serve`` wires this module into the CLI (see integration layer).
"""

from __future__ import annotations

import asyncio
import json
import socket
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from aiohttp import web

from . import __version__
from .models import SEVERITY_ORDER, ScanResult

API_BASE = "/api/v1"


@dataclass
class ApiConfig:
    """Configuration for the REST API server."""

    host: str = "127.0.0.1"
    port: int = 8420
    state_file: Path | None = None
    scan_callback: Callable[[list[str]], Awaitable[ScanResult]] | None = None


def _error(status: int, message: str) -> web.Response:
    """Build a uniform JSON error response."""
    return web.json_response({"error": message}, status=status)


async def _read_json_body(request: web.Request) -> dict:
    """Parse the request body as a JSON object; raise 400 otherwise."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "invalid JSON body"}), content_type="application/json"
        ) from None
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "invalid JSON body"}), content_type="application/json"
        )
    return body


def _validate_domains(body: dict) -> list[str]:
    """Extract and validate the ``domains`` list from a request body."""
    domains = body.get("domains")
    if not isinstance(domains, list) or not all(isinstance(item, str) for item in domains):
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "domains must be a list of strings"}),
            content_type="application/json",
        )
    return domains


def _scan_summary(scan_id: str, result: ScanResult) -> dict:
    """Compact per-scan summary used by the scans listing route."""
    return {
        "scan_id": scan_id,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "bucket_count": len(result.buckets),
        "finding_count": len(result.findings),
    }


@web.middleware
async def error_middleware(request: web.Request, handler: Callable) -> web.Response:
    """Convert unexpected exceptions into a clean 500 without leaking details."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        return _error(500, "internal")


class FestinApi:
    """HTTP server exposing festin scan data over JSON."""

    def __init__(self, config: ApiConfig) -> None:
        self._config = config
        self._runner: web.AppRunner | None = None
        self._sock: socket.socket | None = None
        self._bound_port: int | None = None
        self._results: dict[str, ScanResult] = {}

    @property
    def host(self) -> str:
        """Configured bind host."""
        return self._config.host

    @property
    def port(self) -> int:
        """Actual bound port (resolved when configured as 0)."""
        if self._bound_port is not None:
            return self._bound_port
        return self._config.port

    def build_app(self) -> web.Application:
        """Create the aiohttp application with all routes registered."""
        app = web.Application(middlewares=[error_middleware])
        app.router.add_post(f"{API_BASE}/scans", self.handle_create_scan)
        app.router.add_get(f"{API_BASE}/scans", self.handle_list_scans)
        app.router.add_get(f"{API_BASE}/scans/{{scan_id}}", self.handle_get_scan)
        app.router.add_get(f"{API_BASE}/findings", self.handle_findings)
        app.router.add_get(f"{API_BASE}/buckets", self.handle_buckets)
        app.router.add_get(f"{API_BASE}/health", self.handle_health)
        return app

    async def start(self) -> None:
        """Bind the configured address and start serving."""
        runner = web.AppRunner(self.build_app())
        await runner.setup()
        self._runner = runner
        if self._config.port == 0:
            self._sock = socket.create_server((self._config.host, 0))
            site = web.SockSite(runner, self._sock)
        else:
            site = web.TCPSite(runner, self._config.host, self._config.port)
        await site.start()
        if self._sock is not None:
            self._bound_port = self._sock.getsockname()[1]

    async def stop(self) -> None:
        """Stop serving and release the bound socket."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        self._bound_port = None

    async def handle_health(self, request: web.Request) -> web.Response:
        """Liveness probe."""
        return web.json_response({"status": "ok", "version": __version__})

    async def handle_create_scan(self, request: web.Request) -> web.Response:
        """Accept a new scan request and run the injected scan callback."""
        body = await _read_json_body(request)
        domains = _validate_domains(body)
        if self._config.scan_callback is None:
            return _error(503, "no scan callback configured")
        scan_id = uuid.uuid4().hex
        result = await self._config.scan_callback(domains)
        result.scan_id = scan_id
        self._results[scan_id] = result
        return web.json_response({"scan_id": scan_id, "status": "accepted"}, status=202)

    async def handle_get_scan(self, request: web.Request) -> web.Response:
        """Return one scan result by id."""
        scan_id = request.match_info["scan_id"]
        result = (await self._all_results()).get(scan_id)
        if result is None:
            return _error(404, f"scan {scan_id} not found")
        data = result.to_dict()
        data["status"] = "done"
        return web.json_response(data)

    async def handle_list_scans(self, request: web.Request) -> web.Response:
        """List all known scans, newest first."""
        results = await self._all_results()
        ordered = sorted(results.items(), key=lambda item: item[1].started_at, reverse=True)
        return web.json_response({"scans": [_scan_summary(sid, res) for sid, res in ordered]})

    async def handle_findings(self, request: web.Request) -> web.Response:
        """List findings across all scans, optionally filtered by severity."""
        severity = request.query.get("severity")
        if severity is not None and severity not in SEVERITY_ORDER:
            return _error(400, "invalid severity")
        findings = []
        for scan_id, result in (await self._all_results()).items():
            for finding in result.findings:
                if severity is None or finding.severity == severity:
                    findings.append({**asdict(finding), "scan_id": scan_id})
        return web.json_response({"findings": findings})

    async def handle_buckets(self, request: web.Request) -> web.Response:
        """List buckets across all scans, each tagged with its scan_id."""
        buckets = []
        for scan_id, result in (await self._all_results()).items():
            for bucket in result.buckets:
                buckets.append({**asdict(bucket), "scan_id": scan_id})
        return web.json_response({"buckets": buckets})

    async def _all_results(self) -> dict[str, ScanResult]:
        """Merge in-memory results with those persisted in the state file."""
        results = dict(self._results)
        results.update(await _load_state_results(self._config.state_file))
        return results


async def _load_state_results(path: Path | None) -> dict[str, ScanResult]:
    """Load ScanResult mapping from a festin.state JSON file (lazy import)."""
    if path is None:
        return {}
    from .state import load_state  # sibling module owned by the state agent

    return await load_state(path)


async def run_server(config: ApiConfig) -> None:
    """Start the API server and serve until cancelled."""
    api = FestinApi(config)
    await api.start()
    try:
        await asyncio.Event().wait()
    finally:
        await api.stop()


__all__ = ("API_BASE", "ApiConfig", "FestinApi", "run_server")
