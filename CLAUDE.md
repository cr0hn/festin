# CLAUDE.md — FestIn

LLM startup guide. **Read in layers**: for a small task, levels 0-2 are
enough. Level 3+ only when you're about to touch code.

---

## Level 0 — Identity (10 seconds)

**FestIn**: exposed S3 bucket finder (CLI) + multi-user monitoring web
dashboard (service). Python 3.13+, uv, pytest.

- Scanner CLI: `festin/` — stable, mature, don't touch without need.
- Dashboard: `festin/service/` — new (Sept 2026), aiohttp + SQLite + JWT.
- **355 tests green (+16 PG integration skipped). All work pushed to origin.**

```bash
uv run pytest --timeout=30 -q     # full suite (~75s). ALWAYS with timeout
uv run python -m festin.service.serve   # dashboard on :8420
```

---

## Level 1 — Map (30 seconds)

```
festin/                → scanner CLI (cli.py = typer; scan_runner.py = pipeline)
festin/service/        → dashboard: serve.py (factory), router.py (API),
                         auth.py (JWT/bcrypt), database.py (dual-backend
                         sqlite/asyncpg), scheduler.py (loop + watchdog),
                         queues.py (memory|streaq), static/ (SPA vanilla JS)
tests/                 → 24 files; test_service*.py + test_auth_matrix.py = dashboard
docs/                  → MkDocs Material site (docs/usage/, docs/deploy/, ...)
```

**Map trap:** there are TWO HTTP servers. `festin/api.py` is legacy
(state-file reads only). The live one is `festin/service/serve.py`.

---

## Level 2 — Session rules (mandatory)

1. **Tests always with double timeout**: `timeout 130 uv run pytest --timeout=30 -q`.
   Some tests hang indefinitely without it. This bit twice.
2. **NEVER `git push`** without explicit user order.
3. **VERSION POLICY — NEVER bump the minor without explicit user order.**
   Patch bumps (0.4.0 → 0.4.1) are autonomous when a publish is needed.
   Minor decisions (0.4.0 → 0.5.0) belong to Daniel.
4. **JS after surgery**: `node --check` is insufficient (runtime errors are
   not syntax). Verify every called function is defined:
   `grep -o 'funcName(' festin/service/static/js/app.js | head` vs its
   `function funcName(`. This caused two bugs (`startSession`, `refreshAll`).
5. **Frozen contracts** (breaking = regression):
   - `GET /queues/schedule` returns key `scheduled` (not `schedules`)
   - `POST /auth/register` is NOT exempt in the JWT middleware (the admin
     must be able to authenticate there; anonymous bootstrap passes via the
     no-header branch)
   - `serve` CLI uses `--db` (not `--state`)
6. **UI verification**: always headless browser against the real server
   (`localhost:8420`). `localStorage.clear()` before testing login —
   residual state lies.
7. **Multi-project**: `projects` table; every domain/scan belongs to a
   project (default id=1). `run-scan` accepts `project_id`. Real results
   (buckets/findings) are inserted as rows via `persist_scan_results` —
   never just counters.
8. **Roles**: admin manages users/projects (mutations); viewer read-only.
   YOUR OWN user's role select is disabled in the UI (self-demotion =
   lockout). Deleting the last admin → 400.
9. **All project content in English** — code comments, docs, commit
   messages, CLAUDE.md included.

---

## Level 3 — Service details (only if touching `festin/service/`)

### Auth — exact semantics
| Request | Result |
|---|---|
| register + empty DB (no token) | 201 admin (bootstrap) |
| register + Bearer admin | 201 viewer |
| register + Bearer viewer | 403 |
| anonymous register + populated DB | 401 |
| other endpoints without valid Bearer | 401 (`/health` public) |

The JWT middleware *identifies*, the handler *authorizes*. Never exempt
register in the middleware — an admin with a token would look anonymous
and get 401 (historical bug).

### Scan flow
`POST /scans/run-scan` → upsert domain → `create_scan` (DB) → background
task or streaq queue (see queue mode) runs `festin.scan_runner.run_scan` →
`update_scan_status`. Returned scan_id is the real rowid.

### Scheduler
`_scan_loop` ticks every 10s: (1) watchdog reaps `running` scans older
than `scan_timeout` → `failed`; (2) fires due scheduled_scans (in-memory
`last_run` tracking — after restart everything re-fires on the first tick).

### Database backends
`Database(dsn)` dispatches on DSN: SQLite (aiosqlite, default) or
PostgreSQL (asyncpg) when it starts with `postgres://`. Both backends
expose the identical public method set. Selection: `FESTIN_DB_DSN` /
`--db-dsn`. PG integration tests: 16, skipped unless `FESTIN_TEST_PG_DSN`.

### Queue modes
`FESTIN_QUEUE=memory` (default, in-process) or `streaq` (Redis Streams +
separate `festin-worker` consumer; falls back to memory if Redis is
unreachable at start). Rate limiting: login/register 5 attempts / 60 s
per IP (env-tunable).

---

## Level 4 — Technical traps (read BEFORE editing)

| Area | Trap |
|---|---|
| aiohttp 3.14 | `@web.middleware` on instances doesn't dispatch new-style → use the `_wrap_middleware()` pattern from serve.py; `JWTMiddleware.__middleware_version__ = 1` untouched |
| auth | passlib 1.7 crashes with bcrypt 4.x → auth.py uses bcrypt directly (72B truncation). Don't reinstall passlib |
| aiosqlite | multi-statement DDL = `executescript()` (already in `migrate()`) |
| pyproject | `[dependency-groups.dev]` as a table does NOT parse in uv → `[dependency-groups]` with `dev = [...]`; backend = `hatchling.build` |
| tests | new constants-only module → add to `constant_only` in tests/test_complexity.py |
| CLI tests | pin typer model flags (`--db`); changing the CLI means updating tests/test_cli.py |
| SPA | no alert() (blocks headless) → use `showFlash()`; response keys aligned with app.js |
| assets | bump `?v=N` in index.html on every JS/CSS change or browsers serve stale copies |
| index.html edits | validate tag balance (a lost tag once made the whole SPA inert) |
| PyPI | every master push rebuilds the wheel → same-version upload = 400 file-exists. Bump patch (0.4.X) per release; NEVER bump minor without explicit order |

---

## Level 5 — Where to go deeper

| Need | Go to |
|---|---|
| Architecture, auth flow, API reference | `docs/` (MkDocs site) — published at cr0hn.github.io/festin |
| Why each technical decision (ADR) | `docs/reference/design-decisions.md` |
| Operation, credentials, troubleshooting | `docs/reference/runbook.md` |
| Deployment (Docker/K8s/HA) | `docs/deploy/` |
| Development rules | `docs/reference/development.md` |

## Level 6 — Delivery checklist

```bash
timeout 130 uv run pytest --timeout=30 -q        # → 355 passed, 16 skipped
node --check festin/service/static/js/app.js     # + manual definition check (rule 4)
uv run python -m festin.service.serve &          # smoke: /api/v1/health → {"status":"ok"}
# If auth touched: curl register/login with the Level 3 matrix
# If UI touched: headless browser with clean localStorage
```

Commit with a descriptive message (repo convention: `type(scope): summary`
— see `git log`). No push.

---

## Level 7 — Legacy files (do not extend)

`docs/PROJECT.md`, `docs/RUNBOOK.md`, `docs/DESIGN_DECISIONS.md` are the
old internal Spanish docs — kept for history, excluded from the MkDocs
site, superseded by the English `docs/` site. Don't update them; the
MkDocs site is the single source of truth.