"""Festin command line interface (typer)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from typer._click import Context as _click_Context
from typer.core import TyperGroup

import festin
from festin import scan_runner
from festin.ratelimit import PROFILES


def _looks_like_command(token: str) -> bool:
    """True when the token names a registered subcommand or a help flag."""
    return token in {"help", "--help", "-h", "--version"}


class _DefaultScanGroup(TyperGroup):
    """Group that defaults to the 'scan' command for bare invocations.

    Top-level ``--help`` also renders the scan options: a user asking for
    help wants the full option reference, not just the command list.
    """

    def format_help(self, ctx, formatter) -> None:
        super().format_help(ctx, formatter)
        scan = self.get_command(ctx, "scan")
        sub_ctx = _click_Context(scan, info_name="festin scan", parent=ctx)
        scan.format_help(sub_ctx, formatter)

    def parse_args(self, ctx, args):
        # Bare `festin [flags] domains...` (no subcommand token) is routed to
        # the 'scan' command; registered subcommands and help flags pass
        # through untouched. `festin --version` maps to the version command.
        if args and args[0] == "--version":
            args = ["version"]
        elif args and not _is_subcommand_token(args[0], self):
            args = ["scan", *args]
        return super().parse_args(ctx, args)


def _is_subcommand_token(token: str, group) -> bool:
    """True when the first CLI token selects a command or a help flag."""
    return token in {"help", "--help", "-h", "--version"} or token in group.commands


app = typer.Typer(
    name="festin",
    help=(
        "Festin — credentialless discovery and monitoring of exposed "
        "S3-compatible cloud storage from domains, DNS and web crawling."
    ),
    cls=_DefaultScanGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
    add_completion=False,
)


@app.command(name="version")
def version() -> None:
    """Show the FestIn version."""
    print(f"version: {festin.__version__}")


DomainsArg = Annotated[
    list[str] | None,
    typer.Argument(help="One or more start domains.", show_default=False),
]
FileDomainsOpt = Annotated[
    Path | None,
    typer.Option("-f", "--file-domains", help="File with one domain per line."),
]
WatchOpt = Annotated[
    bool,
    typer.Option(
        "-w",
        "--watch",
        help="Keep running and watch the '-f' domains file for new domains.",
    ),
]
ConcurrencyOpt = Annotated[
    int,
    typer.Option("-c", "--concurrency", min=1, help="Maximum concurrent probes."),
]
NoLinksOpt = Annotated[
    bool,
    typer.Option("--no-links", help="Disable the HTTP link crawler."),
]
HttpTimeoutOpt = Annotated[
    float,
    typer.Option("-T", "--http-timeout", min=0.1, help="HTTP connection timeout in seconds."),
]
HttpMaxRecursionOpt = Annotated[
    int,
    typer.Option(
        "-M",
        "--http-max-recursion",
        min=0,
        help="Maximum crawl recursion when following links.",
    ),
]
DomainRegexOpt = Annotated[
    str | None,
    typer.Option(
        "-dr",
        "--domain-regex",
        help="Only follow domains matching this regular expression.",
    ),
]
DomainBlackListOpt = Annotated[
    Path | None,
    typer.Option("-B", "--domain-black-list", help="File with blacklisted words."),
]
DomainWhiteListOpt = Annotated[
    Path | None,
    typer.Option("-W", "--domain-white-list", help="File with white-listed words."),
]
ResultFileOpt = Annotated[
    Path | None,
    typer.Option("-rr", "--result-file", help="Streaming results file (one JSON per bucket)."),
]
DiscoveredDomainsOpt = Annotated[
    Path | None,
    typer.Option(
        "-rd",
        "--discovered-domains",
        help="File for discovered domains after applying filters.",
    ),
]
RawDiscoveredDomainsOpt = Annotated[
    Path | None,
    typer.Option(
        "-ra",
        "--raw-discovered-domains",
        help="File for every discovered domain, without filters.",
    ),
]
TorOpt = Annotated[
    bool,
    typer.Option("--tor", help="Route traffic through a local Tor SOCKS5 proxy (127.0.0.1:9050)."),
]
DebugOpt = Annotated[
    bool,
    typer.Option("--debug", help="Enable debug output."),
]
NoPrintOpt = Annotated[
    bool,
    typer.Option("--no-print", help="Do not print results to the screen."),
]
QuietOpt = Annotated[
    bool,
    typer.Option("-q", "--quiet", help="Quiet mode: suppress banner and progress output."),
]
NoDnsdiscoverOpt = Annotated[
    bool,
    typer.Option("-dn", "--no-dnsdiscover", help="Do not follow DNS CNAMEs."),
]
DnsResolverOpt = Annotated[
    str | None,
    typer.Option(
        "-ds",
        "--dns-resolver",
        help="Comma-separated custom DNS servers (e.g. '8.8.8.8,1.1.1.1').",
    ),
]
ProfileOpt = Annotated[
    str | None,
    typer.Option(
        "--profile",
        help=f"Rate profile preset: {', '.join(sorted(PROFILES))}. Overrides concurrency and rate.",
    ),
]
PermuteOpt = Annotated[
    bool,
    typer.Option(
        "--permute",
        help="Expand domains into bucket-name permutations and probe them.",
    ),
]
WordlistOpt = Annotated[
    Path | None,
    typer.Option(
        "--wordlist",
        help="Wordlist file merged into the permutation set (implies --permute behavior "
        "combined with --permute or --cloud).",
    ),
]
CloudOpt = Annotated[
    bool,
    typer.Option(
        "--cloud",
        help="Probe plain domain-derived bucket names across all supported cloud providers.",
    ),
]
ScanIdOpt = Annotated[
    str | None,
    typer.Option("--scan-id", help="Identifier for this scan (default: auto-generated)."),
]
StateOpt = Annotated[
    Path | None,
    typer.Option(
        "--state",
        help="State file where the scan result is persisted (enables --diff history).",
    ),
]
DiffOpt = Annotated[
    bool,
    typer.Option(
        "--diff",
        help="After the scan, diff against the previous scan in the state file (requires --state).",
    ),
]
SecretsOpt = Annotated[
    bool,
    typer.Option(
        "--secrets",
        help="Download text objects from found buckets and scan them for secrets.",
    ),
]
CheckpointOpt = Annotated[
    Path | None,
    typer.Option(
        "--checkpoint",
        help="Checkpoint file to persist scan progress and resume interrupted scans.",
    ),
]
ResumeOpt = Annotated[
    bool,
    typer.Option(
        "--resume",
        help="Resume a previously checkpointed scan (requires --checkpoint).",
    ),
]
ExportOpt = Annotated[
    str | None,
    typer.Option(
        "--export",
        help="Export format: csv, sarif or jsonl (requires --output).",
    ),
]
OutputOpt = Annotated[
    Path | None,
    typer.Option("-o", "--output", help="Output path for --export."),
]


@app.command(name="scan")
def scan(
    domains: DomainsArg = None,
    file_domains: FileDomainsOpt = None,
    watch: WatchOpt = False,
    concurrency: ConcurrencyOpt = 5,
    no_links: NoLinksOpt = False,
    http_timeout: HttpTimeoutOpt = 5,
    http_max_recursion: HttpMaxRecursionOpt = 3,
    domain_regex: DomainRegexOpt = None,
    domain_black_list: DomainBlackListOpt = None,
    domain_white_list: DomainWhiteListOpt = None,
    result_file: ResultFileOpt = None,
    discovered_domains: DiscoveredDomainsOpt = None,
    raw_discovered_domains: RawDiscoveredDomainsOpt = None,
    tor: TorOpt = False,
    debug: DebugOpt = False,
    no_print: NoPrintOpt = False,
    quiet: QuietOpt = False,
    no_dnsdiscover: NoDnsdiscoverOpt = False,
    dns_resolver: DnsResolverOpt = None,
    profile: ProfileOpt = None,
    permute: PermuteOpt = False,
    wordlist: WordlistOpt = None,
    cloud: CloudOpt = False,
    scan_id: ScanIdOpt = None,
    state_file: StateOpt = None,
    diff: DiffOpt = False,
    secrets: SecretsOpt = False,
    checkpoint: CheckpointOpt = None,
    resume: ResumeOpt = False,
    export: ExportOpt = None,
    output: OutputOpt = None,
) -> None:
    """Run the bucket discovery scan over the given domains."""
    _scan_command(locals())


@app.command(name="serve")
def serve(
    host: Annotated[str, typer.Option("--host", help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=0, help="Bind port (0 = ephemeral).")] = 8420,
    db_path: Annotated[
        Path | None,
        typer.Option("--db", help="SQLite database path for persistent storage."),
    ] = None,
) -> None:
    """Start the FestIn REST API server with SQLite backend and SPA frontend.

    Endpoints live under /api/v1 (scans, findings, buckets, health, queues).
    A monitoring dashboard SPA is served at the root path.
    """
    from festin.service import ServiceConfig, run_server

    config = ServiceConfig(host=host, port=port, db_path=db_path)
    print(f"[*] FestIn service listening on http://{host}:{port}")
    try:
        asyncio.run(run_server(config))
    except KeyboardInterrupt:
        print("[*] Stopping FestIn service")


def _warn_if_no_regex(domain_regex: str | None) -> None:
    if domain_regex:
        return
    print()
    print("#" * 50)
    print("#                                                #")
    print("#   IT'S VERY IMPORTANT TO CONFIGURE A DOMAIN    #")
    print("#   REGEX (Option '-dr'). OTHERWISE CRAWLER      #")
    print("#   WILL FOLLOW ANY LINK NO MATTER WHERE THEY    #")
    print("#   POINT TO                                     #")
    print("#                                                #")
    print("#" * 50)
    print()


def _compile_domain_regex(domain_regex: str | None):
    """Compile the domain regex, failing fast on invalid patterns."""
    if not domain_regex:
        return None
    import re

    try:
        return re.compile(domain_regex)
    except re.error as e:
        print(f"Invalid regex! You must use a valid regex: {e}")
        raise typer.Exit(1) from e


def _check_list_files(domain_black_list, domain_white_list) -> None:
    """Validate black/white list options and watch requirements."""
    if domain_black_list and domain_white_list:
        print("[!] Black list option and White list option are incompatible.")
        raise typer.Exit(1)

    for path, label in (
        (domain_black_list, "Black list"),
        (domain_white_list, "White list"),
    ):
        if path and not path.exists():
            print(f"[!] {label} doesn't exist: '{path}'")
            raise typer.Exit(1)


def _collect_domains(domains: list[str] | None, file_domains: Path | None) -> list[str]:
    """Gather domains from the command line and the domains file."""
    collected = list(domains or [])
    if file_domains:
        print(f"[*] Loading '{file_domains}' file")
        collected.extend(file_domains.read_text().splitlines())
    return list(set(collected))


def _validate_scan_options(args: dict) -> None:
    """Fail fast on invalid scan option combinations."""
    if args["diff"] and not args["state_file"]:
        print("[!] --diff requires --state")
        raise typer.Exit(1)

    if args["resume"] and not args["checkpoint"]:
        print("[!] --resume requires --checkpoint")
        raise typer.Exit(1)

    if args["export"]:
        if not args["output"]:
            print("[!] --export requires --output")
            raise typer.Exit(1)
        if args["export"] not in ("csv", "sarif", "jsonl"):
            print(f"[!] Unknown export format {args['export']!r}; expected csv, sarif or jsonl")
            raise typer.Exit(1)

    if args["profile"] and args["profile"] not in PROFILES:
        known = ", ".join(sorted(PROFILES))
        print(f"[!] Unknown profile {args['profile']!r}; available profiles: {known}")
        raise typer.Exit(1)


def _warn_if_no_regex(domain_regex: str | None) -> None:
    if domain_regex:
        return
    print()
    print("#" * 50)
    print("#                                                #")
    print("#   IT'S VERY IMPORTANT TO CONFIGURE A DOMAIN    #")
    print("#   REGEX (Option '-dr'). OTHERWISE CRAWLER      #")
    print("#   WILL FOLLOW ANY LINK NO MATTER WHERE THEY    #")
    print("#   POINT TO                                     #")
    print("#                                                #")
    print("#" * 50)
    print()


def _compile_domain_regex(domain_regex: str | None):
    """Compile the domain regex, failing fast on invalid patterns."""
    if not domain_regex:
        return None
    import re

    try:
        return re.compile(domain_regex)
    except re.error as e:
        print(f"Invalid regex! You must use a valid regex: {e}")
        raise typer.Exit(1) from e


def _check_list_files(domain_black_list, domain_white_list) -> None:
    """Validate black/white list options and watch requirements."""
    if domain_black_list and domain_white_list:
        print("[!] Black list option and White list option are incompatible.")
        raise typer.Exit(1)

    for path, label in (
        (domain_black_list, "Black list"),
        (domain_white_list, "White list"),
    ):
        if path and not path.exists():
            print(f"[!] {label} doesn't exist: '{path}'")
            raise typer.Exit(1)


def _collect_domains(domains: list[str] | None, file_domains: Path | None) -> list[str]:
    """Gather domains from the command line and the domains file."""
    collected = list(domains or [])
    if file_domains:
        print(f"[*] Loading '{file_domains}' file")
        collected.extend(file_domains.read_text().splitlines())
    return list(set(collected))


def _validate_scan_options(args: dict) -> None:
    """Fail fast on invalid scan option combinations."""
    if args["diff"] and not args["state_file"]:
        print("[!] --diff requires --state")
        raise typer.Exit(1)

    if args["resume"] and not args["checkpoint"]:
        print("[!] --resume requires --checkpoint")
        raise typer.Exit(1)

    if args["export"]:
        if not args["output"]:
            print("[!] --export requires --output")
            raise typer.Exit(1)
        if args["export"] not in ("csv", "sarif", "jsonl"):
            print(f"[!] Unknown export format {args['export']!r}; expected csv, sarif or jsonl")
            raise typer.Exit(1)

    if args["profile"] and args["profile"] not in PROFILES:
        known = ", ".join(sorted(PROFILES))
        print(f"[!] Unknown profile {args['profile']!r}; available profiles: {known}")
        raise typer.Exit(1)


def _validate_watch(args: dict) -> None:
    """Watch mode requires a domains file to monitor."""
    if args["watch"] and not args["file_domains"]:
        print("[!] For running in 'Watch' mode you must set a domains file ('-f' option)")
        raise typer.Exit(1)


def _print_banner(args: dict) -> None:
    """Show the logo and the regex warning unless quiet."""
    if args["quiet"]:
        return
    print(festin.LOGO)
    if not args["domain_regex"]:
        _warn_if_no_regex(args["domain_regex"])


def _run_pipeline(args: dict, cli_args, domains: list[str]) -> None:
    """Dispatch to watch or one-shot mode, translating Ctrl-C."""
    try:
        if args["watch"]:
            asyncio.run(scan_runner.run_watch(cli_args, domains))
        else:
            asyncio.run(scan_runner.run_scan(cli_args, domains))
    except KeyboardInterrupt:
        print("[*] Stopping Festin")


def _scan_command(args: dict) -> None:
    """Validate options and run the scan pipeline."""
    _check_list_files(args["domain_black_list"], args["domain_white_list"])
    _validate_watch(args)
    _validate_scan_options(args)

    domains = _collect_domains(args["domains"], args["file_domains"])
    if not domains:
        print("[!] You must provide at least one domain")
        raise typer.Exit(1)

    _print_banner(args)

    cli_args = scan_runner.build_namespace(
        **{key: value for key, value in args.items() if key in scan_runner.DEFAULTS}
    )
    cli_args.domain_regex = _compile_domain_regex(args["domain_regex"])
    _run_pipeline(args, cli_args, domains)


def main() -> None:
    """Console-script entry point and ``python -m festin`` target."""
    app()


if __name__ == "__main__":
    main()
