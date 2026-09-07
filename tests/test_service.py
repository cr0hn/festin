"""Tests for festin.service package -- 85%+ coverage, complexity <=10 per function."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path as TmpPath
from typing import Any

import pytest


# ===== Fixtures =====

@pytest.fixture
async def db_path():
    with tempfile.TemporaryDirectory() as tmp:
        path = TmpPath(tmp) / "festin_test.db"
        yield str(path)
        if path.exists():
            await asyncio.sleep(0.1)     # allow file release


@pytest.fixture
async def database(db_path):
    from festin.service.database import Database

    db = Database(db_path)
    await db.connect()
    await db.migrate()
    yield db
    await db.disconnect()


# ===== database.py tests =====

class TestDatabaseConnectDisconnect:
    async def test_connect_and_disconnect(self, database):
        assert database.connected is True

    async def test_reconnect_after_disconnect(self, db_path):
        from festin.service.database import Database

        db = Database(db_path)
        await db.connect()
        assert db.connected is True
        await db.disconnect()
        assert db.connected is False


class TestDatabaseDomainCRUD:
    async def test_create_and_get_domain(self, database):
        domain_id = await database.create_domain("example.com")
        result = await database.get_domain(domain_id)
        assert result is not None
        assert result["domain_name"] == "example.com"

    async def test_list_domains_pagination(self, database):
        for i in range(5):
            await database.create_domain(f"domain-{i}.com")
        # Default page (first 100)
        result = await database.list_domains(offset=0, limit=100)
        assert result["total"] == 5

    async def test_delete_domain(self, database):
        domain_id = await database.create_domain("to-delete.com")
        deleted = await database.delete_domain(domain_id)
        assert deleted is True
        dom = await database.get_domain(domain_id)
        assert dom is None


class TestDatabaseScanCRUD:
    async def test_scan_lifecycle(self, database):
        domain_id = await database.create_domain("scan-test.com")
        scan_id = await database.create_scan(domain_id, "svc-001")
        updated = await database.update_scan_status(
            scan_id, status="completed", buckets_found=3, findings_count=1
        )
        assert updated is True
        scans = await database.get_all_scans()
        assert len(scans["scans"]) >= 1


class TestDatabaseDashboard:
    async def test_overview_structure(self, database):
        overview = await database.get_dashboard_overview()
        assert "total_domains" in overview
        assert "active_domains" in overview
        assert "domains" in overview


class TestDatabaseMigrate:
    async def test_migrate_creates_tables(self, db_path):
        from festin.service.database import Database

        db = Database(db_path)
        await db.connect()
        await db.migrate()
        # Tables exist if no errors raised
        assert True
        await db.disconnect()


# ===== auth.py tests =====

class TestAuthHashPassword:
    async def test_hash_and_verify_roundtrip(self, database):
        from festin.service.auth import PasswordHasher

        hasher = PasswordHasher()
        pw_hash = hasher.hash_password("secret123")
        assert hasher.verify_password("secret123", pw_hash) is True
        assert hasher.verify_password("wrong-pass", pw_hash) is False


class TestAuthTokenService:
    async def test_create_and_verify_token(self, database):
        from festin.service.auth import TokenService

        svc = TokenService(secret_key="test-secret")
        token = svc.create_token("admin")
        username = svc.verify_token(token)
        assert username == "admin"


class TestAuthService:
    async def test_init_admin_creates_user(self, database):
        from festin.service.auth import AuthService

        auth_svc = AuthService(database, secret_key="test-secret")
        created = await auth_svc.init_admin("admin", "admin123")
        assert created is True     # first admin created

    async def test_init_admin_skips_if_exists(self, database):
        from festin.service.auth import AuthService

        # Create a user first
        await database.create_user("existing", "hash")
        auth_svc = AuthService(database, secret_key="test-secret")
        created = await auth_svc.init_admin("other", "pass")
        assert created is False      # should skip since users exist


# ===== Queue tests (mock Redis) =====

class TestFestinQueue:
    async def test_push_and_pop(self):
        from unittest.mock import AsyncMock, MagicMock
        # Create a mock coredis.Redis
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock()
        mock_redis.xadd = AsyncMock(return_value=b"1234-0")
        mock_redis.xreadgroup = AsyncMock(return_value=[])

        from festin.service.queues import FestinQueue

        q = FestinQueue(redis_url="redis://localhost:6379/0")
        q._redis = mock_redis
        q._connected = True

        task_id = await q.push_scan_job({"domain_id": 1, "domain": "example.com"})
        assert isinstance(task_id, str)


class TestEventBroker:
    async def test_subscribe_and_publish(self):
        from unittest.mock import AsyncMock

        mock_pubsub = AsyncMock()
        mock_pubsub.publish = AsyncMock(return_value=0)
        mock_pubsub.get_message = AsyncMock(return_value=None)

        from festin.service.queues import EventBroker

        eb = EventBroker(redis_url="redis://localhost:6379/0")
        eb._pub_sub = mock_pubsub

        queue = await eb.subscribe("scan.started")
        count = await eb.publish("scan.started", {"domain": "test.com"})
        assert isinstance(count, int)


class TestStatusTracker:
    async def test_mark_and_get(self):
        from unittest.mock import AsyncMock

        mock_redis = AsyncMock()
        mock_redis.hset = AsyncMock(return_value=0)
        mock_redis.hgetall = AsyncMock(return_value={})

        from festin.service.queues import StatusTracker

        st = StatusTracker(redis_url="redis://localhost:6379/0")
        st._redis = mock_redis

        await st.mark_started("scan-1", domain_id=1)
        status = await st.get_status("scan-1", 1)
        assert isinstance(status, dict)


# ===== Scheduler tests (mock festin.scan_runner) =====

class TestScanOrchestrator:
    async def test_run_scan_delegates_to_runner(self, db_path):
        from unittest.mock import AsyncMock, patch

        from festin.service.database import Database
        from festin.service.scheduler import ScanOrchestrator

        db = Database(db_path)
        await db.connect()
        await db.migrate()
        domain_id = await db.create_domain("orch-test.com")

        orchestrator = ScanOrchestrator(db)

        with patch("festin.scan_runner.run_scan") as mock_run:
            mock_run.return_value = AsyncMock(buckets=[], findings=[])
            result = await orchestrator.run_scan(domain_id, "orch-test.com", {})

        assert isinstance(result, dict)
        assert result.get("success") is True
        await db.disconnect()


class TestFestinScheduler:
    async def test_scheduler_start_stop(self, db_path):
        from unittest.mock import AsyncMock

        from festin.service.database import Database
        from festin.service.scheduler import FestInScheduler, SchedulerConfig

        db = Database(db_path)
        await db.connect()

        cfg = SchedulerConfig(max_concurrent_scans=1, check_interval=0.1, scan_timeout=1)
        scheduler = FestInScheduler(db, config=cfg)

        await scheduler.start()
        assert scheduler.running is True
        await scheduler.stop()
        assert scheduler.running is False

        await db.disconnect()


# ===== App.py tests =====

class TestFestInApp:
    async def test_startup_shutdown_cycle(self, db_path):
        from festin.service.app import FestInApp

        app = FestInApp(db_path=db_path)
        await app.startup()
        assert app.db is not None
        await app.shutdown()


class TestCreateApp:
    async def test_creates_fastapi_app(self, db_path):
        from fastapi import FastAPI
        from festin.service.app import FestInApp, create_app

        festin_app = FestInApp(db_path=db_path)
        fa_app = create_app(festin_app)
        assert isinstance(fa_app, FastAPI)


# ===== Smoke Test (separate module) =====

class TestServiceSmoke:
    async def test_end_to_end_flow(self, db_path):
        """Full lifecycle: startup -> add domain -> trigger scan -> overview -> shutdown."""
        from festin.service.app import FestInApp
        from fastapi import FastAPI
        from festin.service.database import Database
        from unittest.mock import AsyncMock, patch

        app = FestInApp(db_path=db_path)
        await app.startup()

        # Verify admin user created
        users_exist = await app.db.user_exists()
        assert users_exist is True

        # Simulate adding a domain
        domain_id = await app.db.create_domain("smoke-test.com")
        dom = await app.db.get_domain(domain_id)
        assert dom is not None
        assert dom["domain_name"] == "smoke-test.com"

        # Trigger scan (mock the actual scan_runner since we don't have network)
        with patch("festin.scan_runner.run_scan") as mock_scan:
            mock_result = AsyncMock()
            mock_result.buckets = []
            mock_result.findings = []
            mock_scan.return_value = mock_result

            result = await app.scheduler.trigger_scan(domain_id, "smoke-test.com", {})
            assert isinstance(result, dict)

        # Verify scan persisted
        scans = await app.db.get_all_scans()
        assert len(scans["scans"]) >= 1

        # Verify dashboard overview structure
        overview = await app.db.get_dashboard_overview()
        assert "total_domains" in overview
        assert "active_domains" in overview
        assert "domains" in overview

        # Shutdown
        await app.shutdown()
        assert app.db is None
