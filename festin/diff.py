"""Diff two scans: buckets gained/lost, objects added/removed, new findings."""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Finding, ScanResult
from .s3 import S3Bucket

__all__ = ("DiffReport", "diff_results")


@dataclass
class DiffReport:
    """Outcome of comparing an old scan against a new one."""

    new_buckets: list[S3Bucket] = field(default_factory=list)
    removed_buckets: list[S3Bucket] = field(default_factory=list)
    # bucket_name -> (added_objects, removed_objects)
    changed_objects: dict[str, tuple[list[str], list[str]]] = field(default_factory=dict)
    new_findings: list[Finding] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True when the two scans are equivalent."""
        return not (
            self.new_buckets or self.removed_buckets or self.changed_objects or self.new_findings
        )

    def summary(self) -> str:
        """Human-readable multiline summary of the diff."""
        lines = ["Scan diff:"]
        lines.append(f"  new buckets:     {len(self.new_buckets)}")
        for bucket in self.new_buckets:
            lines.append(f"    + {bucket.bucket_name} ({bucket.domain})")
        lines.append(f"  removed buckets: {len(self.removed_buckets)}")
        for bucket in self.removed_buckets:
            lines.append(f"    - {bucket.bucket_name} ({bucket.domain})")
        lines = self._objects_section(lines)
        lines = self._findings_section(lines)
        if self.is_empty:
            lines.append("  no changes")
        return "\n".join(lines)

    def _objects_section(self, lines: list[str]) -> list[str]:
        lines.append(f"  buckets with object changes: {len(self.changed_objects)}")
        for name, (added, removed) in self.changed_objects.items():
            lines.append(f"    ~ {name}: +{len(added)} / -{len(removed)} objects")
            lines.extend(f"      + {key}" for key in added)
            lines.extend(f"      - {key}" for key in removed)
        return lines

    def _findings_section(self, lines: list[str]) -> list[str]:
        lines.append(f"  new findings:    {len(self.new_findings)}")
        for finding in self.new_findings:
            lines.append(
                f"    ! [{finding.severity}] {finding.rule_id} in "
                f"{finding.bucket_name}/{finding.object_key}:{finding.line}"
            )
        return lines


def _bucket_map(result: ScanResult) -> dict[str, S3Bucket]:
    return {bucket.bucket_name: bucket for bucket in result.buckets}


def _object_changes(
    old: S3Bucket | None, new: S3Bucket | None
) -> tuple[list[str], list[str]] | None:
    """Set-diff the object lists; None when nothing changed."""
    old_objects = set(old.objects) if old else set()
    new_objects = set(new.objects) if new else set()
    added = sorted(new_objects - old_objects)
    removed = sorted(old_objects - new_objects)
    if not added and not removed:
        return None
    return added, removed


def _findings_key(finding: Finding) -> tuple[str, str, str]:
    return (finding.bucket_name, finding.object_key, finding.rule_id)


def diff_results(old: ScanResult, new: ScanResult) -> DiffReport:
    """Compare two scan results bucket-by-bucket and finding-by-finding."""
    old_buckets = _bucket_map(old)
    new_buckets = _bucket_map(new)
    report = DiffReport(
        new_buckets=[b for name, b in new_buckets.items() if name not in old_buckets],
        removed_buckets=[b for name, b in old_buckets.items() if name not in new_buckets],
    )

    report.changed_objects = _collect_object_changes(old_buckets, new_buckets)
    report.new_findings = _new_findings(old, new)
    return report


def _collect_object_changes(
    old_buckets: dict[str, S3Bucket], new_buckets: dict[str, S3Bucket]
) -> dict[str, tuple[list[str], list[str]]]:
    changed: dict[str, tuple[list[str], list[str]]] = {}
    for name in sorted(set(old_buckets) & set(new_buckets)):
        change = _object_changes(old_buckets[name], new_buckets[name])
        if change is not None:
            changed[name] = change
    return changed


def _new_findings(old: ScanResult, new: ScanResult) -> list[Finding]:
    old_keys = {_findings_key(f) for f in old.findings}
    seen: set[tuple[str, str, str]] = set()
    fresh: list[Finding] = []
    for finding in new.findings:
        key = _findings_key(finding)
        if key in old_keys or key in seen:
            continue
        seen.add(key)
        fresh.append(finding)
    return fresh
