"""Regression tests for the deep-audit fixes."""

import pytest
import respx
from httpx import Response

from festin.analysis import _bucket_probe_url, get_s3
from festin.utils import valid_domain_or_link

S3_LISTING = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>example-bucket</Name>
  <Contents><Key>file.txt</Key></Contents>
</ListBucketResult>
"""


class TestBucketProbeUrl:
    """The old slicing (domain[:find('s3')-1]) corrupted domains."""

    def test_s3_at_start_not_corrupted(self):
        # Old code: 's3.example.com' -> 'http://s3.example.com/s3.example.co'
        assert _bucket_probe_url("s3.example.com") == "https://s3.example.com/"

    def test_s3_in_label(self):
        # Old code built 'http://s3.example.com/my' for 'my-s3.example.com'
        assert _bucket_probe_url("my-s3.example.com") == "https://my-s3.example.com/"

    def test_aws_virtual_hosted(self):
        assert _bucket_probe_url("b.s3.amazonaws.com") == "https://b.s3.amazonaws.com/"

    def test_regional_aws(self):
        assert (
            _bucket_probe_url("b.s3.eu-west-1.amazonaws.com")
            == "https://b.s3.eu-west-1.amazonaws.com/"
        )

    def test_plain_domain_uses_path_style_guess(self):
        assert _bucket_probe_url("example.com") == "https://s3.amazonaws.com/example.com/"


class TestGetS3ProbesProviderDomains:
    """Domains with 's3' in them must be probed, not silently dropped
    (old dead-branch built a bogus URL and never fetched anything)."""

    async def test_s3_subdomain_is_probed(self, cli_args, input_queue, results_queue):
        with respx.mock:
            respx.get("https://data.s3.example.org/").mock(
                return_value=Response(200, text=S3_LISTING),
            )
            await get_s3(cli_args, "data.s3.example.org", 3, input_queue, results_queue)

        assert results_queue.qsize() == 1
        bucket = results_queue.get_nowait()
        assert bucket.objects == ["file.txt"]


class TestBlacklistBoundaries:
    """endswith()/startswith() matched at wrong boundaries: it blocked
    'xgoogle.com' and 'cdnfoo.com', and let 'google.com.evil.io' pass."""

    @pytest.mark.parametrize(
        "domain",
        [
            "xgoogle.com",
            "notgoogle.com",
            "google.com.evil.io",
            "cdnfoo.com",
        ],
    )
    def test_similar_but_distinct_domains_allowed(self, domain):
        assert valid_domain_or_link(domain) is None

    @pytest.mark.parametrize(
        "domain",
        [
            "google.com",
            "www.google.com",
            "cdn.jsdelivr.net",
            "cdnjs.cloudflare.com",
        ],
    )
    def test_true_blacklisted_still_blocked(self, domain):
        assert valid_domain_or_link(domain) is not None
