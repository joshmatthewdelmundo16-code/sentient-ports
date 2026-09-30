"""
Application settings loaded from environment variables / .env file.

D0 scope: minimal config needed for bootstrap, DB URL, and environment name.
Later phases add auth secrets, MinIO config, log level, etc.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------
# NOTE: parents[4] resolves to the *repository root* (the parent of platform/),
# not to platform/ — backend/app/config/settings.py → config → app → backend →
# platform → repo root. The default SQLite file, the .env that is read, and the
# default STORAGE_ROOT therefore all live at the repository root. This is the
# long-standing behaviour of every phase from D0 onward and is preserved
# deliberately; the comment is corrected rather than the path, because changing
# it would silently relocate an existing developer database.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_PLATFORM_DIR = _REPO_ROOT  # historical name, kept for compatibility
_DEFAULT_DB_PATH = _PLATFORM_DIR / "federated_platform.db"


def _env(key: str, default: str) -> str:
    """Read from env, then .env file (simple, no external dep for D0)."""
    value = os.environ.get(key)
    if value is not None:
        return value

    # Try loading from .env in platform/ (best-effort, no python-dotenv dep)
    dotenv_path = _PLATFORM_DIR / ".env"
    if dotenv_path.exists():
        for line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip('"').strip("'")

    return default


# ---------------------------------------------------------------------------
# Exported settings
# ---------------------------------------------------------------------------
ENVIRONMENT: str = _env("ENVIRONMENT", "development")


def _normalize_db_url(url: str) -> str:
    """Ensure PostgreSQL URLs use the psycopg (v3) dialect and strip PgBouncer params.

    D25: a Supabase host without an explicit sslmode gets sslmode=require. Supabase accepts
    TLS on both the direct host and the Supavisor pooler; without this, libpq's default
    ("prefer") would silently fall back to plaintext if TLS negotiation failed.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    if "pgbouncer=true" in url:
        url = url.replace("?pgbouncer=true", "").replace("&pgbouncer=true", "")
        if url.endswith("?"):
            url = url[:-1]
    if url.startswith("postgresql") and _is_supabase_host(url) and "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def _db_host_port(url: str) -> tuple[str, int | None]:
    """Host and port of a database URL — never the credentials."""
    rest = url.split("://", 1)[-1]
    hostpart = rest.rsplit("@", 1)[-1].split("/", 1)[0].split("?", 1)[0]
    host, _, port = hostpart.partition(":")
    return host.lower(), int(port) if port.isdigit() else None


def _is_supabase_host(url: str) -> bool:
    host, _ = _db_host_port(url)
    return host.endswith(".supabase.com") or host.endswith(".supabase.co")


# SQLite for demo; set DATABASE_URL for PostgreSQL in .env
DATABASE_URL: str = _normalize_db_url(
    _env("DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH}")
)

# D25: Alembic may use a different connection than the app. Supabase's transaction pooler
# (port 6543) is right for the app but DDL is safer over the session pooler (5432) or the
# direct host. Empty → migrations use DATABASE_URL.
MIGRATION_DATABASE_URL: str = _normalize_db_url(_env("MIGRATION_DATABASE_URL", "")) \
    if _env("MIGRATION_DATABASE_URL", "") else ""

IS_SQLITE: bool = DATABASE_URL.startswith("sqlite")
DB_IS_SUPABASE: bool = _is_supabase_host(DATABASE_URL)
# Supavisor transaction mode listens on 6543. Prepared statements must stay disabled there
# (they are disabled for every PostgreSQL connection anyway — see database._build_engine).
DB_TRANSACTION_POOLER: bool = (not IS_SQLITE) and _db_host_port(DATABASE_URL)[1] == 6543

