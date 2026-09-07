"""PostgreSQL backend tests for the dual-backend Database.

Skipped entirely unless FESTIN_TEST_PG_DSN points at a disposable
PostgreSQL database, e.g.::

    FESTIN_TEST_PG_DSN=postgres://festin:festin@localhost:5432/festin_test \
        uv run pytest tests/test_database_pg.py --timeout=30 -q

The DSN-targeted database is dropped and recreated by the module fixture,
so never point it at data you care about.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("FESTIN_TEST_PG_DSN"),
        reason="FESTIN_TEST_PG_DSN not set; PostgreSQL backend not under test",
    ),
]

PG_DSN = os.environ.get("FESTIN_TEST_PG_DSN", "")


# ===== Fixtures =====


@pytest.fixture
async def db():
    """Connect to a fresh PostgreSQL database for every test."""
    from festin.service.database import Database

    db = Database(PG_DSN)
    await db.connect()
    await _drop_all(db)
    await db.migrate()
    yield db
    await db.disconnect()


async def _drop_all(db: Any) -> None:
    """Drop every FestIn table so migrate() starts from scratch."""
    await db._conn.execute(
        "DROP TABLE IF EXISTS scheduled_scans, buckets, findings, scans, "
        "users, domains, projects CASCADE"
    )


class _Bucket:
    """Shape-compatible stand-in for festin.s3.S3Bucket."""

    def __init__(self, domain: str, bucket_name: str, objects: list[str]):
        self.domain = domain
        self.bucket_name = bucket_name
        self.objects = objects


class _Finding:
    """Shape-compatible stand-in for festin.models.Finding."""

    def __init__(self, bucket: str, obj: str, rule: str, sev: str, line: int):
        self.bucket_name = bucket
        self.object_key = obj
        self.rule_id = rule
        self.severity = sev
        self.line = line
        self.match = "AKIA****EXAMPLE"


# ===== Lifecycle =====


class TestLifecycle:
    async def test_migrate_idempotent(self, db):
        await db.migrate()
        await db.migrate()
        projects = await db.list_projects()
        assert projects["total"] == 1
        assert projects["projects"][0]["id"] == 1


# ===== Projects =====


class TestProjects:
    async def test_create_list_update_delete(self, db):
        pid = await db.create_project("acme", "red team")
        got = await db.get_project(pid)
        assert got["name"] == "acme"
        assert got["description"] == "red team"

        await db.update_project(pid, name="acme-lab", description=None)
        got = await db.get_project(pid)
        assert got["name"] == "acme-lab"

        assert await db.delete_project(pid) is True
        assert await db.get_project(pid) is None

    async def test_duplicate_name_raises(self, db):
        await db.create_project("dup")
        with pytest.raises(ValueError):
            await db.create_project("dup")

    async def test_domain_scoped_to_project(self, db):
        pid = await db.create_project("scoped")
        await db.create_domain("a.example", pid)
        await db.create_domain("b.example", pid)
        await db.create_domain("c.example")  # default project

        listing = await db.list_domains(project_id=pid)
        names = {row["domain_name"] for row in listing["domains"]}
        assert names == {"a.example", "b.example"}
        assert listing["total"] == 2

    async def test_delete_project_cascades(self, db):
        pid = await db.create_project("cascade")
        did = await db.create_domain("x.example", pid)
        sid = await db.create_scan(did)
        await db.update_scan_status(sid, status="completed", buckets_found=1, findings_count=2)
        assert await db.delete_project(pid) is True
        assert await db.get_domain(did) is None
        detail = await db.get_scan_detail(sid)
        assert detail is None

    async def test_project_counts(self, db):
        pid = await db.create_project("counted")
        did = await db.create_domain("count.example", pid)
        sid = await db.create_scan(did)
        await db.update_scan_status(sid, status="completed", buckets_found=3, findings_count=5)
        got = await db.get_project(pid)
        assert got["domain_count"] == 1
        assert got["scan_count"] == 1
        assert got["findings_count"] == 5


# ===== Scan result persistence =====


class TestPersistResults:
    async def test_persist_scan_results(self, db):
        did = await db.create_domain("p.example")
        sid = await db.create_scan(did)
        buckets = [
            _Bucket("p.example", "leak-1", ["a.txt", "b.txt"]),
            _Bucket("p.example", "leak-2", []),
        ]
        findings = [
            _Finding("leak-1", "a.txt", "aws-keys", "critical", 12),
            _Finding("leak-1", "b.txt", "aws-keys", "high", 4),
        ]
        await db.persist_scan_results(sid, buckets, findings)

        detail = await db.get_scan_detail(sid)
        assert detail["scan"]["buckets_found"] == 2
        assert detail["scan"]["findings_count"] == 2
        assert len(detail["buckets"]) == 2
        assert detail["buckets"][0]["name"] == "leak-1"
        assert len(detail["findings"]) == 2
        row = detail["findings"][0]
        assert row["bucket"] == "leak-1"
        assert row["object"] == "a.txt"
        assert row["rule"] == "aws-keys"
        assert row["severity"] == "critical"
        assert row["match"] == "AKIA****EXAMPLE"

    async def test_persist_empty_lists(self, db):
        did = await db.create_domain("e.example")
        sid = await db.create_scan(did)
        await db.persist_scan_results(sid, [], [])
        detail = await db.get_scan_detail(sid)
        assert detail["findings"] == []
        assert detail["buckets"] == []


# ===== Scan listing / filters =====


class TestScanListing:
    async def _seed(self, db: Any) -> tuple[int, int, int, int]:
        pid = await db.create_project("proj")
        did = await db.create_domain("s.example", pid)
        s1 = await db.create_scan(did)
        await db.update_scan_status(s1, status="completed")
        s2 = await db.create_scan(did)
        await db.update_scan_status(s2, status="failed")
        return pid, did, s1, s2

    async def test_list_scans_join_fields(self, db):
        pid, _, _, _ = await self._seed(db)
        data = await db.list_scans()
        assert data["total"] >= 2
        row = data["scans"][0]
        assert row["domain_name"] == "s.example"
        assert row["project_id"] == pid
        assert row["project_name"] == "proj"

    async def test_list_scans_filters(self, db):
        pid, _, _, _ = await self._seed(db)
        done = await db.list_scans(project_id=pid, status="completed")
        assert done["total"] == 1
        assert done["scans"][0]["status"] == "completed"
        failed = await db.list_scans(status="failed")
        assert failed["total"] == 1

    async def test_get_scan_detail_404(self, db):
        assert await db.get_scan_detail(999999) is None


# ===== Users =====


class TestUserManagement:
    async def test_list_and_role_update(self, db):
        uid = await db.create_user("bob", "h", "viewer")
        users = await db.list_users()
        assert any(u["username"] == "bob" and u["role"] == "viewer" for u in users)

        assert await db.set_user_role(uid, "admin") is True
        user = await db.get_user(uid)
        assert user["role"] == "admin"

    async def test_delete_user(self, db):
        uid = await db.create_user("carl", "h")
        assert await db.delete_user(uid) is True
        assert await db.get_user(uid) is None

    async def test_count_admins(self, db):
        await db.create_user("adm1", "h", "admin")
        await db.create_user("adm2", "h", "admin")
        await db.create_user("viewer1", "h", "viewer")
        assert await db.count_admins() == 2


# ===== Stats endpoint data =====


class TestStats:
    async def test_stats_shape_with_timeline(self, db):
        did = await db.create_domain("st.example")
        sid = await db.create_scan(did)
        await db.update_scan_status(sid, status="completed", buckets_found=1, findings_count=1)
        await db.persist_scan_results(sid, [], [])
        stats = await db.get_stats()
        assert stats["scan_count"] == 1
        assert stats["findings"]["medium"] == 0
        assert stats["findings"]["low"] == 0
        assert isinstance(stats["recent_scans"], list)
        for row in stats["recent_scans"]:
            assert set(row) == {"day", "scans", "buckets", "findings", "critical", "high"}

    async def test_stats_severity_counts(self, db):
        did = await db.create_domain("sev.example")
        sid = await db.create_scan(did)
        findings = [
            _Finding("b1", "o1", "r1", "critical", 1),
            _Finding("b1", "o2", "r2", "medium", 2),
        ]
        await db.persist_scan_results(sid, [_Bucket("d", "b1", [])], findings)
        stats = await db.get_stats()
        assert stats["findings"]["critical"] == 1
        assert stats["findings"]["medium"] == 1
        assert stats["findings"]["high"] == 0
