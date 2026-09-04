"""Multi-cloud bucket probing.

Extends the AWS S3 probing concept to Azure Blob Storage, Google Cloud Storage,
DigitalOcean Spaces and Backblaze B2. Every provider is described by a
:class:`ProviderSpec` and probed the same way: fetch the public listing
endpoint, parse the XML listing and collect the object keys.
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as et
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

import httpx

from festin.s3 import S3Bucket

_AWS_HOST = re.compile(r"(?:^|\.)s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com$", re.IGNORECASE)
_AZURE_HOST = re.compile(r"(?:^|\.)blob\.core\.windows\.net$", re.IGNORECASE)
_GCS_HOST = re.compile(r"(?:^|\.)storage\.googleapis\.com$", re.IGNORECASE)
_DO_HOST = re.compile(r"(?:^|\.)digitaloceanspaces\.com$", re.IGNORECASE)
_B2_HOST = re.compile(r"(?:^|\.)s3\.[a-z0-9-]+\.backblazeb2\.com$", re.IGNORECASE)

# Fallback region for providers that require one in the public hostname.
_DO_REGION = "nyc3"
_B2_REGION = "us-west-004"


@dataclass(frozen=True)
class ProviderSpec:
    """Description of one cloud object-storage provider.

    host_pattern: regex matching the provider's public bucket hostnames.
    listing_url: URL of the XML listing endpoint for a bucket.
    probe_subdomain: canonical public hostname for a bucket.
    parse_listing: extract object keys from listing content.
    """

    name: str
    host_pattern: re.Pattern[str]
    listing_url: Callable[[str], str]
    probe_subdomain: Callable[[str], str]
    parse_listing: Callable[[bytes | str], list[str]]


def _local_tag(tag: str) -> str:
    """Return the local part of a possibly namespace-qualified XML tag."""
    return tag.rsplit("}", 1)[-1]


def _xml_keys(content: bytes | str, container_tag: str, key_tag: str) -> list[str]:
    """Extract object keys from an XML listing.

    Works for S3-style listings (``<Contents><Key>``) and Azure listings
    (``<Blob><Name>``); namespace-agnostic so GCS/DO/B2/Azure variants all parse.
    """
    root = et.fromstring(content)
    keys: list[str] = []
    for node in root.iter():
        if _local_tag(node.tag) != container_tag:
            continue
        for child in node:
            if _local_tag(child.tag) == key_tag and child.text:
                keys.append(child.text)
    return keys


_parse_s3_listing = partial(_xml_keys, container_tag="Contents", key_tag="Key")
_parse_azure_listing = partial(_xml_keys, container_tag="Blob", key_tag="Name")


def _s3_hostname(bucket: str) -> str:
    return f"{bucket}.s3.amazonaws.com"


def _azure_hostname(bucket: str) -> str:
    return f"{bucket}.blob.core.windows.net"


def _gcs_hostname(bucket: str) -> str:
    return f"{bucket}.storage.googleapis.com"


def _do_hostname(bucket: str) -> str:
    return f"{bucket}.{_DO_REGION}.digitaloceanspaces.com"


def _b2_hostname(bucket: str) -> str:
    return f"{bucket}.s3.{_B2_REGION}.backblazeb2.com"


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="aws-s3",
        host_pattern=_AWS_HOST,
        listing_url=lambda bucket: f"https://{_s3_hostname(bucket)}/",
        probe_subdomain=_s3_hostname,
        parse_listing=_parse_s3_listing,
    ),
    ProviderSpec(
        name="azure-blob",
        host_pattern=_AZURE_HOST,
        listing_url=lambda bucket: (
            f"https://{_azure_hostname(bucket)}/?restype=container&comp=list"
        ),
        probe_subdomain=_azure_hostname,
        parse_listing=_parse_azure_listing,
    ),
    ProviderSpec(
        name="gcs",
        host_pattern=_GCS_HOST,
        listing_url=lambda bucket: f"https://{_gcs_hostname(bucket)}/",
        probe_subdomain=_gcs_hostname,
        parse_listing=_parse_s3_listing,
    ),
    ProviderSpec(
        name="digitalocean",
        host_pattern=_DO_HOST,
        listing_url=lambda bucket: f"https://{_do_hostname(bucket)}/",
        probe_subdomain=_do_hostname,
        parse_listing=_parse_s3_listing,
    ),
    ProviderSpec(
        name="backblaze",
        host_pattern=_B2_HOST,
        listing_url=lambda bucket: f"https://{_b2_hostname(bucket)}/",
        probe_subdomain=_b2_hostname,
        parse_listing=_parse_s3_listing,
    ),
)


def _strip_port(host: str) -> str:
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def guess_provider_from_host(host: str) -> ProviderSpec | None:
    """Guess the cloud provider of a public bucket hostname.

    Matches ``host`` (port stripped, case-insensitive) against every provider's
    ``host_pattern``. Returns the first match, or None for unknown hosts.
    """
    hostname = _strip_port(host.strip().lower())
    for provider in PROVIDERS:
        if provider.host_pattern.search(hostname):
            return provider
    return None


async def probe_bucket(
    provider: ProviderSpec,
    bucket_name: str,
    client: httpx.AsyncClient,
    timeout: float = 5.0,
) -> S3Bucket | None:
    """Probe one provider's public listing endpoint for a bucket.

    Returns an :class:`S3Bucket` when the listing returns HTTP 200 with a
    parseable listing; None on 403/404, connection errors, timeouts or
    malformed listings. Never raises for HTTP errors.
    """
    url = provider.listing_url(bucket_name)
    try:
        response = await client.get(url, timeout=timeout)
    except httpx.HTTPError:
        return None

    if response.status_code != 200:
        return None

    try:
        keys = provider.parse_listing(response.content)
    except et.ParseError:
        return None

    return S3Bucket(
        domain=provider.probe_subdomain(bucket_name),
        bucket_name=bucket_name,
        objects=keys,
    )


async def _probe_with_sem(
    provider: ProviderSpec,
    bucket_name: str,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore | None,
) -> S3Bucket | None:
    if sem is None:
        return await probe_bucket(provider, bucket_name, client)
    async with sem:
        return await probe_bucket(provider, bucket_name, client)


async def probe_all_providers(
    bucket_name: str,
    client: httpx.AsyncClient,
    providers: tuple[ProviderSpec, ...] = PROVIDERS,
    sem: asyncio.Semaphore | None = None,
) -> list[S3Bucket]:
    """Probe ``bucket_name`` against every provider concurrently.

    Returns the successful buckets, in provider order. Errors are absorbed by
    :func:`probe_bucket` and simply drop the provider from the result.
    """
    buckets = await asyncio.gather(
        *(_probe_with_sem(p, bucket_name, client, sem) for p in providers)
    )
    return [bucket for bucket in buckets if bucket is not None]


__all__ = (
    "PROVIDERS",
    "ProviderSpec",
    "guess_provider_from_host",
    "probe_all_providers",
    "probe_bucket",
)
