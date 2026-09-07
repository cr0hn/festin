# Installation

FestIn requires **Python 3.13+**. Everything else is managed by [uv](https://docs.astral.sh/uv/) or pip.

## Option 1 — uv (recommended)

[uv](https://docs.astral.sh/uv/) handles the virtualenv, the lockfile and the Python version for you:

```bash
# run without installing
uvx festin --version
uvx festin-serve --help

# or clone and develop
git clone https://github.com/cr0hn/festin.git
cd festin
uv sync                      # creates .venv and installs everything
uv run festin --help
```

## Option 2 — pip

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install .

# two entry points become available:
festin --version       # scanner CLI
festin-serve --help    # monitoring dashboard
```

## Option 3 — Docker

```bash
docker pull ghcr.io/cr0hn/festin:latest

# scanner
docker run --rm ghcr.io/cr0hn/festin:latest scan example.com

# dashboard (volume for SQLite persistence)
docker run -d --name festin \
  -p 8420:8420 \
  -e FESTIN_JWT_SECRET="change-me-please" \
  -v festin-data:/data \
  ghcr.io/cr0hn/festin:latest \
  serve --host 0.0.0.0 --db /data/festin.db
```

See [Docker deployment](../deploy/docker.md) for the full image, including a docker-compose file.

## Verify the install

```bash
festin version
# FestIn Monitor 0.3.0

festin scan example.com --no-print --debug   # smoke test the scanner
curl -s localhost:8420/api/v1/health         # after starting the service
# {"status": "ok", "pending": 0}
```

## System requirements

| Requirement | Minimum | Notes |
|---|---|---|
| Python | 3.13 | 3.14 supported |
| RAM | 256 MB | scanner scales with `--concurrency` |
| Network | outbound HTTPS | Tor supported via `--tor` (SOCKS5 on 127.0.0.1:9050) |
| Disk | < 50 MB | SQLite database grows with scan history |

## Development install

```bash
git clone https://github.com/cr0hn/festin.git
cd festin
uv sync --group dev          # + pytest, ruff, radon, coverage

# full test suite — ALWAYS with the double timeout (some tests hang without it)
timeout 130 uv run pytest --timeout=30 -q

# docs preview
uv run mkdocs serve          # http://localhost:8000
```