"""Event handlers for results and discovered domains."""

import asyncio
import json

import aiofiles

from .s3 import S3Bucket, download_s3_objects

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


async def on_results_add_to_redis(cli_args, bucket: S3Bucket):
    from .redis import redis_add_document, redis_create_connection

    print(f"    >> Indexing content for '{bucket.domain}'")

    redis_con = await redis_create_connection(cli_args.index_server)

    async def fulltext_add_fn(bucket_name, object_path, content):
        await redis_add_document(redis_con, bucket_name, object_path, content)

    try:
        await download_s3_objects(bucket, fulltext_add_fn)
    finally:
        await redis_con.aclose()


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
    "on_results_add_to_redis",
    "STOP_KEYWORD",
)
