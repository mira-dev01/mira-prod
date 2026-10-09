"""Covers the internal /admin panel's email one-time-code login
(app/api/v1/admin_auth.py, app/auth/admin.py)."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from sqlalchemy import select

from app.auth.admin import ADMIN_AUDIENCE
from app.config import settings
from app.integrations import email_client
from app.models.admin import AdminLoginCode

ADMIN = "abhayatrivedi2005@gmail.com"
SECRET = "test-admin-secret-0123456789abcdef"


@pytest.fixture(autouse=True)
def _admin_config(monkeypatch):
    monkeypatch.setattr(settings, "admin_jwt_secret", SECRET)
    monkeypatch.setattr(settings, "admin_emails", f"{ADMIN},shagunverma.2004@gmail.com")


@pytest.fixture
def sent(monkeypatch):
    outbox = []

    async def _fake_send(to, subject, body, html_body=None, timeout=None):
        outbox.append({"to": to, "subject": subject, "body": body})
        return {"status": "sent"}

    monkeypatch.setattr(email_client, "send_email", _fake_send)
    return outbox


def _code(outbox) -> str:
    return outbox[-1]["subject"].rsplit(" ", 1)[-1]


async def _login(client, sent) -> str:
    resp = await client.post("/api/v1/admin/auth/request-code", json={"email": ADMIN})
    assert resp.status_code == 200
    resp = await client.post("/api/v1/admin/auth/verify-code", json={"email": ADMIN, "code": _code(sent)})
    assert resp.status_code == 200
    return resp.json()["token"]


async def test_full_login_flow_issues_working_admin_token(client, sent):
    token = await _login(client, sent)

    assert sent[0]["to"] == ADMIN
    me = await client.get("/api/v1/admin/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json() == {"email": ADMIN}


async def test_email_is_case_insensitive(client, sent):
    resp = await client.post("/api/v1/admin/auth/request-code", json={"email": "  AbhayaTrivedi2005@Gmail.com "})
    assert resp.status_code == 200
    assert sent[0]["to"] == ADMIN


async def test_non_admin_email_gets_generic_response_and_no_code(client, sent, db_session):
    resp = await client.post("/api/v1/admin/auth/request-code", json={"email": "someone@example.com"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"
    assert sent == []
    assert (await db_session.scalars(select(AdminLoginCode))).all() == []


async def test_code_is_single_use(client, sent):
    await client.post("/api/v1/admin/auth/request-code", json={"email": ADMIN})
    code = _code(sent)
    first = await client.post("/api/v1/admin/auth/verify-code", json={"email": ADMIN, "code": code})
    second = await client.post("/api/v1/admin/auth/verify-code", json={"email": ADMIN, "code": code})

    assert first.status_code == 200
    assert second.status_code == 401


async def test_wrong_code_locks_after_max_attempts(client, sent):
    await client.post("/api/v1/admin/auth/request-code", json={"email": ADMIN})
    code = _code(sent)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        resp = await client.post("/api/v1/admin/auth/verify-code", json={"email": ADMIN, "code": wrong})
        assert resp.status_code == 401
    # The real code no longer works once the attempt limit is hit.
    resp = await client.post("/api/v1/admin/auth/verify-code", json={"email": ADMIN, "code": code})
    assert resp.status_code == 401


async def test_new_code_retires_the_previous_one(client, sent):
    await client.post("/api/v1/admin/auth/request-code", json={"email": ADMIN})
    old = _code(sent)
    await client.post("/api/v1/admin/auth/request-code", json={"email": ADMIN})
    new = _code(sent)

    if old != new:
        assert (await client.post("/api/v1/admin/auth/verify-code", json={"email": ADMIN, "code": old})).status_code == 401
    assert (await client.post("/api/v1/admin/auth/verify-code", json={"email": ADMIN, "code": new})).status_code == 200


async def test_expired_code_is_rejected(client, sent, db_session):
    await client.post("/api/v1/admin/auth/request-code", json={"email": ADMIN})
    row = (await db_session.scalars(select(AdminLoginCode))).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    resp = await client.post("/api/v1/admin/auth/verify-code", json={"email": ADMIN, "code": _code(sent)})
    assert resp.status_code == 401


async def test_request_rate_limited(client, sent):
    for _ in range(5):
        assert (await client.post("/api/v1/admin/auth/request-code", json={"email": ADMIN})).status_code == 200
    resp = await client.post("/api/v1/admin/auth/request-code", json={"email": ADMIN})
    assert resp.status_code == 429


async def test_email_not_configured_returns_503(client, monkeypatch):
    async def _skipped(**kwargs):
        return {"status": "skipped", "reason": "Resend is not configured"}

    monkeypatch.setattr(email_client, "send_email", _skipped)
    resp = await client.post("/api/v1/admin/auth/request-code", json={"email": ADMIN})
    assert resp.status_code == 503


async def test_admin_login_disabled_without_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_jwt_secret", None)

    assert (await client.post("/api/v1/admin/auth/request-code", json={"email": ADMIN})).status_code == 503
    assert (await client.get("/api/v1/admin/overview")).status_code == 404


async def test_admin_data_routes_require_admin_token(client, sent):
    assert (await client.get("/api/v1/admin/overview")).status_code == 401
    bad = jwt.encode({"sub": ADMIN, "aud": ADMIN_AUDIENCE}, "wrong-secret", algorithm="HS256")
    assert (await client.get("/api/v1/admin/overview", headers={"Authorization": f"Bearer {bad}"})).status_code == 401
    # A host's Clerk-style bearer (here: a raw UUID) is not an admin session.
    host = await client.get("/api/v1/admin/overview", headers={"Authorization": "Bearer 00000000-0000-0000-0000-000000000000"})
    assert host.status_code == 401


async def test_removing_email_from_allowlist_revokes_existing_token(client, sent, monkeypatch):
    token = await _login(client, sent)
    monkeypatch.setattr(settings, "admin_emails", "shagunverma.2004@gmail.com")

    resp = await client.get("/api/v1/admin/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


async def test_admin_token_cannot_be_forged_for_non_admin(client):
    forged = jwt.encode(
        {"sub": "someone@example.com", "aud": ADMIN_AUDIENCE, "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        SECRET,
        algorithm="HS256",
    )
    resp = await client.get("/api/v1/admin/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 403
