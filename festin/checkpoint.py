"""Checkpoint/resume support for long-running scans.

A :class:`Checkpoint` records scan progress so a crashed or interrupted scan
can be resumed without re-scanning domains that already completed. The
:class:`CheckpointStore` persists checkpoints as JSON with atomic replaces,
and :func:`with_checkpoint` drives the resumable iteration over pending
domains.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiofiles

from .s3 import S3Bucket

CHECKPOINT_VERSION = 1
SAVE_EVERY = 50

_TMP_SUFFIX = ".tmp"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Checkpoint:
    """Progress snapshot of one scan run."""

    scan_id: str
    pending_domains: list[str] = field(default_factory=list)
    processed_domains: list[str] = field(default_factory=list)
    results_so_far: list[S3Bucket] = field(default_factory=list)
    started_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)
    version: int = CHECKPOINT_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["results_so_far"] = [asdict(bucket) for bucket in self.results_so_far]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Checkpoint | None:
        """Build a checkpoint from parsed JSON, or None on schema mismatch."""
        if not isinstance(data, dict) or data.get("version") != CHECKPOINT_VERSION:
            return None
        scan_id = data.get("scan_id")
        if not isinstance(scan_id, str) or not scan_id:
            return None
        buckets = [_bucket_from(item) for item in data.get("results_so_far", [])]
        if any(bucket is None for bucket in buckets):
            return None
        pending = _str_list(data.get("pending_domains"))
        processed = _str_list(data.get("processed_domains"))
        return cls(
            scan_id=scan_id,
            pending_domains=pending,
            processed_domains=processed,
            results_so_far=[bucket for bucket in buckets if bucket is not None],
            started_at=str(data.get("started_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


def _bucket_from(item: Any) -> S3Bucket | None:
    if not isinstance(item, dict):
        return None
    domain = item.get("domain")
    bucket_name = item.get("bucket_name")
    objects = item.get("objects", [])
    if not isinstance(domain, str) or not isinstance(bucket_name, str):
        return None
    if not isinstance(objects, list) or not all(isinstance(key, str) for key in objects):
        return None
    return S3Bucket(domain=domain, bucket_name=bucket_name, objects=objects)


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


class CheckpointStore:
    """Persists checkpoints as JSON with atomic file replaces."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    async def save(self, cp: Checkpoint) -> None:
        """Atomically write the checkpoint: tmp file in same dir, then replace."""
        cp.updated_at = _utc_now_iso()
        payload = json.dumps(cp.to_dict(), indent=2, sort_keys=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + _TMP_SUFFIX)
        async with aiofiles.open(tmp_path, "w", encoding="utf-8") as handle:
            await handle.write(payload)
        await asyncio.to_thread(os.replace, tmp_path, self.path)

    async def load(self) -> Checkpoint | None:
        """Load the stored checkpoint; returns None if missing or corrupt."""
        try:
            return await self._load_valid()
        except (OSError, ValueError):
            print(f"checkpoint: discarding unreadable file {self.path}", file=sys.stderr)
            return None

    async def _load_valid(self) -> Checkpoint | None:
        try:
            async with aiofiles.open(self.path, encoding="utf-8") as handle:
                raw = await handle.read()
        except FileNotFoundError:
            return None
        data = json.loads(raw)
        return Checkpoint.from_dict(data)

    async def clear(self) -> None:
        """Remove the stored checkpoint if it exists."""
        try:
            await asyncio.to_thread(self.path.unlink)
        except FileNotFoundError:
            pass


async def with_checkpoint(
    pending: list[str],
    store: CheckpointStore,
    run_fn: Callable[[str], Any],
    resume: bool = False,
) -> tuple[list[S3Bucket], Checkpoint]:
    """Run ``run_fn`` over ``pending`` domains with checkpoint persistence.

    With ``resume=True`` and an existing checkpoint, already-processed
    domains are skipped and their stored results are reused. A checkpoint is
    saved every :data:`SAVE_EVERY` items, at the end, and right before
    re-raising a ``run_fn`` failure (with the remaining domains still
    pending).
    """
    checkpoint = await _initial_checkpoint(pending, store, resume)
    remaining = checkpoint.pending_domains
    while remaining:
        domain = remaining[0]
        try:
            result = await run_fn(domain)
        except BaseException:
            await store.save(checkpoint)
            raise
        remaining.pop(0)
        checkpoint.processed_domains.append(domain)
        if result is not None:
            checkpoint.results_so_far.append(result)
        if len(checkpoint.processed_domains) % SAVE_EVERY == 0:
            await store.save(checkpoint)
    await store.save(checkpoint)
    return checkpoint.results_so_far, checkpoint


async def _initial_checkpoint(
    pending: list[str], store: CheckpointStore, resume: bool
) -> Checkpoint:
    if not resume:
        return Checkpoint(scan_id=uuid.uuid4().hex, pending_domains=list(pending))
    stored = await store.load()
    if stored is None:
        return Checkpoint(scan_id=uuid.uuid4().hex, pending_domains=list(pending))
    stored.pending_domains = list(stored.pending_domains)
    return stored


__all__ = ("CHECKPOINT_VERSION", "SAVE_EVERY", "Checkpoint", "CheckpointStore", "with_checkpoint")
