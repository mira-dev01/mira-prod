"""TEST-ONLY stand-ins for the payment provider (Razorpay) and WhatsApp (Twilio).

These live in tests/, never in app/: the product has no simulation mode. A test that wants the real hold /
poll / confirm flow to run without real money or real guests imports `fake_providers_on` (an autouse fixture)
and drives the fakes through this module:

    from tests import fake_providers as sim
    from tests.fake_providers import fake_providers_on  # noqa: F401  (autouse fixture)

    await sim.set_outcome(link_id, "paid")       # what the guest does on the pay page
    await sim.set_down("payment", True)          # the provider is unreachable
    sim.messages()                               # every WhatsApp message "sent", oldest first

State is per test: the fixture resets it and restores the real functions afterwards.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field

import pytest
from cryptography.fernet import Fernet

from app.config import settings
from app.integrations import razorpay_client, twilio_client
from app.integrations.razorpay_client import MIN_AMOUNT_PAISE, PaymentLink, RazorpayError, to_paise
from app.services import billing_service, calendar_service
from app.utils import secrets as secrets_util

TARGETS = ("payment", "messaging", "calendar")
OUTCOMES = ("paid", "failed", "cancelled", "expired", "reset")
TEST_KEY_ID = "rzp_test_fakeprovider01"


class SimulatedOutage(Exception):
    """A fake service is switched down. Raised where the real one would fail."""


@dataclass
class _Link:
    id: str
    amount_paise: int
    description: str
    status: str = "created"
    last_attempt: str | None = None
    payment_id: str | None = None


@dataclass
class Message:
    to_phone: str
    body: str
    status: str = "sent"
    kind: str = "text"
    content_sid: str | None = None
    variables: dict = field(default_factory=dict)


_links: dict[str, _Link] = {}
_messages: list[Message] = []
_down: dict[str, bool] = {t: False for t in TARGETS}


def reset() -> None:
    _links.clear()
    _messages.clear()
    for target in TARGETS:
        _down[target] = False


def messages() -> list[Message]:
    return list(_messages)


def message_bodies() -> list[str]:
    return [m.body for m in _messages]


def billing_config() -> dict:
    """A host's saved Razorpay connection that works with the fake provider (needs the fixture below)."""
    return {
        "razorpay": {
            "key_id": TEST_KEY_ID,
            "secret_enc": secrets_util.encrypt("fake-secret"),
            "mode": "test",
            "verified_at": "2026-11-10T00:00:00+00:00",
        }
    }


async def set_down(target: str, down: bool) -> None:
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r}")
    _down[target] = down


def _view(row: _Link) -> PaymentLink:
    return PaymentLink(
        id=row.id,
        short_url=f"https://rzp.test/pay/{row.id}",
        status=row.status,
        payment_id=row.payment_id,
        amount_paise=row.amount_paise,
        last_attempt=row.last_attempt,
    )


async def set_outcome(link_id: str, outcome: str) -> PaymentLink:
    """What the guest does on the pay page (or a tester does for them)."""
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}")
    row = _links.get(link_id)
    if row is None:
        raise RazorpayError("We couldn't find this booking.", status_code=404)
    if outcome == "paid":
        if row.status == "paid":
            raise RazorpayError("This booking is already paid.")
        if row.status in ("cancelled", "expired"):
            raise RazorpayError("This reservation was cancelled." if row.status == "cancelled" else "This link has expired.")
        row.status, row.last_attempt, row.payment_id = "paid", None, f"pay_{secrets.token_hex(6)}"
    elif outcome == "failed":
        if row.status == "created":
            row.last_attempt = "failed"
    elif outcome in ("cancelled", "expired"):
        if row.status == "created":
            row.status = outcome
    elif outcome == "reset" and row.status != "paid":
        row.status, row.last_attempt = "created", None
    return _view(row)


# -- what replaces razorpay_client / twilio_client -------------------------------------------------------


