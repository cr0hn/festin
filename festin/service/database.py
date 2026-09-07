"""SQLite-based persistent storage for scans, domains, users, and findings."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

_DB_CONN: Any = None


class Database:
    """Async SQLite database layer backed by aiosqlite."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self.connected = False
        self._conn: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the database connection."""
        global _DB_CONN
        loop = asyncio.get_event_loop()
        self._conn = await loop.run_in_executor(None, lambda: __import__('aiosqlite').connect(self._db_path))
        self.connected = True

    async def disconnect(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        self.connected = False

    async def migrate(self) -> None:
        """Create tables and indexes if they don't exist."""
        query = """
            CREATE TABLE IF NOT EXISTS domains (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain_name TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain_id INTEGER NOT NULL REFERENCES domains(id),
                scanner_name TEXT DEFAULT 'svc-001',
                status TEXT DEFAULT 'pending',
                buckets_found INTEGER DEFAULT 0,
                findings_count INTEGER DEFAULT 0,
                started_at TEXT NOT NULL,
                finished_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_scans_domain ON scans(domain_id);
            CREATE INDEX IF NOT EXISTS idx_scans_status ON scans(status);
        """
        await self._conn.execute(query)
        await self._conn.commit()

    # ------------------------------------------------------------------
    # Domain CRUD
    # ------------------------------------------------------------------

    async def create_domain(self, domain_name: str) -> int:
        """Insert a domain. Returns its id."""
        now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        cursor = await self._conn.execute(
            "INSERT INTO domains (domain_name, created_at) VALUES (?, ?)",
            (domain_name, now),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_domain(self, domain_id: int) -> dict[str, Any] | None:
        """Return a domain dict or None."""
        row = await self._conn.execute(
            "SELECT id AS domain_id, domain_name FROM domains WHERE id = ?", (domain_id,)
        )
        row_data = await row.fetchone()
        if row_data is None:
            return None
        return {"domain_id": row_data[0], "domain_name": row_data[1]}

    async def list_domains(self, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """Return paginated domain list with total count."""
        count_row = await self._conn.execute("SELECT COUNT(*) FROM domains")
        total = (await count_row.fetchone())[0]

        row_iter = await self._conn.execute(
            "SELECT id AS domain_id, domain_name FROM domains ORDER BY id LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = [
            {"domain_id": r[0], "domain_name": r[1]}
            async for r in row_iter
        ]
        return {"total": total, "domains": rows}

    async def delete_domain(self, domain_id: int) -> bool:
        """Delete a domain. Returns True if deleted."""
        cursor = await self._conn.execute(
            "DELETE FROM domains WHERE id = ?", (domain_id,)
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    async def user_exists(self) -> bool:
        """True if at least one user exists."""
        count_row = await self._conn.execute("SELECT COUNT(*) FROM users")
        return (await count_row.fetchone())[0] > 0

    async def create_user(self, username: str, password_hash: str) -> int:
        """Create a user. Returns its id."""
        cursor = await self._conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, password_hash),
        )
        await self._conn.commit()
        return cursor.lastrowid

    # ------------------------------------------------------------------
    # Scan CRUD
    # ------------------------------------------------------------------

    async def create_scan(self, domain_id: int, scanner: str = "svc-001") -> int:
        """Create a scan record. Returns its id."""
        now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        cursor = await self._conn.execute(
            "INSERT INTO scans (domain_id, scanner_name, started_at) VALUES (?, ?, ?)",
            (domain_id, scanner, now),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def update_scan_status(
        self,
        scan_id: int,
        status: str = "completed",
        buckets_found: int = 0,
        findings_count: int = 0,
    ) -> bool:
        """Update scan metadata. Returns True if updated."""
        now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        cursor = await self._conn.execute(
            "UPDATE scans SET status=?, finished_at=?, buckets_found=?, findings_count=? WHERE id=?",
            (status, now, buckets_found, findings_count, scan_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def get_all_scans(self, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """Return all scans with total count."""
        count_row = await self._conn.execute(
            "SELECT COUNT(*) FROM scans"
        )
        total = (await count_row.fetchone())[0]

        row_iter = await self._conn.execute(
            "SELECT id, domain_id, scanner_name, status, buckets_found, findings_count, started_at, finished_at FROM scans ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = []
        async for r in row_iter:
            rows.append({
                "scan_id": r[0],
                "domain_id": r[1],
                "scanner_name": r[2],
                "status": r[3],
                "buckets_found": r[4],
                "findings_count": r[5],
                "started_at": r[6],
                "finished_at": r[7],
            })
        return {"scans": rows, "total": total}

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    async def get_dashboard_overview(self) -> dict[str, Any]:
        """Build a dashboard overview with domain and scan statistics."""
        # Total domains
        cnt = await self._conn.execute("SELECT COUNT(*) FROM domains")
        total_domains = (await cnt.fetchone())[0]

        # Active domains (enabled=1)
        cnt2 = await self._conn.execute("SELECT COUNT(*) FROM domains WHERE enabled=1")
        active_domains = (await cnt2.fetchone())[0]

        # Total scans
        cnt3 = await self._conn.execute("SELECT COUNT(*) FROM scans")
        total_scans = (await cnt3.fetchone())[0]

        # Total buckets found
        row_iter = await self._conn.execute("SELECT COALESCE(SUM(buckets_found), 0) FROM scans")
        total_buckets = (await row_iter.fetchone())[0]

        # Total findings
        row_iter = await self._conn.execute("SELECT COALESCE(SUM(findings_count), 0) FROM scans")
        total_findings = (await row_iter.fetchone())[0]

        # Per-domain stats
        domains_rows = []
        row_iter = await self._conn.execute(
            """
            SELECT d.id, d.domain_name, COUNT(s.id), SUM(s.buckets_found), SUM(s.findings_count)
            FROM domains d LEFT JOIN scans s ON d.id = s.domain_id
            GROUP BY d.id ORDER BY d.id LIMIT 50
            """
        )
        async for r in row_iter:
            domains_rows.append({
                "domain_id": r[0],
                "domain_name": r[1],
                "scan_count": r[2] or 0,
                "total_buckets": r[3] or 0,
                "total_findings": r[4] or 0,
            })

        return {
            "total_domains": total_domains,
            "active_domains": active_domains,
            "total_scans": total_scans,
            "total_buckets_found": total_buckets,
            "total_findings": total_findings,
            "domains": domains_rows,
        }
