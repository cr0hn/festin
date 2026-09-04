"""Tests for festin.secrets: rule coverage, redaction, dedup, binary skip."""

from __future__ import annotations

import re

import pytest

from festin.models import SEVERITY_ORDER, Finding
from festin.s3 import S3Bucket
from festin.secrets import RULES, redact, scan_bucket_objects, scan_content, scan_text

# ---------------------------------------------------------------------------
# Fake-but-realistic credentials, one per rule category
# ---------------------------------------------------------------------------

FAKE_SECRETS = {
    "AWS-ACCESS-KEY": "AKIAIOSFODNN7EXAMPLE",
    "AWS-SECRET-KEY": "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "AWS-CONTEXT": "aws_session_token",
    "GOOGLE-API-KEY": "AIzaSyA1234567890abcdefghijklmnopqrstuv",
    "AZURE-STORAGE-KEY": "azure_storage_account_key",
    ("AZURE-CONN-STRING"): "https://mystorageacc.blob.core.windows.net/container?AccountKey="
    + "a" * 88,
    "PRIVATE-KEY-BLOCK": (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIICXQIBAAKBgQC7JKoKAtZrHlnKSvMk1WcWToGmRnXoHmCjLzQP0wFhA2K1dumE\n"
        "-----END RSA PRIVATE KEY-----"
    ),
    "JWT-TOKEN": (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c"
    ),
    "GITHUB-TOKEN": "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "SLACK-TOKEN": "xoxb-123456789012-1234567890123-" + "AbCdEfGhIjKlMnOpQrStUvWx",
    "HIGH-ENTROPY-SECRET": "client_secret = "
    + "9f2c7a1e8b4d3f6a5c0e9b8d7a2f4c6e1b3d5a7f9c0e2b4d6f8a1c3e5b7d9f2a4",
    "DB-CONNECTION-STRING": "postgres://admin:S3cr3tP4ss@db.internal.example.com:5432/prod",
    "ENV-SECRET-ASSIGNMENT": "PASSWORD=sup3r-s3cr3t-valu3",
}


def _secret_for(rule_id: str) -> str:
    return FAKE_SECRETS[rule_id]


RULE_IDS = {rule["id"] for rule in RULES}


def _findings_for_rule(findings: list[Finding], rule_id: str) -> list[Finding]:
    return [f for f in findings if f.rule_id == rule_id]


def _hit_with_match(hits: list[Finding], match: str) -> Finding:
    assert hits, f"no finding with redacted match {match!r}"
    return next(f for f in hits if f.match == match)


# ---------------------------------------------------------------------------
# RULES table sanity
# ---------------------------------------------------------------------------


def test_rules_table_shape():
    assert len(RULES) >= 12
    for rule in RULES:
        assert set(rule) == {"id", "name", "severity", "pattern", "description"}
        assert rule["severity"] in SEVERITY_ORDER
        assert isinstance(rule["pattern"], re.Pattern)
        assert rule["id"] in RULE_IDS


# ---------------------------------------------------------------------------
# Per-category detection with correct severity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", sorted(RULE_IDS))
def test_each_rule_fires_with_expected_severity(rule_id: str):
    rule = next(r for r in RULES if r["id"] == rule_id)
    secret = _secret_for(rule_id)
    findings = scan_content("mybucket", "notes.txt", secret.encode("utf-8"))

    assert findings, f"rule {rule_id} did not trigger on its sample"
    hits = _findings_for_rule(findings, rule_id)
    assert hits, f"expected {rule_id} in {sorted({f.rule_id for f in findings})}"
    secret_snippet = rule["pattern"].search(secret).group(0)
    hit = _hit_with_match(hits, redact(secret_snippet))
    assert hit.severity == rule["severity"]
    assert hit.bucket_name == "mybucket"
    assert hit.object_key == "notes.txt"
    assert hit.line == 1


# ---------------------------------------------------------------------------
# Benign content and binary content
# ---------------------------------------------------------------------------


def test_benign_content_produces_no_findings():
    benign = (
        "# Project README\n"
        "Welcome to the example project.\n"
        "python version = 3.13\n"
        "see docs at https://example.com/docs\n"
        "total files: 42\n"
        "contact: someone@example.com\n"
    )
    assert scan_content("b", "readme.md", benign.encode()) == []


def test_binary_content_skipped():
    binary = bytes(range(256)) * 4
    assert scan_content("b", "image.bin", binary) == []


def test_empty_content_skipped():
    assert scan_content("b", "empty.txt", b"") == []


def test_latin1_fallback_still_scans():
    content = "café " + FAKE_SECRETS["AWS-ACCESS-KEY"]
    findings = scan_content("b", "k.txt", content.encode("latin-1"))
    assert any(f.rule_id == "AWS-ACCESS-KEY" for f in findings)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_redaction_keeps_head_and_tail_only():
    secret = FAKE_SECRETS["AWS-ACCESS-KEY"]
    content = f"key: {secret}\n".encode()
    findings = scan_content("b", "k.txt", content)

    assert len(findings) == 1
    assert findings[0].match == redact(secret)
    assert "..." in findings[0].match
    assert secret not in findings[0].match
    assert len(findings[0].match) < len(secret)


