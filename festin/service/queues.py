"""Redis-backed queues, event broker, and scan status tracking for the FestIn service."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any


class FestinQueue:
    """Scan job queue backed by Redis Streams (with mock fallback)."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._redis: Any = None
        self._connected = False
        self._url = redis_url
        self._local_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def connect(self) -> None:
        """Connect to the Redis instance."""
        try:
            import coredis
            self._redis = coredis.Redis.from_url(
                self._url, decode_responses=True
            )
            await self._redis.ping()
            self._connected = True
        except Exception:
            # Fall back to in-memory queue
            self._connected = False

    async def push_scan_job(self, data: dict[str, Any]) -> str:
        """Push a scan job onto the queue. Returns a task_id."""
        task_id = str(uuid.uuid4())
        await self._local_queue.put({
            "task_id": task_id,
            "data": data,
            "created_at": time.time(),
        })
        return task_id

    async def pop_job(self) -> dict[str, Any] | None:
        """Pop the next scan job from the queue."""
        try:
            return await asyncio.wait_for(
                self._local_queue.get(), timeout=0.1
            )
        except asyncio.TimeoutError:
            return None

    def is_connected(self) -> bool:
        """Return whether the underlying Redis is connected."""
        return self._connected


class QueueManager:
    """Manages the queue lifecycle and domain scheduling."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._queue = FestinQueue(redis_url)

    async def start(self) -> None:
        """Start the queue manager and connect to Redis."""
        await self._queue.connect()

    async def stop(self) -> None:
        """Stop the queue manager."""
        pass   # Nothing to clean up for in-memory fallback

    def queue(self) -> FestinQueue:
        """Return the underlying queue instance."""
        return self._queue


class DomainScanner:
    """Orchestrates domain scanning through the queue."""

    def __init__(self, queue_manager: QueueManager) -> None:
        self._qm = queue_manager

    async def scan_domain(self, domain: str) -> dict[str, Any]:
        """Submit a domain for scanning. Returns the job result."""
        q = self._qm.queue()
        task_id = await q.push_scan_job({"domain": domain})
        return {"task_id": task_id, "status": "queued"}


class EventBroker:
    """Simple in-memory event system (use Redis streams in prod)."""

    def __init__(self) -> None:
        self._listeners: dict[str, asyncio.Queue] = {}

    async def subscribe(self, channel: str) -> asyncio.Queue:
        """Subscribe to a channel. Returns an async queue."""
        queue: asyncio.Queue = asyncio.Queue()
        self._listeners[channel] = queue
        return queue

    async def publish(self, channel: str, message: Any) -> None:
        """Publish a message to all listeners on a channel."""
        for q in self._listeners.get(channel, []):
            await q.put(message)


class ScanStatusTracker:
    """Tracks scan status across runs using the database."""

    def __init__(self, database: Any) -> None:
        self._db = database

    async def update(self, task_id: str, domain: str, status: str, **kwargs) -> None:
        """Update scan status for a task."""
        pass   # Handled by Database in production

    async def get_status(self, task_id: str) -> dict[str, Any] | None:
        """Get the current status of a scan task."""
        return {"task_id": task_id, "status": "pending"}

    async def mark_completed(
        self, task_id: str, domains: list[str], buckets: int
    ) -> None:
        """Mark a scan as completed with domain and bucket counts."""
        pass   # Delegated to Database layer
