FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY festin ./festin
RUN uv sync --frozen --no-dev

FROM python:3.13-slim

COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["festin"]