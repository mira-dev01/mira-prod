"""HTTP-side observability: a request id on every log line, and the REST
API's own 5xx rate as the "api" health service. Pure ASGI (not
BaseHTTPMiddleware) so streaming responses -- the admin health SSE feed --
pass through untouched. Websocket scopes (the voice pipeline) are skipped.

Also the Postgres connection-error hook (install_db_hook): SQLAlchemy's
handle_error event fires on every failed statement; only connection-level
failures (disconnects, OperationalError/InterfaceError) count against the
database's health -- an IntegrityError is an application bug, not an outage.
"""

from __future__ import annotations

import time

from sqlalchemy import event
from sqlalchemy import exc as sa_exc

from app.observability import health
from app.observability.logging_setup import new_request_id, request_id_var

_UNTRACKED_PATHS = ("/health", "/api/v1/health/", "/api/v1/admin/health")


class ObservabilityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        tracked = not path.startswith(_UNTRACKED_PATHS) and scope.get("method") != "OPTIONS"
        token = request_id_var.set(new_request_id())
        status: dict[str, int] = {}
        started = time.monotonic()

        async def _send(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception as exc:
            if tracked:
                health.record(
                    "api", "error", latency_ms=(time.monotonic() - started) * 1000, error=exc, op=f"{scope.get('method')} {path}"
                )
            raise
        else:
            if tracked:
                code = status.get("code", 500)
                latency_ms = (time.monotonic() - started) * 1000
                if code >= 500:
                    health.record("api", "error", latency_ms=latency_ms, error=f"HTTP {code}", kind=f"http_{code}", op=f"{scope.get('method')} {path}")
                else:
                    health.record("api", "ok", latency_ms=latency_ms)
        finally:
            request_id_var.reset(token)


def install_db_hook(async_engine) -> None:
    @event.listens_for(async_engine.sync_engine, "handle_error")
    def _on_db_error(context) -> None:  # pragma: no cover - exercised via real DB failures
        try:
            if context.is_disconnect or isinstance(
                context.sqlalchemy_exception, (sa_exc.OperationalError, sa_exc.InterfaceError)
            ):
                health.record("postgres", "error", error=context.original_exception, kind="connection_error", op="query")
        except Exception:
            pass
