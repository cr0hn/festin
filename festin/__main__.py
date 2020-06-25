import re
import asyncio
import argparse
import platform

from typing import Set

import aiofiles

from watchgod import awatch

from festin import *
from festin.events import *


async def analyze(cli_args: argparse.Namespace,
                  domain: str,
                  results_queue: asyncio.Queue,
                  sem: asyncio.Semaphore,
                  input_domains_queue: asyncio.Queue):

    print(f"    > Processing '{domain}'", flush=True)

    try:
        #
        # Getting info from AWS
        #
        await get_s3(cli_args,
                     domain,
                     input_domains_queue,
                     results_queue)

    except Exception as e:
        print(e)

    try:
        #
        # Get web links?
        #
        if not cli_args.no_links:
            await get_links(cli_args,
                            domain,
                            input_domains_queue)

    except Exception as e:
        print(e)

    try:
        #
        # Get cnames
        #
        # if cli_args.dns:
        if not cli_args.no_dnsdiscover:
            await get_dns_info(cli_args,
                               domain,
                               input_domains_queue)

    except Exception as e:
        print(e)

    finally:
        sem.release()
        input_domains_queue.task_done()


async def analyze_domains(cli_args: argparse.Namespace,
                          processed_domains: Set[str],
                          results_queue: asyncio.Queue,
                          input_queue_domains: asyncio.Queue,
                          discovered_domains: asyncio.Queue):

    concurrency = cli_args.concurrency
    domain_regex = cli_args.domain_regex

    tasks = []

    sem = asyncio.Semaphore(value=concurrency)

    while True:

        if cli_args.watch:
            domain: str = await input_queue_domains.get()
        else:
            try:
                domain: str = await asyncio.wait_for(
                    input_queue_domains.get(),
                    5
                )
            except asyncio.exceptions.TimeoutError:
                if all(t.done() for t in tasks)\
                        and input_queue_domains.empty():
                    return
                else:
                    continue

        if any(domain.endswith(d) for d in BLACK_LIST_TLD):
            print(f"      -> [SKIP] domain '{domain}' is in blacklist")
            continue

        if any(domain.startswith(d) for d in BLACK_LIST_PREFISES):
            print(f"      -> [SKIP] domain '{domain}' has a prefix "
                  f"blacklisted")
            continue

        if domain in BLACK_LIST_DOMAINS:
            print(f"      -> [SKIP] domain '{domain}' is in blacklist")
            continue

        if not domain or domain in processed_domains:
            print(f"      -> [SKIP] domain '{domain}' already processed")
            continue

        processed_domains.add(domain)

        await discovered_domains.put(domain)

        if domain_regex:
            if not domain_regex.match(domain):
                continue

        tasks.append(
            asyncio.create_task(analyze(
                cli_args,
                domain,
                results_queue,
                sem,
                input_queue_domains
            ))
        )

        await sem.acquire()

        # if all(t.done() for t in tasks) and input_queue_domains.empty():
        #     break

    await asyncio.gather(*tasks)


async def run(cli_args: argparse.Namespace, init_domains: list):

    async def watch_new_domains():

        print("[*] Watching for new domains")
        async for _ in awatch(cli_args.file_domains):
            async with aiofiles.open(cli_args.file_domains, mode='r') as f:
                file_content = await f.read()

                clean_content_file = set(file_content.splitlines())

                # Select only new domains
                new_domains = clean_content_file.difference(domains_processed)

                if not new_domains:
                    print(f"    -> Added new domain to "
                          f"'{cli_args.file_domains}' but there're already "
                          f"in file. So skipping")
                # Append new domains to processed domains and to the queue
                # domains_processed.update(new_domains)
                for d in new_domains:
                    if not d:
                        continue

                    if not quiet:
                        print(f"    > added for processing: '{d}'")

                    await input_domain_queue.put(d)

    quiet = cli_args.quiet
    domains_processed = set()
    input_domain_queue = asyncio.Queue()
    results_queue = asyncio.Queue()
    discovered_domains = asyncio.Queue()

    #
    # Populate initial domains
    #
    for d in init_domains:
        input_domain_queue.put_nowait(d)

    #
    # On results events
    #
    on_results_tasks = []

    if cli_args.index:
        on_results_tasks.append(on_results_add_to_redis)

    if cli_args.result_file:
        on_results_tasks.append(on_result_save_streaming_results)

    if not cli_args.no_print or not cli_args.quiet:
        on_results_tasks.append(on_result_print_results)

    #
    # On domain events
    #
    on_domain_tasks = []

    if cli_args.discovered_domains:
        on_domain_tasks.append(on_domain_save_new_domains)

    #
    # Launch services
    #
    wait_tasks = []

    wait_tasks.append(asyncio.create_task(
        on_result_event(cli_args, results_queue, on_results_tasks)
    ))
    wait_tasks.append(asyncio.create_task(
        on_domain_event(cli_args,
                        discovered_domains,
                        init_domains,
                        on_domain_tasks)
    ))

    # Launch watcher
    if cli_args.watch:
        wait_tasks.append(
            asyncio.create_task(watch_new_domains())
        )

    #
    # Run initial discover
    #
    try:
        await analyze_domains(
            cli_args,
            domains_processed,
            results_queue,
            input_domain_queue,
            discovered_domains
        )
    finally:
        if not cli_args.watch:
            await results_queue.put(STOP_KEYWORD)
            await discovered_domains.put(STOP_KEYWORD)

    #
    # Wait for all tasks finish
    #
    await asyncio.wait(wait_tasks)


