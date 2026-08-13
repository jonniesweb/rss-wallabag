FROM ghcr.io/astral-sh/uv:0.11.23 AS uv

FROM python:3.11-slim

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY rss_tracker.py storage.py feed_cli.py ./

# Create directories for data files
RUN mkdir -p /app/data && chmod 755 /app/data

CMD ["python", "rss_tracker.py"]
