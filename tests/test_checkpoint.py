"""Tests for checkpoint/resume (festin.checkpoint)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from festin.checkpoint import (
    SAVE_EVERY,
    Checkpoint,
    CheckpointStore,
    with_checkpoint,
)
from festin.s3 import S3Bucket

DOMAINS = ["a.com", "b.com", "c.com", "d.com"]


def _bucket(domain: str) -> S3Bucket:
    return S3Bucket(domain=domain, bucket_name=f"{domain}-bucket", objects=["x"])


async def _ok_run(domain: str) -> S3Bucket:
    return _bucket(domain)


def _log_run(log: list[str], fail_on: set[str] | None = None):
    fail_on = fail_on or set()

    async def run(domain: str) -> S3Bucket:
        log.append(domain)
        if domain in fail_on:
            raise RuntimeError(f"boom: {domain}")
        return _bucket(domain)

    return run


# --- CheckpointStore: save/load roundtrip and corruption handling ----------


async def test_save_load_roundtrip(tmp_path: Path):
    store = CheckpointStore(tmp_path / "cp.json")
    cp = Checkpoint(
        scan_id="s1",
        pending_domains=["c.com"],
        processed_domains=["a.com", "b.com"],
        results_so_far=[_bucket("a.com"), _bucket("b.com")],
    )
    await store.save(cp)
    loaded = await store.load()

    assert loaded is not None
    assert loaded.scan_id == "s1"
    assert loaded.pending_domains == ["c.com"]
    assert loaded.processed_domains == ["a.com", "b.com"]
    assert loaded.results_so_far == [_bucket("a.com"), _bucket("b.com")]
    assert loaded.version == 1


async def test_load_missing_file_returns_none(tmp_path: Path):
    assert await CheckpointStore(tmp_path / "absent.json").load() is None


async def test_load_corrupt_json_returns_none(tmp_path: Path):
    path = tmp_path / "cp.json"
    path.write_text("{not json at all", encoding="utf-8")

    assert await CheckpointStore(path).load() is None


async def test_load_schema_mismatch_returns_none(tmp_path: Path):
    path = tmp_path / "cp.json"
    path.write_text(json.dumps({"version": 999, "scan_id": "x"}), encoding="utf-8")
    assert await CheckpointStore(path).load() is None

    path.write_text(json.dumps({"version": 1, "scan_id": 42}), encoding="utf-8")
    assert await CheckpointStore(path).load() is None

    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert await CheckpointStore(path).load() is None


async def test_clear_removes_file_and_tolerates_absence(tmp_path: Path):
    store = CheckpointStore(tmp_path / "cp.json")
    await store.save(Checkpoint(scan_id="s1"))
    await store.clear()
    assert await store.load() is None
    await store.clear()  # second clear must not raise


async def test_save_is_atomic_no_tmp_leftover(tmp_path: Path):
    path = tmp_path / "cp.json"
    store = CheckpointStore(path)
    await store.save(Checkpoint(scan_id="s1"))

    assert path.exists()
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "cp.json"]
    assert leftovers == []


# --- with_checkpoint: basic flow --------------------------------------------


async def test_run_all_domains_saves_final_checkpoint(tmp_path: Path):
    store = CheckpointStore(tmp_path / "cp.json")
    log: list[str] = []

    results, cp = await with_checkpoint(DOMAINS, store, _log_run(log))

    assert log == DOMAINS
    assert [b.domain for b in results] == DOMAINS
    assert cp.processed_domains == DOMAINS
    assert cp.pending_domains == []
    stored = await store.load()
    assert stored is not None
    assert stored.processed_domains == DOMAINS


async def test_empty_pending_completes_without_calls(tmp_path: Path):
    store = CheckpointStore(tmp_path / "cp.json")
    log: list[str] = []

    results, cp = await with_checkpoint([], store, _log_run(log))

    assert results == []
    assert log == []
    assert cp.processed_domains == []
    assert cp.pending_domains == []
    assert await store.load() is not None  # final save still happens


async def test_save_every_batching(tmp_path: Path):
    store = CheckpointStore(tmp_path / "cp.json")
    domains = [f"d{i}.com" for i in range(SAVE_EVERY + 10)]
    calls = {"n": 0}
    real_save = store.save

    async def counting_save(cp: Checkpoint) -> None:
        calls["n"] += 1
        await real_save(cp)

    store.save = counting_save  # type: ignore[method-assign]

    results, cp = await with_checkpoint(domains, store, _ok_run)

    assert len(results) == len(domains)
    # one save per full batch of 50 + final save at the end
    assert calls["n"] == 1 + 1
    assert cp.processed_domains == domains


# --- crash and resume --------------------------------------------------------


async def test_crash_saves_remaining_pending(tmp_path: Path):
    store = CheckpointStore(tmp_path / "cp.json")
    log: list[str] = []

    with pytest.raises(RuntimeError, match="boom"):
        await with_checkpoint(DOMAINS, store, _log_run(log, fail_on={"c.com"}))

    assert log == ["a.com", "b.com", "c.com"]  # crashed on the 3rd domain
    stored = await store.load()
    assert stored is not None
    assert stored.processed_domains == ["a.com", "b.com"]
    assert stored.pending_domains == ["c.com", "d.com"]
    assert [b.domain for b in stored.results_so_far] == ["a.com", "b.com"]


async def test_resume_skips_processed_and_completes(tmp_path: Path):
    store = CheckpointStore(tmp_path / "cp.json")
    crash_log: list[str] = []
    with pytest.raises(RuntimeError, match="boom"):
        await with_checkpoint(DOMAINS, store, _log_run(crash_log, fail_on={"c.com"}))

    resume_log: list[str] = []
    results, cp = await with_checkpoint(DOMAINS, store, _log_run(resume_log), resume=True)

    # already-processed domains must NOT be re-run
    assert resume_log == ["c.com", "d.com"]
    assert [b.domain for b in results] == DOMAINS
    assert cp.processed_domains == DOMAINS
    assert cp.pending_domains == []
    assert cp.scan_id == crash_scan_id(await store.load(), cp)  # same run identity


def crash_scan_id(stored: Checkpoint | None, cp: Checkpoint) -> str:
    assert stored is not None
    return stored.scan_id


async def test_resume_without_checkpoint_starts_fresh(tmp_path: Path):
    store = CheckpointStore(tmp_path / "cp.json")
    log: list[str] = []

    results, cp = await with_checkpoint(DOMAINS, store, _log_run(log), resume=True)

    assert log == DOMAINS
    assert [b.domain for b in results] == DOMAINS
    assert cp.processed_domains == DOMAINS


async def test_resume_false_ignores_existing_checkpoint(tmp_path: Path):
    store = CheckpointStore(tmp_path / "cp.json")
    stale = Checkpoint(
        scan_id="stale",
        pending_domains=["z.com"],
        processed_domains=["a.com"],
        results_so_far=[_bucket("a.com")],
    )
    await store.save(stale)
    log: list[str] = []

    results, cp = await with_checkpoint(DOMAINS, store, _log_run(log), resume=False)

    assert log == DOMAINS  # full re-run, no skipping
    assert [b.domain for b in results] == DOMAINS
    assert cp.scan_id != "stale"
    assert cp.processed_domains == DOMAINS


async def test_none_result_is_allowed_but_not_stored(tmp_path: Path):
    async def none_run(domain: str) -> S3Bucket | None:
        return None if domain == "b.com" else _bucket(domain)

    store = CheckpointStore(tmp_path / "cp.json")
    results, cp = await with_checkpoint(["a.com", "b.com"], store, none_run)

    assert [b.domain for b in results] == ["a.com"]
    assert cp.processed_domains == ["a.com", "b.com"]


async def test_cancelled_error_still_saves_checkpoint(tmp_path: Path):
    store = CheckpointStore(tmp_path / "cp.json")

    async def cancelling(domain: str) -> S3Bucket:
        if domain == "b.com":
            raise asyncio.CancelledError()
        return _bucket(domain)

    with pytest.raises(asyncio.CancelledError):
        await with_checkpoint(DOMAINS, store, cancelling)

    stored = await store.load()
    assert stored is not None
    assert stored.pending_domains == ["b.com", "c.com", "d.com"]
