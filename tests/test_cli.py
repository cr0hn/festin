"""Tests for the typer CLI (festin.cli) and the scan runner wiring."""

import json

import httpx
import pytest
from typer.testing import CliRunner

from festin import scan_runner
from festin.cli import app
from festin.models import ScanResult
from festin.s3 import S3Bucket

runner = CliRunner(env={"COLUMNS": "200"})


def _invoke(*args: str):
    return runner.invoke(app, list(args))


class TestHelpAndBasics:
    def test_top_level_help_renders_commands(self):
        result = _invoke("--help")

        assert result.exit_code == 0
        assert "scan" in result.output
        assert "serve" in result.output

    def test_scan_help_lists_all_flags(self):
        result = _invoke("scan", "--help")

        assert result.exit_code == 0
        for flag in (
            "--file-domains",
            "--watch",
            "--concurrency",
            "--http-timeout",
            "--domain-regex",
            "--domain-black-list",
            "--domain-white-list",
            "--result-file",
            "--discovered-domains",
            "--raw-discovered",  # rich may truncate long flags in the panel
            "--tor",
            "--no-dnsdiscover",
            "--dns-resolver",
            "--profile",
            "--permute",
            "--wordlist",
            "--cloud",
            "--scan-id",
            "--state",
            "--diff",
            "--secrets",
            "--checkpoint",
            "--resume",
            "--export",
        ):
            assert flag in result.output, f"missing flag {flag}"

    def test_serve_help_documents_fallback(self):
        result = _invoke("serve", "--help")

        assert result.exit_code == 0
        assert "503" in result.output
        assert "--state" in result.output

    def test_version_command(self):
        result = _invoke("version")

        assert result.exit_code == 0
        assert "version:" in result.output

    def test_version_flag_maps_to_version_command(self):
        result = _invoke("--version")

        assert result.exit_code == 0
        assert "version:" in result.output


class TestScanValidation:
    def test_no_domains_fails(self):
        result = _invoke("scan", "-q")

        assert result.exit_code == 1
        assert "at least one domain" in result.output

    def test_unknown_profile_fails(self):
        result = _invoke("scan", "-q", "--profile", "bogus", "example.com")

        assert result.exit_code == 1
        assert "Unknown profile" in result.output

    def test_diff_requires_state(self):
        result = _invoke("scan", "-q", "--diff", "example.com")

        assert result.exit_code == 1
        assert "--diff requires --state" in result.output

    def test_export_requires_output(self):
        result = _invoke("scan", "-q", "--export", "csv", "example.com")

        assert result.exit_code == 1
        assert "--export requires --output" in result.output

    def test_unknown_export_format_fails(self):
        result = _invoke("scan", "-q", "--export", "yaml", "-o", "x.yaml", "example.com")

        assert result.exit_code == 1
        assert "Unknown export format" in result.output

    def test_resume_requires_checkpoint(self):
        result = _invoke("scan", "-q", "--resume", "example.com")

        assert result.exit_code == 1
        assert "--resume requires --checkpoint" in result.output

    def test_black_and_white_lists_incompatible(self, tmp_path):
        bl = tmp_path / "bl.txt"
        wl = tmp_path / "wl.txt"
        bl.write_text("a.com\n")
        wl.write_text("b.com\n")

        result = _invoke("scan", "-q", "-B", str(bl), "-W", str(wl), "example.com")

        assert result.exit_code == 1
        assert "incompatible" in result.output


@pytest.fixture
def fake_scan(monkeypatch):
    """Stub the core pipeline so the real run_scan orchestration runs.

    core.run is replaced with a stub that appends a canned bucket to
    cli_args.collect_buckets; run_scan then handles findings printing,
    state saving, diffing and exporting for real.
    """
    recorded = {}
    bucket = S3Bucket(
        domain="example.com",
        bucket_name="http://example.com",
        objects=["docs/readme.txt"],
    )

    async def fake_core_run(cli_args, domains):
        recorded["args"] = cli_args
        recorded["domains"] = domains
        cli_args.collect_buckets.append(bucket)

    monkeypatch.setattr(scan_runner.core, "run", fake_core_run)
    return recorded, bucket


