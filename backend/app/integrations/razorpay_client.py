"""Razorpay Payment Links, called with the HOST's own API keys.

Money goes straight from the guest to the host's Razorpay account; the platform
never holds it and has no Razorpay credentials of its own.

Health: api.razorpay.com is watched by the passive hook (observability/
http_hook.py) like every other external service, with one difference. These are
per-host credentials, so a 401 or other 4xx says the HOST's key or request is
wrong, not that Razorpay is down; the hook counts only 5xx, timeouts and
connection errors against it. Bad-key failures are raised as RazorpayError
(safe to show the host) and handled by the caller.

Amounts: the API takes integer paise; callers pass rupees (Decimal/float) and
this module converts, so no float arithmetic on money happens elsewhere.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.razorpay.com/v1"
TIMEOUT_SECONDS = 20.0
# Razorpay refuses a link that expires sooner than this.
MIN_LINK_LIFETIME_SECONDS = 15 * 60 + 60  # 15 min + a minute of clock slack
MIN_AMOUNT_PAISE = 100


class RazorpayError(Exception):
    """Safe to show the host. `bad_credentials` means the keys were refused."""

    def __init__(self, message: str, *, bad_credentials: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.bad_credentials = bad_credentials
        self.status_code = status_code


@dataclass(frozen=True)
class PaymentLink:
    id: str
    short_url: str | None
    status: str  # created | partially_paid | paid | expired | cancelled
    payment_id: str | None  # the captured payment, once there is one
    amount_paise: int | None


def to_paise(rupees) -> int:
    paise = (Decimal(str(rupees)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(paise)


def mode_of(key_id: str) -> str:
    return "test" if key_id.startswith("rzp_test_") else "live"


def _describe(response: httpx.Response) -> str:
    try:
        return (response.json().get("error") or {}).get("description") or response.reason_phrase
    except Exception:
        return response.reason_phrase or "request failed"


async def _call(method: str, path: str, key_id: str, key_secret: str, *, json: dict | None = None, params: dict | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.request(method, f"{BASE_URL}{path}", auth=(key_id, key_secret), json=json, params=params)
    except httpx.HTTPError as exc:
        logger.warning("razorpay %s %s failed: %s", method, path, type(exc).__name__)
        raise RazorpayError("Couldn't reach Razorpay. Please try again in a moment.") from exc
    if response.status_code in (401, 403):
        raise RazorpayError("Razorpay didn't accept these keys. Check the Key ID and Key Secret.", bad_credentials=True, status_code=response.status_code)
    if response.status_code >= 400:
        raise RazorpayError(f"Razorpay said: {_describe(response)}", status_code=response.status_code)
    return response.json()


def _link(data: dict) -> PaymentLink:
    payments = data.get("payments") or []
    captured = next((p.get("payment_id") for p in payments if isinstance(p, dict) and p.get("status") in (None, "captured")), None)
    return PaymentLink(
        id=data["id"],
        short_url=data.get("short_url"),
        status=str(data.get("status") or "created"),
        payment_id=captured,
        amount_paise=data.get("amount"),
    )


async def verify_credentials(key_id: str, key_secret: str) -> None:
    """Raises RazorpayError unless the keys are accepted (a cheap read-only call)."""
    await _call("GET", "/payment_links", key_id, key_secret, params={"count": 1})


async def create_payment_link(
    key_id: str,
    key_secret: str,
    *,
    amount_rupees,
    reference_id: str,
    description: str,
    expire_by: int,
    customer_name: str | None = None,
    customer_contact: str | None = None,
    notes: dict | None = None,
) -> PaymentLink:
    paise = to_paise(amount_rupees)
    if paise < MIN_AMOUNT_PAISE:
        raise RazorpayError("That amount is too small to collect online.")
    body: dict = {
        "amount": paise,
        "currency": "INR",
        "accept_partial": False,
        "reference_id": reference_id[:40],
        "description": description[:2000],
        "expire_by": int(expire_by),
        # Mira sends the link on WhatsApp herself; Razorpay must not text/email
        # the guest a second copy.
        "notify": {"sms": False, "email": False},
        "reminder_enable": False,
        "notes": {k: str(v)[:250] for k, v in (notes or {}).items()},
    }
    customer = {k: v for k, v in (("name", customer_name), ("contact", customer_contact)) if v}
    if customer:
        body["customer"] = customer
    return _link(await _call("POST", "/payment_links", key_id, key_secret, json=body))


async def fetch_payment_link(key_id: str, key_secret: str, link_id: str) -> PaymentLink:
    return _link(await _call("GET", f"/payment_links/{link_id}", key_id, key_secret))


async def cancel_payment_link(key_id: str, key_secret: str, link_id: str) -> PaymentLink:
    """Only a link that is still unpaid can be cancelled; callers treat a
    refusal as 'already paid or expired' and re-fetch."""
    return _link(await _call("POST", f"/payment_links/{link_id}/cancel", key_id, key_secret))
