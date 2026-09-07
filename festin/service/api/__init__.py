"""festin/service/api - Route injection and dependency getters."""

from __future__ import annotations

from typing import Any

_db_instance: Any = None
_scheduler_instance: Any = None


def set_db(db: Any) -> None:
    global _db_instance
    _db_instance = db


async def get_db() -> Any:
    if _db_instance is None:
        raise RuntimeError("Database not configured. Call create_app first.")
    return _db_instance


def set_scheduler(scheduler: Any) -> None:
    global _scheduler_instance
    _scheduler_instance = scheduler


def get_scheduler() -> Any:
    if _scheduler_instance is None:
        raise RuntimeError("Scheduler not configured. Call create_app first.")
    return _scheduler_instance
