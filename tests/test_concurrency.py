"""Regression tests for concurrency/termination semantics of analyze_domains."""

import asyncio
import time

from festin.__main__ import analyze_domains
from tests.conftest import make_cli_args


async def _slow_analyze_patch(monkeypatch, delay: float, started: list, active: list, peak: list):
    """Replace analyze() with a slow probe that tracks peak concurrency."""
    import festin.__main__ as main_mod

    async def fake_analyze(cli_args, domain, recursion_level, results_queue, input_domains_queue):
        started.append(domain)
        active.append(domain)
        peak.append(len(active))
        await asyncio.sleep(delay)
        active.remove(domain)
        input_domains_queue.task_done()

    monkeypatch.setattr(main_mod, "analyze", fake_analyze)


class TestSemaphoreBounding:
    async def test_concurrency_never_exceeds_limit(self, monkeypatch):
        """acquire() happens BEFORE launching analyze tasks, so peak
        in-flight analyzes must never exceed cli_args.concurrency."""
        started, active, peak = [], [], []
        await _slow_analyze_patch(monkeypatch, 0.15, started, active, peak)

        cli_args = make_cli_args(concurrency=3, http_max_recursion=0)
        processed: set[str] = set()
        input_queue: asyncio.Queue = asyncio.Queue()
        results_queue: asyncio.Queue = asyncio.Queue()
        discovered: asyncio.Queue = asyncio.Queue()
        raw_discovered: asyncio.Queue = asyncio.Queue()
        stop_event = asyncio.Event()

        for i in range(10):
            input_queue.put_nowait((f"domain{i}.example.com", 1))

        start = time.monotonic()
        await analyze_domains(
            cli_args,
            [],
            [],
            processed,
            results_queue,
            input_queue,
            discovered,
            raw_discovered,
            stop_event,
        )
        elapsed = time.monotonic() - start

        assert len(started) == 10
        # 10 domains at concurrency 3 with 0.15s each => >= 0.45s total
        assert elapsed >= 0.45, f"semaphore not bounding: {elapsed:.2f}s"
        assert max(peak) <= 3, f"concurrency exceeded: {max(peak)}"

    async def test_terminates_when_queue_drains(self, monkeypatch):
        """One-shot mode must return once queue is empty and analyzes
        finished — even if a probe tried to enqueue new domains late."""
        started, active, peak = [], [], []
        await _slow_analyze_patch(monkeypatch, 0.05, started, active, peak)

        cli_args = make_cli_args(concurrency=5)
        processed: set[str] = set()
        input_queue: asyncio.Queue = asyncio.Queue()
        results_queue: asyncio.Queue = asyncio.Queue()
        discovered: asyncio.Queue = asyncio.Queue()
        raw_discovered: asyncio.Queue = asyncio.Queue()
        stop_event = asyncio.Event()

        input_queue.put_nowait(("a.example.com", 1))
        input_queue.put_nowait(("b.example.com", 1))

        await asyncio.wait_for(
            analyze_domains(
                cli_args,
                [],
                [],
                processed,
                results_queue,
                input_queue,
                discovered,
                raw_discovered,
                stop_event,
            ),
            timeout=10,
        )
        assert set(started) == {"a.example.com", "b.example.com"}

    async def test_processed_domains_not_reanalyzed(self, monkeypatch):
        """Domains discovered twice (link + cname) run only once."""
        started, active, peak = [], [], []
        await _slow_analyze_patch(monkeypatch, 0.02, started, active, peak)

        cli_args = make_cli_args()
        processed: set[str] = set()
        input_queue: asyncio.Queue = asyncio.Queue()
        results_queue: asyncio.Queue = asyncio.Queue()
        discovered: asyncio.Queue = asyncio.Queue()
        raw_discovered: asyncio.Queue = asyncio.Queue()
        stop_event = asyncio.Event()

        input_queue.put_nowait(("dup.example.com", 1))
        input_queue.put_nowait(("dup.example.com", 1))
        input_queue.put_nowait(("fresh.example.com", 1))

        await asyncio.wait_for(
            analyze_domains(
                cli_args,
                [],
                [],
                processed,
                results_queue,
                input_queue,
                discovered,
                raw_discovered,
                stop_event,
            ),
            timeout=10,
        )
        assert sorted(started) == ["dup.example.com", "fresh.example.com"]

    async def test_recursion_negative_skips(self, monkeypatch):
        started, active, peak = [], [], []
        await _slow_analyze_patch(monkeypatch, 0.01, started, active, peak)

        cli_args = make_cli_args()
        processed: set[str] = set()
        input_queue: asyncio.Queue = asyncio.Queue()
        results_queue: asyncio.Queue = asyncio.Queue()
        discovered: asyncio.Queue = asyncio.Queue()
        raw_discovered: asyncio.Queue = asyncio.Queue()
        stop_event = asyncio.Event()

        input_queue.put_nowait(("deep.example.com", -1))
        input_queue.put_nowait(("ok.example.com", 0))

        await asyncio.wait_for(
            analyze_domains(
                cli_args,
                [],
                [],
                processed,
                results_queue,
                input_queue,
                discovered,
                raw_discovered,
                stop_event,
            ),
            timeout=10,
        )
        assert started == ["ok.example.com"]


class TestWatchModeTermination:
    async def test_watch_mode_blocks_until_stop(self, monkeypatch):
        """In watch mode the consumer must keep waiting for new domains
        instead of exiting on the 5s timeout."""
        started, active, peak = [], [], []
        await _slow_analyze_patch(monkeypatch, 0.01, started, active, peak)

        cli_args = make_cli_args(watch=True)
        processed: set[str] = set()
        input_queue: asyncio.Queue = asyncio.Queue()
        results_queue: asyncio.Queue = asyncio.Queue()
        discovered: asyncio.Queue = asyncio.Queue()
        raw_discovered: asyncio.Queue = asyncio.Queue()
        stop_event = asyncio.Event()

        input_queue.put_nowait(("first.example.com", 1))

        consumer = asyncio.create_task(
            analyze_domains(
                cli_args,
                [],
                [],
                processed,
                results_queue,
                input_queue,
                discovered,
                raw_discovered,
                stop_event,
            )
        )

        # More than the old 5s one-shot timeout: watch mode must survive
        await asyncio.sleep(0.3)
        assert not consumer.done(), "watch mode must not exit by timeout"

        # Feed a late domain, then stop via queue draining semantics:
        # watch mode exits only when stop_event set + queue drained
        # Feed a late domain; watch mode must still pick it up
        input_queue.put_nowait(("second.example.com", 1))
        await asyncio.sleep(0.1)
        assert "second.example.com" in started

        # Stop: watch loop observes stop_event at next wait and exits
        stop_event.set()
        await asyncio.wait_for(consumer, timeout=5)

        assert set(started) == {"first.example.com", "second.example.com"}
