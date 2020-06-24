import asyncio
import argparse
import re

from typing import List, Set

import aiofiles

from watchgod import awatch

from festin import *


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
                     cli_args.debug,
                     input_domains_queue,
                     results_queue)

        #
        # Get web links?
        #
        if not cli_args.no_links:
            await get_links(cli_args,
                            domain,
                            cli_args.debug,
                            input_domains_queue)

        #
        # Get cnames
        #
        # if cli_args.dns:
        if not cli_args.no_dnsdiscover:
            await get_dns_info(cli_args,
                               domain,
                               cli_args.debug,
                               input_domains_queue)

    except Exception as e:
        print(e)
    finally:
        sem.release()
        input_domains_queue.task_done()


async def analyze_domains(cli_args: argparse.Namespace,
                          processed_domains: Set[str],
                          results_queue: asyncio.Queue or None = None,
                          input_domains_queue: asyncio.Queue or None = None):

    concurrency = cli_args.concurrency
    domain_regex = cli_args.domain_regex

    tasks = []

    input_queue_domains = input_domains_queue or asyncio.Queue()
    sem = asyncio.Semaphore(value=concurrency)

    while not input_queue_domains.empty():
        domain = await input_queue_domains.get()

        if domain in processed_domains:
            continue

        processed_domains.add(domain)

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

    await asyncio.gather(*tasks)


async def one_shot_run(cli_args: argparse.Namespace, domains: List[str]):
    def show_results_no_watch(buckets: asyncio.Queue):

        if buckets.empty():
            print("    *> No public content found")
        else:
            while not buckets.empty():

                bucket = buckets.get_nowait()

                print(f"    *> '{bucket.domain}' - Found {len(bucket.objects)}"
                      f"public objects")

                for obj in bucket.objects:
                    print(f"        -> {bucket.domain}/{obj}")

    domains_processed = set()
    input_domain_queue = asyncio.Queue()
    output_domain_queue = asyncio.Queue()

    for d in domains:
        input_domain_queue.put_nowait(d)

    await analyze_domains(
        cli_args,
        domains_processed,
        output_domain_queue,
        input_domain_queue
    )

    if not cli_args.no_print or not cli_args.quiet:
        show_results_no_watch(output_domain_queue)

    if cli_args.index:
        if not cli_args.quiet:
            print("[*] Indexing Buckets content")

        await add_to_redis(cli_args, output_domain_queue, download_s3_objects)


async def run_watch(cli_args: argparse.Namespace, init_domains: list):

    quiet = cli_args.quiet
    domains_processed = set()
    input_domain_queue = asyncio.Queue()
    output_domain_queue = asyncio.Queue()

    for d in init_domains:
        input_domain_queue.put_nowait(d)

    # Run first discover
    await analyze_domains(
        cli_args,
        domains_processed,
        output_domain_queue,
        input_domain_queue
    )

    print("[*] Watching for new domains")
    async for _ in awatch(cli_args.file_domains):
        async with aiofiles.open(cli_args.file_domains, mode='r') as f:
            file_content = await f.read()

            clean_content_file = set(file_content.splitlines())

            # Select only new domains
            new_domains = clean_content_file.difference(domains_processed)

            # Append new domains to processed domains and to the queue
            domains_processed.update(new_domains)
            for d in new_domains:
                if not quiet:
                    print(f"     > added for processing: '{d}'")

                await input_domain_queue.put(d)

def main():
    parser = argparse.ArgumentParser(
        description='S3 Data Analyzer'
    )

    parser.add_argument("domains", nargs="*")
    parser.add_argument("--debug", default=False, action="store_true")
    parser.add_argument("-f", "--file-domains",
                        default=None,
                        help="file with domains")
    parser.add_argument("--no-links",
                        action="store_false",
                        default=True,
                        help="extract web site links")
    parser.add_argument("-w", "--watch",
                        action="store_true",
                        default=False,
                        help="watch for new domains in file domains '-f' "
                             "option")
    parser.add_argument("-c", "--concurrency",
                        default=2,
                        type=int,
                        help="max concurrency")

    group_conn = parser.add_argument_group('Connectivity')
    group_conn.add_argument("--tor",
                        default=None,
                        action="store_true",
                        help="Use Tor as proxy")

    group_display = parser.add_argument_group('Display options')
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
                           default=True,
                           help="not follow dns cnames")
    group_dns.add_argument("-dr", "--domain-regex",
                           default=None,
                           help="only follow domains that matches this regex")
    group_dns.add_argument("-ds", "--dns-resolver",
                           nargs="*",
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

        asyncio.run(run_watch(parsed, domains))
    else:

        if not parsed.quiet:
            print("[*] Starting FestIN")
        asyncio.run(one_shot_run(parsed, domains))


if __name__ == '__main__':
    main()
