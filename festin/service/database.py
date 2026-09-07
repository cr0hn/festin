"""SQLite-based persistent storage for scans, domains, users, and findings."""

from __future__ import annotations

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
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

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
        await self._try_alter("users", "role", "TEXT DEFAULT 'viewer'")
        await self._try_alter("domains", "project_id", "INTEGER")
        await self._try_alter("findings", "match", "TEXT")
        await self._try_alter("scans", "project_id", "INTEGER")
        await self._seed_default_project()
        await self._backfill_project_ids()
        indexes = """
            CREATE INDEX IF NOT EXISTS idx_domains_project
                ON domains(project_id);
            CREATE INDEX IF NOT EXISTS idx_scans_project
                ON scans(project_id);
          """
        await self._conn.executescript(indexes)
        await self._conn.commit()

    async def _try_alter(self, table: str, column: str, decl: str) -> None:
        """Add a column, tolerating databases that already have it."""
        try:
            await self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        except Exception:
            pass

    async def _seed_default_project(self) -> None:
        """Insert the default project (id 1) only when no project exists."""
        cursor = await self._conn.execute("SELECT COUNT(*) FROM projects")
        if (await cursor.fetchone())[0] > 0:
            return
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        await self._conn.execute(
            "INSERT INTO projects (id, name, description, created_at) VALUES (1, 'default', '', ?)",
            (now,),
        )

    async def _backfill_project_ids(self) -> None:
        """Assign legacy rows to the default project."""
        await self._conn.execute("UPDATE domains SET project_id = 1 WHERE project_id IS NULL")
        await self._conn.execute(
            "UPDATE scans SET project_id = "
            "(SELECT project_id FROM domains WHERE domains.id = scans.domain_id) "
            "WHERE project_id IS NULL"
        )

    # ------------------------------------------------------------------
    # Domain CRUD
    # ------------------------------------------------------------------

    async def create_domain(self, domain_name: str, project_id: int = 1) -> int:
        """Insert a domain. Returns its id."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        cursor = await self._conn.execute(
            "INSERT INTO domains (domain_name, project_id, created_at) VALUES (?, ?, ?)",
            (domain_name, project_id, now),
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

    async def create_user(self, username: str, password_hash: str, role: str = "viewer") -> int:
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
        return {
            "id": row[0],
            "username": row[1],
            "password_hash": row[2],
            "role": row[3] or "viewer",
        }

    async def user_count(self) -> int:
        """Number of registered users."""
        cursor = await self._conn.execute("SELECT COUNT(*) FROM users")
        return (await cursor.fetchone())[0]

    async def user_exists(self) -> bool:
        """True if at least one user exists."""
        count_cursor = await self._conn.execute("SELECT COUNT(*) FROM users")
        return (await count_cursor.fetchone())[0] > 0

    async def list_domains(
        self, offset: int = 0, limit: int = 100, project_id: int | None = None
    ) -> dict[str, Any]:
        """Return paginated domain list with total count."""
        if project_id is not None:
            count_cursor = await self._conn.execute(
                "SELECT COUNT(*) FROM domains WHERE project_id = ?",
                (project_id,),
            )
            total = (await count_cursor.fetchone())[0]
            row_cursor = await self._conn.execute(
                "SELECT id, domain_name, project_id, enabled, created_at "
                "FROM domains WHERE project_id = ? "
                "ORDER BY id LIMIT ? OFFSET ?",
                (project_id, limit, offset),
            )
        else:
            count_cursor = await self._conn.execute("SELECT COUNT(*) FROM domains")
            total = (await count_cursor.fetchone())[0]
            row_cursor = await self._conn.execute(
                "SELECT id, domain_name, project_id, enabled, created_at "
                "FROM domains ORDER BY id LIMIT ? OFFSET ?",
                (limit, offset),
            )
        rows = []
        for row in await row_cursor.fetchall():
            rows.append(
                {
                    "id": row[0],
                    "domain_name": row[1],
                    "project_id": row[2],
                    "enabled": bool(row[3]) if row[3] is not None else True,
                    "created_at": row[4],
                }
            )
        return {"total": total, "domains": rows}

    async def delete_domain(self, domain_id: int) -> bool:
        """Delete a domain and its scans/findings/buckets. True if deleted."""
        scan_cursor = await self._conn.execute(
            "SELECT id FROM scans WHERE domain_id = ?", (domain_id,)
        )
        scan_ids = [row[0] for row in await scan_cursor.fetchall()]
        for scan_id in scan_ids:
            await self._delete_scan_rows(scan_id)
        cursor = await self._conn.execute("DELETE FROM domains WHERE id = ?", (domain_id,))
        deleted = cursor.rowcount > 0
        await self._conn.commit()
        return deleted

    async def _delete_scan_rows(self, scan_id: int) -> None:
        """Delete one scan's findings, buckets, and scan row (no commit)."""
        await self._conn.execute("DELETE FROM findings WHERE scan_id = ?", (scan_id,))
        await self._conn.execute("DELETE FROM buckets WHERE scan_id = ?", (scan_id,))
        await self._conn.execute("DELETE FROM scans WHERE id = ?", (scan_id,))

    async def list_findings(
        self,
        scan_id: int | None = None,
        severity: str | None = None,
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

        count_cursor = await self._conn.execute(f"SELECT COUNT(*) FROM findings {where}", params)
        total = (await count_cursor.fetchone())[0]

        row_cursor = await self._conn.execute(
            f"SELECT id, scan_id, bucket, object, rule, severity, line "
            f"FROM findings {where} ORDER BY id DESC LIMIT ?",
            [*params, limit],
        )
        rows = []
        for row in await row_cursor.fetchall():
            rows.append(
                {
                    "id": row[0],
                    "scan_id": row[1],
                    "bucket": row[2],
                    "object": row[3],
                    "rule": row[4],
                    "severity": row[5],
                    "line": row[6],
                }
            )
        return {"findings": rows, "total": total}

    async def list_buckets(self, limit: int = 30) -> dict[str, Any]:
        """Return buckets list with total count."""
        count_cursor = await self._conn.execute("SELECT COUNT(*) FROM buckets")
        total = (await count_cursor.fetchone())[0]

        row_cursor = await self._conn.execute(
            "SELECT id, scan_id, name, objects_count FROM buckets ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = []
        for row in await row_cursor.fetchall():
            rows.append(
                {
                    "id": row[0],
                    "scan_id": row[1],
                    "name": row[2],
                    "objects_count": row[3],
                }
            )
        return {"buckets": rows, "total": total}

    async def get_stats(self) -> dict[str, Any]:
        """Dashboard stats: counts, severity split, and 14-day scan timeline."""
        scan_count = await self._count("SELECT COUNT(*) FROM scans")
        total_findings = await self._count("SELECT COUNT(*) FROM findings")

        sev: dict[str, int] = {}
        for sev_name in ("critical", "high", "medium", "low"):
            sev[sev_name] = await self._count(
                "SELECT COUNT(*) FROM findings WHERE severity = ?", (sev_name,)
            )

        return {
            "scan_count": scan_count,
            "findings": {
                "total": total_findings,
                "critical": sev["critical"],
                "high": sev["high"],
                "medium": sev["medium"],
                "low": sev["low"],
            },
            "recent_scans": await self._scan_timeline(14),
        }

    async def _count(self, sql: str, params: list[Any] | None = None) -> int:
        """Run a single COUNT query and return the integer."""
        cursor = await self._conn.execute(sql, params or [])
        return (await cursor.fetchone())[0]

    async def _scan_timeline(self, days: int) -> list[dict[str, Any]]:
        """Per-day scans/buckets plus critical/high findings (UTC).

        Two queries on purpose: joining findings into the scans aggregate
        would double-count per-scan counters (buckets_found, findings_count)."""
        scan_cursor = await self._conn.execute(
            "SELECT substr(started_at, 1, 10) AS day, COUNT(*), "
            "COALESCE(SUM(buckets_found), 0), COALESCE(SUM(findings_count), 0) "
            "FROM scans WHERE started_at IS NOT NULL "
            "GROUP BY day ORDER BY day DESC LIMIT ?",
            (days,),
        )
        timeline = {
            row[0]: {
                "day": row[0],
                "scans": row[1],
                "buckets": row[2],
                "findings": row[3],
                "critical": 0,
                "high": 0,
            }
            for row in await scan_cursor.fetchall()
        }
        sev_cursor = await self._conn.execute(
            "SELECT substr(s.started_at, 1, 10) AS day, "
            "COALESCE(SUM(f.severity = 'critical'), 0), "
            "COALESCE(SUM(f.severity = 'high'), 0) "
            "FROM findings f JOIN scans s ON s.id = f.scan_id "
            "WHERE s.started_at IS NOT NULL "
            "GROUP BY day"
        )
        for day, crit, high in await sev_cursor.fetchall():
            if day in timeline:
                timeline[day]["critical"] = crit
                timeline[day]["high"] = high
        ordered = sorted(timeline.values(), key=lambda d: d["day"], reverse=True)
        return ordered[:days]

    async def add_scheduled_scan(self, domain: str, interval_minutes: int = 60) -> dict[str, Any]:
        """Insert a scheduled scan. Returns the created row."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        cursor = await self._conn.execute(
            "INSERT INTO scheduled_scans (domain, interval_minutes, created_at) VALUES (?, ?, ?)",
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
            "SELECT id, domain, interval_minutes, created_at FROM scheduled_scans ORDER BY id"
        )
        rows = []
        for row in await cursor.fetchall():
            rows.append(
                {
                    "id": row[0],
                    "domain": row[1],
                    "interval_minutes": row[2],
                    "created_at": row[3],
                }
            )
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
        cursor = await self._conn.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
        deleted = cursor.rowcount > 0
        if deleted:
            await self._conn.execute("DELETE FROM findings WHERE scan_id = ?", (scan_id,))
            await self._conn.execute("DELETE FROM buckets WHERE scan_id = ?", (scan_id,))
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
            "UPDATE scans SET status=?, finished_at=?, buckets_found=?, "
            "findings_count=? WHERE id=?",
            (status, now, buckets_found, findings_count, scan_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def list_stale_running_scans(self, cutoff_ts: str) -> list[dict[str, Any]]:
        """Running scans whose started_at is older than the cutoff (ISO)."""
        cursor = await self._conn.execute(
            "SELECT id, started_at FROM scans "
            "WHERE status = 'running' AND started_at IS NOT NULL "
            "AND started_at <= ?",
            (cutoff_ts,),
        )
        rows = []
        for row in await cursor.fetchall():
            rows.append({"scan_id": row[0], "started_at": row[1]})
        return rows

    async def get_all_scans(self, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """Return all scans with total count."""
        count_cursor = await self._conn.execute("SELECT COUNT(*) FROM scans")
        total = (await count_cursor.fetchone())[0]

        row_cursor = await self._conn.execute(
            "SELECT id, domain_id, scanner_name, status, buckets_found, "
            "findings_count, started_at, finished_at "
            "FROM scans ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = []
        for row in await row_cursor.fetchall():
            rows.append(
                {
                    "scan_id": row[0],
                    "domain_id": row[1],
                    "scanner_name": row[2],
                    "status": row[3],
                    "buckets_found": row[4],
                    "findings_count": row[5],
                    "started_at": row[6],
                    "finished_at": row[7],
                }
            )
        return {"scans": rows, "total": total}

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    async def create_project(self, name: str, description: str = "") -> int:
        """Insert a project. Raises ValueError on duplicate name."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            cursor = await self._conn.execute(
                "INSERT INTO projects (name, description, created_at) VALUES (?, ?, ?)",
                (name, description, now),
            )
        except Exception as exc:
            raise ValueError(f"Project '{name}' already exists") from exc
        await self._conn.commit()
        return cursor.lastrowid

    async def list_projects(self) -> dict[str, Any]:
        """Return projects with per-project counts and last scan time."""
        rows = [self._project_counts_row(row) for row in await self._fetch_project_aggregates(None)]
        return {"projects": rows, "total": len(rows)}

    def _project_counts_row(self, row: Any) -> dict[str, Any]:
        """Build a project dict with counts from an aggregate row."""
        return {
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "created_at": row[3],
            "domain_count": row[4],
            "scan_count": row[5],
            "findings_count": row[6] if row[6] is not None else 0,
            "last_scan_at": row[7],
        }

    async def _fetch_project_aggregates(self, project_id: int | None) -> list[Any]:
        """Fetch project rows with counts; optional single-project filter."""
        where = "WHERE p.id = ?" if project_id is not None else ""
        params: list[Any] = [project_id] if project_id is not None else []
        cursor = await self._conn.execute(
            "SELECT p.id, p.name, p.description, p.created_at, "
            "COUNT(DISTINCT d.id), COUNT(DISTINCT s.id), "
            "SUM(s.findings_count), MAX(s.started_at) "
            "FROM projects p "
            "LEFT JOIN domains d ON d.project_id = p.id "
            "LEFT JOIN scans s ON s.domain_id = d.id "
            f"{where} "
            "GROUP BY p.id ORDER BY p.id",
            params,
        )
        return await cursor.fetchall()

    async def get_project(self, project_id: int) -> dict[str, Any] | None:
        """Return one project dict with counts, or None."""
        rows = await self._fetch_project_aggregates(project_id)
        if not rows:
            return None
        return self._project_counts_row(rows[0])

    async def update_project(
        self,
        project_id: int,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any] | None:
        """Patch a project. Returns the updated row or None if missing."""
        current = await self.get_project(project_id)
        if current is None:
            return None
        new_name = name if name is not None else current["name"]
        new_desc = description if description is not None else current["description"]
        await self._conn.execute(
            "UPDATE projects SET name = ?, description = ? WHERE id = ?",
            (new_name, new_desc, project_id),
        )
        await self._conn.commit()
        return {
            "id": project_id,
            "name": new_name,
            "description": new_desc,
            "created_at": current["created_at"],
        }

    async def delete_project(self, project_id: int) -> bool:
        """Delete a project and cascade its domains' scans. True if deleted."""
        domain_cursor = await self._conn.execute(
            "SELECT id FROM domains WHERE project_id = ?", (project_id,)
        )
        domain_ids = [row[0] for row in await domain_cursor.fetchall()]
        for domain_id in domain_ids:
            scan_cursor = await self._conn.execute(
                "SELECT id FROM scans WHERE domain_id = ?", (domain_id,)
            )
            for (scan_id,) in await scan_cursor.fetchall():
                await self._delete_scan_rows(scan_id)
            await self._conn.execute("DELETE FROM domains WHERE id = ?", (domain_id,))
        cursor = await self._conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        deleted = cursor.rowcount > 0
        await self._conn.commit()
        return deleted

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        """Return a user dict (no password hash) or None."""
        cursor = await self._conn.execute(
            "SELECT id, username, role FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {"id": row[0], "username": row[1], "role": row[2] or "viewer"}

    async def list_users(self) -> list[dict[str, Any]]:
        """Return all users (no password hashes)."""
        cursor = await self._conn.execute("SELECT id, username, role FROM users ORDER BY id")
        rows = []
        for row in await cursor.fetchall():
            rows.append(
                {
                    "id": row[0],
                    "username": row[1],
                    "role": row[2] or "viewer",
                }
            )
        return rows

    async def set_user_role(self, user_id: int, role: str) -> bool:
        """Update a user's role. True if a row was updated."""
        cursor = await self._conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        await self._conn.commit()
        return cursor.rowcount > 0

    async def delete_user(self, user_id: int) -> bool:
        """Delete a user. True if deleted."""
        cursor = await self._conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await self._conn.commit()
        return cursor.rowcount > 0

    async def count_admins(self) -> int:
        """Number of admin users."""
        cursor = await self._conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'")
        return (await cursor.fetchone())[0]

    # ------------------------------------------------------------------
    # Scan listing / detail / persistence
    # ------------------------------------------------------------------

    async def list_scans(
        self,
        project_id: int | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return scans with domain/project names, filtered and capped."""
        clauses = []
        params: list[Any] = []
        if project_id is not None:
            clauses.append("s.domain_id IN (SELECT id FROM domains WHERE project_id = ?)")
            params.append(project_id)
        if status is not None:
            clauses.append("s.status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        count_cursor = await self._conn.execute(f"SELECT COUNT(*) FROM scans s {where}", params)
        total = (await count_cursor.fetchone())[0]

        row_cursor = await self._conn.execute(
            "SELECT s.id, s.domain_id, d.domain_name, "
            "COALESCE(d.project_id, s.project_id) AS project_id, "
            "p.name, s.scanner_name, s.status, s.buckets_found, "
            "s.findings_count, s.started_at, s.finished_at "
            "FROM scans s "
            "JOIN domains d ON d.id = s.domain_id "
            "LEFT JOIN projects p ON p.id = d.project_id "
            f"{where} ORDER BY s.id DESC LIMIT ?",
            [*params, limit],
        )
        rows = []
        for row in await row_cursor.fetchall():
            rows.append(
                {
                    "scan_id": row[0],
                    "domain_id": row[1],
                    "domain_name": row[2],
                    "project_id": row[3],
                    "project_name": row[4],
                    "scanner_name": row[5],
                    "status": row[6],
                    "buckets_found": row[7],
                    "findings_count": row[8],
                    "started_at": row[9],
                    "finished_at": row[10],
                }
            )
        return {"scans": rows, "total": total}

    async def get_scan_detail(self, scan_id: int) -> dict[str, Any] | None:
        """Return the full scan bundle: scan row, findings, buckets."""
        scan = await self._fetch_scan_row(scan_id)
        if scan is None:
            return None
        return {
            "scan": scan,
            "findings": await self.get_scan_findings(scan_id),
            "buckets": await self._get_scan_buckets(scan_id),
        }

    async def _fetch_scan_row(self, scan_id: int) -> dict[str, Any] | None:
        """Return one scan row with domain/project names, or None."""
        cursor = await self._conn.execute(
            "SELECT s.id, s.domain_id, d.domain_name, "
            "COALESCE(d.project_id, s.project_id) AS project_id, "
            "p.name, s.scanner_name, s.status, s.buckets_found, "
            "s.findings_count, s.started_at, s.finished_at "
            "FROM scans s "
            "JOIN domains d ON d.id = s.domain_id "
            "LEFT JOIN projects p ON p.id = d.project_id "
            "WHERE s.id = ?",
            (scan_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "scan_id": row[0],
            "domain_id": row[1],
            "domain_name": row[2],
            "project_id": row[3],
            "project_name": row[4],
            "scanner_name": row[5],
            "status": row[6],
            "buckets_found": row[7],
            "findings_count": row[8],
            "started_at": row[9],
            "finished_at": row[10],
        }

    async def _get_scan_buckets(self, scan_id: int) -> list[dict[str, Any]]:
        """Return bucket rows for one scan."""
        cursor = await self._conn.execute(
            "SELECT id, name, objects_count FROM buckets WHERE scan_id = ? ORDER BY id",
            (scan_id,),
        )
        rows = []
        for row in await cursor.fetchall():
            rows.append(
                {
                    "id": row[0],
                    "name": row[1],
                    "objects_count": row[2],
                }
            )
        return rows

    async def persist_scan_results(
        self, scan_id: int, buckets: list[Any], findings: list[Any]
    ) -> None:
        """Persist ScanResult buckets and findings rows for one scan."""
        for bucket in buckets or []:
            await self._insert_scan_bucket(scan_id, bucket)
        for finding in findings or []:
            await self._insert_scan_finding(scan_id, finding)
        await self._conn.execute(
            "UPDATE scans SET buckets_found = ?, findings_count = ? WHERE id = ?",
            (len(buckets or []), len(findings or []), scan_id),
        )
        await self._conn.commit()

    async def _insert_scan_bucket(self, scan_id: int, bucket: Any) -> None:
        """Insert one bucket row from a ScanResult bucket object."""
        await self._conn.execute(
            "INSERT INTO buckets (scan_id, name, objects_count) VALUES (?, ?, ?)",
            (
                scan_id,
                getattr(bucket, "bucket_name", None) or "",
                len(getattr(bucket, "objects", []) or []),
            ),
        )

    async def _insert_scan_finding(self, scan_id: int, finding: Any) -> None:
        """Insert one finding row from a ScanResult finding object."""
        await self._conn.execute(
            "INSERT INTO findings (scan_id, bucket, object, rule, "
            "severity, line, match) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                scan_id,
                getattr(finding, "bucket_name", None) or "",
                getattr(finding, "object_key", None),
                getattr(finding, "rule_id", None),
                getattr(finding, "severity", None) or "low",
                getattr(finding, "line", None),
                getattr(finding, "match", None),
            ),
        )

    async def get_scan_findings(self, scan_id: int) -> list[dict[str, Any]]:
        """Return all findings for one scan, including the match snippet."""
        cursor = await self._conn.execute(
            "SELECT id, bucket, object, rule, severity, line, match "
            "FROM findings WHERE scan_id = ? ORDER BY id",
            (scan_id,),
        )
        rows = []
        for row in await cursor.fetchall():
            rows.append(
                {
                    "id": row[0],
                    "bucket": row[1],
                    "object": row[2],
                    "rule": row[3],
                    "severity": row[4],
                    "line": row[5],
                    "match": row[6],
                }
            )
        return rows

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    async def get_dashboard_overview(self) -> dict[str, Any]:
        """Build a dashboard overview with domain and scan statistics."""
        # Total domains
        count1 = await self._conn.execute("SELECT COUNT(*) FROM domains")
        total_domains = (await count1.fetchone())[0]

        # Active domains (enabled=1)
        count2 = await self._conn.execute("SELECT COUNT(*) FROM domains WHERE enabled=1")
        active_domains = (await count2.fetchone())[0]

        # Total scans
        count3 = await self._conn.execute("SELECT COUNT(*) FROM scans")
        total_scans = (await count3.fetchone())[0]

        # Total buckets found
        row1 = await self._conn.execute("SELECT COALESCE(SUM(buckets_found), 0) FROM scans")
        total_buckets = (await row1.fetchone())[0]

        # Total findings
        row2 = await self._conn.execute("SELECT COALESCE(SUM(findings_count), 0) FROM scans")
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
            domains_rows.append(
                {
                    "domain_id": row[0],
                    "domain_name": row[1],
                    "scan_count": row[2] if row[2] is not None else 0,
                    "total_buckets": row[3] if row[3] is not None else 0,
                    "total_findings": row[4] if row[4] is not None else 0,
                }
            )

        return {
            "total_domains": total_domains,
            "active_domains": active_domains,
            "total_scans": total_scans,
            "total_buckets_found": total_buckets or 0,
            "total_findings": total_findings or 0,
            "domains": domains_rows,
        }
