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
    """Ensure PostgreSQL URLs use the psycopg (v3) dialect and strip PgBouncer params."""
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    if "pgbouncer=true" in url:
        url = url.replace("?pgbouncer=true", "").replace("&pgbouncer=true", "")
        if url.endswith("?"):
            url = url[:-1]
    return url


# SQLite for demo; set DATABASE_URL for PostgreSQL in .env
DATABASE_URL: str = _normalize_db_url(
    _env("DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH}")
)

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
APP_VERSION: str = "0.24.0-d24"
APP_PHASE: str = "D24"
APP_NAME: str = "Federated Model Orchestration Platform"
