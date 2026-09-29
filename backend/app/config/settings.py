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
# Project root is platform/
_PLATFORM_DIR = Path(__file__).resolve().parents[4]  # backend/app/config/ → platform/
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

# SQLite for demo; swap URL for PostgreSQL at MVP (A-DB assumption §152)
DATABASE_URL: str = _env(
    "DATABASE_URL",
    f"sqlite:///{_DEFAULT_DB_PATH}",
)

# Storage root for local artifacts (MVP: MinIO / S3)
STORAGE_ROOT: str = _env("STORAGE_ROOT", str(_PLATFORM_DIR / "storage"))

# Application version
APP_VERSION: str = "0.1.0-d0"
APP_NAME: str = "Federated Model Orchestration Platform"
