"""Queue backends and scan-job routing for the FestIn service.

Dual-mode scan queue selected by ``FESTIN_QUEUE``:

- ``memory`` (default): in-process asyncio queue, zero extra deps.
- ``streaq``: Redis Streams task queue (https://github.com/tastyware/streaq).
  The API process only enqueues; a separate worker process
  (``festin-worker``) consumes and executes scans.

streaQ is an optional dependency: importing this module never requires it —
the import happens lazily inside the streaq branches.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any, NamedTuple

logger = logging.getLogger("festin.service.queues")

QUEUE_MODE_ENV = "FESTIN_QUEUE"
REDIS_URL_ENV = "FESTIN_REDIS_URL"
DB_DSN_ENV = "FESTIN_DB_DSN"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_DB_DSN = "data/festin.db"
VALID_MODES = ("memory", "streaq")


def get_queue_mode(env: dict[str, str] | None = None) -> str:
    """Resolve the queue mode from the environment (default: memory)."""
    source = os.environ if env is None else env
    mode = source.get(QUEUE_MODE_ENV, "memory").strip().lower()
    if mode not in VALID_MODES:
        logger.warning(
            "Invalid %s=%r — falling back to 'memory' (valid: %s)",
            QUEUE_MODE_ENV,
            mode,
            ", ".join(VALID_MODES),
        )
        return "memory"
    return mode


def get_redis_url(env: dict[str, str] | None = None) -> str:
    """Resolve the Redis URL from the environment."""
    source = os.environ if env is None else env
    return source.get(REDIS_URL_ENV, DEFAULT_REDIS_URL)


def get_db_dsn(env: dict[str, str] | None = None) -> str:
    """Resolve the database DSN for worker processes.

    Workers run in a separate process and cannot share the web service's
    Database connection, so they rebuild one from the same DSN the
    service was configured with (FESTIN_DB_DSN or the default SQLite
    path used by the service).
    """
    source = os.environ if env is None else env
    return source.get(DB_DSN_ENV, DEFAULT_DB_DSN)


class FestinQueue:
    """Scan job queue backed by Redis Streams (with local fallback)."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._redis: Any = None
        self._connected = False
        self._url = redis_url
        self._local_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def connect(self) -> None:
        """Connect to the Redis instance."""
        try:
            import coredis

            self._redis = coredis.Redis.from_url(self._url, decode_responses=True)
            await self._redis.ping()
            self._connected = True
        except Exception:
            self._connected = False

    async def push_scan_job(self, data: dict[str, Any]) -> str:
        """Push a scan job onto the queue. Returns a task_id."""
        task_id = str(uuid.uuid4())
        await self._local_queue.put(
            {
                "task_id": task_id,
                "data": data,
                "created_at": time.time(),
            }
        )
        return task_id

    async def pop_job(self) -> dict[str, Any] | None:
        """Pop the next scan job from the queue."""
        try:
            return await asyncio.wait_for(self._local_queue.get(), timeout=0.1)
        except TimeoutError:
            return None

    def is_connected(self) -> bool:
        """Return whether the underlying Redis is connected."""
        return self._connected


