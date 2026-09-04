"""Tests for festin.cloud_providers — multi-cloud bucket probing."""

import asyncio

import httpx
import pytest
import respx

from festin.cloud_providers import (
    PROVIDERS,
    guess_provider_from_host,
    probe_all_providers,
    probe_bucket,
)
from festin.s3 import S3Bucket

AWS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>acme-backups</Name>
  <Contents>
    <Key>backups/dump-2026-01.sql.gz</Key>
  </Contents>
  <Contents>
    <Key>backups/manifest.json</Key>
  </Contents>
</ListBucketResult>
"""

AZURE_XML = """<?xml version="1.0" encoding="utf-8"?>
<EnumerationResults
    xmlns="http://schemas.microsoft.com/windowsazure/">
  <ContainerName>acme-exports</ContainerName>
  <Blobs>
    <Blob>
      <Name>exports/orders.csv</Name>
      <Properties>
        <Content-Length>1024</Content-Length>
      </Properties>
    </Blob>
    <Blob>
      <Name>exports/customers.parquet</Name>
    </Blob>
  </Blobs>
</EnumerationResults>
"""

GCS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://doc.s3.amazonaws.com/2006-03-01">
  <Name>acme-static</Name>
  <Contents>
    <Key>static/logo.png</Key>
  </Contents>
</ListBucketResult>
"""

DO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>acme-media</Name>
  <Contents>
    <Key>media/video-720.mp4</Key>
  </Contents>
</ListBucketResult>
"""

B2_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>acme-archive</Name>
  <Contents>
    <Key>archive/2025.tar.zst</Key>
  </Contents>
</ListBucketResult>
"""

XML_BY_PROVIDER: dict[str, str] = {
    "aws-s3": AWS_XML,
    "azure-blob": AZURE_XML,
    "gcs": GCS_XML,
    "digitalocean": DO_XML,
    "backblaze": B2_XML,
}

# provider name -> (listing host, path)
LISTING_TARGET: dict[str, str] = {
    "aws-s3": "https://acme-bucket.s3.amazonaws.com/",
    "azure-blob": ("https://acme-bucket.blob.core.windows.net/?restype=container&comp=list"),
    "gcs": "https://acme-bucket.storage.googleapis.com/",
    "digitalocean": "https://acme-bucket.nyc3.digitaloceanspaces.com/",
    "backblaze": "https://acme-bucket.s3.us-west-004.backblazeb2.com/",
}

PROVIDER_BY_NAME = {p.name: p for p in PROVIDERS}


def mock_listing(provider_name: str, status_code: int, body: str | None = None) -> None:
    """Register one respx route for a provider's listing endpoint."""
    respx.get(LISTING_TARGET[provider_name]).respond(
        status_code=status_code,
        text=body if body is not None else XML_BY_PROVIDER[provider_name],
    )


# --------------------------------------------------------------------- probes


@pytest.mark.respx
@respx.mock
async def test_probe_bucket_aws_s3_success():
    mock_listing("aws-s3", 200)
    spec = PROVIDER_BY_NAME["aws-s3"]
    async with httpx.AsyncClient() as client:
        bucket = await probe_bucket(spec, "acme-bucket", client)

    assert bucket == S3Bucket(
        domain="acme-bucket.s3.amazonaws.com",
        bucket_name="acme-bucket",
        objects=["backups/dump-2026-01.sql.gz", "backups/manifest.json"],
    )


@pytest.mark.respx
@respx.mock
async def test_probe_bucket_azure_success_with_namespace():
    mock_listing("azure-blob", 200)
    spec = PROVIDER_BY_NAME["azure-blob"]
    async with httpx.AsyncClient() as client:
        bucket = await probe_bucket(spec, "acme-bucket", client)

    assert bucket is not None
    assert bucket.domain == "acme-bucket.blob.core.windows.net"
    assert bucket.objects == ["exports/orders.csv", "exports/customers.parquet"]


@pytest.mark.respx
@respx.mock
@pytest.mark.parametrize("provider_name", ["gcs", "digitalocean", "backblaze"])
async def test_probe_bucket_xml_providers_success(provider_name):
    mock_listing(provider_name, 200)
    spec = PROVIDER_BY_NAME[provider_name]
    expected_domain = {
        "gcs": "acme-bucket.storage.googleapis.com",
        "digitalocean": "acme-bucket.nyc3.digitaloceanspaces.com",
        "backblaze": "acme-bucket.s3.us-west-004.backblazeb2.com",
    }[provider_name]

    async with httpx.AsyncClient() as client:
        bucket = await probe_bucket(spec, "acme-bucket", client)

    assert bucket is not None
    assert bucket.domain == expected_domain
    assert bucket.bucket_name == "acme-bucket"
    assert len(bucket.objects) == 1


