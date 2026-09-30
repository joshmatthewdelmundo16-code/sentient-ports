#!/bin/sh
# Container entrypoint — D24.
#
# Order matters: migrate, then serve. The app no longer creates its own schema against a
# non-SQLite database (AUTO_CREATE_SCHEMA defaults to false there), so Alembic is the only
# thing that may change a shared database's shape.
#
# A failed migration must stop the container rather than let it serve against a schema
# that does not match the code — hence `set -e` and no `|| true`.

set -eu

: "${PORT:=8000}"
: "${WEB_CONCURRENCY:=2}"
: "${RUN_MIGRATIONS:=true}"
# Which proxy addresses may set X-Forwarded-For/-Proto. Trusting '*' lets any client spoof
# its IP (rate limiting and the audit trail rely on it), so the default trusts only a proxy
# on the same host. On a PaaS whose edge proxy is the ONLY way in (Render, Fly, Cloud Run),
# set FORWARDED_ALLOW_IPS='*' deliberately.
: "${FORWARDED_ALLOW_IPS:=127.0.0.1}"

cd /app

if [ -z "${DATABASE_URL:-}" ]; then
  echo "entrypoint: DATABASE_URL is not set." >&2
  echo "entrypoint: refusing to start — a shared deployment must be given its database" >&2
  echo "entrypoint: explicitly rather than silently falling back to a local SQLite file." >&2
  exit 1
fi

# Report the target without ever printing credentials.
echo "entrypoint: database family = $(printf '%s' "$DATABASE_URL" | sed -E 's|^([a-z+]+)://.*|\1|')"
echo "entrypoint: environment     = ${ENVIRONMENT:-unset}"

if [ "$RUN_MIGRATIONS" = "true" ]; then
  echo "entrypoint: applying migrations (alembic upgrade head)…"
  alembic upgrade head
  echo "entrypoint: migrations applied."
else
  echo "entrypoint: RUN_MIGRATIONS=false — skipping alembic."
fi

echo "entrypoint: starting uvicorn on 0.0.0.0:${PORT} with ${WEB_CONCURRENCY} worker(s)."
exec uvicorn backend.app.main:api \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --workers "${WEB_CONCURRENCY}" \
  --proxy-headers \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS}"
