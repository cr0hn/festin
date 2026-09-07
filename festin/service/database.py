"""SQLite-based persistent storage for scans, domains, users, and findings."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any


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
        import aiosqlite
        self._conn = await aiosqlite.connect(self._db_path)
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

            CREATE TABLE IF NOT EXISTS findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL,
                bucket TEXT NOT NULL,
                object TEXT,
                rule TEXT,
                severity TEXT DEFAULT 'low',
                line INTEGER
              );

            CREATE TABLE IF NOT EXISTS buckets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                objects_count INTEGER DEFAULT 0
              );

            CREATE TABLE IF NOT EXISTS scheduled_scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                interval_minutes INTEGER NOT NULL DEFAULT 60,
                created_at TEXT NOT NULL
              );

            CREATE INDEX IF NOT EXISTS idx_scans_domain ON scans(domain_id);
            CREATE INDEX IF NOT EXISTS idx_scans_status ON scans(status);
            CREATE INDEX IF NOT EXISTS idx_findings_scan ON findings(scan_id);
            CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
            CREATE INDEX IF NOT EXISTS idx_buckets_scan ON buckets(scan_id);
          """
        await self._conn.executescript(query)

          # Add role column for databases created before role existed.
        try:
            await self._conn.execute(
                "ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'viewer'"
            )
        except Exception:
            pass
        await self._conn.commit()

     # ------------------------------------------------------------------
      # Domain CRUD
      # ------------------------------------------------------------------

    async def create_domain(self, domain_name: str) -> int:
        """Insert a domain. Returns its id."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        cursor = await self._conn.execute(
              "INSERT INTO domains (domain_name, created_at) VALUES (?, ?)",
              (domain_name, now),
          )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_domain(self, domain_id: int) -> dict[str, Any] | None:
        """Return a domain dict or None."""
        cursor = await self._conn.execute(
              "SELECT id AS domain_id, domain_name FROM domains WHERE id = ?",
              (domain_id,),
          )
        row_data = await cursor.fetchone()
        if row_data is None:
            return None
        return {"domain_id": row_data[0], "domain_name": row_data[1]}

    async def find_domain(self, domain_name: str) -> dict[str, Any] | None:
        """Return a domain dict matched by name, or None."""
        cursor = await self._conn.execute(
              "SELECT id AS domain_id, domain_name FROM domains WHERE domain_name = ?",
              (domain_name,),
          )
        row_data = await cursor.fetchone()
        if row_data is None:
            return None
        return {"domain_id": row_data[0], "domain_name": row_data[1]}

    async def create_user(
        self, username: str, password_hash: str, role: str = "viewer"
    ) -> int:
        """Create a user. Returns its id."""
        cursor = await self._conn.execute(
              "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
              (username, password_hash, role),
          )
        await self._conn.commit()
        return cursor.lastrowid


    async def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        """Return a user dict with password_hash, or None."""
        cursor = await self._conn.execute(
              "SELECT id, username, password_hash, role FROM users WHERE username = ?",
              (username,),
          )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {"id": row[0], "username": row[1],
                "password_hash": row[2], "role": row[3] or "viewer"}

    async def user_count(self) -> int:
        """Number of registered users."""
        cursor = await self._conn.execute("SELECT COUNT(*) FROM users")
        return (await cursor.fetchone())[0]

    async def list_domains(self, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """Return paginated domain list with total count."""
        count_cursor = await self._conn.execute("SELECT COUNT(*) FROM domains")
        total = (await count_cursor.fetchone())[0]

        row_cursor = await self._conn.execute(
              "SELECT id AS domain_id, domain_name FROM domains ORDER BY id LIMIT ? OFFSET ?",
              (limit, offset),
          )
        rows = []
        for row in await row_cursor.fetchall():
            rows.append({"domain_id": row[0], "domain_name": row[1]})
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
        count_cursor = await self._conn.execute("SELECT COUNT(*) FROM users")
        return (await count_cursor.fetchone())[0] > 0

    async def list_findings(
        self, scan_id: int | None = None, severity: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Return findings list with total count, optionally filtered."""
        clauses = []
        params: list[Any] = []
        if scan_id is not None:
            clauses.append("scan_id = ?")
            params.append(scan_id)
        if severity is not None:
            clauses.append("severity = ?")
            params.append(severity)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        count_cursor = await self._conn.execute(
              f"SELECT COUNT(*) FROM findings {where}", params
          )
        total = (await count_cursor.fetchone())[0]

        row_cursor = await self._conn.execute(
              f"SELECT id, scan_id, bucket, object, rule, severity, line "
              f"FROM findings {where} ORDER BY id DESC LIMIT ?",
              [*params, limit],
          )
        rows = []
        for row in await row_cursor.fetchall():
            rows.append({
                  "id": row[0],
                  "scan_id": row[1],
                  "bucket": row[2],
                  "object": row[3],
                  "rule": row[4],
                  "severity": row[5],
                  "line": row[6],
              })
        return {"findings": rows, "total": total}

    async def list_buckets(self, limit: int = 30) -> dict[str, Any]:
        """Return buckets list with total count."""
        count_cursor = await self._conn.execute("SELECT COUNT(*) FROM buckets")
        total = (await count_cursor.fetchone())[0]

        row_cursor = await self._conn.execute(
              "SELECT id, scan_id, name, objects_count FROM buckets "
              "ORDER BY id DESC LIMIT ?",
              (limit,),
          )
        rows = []
        for row in await row_cursor.fetchall():
            rows.append({
                  "id": row[0],
                  "scan_id": row[1],
                  "name": row[2],
                  "objects_count": row[3],
              })
        return {"buckets": rows, "total": total}

    async def get_stats(self) -> dict[str, Any]:
        """Dashboard stats: scan count and findings by severity."""
        scan_cursor = await self._conn.execute("SELECT COUNT(*) FROM scans")
        scan_count = (await scan_cursor.fetchone())[0]

        total_cursor = await self._conn.execute("SELECT COUNT(*) FROM findings")
        total_findings = (await total_cursor.fetchone())[0]

        crit_cursor = await self._conn.execute(
              "SELECT COUNT(*) FROM findings WHERE severity = 'critical'"
          )
        critical = (await crit_cursor.fetchone())[0]

        high_cursor = await self._conn.execute(
              "SELECT COUNT(*) FROM findings WHERE severity = 'high'"
          )
        high = (await high_cursor.fetchone())[0]

        return {
              "scan_count": scan_count,
              "findings": {
                  "total": total_findings,
                  "critical": critical,
                  "high": high,
              },
          }

    async def add_scheduled_scan(
        self, domain: str, interval_minutes: int = 60
    ) -> dict[str, Any]:
        """Insert a scheduled scan. Returns the created row."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        cursor = await self._conn.execute(
              "INSERT INTO scheduled_scans (domain, interval_minutes, created_at) "
              "VALUES (?, ?, ?)",
              (domain, interval_minutes, now),
          )
        await self._conn.commit()
        return {
              "id": cursor.lastrowid,
              "domain": domain,
              "interval_minutes": interval_minutes,
              "created_at": now,
          }

    async def list_scheduled_scans(self) -> list[dict[str, Any]]:
        """Return all scheduled scans."""
        cursor = await self._conn.execute(
              "SELECT id, domain, interval_minutes, created_at "
              "FROM scheduled_scans ORDER BY id"
          )
        rows = []
        for row in await cursor.fetchall():
            rows.append({
                  "id": row[0],
                  "domain": row[1],
                  "interval_minutes": row[2],
                  "created_at": row[3],
              })
        return rows

    async def remove_scheduled_scan(self, schedule_id: int) -> bool:
        """Delete a scheduled scan. Returns True if deleted."""
        cursor = await self._conn.execute(
              "DELETE FROM scheduled_scans WHERE id = ?", (schedule_id,)
          )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def delete_scan(self, scan_id: int) -> bool:
        """Delete a scan and its findings/buckets. Returns True if deleted."""
        cursor = await self._conn.execute(
              "DELETE FROM scans WHERE id = ?", (scan_id,)
          )
        deleted = cursor.rowcount > 0
        if deleted:
            await self._conn.execute(
                  "DELETE FROM findings WHERE scan_id = ?", (scan_id,)
              )
            await self._conn.execute(
                  "DELETE FROM buckets WHERE scan_id = ?", (scan_id,)
              )
        await self._conn.commit()
        return deleted


     # ------------------------------------------------------------------
      # Scan CRUD
      # ------------------------------------------------------------------

    async def create_scan(self, domain_id: int, scanner: str = "svc-001") -> int:
        """Create a scan record. Returns its id."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
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
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        cursor = await self._conn.execute(
              "UPDATE scans SET status=?, finished_at=?, buckets_found=?, findings_count=? WHERE id=?",
              (status, now, buckets_found, findings_count, scan_id),
          )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def get_all_scans(self, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """Return all scans with total count."""
        count_cursor = await self._conn.execute("SELECT COUNT(*) FROM scans")
        total = (await count_cursor.fetchone())[0]

        row_cursor = await self._conn.execute(
              "SELECT id, domain_id, scanner_name, status, buckets_found, findings_count, started_at, finished_at FROM scans ORDER BY id DESC LIMIT ? OFFSET ?",
              (limit, offset),
          )
        rows = []
        for row in await row_cursor.fetchall():
            rows.append({
                  "scan_id": row[0],
                  "domain_id": row[1],
                  "scanner_name": row[2],
                  "status": row[3],
                  "buckets_found": row[4],
                  "findings_count": row[5],
                  "started_at": row[6],
                  "finished_at": row[7],
              })
        return {"scans": rows, "total": total}

     # ------------------------------------------------------------------
      # Dashboard
      # ------------------------------------------------------------------

    async def get_dashboard_overview(self) -> dict[str, Any]:
        """Build a dashboard overview with domain and scan statistics."""
          # Total domains
        count1 = await self._conn.execute("SELECT COUNT(*) FROM domains")
        total_domains = (await count1.fetchone())[0]

          # Active domains (enabled=1)
        count2 = await self._conn.execute(
              "SELECT COUNT(*) FROM domains WHERE enabled=1"
          )
        active_domains = (await count2.fetchone())[0]

          # Total scans
        count3 = await self._conn.execute("SELECT COUNT(*) FROM scans")
        total_scans = (await count3.fetchone())[0]

          # Total buckets found
        row1 = await self._conn.execute(
              "SELECT COALESCE(SUM(buckets_found), 0) FROM scans"
          )
        total_buckets = (await row1.fetchone())[0]

          # Total findings
        row2 = await self._conn.execute(
              "SELECT COALESCE(SUM(findings_count), 0) FROM scans"
          )
        total_findings = (await row2.fetchone())[0]

          # Per-domain stats
        domain_cursor = await self._conn.execute(
              "SELECT d.id, d.domain_name, COUNT(s.id), "
              "SUM(s.buckets_found), SUM(s.findings_count) "
              "FROM domains d LEFT JOIN scans s ON d.id = s.domain_id "
              "GROUP BY d.id ORDER BY d.id LIMIT 50"
          )
        domains_rows = []
        for row in await domain_cursor.fetchall():
            domains_rows.append({
                  "domain_id": row[0],
                  "domain_name": row[1],
                  "scan_count": row[2] if row[2] is not None else 0,
                  "total_buckets": row[3] if row[3] is not None else 0,
                  "total_findings": row[4] if row[4] is not None else 0,
              })

        return {
              "total_domains": total_domains,
              "active_domains": active_domains,
              "total_scans": total_scans,
              "total_buckets_found": total_buckets or 0,
              "total_findings": total_findings or 0,
              "domains": domains_rows,
          }
