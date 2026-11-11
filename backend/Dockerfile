# syntax=docker/dockerfile:1
# Clinical AI Platform backend (synthetic-data demo — not for clinical use).
#
# Single-origin image: FastAPI serves the API (/api/v1), health (/health/*),
# and the static frontend (/). No .env file or secret is baked in — all
# configuration comes from the runtime environment (docker-compose.yml
# `env_file` / the host dashboard). Reproducible installs: uv.lock only.

# --- Builder -----------------------------------------------------------------
FROM python:3.12-slim AS builder

# uv drives every install from the committed lockfile.
COPY --from=ghcr.io/astral-sh/uv:0.11.4 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app/backend

# Dependency layer (cached until the lockfile changes).
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Application layer: install the project itself from the same lockfile.
COPY backend/ ./
RUN uv sync --frozen --no-dev

# --- Runtime -----------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/backend/.venv/bin:$PATH" \
    PYTHONPATH=/app/backend

# FFmpeg/FFprobe are required only for the bounded voice transcription route;
# they run as subprocesses with fixed arguments and timeouts (app/services/voice.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Non-root process user (no login shell, no sudo).
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home app

WORKDIR /app/backend

# Runtime virtualenv only: no dev/reload tooling, no uv cache in the image.
COPY --from=builder --chown=app:app /app/backend/.venv /app/backend/.venv

# Application code: alembic config, migrations, and the app package.
COPY --chown=app:app backend/alembic.ini backend/pyproject.toml ./
COPY --chown=app:app backend/app ./app
COPY --chown=app:app backend/migrations ./migrations

# Static frontend served at / (single-origin mode; production hosts it on
# Cloudflare Pages and proxies /api/* here).
COPY --chown=app:app frontend /app/frontend

# Entrypoint (migrations on start) and operational scripts (seed, healthcheck).
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY --chown=app:app scripts/seed_demo_data.py scripts/healthcheck.py /app/scripts/
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

USER app
EXPOSE 8000

# Migrations run on start (documented demo-scale trade-off); override the
# command for one-shot jobs (e.g. `docker compose run --rm app python
# /app/scripts/seed_demo_data.py`).
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\""]
