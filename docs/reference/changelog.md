# Changelog

## 0.4.0 — Unreleased

### Added
- **PostgreSQL support**: dual-backend `Database` — SQLite (aiosqlite,
  default) or PostgreSQL (asyncpg) selected by DSN (`FESTIN_DB_DSN` /
  `--db-dsn`). 16 integration tests (skipped unless `FESTIN_TEST_PG_DSN`).
- **streaQ scan queue** (`FESTIN_QUEUE=streaq`): scans enqueue onto Redis
  Streams; a separate `festin-worker` process consumes them. In-flight
  scans survive API restarts; Redis unreachable → automatic fallback to
  memory mode.
- **Rate limiting**: login + register limited per IP (5 attempts / 60 s
  default, env-tunable), 429 + `Retry-After` on excess.
- **Scan watchdog**: scans stuck in `running` beyond `scan_timeout` are
  marked `failed` by the scheduler loop.
- **Docker workflow**: multi-arch image built and pushed to Docker Hub on
  every master push / `v*` tag (cr0hn/festin).
- **Auth matrix tests**: full register/login/role matrix automated
  (bootstrap admin → anon 401 → admin creates viewer → viewer 403).

### Changed
- `festin-worker` console entry point added (`pip install 'festin[queue]'`).
- Author metadata: Daniel Alfocea <daniel@danielalfocea.com>.

### Removed
- Demo database with known credentials removed from the repository.
- Legacy unwired FastAPI routers (`festin/service/api/`) deleted.

## 0.3.1 — 2026-09-08

- PyPI metadata: new tagline + author (Daniel Alfocea).

## Unreleased

### Added
- **Multi-project**: `projects` table, domain scoping, project CRUD API with cascades; `run-scan` accepts `project_id`.
- **Home dashboard**: counter strip, full-width EXPOSURE TREND hero card (data-generated headline), 14-day sparklines, severity bars, scan-outcomes stacked bar, top domains, project ranking.
- **User management**: `/users` API + UI (create, role change, delete) with admin guards; login returns `username` and `role`.
- **Scan result persistence**: `persist_scan_results()` stores real `buckets`/`findings` rows (previously only counters).
- **Scan detail API**: `GET /scans/{id}` bundle (scan + findings + buckets).
- Docs site (MkDocs Material) themed after the dashboard.

### Changed
- `POST /auth/login` now also returns `username` and `role`.
- `GET /stats` timeline includes per-day `critical`/`high` counts (two-query implementation to avoid join double-counting).
- SPA rebuilt: hash routing, ops-console design, two-step destructive actions, no browser dialogs.
- Static assets cache-busted with `?v=N`; `index.html` served with `Cache-Control: no-store`.

### Fixed
- Scan results were never persisted as rows — the dashboard had no results to show.
- `view-findings` section lost from `index.html` made the SPA inert on every route change.
- Self-demotion lockout: own-user role selector disabled in Users view.
- Demo DB scan #1 `started_at` inconsistent with its findings' dates.

## 0.3.0 — 2026-09

- Multi-user JWT auth (HS256, 60 min), first-user bootstrap = admin.
- Complete dashboard API: projects, domains, scans, findings, buckets, schedules, users.
- Working scan pipeline: `POST /scans/run-scan` → background task → persisted results.
- Scheduled scans (10 s scheduler tick, in-memory `last_run`).
- SPA with login/register bootstrap flow.

## 0.2.0

- Scanner CLI stabilized: permutations, multi-cloud probing, Tor support, checkpoint/resume, exports (csv/sarif/jsonl).
- `festin.service` package introduced (first FastAPI iteration, later replaced by aiohttp).

## 0.1.0

- Initial scanner: HTTP crawl, DNS discovery, secret detection, state files.