import re
import os
import asyncio
import argparse
import platform

from typing import Set

import aiofiles
import pkg_resources

from colorama import Fore, Style
from watchgod import awatch

from festin import *
from festin.events import *

SK = f"{Fore.YELLOW}SKIP{Style.RESET_ALL}"
SKR = f"{Fore.CYAN}SKIP-RECURSION{Style.RESET_ALL}"

async def analyze(cli_args: argparse.Namespace,
                  domain: str,
                  recursion_level: int,
                  results_queue: asyncio.Queue,
                  sem: asyncio.Semaphore,
                  input_domains_queue: asyncio.Queue):

    tasks = []
    try:
        #
        # Getting info from AWS
        #
        t1 = asyncio.create_task(get_s3(
            cli_args,
            domain,
            recursion_level,
            input_domains_queue,
            results_queue)
        )

        tasks.append(t1)

    except Exception as e:
        print(e)

    try:
        #
        # Get web links?
        #
        if not cli_args.no_links:
            t2 = asyncio.create_task(get_links(
                cli_args,
                domain,
                recursion_level,
                input_domains_queue,
                results_queue)
            )

            tasks.append(t2)

    except Exception as e:
        print(e)

    try:
        #
        # Get cnames
        #
        # if cli_args.dns:
        if not cli_args.no_dnsdiscover:
            t3 = asyncio.create_task(get_dns_info(
                cli_args,
                domain,
                recursion_level,
                input_domains_queue)
            )

            tasks.append(t3)

    except Exception as e:
        print(e)

    try:
        await asyncio.gather(*tasks)
    finally:
        sem.release()
        input_domains_queue.task_done()


