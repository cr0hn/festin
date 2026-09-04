"""S3 bucket parsing helpers."""

import xml.etree.ElementTree as et
from dataclasses import dataclass, field

S3_XML_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


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


__all__ = ("parse_result", "S3Bucket", "get_redirection")
