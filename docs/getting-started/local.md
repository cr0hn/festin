# Running locally

Every way to run FestIn on your machine, from a one-off scan to a persistent dashboard.

## The scanner CLI

=== "Single domain"

    ```bash
    uv run festin scan example.com
    ```

=== "Multiple domains"

    ```bash
    uv run festin scan example.com example.org example.net
    ```

=== "From a file"

    ```bash
    # domains.txt: one domain per line
    uv run festin scan -f domains.txt
    ```

=== "Watch mode"

    ```bash
    # keep running; new lines appended to domains.txt are scanned automatically
    uv run festin scan -f domains.txt --watch
    ```

### Useful scanner combinations

```bash
# Aggressive: permutations + multi-cloud probing, deep crawl
uv run festin scan example.com --permute --cloud --http-max-recursion 5 --concurrency 10

# Stealth: slow profile through Tor, no banner noise
uv run festin scan example.com --tor --profile stealth --quiet

# Long scan with resume support (Ctrl-C safe)
uv run festin scan -f big-list.txt --checkpoint scan.ckpt
uv run festin scan --resume --checkpoint scan.ckpt

# CI mode: no output, SARIF report, strict timeout
uv run festin scan example.com --quiet --no-print --export sarif --output out.sarif
```

| Flag group | Flags | Purpose |
|---|---|---|
| Speed | `--concurrency`, `--profile fast\|deep\|stealth` | probe rate control |
| Discovery | `--permute`, `--wordlist`, `--cloud`, `--no-dnsdiscover`, `--dns-resolver` | how bucket names are derived |
| Crawl | `--no-links`, `--http-max-recursion`, `--http-timeout`, `--domain-regex`, `--domain-black-list`, `--domain-white-list` | link-following policy |
| Output | `--result-file`, `--export csv\|sarif\|jsonl --output` | persistence |
| Resilience | `--checkpoint`, `--resume`, `--watch` | long-running operations |
| Anonymity | `--tor` | route all probes through local SOCKS5 proxy |

## The dashboard service

=== "Module form (recommended)"

    ```bash
    uv run python -m festin.service.serve --port 8420
    ```

    Flags: `--host`, `--port`, `--db <path>`, `--auth-file <path>` (legacy basic-auth users file).

=== "Typer CLI"

    ```bash
    uv run festin serve --port 8420 --db festin.db
    ```

    Flags: `--host` (default `127.0.0.1`), `--port` (default `8420`), `--db`.

=== "Python API"

    ```python
    import asyncio
    from pathlib import Path
    from festin.service.serve import ServiceConfig, run_server

    config = ServiceConfig(
        host="127.0.0.1",
        port=8420,
        db_path=Path("data/festin.db"),
    )
    asyncio.run(run_server(config))
    ```

### Environment

| Variable | Default | Purpose |
|---|---|---|
| `FESTIN_JWT_SECRET` | dev fallback | **Set this in anything shared.** Secret for JWT signing (HS256, 60 min expiry). |

### First-run bootstrap

With an empty database the first `POST /api/v1/auth/register` creates the
**admin**. After that, only an admin token can create more users (they get
the `viewer` role; admins can promote them from the UI).

```bash
curl -X POST localhost:8420/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"username": "admin", "password": "a-strong-password"}'
```

!!! note "No demo database is shipped"
    The repo does not contain a pre-built database. On first start the
    service creates an empty one — bootstrap the admin yourself with a
    strong password before exposing the service.

### Reset everything

```bash
rm data/festin.db          # wipes users, projects, scans, findings
# restart the service and re-bootstrap
```

## Running both against the same targets

The CLI writes state files; the dashboard owns its database. They don't share state. Typical workflow:

1. Recon in bulk with the CLI: `festin scan -f domains.txt --export jsonl -o recon.jsonl`.
2. Load the interesting domains into a project in the dashboard.
3. Let the scheduler watch them continuously.