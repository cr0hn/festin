"""Secret detection and content classification for discovered bucket objects.

Each rule lives in :data:`RULES` as a plain dict: ``{"id", "name", "severity",
"pattern", "description"}``. :func:`scan_content` runs every rule over the
decoded text, attributes each match to its 1-indexed line, and emits one
redacted :class:`festin.models.Finding` per unique match; full secrets never
reach the output.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Awaitable, Callable

from .models import Finding
from .s3 import S3Bucket

# 90% non-printable => binary content, not worth scanning.
_PRINTABLE_THRESHOLD = 0.90

_FETCH_FN = Callable[[str], Awaitable[bytes]]

# ---------------------------------------------------------------------------
# Rule patterns (compiled once at import time)
# ---------------------------------------------------------------------------

_AWS_ACCESS_KEY = re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b")
# 40-char base64-ish run tied to credential keywords, to avoid flagging random
# base64 blobs: `AWS_SECRET_ACCESS_KEY=...` or `aws_secret_access_key = ...`.
_AWS_SECRET_KEY = re.compile(
    r"(?i)\b(aws[\W_]{0,10}secret[\W_]{0,10}access[\W_]{0,10}key)[\W_]{0,5}"
    r"([A-Za-z0-9/+=]{40})\b"
)
_AWS_CONTEXT = re.compile(
    r"(?i)\b(aws[_-]?(?:access|secret|session|security|credential|auth)"
    r"|aws[_-]?region|aws[_-]?account[_-]?id)[a-z0-9_-]*\b"
)
_GOOGLE_API_KEY = re.compile(r"\b(AIza[0-9A-Za-z\-_]{35})\b")
_AZURE_KEY = re.compile(
    r"(?i)\b(azure[_-]?storage[_-]?(?:account[_-]?key|connection[_-]?string)|account[_-]?key)\b"
)
_AZURE_CONN = re.compile(
    r"(https://[a-z0-9-]{3,24}\.(?:blob|file|table|queue)\.core\.windows\.net"
    r"/?[^\"'\s]*AccountKey=[A-Za-z0-9/+=]{88})"
)
_PRIVATE_KEY = re.compile(
    r"(-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----"
    r"(?:[^-]|-(?!-)){32,})"
)
_JWT = re.compile(r"\b(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_.\-/+=]{10,})\b")
_GITHUB_TOKEN = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,255})\b")
_SLACK_TOKEN = re.compile(r"\b(xox[abposr]-(?:\d{1,13}-)+[0-9a-zA-Z]{10,}(?:-[0-9a-zA-Z]{10,})*)\b")
# hex(64) / base64(40+) next to a credential-ish word, so plain hashes and data
# blobs stay quiet.
_GENERIC_HIGH_ENTROPY = re.compile(
    r"(?i)\b((?:secret|password|passwd|pwd|token|api[_-]?key|apikey|access[_-]?key"
    r"|auth|credential|signature|client[_-]?secret)[a-z0-9_-]{0,12})"
    r"\s*['\"]?\s*[:=]\s*['\"]?([0-9a-f]{64}|[A-Za-z0-9+/]{40,})\b"
)
_DB_CONN = re.compile(
    r"\b((?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|rediss|mssql|oracle"
    r"|amqp)://[^\s@/:'\"<>]{1,128}:[^\s@/:'\"<>]{1,128}@[^\s'\"<>]+)\b"
)
_ENV_ASSIGNMENT = re.compile(
    r"(?im)^\s*((?:SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|AWS_SECRET_ACCESS_KEY"
    r"|ACCESS_TOKEN|AUTH_TOKEN|DB_PASS(?:WORD)?|PRIVATE_KEY|CLIENT_SECRET"
    r"|ENCRYPTION_KEY|SESSION_KEY|SIGNING_KEY)(?:[_A-Z0-9]*)\s*=\s*)"
    r"([^\s\"']{8,}|\"[^\"]{8,}\"|'[^']{8,}')"
)

RULES: tuple[dict, ...] = (
    {
        "id": "AWS-ACCESS-KEY",
        "name": "AWS access key ID",
        "severity": "critical",
        "pattern": _AWS_ACCESS_KEY,
        "description": "Amazon Web Services access key ID (AKIA/ASIA prefix).",
    },
    {
        "id": "AWS-SECRET-KEY",
        "name": "AWS secret access key",
        "severity": "critical",
        "pattern": _AWS_SECRET_KEY,
        "description": "AWS secret access key value (40-char credential).",
    },
    {
        "id": "AWS-CONTEXT",
        "name": "AWS credentials context",
        "severity": "low",
        "pattern": _AWS_CONTEXT,
        "description": "AWS credential-related configuration keyword.",
    },
    {
        "id": "GOOGLE-API-KEY",
        "name": "Google API key",
        "severity": "critical",
        "pattern": _GOOGLE_API_KEY,
        "description": "Google API key (AIza prefix).",
    },
    {
        "id": "AZURE-STORAGE-KEY",
        "name": "Azure storage key",
        "severity": "high",
        "pattern": _AZURE_KEY,
        "description": "Azure storage account key or connection-string setting.",
    },
    {
        "id": "AZURE-CONN-STRING",
        "name": "Azure storage connection string",
        "severity": "critical",
        "pattern": _AZURE_CONN,
        "description": "Azure storage connection string including AccountKey.",
    },
    {
        "id": "PRIVATE-KEY-BLOCK",
        "name": "Private key block",
        "severity": "critical",
        "pattern": _PRIVATE_KEY,
        "description": "Embedded private key (RSA/EC/OPENSSH/PGP/...).",
    },
    {
        "id": "JWT-TOKEN",
        "name": "JWT token",
        "severity": "high",
        "pattern": _JWT,
        "description": "Signed JSON Web Token (header.payload.signature).",
    },
    {
        "id": "GITHUB-TOKEN",
        "name": "GitHub token",
        "severity": "critical",
        "pattern": _GITHUB_TOKEN,
        "description": "GitHub personal access / OAuth / app token (gh*_).",
    },
    {
        "id": "SLACK-TOKEN",
        "name": "Slack token",
        "severity": "critical",
        "pattern": _SLACK_TOKEN,
        "description": "Slack API token (xoxb/xoxp/xoxa/xoxs/xoxo/xoxr).",
    },
    {
        "id": "HIGH-ENTROPY-SECRET",
        "name": "High-entropy secret",
        "severity": "medium",
        "pattern": _GENERIC_HIGH_ENTROPY,
        "description": "Credential keyword followed by a long hex/base64 secret.",
    },
    {
        "id": "DB-CONNECTION-STRING",
        "name": "Database connection string",
        "severity": "critical",
        "pattern": _DB_CONN,
        "description": "DB connection string with embedded credentials.",
    },
    {
        "id": "ENV-SECRET-ASSIGNMENT",
        "name": "Environment secret assignment",
        "severity": "medium",
        "pattern": _ENV_ASSIGNMENT,
        "description": ".env-style assignment of a secret-bearing variable.",
    },
)


def redact(raw: str) -> str:
    """Redact a match, keeping head+tail; never return the raw secret."""
    if len(raw) <= 10:
        return "..."
    return f"{raw[:6]}...{raw[-4:]}"


def _decode(content: bytes) -> str | None:
    """Decode bytes to text; None when content looks binary."""
    if not content:
        return None
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    printable = sum(ch.isprintable() or ch in "\n\r\t" for ch in text)
    if printable / len(text) < _PRINTABLE_THRESHOLD:
        return None
    return text


def _line_starts(text: str) -> list[int]:
    """Offsets where each line begins (line N starts at line_starts[N-1])."""
    starts = [0]
    for pos, ch in enumerate(text):
        if ch == "\n":
            starts.append(pos + 1)
    return starts


def _finding(rule: dict, bucket_name: str, object_key: str, line: int, snippet: str) -> Finding:
    """Build one redacted Finding for a raw match."""
    return Finding(
        bucket_name=bucket_name,
        object_key=object_key,
        rule_id=rule["id"],
        rule_name=rule["name"],
        severity=rule["severity"],
        description=rule["description"],
        line=line,
        match=redact(snippet),
    )


def scan_text(bucket_name: str, object_key: str, text: str) -> list[Finding]:
    """Run every rule over decoded text; one redacted finding per unique match.

    Dedup key is (rule_id, object_key, redacted match): identical secrets hit
    twice are reported once, at the first line where they appear.
    """
    starts = _line_starts(text)
    findings: list[Finding] = []
    seen: set[tuple[str, str, str]] = set()
    for rule in RULES:
        for match in rule["pattern"].finditer(text):
            snippet = match.group(0)
            key = (rule["id"], object_key, redact(snippet))
            if key in seen:
                continue
            seen.add(key)
            line = bisect_right(starts, match.start())
            findings.append(_finding(rule, bucket_name, object_key, line, snippet))
    return findings


def scan_content(bucket_name: str, object_key: str, content: bytes) -> list[Finding]:
    """Scan one object's bytes; returns unique redacted findings.

    Content that cannot be decoded as text (binary) or is mostly non-printable
    yields no findings. Line numbers are 1-indexed.
    """
    text = _decode(content)
    if text is None:
        return []
    return scan_text(bucket_name, object_key, text)


async def scan_bucket_objects(bucket: S3Bucket, fetch_fn: _FETCH_FN) -> list[Finding]:
    """Fetch and scan each object in ``bucket`` using ``fetch_fn(object_url)``.

    Fetch failures are swallowed: one broken object must not kill the scan.
    """
    findings: list[Finding] = []
    for key in bucket.objects:
        url = _object_url(bucket, key)
        try:
            content = await fetch_fn(url)
        except Exception:
            continue
        findings.extend(scan_content(bucket.bucket_name, key, content))
    return findings


def _object_url(bucket: S3Bucket, key: str) -> str:
    """Build the URL fetch_fn should request for one object."""
    domain = bucket.domain.rstrip("/")
    return f"https://{domain}/{key.lstrip('/')}"


__all__ = ("RULES", "redact", "scan_bucket_objects", "scan_content", "scan_text")
