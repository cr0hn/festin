# Architecture

How FestIn is built, in one pass.

## Two systems, one package

```mermaid
flowchart LR
    subgraph scanner["Scanner CLI (festin scan)"]
        A[domains / file] --> B[crawler<br/>httpx]
        B --> C[DNS discovery<br/>dnspython]
        B --> P[permutations]
        P --> D[prober<br/>S3 / GCS / Azure]
        D --> E[secret analysis<br/>rules engine]
        E --> O[exports<br/>csv / sarif / jsonl]
        E --> S[state / checkpoint]
    end
```

```mermaid
flowchart TB
    subgraph svc["Dashboard service (festin-serve)"]
        UI[SPA<br/>static/] --> API[router.py<br/>/api/v1]
        API --> MW[JWT middleware]
        API --> DB[(database.py<br/>aiosqlite)]
        API --> SCH[scheduler.py<br/>10s loop]
        API --> TASK[background task<br/>scan_runner]
        SCH --> TASK
        TASK --> DB
    end
```

## Package layout

```
festin/
├── cli.py               typer CLI: scan / serve / version
├── scan_runner.py       scan pipeline (also called by the service)
├── s3.py                bucket probing across cloud providers
├── secrets.py           secret-detection rules
├── permutations.py      bucket-name permutation engine
├── analysis.py          object content analysis
├── exports.py           csv / sarif / jsonl writers
├── checkpoint.py        resume support
├── state.py             state files (enables diffing)
├── ratelimit.py         probe rate control
├── models.py            shared dataclasses (ScanResult, Finding, S3Bucket)
│
└── service/
    ├── serve.py         app factory, middleware wiring, entrypoint
    ├── router.py        all REST endpoints (FestinRouter)
    ├── auth.py          bcrypt + JWT + middleware
    ├── database.py      aiosqlite layer (schema + CRUD)
    ├── scheduler.py     periodic scan loop + orchestration
    ├── queues.py        in-memory queue (Redis path unwired)
    └── static/          the SPA (vanilla JS, hash routing)
```

!!! warning "Two HTTP servers live in the repo"
    `festin/api.py` is a legacy read-only state-file server.
    The real dashboard is `festin/service/serve.py`. Don't confuse them.

## Request lifecycle (scan trigger)

```mermaid
sequenceDiagram
    actor U as UI/curl
    participant R as router.py
    participant DB as database.py
    participant T as _execute_scan (task)
    participant S as scan_runner

    U->>R: POST /scans/run-scan {domains, project_id}
    R->>DB: find/create domain, create_scan (status=pending)
    R-->>U: 202 {scan_id, job_id}
    R->>T: asyncio.create_task
    T->>DB: update status=running
    T->>S: run_scan(namespace, domains)
    S-->>T: ScanResult(buckets, findings)
    T->>DB: persist_scan_results (real rows)
    T->>DB: update status=completed
    Note over U: SPA polls /scans/{id} every 4s<br/>until terminal status
```

## Authentication flow

```mermaid
sequenceDiagram
    participant C as client
    participant MW as JWTMiddleware
    participant H as handler
    participant A as AuthService

    C->>MW: request + Bearer token
    MW->>MW: verify signature + expiry
    alt valid token
        MW->>H: request["user"] = username
        H->>A: role check for mutations (_admin_guard)
    else no token + /auth/register
        MW->>H: anonymous pass-through
        Note over H: handler decides:<br/>user_count==0 → admin bootstrap<br/>else 401
    else no token, any other path
        MW-->>C: 401
    end
```

The invariant: **the middleware identifies, the handler authorizes.**
`/auth/register` is never exempted from the middleware — exempting it made
valid admins look anonymous (a real bug; see [ADR #4](design-decisions.md)).

## Data model

```mermaid
erDiagram
    PROJECTS ||--o{ DOMAINS : contains
    DOMAINS ||--o{ SCANS : has
    SCANS ||--o{ FINDINGS : yields
    SCANS ||--o{ BUCKETS : finds
    USERS }o--o{ PROJECTS : "access (UI-level)"
```

| Table | Key columns |
|---|---|
| `projects` | id, name (unique), description |
| `domains` | id, domain_name, project_id, enabled |
| `scans` | id, domain_id, project_id, status, buckets_found, findings_count, started_at, finished_at |
| `findings` | id, scan_id, bucket, object, rule, severity, line, match (redacted) |
| `buckets` | id, scan_id, name, objects_count |
| `scheduled_scans` | id, domain, interval_minutes |
| `users` | id, username, password_hash (bcrypt), role |

Migrations are idempotent: `CREATE TABLE IF NOT EXISTS` + guarded `ALTER TABLE` inside `Database.migrate()` (executescript).

## Scaling story

The deliberate seams for growth (all inside `festin/service/`):

1. **Database** — swap `Database` internals for asyncpg/Postgres. Callers never touch raw connections.
2. **Scan execution** — replace the in-process `asyncio.create_task` in `run-scan` with a queue consumer; `queues.py` already sketches the Redis path.
3. **Scheduler** — extract to its own deployment; it only needs the DB.

See [HA patterns](../deploy/ha.md) for how these compose.