@pytest.mark.respx
@respx.mock
@pytest.mark.parametrize("provider_name", [p.name for p in PROVIDERS])
@pytest.mark.parametrize("status_code", [403, 404])
async def test_probe_bucket_http_error_returns_none(provider_name, status_code):
    mock_listing(provider_name, status_code, body="AccessDenied")
    spec = PROVIDER_BY_NAME[provider_name]
    async with httpx.AsyncClient() as client:
        bucket = await probe_bucket(spec, "acme-bucket", client)
    assert bucket is None


@pytest.mark.respx
@respx.mock
async def test_probe_bucket_connection_error_returns_none():
    respx.get(LISTING_TARGET["aws-s3"]).mock(side_effect=httpx.ConnectError("refused"))
    spec = PROVIDER_BY_NAME["aws-s3"]
    async with httpx.AsyncClient() as client:
        bucket = await probe_bucket(spec, "acme-bucket", client)
    assert bucket is None


@pytest.mark.respx
@respx.mock
async def test_probe_bucket_timeout_returns_none():
    respx.get(LISTING_TARGET["gcs"]).mock(side_effect=httpx.ReadTimeout("timed out"))
    spec = PROVIDER_BY_NAME["gcs"]
    async with httpx.AsyncClient() as client:
        bucket = await probe_bucket(spec, "acme-bucket", client)
    assert bucket is None


@pytest.mark.respx
@respx.mock
async def test_probe_bucket_malformed_xml_returns_none():
    mock_listing("backblaze", 200, body="<ListBucketResult><Contents>")
    spec = PROVIDER_BY_NAME["backblaze"]
    async with httpx.AsyncClient() as client:
        bucket = await probe_bucket(spec, "acme-bucket", client)
    assert bucket is None


@pytest.mark.respx
@respx.mock
async def test_probe_all_providers_collects_successes_in_provider_order():
    for name in XML_BY_PROVIDER:
        mock_listing(name, 200)
    async with httpx.AsyncClient() as client:
        buckets = await probe_all_providers("acme-bucket", client)

    assert [b.domain for b in buckets] == [
        "acme-bucket.s3.amazonaws.com",
        "acme-bucket.blob.core.windows.net",
        "acme-bucket.storage.googleapis.com",
        "acme-bucket.nyc3.digitaloceanspaces.com",
        "acme-bucket.s3.us-west-004.backblazeb2.com",
    ]


@pytest.mark.respx
@respx.mock
async def test_probe_all_providers_skips_failures():
    mock_listing("aws-s3", 200)
    mock_listing("azure-blob", 403)
    for name in ("gcs", "digitalocean", "backblaze"):
        respx.get(LISTING_TARGET[name]).mock(side_effect=httpx.ConnectError("unmocked provider"))
    # Only aws-s3 succeeds; azure 403s; the rest fail to connect.
    async with httpx.AsyncClient() as client:
        buckets = await probe_all_providers("acme-bucket", client)

    assert [b.domain for b in buckets] == ["acme-bucket.s3.amazonaws.com"]


@pytest.mark.respx
@respx.mock
async def test_probe_all_providers_respects_semaphore():
    for name in XML_BY_PROVIDER:
        mock_listing(name, 200)
    sem = asyncio.Semaphore(2)
    async with httpx.AsyncClient() as client:
        buckets = await probe_all_providers("acme-bucket", client, sem=sem)

    assert len(buckets) == len(PROVIDERS)


# ------------------------------------------------------------- host guessing


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("mybucket.s3.amazonaws.com", "aws-s3"),
        ("mybucket.s3.eu-west-1.amazonaws.com", "aws-s3"),
        ("mybucket.s3.amazonaws.com:443", "aws-s3"),
        ("mybucket.blob.core.windows.net", "azure-blob"),
        ("mybucket.storage.googleapis.com", "gcs"),
        ("mybucket.nyc3.digitaloceanspaces.com", "digitalocean"),
        ("mybucket.s3.us-west-004.backblazeb2.com", "backblaze"),
        ("S3.US-WEST-004.BACKBLAZEB2.COM", "backblaze"),
        ("example.com", None),
        ("evil.amazonaws.com.attacker.io", None),
        ("blob.core.windows.net.evil.io", None),
    ],
)
def test_guess_provider_from_host(host, expected):
    provider = guess_provider_from_host(host)
    assert (provider.name if provider else None) == expected