class TestScanWithMockedPipeline:
    def test_scan_exports_sarif(self, fake_scan, tmp_path):
        recorded, _bucket = fake_scan
        out = tmp_path / "out.sarif"

        result = _invoke("scan", "-q", "--export", "sarif", "-o", str(out), "example.com")

        assert result.exit_code == 0
        assert recorded["domains"] == ["example.com"]
        assert out.exists()
        assert '"version"' in out.read_text()

    def test_scan_saves_state(self, fake_scan, tmp_path):
        recorded, _bucket = fake_scan
        state = tmp_path / "state.json"

        result = _invoke("scan", "-q", "--scan-id", "myid", "--state", str(state), "example.com")

        assert result.exit_code == 0
        assert recorded["args"].scan_id == "myid"
        saved = json.loads(state.read_text())
        assert saved["scans"][0]["scan_id"] == "myid"

    def test_scan_runs_and_collects(self, fake_scan, tmp_path):
        _recorded, _bucket = fake_scan
        state = tmp_path / "state.json"

        result = _invoke("scan", "-q", "--state", str(state), "example.com")

        assert result.exit_code == 0
        saved = json.loads(state.read_text())
        assert saved["scans"][0]["buckets"][0]["bucket_name"] == "http://example.com"

    def test_profile_sets_concurrency(self, fake_scan):
        recorded, _bucket = fake_scan

        result = _invoke("scan", "-q", "--profile", "stealth", "example.com")

        assert result.exit_code == 0
        assert recorded["args"].concurrency == 2
        assert recorded["args"].rate == 1.0

    def test_diff_prints_summary(self, fake_scan, tmp_path):
        _recorded, _bucket = fake_scan
        state = tmp_path / "state.json"
        old = ScanResult(
            scan_id="old",
            started_at="2025-12-01T00:00:00+00:00",
            finished_at="2025-12-01T00:01:00+00:00",
            domains=["example.com"],
            buckets=[S3Bucket(domain="example.com", bucket_name="http://gone.com", objects=[])],
        )
        state.write_text(json.dumps({"schema": "festin/v1", "scans": [old.to_dict()]}))

        result = _invoke("scan", "--state", str(state), "--diff", "example.com")

        assert result.exit_code == 0
        assert "new buckets" in result.output

    def test_wordlist_merges_candidates(self, fake_scan, monkeypatch, tmp_path):
        recorded, _bucket = fake_scan
        words = tmp_path / "words.txt"
        words.write_text("backup\n# comment\n\nsecret\n")
        probed = []
        monkeypatch.setattr(scan_runner, "probe_candidates", _fake_probe(probed, recorded))

        result = _invoke("scan", "-q", "--permute", "--wordlist", str(words), "example.com")

        assert result.exit_code == 0
        assert "backup" in probed

    def test_cloud_probes_plain_candidates(self, fake_scan, monkeypatch):
        recorded, _bucket = fake_scan
        probed = []
        monkeypatch.setattr(scan_runner, "probe_candidates", _fake_probe(probed, recorded))

        result = _invoke("scan", "-q", "--cloud", "example.com")

        assert result.exit_code == 0
        assert "example-com" in probed

    def test_checkpoint_routes_to_store(self, fake_scan, monkeypatch, tmp_path):
        recorded, _bucket = fake_scan
        ran = {}

        async def fake_with_checkpoint(pending, store, run_fn, resume=False):
            ran["pending"] = pending
            ran["resume"] = resume
            return [], None

        monkeypatch.setattr(scan_runner, "with_checkpoint", fake_with_checkpoint)

        result = _invoke(
            "scan",
            "-q",
            "--checkpoint",
            str(tmp_path / "cp.json"),
            "--resume",
            "example.com",
        )

        assert result.exit_code == 0
        assert ran["pending"] == ["example.com"]
        assert ran["resume"] is True

    def test_auto_scan_id_generated(self, fake_scan, tmp_path):
        recorded, _bucket = fake_scan
        state = tmp_path / "state.json"

        result = _invoke("scan", "-q", "--state", str(state), "example.com")

        assert result.exit_code == 0
        assert len(recorded["args"].scan_id or "") >= 8


