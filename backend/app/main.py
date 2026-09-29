"""
FastAPI application bootstrap — D0 scope.

Exposes:
  GET /health  →  {ok, version, environment, db, storage}
  GET /        →  redirect to /health (convenience)

The health endpoint performs a lightweight DB connectivity check
(SELECT 1) so that environment issues surface immediately.
Later phases add routers for registry, federation, orchestration, etc.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text

from backend.app.config.settings import (
    APP_NAME,
    APP_VERSION,
    DATABASE_URL,
    ENVIRONMENT,
    STORAGE_ROOT,
)
from backend.app.persistence.database import SessionLocal, init_db

# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

api = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description=(
        "Federated Model Orchestration & Decision-Support Platform. "
        "Demo tier — SQLite, no auth, local execution."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
)


# ---------------------------------------------------------------------------
# Startup lifecycle
# ---------------------------------------------------------------------------

@api.on_event("startup")
def on_startup() -> None:
    """
    Initialize the database schema on application startup.
    Safe to call repeatedly — uses CREATE TABLE IF NOT EXISTS semantics.
    """
    init_db()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@api.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    """Convenience redirect so bare / doesn't 404."""
    return RedirectResponse(url="/health")


@api.get("/health", tags=["Platform"])
def health() -> JSONResponse:
    """
    Platform health check.

    Returns:
      ok          — True when all checks pass
      version     — application version string
      environment — deployment environment name
      db          — "ok" | "error: <message>"
      storage     — "ok" | "missing" | "error: <message>"
      timestamp   — UTC ISO-8601
    """
    db_status = _check_db()
    storage_status = _check_storage()

    all_ok = db_status == "ok" and storage_status in ("ok", "missing")
    status_code = 200 if all_ok else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "ok": all_ok,
            "version": APP_VERSION,
            "environment": ENVIRONMENT,
            "db": db_status,
            "storage": storage_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


# ---------------------------------------------------------------------------
# Health sub-checks
# ---------------------------------------------------------------------------

def _check_db() -> str:
    """Return 'ok' or 'error: <detail>' after a lightweight SELECT 1."""
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"
    finally:
        db.close()


def _check_storage() -> str:
    """Return 'ok', 'missing', or 'error: <detail>' for the storage root."""
    try:
        path = Path(STORAGE_ROOT)
        if not path.exists():
            # Create on first access (local FS demo tier)
            path.mkdir(parents=True, exist_ok=True)
        if path.is_dir() and os.access(path, os.W_OK):
            return "ok"
        return "missing"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"