async def analyze_domains(cli_args: argparse.Namespace,
                          processed_domains: Set[str],
                          results_queue: asyncio.Queue,
                          input_queue_domains: asyncio.Queue,
                          discovered_domains: asyncio.Queue,
                          raw_discovered_domains: asyncio.Queue):

    concurrency = cli_args.concurrency
    domain_regex = cli_args.domain_regex

    tasks = []

    sem = asyncio.Semaphore(value=concurrency)

    while True:

        if cli_args.watch:
            domain, recursion_level = await input_queue_domains.get()
        else:
            try:
                #
                # When we execute one shot, we need a way to stop the loop ->
                # a timeout each 5 seconds
                #
                domain, recursion_level = await asyncio.wait_for(
                    input_queue_domains.get(),
                    5
                )
            except asyncio.exceptions.TimeoutError:
                if all(t.done() for t in tasks) \
                        and input_queue_domains.empty():
                    return
                else:
                    continue

        if recursion_level < 0:
            print(f"[{SKR}] Maximum recursion level reached. Omitting "
                  f"'{domain}'")
            continue

        if hasattr(domain, "decode"):
            domain = domain.decode("UTF-8")

        await raw_discovered_domains.put(domain)

        if message := valid_domain_or_link(domain):
            print(message)

        if not domain or domain in processed_domains:
            print(f"[{SK}] domain '{domain}' already processed")
            continue

        processed_domains.add(domain)

        await discovered_domains.put(domain)

        if domain_regex:
            if not domain_regex.search(domain):
                continue

        tasks.append(
            asyncio.create_task(analyze(
                cli_args,
                domain,
                recursion_level,
                results_queue,
                sem,
                input_queue_domains
            ))
        )

        await sem.acquire()

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
                    print(f"[DOMAIN>>>>] Added new domain to "
                          f"'{cli_args.file_domains}' but there're already "
                          f"in file. So skipping")
                # Append new domains to processed domains and to the queue
                # domains_processed.update(new_domains)
                for d in new_domains:
                    if not d:
                        continue

                    if not quiet:
                        print(f"[DOMAIN>>>>] Added for processing: '{d}'")

                    await input_domain_queue.put(
                        (d, cli_args.http_max_recursion)
                    )

    quiet = cli_args.quiet
    domains_processed = set()
    input_domain_queue = asyncio.Queue()
    results_queue = asyncio.Queue()
    filtered_discovered_domains = asyncio.Queue()
    raw_discovered_domains = asyncio.Queue()

    #
    # Check proxy connection
    #
    if cli_args.tor:
        if not await check_tor_connection(cli_args):
            print('[!] Can\'t stablish connection to TOR')
            exit(1)

    #
    # Populate initial domains
    #
    for d in init_domains:
        input_domain_queue.put_nowait(
            (d, cli_args.http_max_recursion)
        )

    #
    # On results events
    #
    on_results_tasks = []

    if cli_args.index:
        on_results_tasks.append(on_results_add_to_redis)

    if not cli_args.result_file:
        cli_args.result_file = "results.fetin"
    on_results_tasks.append(on_result_save_streaming_results)

    if not cli_args.no_print or not cli_args.quiet:
        on_results_tasks.append(on_result_print_results)

    #
    # On domain events
    #
    on_domain_filtered_tasks = []
    on_domain_raw_domains_tasks = []

    if cli_args.discovered_domains:
        on_domain_filtered_tasks.append((
            on_domain_save_new_domains,
            cli_args.discovered_domains
        ))
    if cli_args.raw_discovered_domains:
        on_domain_raw_domains_tasks.append((
            on_domain_save_new_domains,
            cli_args.raw_discovered_domains
        ))

    #
    # Launch services
    #
    wait_tasks = []

    wait_tasks.append(asyncio.create_task(
        on_result_event(cli_args, results_queue, on_results_tasks)
    ))
    wait_tasks.append(asyncio.create_task(
        on_domain_event(cli_args,
                        filtered_discovered_domains,
                        init_domains,
                        on_domain_filtered_tasks)
    ))
    wait_tasks.append(asyncio.create_task(
        on_domain_event(cli_args,
                        raw_discovered_domains,
                        None,
                        on_domain_raw_domains_tasks)
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
            filtered_discovered_domains,
            raw_discovered_domains
        )
    finally:
        if not cli_args.watch:
            await results_queue.put(STOP_KEYWORD)
            await filtered_discovered_domains.put(STOP_KEYWORD)
            await raw_discovered_domains.put(STOP_KEYWORD)

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
        description='Festin - the '
                    'powered S3 bucket finder and content discover'
    )

    parser.add_argument("domains", nargs="*")
    parser.add_argument("--version",
                        action="store_true",
                        default=False,
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
    group_http.add_argument("-M", "--http-max-recursion",
                            type=int,
                            default=3,
                            help="maximum recursison when follow links")
    group_http.add_argument("-dr", "--domain-regex",
                           default=None,
                           help="only follow domains that matches this regex")

    group_results = parser.add_argument_group('Results')
    group_results.add_argument("-rr", "--result-file",
                               default=None,
                               help="results file")
    group_results.add_argument("-rd", "--discovered-domains",
                               default=None,
                               help="file name for storing new discovered "
                                    "after apply filters")
    group_results.add_argument("-ra", "--raw-discovered-domains",
                               default=None,
                               help="file name for storing any domain without "
                                    "filters")

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
    group_dns.add_argument("-ds", "--dns-resolver",
                           default=None,
                           help="comma separated custom domain name servers")

    parsed = parser.parse_args()

    if not parsed.quiet:
        print(LOGO)

    if parsed.version:
        print(f"version: {pkg_resources.get_distribution('festin').version}")
        print()
        exit()

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
        parsed.domain_regex = re.compile(parsed.domain_regex)

    if parsed.watch:
        if not parsed.file_domains:
            print("[!] For running in 'Watch' mode you must set a domains "
                  "file ('-f' option)")
            exit(1)

    if not parsed.quiet:
        print("[*] Starting FestIN")

        try:
            asyncio.run(run(parsed, domains))
        except KeyboardInterrupt:
            print("[*] Stopping Festin")


if __name__ == '__main__':
    main()
