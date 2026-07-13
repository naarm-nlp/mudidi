FROM ghcr.io/astral-sh/uv:0.11.28-python3.12-trixie-slim

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/data \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends pdftk-java \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev --extra web

COPY src ./src
RUN uv sync --frozen --no-dev --extra web

RUN mkdir -p /data/app /data/Documents/MUDIDI-runs

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5)"]

CMD ["mudidi", "web", "--container", "--data-dir", "/data/app", "--no-browser"]
