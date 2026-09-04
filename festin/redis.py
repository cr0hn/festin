"""Redis Search indexing for discovered S3 documents.

Uses plain ``HSET`` writes: since RediSearch 2.0 (2020) — and mandatory in
Redis 8 / Redis Stack ≥7 — documents are indexed automatically from hashes
when a ``FT.CREATE ... ON HASH`` index exists. The legacy ``FT.ADD`` command
is deprecated and removed in modern servers.
"""

import hashlib

import redis.asyncio as redis

INDEX_NAME = "s3_index"

SCHEMA_FIELDS = {
    "bucket": "bucket name the object belongs to",
    "filename": "object key inside the bucket",
    "content": "object content (text only)",
}


async def redis_create_connection(connection_string: str) -> redis.Redis:
    """Connect to Redis and ensure the full-text index exists.

    Raises on unreachable servers: the caller decides how to handle it.
    """
    redis_con = redis.from_url(connection_string)

    try:
        await redis_con.execute_command(
            "FT.CREATE",
            INDEX_NAME,
            "ON",
            "HASH",
            "PREFIX",
            "1",
            "festin:",
            "SCHEMA",
            "bucket",
            "TEXT",
            "filename",
            "TEXT",
            "content",
            "TEXT",
            "WEIGHT",
            "5.0",
        )
    except redis.ResponseError as e:
        if "Index already exists" not in str(e):
            await redis_con.aclose()
            raise

    return redis_con


async def redis_add_document(
    connection: redis.Redis,
    bucket_name: str,
    object_path: str,
    content: bytes,
) -> None:
    """Write one document as a hash; the index picks it up automatically."""
    object_id = f"{bucket_name}{object_path}".encode()

    doc_key = f"festin:{hashlib.sha512(object_id).hexdigest()}"

    try:
        await connection.hset(
            doc_key,
            mapping={
                "bucket": bucket_name,
                "filename": object_path,
                "content": content,
            },
        )
    except redis.RedisError as e:
        print(f"    !> Insertion error: {e}")


__all__ = ("redis_add_document", "redis_create_connection")
