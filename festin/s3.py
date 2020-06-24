import asyncio
import xml.etree.ElementTree as et

from typing import List
from dataclasses import dataclass

import aiohttp
import filetype

FILE_TYPES = [
    filetype.is_audio,
    filetype.is_font,
    filetype.is_image,
    filetype.is_video
]


@dataclass
class S3Bucket:
    domain: str
    bucket_name: str
    objects: List[str]


def get_redirection(text: str or bytes) -> str:
    """Parse S3 XML redirection """
    root = et.fromstring(text)

    return root.find("Endpoint").text


async def download_content_and_index(
        url: str,
        bucket_name: str,
        sem: asyncio.Semaphore,
        fulltext_add_fn
):
    async with sem:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{bucket_name}/{url}") as response:
                content = await response.read()

            # Only storage non-binary files
            if not any(f(content) for f in FILE_TYPES):
                await fulltext_add_fn(bucket_name, url, content)


async def download_s3_objects(bucket: S3Bucket, fulltext_add_fn):
    sem = asyncio.Semaphore(20)

    await asyncio.gather(*[
        download_content_and_index(
            url,
            bucket.bucket_name,
            sem,
            fulltext_add_fn
        ) for url in bucket.objects
    ])


def parse_result(content: str or bytes) -> List[str]:
    """Parse S3 XML Content """
    root = et.fromstring(content)

    contents = []
    # Search contents in the bucket
    for obj in root.findall(
            "{http://s3.amazonaws.com/doc/2006-03-01/}Contents"):
        contents.append(
            obj.find("{http://s3.amazonaws.com/doc/2006-03-01/}Key").text
        )

    return contents


__all__ = ("parse_result", "S3Bucket", "get_redirection",
           "download_s3_objects")
