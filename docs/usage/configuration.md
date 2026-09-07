# Configuration

FestIn is configured by **CLI flags + one environment variable**. There is no config file — every deployment artifact (docker-compose, k8s manifest) passes flags explicitly.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `FESTIN_JWT_SECRET` | **yes, in any shared deployment** | `festin-secret-key-change-in-production` (dev fallback) | HS256 signing secret for auth tokens |
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

## Internal defaults (tunable in code)

| Setting | Where | Default | Notes |
|---|---|---|---|
| Scheduler tick | `festin/service/scheduler.py` | 10 s | `SchedulerConfig.check_interval` |
| Max concurrent scans | `festin/service/scheduler.py` | 3 | `max_concurrent_scans` |
| Scan timeout | `festin/service/scheduler.py` | 600 s | `scan_timeout` |
| SPA asset cache | `festin/service/serve.py` | no-store/no-cache | shell never cached; JS/CSS revalidate |

## What is deliberately NOT configurable (yet)

- **Database engine**: SQLite via aiosqlite. PostgreSQL support is a design goal ([ADR #2](../reference/design-decisions.md#2-sqlite-by-default-asyncpg-prepared-but-not-implemented)) but not implemented — the DB layer isolates the change.
- **Rate limiting on `/auth/login`**: none — put your proxy in front of it (see [security hardening](../deploy/security.md)).