import argparse
import hashlib
from functools import partial

import aioredis


async def redis_create_connection(connection_string: str):
    async def redis_create_index(connection):
        try:
            await connection.execute(
                "FT.CREATE",
                "s3_index",

                "SCHEMA",

                "bucket",
                "TEXT",

                "filename",
                "TEXT",

                "content",
                "TEXT",
                "WEIGHT",
                "5.0"
            )
        except Exception as e:
            if "Index already exists" not in str(e):
                raise

    redis_con = await aioredis.create_redis_pool(connection_string)

    await redis_create_index(redis_con)

    return redis_con


async def redis_add_document(connection,
                             bucket_name: str,
                             object_path: str,
                             content: bytes):
    object_id = f"{bucket_name}{object_path}".encode("utf-8")

    try:
        await connection.execute(
            "FT.ADD",
            "s3_index",
            hashlib.sha512(object_id).hexdigest(),
            "1.0",
            "FIELDS",
            "bucket",
            bucket_name,
            "filename",
            object_path,
            "content",
            content
        )
    except Exception as e:
        message = str(e)
        if "Document already exists" not in message:
            print(f"    !> Insertion error: {message}")






__all__ = ("redis_add_document", "redis_create_connection")
