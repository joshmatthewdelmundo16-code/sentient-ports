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

from backend.app.buildinfo import build_info, missing_capabilities
from backend.app.config.settings import (
    APP_NAME,
    APP_PHASE,
    APP_VERSION,
    AUTO_CREATE_SCHEMA,
    DATABASE_URL,
    DEMO_SEED_ENABLED,
    ENVIRONMENT,
    STORAGE_ROOT,
)
from backend.app.persistence.database import SessionLocal, init_db
from backend.app.api.routers import (
    changes,
    contracts,
    datasets,
    executions,
    governance,
    ingestions,
    graph,
    lineage,
    models,
    results,
    scenarios,
)
from backend.app.ui.router import router as ui_router

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
    """Initialize DB schema and seed the demo chain.

    Both steps are gated (D24). Against a shared/managed database, AUTO_CREATE_SCHEMA and
    DEMO_SEED_ENABLED both default to false, so starting the app neither creates tables nor
    writes demo rows there — Alembic owns a shared schema, and demo data stays local.
    """
    import logging
    log = logging.getLogger(__name__)

    if AUTO_CREATE_SCHEMA:
        init_db()
    else:
        log.info(
            "Schema auto-creation disabled (AUTO_CREATE_SCHEMA=false) — "
            "run `alembic upgrade head` to manage this database's schema."
        )

    if DEMO_SEED_ENABLED:
        _seed_demo()
        _seed_decision()
        _seed_governance()
    else:
        from backend.app.ui import decision_state, demo_state
        demo_state.config = {}
        decision_state.config = {}
        log.info("Demo seeding disabled (DEMO_SEED_ENABLED=false) — no demo rows written.")

    missing = missing_capabilities(api)
    if missing:
        log.warning("Build is missing expected capabilities: %s", ", ".join(missing))
    log.info(
        "Platform startup complete — %s %s (%s), environment=%s.",
        APP_NAME, APP_VERSION, APP_PHASE, ENVIRONMENT,
    )


def _seed_demo() -> None:
    """Ensure the demo federation exists (persisted adapters, bindings, contracts). Non-fatal."""
    import logging
    log = logging.getLogger(__name__)
    from backend.app.ui import demo_state
    from backend.app.ui.demo_seed import seed_demo_data

    db = SessionLocal()
    try:
        config = seed_demo_data(db)
        db.commit()
        demo_state.config = config
        log.info("Demo federation ensured — %d models.", len(config.get("models", [])))
    except Exception as exc:
        db.rollback()
        log.warning("Demo seed failed (non-fatal): %s", exc)
    finally:
        db.close()


def _seed_decision() -> None:
    """Ensure the D20 decision demo (port domain + baseline + scenario) exists.

    SQLite-only and non-fatal: refuses to run against a shared PostgreSQL/Supabase database,
    so starting the app never creates baseline/scenario runs or modifies shared data there.
    The dialect is checked before any connection is opened, so a shared database is not even
    contacted by this step.
    """
    import logging
    log = logging.getLogger(__name__)
    from backend.app.persistence.database import engine
    from backend.app.ui import decision_state

    if engine.dialect.name != "sqlite":
        decision_state.config = {}
        log.info("Decision demo skipped (non-SQLite database).")
        return

    from backend.app.ui.port_decision_seed import seed_port_decision

    db = SessionLocal()
    try:
        config = seed_port_decision(db)
        db.commit()
        decision_state.config = config
        if config:
            log.info("Decision demo ensured — baseline %s / scenario %s.",
                     config.get("baseline_name"), config.get("scenario_name"))
        else:
            log.info("Decision demo skipped (non-SQLite or D19 tables absent).")
    except Exception as exc:
        db.rollback()
        log.warning("Decision seed failed (non-fatal): %s", exc)
    finally:
        db.close()


def _seed_governance() -> None:
    """Ensure the D22 governance demo exists so /ui/governance is not blank.

    Same SQLite-only guard as the decision seed: a shared PostgreSQL never gains
    participants or data-sharing agreements from a startup. Every seeded record is
    explicitly marked synthetic and the UI labels it as such.
    """
    import logging
    log = logging.getLogger(__name__)
    from backend.app.persistence.database import engine

    if engine.dialect.name != "sqlite":
        log.info("Governance demo skipped (non-SQLite database).")
        return

    from backend.app.ui.governance_seed import seed_governance_demo

    db = SessionLocal()
    try:
        result = seed_governance_demo(db)
        db.commit()
        if result:
            log.info(
                "Governance demo ensured — %d participants, %d approvals.",
                result.get("participants", 0), result.get("approvals", 0),
            )
    except Exception as exc:
        db.rollback()
        log.warning("Governance seed failed (non-fatal): %s", exc)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API routers — D11
# ---------------------------------------------------------------------------

api.include_router(models.router)
api.include_router(contracts.router)
api.include_router(datasets.router)
api.include_router(ingestions.router)
api.include_router(graph.router)
api.include_router(executions.router)
api.include_router(changes.router)
api.include_router(results.router)
api.include_router(lineage.router)
api.include_router(scenarios.router)
api.include_router(governance.router)
api.include_router(ui_router)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@api.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    """Bare / opens the product, not the health JSON.

    Before D24 this redirected to /health, so the first thing a non-developer saw when
    they opened the server was a JSON blob. It now lands on the Start Here guide.
    """
    return RedirectResponse(url="/ui/start")


@api.get("/api/build-info", tags=["Platform"],
         summary="Which build is answering, and what it can actually do")
def get_build_info() -> dict:
    """Self-report of the running process.

    Capabilities are derived from this process's real route table, so the answer cannot
    claim a feature the running build does not serve. The browser UI calls this on load
    and warns when it has reached a server older than the page expects — the failure mode
    that produced the D24 `/ui/governance` 404.
    """
    return build_info(api)


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
    missing = missing_capabilities(api)

    all_ok = db_status == "ok" and storage_status in ("ok", "missing")
    status_code = 200 if all_ok else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "ok": all_ok,
            "version": APP_VERSION,
            "phase": APP_PHASE,
            "environment": ENVIRONMENT,
            "db": db_status,
            "storage": storage_status,
            # Present so a load balancer / operator can tell two builds apart without
            # opening the UI. Empty list on a complete build.
            "missing_capabilities": missing,
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
    except Exception:  # noqa: BLE001
        return "error: connection failed"
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
