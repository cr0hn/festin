"""Scan orchestration: wires the feature modules around the core pipeline.

This module is the single entry point used by both the CLI (``festin.cli``)
and the REST API callback. It composes: rate profiles, bucket-name
permutations, multi-cloud probing, checkpoint/resume, secret detection,
persistent state, scan diffing and structured exports.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

from festin import __main__ as core
from festin.analysis import build_http_client
from festin.checkpoint import Checkpoint, CheckpointStore, with_checkpoint
from festin.cloud_providers import probe_all_providers
from festin.diff import diff_results
from festin.exports import export_results
from festin.models import SEVERITY_ORDER, Finding, ScanResult
from festin.permutations import generate_candidates, load_wordlist, merge_wordlists
from festin.ratelimit import AdaptiveController, AsyncRateLimiter, apply_profile
from festin.s3 import S3Bucket
from festin.secrets import scan_content
from festin.state import latest_before, save_result

#: Objects larger than this are skipped by the --secrets fetcher.
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024

#: Max concurrent object downloads when scanning for secrets.
FETCH_CONCURRENCY = 5

DEFAULTS: dict = {
    "domains": [],
    "version": False,
    "file_domains": None,
    "watch": False,
    "concurrency": 5,
    "no_links": False,
    "http_timeout": 5,
    "http_max_recursion": 3,
    "domain_regex": None,
    "domain_black_list": None,
    "domain_white_list": None,
    "result_file": None,
    "discovered_domains": None,
    "raw_discovered_domains": None,
    "tor": False,
    "debug": False,
    "no_print": False,
    "quiet": False,
    "no_dnsdiscover": False,
    "dns_resolver": None,
    "profile": None,
    "permute": False,
    "wordlist": None,
    "cloud": False,
    "scan_id": None,
    "state_file": None,
    "diff": False,
    "secrets": False,
    "checkpoint": None,
    "resume": False,
    "export": None,
    "output": None,
    "rate": None,
    "rate_jitter": 0.0,
    "rate_controller": None,
    "collect_buckets": None,
}

__all__ = (
    "DEFAULTS",
    "FETCH_CONCURRENCY",
    "MAX_DOWNLOAD_BYTES",
    "build_namespace",
    "new_scan_id",
    "run_scan",
    "run_watch",
)


def build_namespace(**overrides) -> argparse.Namespace:
    """Build a pipeline-ready argparse.Namespace with sane defaults."""
    options = dict(DEFAULTS)
    options.update(overrides)
    return argparse.Namespace(**options)


def new_scan_id() -> str:
    """Generate a short scan identifier."""
    return uuid.uuid4().hex[:12]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def install_rate_controller(cli_args: argparse.Namespace) -> None:
    """Apply the profile preset and build the adaptive rate controller."""
    profile = getattr(cli_args, "profile", None)
    if profile:
        apply_profile(cli_args, profile)

    rate = getattr(cli_args, "rate", None)
    if rate:
        limiter = AsyncRateLimiter(rate, getattr(cli_args, "rate_jitter", 0.0))
        cli_args.rate_controller = AdaptiveController(limiter)


async def candidate_names(cli_args: argparse.Namespace, domains: list[str]) -> list[str]:
    """Bucket-name candidates for --cloud/--permute; empty when disabled.

    ``--cloud`` probes the plain domain variants; ``--permute`` adds the
    full permutation set and, with ``--wordlist``, merges a wordlist.
    """
    if not (cli_args.permute or cli_args.cloud):
        return []

    candidates: set[str] = set()
    for domain in domains:
        candidates |= generate_candidates(domain)

    words: list[str] = []
    if cli_args.wordlist:
        words = await load_wordlist(Path(cli_args.wordlist))

    return merge_wordlists(candidates, words)


async def probe_candidates(cli_args: argparse.Namespace, candidates: list[str]) -> list[S3Bucket]:
    """Probe candidate bucket names against every supported cloud provider."""
    controller = getattr(cli_args, "rate_controller", None)
    sem = asyncio.Semaphore(cli_args.concurrency)
    found: list[S3Bucket] = []

    async with build_http_client(cli_args) as client:
        for name in candidates:
            if controller is not None:
                await controller.acquire()
            buckets = await probe_all_providers(name, client, sem=sem)
            if buckets:
                if controller is not None:
                    controller.record_success()
                found.extend(buckets)
                _print_candidate_buckets(buckets)

    return found


def _print_candidate_buckets(buckets: list[S3Bucket]) -> None:
    for bucket in buckets:
        print(f"[{core.PB}] Found '{len(bucket.objects)}' objects at bucket '{bucket.bucket_name}'")


async def run_domains(cli_args: argparse.Namespace, domains: list[str]) -> None:
    """Run the pipeline over input domains, honoring --checkpoint/--resume."""
    if cli_args.checkpoint:
        await _run_with_checkpoint(cli_args, domains)
        return
    await core.run(cli_args, domains)


async def _run_with_checkpoint(
    cli_args: argparse.Namespace, domains: list[str]
) -> tuple[list[S3Bucket], Checkpoint]:
    """Run one full pipeline per domain with checkpoint persistence.

    ``with_checkpoint`` expects ``run_fn`` to yield one :class:`S3Bucket`
    (or None); when a domain produces several buckets the first is stored in
    the checkpoint and all of them flow through ``collect_buckets``.
    """
    store = CheckpointStore(Path(cli_args.checkpoint))
    all_buckets: list[S3Bucket] = cli_args.collect_buckets

    async def run_fn(domain: str) -> S3Bucket | None:
        per_run: list[S3Bucket] = []
        cli_args.collect_buckets = per_run
        try:
            await core.run(cli_args, [domain])
        finally:
            cli_args.collect_buckets = all_buckets
        all_buckets.extend(per_run)
        return per_run[0] if per_run else None

    stored_buckets, _checkpoint = await with_checkpoint(
        domains, store, run_fn, resume=cli_args.resume
    )
    # Buckets recovered from a previous run (resume) merge into the result.
    known = {b.bucket_name for b in all_buckets}
    for bucket in stored_buckets:
        if bucket.bucket_name not in known:
            all_buckets.append(bucket)

    return all_buckets, _checkpoint


def _record_outcome(controller, status: int) -> None:
    """Feed the adaptive controller with a real HTTP status code."""
    if controller is None:
        return
    if status in (429, 403) or 500 <= status <= 599:
        controller.record_error(status)
    else:
        controller.record_success()


async def _read_capped(response: httpx.Response) -> bytes | None:
    """Read a streaming response body, None when larger than the cap."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_DOWNLOAD_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def _fetch_object(client: httpx.AsyncClient, url: str, controller) -> bytes | None:
    """Download one object (text-only, capped); None on error or oversize."""
    try:
        async with client.stream("GET", url) as response:
            _record_outcome(controller, response.status_code)
            if response.status_code != 200:
                return None
            return await _read_capped(response)
    except httpx.HTTPError:
        return None


