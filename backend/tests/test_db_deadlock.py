"""app/observability/db_deadlock.py against a real Postgres deadlock: two
sessions lock two rows in opposite order, Postgres aborts one of them, and the
admins must be told what that transaction was writing. Also checks the
listener never changes what the caller sees."""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.config import settings
from app.database import AsyncSessionLocal, engine
from app.integrations import email_client, email_templates
from app.models.user import User
from app.observability import db_deadlock
from app.services import health_monitor_service
from app.utils.throttle import Throttle


@pytest.fixture
def emails(monkeypatch):
    """Alerts on, and every email captured instead of sent."""
    sent: list[dict] = []

    async def fake_send(to, subject, body, html_body=None, timeout=15.0):
        sent.append({"to": to, "subject": subject, "text": body, "html": html_body})
        return {"status": "sent"}

    monkeypatch.setattr(settings, "health_alerts_enabled", True)
    monkeypatch.setattr(settings, "alert_emails", "ops@example.com")
    monkeypatch.setattr(email_client, "send_email", fake_send)
    monkeypatch.setattr(db_deadlock, "_throttle", Throttle(db_deadlock.ALERT_WINDOW))
    monkeypatch.setattr(health_monitor_service, "_last_send_failure_at", 0.0)  # no leftover Resend back-off
    return sent


async def _settle() -> None:
    """Let the detached alert tasks finish."""
    pending = list(db_deadlock._background)
    if pending:
        await asyncio.gather(*pending)


async def _two_users(db):
    users = []
    for tag in ("a", "b"):
        user = User(email=f"{tag}-{uuid.uuid4().hex[:8]}@example.com", clerk_user_id=f"user_{uuid.uuid4().hex[:16]}", name=tag)
        db.add(user)
        users.append(user)
    await db.commit()
    return users[0].id, users[1].id


async def _deadlock(a, b, *, committed_first: str | None = None) -> list:
    """t1 locks a then b; t2 locks b then a. One of them is the victim.
    `committed_first`: write+commit that value in each transaction's session
    before it starts, to prove finished transactions are not reported."""

    async def tx(first, second, tag):
        async with AsyncSessionLocal() as session:
            if committed_first:
                await session.execute(update(User).where(User.id == first).values(phone=committed_first))
                await session.commit()
            await session.execute(update(User).where(User.id == first).values(name=f"{tag}-first"))
            await asyncio.sleep(0.3)  # both now hold their first lock
            await session.execute(update(User).where(User.id == second).values(name=f"{tag}-second"))
            await session.commit()

    return await asyncio.gather(tx(a, b, "t1"), tx(b, a, "t2"), return_exceptions=True)


def _victim(results) -> tuple[int, BaseException]:
    errors = [(i, r) for i, r in enumerate(results) if isinstance(r, BaseException)]
    assert len(errors) == 1, results  # Postgres aborts exactly one side
    return errors[0]


async def test_a_deadlock_emails_the_admins_the_failing_statement_and_the_lost_write(db_session, emails, monkeypatch):
    # Treat this test file as "the app" so the code path has frames to show.
    monkeypatch.setattr(db_deadlock, "_APP_MARKER", "/tests/")
    a, b = await _two_users(db_session)

    results = await _deadlock(a, b)
    index, error = _victim(results)
    await _settle()

    # The caller still gets the real database error, untouched.
    assert isinstance(error, DBAPIError)
    assert getattr(error.orig, "sqlstate", None) == "40P01" or "deadlock" in str(error.orig).lower()

    assert len(emails) == 1
    mail = emails[0]
    tag = f"t{index + 1}"
    assert mail["to"] == "ops@example.com"
    assert "deadlock" in mail["subject"].lower()
    # the statement that failed, with the value it was writing...
    assert "UPDATE users" in mail["text"] and f"{tag}-second" in mail["text"]
    # ...and the write that was rolled back along with it
    assert f"{tag}-first" in mail["text"]
    # the failing statement is listed once, not again among the lost writes
    assert mail["text"].count(f"{tag}-second") == 1
    assert mail["text"].count(f"{tag}-first") == 1
    assert "rolled back" in mail["text"].lower()
    assert mail["html"] and f"{tag}-first" in mail["html"]
    # the code path that ran it is named (frames under app/, innermost last)
    where = mail["text"].split("Where:")[1].splitlines()[0]
    assert "tests/test_db_deadlock.py" in where and "tx" in where


