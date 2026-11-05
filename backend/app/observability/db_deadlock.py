"""Postgres deadlocks, reported with the data that was lost.

When two transactions each wait on a lock the other holds, Postgres aborts one
of them (SQLSTATE 40P01) and rolls back EVERYTHING that transaction had done.
The caller just sees a database error, and what the transaction was writing is
gone. This module makes that visible: the admins get an email naming the code
path, the call/host it ran for, the statement that failed, and the writes the
transaction was holding when it was rolled back, so nothing is silently lost.

How: a SQLAlchemy `handle_error` listener on the shared engine sees every
failed statement with its SQL and parameters. A `before_cursor_execute`
listener remembers the last few statements of the transaction in progress
(per DBAPI connection, cleared on begin/commit/rollback) -- that memory is
all the "lost data" there is to report, since the database has already thrown
the rest away.

It only observes (same rule as the rest of app/observability): the listener
never raises, never swallows or changes the exception, and does no I/O on the
query path. The email is a detached task, sent only where health alerts are
active (production by default) and throttled so one lock storm is one email.
The structured log line is written either way.

Values are truncated, and a statement that touches a password/token/secret
or payment-credential column (billing_config, anything Razorpay) has its values
withheld instead of printed. Guest names and phone numbers are shown: the
admins asked to see exactly what was lost.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import settings
from app.utils.throttle import Throttle

logger = logging.getLogger(__name__)

DEADLOCK_SQLSTATE = "40P01"
HISTORY_LENGTH = 25  # statements remembered per open transaction
STATEMENT_LIMIT = 700  # characters of SQL shown
VALUE_LIMIT = 200  # characters per parameter value shown
VALUES_SHOWN = 40  # parameter values shown per statement
ROWS_SHOWN = 3  # rows shown for a multi-row (executemany) statement
FRAMES_SHOWN = 8
ALERT_WINDOW = timedelta(minutes=10)

_APP_MARKER = "/app/"  # path segment that identifies this project's own frames
_HISTORY_KEY = "mira_tx_statements"
_WRITE = re.compile(r"^\s*(insert|update|delete)\b", re.I)
_SENSITIVE = re.compile(r"password|passwd|secret|token|credential|api_key|authorization|otp|code_hash|billing_config|razorpay|short_url", re.I)

_throttle = Throttle(ALERT_WINDOW)
_background: set[asyncio.Task] = set()  # keep detached sends from being collected mid-flight


@dataclass(frozen=True)
class Statement:
    sql: str
    values: str


@dataclass
class DeadlockReport:
    occurred_at: datetime
    message: str
    pg_detail: str | None
    failing: Statement
    lost_writes: list[Statement]
    location: list[str]
    call_session_id: str | None = None
    host_id: str | None = None
    request_id: str | None = None
    held_back: int = 0
    extra: dict = field(default_factory=dict)

    def fingerprint(self) -> str:
        where = self.location[-1] if self.location else "unknown"
        return f"{where}|{self.failing.sql[:80]}"

    def as_text(self) -> str:
        lines = [
            "A database transaction was rolled back to break a deadlock.",
            f"When: {self.occurred_at.isoformat()}",
            f"Where: {' <- '.join(reversed(self.location)) or 'unknown'}",
            f"Call: {self.call_session_id or '-'}  Host: {self.host_id or '-'}  Request: {self.request_id or '-'}",
            f"Postgres: {self.message}" + (f" ({self.pg_detail})" if self.pg_detail else ""),
            f"Failing statement: {self.failing.sql}",
            f"  values: {self.failing.values}",
        ]
        if self.lost_writes:
            lines.append(f"Writes rolled back with it ({len(self.lost_writes)}):")
            for stmt in self.lost_writes:
                lines += [f"  - {stmt.sql}", f"    values: {stmt.values}"]
        else:
            lines.append("No earlier writes were recorded in this transaction.")
        if self.held_back:
            lines.append(f"{self.held_back} similar deadlock(s) in the last {int(ALERT_WINDOW.total_seconds() // 60)} min were folded into this report.")
        return "\n".join(lines)


# -- what the transaction was doing ---------------------------------------


def _remember(conn, cursor, statement, parameters, context, executemany) -> None:
    try:
        history = conn.info.get(_HISTORY_KEY)
        if history is None:
            history = conn.info[_HISTORY_KEY] = deque(maxlen=HISTORY_LENGTH)
        history.append((statement, parameters, executemany))
    except Exception:
        pass  # observation must never touch the query


def _forget(conn) -> None:
    try:
        conn.info.pop(_HISTORY_KEY, None)
    except Exception:
        pass


# -- describing it ---------------------------------------------------------


def _short(value) -> str:
    text = repr(value)
    return text if len(text) <= VALUE_LIMIT else f"{text[:VALUE_LIMIT]}... ({len(text)} chars)"


def _values(parameters, executemany: bool) -> str:
    if parameters is None:
        return "-"
    if executemany and isinstance(parameters, (list, tuple)):
        shown = [_values(row, False) for row in list(parameters)[:ROWS_SHOWN]]
        more = len(parameters) - ROWS_SHOWN
        return " | ".join(shown) + (f" | (+{more} more rows)" if more > 0 else "")
    if isinstance(parameters, dict):
        items = [f"{k}={_short(v)}" for k, v in list(parameters.items())[:VALUES_SHOWN]]
        extra = len(parameters) - VALUES_SHOWN
    else:
        items = [_short(v) for v in list(parameters)[:VALUES_SHOWN]]
        extra = len(parameters) - VALUES_SHOWN
    return "(" + ", ".join(items) + (f", ... +{extra} more" if extra > 0 else "") + ")"


def _describe(statement: str, parameters, executemany: bool = False) -> Statement:
    sql = " ".join(str(statement).split())
    if len(sql) > STATEMENT_LIMIT:
        sql = sql[:STATEMENT_LIMIT] + "..."
    if _SENSITIVE.search(str(statement)):
        return Statement(sql, "withheld (this statement touches a password/token/secret column)")
    try:
        return Statement(sql, _values(parameters, executemany))
    except Exception:
        return Statement(sql, "(values could not be shown)")


def _is_deadlock(exc: BaseException | None) -> bool:
    for _ in range(5):  # the driver error is wrapped; follow the cause chain
        if exc is None:
            return False
        code = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
        if code == DEADLOCK_SQLSTATE or type(exc).__name__ == "DeadlockDetectedError":
            return True
        exc = exc.__cause__
    return False


def _full_stack() -> list[traceback.FrameSummary]:
    """Outermost first. SQLAlchemy runs the query inside a helper greenlet, so
    the app code that awaited it is not on this greenlet's own stack: it is
    the parent greenlet's, suspended at the point it handed over. Walk the
    chain so the caller is visible."""
    try:
        import greenlet

        current = greenlet.getcurrent()
        chain, g = [], current
        while g is not None:
            chain.append(g)
            g = g.parent
        stack: list[traceback.FrameSummary] = []
        for g in reversed(chain):
            frame = sys._getframe() if g is current else g.gr_frame
            if frame is not None:
                stack += traceback.extract_stack(frame)
        return stack
    except Exception:
        return traceback.extract_stack()


def _code_path() -> list[str]:
    """The app frames that led to the query, outermost first."""
    frames = []
    for frame in _full_stack():
        filename = frame.filename.replace("\\", "/")
        at = filename.rfind(_APP_MARKER)
        if at == -1 or "/observability/db_deadlock" in filename or "site-packages" in filename:
            continue
        frames.append(f"{filename[at + 1:]}:{frame.lineno} in {frame.name}")
    return frames[-FRAMES_SHOWN:]


def _build_report(ctx) -> DeadlockReport:
    from app.observability.logging_setup import request_id_var
    from app.services.usage_meter import current_usage_context

    exc = ctx.original_exception
    cause = exc.__cause__ or exc
    history = list(ctx.connection.info.get(_HISTORY_KEY, ())) if ctx.connection is not None else []
    if history and history[-1][0] == ctx.statement:
        history.pop()  # the failing statement is shown on its own
    call_session_id, host_id = current_usage_context()
    return DeadlockReport(
        occurred_at=datetime.now(timezone.utc),
        message=str(getattr(cause, "message", None) or cause).splitlines()[0][:300],
        pg_detail=" ".join(str(getattr(cause, "detail", "") or "").split()) or None,
        failing=_describe(ctx.statement or "?", ctx.parameters, bool(getattr(ctx, "is_executemany", False))),
        lost_writes=[_describe(s, p, m) for s, p, m in history if _WRITE.match(str(s))],
        location=_code_path(),
        call_session_id=str(call_session_id) if call_session_id else None,
        host_id=str(host_id) if host_id else None,
        request_id=request_id_var.get(),
    )


# -- telling someone -------------------------------------------------------


def _subject() -> str:
    return f"🔴 Mira [{settings.environment}] · Database deadlock — a transaction was rolled back"


async def _send(report: DeadlockReport) -> None:
    try:
        from app.integrations import email_templates
        from app.services import health_monitor_service

        html = email_templates.build_deadlock_alert_email_html(
            occurred_at=report.occurred_at,
            location=report.location,
            call_session_id=report.call_session_id,
            host_id=report.host_id,
            request_id=report.request_id,
            message=report.message,
            pg_detail=report.pg_detail,
            failing=(report.failing.sql, report.failing.values),
            lost_writes=[(s.sql, s.values) for s in report.lost_writes],
            held_back=report.held_back,
            window_minutes=int(ALERT_WINDOW.total_seconds() // 60),
            admin_url=health_monitor_service.admin_url(),
        )
        await health_monitor_service.send_admin_alert(_subject(), report.as_text(), html)
    except Exception:
        logger.exception("db_deadlock_alert_failed")


def _emit(report: DeadlockReport) -> None:
    logger.error(
        "db_deadlock_detected\n%s",
        report.as_text(),
        extra={"event": "db_deadlock", "service": "postgres", "location": report.location[-1] if report.location else None},
    )
    if not (settings.health_alerts_active and settings.alert_email_list):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop to send on; the log line above is the record
    send, held_back = _throttle.take(report.fingerprint())
    if not send:
        return
    report.held_back = held_back
    task = loop.create_task(_send(report))
    _background.add(task)
    task.add_done_callback(_background.discard)


def _on_error(ctx) -> None:
    """Returns nothing, so SQLAlchemy re-raises the original exception."""
    try:
        if _is_deadlock(ctx.original_exception):
            _emit(_build_report(ctx))
    except Exception:
        logger.exception("db_deadlock_report_failed")


_installed: set[int] = set()


def install(engine: AsyncEngine) -> None:
    sync_engine = engine.sync_engine
    if id(sync_engine) in _installed:
        return
    _installed.add(id(sync_engine))
    event.listen(sync_engine, "before_cursor_execute", _remember)
    for name in ("begin", "commit", "rollback"):
        event.listen(sync_engine, name, _forget)
    event.listen(sync_engine, "handle_error", _on_error)
