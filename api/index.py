"""Vercel serverless entry point — lazy ASGI wrapper.

Vercel validates this file before pip-installing requirements, so the top
level must not import anything from backend (which needs psycopg, SQLAlchemy,
etc.). The real app is loaded on the first request instead.
"""

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


class _LazyApp:
    """Thin ASGI wrapper that defers the real import until the first request."""

    _app = None
    _import_error: "str | None" = None

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            # Serverless: skip lifespan — no persistent process to manage.
            await receive()
            await send({"type": "lifespan.startup.complete"})
            await receive()
            await send({"type": "lifespan.shutdown.complete"})
            return

        if self._app is None and self._import_error is None:
            try:
                from backend.app.main import api as _real_app
                self._app = _real_app
            except Exception:
                import traceback
                self._import_error = traceback.format_exc()

        if self._import_error:
            body = (
                "Import failed — see traceback below.\n\n"
                + self._import_error
            ).encode()
            headers = [
                [b"content-type", b"text/plain; charset=utf-8"],
                [b"content-length", str(len(body)).encode()],
            ]
            await send({"type": "http.response.start", "status": 500, "headers": headers})
            await send({"type": "http.response.body", "body": body})
            return

        # Echo path for quick sanity-check — remove once routing confirmed working.
        if scope.get("path") == "/_debug":
            import json
            body = json.dumps({
                "path": scope.get("path"),
                "raw_path": scope.get("raw_path", b"").decode(errors="replace"),
                "query_string": scope.get("query_string", b"").decode(errors="replace"),
                "app_loaded": self._app is not None,
            }).encode()
            headers = [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode()],
            ]
            await send({"type": "http.response.start", "status": 200, "headers": headers})
            await send({"type": "http.response.body", "body": body})
            return

        await self._app(scope, receive, send)


app = _LazyApp()
