"""Vercel serverless entry point — lazy ASGI wrapper.

Vercel validates this file before pip-installing requirements, so the top
level must not import anything from backend (which needs psycopg, SQLAlchemy,
etc.). The real app is loaded on the first request instead.

Path restoration
----------------
Vercel's catch-all rewrite `/(.*) -> /api/index?__vpath=$1` captures the
original request path in the __vpath query parameter, because the ASGI scope
always has path="/api/index" regardless of what URL the user visited.
The wrapper strips __vpath from the query string and patches scope["path"]
before handing the request to FastAPI, so routing works correctly.
"""

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


class _LazyApp:
    """Thin ASGI wrapper: defers import, restores original URL path."""

    _app = None
    _import_error: "str | None" = None

    def _load(self) -> None:
        if self._app is None and self._import_error is None:
            try:
                from backend.app.main import api as _real_app
                self._app = _real_app
            except Exception:
                import traceback
                self._import_error = traceback.format_exc()

    async def __call__(self, scope, receive, send):
        self._load()

        if self._import_error:
            if scope["type"] == "lifespan":
                await receive()
                await send({"type": "lifespan.startup.complete"})
                await receive()
                await send({"type": "lifespan.shutdown.complete"})
            else:
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

        # Restore original path: Vercel injects __vpath=<original-path> via the
        # rewrite rule `/(.*) -> /api/index?__vpath=$1`. Strip it from the query
        # string and patch scope so FastAPI routes correctly.
        if scope["type"] == "http":
            from urllib.parse import parse_qs
            qs_str = scope.get("query_string", b"").decode("latin-1")
            params = parse_qs(qs_str, keep_blank_values=True)
            vpath_parts = params.pop("__vpath", None)
            if vpath_parts is not None:
                original_path = "/" + vpath_parts[0]
                new_qs = "&".join(
                    f"{k}={v[0]}" for k, v in params.items()
                ).encode("latin-1")
                scope.update({
                    "path": original_path,
                    "raw_path": original_path.encode("latin-1"),
                    "query_string": new_qs,
                })

        await self._app(scope, receive, send)


app = _LazyApp()
