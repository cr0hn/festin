"""S3 bucket parsing and download helpers."""

import asyncio
import xml.etree.ElementTree as et
from dataclasses import dataclass, field

import filetype
import httpx

S3_XML_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"

FILE_TYPES = [
    filetype.is_audio,
    filetype.is_font,
    filetype.is_image,
    filetype.is_video,
]


@dataclass
class S3Bucket:
    domain: str
    bucket_name: str
    objects: list[str] = field(default_factory=list)


def get_redirection(text: str | bytes) -> str:
    """Parse S3 XML redirection."""
    root = et.fromstring(text)

    endpoint = root.find("Endpoint")
    if endpoint is None or endpoint.text is None:
        raise ValueError("Malformed S3 redirection: missing <Endpoint>")

    return endpoint.text


MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024  # skip objects larger than 10 MB


async def download_content_and_index(
    url: str,
    bucket_name: str,
    sem: asyncio.Semaphore,
    fulltext_add_fn,
) -> None:
    """Download one object and index it if it is a small text file."""
    async with sem:
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
            async with client.stream("GET", f"{bucket_name}/{url}") as response:
                # Only index successful, bounded, non-binary responses.
                if response.status_code != 200:
                    return

                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) > MAX_DOWNLOAD_BYTES:
                    return

                chunks: list[bytes] = []
                received = 0
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > MAX_DOWNLOAD_BYTES:
                        return
                    chunks.append(chunk)

        content = b"".join(chunks)

        # Only store non-binary files
        if not any(f(content) for f in FILE_TYPES):
            await fulltext_add_fn(bucket_name, url, content)


async def download_s3_objects(bucket: S3Bucket, fulltext_add_fn) -> None:
    sem = asyncio.Semaphore(20)

    await asyncio.gather(
        *[
            download_content_and_index(
                url,
                bucket.bucket_name,
                sem,
                fulltext_add_fn,
            )
            for url in bucket.objects
        ]
    )


def parse_result(content: str | bytes) -> list[str]:
    """Parse S3 XML listing content; returns bucket object keys."""
    root = et.fromstring(content)

    contents = []
    # Search contents in the bucket
    for obj in root.findall(f"{S3_XML_NS}Contents"):
        key = obj.find(f"{S3_XML_NS}Key")
        if key is not None and key.text:
            contents.append(key.text)

    return contents


__all__ = ("parse_result", "S3Bucket", "get_redirection", "download_s3_objects")
