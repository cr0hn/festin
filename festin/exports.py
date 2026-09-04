"""Structured output exporters: CSV, SARIF 2.1.0 and JSONL."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict
from pathlib import Path

import aiofiles

from .models import Finding, ScanResult

CSV_HEADER = ("bucket_name", "object_key", "rule_id", "severity", "line")

# SARIF level for each festin severity.
SARIF_LEVELS = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
}

SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"
)


async def _write_text(path: Path, text: str) -> None:
    async with aiofiles.open(path, "w", encoding="utf-8", newline="") as fh:
        await fh.write(text)


async def to_csv(findings: list[Finding], path: Path) -> None:
    """Write findings as CSV with proper quoting."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADER)
    for finding in findings:
        writer.writerow(
            (
                finding.bucket_name,
                finding.object_key,
                finding.rule_id,
                finding.severity,
                finding.line,
            )
        )
    await _write_text(path, buffer.getvalue())


def _sarif_rule(finding: Finding) -> dict:
    return {
        "id": finding.rule_id,
        "name": finding.rule_name,
        "shortDescription": {"text": finding.description},
    }


def _sarif_result(finding: Finding) -> dict:
    return {
        "ruleId": finding.rule_id,
        "level": SARIF_LEVELS[finding.severity],
        "message": {"text": finding.description},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": f"s3://{finding.bucket_name}/{finding.object_key}"},
                    "region": {"startLine": finding.line},
                }
            }
        ],
    }


def _collect_rules(findings: list[Finding]) -> list[dict]:
    rules: dict[str, dict] = {}
    for finding in findings:
        if finding.rule_id not in rules:
            rules[finding.rule_id] = _sarif_rule(finding)
    return list(rules.values())


def to_sarif(results: ScanResult) -> dict:
    """Build a minimal valid SARIF 2.1.0 document from a scan result."""
    return {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "festin", "rules": _collect_rules(results.findings)}},
                "results": [_sarif_result(f) for f in results.findings],
            }
        ],
    }


async def write_jsonl(results: ScanResult, path: Path) -> None:
    """Write one JSON object per line, buckets first then findings."""
    lines = [{"type": "bucket", **asdict(bucket)} for bucket in results.buckets]
    lines += [{"type": "finding", **asdict(finding)} for finding in results.findings]
    payload = "".join(json.dumps(entry) + "\n" for entry in lines)
    await _write_text(path, payload)


async def export_results(results: ScanResult, fmt: str, path: Path) -> None:
    """Dispatch ``results`` to the exporter selected by ``fmt``."""
    if fmt == "csv":
        await to_csv(results.findings, path)
    elif fmt == "sarif":
        await _write_text(path, json.dumps(to_sarif(results), indent=2))
    elif fmt == "jsonl":
        await write_jsonl(results, path)
    else:
        raise ValueError(f"Unknown export format {fmt!r}; expected csv, sarif or jsonl")


__all__ = ("CSV_HEADER", "SARIF_LEVELS", "export_results", "to_csv", "to_sarif", "write_jsonl")