def _fake_probe(probed: list, recorded: dict):
    async def fake_probe_candidates(cli_args, candidates):
        probed.extend(candidates)
        return []

    return fake_probe_candidates


class TestServeCommand:
    def test_serve_health_endpoint(self, tmp_path, monkeypatch):
        from festin.api import ApiConfig, FestinApi

        captured = {}

        async def fake_run_server(config: ApiConfig) -> None:
            captured["config"] = config
            api = FestinApi(config)
            await api.start()
            port = api.port
            async with httpx.AsyncClient() as client:
                response = await client.get(f"http://127.0.0.1:{port}/api/v1/health")
            captured["health"] = response.json()
            await api.stop()

        monkeypatch.setattr("festin.api.run_server", fake_run_server)

        result = _invoke(
            "serve", "--host", "127.0.0.1", "--port", "0", "--state", str(tmp_path / "s.json")
        )

        assert result.exit_code == 0
        assert captured["health"]["status"] == "ok"


class TestRunnerUnits:
    async def test_probe_candidates_disabled_by_default(self):
        cli_args = scan_runner.build_namespace()

        candidates = await scan_runner.candidate_names(cli_args, ["example.com"])

        assert candidates == []

    async def test_candidate_names_cloud(self):
        cli_args = scan_runner.build_namespace(cloud=True)

        candidates = await scan_runner.candidate_names(cli_args, ["sub.example.com"])

        assert "sub-example-com" in candidates

    async def test_candidate_names_permute_with_wordlist(self, tmp_path):
        words = tmp_path / "w.txt"
        words.write_text("backup\n")
        cli_args = scan_runner.build_namespace(permute=True, wordlist=str(words))

        candidates = await scan_runner.candidate_names(cli_args, ["example.com"])

        assert "backup" in candidates

    async def test_install_rate_controller(self):
        cli_args = scan_runner.build_namespace(profile="stealth")

        scan_runner.install_rate_controller(cli_args)

        assert cli_args.concurrency == 2
        assert cli_args.rate_controller is not None

    def test_new_scan_id_is_short_hex(self):
        scan_id = scan_runner.new_scan_id()

        assert len(scan_id) == 12
        int(scan_id, 16)

    async def test_collect_findings_scans_objects(self, monkeypatch, tmp_path):
        """--secrets fetches objects over HTTP and detects real secrets."""
        body = b"AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=body)

        transport = httpx.MockTransport(handler)

        def fake_build_http_client(cli_args):
            return httpx.AsyncClient(transport=transport)

        monkeypatch.setattr(scan_runner, "build_http_client", fake_build_http_client)
        cli_args = scan_runner.build_namespace(secrets=True)
        bucket = S3Bucket(domain="example.com", bucket_name="example.com", objects=["k.txt"])

        findings = await scan_runner.collect_findings(cli_args, [bucket])

        assert findings
        assert any(f.severity == "critical" for f in findings)
        for finding in findings:
            assert "wJalr" not in finding.match

    async def test_run_scan_saves_and_exports(self, monkeypatch, tmp_path):
        state = tmp_path / "state.json"
        export = tmp_path / "out.csv"
        calls = []

        async def fake_core_run(cli_args, domains):
            calls.append(domains)
            cli_args.collect_buckets.append(
                S3Bucket(domain="example.com", bucket_name="example.com", objects=[])
            )

        monkeypatch.setattr(scan_runner.core, "run", fake_core_run)
        cli_args = scan_runner.build_namespace(
            state_file=str(state), export="csv", output=str(export), quiet=True
        )

        result = await scan_runner.run_scan(cli_args, ["example.com"])

        assert calls == [["example.com"]]
        assert state.exists()
        assert export.exists()
        assert result.finished_at is not None
        saved = json.loads(state.read_text())
        assert saved["scans"][0]["scan_id"] == result.scan_id


