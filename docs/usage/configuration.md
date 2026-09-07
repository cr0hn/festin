# Configuration

FestIn is configured by **CLI flags + environment variables**. There is no config file — every deployment artifact (docker-compose, k8s manifest) passes flags explicitly.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `FESTIN_JWT_SECRET` | **yes, in any shared deployment** | `festin-secret-key-change-in-production` (dev fallback) | Secret for JWT signing (HS256, 60 min expiry). |
| `FESTIN_DB_DSN` | no | SQLite path | `postgres://user:pass@host:5432/festin` selects the **PostgreSQL** backend (asyncpg). Also used by `festin-worker`. |
| `FESTIN_QUEUE` | no | `memory` | Scan queue backend: `memory` (in-process) or `streaq` (Redis Streams). See [Queue backend](#queue-backend). |
| `FESTIN_REDIS_URL` | only when `FESTIN_QUEUE=streaq` | `redis://localhost:6379/0` | Redis endpoint for the streaQ queue. |
| `FESTIN_RATE_LIMIT_ATTEMPTS` | no | `5` | Max login/register attempts per IP within the window. |
| `FESTIN_RATE_LIMIT_WINDOW` | no | `60` | Rate-limit window in seconds. |
| `TOR_SOCKS_URL` *(scanner)* | no | `socks5://127.0.0.1:9050` | Tor proxy endpoint used by `--tor` |

```bash
# generate a solid secret
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Service flags

=== "`python -m festin.service.serve`"

    ```bash
    python -m festin.service.serve \
      --host 0.0.0.0 \
      --port 8420 \
      --db /var/lib/festin/festin.db
    ```

    | Flag | Default | Description |
    |---|---|---|
    | `--host` | `127.0.0.1` | Bind address. Use `0.0.0.0` behind a proxy/container. |
    | `--port` | `8420` | Bind port. |
    | `--db` | `data/festin.db` | SQLite database path (created + migrated on start). |
    | `--db-dsn` | none | Database DSN — `postgres://user:pass@host:5432/festin` selects the **PostgreSQL** backend (asyncpg). Overrides `--db`. |
    | `--auth-file` | none | Legacy basic-auth users file (`user:pass` per line). Only used if JWT is unavailable. |

=== "`festin serve` (typer)"

    ```bash
    festin serve --host 0.0.0.0 --port 8420 --db /var/lib/festin/festin.db
    ```

    | Flag | Default | Description |
    |---|---|---|
    | `--host` | `127.0.0.1` | Bind address |
    | `--port` | `8420` | Bind port |
    | `--db` | `festin.db` | SQLite path |

=== "`festin-worker` (queue consumer)"

    ```bash
    festin-worker --db /var/lib/festin/festin.db
    # or with PostgreSQL:
    FESTIN_DB_DSN=postgres://user:pass@host:5432/festin festin-worker
    ```

    Only relevant when `FESTIN_QUEUE=streaq`. Consumes scan jobs from Redis
    Streams. Run one or more; they share the Redis queue.

## Queue backend

`FESTIN_QUEUE=memory` (default): scans execute in-process. Zero
dependencies, but a service restart kills in-flight scans.

`FESTIN_QUEUE=streaq`: the API process **enqueues** scans onto Redis
Streams ([streaQ](https://github.com/tastyware/streaq)); a separate worker
process consumes them:

```bash
pip install 'festin[queue]'
export FESTIN_QUEUE=streaq FESTIN_REDIS_URL=redis://localhost:6379/0
festin-worker --db /var/lib/festin/festin.db   # consumer process
```

Benefits: in-flight scans survive API restarts, and scanning can scale
independently (several `festin-worker` processes on the same Redis).
If Redis is unreachable at startup the service logs an error and **falls
back to memory mode** — it never crashes.

## Internal defaults (tunable in code)

| Setting | Where | Default | Notes |
|---|---|---|---|
| Scheduler tick | `festin/service/scheduler.py` | 10 s | `SchedulerConfig.check_interval` |
| Max concurrent scans | `festin/service/scheduler.py` | 3 | `max_concurrent_scans` |
| Scan timeout / watchdog | `festin/service/scheduler.py` | 600 s | scans stuck in `running` are marked `failed` after this |
| Rate-limit window | `festin/service/serve.py` | 60 s / 5 attempts | login + register, per IP |
| SPA asset cache | `festin/service/serve.py` | no-store/no-cache | shell never cached; JS/CSS revalidate |

## See also

- [Queue backend details](#queue-backend) — memory vs streaq tradeoffs.
- [High availability](../deploy/ha.md) — how PostgreSQL + streaq compose
  into multi-replica deployments.
- [Security hardening](../deploy/security.md) — proxy-level rate limiting
  as a second layer on top of the in-app limiter.