def _bucket_base_url(bucket: S3Bucket) -> str:
    """URL the bucket listing was found at, to fetch objects from."""
    name = bucket.bucket_name
    if name.startswith("http://") or name.startswith("https://"):
        return name.rstrip("/")
    return f"https://{bucket.domain.rstrip('/')}"


async def _scan_object(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    controller,
    bucket: S3Bucket,
    key: str,
) -> list[Finding]:
    """Download and scan a single bucket object."""
    url = f"{_bucket_base_url(bucket)}/{key.lstrip('/')}"
    async with sem:
        content = await _fetch_object(client, url, controller)
    if content is None:
        return []
    return scan_content(bucket.bucket_name, key, content)


async def _scan_one_bucket(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    controller,
    bucket: S3Bucket,
) -> list[Finding]:
    """Scan every object in one bucket, bounded by the semaphore."""
    tasks = [
        asyncio.create_task(_scan_object(client, sem, controller, bucket, key))
        for key in bucket.objects
    ]
    results = await asyncio.gather(*tasks)
    return [finding for group in results for finding in group]


async def collect_findings(cli_args: argparse.Namespace, buckets: list[S3Bucket]) -> list[Finding]:
    """Fetch bucket objects and run secret detection over their content."""
    controller = getattr(cli_args, "rate_controller", None)
    sem = asyncio.Semaphore(FETCH_CONCURRENCY)
    findings: list[Finding] = []

    async with build_http_client(cli_args) as client:
        for bucket in buckets:
            findings.extend(await _scan_one_bucket(client, sem, controller, bucket))

    return findings


