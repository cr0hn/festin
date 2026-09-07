"""Tests for the scan watchdog: stale running scans get marked failed."""

from __future__ import annotations

import time

import pytest


@pytest.fixture
async def db():
    import tempfile

    from festin.service.database import Database

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(f"{tmp}/watchdog.db")
        await db.connect()
        await db.migrate()
        yield db
        await db.disconnect()


@pytest.fixture
def scheduler(db):
    from festin.service.scheduler import FestInScheduler, SchedulerConfig

    cfg = SchedulerConfig(check_interval=0.05, scan_timeout=600.0)
    return FestInScheduler(db, cfg)


class TestWatchdog:
    async def test_stale_scan_marked_failed(self, db, scheduler):
        did = await db.create_domain("w.example")
        sid = await db.create_scan(did)
        # backdate started_at beyond the timeout
        old_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
        await db._conn.execute(
            "UPDATE scans SET status='running', started_at=? WHERE id=?",
            (old_ts, sid),
        )
        await db._conn.commit()

        await scheduler._reap_stale_scans(db)

        detail = await db.get_scan_detail(sid)
        assert detail["scan"]["status"] == "failed"

    async def test_fresh_running_scan_untouched(self, db, scheduler):
        did = await db.create_domain("fresh.example")
        sid = await db.create_scan(did)
        await db.update_scan_status(sid, status="running")

        await scheduler._reap_stale_scans(db)

        detail = await db.get_scan_detail(sid)
        assert detail["scan"]["status"] == "running"

    async def test_completed_scans_untouched(self, db, scheduler):
        did = await db.create_domain("c.example")
        sid = await db.create_scan(did)
        old_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
        await db._conn.execute(
            "UPDATE scans SET status='completed', started_at=?",
            (old_ts,),
        )
        await db._conn.commit()

        await scheduler._reap_stale_scans(db)

        detail = await db.get_scan_detail(sid)
        assert detail["scan"]["status"] == "completed"

    async def test_no_stale_scans_is_noop(self, db, scheduler):
        # must not raise on empty database
        await scheduler._reap_stale_scans(db)
