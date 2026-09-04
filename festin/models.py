"""Shared data models for scan results, findings and exports.

Every feature module imports from here; only core maintainers edit it.
If you need a change to these models, message the coordinator agent first.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .s3 import S3Bucket

SCHEMA_VERSION = "festin/v1"

SEVERITY_ORDER = ("critical", "high", "medium", "low")


@dataclass
class Finding:
    """One secret/classification hit found in a bucket object."""

    bucket_name: str
    object_key: str
    rule_id: str
    rule_name: str
    severity: str  # critical | high | medium | low
    description: str
    line: int
    match: str  # redacted snippet


@dataclass
class ScanResult:
    """Full outcome of one scan run, version-serializable."""

    scan_id: str
    started_at: str  # ISO-8601 UTC
    finished_at: str | None = None
    domains: list[str] = field(default_factory=list)
    buckets: list[S3Bucket] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["schema"] = SCHEMA_VERSION
        return data

    @classmethod
    def from_dict(cls, data: dict) -> ScanResult:
        data = dict(data)
        data.pop("schema", None)
        buckets = [S3Bucket(**b) for b in data.pop("buckets", [])]
        findings = [Finding(**f) for f in data.pop("findings", [])]
        return cls(buckets=buckets, findings=findings, **data)


__all__ = ("SCHEMA_VERSION", "SEVERITY_ORDER", "Finding", "ScanResult")
