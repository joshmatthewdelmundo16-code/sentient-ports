"""Build identity and capability self-report — D24.

Why this exists
---------------
The D24 defect report was `/ui/governance` returning `{"detail":"Not Found"}` in the
user's browser while the route existed in the source tree and passed its tests. The cause
was environmental: an older server process was still bound to the port, so the browser was
talking to a build that predated the route. Nothing in the product made that visible.

This module makes the running build self-describing. Capabilities are derived from the
application's **actual route table at runtime** — never from a hand-maintained list — so
the report cannot drift from reality or claim a route the process does not serve. The
browser UI calls `/api/build-info` on load and shows a banner when the server it reached
is missing capabilities the page depends on.
"""

from __future__ import annotations

from typing import Any, Iterable

from backend.app.config.settings import (
    APP_NAME,
    APP_PHASE,
    APP_VERSION,
    AUTO_CREATE_SCHEMA,
    DATABASE_URL,
    DEMO_SEED_ENABLED,
    ENVIRONMENT,
    PUBLIC_BASE_URL,
)

# capability key → the route path that proves it is served by this process.
# Every value must be a path that appears verbatim in the app's route table.
CAPABILITY_ROUTES: dict[str, str] = {
    "ui.workspace": "/ui",
    "ui.start_here": "/ui/start",
    "ui.governance": "/ui/governance",
    "decision.config": "/api/decision-config",
    "decision.comparison": "/api/scenarios/{scenario_id}/comparison",
    "scenario.overrides": "/api/scenarios/{scenario_id}/overrides",
    "excel.mappings": "/api/ingestions/mappings",
    "excel.template": "/api/ingestions/template",
    "excel.validate": "/api/ingestions/excel/validate",
    "excel.commit": "/api/ingestions/excel",
    "governance.summary": "/api/governance/summary",
    "governance.exposed_outputs": "/api/exposed-outputs",
    "execution.runs": "/api/executions",
    "federation.graph": "/api/graph",
    "lineage.result": "/api/lineage/{result_id}",
    # D25 — React product and its read models.
    "product.app": "/app/{path:path}",
    "product.workspace": "/api/workspace",
    "product.catalog": "/api/catalog",
    "product.activity": "/api/activity",
    "product.explanation": "/api/scenarios/{scenario_id}/explanation",
    "product.run_compare": "/api/runs/compare",
    "product.upload_impact": "/api/ingestions/{ingestion_id}/impact",
    "platform.ready": "/ready",
    # D26 — sign-in, scope and audit.
    "auth.session": "/api/session",
    "auth.login": "/api/auth/login",
    "governance.audit": "/api/audit",
}


def route_paths(app: Any) -> set[str]:
    """Every path the application actually serves.

    Walks the route tree rather than reading `app.routes` flat. FastAPI 0.141 does not
    flatten `include_router` results any more: an included router appears as a single
    wrapper object holding the real `APIRoute`s on `.original_router`. Earlier versions
    flattened them. Both shapes — plus `Mount`ed sub-applications — are handled here, so
    the capability report keeps working across FastAPI versions instead of silently
    reporting every route as missing.

    Routes carry their prefix in `.path` already, so no prefix arithmetic is needed.
    """
    paths: set[str] = set()
    seen: set[int] = set()

    def walk(node: Any) -> None:
        if node is None or id(node) in seen:
            return
        seen.add(id(node))

        path = getattr(node, "path", None)
        if isinstance(path, str) and path:
            paths.add(path)

        # Wrapped include (FastAPI >= 0.141), a Mount's sub-app, or a plain router.
        for attr in ("original_router", "router", "app"):
            child = getattr(node, attr, None)
            if child is not None and child is not node:
                walk(child)

        routes = getattr(node, "routes", None)
        if isinstance(routes, (list, tuple)):
            for route in routes:
                walk(route)

    walk(app)
    return paths


def capabilities(app: Any) -> dict[str, bool]:
    """capability key → whether this process actually serves the proving route."""
    served = route_paths(app)
    return {key: path in served for key, path in CAPABILITY_ROUTES.items()}


def missing_capabilities(app: Any) -> list[str]:
    return sorted(k for k, ok in capabilities(app).items() if not ok)


def _db_flavour(url: str) -> str:
    """Database family only — never the host, user, or password."""
    scheme = url.split("://", 1)[0]
    if scheme.startswith("sqlite"):
        return "sqlite"
    if scheme.startswith("postgresql"):
        return "postgresql"
    return scheme or "unknown"


def build_info(app: Any) -> dict[str, Any]:
    """The full self-report served at /api/build-info.

    Contains no secrets: the database is reported by family only, and PUBLIC_BASE_URL is
    whatever an operator configured (empty when the app has not been deployed anywhere).
    """
    from backend.app.web import frontend_info

    caps = capabilities(app)
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "phase": APP_PHASE,
        "environment": ENVIRONMENT,
        "database": _db_flavour(DATABASE_URL),
        "auto_create_schema": AUTO_CREATE_SCHEMA,
        "demo_seed_enabled": DEMO_SEED_ENABLED,
        "public_base_url": PUBLIC_BASE_URL or None,
        "capabilities": caps,
        "missing_capabilities": sorted(k for k, ok in caps.items() if not ok),
        "route_count": len(route_paths(app)),
        "frontend": frontend_info(),
        "auth_mode": _auth_mode(),
    }


def _auth_mode() -> str:
    from backend.app.security.auth import AuthConfigError, effective_auth_mode

    try:
        return effective_auth_mode()
    except AuthConfigError:
        return "misconfigured"


def format_missing(missing: Iterable[str]) -> str:
    items = list(missing)
    return ", ".join(items) if items else "none"
