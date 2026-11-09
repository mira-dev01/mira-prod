"""Access-control hardening: Clerk session-token verification, account
linking, notification ownership, speech-to-text upload limits, placeholder
secrets, and the voice test page's reflected-XSS fix."""

import base64
import hashlib
import hmac
import json
import time
import uuid

import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import Response

from app.auth import dependencies as deps
from app.config import Settings, is_placeholder_secret, settings
from app.integrations import clerk_client
from app.models.user import User
from app.services import notification_service
from app.utils import rate_limit, webhook_auth
from tests.conftest import auth_headers_for

ISSUER = "https://clerk.example.test"


@pytest.fixture(scope="module")
def keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def clerk(monkeypatch, keypair):
    """Points the verifier at a local keypair instead of Clerk's JWKS."""

    class _Key:
        key = keypair.public_key()

    class _Client:
        def get_signing_key_from_jwt(self, token):
            return _Key()

    monkeypatch.setattr(deps, "_jwk_client", lambda: _Client())
    monkeypatch.setattr(deps, "_clerk_issuer", lambda: ISSUER)
    monkeypatch.setattr(settings, "frontend_base_url", "https://app.example.test")
    monkeypatch.setattr(settings, "cors_extra_origins", "")
    return keypair


def _token(key, **overrides):
    now = int(time.time())
    claims = {"sub": "user_1", "iss": ISSUER, "iat": now, "exp": now + 60, "azp": "https://app.example.test"}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256")


def test_valid_token_is_accepted(clerk):
    assert deps._verify_session_token(_token(clerk))["sub"] == "user_1"


def test_token_without_azp_is_accepted(clerk):
    assert deps._verify_session_token(_token(clerk, azp=None))["sub"] == "user_1"


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://clerk.other-instance.test"},  # another Clerk instance
        {"azp": "https://evil.example.test"},  # minted for a different site
        {"exp": None},  # never expires
        {"iat": None},
        {"sub": None},
        {"exp": int(time.time()) - 3600},  # expired
        {"nbf": int(time.time()) + 3600},  # not valid yet
    ],
)
def test_bad_claims_are_rejected(clerk, overrides):
    with pytest.raises(jwt.PyJWTError):
        deps._verify_session_token(_token(clerk, **overrides))


def test_hs256_token_signed_with_public_key_is_rejected(clerk):
    """Classic algorithm-confusion attack: HS256 keyed by the RSA public key."""
    from cryptography.hazmat.primitives import serialization

    public_pem = clerk.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    now = int(time.time())

    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    # Hand-built: PyJWT itself refuses to HMAC-sign with a PEM key, which is
    # exactly why an attacker would craft the token without the library.
    signing_input = (
        b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        + b"."
        + b64(json.dumps({"sub": "user_1", "iss": ISSUER, "iat": now, "exp": now + 60}).encode())
    )
    signature = hmac.new(public_pem, signing_input, hashlib.sha256).digest()
    forged = (signing_input + b"." + b64(signature)).decode()
    with pytest.raises(jwt.PyJWTError):
        deps._verify_session_token(forged)


def test_alg_none_is_rejected(clerk):
    now = int(time.time())
    forged = jwt.encode({"sub": "user_1", "iss": ISSUER, "iat": now, "exp": now + 60}, None, algorithm="none")
    with pytest.raises(jwt.PyJWTError):
        deps._verify_session_token(forged)


def test_issuer_is_derived_from_publishable_key(monkeypatch):
    key = "pk_test_" + base64.b64encode(b"clerk.example.test$").decode()
    monkeypatch.setattr(settings, "clerk_publishable_key", key)
    assert deps._clerk_issuer() == "https://clerk.example.test"
    assert deps._clerk_jwks_url() == "https://clerk.example.test/.well-known/jwks.json"


# ---- account linking ------------------------------------------------------


async def test_unlinked_legacy_user_is_claimed_by_verified_email(db_session, monkeypatch):
    email = f"legacy-{uuid.uuid4().hex[:8]}@example.com"
    legacy = User(email=email)
    db_session.add(legacy)
    await db_session.commit()

    async def _email(_):
        return email

    monkeypatch.setattr(deps, "fetch_user_email", _email)
    resolved = await deps._resolve_local_user(db_session, "user_new")
    assert resolved.id == legacy.id
    assert resolved.clerk_user_id == "user_new"


