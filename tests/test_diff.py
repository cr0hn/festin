"""Tests for festin.diff: bucket/object/finding comparisons between scans."""

from __future__ import annotations

from festin.diff import DiffReport, diff_results
from festin.models import Finding, ScanResult
from festin.s3 import S3Bucket


def bucket(name: str, *objects: str, domain: str = "example.com") -> S3Bucket:
    return S3Bucket(domain=domain, bucket_name=name, objects=list(objects))


def finding(bucket_name: str, key: str, rule_id: str, line: int = 1, severity: str = "high"):
    return Finding(
        bucket_name=bucket_name,
        object_key=key,
        rule_id=rule_id,
        rule_name=f"{rule_id} name",
        severity=severity,
        description="test",
        line=line,
        match="REDACTED",
    )


def scan(buckets: list[S3Bucket], findings: list[Finding] | None = None) -> ScanResult:
    return ScanResult(
        scan_id="s",
        started_at="2026-09-01T00:00:00+00:00",
        buckets=buckets,
        findings=findings or [],
    )


def test_diff_new_and_removed_buckets():
    old = scan([bucket("kept"), bucket("gone")])
    new = scan([bucket("kept"), bucket("fresh")])

    report = diff_results(old, new)

    assert [b.bucket_name for b in report.new_buckets] == ["fresh"]
    assert [b.bucket_name for b in report.removed_buckets] == ["gone"]
    assert report.changed_objects == {}
    assert report.new_findings == []


def test_diff_object_additions_and_removals():
    old = scan([bucket("bkt", "a.txt", "b.txt", "c.txt")])
    new = scan([bucket("bkt", "b.txt", "c.txt", "d.txt", "e.txt")])

    report = diff_results(old, new)

    assert report.new_buckets == []
    assert report.removed_buckets == []
    assert report.changed_objects == {"bkt": (["d.txt", "e.txt"], ["a.txt"])}


def test_diff_unchanged_bucket_not_reported():
    old = scan([bucket("bkt", "a.txt")])
    new = scan([bucket("bkt", "a.txt")])

    report = diff_results(old, new)

    assert report.changed_objects == {}
    assert report.is_empty


def test_diff_object_order_deduped_by_set():
    old = scan([bucket("bkt", "z.txt", "a.txt")])
    new = scan([bucket("bkt", "a.txt", "z.txt")])

    report = diff_results(old, new)

    assert report.changed_objects == {}


def test_diff_findings_new_and_duplicate():
    old = scan([bucket("bkt", "a.txt")], [finding("bkt", "a.txt", "aws-key", line=3)])
    new = scan(
        [bucket("bkt", "a.txt")],
        [
            finding("bkt", "a.txt", "aws-key", line=3),
            finding("bkt", "a.txt", "aws-key", line=7),
            finding("bkt", "b.txt", "gcp-token"),
        ],
    )

    report = diff_results(old, new)

    assert len(report.new_findings) == 1
    only = report.new_findings[0]
    assert (only.bucket_name, only.object_key, only.rule_id) == ("bkt", "b.txt", "gcp-token")


def test_diff_findings_ignored_when_removed_before():
    old = scan([bucket("bkt", "a.txt")], [finding("bkt", "a.txt", "aws-key")])
    new = scan([bucket("bkt", "a.txt")], [])

    report = diff_results(old, new)

    assert report.new_findings == []


def test_empty_scans_diff_is_empty():
    report = diff_results(scan([]), scan([]))

    assert isinstance(report, DiffReport)
    assert report.is_empty
    assert report.summary().startswith("Scan diff:")
    assert "no changes" in report.summary()


def test_summary_lists_changes():
    old = scan(
        [bucket("kept", "a.txt"), bucket("gone", "x.txt")],
        [finding("gone", "x.txt", "old-rule")],
    )
    new = scan(
        [bucket("kept", "a.txt", "b.txt"), bucket("fresh")],
        [finding("kept", "a.txt", "aws-key", severity="critical")],
    )

    report = diff_results(old, new)
    text = report.summary()

    assert "+ fresh" in text
    assert "- gone" in text
    assert "~ kept: +1 / -0 objects" in text
    assert "+ b.txt" in text
    assert "new findings:    1" in text
    assert "[critical] aws-key in kept/a.txt:1" in text


def test_summary_empty_has_no_changes_line():
    report = diff_results(scan([bucket("same")]), scan([bucket("same")]))

    assert report.is_empty
    assert "no changes" in report.summary()
