# FestIn Monitoring Dashboard — Project State & Design Decisions

## Quick Overview (3 seconds)

**Qué es:** Un dashboard web para monitorización de buckets S3 expuestos públicamente. Extiende el escáner CLI existente (`festin scan`) añadiendo una capa de servicio persistente con cola Redis, scheduler automático y REST API con autenticación.

**Por qué existe:** El escáner original era un-command-line-tool-que-se-queda-en-memoria. Ahora los buckets descubiertos se mantienen en BD, se rescanean periódicamente por dominio, y se sirven через HTTP API + SPA para equipos de ciberseguridad.

**Estado actual:** 13 archivos en `festin/service/` (database.py, auth.py, queues.py, scheduler.py, app.py, api/*.py, static/*.html/.css/.js) — funcional pero sin tests completos ni smoke verification final. Falta pulir la integración con el CLI existente (`festin serve` vs `python -m festin.service cli`).

---

## Architecture Summary (30 seconds)

### The Problem Solved
```
Before: festin scan example.com → outputs to stdout → process exits
After:  festin-serve launches FastAPI SPA → monitors domains persistently → schedules rescans via Redis queues → serves results over HTTP API
```

### Core Flow
1. `festin-serve` starts → creates `FestInApp` → initialises SQLite/PostgreSQL DB → runs migrations → creates admin user via bcrypt
2. Scheduler loads enabled domains from DB → registers per-domain scan loops (asyncio.gather bounded by semaphore)
3. Each domain loop calls `scan_runner.run_scan()` → pushes results to Redis stream queue → persists to scans table
4. SPA frontend polls `/api/v1/scans/overview` every 30s → displays buckets, findings, scan status per domain
5. User can trigger manual rescans via SPA buttons → POST to `/api/v1/domains/rescan/{id}` → scheduler picks up immediately

### Tech Stack Decisions (with rationale)

| Decision | What | Why |
|----------|------|-----|
| **FastAPI** | Web framework for SPA serving + REST API | Built-in async support, auto docs (Swagger UI at /docs), minimal boilerplate for middleware/CORS. More suitable than aiohttp for a modern dashboard. |
| **SQLite default / PostgreSQL optional** | Dual backend: SQLite via aiosqlite (always works) or PostgreSQL via asyncpg (for prod clusters) | SQLite is zero-config, perfect for single-host monitoring. PostgreSQL allows horizontal scaling later. Both share the same schema and code paths. |
| **Redis streams (streaq pattern)** | Task queue with XADD/XREADGROUP for reliable delivery | Faster than pub/sub for task processing. Supports consumer groups (multiple workers). Handles backpressure naturally via stream length limits. coredis provides typed Redis client with async support. |
| **JWT + bcrypt** | Stateless auth via HS256 tokens, password hashing via passlib [bcrypt] | Simple token-based auth without session store overhead. bcrypt handles salt automatically, safe for passwords. No need for OAuth unless integrating with SSO later. |
| **Alpine.js + TailwindCSS CDN** | SPA frontend with no build step | Alpine.js provides reactive state management in vanilla JS — no Webpack/Vite needed. Tailwind via CDN avoids npm ecosystem. Perfect for internal dashboards where speed-to-value > performance-optimisation. |
| **streaq library** | Redis stream queue system from tastyware/streaq | 14x faster than arq (arbitrary benchmark). Supports cron jobs, task dependencies, priority queues. Mature enough (v7.x) with good test coverage. MIT license compatible with FestIn's free-for-personal-use model. |

---

## Component Breakdown (Layer by Layer)

### Level 1: Data Layer (`database.py`)
**Purpose:** SQLite + asyncpg bridge providing unified CRUD operations for domains, scans, and users.

**Key Design Choices:**
- `connect()` detects backend from connection string prefix (`sqlite:///` or `postgres://`)
- Shared DDL templates per engine type (SQLITE/POSTGRES constants at module level)
- Migrations table tracks applied schema versions; automatic first-run migration creates all tables
- All methods are async via aiosqlite for SQLite, asyncpg pool for PostgreSQL

**API Surface:**
```python
# Domain CRUD
db.create_domain(domain_name, enabled=True, ...) → int (id)
db.get_domain(id) → dict | None
db.list_domains(offset=0, limit=100) → {"domains": [...], "total": N}
db.update_domain(id, **fields) → bool
db.delete_domain(id) → bool

# Scan CRUD  
db.create_scan(domain_id, scan_id) → int (id)
db.update_scan_status(scan_id_internal, status="completed", buckets_found=3) → bool
db.get_domain_scans(domain_id, limit=50) → list[dict]
db.get_scan_by_external_id(external_id) → dict | None
db.get_all_scans(offset=0, limit=100) → {"scans": [...], "total": N}

# Users
db.user_exists() → bool
db.create_user(username, pw_hash) → int (id)
db.get_user_by_username(username) → dict | None

# Dashboard aggregation (raw SQL LEFT JOINs)
db.get_dashboard_overview() → {total_domains, active_domains, total_scans, domains: [...]}
```

**Complexity Gate:** All functions ≤10 cyclomatic complexity per radon. Complex SQL queries encapsulated in named helper methods (_row_to_domain, _scan_row_to_dict).

---

### Level 2: Auth Layer (`auth.py`)
**Purpose:** Password hashing + JWT token management for user authentication.

**Design Decisions:**
- `PasswordHasher`: sync bcrypt via passlib [bcrypt] — fast enough (<10ms per hash) to call in async context safely
- `TokenService`: HS256 JWT with username as subject claim, expiry set to 60 minutes
- `AuthService`: combines user management + auth logic; auto-generates random admin password on first startup if not provided

**API Surface:**
```python
auth_svc.init_admin(username="admin", password=None) → bool (created/skipped)
auth_svc.login(username, password) → dict[access_token, token_type]
auth_svc.verify(token) → dict[user_id, username, role] | None
```

**Security Notes:** Passwords never stored in plaintext. Tokens use HS256 which is sufficient for internal service auth (no need for asymmetric keys unless distributing to external clients). JWT expiry is short (60min) requiring periodic re-auth or session refresh.

---

### Level 3: Queue Layer (`queues.py`)
**Purpose:** Redis stream-based task queue for reliable scan job delivery + pub/sub for real-time updates.

**Streaq Pattern Implementation:**
- `FestinQueue`: XADD/XREADGROUP flow mimicking streaq's internal pattern. Jobs pushed to `festin:scans:stream` with auto-generated IDs. Consumers ACK messages after processing.
- `EventBroker`: Pub/sub via Redis PUBLISH/SUBSCRIBE channels for real-time scan status updates (scan.started, scan.completed, scan.failed).
- `StatusTracker`: Hash-based Redis storage for per-scan-status metadata (started_at, finished_at, buckets_count, findings_count).

**Why not pub/sub alone?** Streams provide reliable delivery (XACK) and consumer groups (multiple workers processing same queue). Pub/sub is fire-and-forget — good for real-time SPA updates but insufficient for task orchestration.

**API Surface:**
```python
queue.push_scan_job({"domain_id": 1, "domain": "example.com", ...}) → str (task_id)
queue.pop_scan_job(timeout=1.0) → dict | None   # blocking pop
broker.subscribe("scan.started") → asyncio.Queue  # for SPA updates
status_tracker.mark_completed(scan_id, domain_id, buckets_count, findings_count) → None
```

**Complexity Note:** Queue internals deliberately kept simple to avoid RabbitMQ/Kafka complexity. If FestIn scales beyond single-host monitoring, migration path exists to full Redis Cluster with sentinel nodes.

---

### Level 4: Scheduler Layer (`scheduler.py`)
**Purpose:** Per-domain periodic scanning backed by asyncio.gather + Redis stream queue.

**Core Flow:**
1. `FestInScheduler.start()` loads all enabled domains from DB
2. For each domain, registers a background loop via `asyncio.create_task(self._domain_loop(...))`
3. Each loop runs: `scan_runner.run_scan()` → pushes results to Redis stream → persists to scans table
4. Loops are bounded by max_concurrent_scans semaphore (default 3) preventing overwhelming of target domains
5. Manual trigger via `trigger_scan(domain_id, domain_name, options)` bypasses scheduled intervals

**ScanOrchestrator Integration:**
```python
orchestrator = ScanOrchestrator(db)
result = await orchestrator.run_scan(domain_id=1, domain_name="example.com", options={})
# result: {"scan_record_id": 5, "success": True, "buckets_found": 3}
```

**Design Choices:**
- Semaphore bound prevents scanning too many domains concurrently (rate limiting at scheduler level)
- Each domain scan runs independently; failure in one doesn't block others
- Scheduler lifecycle managed by FastAPI lifespan (startup/shutdown events)

---

### Level 5: App Layer (`app.py`)
**Purpose:** FastAPI application factory with SPA serving and lifecycle management.

**FestInApp Factory Pattern:**
```python
festin_app = FestInApp(
    db_path="sqlite:///festin.db",
    admin_username="admin",
    admin_password=None,  # auto-generated random
    jwt_secret="change-me-in-production"
)
app = create_app(festin_app)
uvicorn.run(app, host="0.0.0.0", port=8000)
```

**Lifecycle:**
- `startup()`: creates DB connection → runs migrations → initializes admin user → starts scheduler
- `shutdown()`: stops scheduler (drains ongoing scans) → closes DB connection

**SPA Serving:** Static files at `/static/` serve index.html which loads TailwindCSS + Alpine.js from CDN. SPA handles all client-side routing via hash fragments (#dashboard, #domains).

---

### Level 6: API Routers (`api/*.py`)
**Purpose:** REST endpoints for domain CRUD and scan execution/status queries.

**Router Structure:**
- `api/__init__.py`: Injector functions (set_db, get_db, set_scheduler, get_scheduler) — called by FastAPI lifespan during startup to wire up dependencies
- `api/domains.py`: Domain CRUD + rescan triggers
- `api/scans.py`: Scan list/status queries + dashboard overview aggregation

**Endpoints:**
```
GET  /api/v1/domains/list             → paginated domain list
POST /api/v1/domains/add              → add new domain
DELETE /api/v1/domains/remove/{id}    → delete domain  
POST /api/v1/domains/rescan/{id}      → trigger manual scan
GET  /api/v1/scans/list               → paginated scan list
GET  /api/v1/scans/domain/{id}        → scans for specific domain
GET  /api/v1/scans/overview           → aggregated dashboard data
```

**Auth:** All endpoints expect `Authorization: Bearer <token>` header. Health endpoint (`/api/v1/health`) is public (no auth required).

---

### Level 7: Frontend SPA (`static/index.html`, `.css`, `.js`)
**Purpose:** Single-page dashboard for monitoring domains, viewing scan results, and triggering rescans.

**Tech Choices:**
- **TailwindCSS via CDN**: Utility-first CSS framework avoiding build step. Loads from `cdn.tailwindcss.com`
- **Alpine.js 3.x**: Reactive state management in vanilla JS. Handles x-data bindings, event listeners (@click, @submit.prevent), conditional rendering (x-show)
- **No bundlers**: Single HTML file with inline JS/CSS references to /static/js/app.js and /static/css/app.css

**SPA Structure:**
1. **Login Page** (full-screen centered): Username/password form → POST to `/api/v1/auth/login` → stores JWT in localStorage
2. **Dashboard View**: Summary cards (Total Domains, Active Scans, Buckets Found, Critical Findings) → Domain grid with per-domain stats and "Rescan" action buttons
3. **Domains View**: Add domain form + Table listing all domains with Rescan/Remove actions

**Client-Side Flow:**
```javascript
festinApp.$refresh()  // fetches /api/v1/scans/overview every 30s
festinApp.addDomain()  // POST /api/v1/domains/add {domain_name}
festinApp.rescanDomain(dom)  // POST /api/v1/domains/rescan/{id}
```

**Progressive Disclosure Design:** SPA loads all JS at once but renders content progressively: login → dashboard → domain details. Alpine.js handles show/hide toggling without page reloads.

---

## Current Gaps & Next Steps

### Immediate Needs (Do These First)
1. **Fix indentation in auth.py** — Line 81 has mismatched indent; needs fixing before any test runs
2. **Run tests against SQLite** — `festin.service.database` should work with sqlite:/// but needs smoke test to confirm
3. **Add real Redis tests** — queues.py requires coredis for XADD/XREADGROUP; mock everything else for CI

### Medium Priority (After Smoke Test)
4. **Integrate with CLI**: Replace old `festin serve` aiohttp endpoint with FastAPI SPA launcher via `python -m festin.service cli`
5. **Add /docs auto-generated** — FastAPI provides Swagger UI at /docs by default; expose it for API exploration
6. **Add pagination controls** — Domains list and scans list need frontend pagination UI (currently backend-only)

### Long Term (Future Improvements)
7. **PostgreSQL migration path** — asyncpg support exists but needs manual testing; add to CI matrix if needed
8. **Add scan history diffs** — Diff report feature from original `festin --diff` flag isn't wired into dashboard yet  
9. **Add WebSocket for real-time updates** — Currently SPA polls every 30s; could upgrade to WebSocket for live scan progress
10. **Add role-based access control** — Current auth is single-user (admin). Add user management API + RBAC for team access

---

## Decision Log

### Why not Celery/RQ?
- These are heavyweight background task systems requiring separate worker processes
- FestIn's scheduler runs in-process with asyncio.gather; simpler architecture, fewer moving parts
- Redis stream queue provides sufficient reliability without celery/broker overhead
- If scaling to distributed workers later, migration path exists via streaq's built-in multi-worker support

### Why not Docker/K8s from day one?
- Monitoring dashboards for small teams don't need orchestration complexity initially
- Single-process FastAPI server is sufficient for 1-10 monitored domains
- Containerization (Dockerfile, k8s manifests) can be added when scaling needs emerge

### Why async SQLite over sync?
- FestIn's entire architecture is async-first (asyncio.gather, async methods throughout)
- aiosqlite provides async-compatible access to SQLite with same API surface as sync version
- Allows seamless migration to PostgreSQL via asyncpg later (both are async libraries)

### Why Tailwind CSS + Alpine.js instead of React/Vue?
- No build step required; works directly in browser via CDN links
- Smaller learning curve for security teams (vanilla JS familiar vs frameworks)
- Faster initial load time (no bundle download); better for low-bandwidth environments
- Adequate complexity for single-page dashboard with CRUD operations

### Why JWT over session cookies?
- Stateless tokens simplify horizontal scaling (no shared session store needed)
- Easier to integrate with mobile apps or CI/CD pipelines later
- Bearer token pattern widely understood by security professionals
- Short expiry (60min) limits exposure window if token is compromised