class TestDiffBehavior:
    async def test_print_diff_no_previous_scan(self, tmp_path, capsys):
        cli_args = scan_runner.build_namespace(diff=True, state_file=str(tmp_path / "empty.json"))

        await scan_runner._print_diff(cli_args, "2026-01-01T00:00:00+00:00")

        out = capsys.readouterr().out
        assert "No previous scan" in out

    async def test_print_diff_with_previous(self, tmp_path, capsys):
        from festin.state import save_result

        state = tmp_path / "state.json"
        old = ScanResult(
            scan_id="old",
            started_at="2025-12-01T00:00:00+00:00",
            finished_at="2025-12-01T00:01:00+00:00",
            domains=["example.com"],
            buckets=[S3Bucket(domain="example.com", bucket_name="http://gone.com", objects=[])],
        )
        await save_result(old, state)
        cli_args = scan_runner.build_namespace(diff=True, state_file=str(state))
        cli_args.current_result = ScanResult(
            scan_id="new",
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:01:00+00:00",
            domains=["example.com"],
            buckets=[S3Bucket(domain="example.com", bucket_name="http://new.com", objects=["a"])],
        )

        await scan_runner._print_diff(cli_args, "2026-01-01T00:00:00+00:00")

        out = capsys.readouterr().out
        assert "http://new.com" in out
        assert "http://gone.com" in out


class TestCheckpointWiring:
    async def test_run_with_checkpoint_resumes(self, monkeypatch, tmp_path):
        """--resume skips domains processed before the simulated crash."""
        from festin.s3 import S3Bucket as B

        cp = tmp_path / "cp.json"
        processed: list[str] = []

        crash_on_two = {"armed": True}

        async def fake_core_run(cli_args, domains):
            domain = domains[0]
            if domain == "two.com" and crash_on_two["armed"]:
                crash_on_two["armed"] = False
                raise RuntimeError("simulated crash")
            processed.append(domain)
            cli_args.collect_buckets.append(B(domain=domain, bucket_name=f"b-{domain}", objects=[]))

        monkeypatch.setattr(scan_runner.core, "run", fake_core_run)

        # First attempt: one.com completes, two.com crashes mid-scan.
        crashed: list = []
        cli_args = scan_runner.build_namespace(checkpoint=str(cp), quiet=True)
        cli_args.collect_buckets = crashed
        try:
            await scan_runner._run_with_checkpoint(cli_args, ["one.com", "two.com", "three.com"])
        except RuntimeError:
            pass
        assert processed == ["one.com"]

        # Resume: one.com is skipped, the remaining two are processed.
        recovered: list = []
        cli_args2 = scan_runner.build_namespace(checkpoint=str(cp), resume=True, quiet=True)
        cli_args2.collect_buckets = recovered
        buckets, checkpoint = await scan_runner._run_with_checkpoint(
            cli_args2, ["one.com", "two.com", "three.com"]
        )

        assert processed == ["one.com", "two.com", "three.com"]
        names = {b.bucket_name for b in recovered}
        assert names == {"b-one.com", "b-two.com", "b-three.com"}
        assert checkpoint.processed_domains == ["one.com", "two.com", "three.com"]


class TestCollectingConsumer:
    async def test_pipeline_collects_buckets(self, monkeypatch):
        """core.run streams buckets into cli_args.collect_buckets."""

        import festin.__main__ as core

        cli_args = scan_runner.build_namespace()
        collected: list = []
        cli_args.collect_buckets = collected

        async def fake_get_s3(cli_args, domain, level, input_q, results_q):
            await results_q.put(S3Bucket(domain=domain, bucket_name=domain, objects=["x"]))

        async def fake_get_links(*_a, **_k):
            return

        async def fake_get_dns(*_a, **_k):
            return

        monkeypatch.setattr(core, "get_s3", fake_get_s3)
        monkeypatch.setattr(core, "get_links", fake_get_links)
        monkeypatch.setattr(core, "get_dns_info", fake_get_dns)

        await core.run(cli_args, ["example.com"])

        assert [b.bucket_name for b in collected] == ["example.com"]
