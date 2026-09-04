"""Persistent scan state: save and load scan history as a single JSON file.

The file schema is intentionally stable so older festin versions can read
newer state files:

    {"schema": "festin/v1", "scans": [ScanResult.to_dict(), ...]}

No SQLite, no extra dependencies: just ``aiofiles`` + stdlib ``json``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import aiofiles

from .models import SCHEMA_VERSION, ScanResult
from .s3 import S3Bucket

__all__ = ("SCHEMA_VERSION", "ScanState", "latest_before", "load_state", "save_result")


def _empty_state() -> dict:
    return {"schema": SCHEMA_VERSION, "scans": []}


def _parse_state(content: str) -> dict:
    """Parse raw file content; never raises, falls back to an empty state."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return _empty_state()
    if not isinstance(data, dict) or not isinstance(data.get("scans"), list):
        return _empty_state()
    return {"schema": SCHEMA_VERSION, "scans": data["scans"]}


async def _read_state(path: Path) -> dict:
    try:
        async with aiofiles.open(path, encoding="utf-8") as fh:
            content = await fh.read()
    except FileNotFoundError:
        return _empty_state()
    return _parse_state(content)


def _merge_existing(prior: dict, result: ScanResult) -> ScanResult:
    """Merge a re-saved scan_id into its previous entry (watch/incremental mode)."""
    old = ScanResult.from_dict(prior)
    buckets = {b.bucket_name: b for b in old.buckets}
    for bucket in result.buckets:
        previous = buckets.get(bucket.bucket_name)
        if previous is None:
            buckets[bucket.bucket_name] = bucket
            continue
        merged = list(dict.fromkeys(previous.objects + bucket.objects))
        buckets[bucket.bucket_name] = S3Bucket(bucket.domain, bucket.bucket_name, merged)

    findings = {(f.bucket_name, f.object_key, f.rule_id, f.line): f for f in old.findings}
    for finding in result.findings:
        findings.setdefault(
            (finding.bucket_name, finding.object_key, finding.rule_id, finding.line), finding
        )

    started = result.started_at
    if old.started_at and old.started_at < started:
        started = old.started_at
    return ScanResult(
        scan_id=result.scan_id,
        started_at=started,
        finished_at=result.finished_at or old.finished_at,
        domains=list(dict.fromkeys(old.domains + result.domains)),
        buckets=list(buckets.values()),
        findings=list(findings.values()),
    )


def _upsert(scans: list, result: ScanResult) -> list[dict]:
    """Return the scan list with ``result`` merged under its scan_id."""
    kept: list[dict] = []
    replaced = False
    for entry in scans:
        if isinstance(entry, dict) and entry.get("scan_id") == result.scan_id:
            if not replaced:
                kept.append(_merge_existing(entry, result).to_dict())
                replaced = True
            continue
        kept.append(entry)
    if not replaced:
        kept.append(result.to_dict())
    return kept


async def save_result(result: ScanResult, path: Path) -> None:
    """Persist ``result`` into the state file at ``path``, keyed by scan_id."""
    path = Path(path)
    state = await _read_state(path)
    state["scans"] = _upsert(state["scans"], result)
    async with aiofiles.open(path, "w", encoding="utf-8") as fh:
        await fh.write(json.dumps(state, indent=2, sort_keys=True))


async def load_state(path: Path) -> dict[str, ScanResult]:
    """Load every stored scan as ``{scan_id: ScanResult}``.

    Corrupt or unknown entries are skipped so one bad record cannot poison
    the whole history.
    """
    state = await _read_state(Path(path))
    results: dict[str, ScanResult] = {}
    for entry in state["scans"]:
        if not isinstance(entry, dict):
            continue
        try:
            result = ScanResult.from_dict(entry)
        except (TypeError, KeyError, ValueError):
            continue
        results[result.scan_id] = result
    return results


def _parse_ts(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp; naive values are assumed UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


async def latest_before(path: Path, before: str) -> ScanResult | None:
    """Most recent finished scan whose ``finished_at`` <= ``before`` ISO stamp.

    Returns ``None`` when no stored scan qualifies (or ``before`` is not a
    valid ISO timestamp).
    """
    cutoff = _parse_ts(before)
    if cutoff is None:
        return None
    results = await load_state(path)
    candidates: list[tuple[datetime, ScanResult]] = []
    for result in results.values():
        finished = _parse_ts(result.finished_at)
        if finished is not None and finished <= cutoff:
            candidates.append((finished, result))
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0])[1]


class ScanState:
    """Thin object facade over the module-level state helpers."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    async def save(self, result: ScanResult) -> None:
        await save_result(result, self.path)

    async def load(self) -> dict[str, ScanResult]:
        return await load_state(self.path)

    async def latest_before(self, before: str) -> ScanResult | None:
        return await latest_before(self.path, before)
