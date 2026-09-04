"""Event handlers for results and discovered domains."""

import asyncio
import json

import aiofiles

from .s3 import S3Bucket

STOP_KEYWORD = "########STOP########"


async def on_result_print_results(cli_args, bucket: S3Bucket):
    print(f"[[[FOUND]]] '{bucket.domain}' - Found {len(bucket.objects)} public objects")

    if cli_args.debug:
        for obj in bucket.objects:
            print(f"        -> {bucket.domain}/{obj}")

    if not bucket.objects:
        print(f"    *> '{bucket.domain}' - Found 0 objects")


async def on_result_save_streaming_results(cli_args, bucket: S3Bucket):
    async with aiofiles.open(cli_args.result_file, mode="a") as f:
        await f.write(f"{json.dumps(bucket.__dict__)}\n")


async def on_domain_save_new_domains(
    cli_args,
    domain: str,
    file_name: str,
    initial_domains: list[str] | None,
):
    if initial_domains and domain in initial_domains:
        return

    async with aiofiles.open(file_name, mode="a") as f:
        await f.write(f"{domain}\n")


async def on_domain_event(
    cli_args,
    domain_queue: asyncio.Queue,
    initial_domains: list[str] | None,
    consumers: list,
):
    """Consume domain events until STOP_KEYWORD arrives."""
    while True:
        domain = await domain_queue.get()

        if domain == STOP_KEYWORD:
            break

        for consumer, filename in consumers:
            await consumer(cli_args, domain, filename, initial_domains)


async def on_result_event(
    cli_args,
    results_queue: asyncio.Queue,
    consumers: list,
):
    """Consume result events until STOP_KEYWORD arrives."""
    while True:
        bucket = await results_queue.get()

        if bucket == STOP_KEYWORD:
            return

        for consumer in consumers:
            await consumer(cli_args, bucket)


__all__ = (
    "on_result_event",
    "on_domain_event",
    "on_domain_save_new_domains",
    "on_result_print_results",
    "on_result_save_streaming_results",
    "STOP_KEYWORD",
)
