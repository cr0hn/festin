"""Festin CLI entrypoint and async orchestration."""

import argparse
import asyncio
import os
import re
from dataclasses import dataclass

import aiofiles
import watchfiles

import festin
from festin.analysis import check_tor_connection, get_dns_info, get_links, get_s3
from festin.events import STOP_KEYWORD, on_domain_event, on_result_event
from festin.utils import valid_domain_or_link

SK = "SKIP"
SKR = "SKIP-RECURSION"


async def analyze(
    cli_args: argparse.Namespace,
    domain: str,
    recursion_level: int,
    results_queue: asyncio.Queue,
    input_domains_queue: asyncio.Queue,
):
    """Run all discovery probes for one domain.

    The semaphore is held by the caller (analyze_domains) for the whole
    lifetime of this coroutine, so concurrency is truly bounded.
    """
    tasks = []

    try:
        tasks.append(
            asyncio.create_task(
                get_s3(cli_args, domain, recursion_level, input_domains_queue, results_queue),
            )
        )

        if not cli_args.no_links:
            tasks.append(
                asyncio.create_task(
                    get_links(
                        cli_args, domain, recursion_level, input_domains_queue, results_queue
                    ),
                )
            )

        if not cli_args.no_dnsdiscover:
            tasks.append(
                asyncio.create_task(
                    get_dns_info(cli_args, domain, recursion_level, input_domains_queue),
                )
            )

        await asyncio.gather(*tasks)
    except Exception as e:
        if cli_args.debug:
            print(f"[ANALYZE-ERROR] probe failure for '{domain}': {e}")
    finally:
        input_domains_queue.task_done()


async def analyze_domains(
    cli_args: argparse.Namespace,
    black_list: list[str],
    white_list: list[str],
    processed_domains: set[str],
    results_queue: asyncio.Queue,
    input_queue_domains: asyncio.Queue,
    discovered_domains: asyncio.Queue,
    raw_discovered_domains: asyncio.Queue,
    stop_event: asyncio.Event,
):
    """Consume the input queue and launch per-domain analysis tasks.

    Exit condition (one-shot mode): queue empty AND no in-flight analysis.
    Watch mode never exits until stop_event is set.
    """
    sem = asyncio.Semaphore(cli_args.concurrency)
    in_flight: set[asyncio.Task] = set()
    filters = _DomainFilters(
        cli_args=cli_args,
        black_list=black_list,
        white_list=white_list,
        processed=processed_domains,
    )

    while True:
        item = await _next_queue_item(cli_args, input_queue_domains, in_flight, stop_event)
        if item is None:
            return

        domain, recursion_level = item

        if recursion_level < 0:
            print(f"[{SKR}] Maximum recursion level reached. Omitting '{domain}'")
            input_queue_domains.task_done()
            continue

        if hasattr(domain, "decode"):
            domain = domain.decode("UTF-8")

        await raw_discovered_domains.put(domain)

        if message := valid_domain_or_link(domain):
            print(message)

        if filters.reject(domain):
            print(f"[{SK}] domain '{domain}' rejected by filters")
            input_queue_domains.task_done()
            continue

        # Emit to the filtered stream only after passing all filters:
        # '-rd' documents domains that will actually be analyzed.
        await discovered_domains.put(domain)
        await _launch_analysis(
            cli_args,
            domain,
            recursion_level,
            results_queue,
            input_queue_domains,
            sem,
            in_flight,
        )


@dataclass
class _DomainFilters:
    """Domain acceptance rules applied before analysis."""

    cli_args: argparse.Namespace
    black_list: list[str]
    white_list: list[str]
    processed: set[str]

    def reject(self, domain: str) -> bool:
        if not domain or domain in self.processed:
            return True

        # Built-in blacklist (CDNs, social networks, ...) is a hard filter;
        # the caller prints the reason.
        if valid_domain_or_link(domain):
            return True

        self.processed.add(domain)

        regex = self.cli_args.domain_regex
        if regex and not regex.search(domain):
            return True

        if self.black_list and domain in self.black_list:
            return True

        if self.white_list and domain not in self.white_list:
            return True

        return False


