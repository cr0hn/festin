# Design decisions (ADR summary)

Why things are the way they are — and what was rejected. The short version of the full [DESIGN_DECISIONS.md](https://github.com/cr0hn/festin/blob/master/docs/DESIGN_DECISIONS.md) in the repo.

## 1. aiohttp for the service (not FastAPI)

**Decision.** The dashboard uses aiohttp end-to-end.

**Why.** The original API was already aiohttp and the runtime is asyncio-native. Migrating to FastAPI would have meant rewriting auth middleware and static serving for zero functional gain.

**Consequence.** `festin/service/api/` contains orphaned FastAPI routers from the first design — legacy, unwired, deletion candidate.

## 2. Dual-backend database: SQLite default, PostgreSQL for scale

**Decision.** `Database(dsn)` dispatches on the DSN: SQLite (aiosqlite) by default, PostgreSQL (asyncpg) when the DSN starts with `postgres://`. Both backends expose the identical method set.

**Why this shape.** SQLite stays zero-config for single-host monitoring; PostgreSQL unlocks multi-replica HA without touching call sites. The public entrypoint never branches on the engine.

**Status.** Implemented in 0.4.0 — 16 integration tests (run against a live PG when `FESTIN_TEST_PG_DSN` is set).

## 3. JWT HS256 with env secret, no refresh tokens

**Decision.** `python-jose`, HS256, 60-minute expiry, secret from `FESTIN_JWT_SECRET` (dev fallback exists), token stored in `localStorage`.

**Rejected.** Server sessions (need shared store to scale), OAuth (no identity provider in scope), refresh tokens (the SPA just re-logs in).

**Debt.** No login rate-limit; HS256 is fine for internal tooling, not for external clients.

## 4. First-user bootstrap in the handler, not the middleware

**Decision.** `JWTMiddleware` never exempts `/auth/register`. No header → anonymous pass-through; valid header → `request["user"]` set. The handler decides by `user_count()`.

**Why.** Exempting register in the middleware made a valid admin look anonymous and get 401 — a real bug. The rule that prevents it: **the middleware identifies, the handler authorizes.**

## 5. Scans as in-process background tasks (no distributed queue)

**Decision.** `POST /scans/run-scan` persists the record and executes via `asyncio.create_task` in the same process.

**Rejected (for now).** Redis queue + worker fleet. The volume doesn't justify the moving parts; `queues.py` keeps the Redis path sketched.

**Upgrade seam.** Swap the task launch for a queue consumer — one integration point ([architecture](architecture.md#scaling-story)).

## 6. Scan results persisted as real rows

**Decision.** `persist_scan_results()` inserts actual `buckets` and `findings` rows; `findings_count` is derived.

**Why it matters.** An earlier iteration only stored counters — the dashboard had nothing to show. The invariant: *the dashboard renders rows, never aggregated counters alone.*

## 7. SPA as hand-rolled vanilla JS, hash routing

**Decision.** Single IIFE, no framework, no build step, `?v=N` cache busting.

**Why.** The UI is ~1000 lines serving 7 views; a framework would out-weigh the app. Zero-dependency also means zero supply-chain surface.

**Convention.** No `alert()/confirm()/prompt()` (they block headless testing) — destructive actions use a two-step arm button; feedback through a flash toast.

## 8. Docs theme mirrors the dashboard

**Decision.** MkDocs Material with custom `festin-dark` scheme (warm dark `#141518`, amber `#ffb454`), IBM Plex Mono everywhere.

**Why.** One visual identity from terminal to browser to docs; the docs feel like part of the product.

## Rejected ideas worth remembering

| Idea | Why rejected |
|---|---|
| FastAPI everywhere | rewrite cost, zero gain (ADR #1) |
| PostgreSQL at v1 | no multi-writer need yet; asyncpg staged |
| OAuth / SSO | no identity provider; internal tool |
| Celery/RQ workers | asyncio-native tasks are simpler and sufficient |
| React/Vue SPA | 7 views don't justify a build chain |
| `get_dashboard_overview()` endpoint | superseded by `/stats`; legacy method kept for tests |