async def test_user_already_bound_to_another_clerk_identity_is_never_relinked(db_session, monkeypatch):
    email = f"bound-{uuid.uuid4().hex[:8]}@example.com"
    owner = User(email=email, clerk_user_id="user_owner")
    db_session.add(owner)
    await db_session.commit()

    async def _email(_):
        return email

    monkeypatch.setattr(deps, "fetch_user_email", _email)
    assert await deps._resolve_local_user(db_session, "user_attacker") is None
    await db_session.refresh(owner)
    assert owner.clerk_user_id == "user_owner"


@respx.mock
async def test_unverified_email_is_never_returned(monkeypatch):
    monkeypatch.setattr(settings, "clerk_secret_key", "sk_test_x")
    respx.get("https://api.clerk.com/v1/users/user_1").mock(
        return_value=Response(
            200,
            json={
                "primary_email_address_id": "idn_1",
                "email_addresses": [
                    {"id": "idn_1", "email_address": "victim@example.com", "verification": {"status": "unverified"}}
                ],
            },
        )
    )
    assert await clerk_client.fetch_user_email("user_1") is None


@respx.mock
async def test_verified_primary_email_is_returned(monkeypatch):
    monkeypatch.setattr(settings, "clerk_secret_key", "sk_test_x")
    respx.get("https://api.clerk.com/v1/users/user_1").mock(
        return_value=Response(
            200,
            json={
                "primary_email_address_id": "idn_2",
                "email_addresses": [
                    {"id": "idn_1", "email_address": "other@example.com", "verification": {"status": "verified"}},
                    {"id": "idn_2", "email_address": "me@example.com", "verification": {"status": "verified"}},
                ],
            },
        )
    )
    assert await clerk_client.fetch_user_email("user_1") == "me@example.com"


# ---- notification ownership -----------------------------------------------


async def test_mark_read_requires_authentication(client, db_session, test_property):
    n = await notification_service.create_notification(db_session, "system", "hi", property_id=test_property.id)
    res = await client.post(f"/api/v1/notifications/{n.id}/read")
    assert res.status_code == 401


async def test_owner_can_mark_own_notification_read(client, db_session, test_user, test_property):
    n = await notification_service.create_notification(db_session, "system", "hi", property_id=test_property.id)
    res = await client.post(f"/api/v1/notifications/{n.id}/read", headers=auth_headers_for(test_user))
    assert res.status_code == 200
    assert res.json()["status"] == "read"


async def test_other_host_gets_404_and_notification_is_untouched(client, db_session, test_property):
    other = User(email=f"other-{uuid.uuid4().hex[:8]}@example.com", clerk_user_id=f"user_{uuid.uuid4().hex[:12]}")
    db_session.add(other)
    await db_session.commit()
    n = await notification_service.create_notification(db_session, "system", "secret", property_id=test_property.id)

    res = await client.post(f"/api/v1/notifications/{n.id}/read", headers=auth_headers_for(other))
    assert res.status_code == 404
    assert "secret" not in res.text
    await db_session.refresh(n)
    assert n.status != "read"


# ---- speech-to-text uploads -----------------------------------------------


def _audio(content=b"x" * 100, content_type="audio/webm"):
    return {"audio": ("clip.webm", content, content_type)}


async def test_transcribe_intro_requires_authentication(client):
    res = await client.post("/api/v1/auth/register-host/transcribe-intro", files=_audio())
    assert res.status_code == 401


async def test_transcribe_rejects_oversized_audio(client, test_user):
    rate_limit._transcribe_limiter._hits.clear()
    big = b"x" * (8 * 1024 * 1024 + 1)
    res = await client.post(
        "/api/v1/auth/register-host/transcribe-intro", files=_audio(big), headers=auth_headers_for(test_user)
    )
    assert res.status_code == 413


