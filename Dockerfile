# Dockerfile for FestIn — dashboard + scanner in one image
# build:  docker build -t festin:local .
# run:    docker run -p 8420:8420 -v festin-data:/data festin:local serve --host 0.0.0.0 --db /data/festin.db
# scan:   docker run --rm festin:local scan example.com

FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY festin ./festin
COPY README.md LICENSE ./
RUN uv sync --frozen --no-dev

FROM python:3.13-slim

# Tor included so `--tor` works out of the box
RUN apt-get update \
    && apt-get install -y --no-install-recommends tor ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --home-dir /data festin

COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /data
USER festin
EXPOSE 8420

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8420/api/v1/health', timeout=3)" || exit 1

# default: dashboard. Override the command for CLI usage.
ENTRYPOINT ["festin"]
CMD ["serve", "--host", "0.0.0.0", "--db", "/data/festin.db"]