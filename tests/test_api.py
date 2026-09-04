"""Tests for the festin REST API server."""

from __future__ import annotations

import json
import uuid

import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from festin.api import ApiConfig, FestinApi
from festin.models import Finding, ScanResult
from festin.s3 import S3Bucket


def _finding(severity: str = "critical", bucket: str = "b-1") -> Finding:
    return Finding(
        bucket_name=bucket,
        object_key="notes.txt",
        rule_id="R1",
        rule_name="AWS key",
        severity=severity,
        description="aws key found",
        line=1,
        match="AKIA****",
    )


def _scan_result(scan_id: str | None = None, findings: list[Finding] | None = None) -> ScanResult:
    return ScanResult(
        scan_id=scan_id or uuid.uuid4().hex,
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:05+00:00",
        domains=["example.com"],
        buckets=[S3Bucket(domain="example.com", bucket_name="b-1", objects=["notes.txt"])],
        findings=findings or [],
    )


@pytest_asyncio.fixture
async def api_client():
    """Start a real TestServer on an ephemeral port with a fresh FestinApi."""
    api = FestinApi(ApiConfig())
    client = TestClient(TestServer(api.build_app()))
    await client.start_server()
    yield api, client
    await client.close()
    await api.stop()


def _assert_error(data: dict, expected: str) -> None:
    assert "error" in data
    assert data["error"] == expected


class TestHealth:
    async def test_health_ok(self, api_client):
        _, client = api_client
        resp = await client.get("/api/v1/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert data["version"]


class TestCreateScan:
    async def test_accepted_shape(self, api_client):
        async def callback(domains: list[str]) -> ScanResult:
            return _scan_result()

        api, client = api_client
        api._config.scan_callback = callback
        resp = await client.post("/api/v1/scans", json={"domains": ["a.com"]})
        assert resp.status == 202
        data = await resp.json()
        assert set(data) == {"scan_id", "status"}
        assert data["status"] == "accepted"
        assert data["scan_id"]

    async def test_no_callback_is_503(self, api_client):
        _, client = api_client
        resp = await client.post("/api/v1/scans", json={"domains": ["a.com"]})
        assert resp.status == 503
        _assert_error(await resp.json(), "no scan callback configured")

    async def test_bad_json_is_400(self, api_client):
        _, client = api_client
        resp = await client.post(
            "/api/v1/scans", data="not json", headers={"Content-Type": "application/json"}
        )
        assert resp.status == 400
        _assert_error(await resp.json(), "invalid JSON body")

    async def test_domains_not_list_is_400(self, api_client):
        _, client = api_client
        resp = await client.post("/api/v1/scans", json={"domains": "a.com"})
        assert resp.status == 400
        _assert_error(await resp.json(), "domains must be a list of strings")

    async def test_raising_callback_is_500(self, api_client):
        async def callback(domains: list[str]) -> ScanResult:
            raise RuntimeError("boom")

        api, client = api_client
        api._config.scan_callback = callback
        resp = await client.post("/api/v1/scans", json={"domains": ["a.com"]})
        assert resp.status == 500
        data = await resp.json()
        _assert_error(data, "internal")
        assert "boom" not in json.dumps(data)


class TestGetScan:
    async def test_unknown_scan_404(self, api_client):
        _, client = api_client
        resp = await client.get("/api/v1/scans/doesnotexist")
        assert resp.status == 404
        data = await resp.json()
        assert "error" in data

    async def test_known_scan_returns_result(self, api_client):
        async def callback(domains: list[str]) -> ScanResult:
            return _scan_result(findings=[_finding()])

        api, client = api_client
        api._config.scan_callback = callback
        created = await (await client.post("/api/v1/scans", json={"domains": ["a.com"]})).json()
        resp = await client.get(f"/api/v1/scans/{created['scan_id']}")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "done"
        assert data["scan_id"] == created["scan_id"]
        assert data["buckets"][0]["bucket_name"] == "b-1"


class TestListScans:
    async def test_empty_list(self, api_client):
        _, client = api_client
        resp = await client.get("/api/v1/scans")
        assert resp.status == 200
        assert (await resp.json()) == {"scans": []}

    async def test_newest_first(self, api_client):
        scan_a = _scan_result()
        scan_b = _scan_result()
        scan_b.started_at = "2026-02-01T00:00:00+00:00"
        api, client = api_client
        api._results = {scan_a.scan_id: scan_a, scan_b.scan_id: scan_b}
        resp = await client.get("/api/v1/scans")
        data = await resp.json()
        ids = [s["scan_id"] for s in data["scans"]]
        assert ids == [scan_b.scan_id, scan_a.scan_id]
        summary = data["scans"][0]
        assert set(summary) == {
            "scan_id",
            "started_at",
            "finished_at",
            "bucket_count",
            "finding_count",
        }


class TestFindings:
    async def test_filter_by_severity(self, api_client):
        result = _scan_result(findings=[_finding("critical"), _finding("low", "b-2")])
        api, client = api_client
        api._results = {result.scan_id: result}
        resp = await client.get("/api/v1/findings", params={"severity": "critical"})
        data = await resp.json()
        assert len(data["findings"]) == 1
        assert data["findings"][0]["severity"] == "critical"
        assert data["findings"][0]["scan_id"] == result.scan_id

    async def test_invalid_severity_400(self, api_client):
        _, client = api_client
        resp = await client.get("/api/v1/findings", params={"severity": "nope"})
        assert resp.status == 400
        _assert_error(await resp.json(), "invalid severity")

    async def test_unfiltered_lists_all(self, api_client):
        result = _scan_result(findings=[_finding("high")])
        api, client = api_client
        api._results = {result.scan_id: result}
        resp = await client.get("/api/v1/findings")
        data = await resp.json()
        assert len(data["findings"]) == 1
        assert data["findings"][0]["severity"] == "high"


class TestBuckets:
    async def test_buckets_tagged_with_scan_id(self, api_client):
        result = _scan_result()
        api, client = api_client
        api._results = {result.scan_id: result}
        resp = await client.get("/api/v1/buckets")
        data = await resp.json()
        assert len(data["buckets"]) == 1
        bucket = data["buckets"][0]
        assert bucket["scan_id"] == result.scan_id
        assert bucket["bucket_name"] == "b-1"

    async def test_empty(self, api_client):
        _, client = api_client
        resp = await client.get("/api/v1/buckets")
        assert (await resp.json()) == {"buckets": []}


class TestServerLifecycle:
    async def test_ephemeral_port_binding(self):
        api = FestinApi(ApiConfig(host="127.0.0.1", port=0))
        await api.start()
        try:
            assert api.port > 0
            assert api.host == "127.0.0.1"
        finally:
            await api.stop()

    async def test_stop_is_idempotent(self):
        api = FestinApi(ApiConfig())
        await api.stop()
        await api.stop()
