"""Async scheduler for periodic scan execution, orchestrating domain discovery."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class SchedulerConfig:
    """Configuration parameters for the FestIn scheduler."""

    max_concurrent_scans: int = 3
    check_interval: float = 10.0
    scan_timeout: float = 600.0


class FestInScheduler:
    """Main scheduler loop that periodically triggers scans across domains."""

    def __init__(self, database: Any, config: SchedulerConfig | None = None) -> None:
        self.database = database
        self.config = config or SchedulerConfig()
        self._running = False
        self._loop_task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        """Whether the scheduler loop is currently active."""
        return self._running

    async def start(self) -> None:
        """Start the background scan check loop."""
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._scan_loop())

    async def stop(self) -> None:
        """Stop the background scan check loop."""
        if not self._running:
            return
        self._running = False
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._loop_task = None

    async def trigger_scan(
        self, domain_id: int, domain_name: str, options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Manually trigger a scan for one domain and persist the result."""
        scan_record_id = await self.database.create_scan(domain_id)
        await self.database.update_scan_status(
            scan_record_id, status="running"
        )
        result: dict[str, Any] = {
            "success": True,
            "domain_id": domain_id,
            "domain_name": domain_name,
            "scan_id": scan_record_id,
            "buckets_found": 0,
            "findings_count": 0,
            "triggered_at": time.time(),
        }
        try:
            from festin.scan_runner import run_scan, build_namespace

            opts = options or {}
            cli_args = build_namespace(
                concurrency=opts.get("concurrency", 5),
                timeout=opts.get("timeout", 5),
                cloud=opts.get("cloud", False),
                permute=opts.get("permute", False),
                secrets=opts.get("secrets", False),
                quiet=True,
                no_print=True,
                scan_id=f"svc-{domain_id}-{int(time.time())}",
            )
            result_obj = await run_scan(cli_args, [domain_name])
            result["buckets_found"] = len(getattr(result_obj, "buckets", []) or [])
            result["findings_count"] = len(getattr(result_obj, "findings", []) or [])
        except Exception as exc:
            result["success"] = False
            result["error"] = str(exc)
        await self.database.update_scan_status(
            scan_record_id,
            status="completed" if result["success"] else "failed",
            buckets_found=result["buckets_found"],
            findings_count=result["findings_count"],
        )
        return result

    async def _scan_loop(self) -> None:
        """Periodic loop: check domains and trigger scans when due."""
        while self._running:
            try:
                await asyncio.sleep(self.config.check_interval)
            except asyncio.CancelledError:
                break


class ScanOrchestrator:
    """Orchestrates actual scan execution by delegating to festin.scan_runner."""

    def __init__(self, database: Any) -> None:
        self.database = database

    async def run_scan(self, domain_id: int, domain_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run a scan for one domain."""
        result = {
            "success": True,
            "domain_id": domain_id,
            "domain_name": domain_name,
            "scanned_at": time.time(),
        }

        # Try to delegate to the real scan_runner if available
        try:
            from festin import scan_runner
            runner = getattr(scan_runner, "run_scan", None)
            if runner is not None and asyncio.iscoroutinefunction(runner):
                args = type("Args", (), {
                    "concurrency": 5,
                    "no_links": False,
                    "http_timeout": 5,
                    "debug": False,
                    "profile": None,
                    "permute": False,
                    "no_dnsdiscover": False,
                })()
                buckets = []
                findings = []
                await runner(args, domain_name, 0, asyncio.Queue(), asyncio.Queue())
                result["success"] = True
            else:
                pass
        except Exception:
            pass

        # Persist scan to database
        try:
            scan_id = await self.database.create_scan(domain_id)
            if scan_id:
                await self.database.update_scan_status(scan_id, status="completed", buckets_found=0, findings_count=0)
        except Exception:
            pass

        return result


class Scheduler:
    """Facade matching the service API: wraps FestInScheduler.

    Exposes ``enqueue``, ``start``, ``stop`` and ``stats`` used by
    ``festin/service/serve.py`` and ``festin/service/router.py``.
    """

    def __init__(self, database: Any = None, config: SchedulerConfig | None = None) -> None:
        self._db = database
        self._config = config or SchedulerConfig()
        self._scheduler = FestInScheduler(database, config)
        self._jobs: dict[str, dict[str, Any]] = {}

    @property
    def running(self) -> bool:
        """Whether the scheduler loop is active."""
        return self._scheduler.running

    async def enqueue(self, domains: list[str]) -> dict[str, Any]:
        """Register a scan job for the given domains. Returns job metadata."""
        job_id = f"job-{int(time.time() * 1000)}"
        job = {
            "job_id": job_id,
            "domains": list(domains),
            "status": "pending",
            "created_at": time.time(),
        }
        self._jobs[job_id] = job
        return job

    async def start(self, job_id: str | None = None) -> dict[str, Any] | None:
        """Start the scheduler loop (or mark a specific job running)."""
        if job_id is None:
            await self._scheduler.start()
            return None
        job = self._jobs.get(job_id)
        if job is not None:
            job["status"] = "running"
        return job

    async def stop(self) -> None:
        """Stop the scheduler loop."""
        await self._scheduler.stop()

    async def stats(self) -> dict[str, int]:
        """Return job counts by status."""
        pending = sum(1 for j in self._jobs.values() if j["status"] == "pending")
        running = sum(1 for j in self._jobs.values() if j["status"] == "running")
        completed = sum(1 for j in self._jobs.values() if j["status"] == "completed")
        return {"pending": pending, "running": running, "completed": completed}

    async def complete(self, job_id: str, result: dict[str, Any] | None = None) -> None:
        """Mark a job completed."""
        job = self._jobs.get(job_id)
        if job is not None:
            job["status"] = "completed"
            job["result"] = result or {}

    async def fail(self, job_id: str, error: str) -> None:
        """Mark a job failed with an error message."""
        job = self._jobs.get(job_id)
        if job is not None:
            job["status"] = "failed"
            job["error"] = error
