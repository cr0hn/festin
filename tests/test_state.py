"""Tests for festin.state: JSON persistence, merging and history queries."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from festin.models import Finding, ScanResult
from festin.s3 import S3Bucket
from festin.state import ScanState, latest_before, load_state, save_result

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def make_result(
    scan_id: str,
    finished_at: str,
    *,
    buckets: list[S3Bucket] | None = None,
    findings: list[Finding] | None = None,
    domains: list[str] | None = None,
    started_at: str | None = None,
) -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        started_at=started_at
        or (iso(datetime.fromisoformat(finished_at) - timedelta(hours=1)) if finished_at else None),
        finished_at=finished_at,
        domains=domains or [],
        buckets=buckets or [],
        findings=findings or [],
    )


def bucket(name: str, *objects: str, domain: str = "example.com") -> S3Bucket:
    return S3Bucket(domain=domain, bucket_name=name, objects=list(objects))


def finding(bucket_name: str, key: str, rule_id: str, line: int = 1) -> Finding:
    return Finding(
        bucket_name=bucket_name,
        object_key=key,
        rule_id=rule_id,
        rule_name=rule_id.title(),
        severity="high",
        description="test finding",
        line=line,
        match="REDACTED",
    )


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "state.json"


async def test_save_then_load_roundtrip(state_path: Path):
    result = make_result(
        "scan-1",
        iso(NOW),
        buckets=[bucket("bkt", "a.txt", "b.txt")],
        findings=[finding("bkt", "a.txt", "aws-key")],
        domains=["example.com"],
        started_at=iso(NOW - timedelta(hours=2)),
    )

    await save_result(result, state_path)
    loaded = await load_state(state_path)

    assert list(loaded) == ["scan-1"]
    assert loaded["scan-1"] == result


async def test_save_appends_multiple_scans(state_path: Path):
    await save_result(make_result("scan-1", iso(NOW - timedelta(days=1))), state_path)
    await save_result(make_result("scan-2", iso(NOW)), state_path)

    loaded = await load_state(state_path)
    assert set(loaded) == {"scan-1", "scan-2"}


async def test_load_missing_file_returns_empty(state_path: Path):
    assert await load_state(state_path) == {}
    assert await latest_before(state_path, iso(NOW)) is None


async def test_load_corrupt_file_returns_empty(state_path: Path):
    state_path.write_text("{not json", encoding="utf-8")
    assert await load_state(state_path) == {}


async def test_load_skips_malformed_entries(state_path: Path):
    good = make_result("scan-1", iso(NOW))
    state = {"schema": "festin/v1", "scans": [good.to_dict(), {"scan_id": 3}, "junk", None]}
    state_path.write_text(json.dumps(state), encoding="utf-8")

    loaded = await load_state(state_path)
    assert set(loaded) == {"scan-1"}


async def test_resave_merges_same_scan_id(state_path: Path):
    first = make_result(
        "scan-1",
        iso(NOW - timedelta(hours=1)),
        buckets=[bucket("bkt", "a.txt")],
        domains=["example.com"],
        started_at=iso(NOW - timedelta(hours=2)),
    )
    second = make_result(
        "scan-1",
        iso(NOW),
        buckets=[bucket("bkt", "b.txt")],
        findings=[finding("bkt", "b.txt", "aws-key")],
        domains=["other.com"],
        started_at=iso(NOW - timedelta(minutes=10)),
    )

    await save_result(first, state_path)
    await save_result(second, state_path)

    loaded = await load_state(state_path)
    assert set(loaded) == {"scan-1"}
    merged = loaded["scan-1"]
    assert merged.finished_at == iso(NOW)
    assert merged.started_at == iso(NOW - timedelta(hours=2))
    assert merged.domains == ["example.com", "other.com"]
    bucket_names = {b.bucket_name for b in merged.buckets}
    assert bucket_names == {"bkt"}
    objects = {o for b in merged.buckets for o in b.objects}
    assert objects == {"a.txt", "b.txt"}
    assert [f.rule_id for f in merged.findings] == ["aws-key"]


async def test_latest_before_picks_most_recent_finished(state_path: Path):
    older = make_result("old", iso(NOW - timedelta(days=2)))
    mid = make_result("mid", iso(NOW - timedelta(days=1)))
    newer = make_result("new", iso(NOW))
    for result in (older, mid, newer):
        await save_result(result, state_path)

    assert (await latest_before(state_path, iso(NOW))).scan_id == "new"
    assert (await latest_before(state_path, iso(NOW - timedelta(days=1)))).scan_id == "mid"
    assert (await latest_before(state_path, iso(NOW - timedelta(days=2)))).scan_id == "old"


async def test_latest_before_excludes_unfinished_and_future(state_path: Path):
    running = make_result("running", None)
    future = make_result("future", iso(NOW + timedelta(days=1)))
    past = make_result("past", iso(NOW - timedelta(hours=1)))
    for result in (running, future, past):
        await save_result(result, state_path)

    found = await latest_before(state_path, iso(NOW))
    assert found is not None
    assert found.scan_id == "past"


async def test_latest_before_no_match_returns_none(state_path: Path):
    await save_result(make_result("scan-1", iso(NOW)), state_path)
    assert await latest_before(state_path, iso(NOW - timedelta(days=1))) is None


async def test_latest_before_invalid_timestamp_returns_none(state_path: Path):
    await save_result(make_result("scan-1", iso(NOW)), state_path)
    assert await latest_before(state_path, "not-a-timestamp") is None


async def test_latest_before_accepts_naive_before(state_path: Path):
    await save_result(make_result("scan-1", iso(NOW)), state_path)
    found = await latest_before(state_path, NOW.replace(tzinfo=None).isoformat())
    assert found is not None
    assert found.scan_id == "scan-1"


async def test_file_schema_is_stable(state_path: Path):
    await save_result(make_result("scan-1", iso(NOW)), state_path)
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["schema"] == "festin/v1"
    assert isinstance(data["scans"], list)
    assert data["scans"][0]["scan_id"] == "scan-1"


async def test_scan_state_facade(state_path: Path):
    store = ScanState(state_path)
    result = make_result("scan-1", iso(NOW))

    await store.save(result)
    loaded = await store.load()
    assert list(loaded) == ["scan-1"]
    assert (await store.latest_before(iso(NOW))).scan_id == "scan-1"
