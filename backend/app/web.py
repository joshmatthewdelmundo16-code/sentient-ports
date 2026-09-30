"""Serving the React product and reporting readiness — D25.

The React bundle (frontend/dist, built by Vite) is served by this same process at /app, so
the browser talks to exactly one origin: no CORS, and the session cookie never crosses
sites. Client-side routes (/app/scenarios, /app/network, …) fall back to index.html; real
files are served with cache headers that suit Vite's content-hashed assets.

If the bundle has not been built, /app answers 503 with instructions instead of a blank
page, and /api/build-info reports `frontend.built = false` — the same "never let a stale or
missing build look like a working one" rule D24 introduced for the server itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from backend.app.config import settings

_PLATFORM_DIR = Path(__file__).resolve().parents[2]

_NOT_BUILT = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Product not built</title>
<style>body{font:16px/1.5 system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem;color:#1b2430}
code{background:#eef1f5;padding:.1rem .3rem;border-radius:4px}</style></head><body>
<h1>The React product has not been built</h1>
<p>This server is running, but it has no frontend bundle to serve at <code>/app</code>.</p>
<p>Build it once from <code>platform/</code>:</p>
<p><code>python scripts/build_frontend.py</code></p>
<p>The previous interface is still available at <a href="/ui">/ui</a>.</p>
</body></html>"""


def frontend_dist() -> Path:
    return Path(settings.FRONTEND_DIST)


def frontend_info() -> dict[str, Any]:
    dist = frontend_dist()
    index = dist / "index.html"
    info: dict[str, Any] = {"built": index.is_file(), "mount": "/app"}
    meta = dist / "build-meta.json"
    if meta.is_file():
        try:
            info.update(json.loads(meta.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return info


def _safe_file(dist: Path, rel: str) -> Path | None:
    """Resolve rel inside dist; None if it escapes dist or is not a file."""
    try:
        candidate = (dist / rel).resolve()
        root = dist.resolve()
    except (OSError, ValueError):
        return None
    if candidate != root and root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


def mount_frontend(app: FastAPI) -> None:
    @app.api_route("/app", methods=["GET", "HEAD"], include_in_schema=False)
    def app_root() -> RedirectResponse:
        return RedirectResponse(url="/app/")

    @app.api_route("/app/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def app_spa(path: str) -> Response:
        dist = frontend_dist()
        index = dist / "index.html"
        if not index.is_file():
            return HTMLResponse(_NOT_BUILT, status_code=503)
        if path:
            found = _safe_file(dist, path)
            if found is not None:
                immutable = path.startswith("assets/")
                return FileResponse(found, headers={
                    "Cache-Control": "public, max-age=31536000, immutable" if immutable
                    else "no-cache",
                })
            # A missing asset must be a 404, not the HTML shell (which would break the page
            # with a MIME error instead of a clear failure).
            if "." in path.rsplit("/", 1)[-1]:
                raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

def alembic_head() -> str | None:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(str(_PLATFORM_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(_PLATFORM_DIR / "alembic"))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:  # noqa: BLE001 — readiness must report, not crash
        return None


def readiness(engine) -> tuple[bool, dict[str, Any]]:
    """(ready, report). Ready = database reachable AND schema matches this build."""
    report: dict[str, Any] = {"database": "unknown", "schema": "unknown"}
    head = alembic_head()
    report["schema_head"] = head
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            report["database"] = "ok"
            insp = sa_inspect(conn)
            if insp.has_table("alembic_version"):
                current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
                report["schema_revision"] = current
                if head is not None and current != head:
                    report["schema"] = "migrations_pending"
                else:
                    report["schema"] = "ok"
            elif engine.dialect.name == "sqlite" and settings.AUTO_CREATE_SCHEMA:
                report["schema"] = "managed_by_create_all"
            else:
                report["schema"] = "not_migrated"
    except Exception:  # noqa: BLE001 — never leak connection strings or driver detail
        report["database"] = "unreachable"
    ready = report["database"] == "ok" and report["schema"] in ("ok", "managed_by_create_all")
    report["frontend_built"] = frontend_info()["built"]
    return ready, report
