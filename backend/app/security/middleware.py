"""HTTP hardening middleware — D26 (pure ASGI, safe for streaming responses).

  * Request ID          X-Request-ID on every response (accepted from a trusted proxy if it
                        looks sane), recorded on audit events and in the access log.
  * Body size limit     Content-Length checked up front and the streamed body counted, so a
                        chunked upload cannot bypass MAX_REQUEST_BYTES.
  * Security headers    per-surface Content-Security-Policy (strict for the React product,
                        relaxed only where the legacy pages need inline script), nosniff,
                        frame denial, referrer and permissions policies, COOP/CORP, HSTS when
                        the deployment is behind HTTPS, and no-store on API responses.
  * Safe errors         an unhandled exception becomes a JSON 500 with the request ID —
                        never a traceback or exception text. The traceback goes to the log.
  * Deferred audit      denials recorded during the request are written after the response.
  * Access log          method, route, status, duration, request ID, principal — no bodies,
                        no query strings (they may carry identifiers), no cookies.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid

import anyio
from starlette.datastructures import MutableHeaders, State
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.app.config import settings
from backend.app.security.audit import flush_deferred

log = logging.getLogger("platform.http")

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

CSP_APP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
           "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
           "form-action 'self'; object-src 'none'")
# The D12–D24 Jinja pages use inline <script> and onclick handlers. Kept as the rollback
# path, so they get a policy that still blocks framing, plugins and third-party origins.
CSP_LEGACY = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
              "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
              "form-action 'self'; object-src 'none'")
CSP_DOCS = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: "
            "https://fastapi.tiangolo.com; connect-src 'self'; frame-ancestors 'none'; object-src 'none'")
CSP_API = "default-src 'none'; frame-ancestors 'none'"


class _BodyTooLarge(Exception):
    pass


def _csp_for(path: str) -> str:
    if path.startswith("/app"):
        return CSP_APP
    if path.startswith("/ui"):
        return CSP_LEGACY
    if path.startswith(("/docs", "/redoc")):
        return CSP_DOCS
    return CSP_API


async def _send_json(send: Send, status: int, body: dict, request_id: str, path: str) -> None:
    payload = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode()),
        (b"x-request-id", request_id.encode()),
        (b"x-content-type-options", b"nosniff"),
        (b"cache-control", b"no-store"),
        (b"content-security-policy", _csp_for(path).encode()),
    ]})
    await send({"type": "http.response.body", "body": payload})


class SecurityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        rid = headers.get("x-request-id", "")
        request_id = rid if _REQUEST_ID.match(rid) else uuid.uuid4().hex
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        path = scope.get("path", "")
        limit = settings.MAX_REQUEST_BYTES

        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            await _send_json(send, 413, {"detail": f"Request body is larger than {limit} bytes.",
                                         "request_id": request_id}, request_id, path)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge()
            return message

        started = False
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                status_holder["status"] = message["status"]
                h = MutableHeaders(scope=message)
                h.setdefault("x-request-id", request_id)
                h.setdefault("x-content-type-options", "nosniff")
                h.setdefault("x-frame-options", "DENY")
                h.setdefault("referrer-policy", "strict-origin-when-cross-origin")
                h.setdefault("permissions-policy", "camera=(), microphone=(), geolocation=(), payment=()")
                h.setdefault("cross-origin-opener-policy", "same-origin")
                h.setdefault("cross-origin-resource-policy", "same-origin")
                h.setdefault("content-security-policy", _csp_for(path))
                if path.startswith("/api/") or path in ("/health", "/ready"):
                    h.setdefault("cache-control", "no-store")
                if settings.HSTS_ENABLED:
                    h.setdefault("strict-transport-security", "max-age=31536000; includeSubDomains")
            await send(message)

        begin = time.perf_counter()
        try:
            await self.app(scope, limited_receive, send_wrapper)
        except _BodyTooLarge:
            if not started:
                await _send_json(send, 413, {"detail": f"Request body is larger than {limit} bytes.",
                                             "request_id": request_id}, request_id, path)
            status_holder["status"] = 413
        except Exception:
            log.exception("Unhandled error [%s] %s %s", request_id, scope.get("method"), path)
            if not started:
                await _send_json(send, 500, {"detail": "Internal server error.",
                                             "request_id": request_id}, request_id, path)
            status_holder["status"] = 500
            raise
        finally:
            await anyio.to_thread.run_sync(flush_deferred, State(state))
            ctx = state.get("ctx")
            who = getattr(getattr(ctx, "principal", None), "label", "-") if ctx else "-"
            route = scope.get("route")
            log.info("%s %s %s %.1fms rid=%s by=%s", scope.get("method"),
                     getattr(route, "path", path), status_holder["status"],
                     (time.perf_counter() - begin) * 1000, request_id, who)