async def test_the_other_transaction_is_untouched_and_commits(db_session, emails):
    a, b = await _two_users(db_session)
    results = await _deadlock(a, b)
    _victim(results)
    await _settle()
    survivors = [r for r in results if not isinstance(r, BaseException)]
    assert len(survivors) == 1


async def test_a_transaction_that_already_committed_is_not_reported_as_lost(db_session, emails):
    a, b = await _two_users(db_session)
    await _deadlock(a, b, committed_first="+919000000000")
    await _settle()
    assert len(emails) == 1
    assert "+919000000000" not in emails[0]["text"]  # that write was saved, not lost


async def test_logged_but_not_emailed_when_alerts_are_off(db_session, monkeypatch, caplog):
    sent = []

    async def fake_send(*args, **kwargs):
        sent.append(args)
        return {"status": "sent"}

    monkeypatch.setattr(settings, "health_alerts_enabled", False)
    monkeypatch.setattr(email_client, "send_email", fake_send)
    a, b = await _two_users(db_session)
    with caplog.at_level(logging.ERROR, logger="app.observability.db_deadlock"):
        _victim(await _deadlock(a, b))
        await _settle()
    assert sent == []
    assert any("db_deadlock_detected" in r.getMessage() for r in caplog.records)


async def test_other_database_errors_are_not_reported(db_session, emails, caplog):
    user = User(email=f"dup-{uuid.uuid4().hex[:6]}@example.com", clerk_user_id=f"user_{uuid.uuid4().hex[:16]}")
    db_session.add(user)
    await db_session.commit()
    async with AsyncSessionLocal() as other:
        other.add(User(email=user.email, clerk_user_id=f"user_{uuid.uuid4().hex[:16]}"))
        with caplog.at_level(logging.ERROR, logger="app.observability.db_deadlock"):
            with pytest.raises(IntegrityError):
                await other.commit()
    await _settle()
    assert emails == []
    assert not any("db_deadlock" in r.getMessage() for r in caplog.records)


async def test_a_failing_email_never_changes_what_the_caller_sees(db_session, monkeypatch):
    async def broken_send(*args, **kwargs):
        raise RuntimeError("resend is down")

    monkeypatch.setattr(settings, "health_alerts_enabled", True)
    monkeypatch.setattr(settings, "alert_emails", "ops@example.com")
    monkeypatch.setattr(email_client, "send_email", broken_send)
    monkeypatch.setattr(db_deadlock, "_throttle", Throttle(db_deadlock.ALERT_WINDOW))
    monkeypatch.setattr(health_monitor_service, "_last_send_failure_at", 0.0)
    a, b = await _two_users(db_session)
    _, error = _victim(await _deadlock(a, b))
    await _settle()  # the alert task swallows the send failure
    assert isinstance(error, DBAPIError)


def _report(**overrides) -> db_deadlock.DeadlockReport:
    base = dict(
        occurred_at=datetime(2031, 3, 10, 9, 0, tzinfo=timezone.utc),
        message="deadlock detected",
        pg_detail="Process 1 waits for ShareLock on transaction 2; blocked by process 3.",
        failing=db_deadlock.Statement("UPDATE leads SET status=$1 WHERE id=$2", "('hot', 'abc')"),
        lost_writes=[db_deadlock.Statement("INSERT INTO notifications (message) VALUES ($1)", "('Guest wants a refund',)")],
        location=["app/voice/pipeline.py:100 in _run_pipeline", "app/services/lead_service.py:50 in upsert_lead"],
    )
    base.update(overrides)
    return db_deadlock.DeadlockReport(**base)


