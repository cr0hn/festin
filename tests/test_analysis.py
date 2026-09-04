"""Unit tests for the analysis pipeline (S3, links, DNS) with mocked IO."""

import pytest
import respx
from httpx import Response

from festin.analysis import (
    BucketRedirectException,
    build_http_client,
    get_bucket_info,
    get_links,
    get_s3,
)
from festin.s3 import S3Bucket

S3_LISTING = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>example-bucket</Name>
  <Contents><Key>file.txt</Key></Contents>
</ListBucketResult>
"""

S3_REDIRECT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Error>
  <Code>PermanentRedirect</Code>
  <Endpoint>example-bucket.s3.eu-west-1.amazonaws.com</Endpoint>
</Error>
"""


class TestBuildHttpClient:
    async def test_no_tor_no_proxy(self, cli_args):
        async with build_http_client(cli_args) as client:
            assert client._transport is not None

    async def test_tor_uses_socks5(self, cli_args):
        cli_args.tor = True
        # Must not raise during construction
        client = build_http_client(cli_args)
        await client.aclose()


class TestGetBucketInfo:
    async def test_yields_bucket_on_listing(self, cli_args):
        with respx.mock:
            respx.get("http://buckets.example.com").mock(
                return_value=Response(200, text=S3_LISTING),
            )
            buckets = [
                b
                async for b in get_bucket_info(
                    cli_args,
                    "example.com",
                    "buckets.example.com",
                )
            ]

        assert len(buckets) == 1
        assert buckets[0].objects == ["file.txt"]

    async def test_redirect_raises(self, cli_args):
        with respx.mock:
            respx.get("https://s3.amazonaws.com/bucket").mock(
                return_value=Response(301, text=S3_REDIRECT_XML),
            )
            with pytest.raises(BucketRedirectException) as exc_info:
                async for _ in get_bucket_info(
                    cli_args,
                    "example.com",
                    "https://s3.amazonaws.com/bucket",
                ):
                    pass

        assert exc_info.value.redirection == "example-bucket.s3.eu-west-1.amazonaws.com"

    async def test_empty_bucket_yields_nothing(self, cli_args):
        empty = """<?xml version="1.0"?>
        <ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
          <Name>b</Name>
        </ListBucketResult>"""
        with respx.mock:
            respx.get("http://b.example.com").mock(
                return_value=Response(200, text=empty),
            )
            buckets = [
                b
                async for b in get_bucket_info(
                    cli_args,
                    "example.com",
                    "b.example.com",
                )
            ]

        assert buckets == []


class TestGetS3:
    async def test_aws_bucket_via_get_s3(self, cli_args, input_queue, results_queue):
        with respx.mock:
            respx.get(url__startswith="https://s3.amazonaws.com/example.com").mock(
                return_value=Response(200, text=S3_LISTING),
            )
            await get_s3(
                cli_args,
                "example.com",
                3,
                input_queue,
                results_queue,
            )

        assert results_queue.qsize() == 1
        bucket = results_queue.get_nowait()
        assert isinstance(bucket, S3Bucket)
        assert bucket.objects == ["file.txt"]

    async def test_redirect_enqueues_new_domain(
        self,
        cli_args,
        input_queue,
        results_queue,
    ):
        with respx.mock:
            respx.get(url__startswith="https://s3.amazonaws.com/example.com").mock(
                return_value=Response(301, text=S3_REDIRECT_XML),
            )
            await get_s3(
                cli_args,
                "example.com",
                3,
                input_queue,
                results_queue,
            )

        assert input_queue.qsize() == 1
        domain, recursion = input_queue.get_nowait()
        assert domain == "example-bucket.s3.eu-west-1.amazonaws.com"
        assert recursion == 2  # decremented

    async def test_s3_subdomain_provider_url(self, cli_args, input_queue, results_queue):
        """Domains containing 's3' build a provider URL and probe it."""
        with respx.mock:
            respx.get(url__startswith="http://s3.example.org").mock(
                return_value=Response(200, text=S3_LISTING),
            )
            await get_s3(
                cli_args,
                "data.s3.example.org",
                3,
                input_queue,
                results_queue,
            )

        # get_s3 does not yield for provider-style URLs (no get_bucket_info
        # call in that branch), queue stays empty, no crash
        assert results_queue.empty()
        assert input_queue.empty()

    async def test_nonexistent_domain_swallows_error(
        self,
        cli_args,
        input_queue,
        results_queue,
    ):
        with respx.mock:
            respx.get(url__startswith="https://s3.amazonaws.com/nope.invalid").mock(
                return_value=Response(404),
            )
            # Must not raise
            await get_s3(
                cli_args,
                "nope.invalid",
                3,
                input_queue,
                results_queue,
            )
        assert results_queue.empty()


class TestGetLinks:
    HTML_PAGE = """<html>
        <a href="https://other.example.com/page">x</a>
        <a href="https://google.com">blocked</a>
        <img src="/relative.png">
        <a href="https://cdn.example.com/a">blocked-prefix</a>
        <a href="https://repeat.example.com/1">1</a>
        <a href="https://repeat.example.com/2">2</a>
    </html>"""

    async def test_extracts_new_domains_and_dedupes(
        self,
        cli_args,
        input_queue,
        results_queue,
    ):
        with respx.mock:
            respx.get("http://example.com").mock(
                return_value=Response(
                    200,
                    text=self.HTML_PAGE,
                    headers={"Content-Type": "text/html"},
                ),
            )
            respx.get("https://example.com").mock(
                return_value=Response(404),
            )
            await get_links(
                cli_args,
                "example.com",
                3,
                input_queue,
                results_queue,
            )

        enqueued = set()
        while not input_queue.empty():
            domain, level = input_queue.get_nowait()
            enqueued.add(domain)
            assert level == 2

        assert "other.example.com" in enqueued
        assert "repeat.example.com" in enqueued
        # blacklist filtered, relative links skipped
        assert "google.com" not in enqueued
        assert "cdn.example.com" not in enqueued

    async def test_xml_listing_reports_bucket(
        self,
        cli_args,
        input_queue,
        results_queue,
    ):
        with respx.mock:
            respx.get("http://example.com").mock(
                return_value=Response(
                    200,
                    content=S3_LISTING.encode(),
                    headers={"Content-Type": "application/xml"},
                ),
            )
            respx.get("https://example.com").mock(
                return_value=Response(404),
            )
            await get_links(
                cli_args,
                "example.com",
                3,
                input_queue,
                results_queue,
            )

        assert results_queue.qsize() == 1
        bucket = results_queue.get_nowait()
        assert bucket.bucket_name == "http://example.com"

    async def test_connection_error_is_silent(
        self,
        cli_args,
        input_queue,
        results_queue,
    ):
        with respx.mock:
            respx.get("http://down.example.com").mock(
                side_effect=Exception("boom"),
            )
            respx.get("https://down.example.com").mock(
                side_effect=Exception("boom"),
            )
            # Must not raise
            await get_links(
                cli_args,
                "down.example.com",
                3,
                input_queue,
                results_queue,
            )
        assert input_queue.empty()
