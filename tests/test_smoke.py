"""Smoke tests: exercise the real CLI end-user entrypoint.

Runs the installed console command as a subprocess, the same way a user
does. Requires no network beyond what the local resolver does; error paths
are pure argument validation.
"""

import subprocess
import sys


def _run_festin(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "festin", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestCLISmoke:
    def test_help_exits_zero_and_shows_options(self):
        proc = _run_festin("--help")

        assert proc.returncode == 0
        assert "Festin" in proc.stdout
        assert "--concurrency" in proc.stdout
        assert "--tor" in proc.stdout

    def test_version_flag(self):
        proc = _run_festin("--version", "-q")

        assert proc.returncode == 0
        assert "version:" in proc.stdout

    def test_no_domains_fails(self):
        proc = _run_festin("-q")

        assert proc.returncode == 1
        assert "at least one domain" in proc.stdout

    def test_invalid_regex_fails(self):
        proc = _run_festin("-q", "-dr", "*invalid[", "example.com")

        assert proc.returncode == 1
        assert "Invalid regex" in proc.stdout

    def test_black_and_white_list_incompatible(self, tmp_path):
        bl = tmp_path / "bl.txt"
        wl = tmp_path / "wl.txt"
        bl.write_text("a.com\n")
        wl.write_text("b.com\n")

        proc = _run_festin("-q", "-B", str(bl), "-W", str(wl), "example.com")

        assert proc.returncode == 1
        assert "incompatible" in proc.stdout

    def test_watch_requires_domains_file(self):
        proc = _run_festin("-q", "-w", "example.com")

        assert proc.returncode == 1
        assert "Watch" in proc.stdout

    def test_blacklist_file_must_exist(self):
        proc = _run_festin("-q", "-B", "/nonexistent/bl.txt", "example.com")

        assert proc.returncode == 1
        assert "Black list" in proc.stdout

    def test_bad_timeout_value_fails(self):
        proc = _run_festin("-q", "--http-timeout", "abc", "example.com")

        assert proc.returncode == 2  # argparse error

    def test_non_dns_domain_completes_without_crash(self, tmp_path):
        """A bogus positional domain must not crash the crawler: probes
        fail (S3 returns 404), no results are written, exit code stays 0.
        The results file is created lazily on first write."""
        proc = _run_festin(
            "-q",
            "--no-links",
            "--no-dnsdiscover",
            "-rr",
            str(tmp_path / "r.festin"),
            "not-a-domain.invalid",
        )

        assert proc.returncode == 0
        if (tmp_path / "r.festin").exists():
            assert (tmp_path / "r.festin").read_text() == ""