async def _next_queue_item(
    cli_args: argparse.Namespace,
    input_queue: asyncio.Queue,
    in_flight: set[asyncio.Task],
    stop_event: asyncio.Event,
) -> tuple[str, int] | None:
    """Get the next (domain, recursion_level); None means 'stop'.

    Watch mode: returns None only when stop_event fires.
    One-shot: returns None when queue is empty and nothing is in flight.
    """
    if cli_args.watch:
        getter = asyncio.create_task(input_queue.get())
        stopper = asyncio.create_task(stop_event.wait())
        done, _pending = await asyncio.wait(
            {getter, stopper},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if getter in done:
            stopper.cancel()
            return getter.result()

        getter.cancel()
        return None

    while True:
        try:
            return await asyncio.wait_for(input_queue.get(), 5)
        except TimeoutError:
            if not in_flight and input_queue.empty():
                return None


async def _launch_analysis(
    cli_args: argparse.Namespace,
    domain: str,
    recursion_level: int,
    results_queue: asyncio.Queue,
    input_queue_domains: asyncio.Queue,
    sem: asyncio.Semaphore,
    in_flight: set[asyncio.Task],
) -> None:
    """Acquire BEFORE launching: the semaphore bounds real in-flight work,
    with no task_done/release race window. Released in the task's
    done-callback, even on error."""
    await sem.acquire()

    def _on_done(task: asyncio.Task):
        in_flight.discard(task)
        sem.release()

    task = asyncio.create_task(
        analyze(
            cli_args,
            domain,
            recursion_level,
            results_queue,
            input_queue_domains,
        )
    )
    in_flight.add(task)
    task.add_done_callback(_on_done)


async def run(cli_args: argparse.Namespace, init_domains: list[str]):
    """Wire queues, consumers and watchers; run the discovery pipeline."""
    domains_processed: set[str] = set()
    input_domain_queue: asyncio.Queue = asyncio.Queue()
    results_queue: asyncio.Queue = asyncio.Queue()
    filtered_discovered_domains: asyncio.Queue = asyncio.Queue()
    raw_discovered_domains: asyncio.Queue = asyncio.Queue()
    stop_event = asyncio.Event()

    white_list = await _load_domain_list(cli_args.domain_white_list)
    black_list = await _load_domain_list(cli_args.domain_black_list)

    if cli_args.tor and not await check_tor_connection(cli_args):
        print("[!] Can't establish connection to TOR")
        raise SystemExit(1)

    for d in init_domains:
        input_domain_queue.put_nowait((d, cli_args.http_max_recursion))

    on_results_tasks = _build_result_consumers(cli_args)
    on_domain_filtered_tasks = _build_domain_consumers(
        cli_args.discovered_domains,
    )
    on_domain_raw_domains_tasks = _build_domain_consumers(
        cli_args.raw_discovered_domains,
    )

    wait_tasks = [
        asyncio.create_task(
            on_result_event(cli_args, results_queue, on_results_tasks),
        ),
        asyncio.create_task(
            on_domain_event(
                cli_args,
                filtered_discovered_domains,
                init_domains,
                on_domain_filtered_tasks,
            ),
        ),
        asyncio.create_task(
            on_domain_event(
                cli_args,
                raw_discovered_domains,
                None,
                on_domain_raw_domains_tasks,
            ),
        ),
    ]

    if cli_args.watch:
        wait_tasks.append(
            asyncio.create_task(
                _watch_new_domains(cli_args, domains_processed, input_domain_queue),
            )
        )

    try:
        await analyze_domains(
            cli_args,
            black_list,
            white_list,
            domains_processed,
            results_queue,
            input_domain_queue,
            filtered_discovered_domains,
            raw_discovered_domains,
            stop_event,
        )
    finally:
        await results_queue.put(STOP_KEYWORD)
        await filtered_discovered_domains.put(STOP_KEYWORD)
        await raw_discovered_domains.put(STOP_KEYWORD)

    await asyncio.gather(*wait_tasks)


def _build_result_consumers(cli_args: argparse.Namespace) -> list:
    """Result consumers: redis indexing, streaming file, printing."""
    consumers = []

    if cli_args.index:
        consumers.append(_redis_index_consumer)

    if not cli_args.result_file:
        cli_args.result_file = "results.festin"
    consumers.append(_streaming_results_consumer)

    if not cli_args.no_print or not cli_args.quiet:
        consumers.append(_print_results_consumer)

    return consumers


def _build_domain_consumers(file_name: str | None) -> list:
    """Domain consumers as (fn, filename) pairs for on_domain_event."""
    if not file_name:
        return []
    return [(_save_domains_consumer, file_name)]


async def _watch_new_domains(
    cli_args: argparse.Namespace,
    domains_processed: set[str],
    input_domain_queue: asyncio.Queue,
) -> None:
    """Watch the domains file and enqueue domains never processed."""
    quiet = cli_args.quiet
    print("[*] Watching for new domains")
    async for _ in watchfiles.awatch(cli_args.file_domains):
        async with aiofiles.open(cli_args.file_domains) as f:
            file_content = await f.read()

        clean_content_file = set(file_content.splitlines())

        # Select only new domains, using the same set the consumer
        # mutates (no duplicate-reprocessing race).
        new_domains = clean_content_file.difference(domains_processed)

        if not new_domains:
            print(
                f"[DOMAIN>>>>] Added new domain to "
                f"'{cli_args.file_domains}' but they're already "
                f"in file. So skipping"
            )
            continue

        for d in new_domains:
            if not d:
                continue

            if not quiet:
                print(f"[DOMAIN>>>>] Added for processing: '{d}'")

            await input_domain_queue.put((d, cli_args.http_max_recursion))


async def _load_domain_list(file_name: str | None) -> list[str]:
    """Read a domain list file into a de-duplicated list."""
    if not file_name:
        return []
    async with aiofiles.open(file_name) as f:
        content = await f.read()
    return list(set(content.splitlines()))


async def _redis_index_consumer(cli_args, bucket):
    from festin.events import on_results_add_to_redis

    await on_results_add_to_redis(cli_args, bucket)


async def _streaming_results_consumer(cli_args, bucket):
    from festin.events import on_result_save_streaming_results

    await on_result_save_streaming_results(cli_args, bucket)


async def _print_results_consumer(cli_args, bucket):
    from festin.events import on_result_print_results

    await on_result_print_results(cli_args, bucket)


async def _save_domains_consumer(cli_args, domain, file_name, initial_domains):
    from festin.events import on_domain_save_new_domains

    await on_domain_save_new_domains(
        cli_args,
        domain,
        file_name,
        initial_domains,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Festin - the powered S3 bucket finder and content discover",
    )

    parser.add_argument("domains", nargs="*")
    parser.add_argument(
        "--version",
        action="store_true",
        default=False,
        help="show version",
    )
    parser.add_argument(
        "-f",
        "--file-domains",
        default=None,
        help="file with domains",
    )
    parser.add_argument(
        "-w",
        "--watch",
        action="store_true",
        default=False,
        help="watch for new domains in file domains '-f' option",
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        default=5,
        type=int,
        help="max concurrency",
    )

    group_http = parser.add_argument_group("HTTP Probes")
    group_http.add_argument(
        "--no-links",
        action="store_false",
        default=False,
        help="extract web site links",
    )
    group_http.add_argument(
        "-T",
        "--http-timeout",
        type=float,
        default=5,
        help="set timeout for http connections",
    )
    group_http.add_argument(
        "-M",
        "--http-max-recursion",
        type=int,
        default=3,
        help="maximum recursion when follow links",
    )

    group_filtering = parser.add_argument_group("filtering")
    group_filtering.add_argument(
        "-dr",
        "--domain-regex",
        default=None,
        help="only follow domains that matches this regex",
    )
    group_filtering.add_argument(
        "-B",
        "--domain-black-list",
        default=None,
        help="load a file with a black list words",
    )
    group_filtering.add_argument(
        "-W",
        "--domain-white-list",
        default=None,
        help="load a file with a white list words",
    )

    group_results = parser.add_argument_group("results")
    group_results.add_argument(
        "-rr",
        "--result-file",
        default=None,
        help="results file",
    )
    group_results.add_argument(
        "-rd",
        "--discovered-domains",
        default=None,
        help="file name for storing new discovered after apply filters",
    )
    group_results.add_argument(
        "-ra",
        "--raw-discovered-domains",
        default=None,
        help="file name for storing any domain without filters",
    )

    group_conn = parser.add_argument_group("Connectivity")
    group_conn.add_argument(
        "--tor",
        default=None,
        action="store_true",
        help="Use Tor as proxy",
    )

    group_display = parser.add_argument_group("Display options")
    group_display.add_argument(
        "--debug",
        default=False,
        action="store_true",
        help="enable debug mode",
    )
    group_display.add_argument(
        "--no-print",
        default=False,
        action="store_true",
        help="doesn't print results in screen",
    )
    group_display.add_argument(
        "-q",
        "--quiet",
        default=False,
        action="store_true",
        help="Use quiet mode",
    )

    group_redis = parser.add_argument_group("Redis Search")
    group_redis.add_argument(
        "--index",
        default=None,
        action="store_true",
        help="Download and index documents into Redis",
    )
    group_redis.add_argument(
        "--index-server",
        default="redis://localhost:6379",
        help="Redis Search Server. Default: redis://localhost:6379",
    )

    group_dns = parser.add_argument_group("DNS options")
    group_dns.add_argument(
        "-dn",
        "--no-dnsdiscover",
        action="store_false",
        default=False,
        help="not follow dns cnames",
    )
    group_dns.add_argument(
        "-ds",
        "--dns-resolver",
        default=None,
        help="comma separated custom domain name servers",
    )

    return parser


def main():
    parsed = build_parser().parse_args()

    if not parsed.quiet:
        print(festin.LOGO)

    if parsed.version:
        print(f"version: {festin.__version__}")
        print()
        raise SystemExit(0)

    _warn_if_no_regex(parsed)

    domains = _collect_domains(parsed)
    if not domains:
        print("[!] You must provide at least one domain")
        raise SystemExit(1)

    _validate_options(parsed)

    if not parsed.quiet:
        print("[*] Starting FestIN")

    try:
        asyncio.run(run(parsed, domains))
    except KeyboardInterrupt:
        print("[*] Stopping Festin")


def _warn_if_no_regex(parsed: argparse.Namespace) -> None:
    if not parsed.domain_regex:
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


def _collect_domains(parsed: argparse.Namespace) -> list[str]:
    """Gather domains from CLI args and the domains file, de-duplicated."""
    domains = list(parsed.domains)

    if parsed.file_domains:
        print(f"[*] Loading '{parsed.file_domains}' file")
        with open(parsed.file_domains) as f:
            domains.extend(f.read().splitlines())

    return list(set(domains))


def _validate_options(parsed: argparse.Namespace) -> None:
    """Fail fast on invalid option combinations."""
    _compile_domain_regex(parsed)
    _check_list_options(parsed)


def _compile_domain_regex(parsed: argparse.Namespace) -> None:
    if not parsed.domain_regex:
        return

    try:
        parsed.domain_regex = re.compile(parsed.domain_regex)
    except re.error as e:
        print(f"Invalid regex! You must use a valid regex: {e}")
        raise SystemExit(1) from e


def _check_list_options(parsed: argparse.Namespace) -> None:
    if parsed.domain_black_list and parsed.domain_white_list:
        print("[!] Black list option and White list option are incompatible.")
        raise SystemExit(1)

    for option, label in (
        ("domain_black_list", "Black list"),
        ("domain_white_list", "White list"),
    ):
        path = getattr(parsed, option)
        if path and not os.path.exists(path):
            print(f"[!] {label} doesn't exist: '{path}'")
            raise SystemExit(1)

    if parsed.watch and not parsed.file_domains:
        print("[!] For running in 'Watch' mode you must set a domains file ('-f' option)")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