async def _verify_credentials(key_id, key_secret) -> None:
    if _down["payment"]:
        raise RazorpayError("Online payment is temporarily unavailable.")


async def _create_payment_link(key_id, key_secret, *, amount_rupees, description, **_ignored) -> PaymentLink:
    if _down["payment"]:
        raise RazorpayError("Online payment is temporarily unavailable.")
    paise = to_paise(amount_rupees)
    if paise < MIN_AMOUNT_PAISE:
        raise RazorpayError("That amount is too small to collect online.")
    row = _Link(id=f"plink_{uuid.uuid4().hex[:16]}", amount_paise=paise, description=description[:500])
    _links[row.id] = row
    return _view(row)


async def _fetch_payment_link(key_id, key_secret, link_id) -> PaymentLink:
    if _down["payment"]:
        raise RazorpayError("Online payment is temporarily unavailable.")
    row = _links.get(link_id)
    if row is None:
        raise RazorpayError("We couldn't find this booking.", status_code=404)
    return _view(row)


async def _cancel_payment_link(key_id, key_secret, link_id) -> PaymentLink:
    row = _links.get(link_id)
    if row is None:
        raise RazorpayError("We couldn't find this booking.", status_code=404)
    if row.status == "created":
        row.status = "cancelled"
    return _view(row)


def _record(to_phone: str, body: str, kind: str, **extra) -> str:
    down = _down["messaging"]
    message = Message(to_phone=to_phone, body=body, status="failed" if down else "sent", kind=kind, **extra)
    _messages.append(message)
    if down:
        raise twilio_client.TwilioError("WhatsApp is switched down for this test")
    return f"SM{uuid.uuid4().hex[:30]}"


async def _send_whatsapp_message(to_phone: str, body: str, timeout: float = 15.0) -> dict:
    return {"status": "sent", "sid": _record(to_phone, body, "text"), "twilio_status": "delivered"}


async def _send_whatsapp_template(to_phone: str, content_sid: str, content_variables: dict, timeout: float = 15.0) -> dict:
    sid = _record(
        to_phone, f"[template {content_sid}] {content_variables}", "template", content_sid=content_sid, variables=dict(content_variables)
    )
    return {"status": "sent", "sid": sid, "twilio_status": "delivered"}


async def _fetch_message_status(message_sid: str, timeout: float = 10.0):
    return "delivered", None


@pytest.fixture(autouse=True)
def fake_providers_on(monkeypatch):
    """Replaces the payment provider and WhatsApp with in-memory fakes for the test."""
    reset()
    monkeypatch.setattr(settings, "payment_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(razorpay_client, "verify_credentials", _verify_credentials)
    monkeypatch.setattr(razorpay_client, "create_payment_link", _create_payment_link)
    monkeypatch.setattr(razorpay_client, "fetch_payment_link", _fetch_payment_link)
    monkeypatch.setattr(razorpay_client, "cancel_payment_link", _cancel_payment_link)
    monkeypatch.setattr(twilio_client, "send_whatsapp_message", _send_whatsapp_message)
    monkeypatch.setattr(twilio_client, "send_whatsapp_template", _send_whatsapp_template)
    monkeypatch.setattr(twilio_client, "fetch_message_status", _fetch_message_status)

    # The calendar is the host's own data, not a third party: "down" here means the availability lookup raises,
    # which is how a database or iCal-feed failure would reach the tool.
    def _guard_calendar(name: str) -> None:
        real = getattr(calendar_service, name)

        async def guarded(*args, **kwargs):
            if _down["calendar"]:
                raise SimulatedOutage("the availability service is switched down for this test")
            return await real(*args, **kwargs)

        monkeypatch.setattr(calendar_service, name, guarded)

    _guard_calendar("scope_availability")
    _guard_calendar("is_available")
    yield
    reset()


__all__ = ["billing_service", "fake_providers_on", "SimulatedOutage"]
