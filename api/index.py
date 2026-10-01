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
    """Thin ASGI wrapper that defers the real import until the first request.

    The real app is loaded before the lifespan scope is forwarded so that
    Starlette can mark itself started — without this, Starlette 1.x returns
    404 for every HTTP route because the router is flagged as uninitialised.
    """

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

        # TEMP DIAGNOSTIC — remove after confirming path forwarding works.
        import json as _json
        _body = _json.dumps({
            "scope_path": scope.get("path"),
            "import_ok": self._app is not None,
            "import_error": self._import_error,
        }).encode()
        await send({"type": "http.response.start", "status": 200, "headers": [
            [b"content-type", b"application/json"],
            [b"content-length", str(len(_body)).encode()],
        ]})
        await send({"type": "http.response.body", "body": _body})
        return
        # END DIAGNOSTIC

        await self._app(scope, receive, send)  # noqa: unreachable


app = _LazyApp()
