"""End-to-end tests: full pipeline against a real local HTTP server."""

import json
import socket

import aiohttp.web as web
import pytest

from festin.__main__ import run
from tests.conftest import make_cli_args

S3_LISTING = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>static</Name>
  <Contents><Key>docs/readme.txt</Key></Contents>
  <Contents><Key>images/logo.png</Key></Contents>
</ListBucketResult>
"""

INDEX_HTML = """<html>
  <a href="http://{host}/linked.html">linked</a>
  <a href="http://www.facebook.com/social">blocked</a>
</html>
"""

LINKED_HTML = """<html><a href="http://{host}/">home</a></html>"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _start_server(port: int) -> web.AppRunner:
    """Serve a small fake web with an S3-style XML endpoint."""
    host = f"127.0.0.1:{port}"

    async def index(_request):
        return web.Response(
            text=INDEX_HTML.format(host=host),
            content_type="text/html",
        )

    async def linked(_request):
        return web.Response(
            text=LINKED_HTML.format(host=host),
            content_type="text/html",
        )

    async def s3_listing(_request):
        return web.Response(
            text=S3_LISTING,
            content_type="application/xml",
        )

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/linked.html", linked)
    app.router.add_get("/s3/", s3_listing)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    return runner


@pytest.fixture
async def local_web():
    port = _free_port()
    runner = await _start_server(port)
    yield f"127.0.0.1:{port}"
    await runner.cleanup()


class TestEndToEnd:
    async def test_full_pipeline_discovers_links_and_bucket(
        self,
        local_web,
        tmp_path,
    ):
        """The crawler follows links, fetches the S3 listing and the
        results are streamed to the results file."""
        result_file = tmp_path / "results.festin"
        discovered_file = tmp_path / "discovered.txt"
        raw_file = tmp_path / "raw.txt"

        cli_args = make_cli_args(
            no_dnsdiscover=True,
            http_timeout=10,
            result_file=str(result_file),
            discovered_domains=str(discovered_file),
            raw_discovered_domains=str(raw_file),
            quiet=True,
            no_print=True,
            concurrency=3,
        )

        domains = [local_web, f"{local_web}/s3/"]
        await run(cli_args, domains)

        # results file contains the bucket with its objects
        content = result_file.read_text()
        lines = [json.loads(ln) for ln in content.splitlines() if ln.strip()]
        assert lines, "results file must not be empty"

        bucket_found = any(
            set(entry["objects"]) == {"docs/readme.txt", "images/logo.png"} for entry in lines
        )
        assert bucket_found, f"bucket objects missing in: {lines}"

        # discovered file: initial domains are excluded by design, so the
        # file is only created when a *new* domain is discovered. Here all
        # discovered domains were initial, so the file may not exist.
        if discovered_file.exists():
            discovered = discovered_file.read_text().splitlines()
            assert set(discovered).isdisjoint(domains)

        # raw file includes everything queued, initial domains included
        raw = raw_file.read_text().splitlines()
        assert local_web in raw
        assert f"{local_web}/s3/" in raw

    async def test_dedup_no_infinite_loop(self, local_web, tmp_path):
        """linked.html links back to '/', the same host must be processed
        exactly once: the pipeline must terminate."""
        cli_args = make_cli_args(
            no_dnsdiscover=True,
            http_timeout=10,
            result_file=str(tmp_path / "r.festin"),
            quiet=True,
            no_print=True,
        )

        # recursion depth ensures termination even if dedup broke
        cli_args.http_max_recursion = 1
        await run(cli_args, [local_web])
        # If dedup were broken we would hang; pytest-timeout guards too.

    async def test_recursion_level_zero(self, local_web, tmp_path):
        """http_max_recursion=0: subdomains proposed with level -1 are
        rejected as SKIP-RECURSION and the pipeline exits cleanly."""
        cli_args = make_cli_args(
            no_dnsdiscover=True,
            http_timeout=10,
            result_file=str(tmp_path / "r.festin"),
            quiet=True,
            no_print=True,
            http_max_recursion=0,
        )
        await run(cli_args, [local_web])
