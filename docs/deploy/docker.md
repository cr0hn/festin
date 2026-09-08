# Production deployment with Docker

## The image

One image serves both missions:

- `festin serve ...` (default CMD) — the dashboard
- `festin scan ...` — one-shot CLI scans

```bash
docker build -t festin:local .
```

Design choices:

- `python:3.13-slim` multi-stage; venv built with `uv` for lockfile fidelity.
- Tor included so `--tor` works out of the box.
- Non-root user `festin`, data in `/data` (volume).
- `HEALTHCHECK` wired to `/api/v1/health`.

## Quick start

```bash
export FESTIN_JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
docker run -d --name festin \
  -p 8420:8420 \
  -e FESTIN_JWT_SECRET=$FESTIN_JWT_SECRET \
  -v festin-data:/data \
  cr0hn/festin:latest

# register the admin (bootstrap)
curl -X POST localhost:8420/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"username": "admin", "password": "a-strong-password"}'
```

Prebuilt images: [`cr0hn/festin`](https://hub.docker.com/r/cr0hn/festin) —
multi-arch (amd64 + arm64), published automatically by CI on every push to
`master` and on `v*` tags.

## docker compose (recommended)

The included [docker-compose.yml](https://github.com/cr0hn/festin/blob/master/docker-compose.yml) adds restart policy, log rotation, healthcheck and an optional Tor sidecar:

```bash
export FESTIN_JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
docker compose up -d

# anonymized-scanning variant
docker compose --profile tor up -d
```

## Production configuration

| Variable | Default | Purpose |
|---|---|---|
| `FESTIN_JWT_SECRET` | dev fallback | JWT signing — **always set in production** |
| `FESTIN_DB_DSN` | SQLite path | `postgres://user:pass@host:5432/festin` → PostgreSQL backend |
| `FESTIN_QUEUE` | `memory` | `streaq` = durable Redis Streams queue |
| `FESTIN_REDIS_URL` | `redis://localhost:6379/0` | Redis endpoint (streaq mode) |
| `FESTIN_RATE_LIMIT_ATTEMPTS` / `_WINDOW` | 5 / 60 | login + register throttle |

Full reference: [configuration](../usage/configuration.md).

### Production topology (SQLite)

```yaml
services:
  festin:
    image: cr0hn/festin:latest
    environment:
      FESTIN_JWT_SECRET: ${FESTIN_JWT_SECRET}
    volumes: [festin-data:/data]
    ports: ["8420:8420"]
```

### Production topology (PostgreSQL + queue workers)

```yaml
services:
  festin-api:
    image: cr0hn/festin:latest
    environment:
      FESTIN_JWT_SECRET: ${FESTIN_JWT_SECRET}
      FESTIN_DB_DSN: ${FESTIN_DB_DSN}          # postgres://...
      FESTIN_QUEUE: streaq
      FESTIN_REDIS_URL: redis://redis:6379/0
    ports: ["8420:8420"]
    deploy: {replicas: 2}

  festin-worker:
    image: cr0hn/festin:latest
    command: festin-worker
    environment:
      FESTIN_DB_DSN: ${FESTIN_DB_DSN}
      FESTIN_REDIS_URL: redis://redis:6379/0
    deploy: {replicas: 2}                      # scan throughput knob

  redis:
    image: redis:7-alpine
```

The API only **enqueues**; workers **consume**. Restart API pods freely —
queued scans survive in Redis. Scale `festin-worker` replicas for scan
throughput. Full HA patterns: [high availability](ha.md).

## Operation

```bash
# logs
docker compose logs -f festin

# one-shot CLI scan using the same image
docker compose run --rm festin scan example.com --quiet --export sarif --output /data/scan.sarif
docker compose exec festin cat /data/scan.sarif

# backup the SQLite database (use the .backup command — safe while running)
docker compose exec festin python -c \
  "import sqlite3; sqlite3.connect('/data/festin.db').backup('/data/backup.db')"

# upgrade
docker compose pull && docker compose up -d
```

## Reverse proxy (TLS)

FestIn serves plain HTTP — terminate TLS in front:

=== "Caddy"

    ```caddyfile
    festin.example.com {
        reverse_proxy 127.0.0.1:8420
    }
    ```

=== "nginx"

    ```nginx
    server {
        listen 443 ssl http2;
        server_name festin.example.com;
        # ssl_certificate ...;

        location / {
            proxy_pass http://127.0.0.1:8420;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }
    }
    ```

!!! tip "Rate limiting"
    Login and register are rate-limited **in-app** (5 attempts / 60 s per IP
    by default — see [configuration](../usage/configuration.md)). Adding a
    proxy-level limit as well is recommended as a second layer.

## Data lifecycle

- Everything lives in **one SQLite file** (`/data/festin.db` in the container).
- Migrations are idempotent (`CREATE TABLE IF NOT EXISTS` + `ALTER TABLE` guards) — upgrading is: stop, swap image, start.
- Back up with `sqlite3 ... ".backup"` (see above); a raw `cp` while running can catch a mid-write file.