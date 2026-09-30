"""UI router — D12.

Serves the browser UI (HTML pages) and the /api/demo-config endpoint.
All data fetching is done client-side via JavaScript; these routes
just render the page shell or return demo metadata.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
def ui_index(request: Request) -> HTMLResponse:
    """Main browser UI entry point."""
    return templates.TemplateResponse(request, "index.html")


@router.get("/ui/start", response_class=HTMLResponse, include_in_schema=False)
def ui_start(request: Request) -> HTMLResponse:
    """Start Here (D24) — the first-time-user explanation of the whole product.

    Everything a new user needs is in the product itself: what the system does, what a
    baseline and a scenario are, which assumptions can be changed, the full Excel
    lifecycle, where results and reasoning appear, and an honest statement of which
    capabilities are implemented, synthetic, or absent. No developer README required.
    """
    return templates.TemplateResponse(request, "start.html")


@router.get("/ui/governance", response_class=HTMLResponse, include_in_schema=False)
def ui_governance(request: Request) -> HTMLResponse:
    """Read-only governance view (D22): participants, approved outputs, exposed fields."""
    return templates.TemplateResponse(request, "governance.html")


def _scoped():
    # Late import: security.auth imports the ORM, which must not load before settings.
    from backend.app.security.auth import request_context
    return [Depends(request_context)]


@router.get(
    "/api/demo-config",
    dependencies=_scoped(),
    tags=["Demo"],
    summary="Demo configuration — version IDs and dataset IDs for the UI",
)
def get_demo_config() -> dict:
    """Returns the demo chain configuration populated at startup.

    The browser UI uses these IDs to call graph-execution and
    propagation endpoints without having to discover them.
    """
    from backend.app.ui import demo_state  # late import avoids circular deps

    return demo_state.config


@router.get(
    "/api/decision-config",
    dependencies=_scoped(),
    tags=["Demo"],
    summary="Decision-support config — demo baseline/scenario IDs for the UI (D20)",
)
def get_decision_config() -> dict:
    """Read-only. Returns the demo baseline/scenario IDs and port-domain metadata the
    decision workspace loads by default. Empty ({}) when no decision demo data was seeded
    (e.g. running against a shared PostgreSQL/Supabase database), which the UI renders as an
    empty state. The UI still resolves all metric values through the D19 comparison API.
    """
    from backend.app.ui import decision_state  # late import avoids circular deps

    return decision_state.config
