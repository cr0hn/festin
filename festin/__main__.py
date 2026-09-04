"""Core scan pipeline: queues, consumers and per-domain analysis.

The user-facing CLI lives in :mod:`festin.cli`; ``python -m festin``
delegates there. Feature wiring (profiles, permutations, checkpointing,
secrets, state/diff, exports) lives in :mod:`festin.scan_runner`.
"""

import argparse
import asyncio
from dataclasses import dataclass

import aiofiles
import watchfiles

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

    def _unwanted(self, domain: str) -> bool:
        """Already processed, empty, or in the built-in blacklist."""
        return not domain or domain in self.processed or valid_domain_or_link(domain)

    def _filtered_out(self, domain: str) -> bool:
        """User filters: regex, blacklist membership, whitelist mismatch."""
        regex = self.cli_args.domain_regex
        if regex and not regex.search(domain):
            return True
        if self.black_list and domain in self.black_list:
            return True
        return bool(self.white_list) and domain not in self.white_list

    def reject(self, domain: str) -> bool:
        if self._unwanted(domain):
            return True

        self.processed.add(domain)
        return self._filtered_out(domain)


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
    """Result consumers: streaming file, printing, optional collection."""
    consumers = []

    if not cli_args.result_file:
        cli_args.result_file = "results.festin"
    consumers.append(_streaming_results_consumer)

    if not cli_args.no_print or not cli_args.quiet:
        consumers.append(_print_results_consumer)

    collector = getattr(cli_args, "collect_buckets", None)
    if collector is not None:
        consumers.append(_collecting_consumer)

    return consumers


async def _collecting_consumer(cli_args, bucket):
    """Append the bucket to cli_args.collect_buckets for the scan runner."""
    cli_args.collect_buckets.append(bucket)


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


def main():
    """Entry point: delegate to the typer CLI in festin.cli."""
    from festin.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
