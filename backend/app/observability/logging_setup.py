"""One log pipeline for the whole process: stdlib logging (app code,
uvicorn, SQLAlchemy) is routed into loguru (pipecat's logger), and loguru
writes one line per record to stderr -- JSON on Railway, plain text locally
(settings.log_as_json).

Every JSON line carries `level`, `message`, `logger`, `time`, plus whatever
context is bound: `call_session_id` / `host_id` for anything running inside
a call (usage_meter's per-task context), `request_id` for an HTTP request,
and any structured fields a caller passed via `extra=` (e.g. `service`,
`event` from app/observability/health.py). Railway indexes these, so a
failing call can be pulled up with `@call_session_id:<id>` instead of
grepping text.

loguru -> stdlib propagation is kept (pytest's caplog relies on it); records
that came from loguru are tagged so the stdlib -> loguru intercept never
loops them back.
"""

from __future__ import annotations

import inspect
import json
import logging
import sys
import traceback
import uuid
from contextvars import ContextVar

from loguru import logger as loguru_logger

from app.config import settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_FROM_LOGURU = "_mira_from_loguru"
_STD_RECORD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", _FROM_LOGURU}
_configured = False


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def _context_fields() -> dict:
    fields: dict = {}
    try:
        from app.services.usage_meter import current_usage_context

        call_session_id, host_id = current_usage_context()
        if call_session_id:
            fields["call_session_id"] = str(call_session_id)
        if host_id:
            fields["host_id"] = str(host_id)
    except Exception:
        pass
    request_id = request_id_var.get()
    if request_id:
        fields["request_id"] = request_id
    return fields


def _json_default(value):
    return str(value)


def _json_sink(message) -> None:
    record = message.record
    payload = {
        "time": record["time"].isoformat(),
        "level": record["level"].name.lower(),
        "message": record["message"],
        "logger": record["name"],
        "environment": settings.environment,
    }
    payload.update(_context_fields())
    for key, value in record["extra"].items():
        if value is not None:
            payload[key] = value
    if record["exception"] is not None:
        exc = record["exception"]
        payload["exception"] = "".join(traceback.format_exception(exc.type, exc.value, exc.traceback))[-8000:]
    try:
        line = json.dumps(payload, default=_json_default, ensure_ascii=False)
    except Exception:
        line = json.dumps({"level": payload["level"], "message": str(record["message"])})
    sys.stderr.write(line + "\n")


class _InterceptHandler(logging.Handler):
    """stdlib record -> loguru, preserving level, logger name, exception and
    any `extra=` fields."""

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, _FROM_LOGURU, False):
            return
        try:
            level = loguru_logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        extra = {k: v for k, v in vars(record).items() if k not in _STD_RECORD_ATTRS and not k.startswith("_")}
        extra["logger_name"] = record.name
        # Report the real caller's function/line, not this handler's.
        # (loguru's documented InterceptHandler recipe.)
        frame, depth = inspect.currentframe(), 0
        while frame is not None and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1
        loguru_logger.bind(**extra).opt(exception=record.exc_info, depth=depth).log(level, record.getMessage())


class _PropagateToStdlib(logging.Handler):
    """loguru record -> the stdlib logger of the same name (caplog in tests)."""

    def emit(self, record: logging.LogRecord) -> None:
        setattr(record, _FROM_LOGURU, True)
        logging.getLogger(record.name).handle(record)


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    def _patch_name(record) -> None:
        # Intercepted stdlib records report their own logger name, not this
        # module's.
        name = record["extra"].pop("logger_name", None)
        if name:
            record["name"] = name

    loguru_logger.remove()
    loguru_logger.configure(patcher=_patch_name)
    if settings.log_as_json:
        loguru_logger.add(_json_sink, level="DEBUG")
    else:
        loguru_logger.add(sys.stderr, level="DEBUG")
    loguru_logger.add(_PropagateToStdlib(), level="DEBUG", format="{message}")

    root = logging.getLogger()
    root.handlers = [_InterceptHandler()]
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True
