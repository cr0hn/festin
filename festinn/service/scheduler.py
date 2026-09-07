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

    def __init__(
        self, database: Any, config: SchedulerConfig | None = None
    ) -> None:
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
        """Stop the scheduler gracefully."""
        if not self._running:
            return
        self._running = False
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):
                pass

    async def _scan_loop(self) -> None:
        """Background loop that triggers periodic scans."""
        interval = self.config.check_interval
        while self._running:
            try:
                await self._check_domains()
            except Exception:
                await asyncio.sleep(1)
            await asyncio.sleep(interval)

    async def _check_domains(self) -> None:
        """Check all registered domains for new buckets."""
        pass   # Delegate to queue manager in production


class DomainScanner:
    """Wraps the scan_runner for use by the scheduler."""

    def __init__(self, queue_manager: Any) -> None:
        self._queue = queue_manager

    async def trigger_scan(self, domain: str) -> dict[str, Any]:
        """Submit a domain for scanning through the queue."""
        task_id = await self._queue.push_scan_job({"domain": domain})
        return {"task_id": task_id, "status": "queued"}


class Scheduler:
    """High-level scheduler wrapper coordinating domain scanning."""

    def __init__(
        self, database: Any, config: SchedulerConfig | None = None
    ) -> None:
        self._db = database
        self._config = config or SchedulerConfig()
        self._scheduler = FestInScheduler(database, config)
        self._queue_manager: Any = None

    @property
    def running(self) -> bool:
        """Whether the scheduler is currently active."""
        return self._scheduler.running

    async def start(self) -> None:
        """Start all background scanning tasks."""
        await self._scheduler.start()

    async def stop(self) -> None:
        """Stop all background scanning tasks."""
        await self._scheduler.stop()

    async def trigger_domain_scan(self, domain: str) -> dict[str, Any]:
        """Trigger an immediate scan for a single domain."""
        if self._queue_manager is None:
            return {"error": "Queue not configured"}
        scanner = DomainScanner(self._queue_manager)
        return await scanner.trigger_scan(domain)

    def status(self) -> dict[str, Any]:
        """Return the current scheduler status as a dictionary."""
        return {
            "running": self.running,
            "config": vars(self._config),
        }
