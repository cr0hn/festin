"""Unit tests for S3 XML parsing and S3Bucket dataclass."""

import xml.etree.ElementTree as et

import pytest

from festin.s3 import S3Bucket, get_redirection, parse_result

S3_LISTING = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>example-bucket</Name>
  <Contents>
    <Key>docs/file1.txt</Key>
  </Contents>
  <Contents>
    <Key>images/photo.png</Key>
  </Contents>
</ListBucketResult>
"""

S3_EMPTY_LISTING = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>empty-bucket</Name>
</ListBucketResult>
"""

S3_REDIRECT = """<?xml version="1.0" encoding="UTF-8"?>
<Error>
  <Code>PermanentRedirect</Code>
  <Endpoint>example-bucket.s3.eu-west-1.amazonaws.com</Endpoint>
</Error>
"""


class TestParseResult:
    def test_parses_object_keys(self):
        assert parse_result(S3_LISTING) == ["docs/file1.txt", "images/photo.png"]

    def test_accepts_bytes(self):
        assert parse_result(S3_LISTING.encode()) == [
            "docs/file1.txt",
            "images/photo.png",
        ]

    def test_empty_bucket_returns_empty_list(self):
        assert parse_result(S3_EMPTY_LISTING) == []

    def test_malformed_xml_raises(self):
        with pytest.raises(et.ParseError):
            parse_result("<not-xml")


class TestGetRedirection:
    def test_extracts_endpoint(self):
        assert get_redirection(S3_REDIRECT) == "example-bucket.s3.eu-west-1.amazonaws.com"

    def test_missing_endpoint_raises(self):
        with pytest.raises(ValueError, match="missing <Endpoint>"):
            get_redirection("<Error><Code>PermanentRedirect</Code></Error>")


class TestS3Bucket:
    def test_dataclass_fields(self):
        bucket = S3Bucket(domain="d", bucket_name="b", objects=["k"])
        assert bucket.objects == ["k"]

    def test_default_objects_not_shared(self):
        """Two default buckets must not share the same list instance."""
        a = S3Bucket(domain="d", bucket_name="b")
        b = S3Bucket(domain="d", bucket_name="b")
        a.objects.append("x")
        assert b.objects == []