async def test_transcribe_rejects_non_audio_content_type(client, test_user):
    rate_limit._transcribe_limiter._hits.clear()
    res = await client.post(
        "/api/v1/auth/register-host/transcribe-intro",
        files=_audio(content_type="application/x-msdownload"),
        headers=auth_headers_for(test_user),
    )
    assert res.status_code == 415


async def test_transcribe_is_rate_limited_per_user(client, test_user):
    rate_limit._transcribe_limiter._hits.clear()
    codes = [
        (
            await client.post(
                "/api/v1/auth/register-host/transcribe-intro",
                files=_audio(content_type="application/x-msdownload"),
                headers=auth_headers_for(test_user),
            )
        ).status_code
        for _ in range(14)
    ]
    assert codes.count(415) == 12
    assert codes[-2:] == [429, 429]
    rate_limit._transcribe_limiter._hits.clear()


def test_sliding_window_limiter_expires_old_hits():
    now = [0.0]
    limiter = rate_limit.SlidingWindowLimiter(limit=2, window_seconds=10, clock=lambda: now[0])
    assert limiter.allow("a") and limiter.allow("a")
    assert not limiter.allow("a")
    assert limiter.allow("b")  # independent key
    now[0] = 10.0
    assert limiter.allow("a")


# ---- placeholder secrets ---------------------------------------------------


def test_placeholder_detection():
    assert is_placeholder_secret(None) and is_placeholder_secret("") and is_placeholder_secret("  ")
    assert is_placeholder_secret("change-me") and is_placeholder_secret("Change-Me")
    assert not is_placeholder_secret("a-real-secret")


def test_production_refuses_placeholder_secrets():
    with pytest.raises(ValueError, match="TAKE_CALL_TOKEN_SECRET"):
        Settings(environment="production", take_call_token_secret="change-me", exotel_webhook_token="x" * 32)
    with pytest.raises(ValueError, match="EXOTEL_WEBHOOK_TOKEN"):
        Settings(environment="production", take_call_token_secret="x" * 32, exotel_webhook_token="change-me")
    with pytest.raises(ValueError, match="shorter than 32"):
        Settings(environment="production", take_call_token_secret="short", exotel_webhook_token="x" * 32)


def test_production_accepts_real_secrets_and_dev_accepts_defaults():
    Settings(environment="production", take_call_token_secret="x" * 32, exotel_webhook_token="y" * 32)
    Settings(environment="development", take_call_token_secret="change-me", exotel_webhook_token="change-me")


def test_webhook_token_fails_closed_in_production_on_placeholder(monkeypatch):
    assert webhook_auth.verify_shared_secret_token("change-me", "change-me") is True  # dev/test convenience
    monkeypatch.setattr(settings, "environment", "production")
    assert webhook_auth.verify_shared_secret_token("change-me", "change-me") is False
    assert webhook_auth.verify_shared_secret_token("real", "real") is True
    assert webhook_auth.verify_shared_secret_token("wrong", "real") is False
    assert webhook_auth.verify_shared_secret_token("héllo", "real") is False  # non-ASCII must not raise


# ---- voice test page: reflected XSS ---------------------------------------


async def test_test_page_escapes_token(client):
    payload = '";alert(1);//</script><script>alert(2)</script>'
    res = await client.get("/api/v1/voice/test", params={"token": payload})
    assert res.status_code == 200
    assert "</script><script>alert(2)" not in res.text
    assert res.headers["referrer-policy"] == "no-referrer"
    assert res.headers["cache-control"] == "no-store"


async def test_test_page_token_cannot_reinject_server_placeholders(client):
    res = await client.get("/api/v1/voice/test", params={"token": "__ICE_SERVERS__;alert(1)//"})
    body = res.text
    assert 'const token = "__ICE_SERVERS__;alert(1)//";' in body


async def test_test_page_rejects_non_uuid_property_id(client):
    res = await client.get("/api/v1/voice/test", params={"token": "t", "property_id": '";alert(1)//'})
    assert res.status_code == 400
    ok = await client.get("/api/v1/voice/test", params={"token": "t", "property_id": str(uuid.uuid4())})
    assert ok.status_code == 200