# Connection pool (PostgreSQL only). Small by default: every app worker holds its own pool,
# and a Supabase project caps pooler client connections per plan. "null" disables client-side
# pooling entirely (one connection per session), which suits very small or serverless hosts.
DB_POOL_MODE: str = _env("DB_POOL_MODE", "queue").strip().lower()
DB_POOL_SIZE: int = int(_env("DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW: int = int(_env("DB_MAX_OVERFLOW", "5"))
DB_POOL_TIMEOUT_S: float = float(_env("DB_POOL_TIMEOUT_S", "10"))
# Recycle before typical idle-connection reapers (Supavisor, cloud NAT) drop the socket.
DB_POOL_RECYCLE_S: int = int(_env("DB_POOL_RECYCLE_S", "300"))
DB_CONNECT_TIMEOUT_S: int = int(_env("DB_CONNECT_TIMEOUT_S", "10"))
DB_STATEMENT_TIMEOUT_MS: int = int(_env("DB_STATEMENT_TIMEOUT_MS", "30000"))

# Storage root for local artifacts (MVP: MinIO / S3)
STORAGE_ROOT: str = _env("STORAGE_ROOT", str(_PLATFORM_DIR / "storage"))

# ---------------------------------------------------------------------------
# Airflow optional executor (D21)
# ---------------------------------------------------------------------------
# Airflow is an OPTIONAL external executor. The default in-process executor never
# reads any of these. Nothing here is required for ordinary development or tests.
# Credentials are read from the environment only; never hard-code or log them.

def _env_bool(key: str, default: bool) -> bool:
    return _env(key, "true" if default else "false").strip().lower() in ("1", "true", "yes", "on")


# Feature flag. When false, an unspecified executor resolves to in_process and an
# explicit "airflow" request raises a clear configuration/availability error.
AIRFLOW_ENABLED: bool = _env_bool("AIRFLOW_ENABLED", False)
# Base URL of the Airflow webserver REST API host, e.g. "http://localhost:8080".
AIRFLOW_BASE_URL: str = _env("AIRFLOW_BASE_URL", "")
# The single stable DAG that triggers a platform-owned GraphRun execution.
AIRFLOW_DAG_ID: str = _env("AIRFLOW_DAG_ID", "platform_graphrun")
# Auth: a bearer token (preferred) OR basic username/password. Never logged.
AIRFLOW_AUTH_TOKEN: str = _env("AIRFLOW_AUTH_TOKEN", "")
AIRFLOW_USERNAME: str = _env("AIRFLOW_USERNAME", "")
AIRFLOW_PASSWORD: str = _env("AIRFLOW_PASSWORD", "")
# HTTP behaviour.
AIRFLOW_POLL_TIMEOUT_S: float = float(_env("AIRFLOW_POLL_TIMEOUT_S", "10"))
AIRFLOW_VERIFY_TLS: bool = _env_bool("AIRFLOW_VERIFY_TLS", True)

# ---------------------------------------------------------------------------
# Startup safety (D24)
# ---------------------------------------------------------------------------
# Two things used to happen on *every* startup against *any* database:
#   1. init_db() ran Base.metadata.create_all()
#   2. the D0–D16 demo federation was seeded
# Against a shared/managed PostgreSQL that silently creates tables and writes demo
# rows into shared data. Both are now gated. The defaults below preserve the local
# SQLite developer experience exactly (both on) while making a shared database
# read-only at startup unless the operator opts in explicitly.

_IS_SQLITE_DEFAULT = DATABASE_URL.startswith("sqlite")

# Create missing tables at startup. SQLite: on (zero-install demo). Otherwise: off —
# Alembic migrations own the schema of a shared/production database.
AUTO_CREATE_SCHEMA: bool = _env_bool("AUTO_CREATE_SCHEMA", _IS_SQLITE_DEFAULT)

# Seed the demo federation at startup. SQLite: on. Otherwise: off — a shared database
# must never gain demo rows just because someone started the app.
DEMO_SEED_ENABLED: bool = _env_bool("DEMO_SEED_ENABLED", _IS_SQLITE_DEFAULT)

# Optional: the externally reachable base URL, shown in the UI and /api/build-info.
# Never invented — empty until an operator actually deploys and sets it.
PUBLIC_BASE_URL: str = _env("PUBLIC_BASE_URL", "")

# Application version — bumped per phase so a running server can be identified.
APP_VERSION: str = "0.27.0-d27"
APP_PHASE: str = "D25-D27"
APP_NAME: str = "Federated Model Orchestration Platform"

IS_PRODUCTION: bool = ENVIRONMENT.strip().lower() == "production"

# ---------------------------------------------------------------------------
# Frontend (D25)
# ---------------------------------------------------------------------------
# The React product is served by FastAPI from its built bundle at /app — same origin as the
# API, so no CORS is needed and the session cookie never has to cross sites.
FRONTEND_DIST: str = _env(
    "FRONTEND_DIST", str(Path(__file__).resolve().parents[3] / "frontend" / "dist")
)
# Which experience "/" opens. "react" (default) or "legacy" — the rollback switch to the
# D12–D24 Jinja pages, which stay served at /ui regardless.
PRIMARY_UI: str = _env("PRIMARY_UI", "react").strip().lower()
# Interactive API docs (/docs, /redoc). Off in production: they load third-party scripts
# and advertise the full API surface. Opt in explicitly if you need them there.
DOCS_ENABLED: bool = _env_bool("DOCS_ENABLED", not IS_PRODUCTION)

# ---------------------------------------------------------------------------
# Authentication, sessions and request security (D26)
# ---------------------------------------------------------------------------
# AUTH_MODE:
#   "required" — every API call needs a signed-in user (server-side session cookie).
#   "local"    — single-developer mode: requests act as a local principal that may enter
#                every organization. Allowed ONLY with SQLite and outside production;
#                see security.auth.effective_auth_mode(), which enforces that at startup.
# Default: "required", except the automated test suite (ENVIRONMENT=test), which opts in to
# "local" so pre-D26 tests keep exercising the engine without logging in.
AUTH_MODE: str = _env(
    "AUTH_MODE", "local" if ENVIRONMENT.strip().lower() == "test" else "required"
).strip().lower()

SESSION_COOKIE_NAME: str = _env("SESSION_COOKIE_NAME", "sp_session")
CSRF_COOKIE_NAME: str = _env("CSRF_COOKIE_NAME", "sp_csrf")
# Secure cookies require HTTPS. Browsers treat http://localhost as secure, but the test
# client does not, so the default is on only in production.
SESSION_COOKIE_SECURE: bool = _env_bool("SESSION_COOKIE_SECURE", IS_PRODUCTION)
SESSION_ABSOLUTE_HOURS: float = float(_env("SESSION_ABSOLUTE_HOURS", "12"))
SESSION_IDLE_MINUTES: float = float(_env("SESSION_IDLE_MINUTES", "120"))

# Password hashing (stdlib scrypt). N=2^14, r=8, p=5 is one of OWASP's equivalent scrypt
# profiles and needs only 16 MiB per hash, so concurrent logins cannot exhaust a small
# container's memory. Stored hashes carry their parameters, so these can be raised later.
AUTH_SCRYPT_N: int = int(_env("AUTH_SCRYPT_N", str(2 ** 14)))
AUTH_SCRYPT_R: int = int(_env("AUTH_SCRYPT_R", "8"))
AUTH_SCRYPT_P: int = int(_env("AUTH_SCRYPT_P", "5"))
AUTH_MIN_PASSWORD_LENGTH: int = int(_env("AUTH_MIN_PASSWORD_LENGTH", "12"))

# Login throttling (per client IP and per account, sliding window). In-process: correct for a
# single instance; with several instances each keeps its own window (documented limitation).
LOGIN_MAX_ATTEMPTS: int = int(_env("LOGIN_MAX_ATTEMPTS", "10"))
LOGIN_WINDOW_S: int = int(_env("LOGIN_WINDOW_S", "900"))
# Unsafe-method (POST/PUT/PATCH/DELETE) budget per session or client per minute.
WRITE_RATE_PER_MINUTE: int = int(_env("WRITE_RATE_PER_MINUTE", "240"))
# Hard cap on any request body. The Excel/CSV connectors enforce tighter limits of their own.
MAX_REQUEST_BYTES: int = int(_env("MAX_REQUEST_BYTES", str(8 * 1024 * 1024)))

# Cross-origin access. Empty (default) = same-origin only: the React bundle is served by this
# process, so no CORS headers are emitted at all. List exact origins to allow a separately
# hosted frontend, e.g. "https://app.example.com". Wildcards are rejected.
CORS_ALLOWED_ORIGINS: list[str] = [
    o.strip().rstrip("/") for o in _env("CORS_ALLOWED_ORIGINS", "").split(",")
    if o.strip()
]
# HSTS only makes sense once the service is really behind HTTPS.
HSTS_ENABLED: bool = _env_bool("HSTS_ENABLED", IS_PRODUCTION)

# Service-to-service token for the optional Airflow callback (D21). Empty → callbacks need a
# normal signed-in user like any other call.
SERVICE_TOKEN: str = _env("PLATFORM_SERVICE_TOKEN", "")

# ---------------------------------------------------------------------------
# Connectors and live data (D27)
# ---------------------------------------------------------------------------
# Master key for webhook signatures. Per-source signing keys are derived from it (HMAC), so
# no per-source secret is stored in the database. Webhooks are refused while it is empty.
CONNECTOR_SIGNING_KEY: str = _env("CONNECTOR_SIGNING_KEY", "")
# REST polling may only reach these hosts (exact names, comma-separated). Empty → REST
# connectors can be defined but never fetch anything. Private/loopback addresses are
# rejected even when listed, unless CONNECTOR_ALLOW_PRIVATE_HOSTS is on (local testing).
CONNECTOR_ALLOWED_HOSTS: list[str] = [
    h.strip().lower() for h in _env("CONNECTOR_ALLOWED_HOSTS", "").split(",") if h.strip()
]
CONNECTOR_ALLOW_PRIVATE_HOSTS: bool = _env_bool("CONNECTOR_ALLOW_PRIVATE_HOSTS", False)
# In-process poll scheduler for REST sources. Off by default: with several app instances
# each would poll; run it on exactly one instance (or leave polling to an external cron).
POLL_SCHEDULER_ENABLED: bool = _env_bool("POLL_SCHEDULER_ENABLED", False)
# How often the live-refresh stream checks the database for new activity.
LIVE_STREAM_INTERVAL_S: float = float(_env("LIVE_STREAM_INTERVAL_S", "2"))