async def test_a_burst_is_one_email_and_the_next_says_how_many_were_folded_in(emails, monkeypatch):
    now = [datetime(2031, 3, 10, 9, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(db_deadlock, "_throttle", Throttle(db_deadlock.ALERT_WINDOW, clock=lambda: now[0]))

    for _ in range(3):
        db_deadlock._emit(_report())
    await _settle()
    assert len(emails) == 1  # the other two were held back

    now[0] += db_deadlock.ALERT_WINDOW + timedelta(seconds=1)
    db_deadlock._emit(_report())
    await _settle()
    assert len(emails) == 2
    assert "2 similar deadlock" in emails[1]["text"]

    db_deadlock._emit(_report(failing=db_deadlock.Statement("UPDATE bookings SET status=$1", "('x',)")))
    await _settle()
    assert len(emails) == 3  # a different statement is a different problem, not throttled


async def test_the_code_path_includes_the_app_code_that_awaited_the_query(monkeypatch):
    # SQLAlchemy runs the query in a helper greenlet; the awaiting caller is on
    # the PARENT greenlet's stack. Reading only the current stack finds nothing.
    from sqlalchemy.util import greenlet_spawn

    monkeypatch.setattr(db_deadlock, "_APP_MARKER", "/tests/")
    seen: dict = {}

    def inside_the_query():
        seen["path"] = db_deadlock._code_path()

    async def app_function_that_awaits_a_query():
        await greenlet_spawn(inside_the_query)

    await app_function_that_awaits_a_query()
    assert any("app_function_that_awaits_a_query" in frame for frame in seen["path"]), seen["path"]
    # outermost caller first, innermost last
    assert seen["path"][-1].endswith("in inside_the_query")


def test_values_of_password_and_token_columns_are_withheld():
    stmt = db_deadlock._describe("UPDATE users SET hashed_password=$1 WHERE id=$2", ("s3cret-hash", "id-1"))
    assert "s3cret-hash" not in stmt.values and "withheld" in stmt.values
    stmt = db_deadlock._describe("INSERT INTO x (take_call_token) VALUES ($1)", ("tok-123",))
    assert "tok-123" not in stmt.values


def test_payment_credentials_never_reach_a_deadlock_email():
    for statement in (
        "UPDATE users SET billing_config=$1 WHERE users.id = $2::UUID",
        "INSERT INTO payment_requests (razorpay_link_id, short_url) VALUES ($1, $2)",
        "UPDATE users SET agent_config=$1, razorpay_key=$2",
    ):
        shown = db_deadlock._describe(statement, ('{"razorpay": {"secret_enc": "gAAAA-cipher"}}', "https://rzp.io/i/abc"))
        assert "gAAAA-cipher" not in shown.values and "rzp.io" not in shown.values and "withheld" in shown.values
    # guest details are still shown: the admins asked to see exactly what was lost
    ok = db_deadlock._describe("UPDATE leads SET guest_name=$1, phone=$2", ("Asha Rao", "+919811111111"))
    assert "Asha Rao" in ok.values and "+919811111111" in ok.values


def test_long_values_and_many_rows_are_cut_down():
    big = "x" * 5000
    stmt = db_deadlock._describe("UPDATE faq_entries SET answer=$1", (big,))
    assert len(stmt.values) < 400
    assert "5002 chars" in stmt.values  # says how much was cut, not just that it was
    rows = [(i, "v") for i in range(100)]
    many = db_deadlock._describe("INSERT INTO t (a, b) VALUES ($1, $2)", rows, executemany=True)
    assert "+97 more rows" in many.values
    assert len(db_deadlock._describe("SELECT " + "y" * 5000, None).sql) <= db_deadlock.STATEMENT_LIMIT + 3


def test_only_the_last_few_statements_of_a_transaction_are_remembered():
    class Conn:
        info: dict = {}

    conn = Conn()
    conn.info = {}
    for i in range(db_deadlock.HISTORY_LENGTH + 15):
        db_deadlock._remember(conn, None, f"UPDATE t SET a={i}", (i,), None, False)
    history = conn.info[db_deadlock._HISTORY_KEY]
    assert len(history) == db_deadlock.HISTORY_LENGTH
    db_deadlock._forget(conn)
    assert db_deadlock._HISTORY_KEY not in conn.info


def test_the_email_escapes_what_it_quotes():
    html = email_templates.build_deadlock_alert_email_html(
        occurred_at=datetime(2031, 3, 10, 9, 0, tzinfo=timezone.utc),
        location=["app/x.py:1 in f"],
        call_session_id=None,
        host_id=None,
        request_id=None,
        message="deadlock detected",
        pg_detail=None,
        failing=("UPDATE t SET a=$1", "('<script>alert(1)</script>',)"),
        lost_writes=[],
        held_back=0,
        window_minutes=10,
        admin_url="https://example.com/admin",
    )
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "None recorded earlier" in html


def test_the_listener_is_installed_once_on_the_shared_engine():
    assert id(engine.sync_engine) in db_deadlock._installed
    before = len(db_deadlock._installed)
    db_deadlock.install(engine)
    assert len(db_deadlock._installed) == before
