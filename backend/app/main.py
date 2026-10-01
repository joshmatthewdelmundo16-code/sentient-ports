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

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text

from backend.app.buildinfo import build_info, missing_capabilities
from backend.app.config import settings
from backend.app.config.settings import (
    APP_NAME,
    APP_PHASE,
    APP_VERSION,
    AUTO_CREATE_SCHEMA,
    DATABASE_URL,
    DEMO_SEED_ENABLED,
    DOCS_ENABLED,
    ENVIRONMENT,
    PRIMARY_UI,
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
    product,
    results,
    scenarios,
)
from backend.app.api.routers import auth as auth_routes
from backend.app.api.routers import connectors as connector_routes
from backend.app.api.routers import library as library_routes
from backend.app.api.routers import live as live_routes
from backend.app.api.routers import network as network_routes
from backend.app.api.routers import planning as planning_routes
from backend.app.security.auth import AuthConfigError, effective_auth_mode, request_context
from backend.app.security.middleware import SecurityMiddleware
from backend.app.security.tenancy import TenantViolation
from backend.app.ui.router import router as ui_router
from backend.app.web import mount_frontend, readiness

# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

api = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description=(
        "Federated Model Orchestration & Decision-Support Platform. "
        "React product at /app; this API is its only data source."
    ),
    # D25: off in production unless DOCS_ENABLED — the docs pages load third-party scripts
    # and enumerate the whole API surface.
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url="/redoc" if DOCS_ENABLED else None,
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
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

    # D26: refuse to start with an unsafe authentication configuration (e.g. AUTH_MODE=local
    # against a shared database or in production) rather than serve it.
    mode = effective_auth_mode()
    log.info("Authentication mode: %s.", mode)

    if AUTO_CREATE_SCHEMA:
        init_db()
    else:
        log.info(
            "Schema auto-creation disabled (AUTO_CREATE_SCHEMA=false) — "
            "run `alembic upgrade head` to manage this database's schema."
        )

    if DEMO_SEED_ENABLED and settings.DEMO_NETWORK_SEED:
        _seed_network()
    elif DEMO_SEED_ENABLED:
        _seed_demo()
        _seed_decision()
        _seed_governance()
    else:
        from backend.app.ui import decision_state, demo_state
        demo_state.config = {}
        decision_state.config = {}
        log.info("Demo seeding disabled (DEMO_SEED_ENABLED=false) — no demo rows written.")

    from backend.app.config import settings as _s
    if _s.POLL_SCHEDULER_ENABLED:
        from backend.app.connectors import scheduler
        scheduler.start()

    missing = missing_capabilities(api)
    if missing:
        log.warning("Build is missing expected capabilities: %s", ", ".join(missing))
    log.info(
        "Platform startup complete — %s %s (%s), environment=%s.",
        APP_NAME, APP_VERSION, APP_PHASE, ENVIRONMENT,
    )


def _seed_network() -> None:
    """D26: the synthetic multi-port network (SQLite only, non-fatal, idempotent).

    Places the D0–D16 fuel chain in an "Engine sandbox" organization and the D17/D20 port
    demo in each port's own private zone, then points the legacy pages' demo configs at the
    sandbox and at Northbay respectively.
    """
    import logging
    log = logging.getLogger(__name__)
    from backend.app.persistence.database import engine
    from backend.app.ui import decision_state, demo_state

    from backend.app.ui.network_seed import seed_network_demo

    db = SessionLocal()
    try:
        result = seed_network_demo(db)
        db.commit()
        demo_state.config = result.get("sandbox", {})
        decision_state.config = result.get("decision", {})
        log.info("Network demo ensured — %d organizations.", len(result.get("organizations", {})))
    except Exception as exc:
        db.rollback()
        log.warning("Network demo seed failed (non-fatal): %s", exc, exc_info=True)
    finally:
        db.close()


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

# D26: every data router is behind request_context — authentication, organization scope,
# role, CSRF and write limits are enforced there, and the database session is scoped to one
# organization before any handler runs. Only sign-in, the session probe, health/readiness,
# build identity and static page shells are reachable without it.
_SCOPED = [Depends(request_context)]
for _r in (models.router, contracts.router, datasets.router, ingestions.router, graph.router,
           executions.router, changes.router, results.router, lineage.router, scenarios.router,
           governance.router, product.router,
           # D27
           network_routes.router, library_routes.router, connector_routes.router,
           planning_routes.router, live_routes.router):
    api.include_router(_r, dependencies=_SCOPED)
api.include_router(auth_routes.router)
# Signed webhooks authenticate by HMAC signature, not by session (connectors.public).
api.include_router(connector_routes.public)
api.include_router(ui_router)
mount_frontend(api)
api.add_middleware(SecurityMiddleware)

if settings.CORS_ALLOWED_ORIGINS:
    from fastapi.middleware.cors import CORSMiddleware

    if any("*" in o for o in settings.CORS_ALLOWED_ORIGINS):
        raise AuthConfigError("CORS_ALLOWED_ORIGINS must list exact origins; wildcards are refused.")
    api.add_middleware(
        CORSMiddleware, allow_origins=settings.CORS_ALLOWED_ORIGINS, allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Scope-Org", "X-Request-ID"],
    )


@api.exception_handler(TenantViolation)
def _tenant_violation(_request: Request, exc: TenantViolation) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": "That record belongs to another organization."})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@api.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def root_redirect() -> RedirectResponse:
    """Bare / opens the product, not the health JSON.

    D25: the React product (/app) is the primary experience. PRIMARY_UI=legacy is the
    rollback switch back to the D24 Start Here page; the legacy pages stay served at /ui
    either way.
    """
    from backend.app.web import frontend_info

    # An unbuilt React bundle falls back to the legacy product rather than a 503 page.
    legacy = PRIMARY_UI == "legacy" or not frontend_info()["built"]
    return RedirectResponse(url="/ui/start" if legacy else "/app/")


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


@api.get("/api/capabilities", tags=["Platform"], dependencies=[Depends(request_context)],
         summary="What this platform implements, honestly classified, with evidence")
def get_capabilities() -> dict:
    from backend.app.capabilities import registry

    return registry(api)


@api.api_route("/ready", methods=["GET", "HEAD"], tags=["Platform"],
               summary="Readiness: database reachable and schema at this build's migration head")
def ready() -> JSONResponse:
    """503 until the database answers AND its Alembic revision matches this build.

    Liveness is /health. Use this one for load-balancer readiness and deploy gates: a
    container whose migrations failed, or that is pointed at an unmigrated database, reports
    not-ready instead of serving requests against a schema it does not understand.
    """
    from backend.app.persistence.database import engine

    ok, report = readiness(engine)
    return JSONResponse(status_code=200 if ok else 503, content={"ready": ok, **report})


@api.api_route("/health", methods=["GET", "HEAD"], tags=["Platform"])
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
