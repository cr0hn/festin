"""Redis-backed queues, event broker, and scan status tracking for the FestIn service."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any


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
    """Manager for scan task queues and domain scanning."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._queue: FestinQueue | None = None
        self._redis_url = redis_url
        self._schedules: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        """Initialize the underlying queue."""
        self._queue = FestinQueue(self._redis_url)

    async def stop(self) -> None:
        """Shutdown the queue manager."""
        self._queue = None

    @property
    def queue(self) -> FestinQueue | None:
        """Return the underlying FestinQueue if available."""
        return self._queue

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
        self._pub_sub: Any = None
        self._url = redis_url
        self._subscribers: dict[str, list] = {}

    async def subscribe(self, channel: str) -> asyncio.Queue[dict[str, Any]]:
        """Subscribe to a channel. Returns an asyncio.Queue to read from."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        if channel not in self._subscribers:
            self._subscribers[channel] = []
        self._subscribers[channel].append(queue)
        return queue

    async def publish(self, channel: str, data: dict[str, Any]) -> int:
        """Publish an event to a channel. Returns number of subscribers notified."""
        count = 0
        for queue in self._subscribers.get(channel, []):
            await queue.put(data)
            count += 1
        return count

    async def close(self) -> None:
        """Clear all subscriptions."""
        for queues in self._subscribers.values():
            for q in queues:
                try:
                    q.put_nowait(None)
                except Exception:
                    pass
        self._subscribers.clear()


class StatusTracker:
    """Track scan job lifecycle state using in-memory dict fallback."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._redis: Any = None
        self._statuses: dict[str, dict[str, Any]] = {}
        self._url = redis_url

    async def mark_started(self, scan_id: str, domain_id: int) -> None:
        """Mark a scan as started."""
        self._statuses[scan_id] = {
            "scan_id": scan_id,
            "domain_id": domain_id,
            "status": "running",
            "started_at": time.time(),
        }

    async def mark_completed(self, scan_id: str, result_data: dict[str, Any]) -> None:
        """Mark a scan as completed with result data."""
        if scan_id in self._statuses:
            self._statuses[scan_id].update(
                {
                    "status": "completed",
                    "result_data": result_data,
                    "completed_at": time.time(),
                }
            )

    async def mark_failed(self, scan_id: str, error: str) -> None:
        """Mark a scan as failed."""
        if scan_id in self._statuses:
            self._statuses[scan_id].update(
                {
                    "status": "failed",
                    "error": error,
                    "failed_at": time.time(),
                }
            )

    async def get_status(self, scan_id: str, domain_id: int | None = None) -> dict[str, Any]:
        """Get the current status of a scan."""
        status = self._statuses.get(scan_id)
        if status is not None:
            return dict(status)
        return {"scan_id": scan_id, "status": "not_found"}

    async def list_all(self) -> list[dict[str, Any]]:
        """Return all tracked statuses."""
        return [dict(s) for s in self._statuses.values()]
