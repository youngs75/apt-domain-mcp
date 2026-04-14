FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src/ src/
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 1000 app \
    && chown -R app:app /app
USER app

ENV PATH="/app/.venv/bin:$PATH" \
    PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os,urllib.request,sys; port=os.environ.get('PORT','8080'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=3).status==200 else 1)"

# Shell form so ${PORT} is expanded at runtime (default 8080, override via env)
CMD uvicorn apt_domain_mcp.server:app --host 0.0.0.0 --port ${PORT:-8080}
