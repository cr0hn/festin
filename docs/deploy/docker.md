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
  festin:local

# register the admin (bootstrap)
curl -X POST localhost:8420/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"username": "admin", "password": "a-strong-password"}'
```

## docker compose (recommended)

The included [docker-compose.yml](https://github.com/cr0hn/festin/blob/master/docker-compose.yml) adds restart policy, log rotation, healthcheck and an optional Tor sidecar:

```bash
export FESTIN_JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
docker compose up -d

# anonymized-scanning variant
docker compose --profile tor up -d
```

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

!!! warning "Rate-limit `/api/v1/auth/login` at the proxy"
    The service has no built-in brute-force protection (see [security hardening](security.md)).

## Data lifecycle

- Everything lives in **one SQLite file** (`/data/festin.db` in the container).
- Migrations are idempotent (`CREATE TABLE IF NOT EXISTS` + `ALTER TABLE` guards) — upgrading is: stop, swap image, start.
- Back up with `sqlite3 ... ".backup"` (see above); a raw `cp` while running can catch a mid-write file.