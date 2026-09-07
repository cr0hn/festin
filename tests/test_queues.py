"""Tests for the dual-mode queue backend (memory / streaq).

streaQ is an optional dependency: these tests mock the Worker and its
enqueue path so no live Redis (or streaq install) is required. Real-Redis
integration is intentionally out of scope — see docs/usage/configuration.md.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ===== Mode selection =====


class TestModeSelection:
    def test_default_mode_is_memory(self, monkeypatch):
        from festin.service.queues import get_queue_mode

        monkeypatch.delenv("FESTIN_QUEUE", raising=False)
        assert get_queue_mode() == "memory"

    def test_explicit_memory_mode(self, monkeypatch):
        from festin.service.queues import get_queue_mode

        monkeypatch.setenv("FESTIN_QUEUE", "memory")
        assert get_queue_mode() == "memory"

    def test_explicit_streaq_mode(self, monkeypatch):
        from festin.service.queues import get_queue_mode

        monkeypatch.setenv("FESTIN_QUEUE", "streaq")
        assert get_queue_mode() == "streaq"

    def test_mode_is_case_insensitive(self, monkeypatch):
        from festin.service.queues import get_queue_mode

        monkeypatch.setenv("FESTIN_QUEUE", "  STREAQ  ")
        assert get_queue_mode() == "streaq"

    def test_invalid_mode_falls_back_to_memory(self, monkeypatch):
        from festin.service.queues import get_queue_mode

        monkeypatch.setenv("FESTIN_QUEUE", "kafka")
        assert get_queue_mode() == "memory"

    def test_manager_takes_mode_from_env(self, monkeypatch):
        from festin.service.queues import QueueManager

        monkeypatch.setenv("FESTIN_QUEUE", "streaq")
        assert QueueManager().mode == "streaq"
        monkeypatch.delenv("FESTIN_QUEUE")
        assert QueueManager().mode == "memory"

    def test_explicit_mode_beats_env(self, monkeypatch):
        from festin.service.queues import QueueManager

        monkeypatch.setenv("FESTIN_QUEUE", "streaq")
        assert QueueManager(mode="memory").mode == "memory"

    def test_redis_url_default(self, monkeypatch):
        from festin.service.queues import DEFAULT_REDIS_URL, get_redis_url

        monkeypatch.delenv("FESTIN_REDIS_URL", raising=False)
        assert get_redis_url() == DEFAULT_REDIS_URL

    def test_redis_url_from_env(self, monkeypatch):
        from festin.service.queues import get_redis_url

        monkeypatch.setenv("FESTIN_REDIS_URL", "redis://other:6380/2")
        assert get_redis_url() == "redis://other:6380/2"


# ===== QueueManager: memory mode (default behavior unchanged) =====


class TestQueueManagerMemory:
    async def test_start_memory_mode(self):
        from festin.service.queues import QueueManager

        qm = QueueManager(mode="memory")
        await qm.start()
        assert qm.mode == "memory"
        assert qm.queue is not None
        assert qm.streaq_task is None
        await qm.stop()

    async def test_enqueue_scan_returns_none_in_memory(self):
        from festin.service.queues import QueueManager

        qm = QueueManager(mode="memory")
        await qm.start()
        assert await qm.enqueue_scan(1, 1, ["example.com"]) is None
        await qm.stop()


def _fake_worker_factory(enqueue_log: list[tuple]) -> Any:
    """Build an async streaq-worker factory whose task records enqueues."""

    async def factory(redis_url: str) -> Any:
        from festin.service.queues import StreaqBundle

        worker = MagicMock()
        registered = MagicMock()

        async def fake_enqueue(scan_id: int, domain_id: int, domains: list[str]) -> Any:
            enqueue_log.append((scan_id, domain_id, list(domains)))
            task = MagicMock()
            task.id = f"streaq-{scan_id}"
            return task

        registered.enqueue = fake_enqueue
        worker.task.return_value = lambda fn: registered
        return StreaqBundle(worker=worker, task=registered)

    return factory


class TestQueueManagerStreaq:
    async def test_streaq_start_builds_worker_and_task(self, monkeypatch):
        import festin.service.queues as qmod
        from festin.service.queues import QueueManager

        built = {}

        def factory(redis_url: str):
            built["url"] = redis_url
            return _fake_worker_factory([])(redis_url)

        monkeypatch.setattr(qmod, "build_streaq_worker", factory)
        qm = QueueManager(mode="streaq", redis_url="redis://mock:6379/0")
        await qm.start()
        assert built["url"] == "redis://mock:6379/0"
        assert qm.streaq_task is not None
        await qm.stop()
        assert qm.streaq_task is None

    async def test_streaq_enqueue_returns_task_id(self, monkeypatch):
        import festin.service.queues as qmod
        from festin.service.queues import QueueManager

        log: list[tuple] = []

        async def factory(redis_url: str) -> Any:
            return await _fake_worker_factory(log)(redis_url)

        monkeypatch.setattr(qmod, "build_streaq_worker", factory)
        qm = QueueManager(mode="streaq")
        await qm.start()
        job_id = await qm.enqueue_scan(42, 7, ["a.example.com", "b.example.com"])
        assert job_id == "streaq-42"
        assert log == [(42, 7, ["a.example.com", "b.example.com"])]
        await qm.stop()

    async def test_streaq_fallback_to_memory_when_redis_unreachable(self, monkeypatch, caplog):
        import festin.service.queues as qmod
        from festin.service.queues import QueueManager

        async def unreachable(redis_url: str) -> Any:
            raise ConnectionError("Redis unreachable")

        monkeypatch.setattr(qmod, "build_streaq_worker", unreachable)
        qm = QueueManager(mode="streaq")
        await qm.start()
        assert qm.mode == "memory", "must degrade to memory, never crash"
        assert qm.queue is not None
        await qm.stop()


# ===== Lazy import: module works without streaq installed =====


class TestLazyImport:
    def test_import_without_streaq(self, monkeypatch):
        import festin.service.queues as qmod

        monkeypatch.setitem(sys.modules, "streaq", None)
        # Memory path never touches streaq
        assert qmod.get_queue_mode({"FESTIN_QUEUE": "memory"}) == "memory"
        # streaq path raises a clear ImportError instead of crashing at import
        with pytest.raises(ImportError):
            import asyncio

            asyncio.run(qmod.build_streaq_worker("redis://localhost:6379/0"))


# ===== Router dispatch (mocked) =====


async def _register_admin(client: Any) -> dict:
    """Bootstrap the first user (admin) and return auth headers."""
    resp = await client.post(
        "/api/v1/auth/register", json={"username": "admin", "password": "strong-pass-1"}
    )
    assert resp.status == 201
    resp = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "strong-pass-1"}
    )
    token = (await resp.json())["access_token"]
    return {"Authorization": f"Bearer {token}"}


class TestRouterDispatch:
    @pytest.fixture
    async def db(self, tmp_path):
        from festin.service.database import Database

        database = Database(tmp_path / "dispatch.db")
        await database.connect()
        await database.migrate()
        yield database
        await database.disconnect()

    def _streaq_manager(self) -> Any:
        from festin.service.queues import QueueManager

        qm = QueueManager(mode="streaq")
        registered = MagicMock()

        async def fake_enqueue(scan_id: int, domain_id: int, domains: list) -> Any:
            task = MagicMock()
            task.id = f"streaq-{scan_id}"
            return task

        registered.enqueue = fake_enqueue
        qm._task = registered
        return qm

    async def test_run_scan_enqueues_via_streaq(self, db, aiohttp_client):
        """In streaq mode run-scan returns the streaq job id and does NOT
        execute the scan in-process."""
        from pathlib import Path
        from unittest.mock import patch as sync_patch

        from festin.service.serve import ServiceConfig, create_app

        qm = MagicMock()
        qm.mode = "streaq"

        async def fake_enqueue(scan_id: int, domain_id: int, domains: list) -> str:
            return f"streaq-{scan_id}"

        qm.enqueue_scan = fake_enqueue
        qm.start = AsyncMock()

        with (
            sync_patch("festin.scan_runner.run_scan", new=AsyncMock()) as mock_run,
            sync_patch(
                "festin.service.queues.QueueManager",
                lambda *a, **kw: qm,
            ),
        ):
            config = ServiceConfig(db_path=Path(db._db_path))
            app = await create_app(config)
            client = await aiohttp_client(app)
            headers = await _register_admin(client)
            resp = await client.post(
                "/api/v1/scans/run-scan",
                json={"domains": ["dispatch-test.com"]},
                headers=headers,
            )
            assert resp.status == 202
            data = await resp.json()
            assert data["status"] == "accepted"
            assert data["job_id"].startswith("streaq-")
            # Give any stray in-process task a moment; the scan must not run
            await asyncio.sleep(0.05)
            mock_run.assert_not_awaited()
            await client.close()

    async def test_run_scan_executes_in_process_in_memory_mode(self, db, aiohttp_client):
        from pathlib import Path

        from festin.service.serve import ServiceConfig, create_app

        config = ServiceConfig(db_path=Path(db._db_path))
        app = await create_app(config)
        assert app["queues"].mode == "memory"
        client = await aiohttp_client(app)
        headers = await _register_admin(client)
        resp = await client.post(
            "/api/v1/scans/run-scan",
            json={"domains": ["memory-test.com"]},
            headers=headers,
        )
        assert resp.status == 202
        data = await resp.json()
        assert data["status"] == "accepted"
        assert "job_id" in data
        await client.close()

    async def test_router_dispatch_helper_routes_streaq(self, db):
        """FestinRouter._dispatch_scan: streaq mode -> enqueue; memory -> in-process."""
        from festin.service.router import FestinRouter

        qm = self._streaq_manager()
        router = FestinRouter(database=db, queue_manager=qm)
        job_id = await router._dispatch_scan(9, 3, ["x.example.com"])
        assert job_id == "streaq-9"

    async def test_router_dispatch_helper_routes_memory(self, db):
        from unittest.mock import patch as sync_patch

        from festin.service.queues import QueueManager
        from festin.service.router import FestinRouter

        domain_id = await db.create_domain("y.example.com")
        scan_id = await db.create_scan(domain_id)
        qm = QueueManager(mode="memory")
        router = FestinRouter(database=db, queue_manager=qm)
        with sync_patch("festin.scan_runner.run_scan", new=AsyncMock()):
            job_id = await router._dispatch_scan(scan_id, domain_id, ["y.example.com"])
            assert job_id is None
            # in-process task updates the scan status (completed with mock)
            for _ in range(20):
                await asyncio.sleep(0.05)
                detail = await db.get_scan_detail(scan_id)
                if detail is not None and detail["scan"]["status"] != "running":
                    break
            assert detail is not None
            assert detail["scan"]["status"] == "completed"