class QueueManager:
    """Manager for scan task queues and domain scanning.

    Mode ``memory`` (default): scans are dispatched by the caller
    (in-process executor) and jobs are tracked locally.

    Mode ``streaq``: scan jobs are enqueued through streaQ onto Redis
    Streams and consumed by a separate ``festin-worker`` process.
    """

    def __init__(
        self,
        redis_url: str = DEFAULT_REDIS_URL,
        mode: str | None = None,
    ) -> None:
        self._queue: FestinQueue | None = None
        self._redis_url = redis_url
        self._schedules: dict[str, dict[str, Any]] = {}
        self._mode = mode or get_queue_mode()
        self._worker: Any = None
        self._task: Any = None  # registered streaq task (run_scan_task)

    @property
    def mode(self) -> str:
        """Active queue backend: 'memory' or 'streaq'."""
        return self._mode

    async def start(self) -> None:
        """Initialize the underlying queue.

        In streaq mode the Redis connection is probed eagerly; if it is
        unreachable the manager logs an error and falls back to memory
        mode instead of crashing the service.
        """
        if self._mode == "streaq":
            try:
                bundle = await build_streaq_worker(self._redis_url)
                self._worker = bundle.worker
                self._task = bundle.task
            except Exception as exc:
                logger.error(
                    "streaq queue mode requested but Redis is unreachable (%s) "
                    "— falling back to in-memory queue",
                    exc,
                )
                self._mode = "memory"
        self._queue = FestinQueue(self._redis_url)

    async def stop(self) -> None:
        """Shutdown the queue manager."""
        self._queue = None
        self._task = None
        self._worker = None

    @property
    def queue(self) -> FestinQueue | None:
        """Return the underlying FestinQueue if available."""
        return self._queue

    @property
    def streaq_task(self) -> Any:
        """Registered streaq run_scan task (streaq mode only)."""
        return self._task

    async def enqueue_scan(
        self,
        scan_id: int,
        domain_id: int,
        domains: list[str],
    ) -> str | None:
        """Enqueue one scan job. Returns the backend job id, or None in
        memory mode (the caller executes scans in-process there)."""
        if self._mode == "streaq" and self._task is not None:
            task = await self._task.enqueue(scan_id, domain_id, domains)
            return task.id
        return None

    async def add_schedule(self, domain: str, interval_minutes: int) -> dict[str, Any]:
        """Register a recurring scan schedule for a domain (in-memory)."""
        schedule_id = f"sched-{uuid.uuid4().hex[:12]}"
        schedule = {
            "id": schedule_id,
            "domain": domain,
            "interval_minutes": int(interval_minutes),
            "created_at": time.time(),
            "next_run_at": time.time() + int(interval_minutes) * 60,
        }
        self._schedules[schedule_id] = schedule
        return dict(schedule)

    async def list_schedules(self) -> list[dict[str, Any]]:
        """List all registered scan schedules."""
        return [dict(s) for s in self._schedules.values()]

    async def remove_schedule(self, schedule_id: str) -> bool:
        """Remove a scan schedule. Returns True if it existed."""
        return self._schedules.pop(schedule_id, None) is not None


class DomainScanner:
    """Scan a domain against known cloud providers."""

    def __init__(self, queue_manager: QueueManager) -> None:
        self.queue_manager = queue_manager

    async def scan_domain(self, domain: str) -> dict[str, Any]:
        """Scan one domain and return results."""
        return {"domain": domain, "found": False, "buckets": []}


class EventBroker:
    """Pub/sub event bus for scan lifecycle events."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._redis: Any = None
        self._connected = False
        self._url = redis_url
        self._pub_sub: Any = None
        self._subscribers: dict[str, list[Any]] = {}

    async def connect(self) -> None:
        """Connect to the Redis instance."""
        try:
            import coredis

            self._redis = coredis.Redis.from_url(self._url, decode_responses=True)
            await self._redis.ping()
            self._pub_sub = self._redis.pubsub()
            self._connected = True
        except Exception:
            self._connected = False

    async def publish(self, event: str, data: dict[str, Any]) -> int:
        """Publish an event; returns number of subscribers notified."""
        for callback in list(self._subscribers.get(event, [])):
            try:
                result = callback(data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("Event subscriber for %s failed", event)
        return len(self._subscribers.get(event, []))

    async def subscribe(self, event: str, callback: Any) -> None:
        """Register a local subscriber for an event."""
        self._subscribers.setdefault(event, []).append(callback)

    def is_connected(self) -> bool:
        """Return whether the underlying Redis is connected."""
        return self._connected

    async def disconnect(self) -> None:
        """Disconnect from Redis."""
        self._connected = False
        self._redis = None
        self._pub_sub = None

    async def close(self) -> None:
        """Alias for disconnect()."""
        await self.disconnect()

    def subscriber_count(self, event: str) -> int:
        """Number of local subscribers for an event."""
        return len(self._subscribers.get(event, []))

    def clear_subscribers(self) -> None:
        """Drop all local subscribers."""
        self._subscribers.clear()


class StatusTracker:
    """Track scan job lifecycle state using in-memory dict fallback."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._redis: Any = None
        self._connected = False
        self._url = redis_url
        self._statuses: dict[str, dict[str, Any]] = {}

    async def connect(self) -> None:
        """Connect to the Redis instance."""
        try:
            import coredis

            self._redis = coredis.Redis.from_url(self._url, decode_responses=True)
            await self._redis.ping()
            self._connected = True
        except Exception:
            self._connected = False

    async def mark_started(self, scan_id: str, **fields: Any) -> None:
        """Record scan start."""
        self._statuses[scan_id] = {
            "status": "running",
            "started_at": time.time(),
            **fields,
        }

    async def mark_completed(self, scan_id: str, **fields: Any) -> None:
        """Record scan completion."""
        entry = self._statuses.setdefault(scan_id, {})
        entry.update({"status": "completed", "completed_at": time.time(), **fields})

    async def get_status(self, scan_id: str, domain_id: int = 0) -> dict[str, Any]:
        """Return the tracked status for a scan."""
        data = self._statuses.get(scan_id)
        if data is None:
            return {"scan_id": scan_id, "domain_id": domain_id, "status": "unknown"}
        return dict(data)

    def list_statuses(self) -> list[dict[str, Any]]:
        """Return all tracked statuses."""
        return [dict(s) for s in self._statuses.values()]


