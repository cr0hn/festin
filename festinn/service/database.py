"""FestIn service database layer: async SQLite + PostgreSQL via asyncpg."""
from __future__ import annotations

import json
from typing import Any

import aiosqlite

try:
    import asyncpg
except ImportError:
    asyncpg = None   # type: ignore


# ── SQLite DDL ────────────────────────────────────────────────

DOMAINS_SQLITE = """\
CREATE TABLE IF NOT EXISTS festin_domains (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_name         TEXT    UNIQUE NOT NULL,
    enabled             BOOLEAN DEFAULT 1,
    scan_interval_sec   INTEGER DEFAULT 300,
    flag_cloud          BOOLEAN DEFAULT 0,
    flag_permute        BOOLEAN DEFAULT 0,
    flag_secrets        BOOLEAN DEFAULT 0,
    state_file          TEXT,
    last_scan_id        TEXT,
    last_scan_status    TEXT,
    last_scanned_at     TIMESTAMP,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP
)"""

SCANS_SQLITE = """\
CREATE TABLE IF NOT EXISTS festin_scans (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id           INTEGER REFERENCES festin_domains(id),
    status              TEXT CHECK(status IN "pending", "running", "completed", "failed"),
    scan_id             TEXT,
    started_at          TIMESTAMP,
    finished_at         TIMESTAMP,
    duration_seconds    REAL,
    domains_scanned     TEXT DEFAULT "[]",
    buckets_found       INTEGER DEFAULT 0,
    findings_count      INTEGER DEFAULT 0,
    diff_data           TEXT
)"""

USERS_SQLITE = """\
CREATE TABLE IF NOT EXISTS festin_users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT UNIQUE NOT NULL,
    pw_hash     TEXT NOT NULL,
    role        TEXT DEFAULT "user",
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)"""

MIGRATIONS_SQLITE = """\
CREATE TABLE IF NOT EXISTS festin_migrations (
    version   INTEGER UNIQUE NOT NULL,
    applied   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)"""

# ── PostgreSQL DDL ────────────────────────────────────────────

DOMAINS_POSTGRES = """\
CREATE TABLE IF NOT EXISTS festin_domains (
    id                  SERIAL PRIMARY KEY,
    domain_name         TEXT    UNIQUE NOT NULL,
    enabled             BOOLEAN DEFAULT TRUE,
    scan_interval_sec   INTEGER DEFAULT 300,
    flag_cloud          BOOLEAN DEFAULT FALSE,
    flag_permute        BOOLEAN DEFAULT FALSE,
    flag_secrets        BOOLEAN DEFAULT FALSE,
    state_file          TEXT,
    last_scan_id        TEXT,
    last_scan_status    TEXT,
    last_scanned_at     TIMESTAMP,
    created_at          TIMESTAMP DEFAULT now(),
    updated_at          TIMESTAMP
)"""

SCANS_POSTGRES = """\
CREATE TABLE IF NOT EXISTS festin_scans (
    id                  SERIAL PRIMARY KEY,
    domain_id           INTEGER REFERENCES festin_domains(id),
    status              TEXT CHECK(status IN "pending", "running", "completed", "failed"),
    scan_id             TEXT,
    started_at          TIMESTAMP,
    finished_at         TIMESTAMP,
    duration_seconds    REAL,
    domains_scanned     TEXT DEFAULT "[]"::jsonb,
    buckets_found       INTEGER DEFAULT 0,
    findings_count      INTEGER DEFAULT 0,
    diff_data           TEXT
)"""

USERS_POSTGRES = """\
CREATE TABLE IF NOT EXISTS festin_users (
    id          SERIAL PRIMARY KEY,
    username    TEXT UNIQUE NOT NULL,
    pw_hash     TEXT NOT NULL,
    role        TEXT DEFAULT "user",
    created_at  TIMESTAMP DEFAULT now()
)"""

MIGRATIONS_POSTGRES = """\
CREATE TABLE IF NOT EXISTS festin_migrations (
    version   INTEGER UNIQUE NOT NULL,
    applied   TIMESTAMP DEFAULT now()
)"""


class Database:
    """Unified async SQLite + PostgreSQL database with migrations."""

    def __init__(self, connection_string: str = "sqlite:///festin.db") -> None:
        self._conn_string = connection_string
        self._backend: str | None = None
        self._pool: Any = None

    @property
    def connected(self) -> bool:
        return self._backend in ("sqlite", "postgres")

    async def connect(self) -> None:
        cs = self._conn_string
        if cs.startswith("sqlite:///"):
            await self._connect_sqlite(cs.replace("sqlite:///", ""))
        elif cs.startswith("postgres://") or cs.startswith("postgresql://"):
            await self._connect_postgres(cs)
        else:
            raise ValueError(f"Unsupported connection string: {cs!r}")

    async def _connect_sqlite(self, path_str: str) -> None:
        conn = await aiosqlite.connect(path_str)
        conn.row_factory = aiosqlite.Row
        self._backend = "sqlite"
        self._pool = conn

    async def _connect_postgres(self, url: str) -> None:
        if asyncpg is None:
            raise ImportError("asyncpg not installed for PostgreSQL support")
        from urllib.parse import urlparse
        parsed = urlparse(url)
        kwargs: dict[str, Any] = {
            "host": parsed.hostname,
            "port": parsed.port or 5432,
            "user": parsed.username,
            "password": parsed.password,
            "database": parsed.path.lstrip("/"),
        }
        self._pool = await asyncpg.create_pool(**kwargs, min_size=1, max_size=5)
        self._backend = "postgres"

    async def disconnect(self) -> None:
        if self._backend == "sqlite" and self._pool is not None:
            await self._pool.close()
        elif self._backend == "postgres" and self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
        self._backend = None

    async def _execute(self, sql: str, params: tuple | list | None = None) -> Any:
        if self._backend == "sqlite":
            conn = self._pool
            cur = await conn.execute(sql, params or ())
            return cur
        elif self._backend == "postgres":
            pool = self._pool   # type: ignore
            async with pool.acquire() as conn:   # type: ignore
                if params is not None:
                    rows = await conn.fetch(sql, *params)
                else:
                    await conn.execute(sql)
                    return []
                return rows
        raise RuntimeError(f"No backend connected ({self._backend})")

    async def _fetchall(self, sql: str, params: tuple | list | None = None) -> list[Any]:
        result = await self._execute(sql, params)
        if self._backend == "sqlite":
            return await result.fetchall()
        return result

    async def _fetchone(self, sql: str, params: tuple | list | None = None) -> Any | None:
        rows = await self._fetchall(sql, params)
        return rows[0] if rows else None

    async def _commit(self) -> None:
        if self._backend == "sqlite":
            conn = self._pool   # type: ignore
            await conn.commit()
