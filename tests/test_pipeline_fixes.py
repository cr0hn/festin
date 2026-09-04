"""Regression tests for filtered-discovered stream and download bounds."""

import asyncio

import respx
from httpx import Response

import festin.__main__ as main_mod
from festin.__main__ import analyze_domains
from festin.s3 import download_content_and_index
from tests.conftest import make_cli_args

BIG_CONTENT = b"x" * (1024 * 1024)  # 1 MB


def _queues():
    return (
        asyncio.Queue(),
        asyncio.Queue(),
        asyncio.Queue(),
        asyncio.Queue(),
    )


class TestDiscoveredStreamRespectsFilters:
    """'discovered' must only contain domains that passed all filters.
    Old code emitted before regex/black/white checks."""

    async def test_regex_filtered_domain_not_in_discovered(self, monkeypatch):
        import re

        started = []

        async def fake_analyze(cli_args, domain, level, results_q, input_q):
            started.append(domain)
            input_q.task_done()

        monkeypatch.setattr(main_mod, "analyze", fake_analyze)

        cli_args = make_cli_args(
            domain_regex=re.compile(r"good"),
        )
        input_q, results_q, discovered_q, raw_q = _queues()
        stop = asyncio.Event()

        input_q.put_nowait(("good.example.com", 1))
        input_q.put_nowait(("bad.example.com", 1))

        await analyze_domains(
            cli_args,
            [],
            [],
            set(),
            results_q,
            input_q,
            discovered_q,
            raw_q,
            stop,
        )

        assert started == ["good.example.com"]
        discovered = []
        while not discovered_q.empty():
            discovered.append(discovered_q.get_nowait())
        assert discovered == ["good.example.com"]

    async def test_blacklisted_domain_not_in_discovered(self, monkeypatch):
        started = []

        async def fake_analyze(cli_args, domain, level, results_q, input_q):
            started.append(domain)
            input_q.task_done()

        monkeypatch.setattr(main_mod, "analyze", fake_analyze)

        cli_args = make_cli_args()
        input_q, results_q, discovered_q, raw_q = _queues()
        stop = asyncio.Event()

        input_q.put_nowait(("www.google.com", 1))  # FLD-blacklisted
        input_q.put_nowait(("ok.example.com", 1))

        await analyze_domains(
            cli_args,
            [],
            [],
            set(),
            results_q,
            input_q,
            discovered_q,
            raw_q,
            stop,
        )

        assert started == ["ok.example.com"]
        discovered = []
        while not discovered_q.empty():
            discovered.append(discovered_q.get_nowait())
        assert discovered == ["ok.example.com"]

    async def test_raw_stream_still_contains_filtered(self, monkeypatch):
        """Raw (-ra) keeps pre-filter contents: that is its contract."""

        async def fake_analyze(cli_args, domain, level, results_q, input_q):
            input_q.task_done()

        monkeypatch.setattr(main_mod, "analyze", fake_analyze)

        cli_args = make_cli_args()
        input_q, results_q, discovered_q, raw_q = _queues()
        stop = asyncio.Event()

        input_q.put_nowait(("www.google.com", 1))
        input_q.put_nowait(("ok.example.com", 1))

        await analyze_domains(
            cli_args,
            [],
            [],
            set(),
            results_q,
            input_q,
            discovered_q,
            raw_q,
            stop,
        )

        raw = []
        while not raw_q.empty():
            raw.append(raw_q.get_nowait())
        assert set(raw) == {"www.google.com", "ok.example.com"}


class TestDownloadBounds:
    """download_content_and_index must not buffer unbounded responses."""

    async def test_oversized_content_length_skipped(self):
        indexed = []

        async def add_fn(bucket, path, content):
            indexed.append((bucket, path, content))

        with respx.mock:
            respx.get(url__startswith="http://b.example.com").mock(
                return_value=Response(
                    200,
                    content=b"",
                    headers={"Content-Length": str(100 * 1024 * 1024)},
                ),
            )
            sem = asyncio.Semaphore(2)
            await download_content_and_index(
                "big.bin",
                "http://b.example.com",
                sem,
                add_fn,
            )

        assert indexed == []

    async def test_small_text_indexed(self):
        indexed = []

        async def add_fn(bucket, path, content):
            indexed.append((bucket, path, content))

        with respx.mock:
            respx.get(url__startswith="http://b.example.com").mock(
                return_value=Response(200, content=b"hello world"),
            )
            sem = asyncio.Semaphore(2)
            await download_content_and_index(
                "notes.txt",
                "http://b.example.com",
                sem,
                add_fn,
            )

        assert len(indexed) == 1
        assert indexed[0][2] == b"hello world"

    async def test_non_200_skipped(self):
        indexed = []

        async def add_fn(bucket, path, content):
            indexed.append((bucket, path, content))

        with respx.mock:
            respx.get(url__startswith="http://b.example.com").mock(
                return_value=Response(403),
            )
            sem = asyncio.Semaphore(2)
            await download_content_and_index(
                "denied.txt",
                "http://b.example.com",
                sem,
                add_fn,
            )

        assert indexed == []


class TestAnalyzeErrorLogging:
    async def test_probe_crash_does_not_poison_pipeline(self, monkeypatch, capsys):
        """If a probe raises, analyze must log (debug), call task_done and
        release the semaphore — the pipeline keeps going."""
        from festin.analysis import BucketRedirectException
        from festin.s3 import S3Bucket

        async def fake_get_s3(cli_args, domain, level, in_q, res_q):
            raise RuntimeError("probe exploded")

        async def fake_get_links(cli_args, domain, level, in_q, res_q):
            await res_q.put(S3Bucket(domain=domain, bucket_name="b", objects=["o"]))

        async def fake_get_dns(cli_args, domain, level, in_q):
            raise BucketRedirectException("x")

        monkeypatch.setattr(main_mod, "get_s3", fake_get_s3)
        monkeypatch.setattr(main_mod, "get_links", fake_get_links)
        monkeypatch.setattr(main_mod, "get_dns_info", fake_get_dns)

        cli_args = make_cli_args(debug=True)
        input_q, results_q = asyncio.Queue(), asyncio.Queue()
        input_q.put_nowait(("example.com", 1))
        input_q.get_nowait()  # simulate the consumer get() before analyze

        await main_mod.analyze(cli_args, "example.com", 1, results_q, input_q)

        # queue finished (task_done called) and results from healthy probe kept
        assert results_q.qsize() == 1
        out = capsys.readouterr().out
        assert "ANALYZE-ERROR" in out
        assert "probe exploded" in out