# ---------------------------------------------------------------------------
# streaQ integration (lazy: requires the optional 'streaq' extra)
# ---------------------------------------------------------------------------

# Name of the single registered streaq task. The worker process looks this
# task up by name so both sides agree without sharing a module-level worker.
SCAN_TASK_NAME = "run_scan_task"


class StreaqBundle(NamedTuple):
    """Worker plus its registered scan task.

    ``task`` is the AsyncRegisteredTask returned by the ``@worker.task``
    decorator — the only object with a working ``.enqueue()``.
    """

    worker: Any
    task: Any


async def build_streaq_worker(redis_url: str) -> StreaqBundle:
    """Build a streaQ Worker with the scan task registered.

    Probes the Redis connection before returning so callers can fall back
    to memory mode when Redis is unreachable. Raises on any failure.

    Returns the worker (for the consumer process to run) and the
    registered task (for the API process to enqueue with). The worker
    object itself does NOT run its loop here — enqueueing only needs the
    registered task; consumption happens in the separate ``festin-worker``
    process.
    """
    from streaq import Worker  # optional dependency (lazy import)

    worker: Any = Worker(redis_url=redis_url)

    @worker.task(name=SCAN_TASK_NAME)
    async def run_scan_task(scan_id: int, domain_id: int, domains: list[str]) -> dict:
        """Execute the scan pipeline; owns a private Database connection."""
        return await execute_scan_task(scan_id, domain_id, domains, get_db_dsn())

    # Probe connectivity: entering the context establishes the Redis
    # connection; any failure (refused, timeout) surfaces here.
    async with worker:
        await worker.redis.ping()
    return StreaqBundle(worker=worker, task=run_scan_task)


async def execute_scan_task(
    scan_id: int,
    domain_id: int,
    domains: list[str],
    db_dsn: str,
) -> dict[str, Any]:
    """Run the scan pipeline for one enqueued job.

    Mirrors the in-process executor in router.py: run the scan, persist
    buckets/findings, and update the scan status. Uses its own Database
    instance because it runs inside the worker process.
    """
    from .database import Database

    db = Database(db_dsn)
    await db.connect()
    status = "completed"
    buckets_found = findings_count = 0
    try:
        await db.update_scan_status(scan_id, status="running")
        from festin.scan_runner import build_namespace, run_scan

        cli_args = build_namespace(
            quiet=True,
            no_print=True,
            scan_id=f"svc-{scan_id}",
        )
        result_obj = await run_scan(cli_args, domains)
        if result_obj is not None:
            await db.persist_scan_results(scan_id, result_obj.buckets, result_obj.findings)
        buckets_found = len(getattr(result_obj, "buckets", []) or [])
        findings_count = len(getattr(result_obj, "findings", []) or [])
    except Exception:
        logger.exception("streaq scan %s failed", scan_id)
        status = "failed"
    finally:
        await db.update_scan_status(
            scan_id,
            status=status,
            buckets_found=buckets_found,
            findings_count=findings_count,
        )
        await db.disconnect()
    return {
        "scan_id": scan_id,
        "status": status,
        "buckets_found": buckets_found,
        "findings_count": findings_count,
    }


async def run_streaq_worker(db_dsn: str | None = None) -> None:
    """Run the streaQ worker loop (blocks until interrupted).

    This is the ``festin-worker`` entry: it consumes scan jobs enqueued
    by the web service and executes them in this process. ``db_dsn``
    overrides the FESTIN_DB_DSN environment variable.
    """
    from streaq import Worker  # optional dependency (lazy import)

    if db_dsn is not None:
        os.environ[DB_DSN_ENV] = db_dsn
    worker: Any = Worker(redis_url=get_redis_url())

    @worker.task(name=SCAN_TASK_NAME)
    async def run_scan_task(scan_id: int, domain_id: int, domains: list[str]) -> dict:
        """Execute the scan pipeline; owns a private Database connection."""
        return await execute_scan_task(scan_id, domain_id, domains, get_db_dsn())

    await worker.run_async()
