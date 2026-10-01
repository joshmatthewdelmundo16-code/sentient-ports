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

    async def __call__(self, scope, receive, send):
        if self._app is None:
            from backend.app.main import api as _real_app
            self._app = _real_app
        await self._app(scope, receive, send)


app = _LazyApp()
