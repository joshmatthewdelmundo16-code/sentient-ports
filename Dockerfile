# Federated Model Orchestration Platform — production image.
#
# Build context is platform/ :
#     docker build -t sentient-platform:d24 platform/
#
# Design notes
#   * One process, one image. No Node toolchain: the UI is server-rendered Jinja2 plus
#     vanilla JS, so there is nothing to bundle.
#   * Schema is owned by Alembic, never by create_all — the entrypoint runs
#     `alembic upgrade head` before the server starts, and AUTO_CREATE_SCHEMA defaults to
#     false for a non-SQLite database.
#   * Demo seeding is off by default here. A shared database must not gain demo rows
#     because someone started a container.
#   * No secrets are baked in. DATABASE_URL and friends arrive from the environment at
#     run time; there is no .env in the image (see .dockerignore).

FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# psycopg[binary] ships its own libpq, so no build toolchain or libpq-dev is needed.
# curl is here only so the container HEALTHCHECK can call /health.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so application edits do not invalidate the layer.
COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
 && python -m pip install -r requirements.txt

COPY . .

# The build context comes from Windows, where the executable bit and LF endings are not
# preserved. Normalise the entrypoint explicitly rather than relying on the host's git
# config, or the container fails with "exec format error" / "no such file or directory".
RUN sed -i 's/\r$//' /app/scripts/entrypoint.sh \
 && chmod +x /app/scripts/entrypoint.sh

# Run as a non-root user that owns the storage volume mount point.
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /data/storage \
 && chown -R appuser:appuser /app /data
USER appuser

# Defaults are the *safe* ones. Everything here is overridable at run time.
ENV ENVIRONMENT=production \
    STORAGE_ROOT=/data/storage \
    AUTO_CREATE_SCHEMA=false \
    DEMO_SEED_ENABLED=false \
    PORT=8000

EXPOSE 8000
VOLUME ["/data/storage"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
