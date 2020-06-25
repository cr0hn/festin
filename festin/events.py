import json
import asyncio
import argparse

from typing import List, Tuple
from functools import partial

import aiofiles

from .s3 import download_s3_objects, S3Bucket
from .redis import redis_create_connection, redis_add_document

STOP_KEYWORD = "########STOP########"


async def on_result_print_results(cli_args, bucket):

    try:
        print(f"[[[FOUND]]]] '{bucket.domain}' - Found {len(bucket.objects)}"
              f"public objects")

        if cli_args.debug:
            for obj in bucket.objects:
                print(f"        -> {bucket.domain}/{obj}")

    finally:
        if not bucket.objects:
            print(f"    *> '{bucket.domain}' - Found {len(bucket.objects)}")


async def on_result_save_streaming_results(cli_args, bucket):
    async with aiofiles.open(cli_args.result_file, mode='a+') as f:
        await f.write(f"{json.dumps(bucket.__dict__)}\n")


async def on_domain_save_new_domains(cli_args,
                                     domain: str,
                                     file_name: str,
                                     initial_domains: List[str]):

    if initial_domains and domain in initial_domains:
        return

    async with aiofiles.open(file_name, mode='a+') as f:
        await f.write(f"{domain}\n")


async def on_results_add_to_redis(
        cli_args: argparse.Namespace,
        bucket: S3Bucket):

    print(f"    >> Indexing content for '{bucket.domain}'")
    redis_con = await redis_create_connection(cli_args.index_server)

    fulltext_add_fn = partial(redis_add_document, redis_con)

    await download_s3_objects(bucket, fulltext_add_fn)



async def on_domain_event(cli_args,
                          domain_queue: asyncio.Queue,
                          initial_domains: List or None,
                          consumers: List[Tuple]):
    while True:

        domain = await domain_queue.get()

        if domain == STOP_KEYWORD:
            break

        for fn, filename in consumers:
            await fn(cli_args, domain, filename, initial_domains)


async def on_result_event(cli_args,
                          results_queue: asyncio.Queue,
                          consumers: List):

    while True:

        bucket = await results_queue.get()

        if bucket == STOP_KEYWORD:
            return

        for c in consumers:
            await c(cli_args, bucket)


__all__ = ("on_result_event", "on_domain_event", "on_domain_save_new_domains",
           "on_result_print_results", "on_result_save_streaming_results",
           "on_results_add_to_redis", "STOP_KEYWORD")