def main():

    #
    # Check python version
    #
    if platform.python_version_tuple() < ("3", "8"):
        print("\n[!] Python 3.8 or above is required\n")
        print("If you don't want to install Python 3.8. "
              "Try with Docker:\n")
        print("   $ docker run --rm cr0hn/festin -h")
        exit(1)

    parser = argparse.ArgumentParser(
        description='S3 Data Analyzer'
    )

    parser.add_argument("domains", nargs="*")
    parser.add_argument("--version",
                        help="show version")
    parser.add_argument("-f", "--file-domains",
                        default=None,
                        help="file with domains")
    parser.add_argument("-w", "--watch",
                        action="store_true",
                        default=False,
                        help="watch for new domains in file domains '-f' "
                             "option")
    parser.add_argument("-c", "--concurrency",
                        default=5,
                        type=int,
                        help="max concurrency")

    group_http = parser.add_argument_group('HTTP Probes')
    group_http.add_argument("--no-links",
                        action="store_false",
                        default=False,
                        help="extract web site links")
    group_http.add_argument("-T", "--http-timeout",
                        type=int,
                        default=5,
                        help="set timeout for http connections")

    group_results = parser.add_argument_group('Results')
    group_results.add_argument("-rr", "--result-file",
                               default=None,
                               help="results file")
    group_results.add_argument("-rd", "--discovered-domains",
                               default=None,
                               help="file name for storing new discovered "
                                    "domains")

    group_conn = parser.add_argument_group('Connectivity')
    group_conn.add_argument("--tor",
                            default=None,
                            action="store_true",
                            help="Use Tor as proxy")

    group_display = parser.add_argument_group('Display options')
    group_display.add_argument("--debug",
                               default=False,
                               action="store_true",
                               help="enable debug mode")
    group_display.add_argument("--no-print",
                               default=False,
                               action="store_true",
                               help="doesn't print results in screen")
    group_display.add_argument("-q", "--quiet",
                               default=False,
                               action="store_true",
                               help="Use quiet mode")

    group_redis = parser.add_argument_group('Redis Search')
    group_redis.add_argument("--index",
                             default=None,
                             action="store_true",
                             help="Download and index documents into Redis")
    group_redis.add_argument("--index-server",
                             default="redis://localhost:6379",
                             help="Redis Search Server"
                                  "Default: redis://localhost:6379")

    group_dns = parser.add_argument_group('DNS options')
    group_dns.add_argument("-dn", "--no-dnsdiscover",
                           action="store_false",
                           default=False,
                           help="not follow dns cnames")
    group_dns.add_argument("-dr", "--domain-regex",
                           default=None,
                           help="only follow domains that matches this regex")
    group_dns.add_argument("-ds", "--dns-resolver",
                           default=None,
                           help="comma separated custom domain name servers")

    parsed = parser.parse_args()

    domains = []
    if parsed.domains:
        domains.extend(parsed.domains)

    if parsed.file_domains:
        print(f"[*] Loading '{parsed.file_domains}' file")
        with open(parsed.file_domains, "r") as f:
            domains.extend(f.read().splitlines())

    # Remove duplicates
    domains = list(set(domains))

    if not domains:
        print("[!] You must provide at least one domain")
        exit(1)

    if parsed.domain_regex:
        # Check regex
        parsed.domain_regex = re.compile(parsed.domain_regex)

    if not parsed.quiet:
        print(LOGO)

    if parsed.watch:
        if not parsed.file_domains:
            print("[!] For running in 'Watch' mode you must set a domains "
                  "file ('-f' option)")
            exit(1)

    if not parsed.quiet:
        print("[*] Starting FestIN")

        asyncio.run(run(parsed, domains))


if __name__ == '__main__':
    main()
