"""Tests for festin.exports: CSV, SARIF and JSONL exporters."""

import csv
import io
import json
from pathlib import Path

import pytest

from festin.exports import CSV_HEADER, SARIF_LEVELS, export_results, to_csv, to_sarif, write_jsonl
from festin.models import Finding, ScanResult
from festin.s3 import S3Bucket


def make_finding(rule_id="AWS-KEY", severity="high", line=7, **overrides):
    data = {
        "bucket_name": "corp-backups",
        "object_key": "logs/app.log",
        "rule_id": rule_id,
        "rule_name": "AWS Access Key",
        "severity": severity,
        "description": "AWS access key exposed",
        "line": line,
        "match": "AKIA…REDACTED",
    }
    data.update(overrides)
    return Finding(**data)


def make_result(findings=None, buckets=None):
    return ScanResult(
        scan_id="scan-1",
        started_at="2026-09-04T00:00:00Z",
        buckets=buckets or [S3Bucket(domain="s3.example.com", bucket_name="corp-backups")],
        findings=findings if findings is not None else [make_finding()],
    )


class TestToCsv:
    async def test_roundtrip(self, tmp_path: Path):
        findings = [
            make_finding(line=1),
            make_finding(rule_id="PRIVATE-KEY", severity="critical", line=42),
        ]
        path = tmp_path / "out.csv"
        await to_csv(findings, path)

        rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
        assert rows[0] == list(CSV_HEADER)
        assert rows[1] == ["corp-backups", "logs/app.log", "AWS-KEY", "high", "1"]
        assert rows[2] == ["corp-backups", "logs/app.log", "PRIVATE-KEY", "critical", "42"]

    async def test_quoting_commas_and_newlines(self, tmp_path: Path):
        finding = make_finding(object_key="weird,key,\nfile.txt")
        path = tmp_path / "out.csv"
        await to_csv([finding], path)

        rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"))))
        assert len(rows) == 2
        assert rows[1][1] == "weird,key,\nfile.txt"

    async def test_empty_findings_writes_header_only(self, tmp_path: Path):
        path = tmp_path / "out.csv"
        await to_csv([], path)
        rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
        assert rows == [list(CSV_HEADER)]


class TestToSarif:
    def test_structure(self):
        findings = [
            make_finding(rule_id="AWS-KEY", severity="critical", line=3),
            make_finding(rule_id="AWS-KEY", severity="high", line=9),
            make_finding(rule_id="TODO", severity="medium", line=1),
        ]
        doc = to_sarif(make_result(findings))

        assert doc["$schema"]
        assert doc["version"] == "2.1.0"
        run = doc["runs"][0]
        assert run["tool"]["driver"]["name"] == "festin"

        rule_ids = [r["id"] for r in run["tool"]["driver"]["rules"]]
        assert rule_ids == ["AWS-KEY", "TODO"]  # distinct, order preserved

        assert len(run["results"]) == 3
        first = run["results"][0]
        assert first["ruleId"] == "AWS-KEY"
        assert first["level"] == SARIF_LEVELS["critical"]
        assert first["message"]["text"] == findings[0].description
        loc = first["locations"][0]["physicalLocation"]
        assert loc["artifactLocation"]["uri"] == "s3://corp-backups/logs/app.log"
        assert loc["region"]["startLine"] == 3

    def test_severity_level_mapping(self):
        expected = {"critical": "error", "high": "error", "medium": "warning", "low": "note"}
        assert SARIF_LEVELS == expected
        for severity, level in expected.items():
            doc = to_sarif(make_result([make_finding(severity=severity)]))
            assert doc["runs"][0]["results"][0]["level"] == level

    def test_empty_findings(self):
        doc = to_sarif(make_result([]))
        assert doc["runs"][0]["results"] == []
        assert doc["runs"][0]["tool"]["driver"]["rules"] == []


class TestWriteJsonl:
    async def test_roundtrip(self, tmp_path: Path):
        result = make_result([make_finding(), make_finding(rule_id="PRIVATE-KEY")])
        path = tmp_path / "out.jsonl"
        await write_jsonl(result, path)

        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3  # 1 bucket + 2 findings
        entries = [json.loads(line) for line in lines]

        assert entries[0]["type"] == "bucket"
        assert entries[0]["bucket_name"] == "corp-backups"
        assert entries[1]["type"] == "finding"
        assert entries[1]["rule_id"] == "AWS-KEY"
        assert entries[2]["type"] == "finding"
        assert entries[2]["rule_id"] == "PRIVATE-KEY"

    async def test_empty_result(self, tmp_path: Path):
        result = make_result([])
        result.buckets = []
        path = tmp_path / "out.jsonl"
        await write_jsonl(result, path)
        assert path.read_text(encoding="utf-8") == ""


class TestExportResults:
    async def test_csv_dispatch(self, tmp_path: Path):
        path = tmp_path / "out.csv"
        await export_results(make_result(), "csv", path)
        rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
        assert rows[0] == list(CSV_HEADER)
        assert len(rows) == 2  # header + finding; buckets excluded from CSV

    async def test_sarif_dispatch(self, tmp_path: Path):
        path = tmp_path / "out.sarif"
        await export_results(make_result(), "sarif", path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["version"] == "2.1.0"
        assert doc["runs"][0]["results"][0]["ruleId"] == "AWS-KEY"

    async def test_jsonl_dispatch(self, tmp_path: Path):
        path = tmp_path / "out.jsonl"
        await export_results(make_result([make_finding()]), "jsonl", path)
        entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert [e["type"] for e in entries] == ["bucket", "finding"]

    async def test_unknown_format_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Unknown export format"):
            await export_results(make_result(), "xml", tmp_path / "out.xml")