def print_findings(findings: list[Finding], quiet: bool) -> None:
    """Print findings grouped by severity, critical first."""
    if quiet or not findings:
        return
    print(f"[SECRETS] {len(findings)} findings")
    for severity in SEVERITY_ORDER:
        group = [f for f in findings if f.severity == severity]
        if group:
            _print_severity_group(severity, group)


def _print_severity_group(severity: str, group: list[Finding]) -> None:
    print(f"  {severity}: {len(group)}")
    for finding in group:
        print(
            f"    [{finding.rule_id}] {finding.bucket_name}/{finding.object_key}:"
            f"{finding.line} - {finding.description} ({finding.match})"
        )


async def _print_diff(cli_args: argparse.Namespace, started_at: str) -> None:
    """Diff this scan against the latest stored scan before it started."""
    if not (getattr(cli_args, "diff", False) and cli_args.state_file):
        return

    old = await latest_before(Path(cli_args.state_file), started_at)
    if old is None:
        if not cli_args.quiet:
            print("[DIFF] No previous scan found; nothing to compare")
        return

    print(diff_results(old, cli_args.current_result).summary())


def _build_result(
    cli_args: argparse.Namespace,
    domains: list[str],
    started_at: str,
    buckets: list[S3Bucket],
    findings: list[Finding],
) -> ScanResult:
    return ScanResult(
        scan_id=cli_args.scan_id or new_scan_id(),
        started_at=started_at,
        finished_at=_utc_now(),
        domains=sorted(set(domains)),
        buckets=buckets,
        findings=findings,
    )


async def _collect_secrets_if_enabled(
    cli_args: argparse.Namespace,
    buckets: list[S3Bucket],
) -> list[Finding]:
    """Run the --secrets fetcher when the option is enabled."""
    if not getattr(cli_args, "secrets", False):
        return []
    return await collect_findings(cli_args, buckets)


async def _finalize(cli_args: argparse.Namespace, result: ScanResult, started_at: str) -> None:
    """Post-run side effects: report, persist, diff and export."""
    print_findings(result.findings, cli_args.quiet)

    if cli_args.state_file:
        await save_result(result, Path(cli_args.state_file))

    cli_args.current_result = result
    await _print_diff(cli_args, started_at)

    if cli_args.export:
        await export_results(result, cli_args.export, Path(cli_args.output))


async def run_scan(cli_args: argparse.Namespace, domains: list[str]) -> ScanResult:
    """Run one full scan (probe, collect, classify, persist) and return it."""
    started_at = _utc_now()
    install_rate_controller(cli_args)

    collected: list[S3Bucket] = []
    cli_args.collect_buckets = collected

    candidates = await candidate_names(cli_args, domains)
    if candidates:
        collected.extend(await probe_candidates(cli_args, candidates))

    await run_domains(cli_args, domains)

    if not cli_args.scan_id:
        cli_args.scan_id = new_scan_id()

    findings = await _collect_secrets_if_enabled(cli_args, collected)
    result = _build_result(cli_args, domains, started_at, collected, findings)
    await _finalize(cli_args, result, started_at)

    return result


async def run_watch(cli_args: argparse.Namespace, domains: list[str]) -> None:
    """Watch mode: run the pipeline until the user interrupts it."""
    cli_args.collect_buckets = []
    await core.run(cli_args, domains)