def test_no_finding_ever_contains_the_full_secret():
    blob = "\n".join(FAKE_SECRETS.values()).encode()
    findings = scan_content("b", "all.txt", blob)
    assert findings
    for finding in findings:
        assert "..." in finding.match
        for raw_secret in FAKE_SECRETS.values():
            assert raw_secret not in finding.match


def test_redact_short_values():
    assert redact("short") == "..."
    assert redact("0123456789X") == "012345...789X"


# ---------------------------------------------------------------------------
# Line numbers
# ---------------------------------------------------------------------------


def test_line_numbers_are_one_indexed():
    secret = FAKE_SECRETS["GITHUB-TOKEN"]
    content = (f"line one\nline two\ntoken here: {secret}\nline four\n").encode()

    findings = scan_content("b", "k.txt", content)

    assert len(findings) == 1
    assert findings[0].line == 3


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_same_secret_reported_once_per_rule():
    secret = FAKE_SECRETS["AWS-ACCESS-KEY"]
    content = (f"first: {secret}\nsecond: {secret}\nthird: {secret}\n").encode()

    findings = scan_content("b", "k.txt", content)

    access = [f for f in findings if f.rule_id == "AWS-ACCESS-KEY"]
    assert len(access) == 1
    assert access[0].line == 1


def test_different_secrets_same_rule_both_reported():
    first = "AKIAIOSFODNN7EXAMPLE"
    second = "AKIAI44QH8DHBEXAMPLE"
    content = f"a: {first}\nb: {second}\n".encode()

    access = [f for f in scan_content("b", "k.txt", content) if f.rule_id == "AWS-ACCESS-KEY"]
    assert {f.match for f in access} == {redact(first), redact(second)}


def test_dedup_key_includes_rule_id_and_match():
    secret = FAKE_SECRETS["GITHUB-TOKEN"]
    findings = scan_text("b", "k.txt", f"{secret}\n{secret}\n")
    github = [f for f in findings if f.rule_id == "GITHUB-TOKEN"]
    assert len(github) == 1


# ---------------------------------------------------------------------------
# scan_bucket_objects
# ---------------------------------------------------------------------------


async def test_scan_bucket_objects_aggregates_findings():
    bucket = S3Bucket(domain="example.s3.amazonaws.com", bucket_name="example")
    bucket.objects = ["creds.txt", "empty.bin", "notes.txt"]
    payloads = {
        "https://example.s3.amazonaws.com/creds.txt": (
            f"AWS_ACCESS_KEY_ID={FAKE_SECRETS['AWS-ACCESS-KEY']}\n".encode()
        ),
        "https://example.s3.amazonaws.com/empty.bin": b"",
        "https://example.s3.amazonaws.com/notes.txt": b"nothing to see here\n",
    }

    async def fetch_fn(url: str) -> bytes:
        return payloads[url]

    findings = await scan_bucket_objects(bucket, fetch_fn)

    access = [f for f in findings if f.rule_id == "AWS-ACCESS-KEY"]
    assert len(access) == 1
    assert access[0].object_key == "creds.txt"
    assert access[0].bucket_name == "example"
    assert all(f.match != FAKE_SECRETS["AWS-ACCESS-KEY"] for f in findings)


async def test_scan_bucket_objects_swallows_fetch_errors():
    bucket = S3Bucket(domain="example.s3.amazonaws.com", bucket_name="example")
    bucket.objects = ["broken.txt", "good.txt"]

    async def fetch_fn(url: str) -> bytes:
        if "broken" in url:
            raise OSError("boom")
        return f"token {FAKE_SECRETS['GITHUB-TOKEN']}\n".encode()

    findings = await scan_bucket_objects(bucket, fetch_fn)
    assert [f.rule_id for f in findings] == ["GITHUB-TOKEN"]


async def test_scan_bucket_objects_dedups_across_objects():
    """Same secret in two objects: (rule_id, object_key, match) differs -> 2 hits."""
    bucket = S3Bucket(domain="example.s3.amazonaws.com", bucket_name="example")
    bucket.objects = ["a.txt", "b.txt"]
    secret_line = b"AKIAIOSFODNN7EXAMPLE\n"

    async def fetch_fn(url: str) -> bytes:
        return secret_line

    findings = await scan_bucket_objects(bucket, fetch_fn)
    assert len(findings) == 2
    assert {f.object_key for f in findings} == {"a.txt", "b.txt"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_object_url_building():
    bucket = S3Bucket(domain="b.s3.amazonaws.com/", bucket_name="b")
    bucket.objects = ["x.txt"]
    seen_urls: list[str] = []

    async def fetch_fn(url: str) -> bytes:
        seen_urls.append(url)
        return b""

    import asyncio

    asyncio.run(scan_bucket_objects(bucket, fetch_fn))
    assert seen_urls == ["https://b.s3.amazonaws.com/x.txt"]
