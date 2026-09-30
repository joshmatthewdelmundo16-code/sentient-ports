# Federated Model Orchestration Platform — production image.
#
# Build context is platform/ :
#     docker build -t sentient-platform:d27 platform/
#
# Design notes
#   * Two stages. Stage "web" builds the React product with Node; the runtime stage is
#     Python only and receives the static bundle. No Node runtime ships in the final image.
#   * One process serves both the API and the React bundle (/app), so the browser talks to a
#     single origin: no CORS, and the session cookie never crosses sites.
#   * Schema is owned by Alembic, never by create_all — the entrypoint runs
#     `alembic upgrade head` before the server starts, and AUTO_CREATE_SCHEMA defaults to
#     false for a non-SQLite database.
#   * Demo seeding is off by default here. A shared database must not gain demo rows
#     because someone started a container.
#   * No secrets are baked in. DATABASE_URL and friends arrive from the environment at
#     run time; there is no .env in the image (see .dockerignore).

# ── Stage 1: build the React product ────────────────────────────────────────
FROM node:24-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# psycopg[binary] ships its own libpq, so no build toolchain or libpq-dev is needed.
# curl is here only so the container HEALTHCHECK can call /ready.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so application edits do not invalidate the layer.
COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
 && python -m pip install -r requirements.txt

COPY . .
# The React bundle built above. Anything under frontend/ other than dist is not needed at
# run time and is excluded by .dockerignore.
COPY --from=web /web/dist /app/frontend/dist

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
    AUTH_MODE=required \
    FRONTEND_DIST=/app/frontend/dist \
    PORT=8000

EXPOSE 8000
VOLUME ["/data/storage"]

# /ready fails until the database answers AND its schema is at this build's migration head.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/ready" || exit 1

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
