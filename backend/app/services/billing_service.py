"""A host's payment setup: Razorpay keys, the wording of the messages guests get,
and whether the host is told when someone pays (Settings -> Billing).

Stored in users.billing_config (JSONB):
  {"razorpay": {"key_id", "secret_enc", "mode", "verified_at"},
   "templates": {"payment_request": {"approved", "draft"}, "payment_success": {...}},
   "notify_host_on_payment": bool}

Rules this module keeps:
- The Razorpay secret is encrypted before it is stored and is never returned by
  any function here other than `get_credentials`, whose only callers talk to
  Razorpay. Keys are verified against Razorpay before they are saved.
- Message wording follows edit -> approve: an edit is a draft and changes
  nothing guests receive until the host approves it. Until the host writes
  their own, a built-in default is used.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.integrations import razorpay_client
from app.integrations.razorpay_client import RazorpayError
from app.models.payment import PaymentRequest
from app.models.user import User
from app.utils import secrets
from app.utils.secrets import SecretsError

KEY_ID_RE = re.compile(r"^rzp_(live|test)_[A-Za-z0-9]{6,40}$")
TEMPLATE_MAX = 900  # WhatsApp allows 1024; leave room for the link and name

# What a host can put in a message, and what each is replaced with.
PLACEHOLDERS: dict[str, str] = {
    "guest_name": "The guest's first name",
    "property": "The property's name",
    "check_in": "Check-in date",
    "check_out": "Check-out date",
    "amount": "What this link charges, e.g. ₹12,000",
    "total": "The full stay price",
    "balance_note": "For split payments: how much is left and when it's due (empty otherwise)",
    "link": "The payment link",
    "hold_minutes": "How long the dates are held",
    "host_name": "Your name or business name",
}

DEFAULT_TEMPLATES: dict[str, str] = {
    "payment_request": (
        "Hi {guest_name}! To confirm your stay at {property} ({check_in} to {check_out}), "
        "please pay {amount} here: {link}\n"
        "We're holding these dates for you for {hold_minutes} minutes."
    ),
    "payment_success": (
        "Thank you {guest_name}! We've received your payment of {amount} for {property} "
        "({check_in} to {check_out}). {balance_note}"
    ),
}
TEMPLATE_LABELS = {"payment_request": "Payment request", "payment_success": "Payment received"}


class BillingError(Exception):
    """Safe to show the host."""


# -- reading ---------------------------------------------------------------


def _config(user: User) -> dict:
    return dict(user.billing_config or {})


def _razorpay(user: User) -> dict | None:
    stored = _config(user).get("razorpay")
    return stored if isinstance(stored, dict) and stored.get("key_id") and stored.get("secret_enc") else None


def is_connected(user: User) -> bool:
    return _razorpay(user) is not None


def get_credentials(user: User) -> tuple[str, str] | None:
    """(key_id, key_secret) in the clear, for calling Razorpay only."""
    stored = _razorpay(user)
    if stored is None:
        return None
    return stored["key_id"], secrets.decrypt(stored["secret_enc"])


def notify_host_on_payment(user: User) -> bool:
    return bool(_config(user).get("notify_host_on_payment", True))


def _template_state(user: User, name: str) -> dict:
    stored = (_config(user).get("templates") or {}).get(name) or {}
    approved = stored.get("approved")
    return {"approved": approved, "draft": stored.get("draft")}


def template_text(user: User, name: str) -> str:
    """The wording guests receive: the host's approved text, else the default."""
    return _template_state(user, name)["approved"] or DEFAULT_TEMPLATES[name]


def public_view(user: User) -> dict:
    """Everything the Billing tab shows. Never contains the secret."""
    stored = _razorpay(user)
    templates = {}
    for name, label in TEMPLATE_LABELS.items():
        state = _template_state(user, name)
        templates[name] = {
            "label": label,
            "text": state["approved"] or DEFAULT_TEMPLATES[name],
            "is_default": state["approved"] is None,
            "draft": state["draft"],
        }
    return {
        "encryption_ready": secrets.is_configured(),
        "razorpay": {
            "connected": stored is not None,
            "key_id": stored["key_id"] if stored else None,
            "mode": stored.get("mode") if stored else None,
            "verified_at": stored.get("verified_at") if stored else None,
        },
        "templates": templates,
        "defaults": dict(DEFAULT_TEMPLATES),
        "placeholders": [{"name": k, "help": v} for k, v in PLACEHOLDERS.items()],
        "notify_host_on_payment": notify_host_on_payment(user),
    }


# -- writing ---------------------------------------------------------------


async def _save(db: AsyncSession, user: User, config: dict) -> None:
    user.billing_config = config  # a new dict: JSONB change detection needs a reassignment
    await db.commit()


async def connect_razorpay(db: AsyncSession, user: User, key_id: str, key_secret: str) -> None:
    key_id, key_secret = (key_id or "").strip(), (key_secret or "").strip()
    if not KEY_ID_RE.match(key_id):
        raise BillingError("That doesn't look like a Razorpay Key ID. It starts with rzp_live_ or rzp_test_.")
    if len(key_secret) < 10:
        raise BillingError("Enter your Razorpay Key Secret.")
    stored = _razorpay(user)
    if stored is not None and stored["key_id"] != key_id:
        # Links already sent live in the OLD account; polling them with the new
        # keys would never see a payment, and the hold could not be cancelled.
        open_links = await db.scalar(
            select(func.count()).select_from(PaymentRequest).where(PaymentRequest.user_id == user.id, PaymentRequest.status == "active")
        )
        if open_links:
            raise BillingError(
                f"{open_links} payment link{'s are' if open_links != 1 else ' is'} still open under your current Razorpay account. "
                "Switch accounts once they are paid or have expired."
            )
    try:
        encrypted = secrets.encrypt(key_secret)  # fails first if the server can't store it safely
        await razorpay_client.verify_credentials(key_id, key_secret)
    except SecretsError as exc:
        raise BillingError(str(exc))
    except RazorpayError as exc:
        raise BillingError(str(exc))
    config = _config(user)
    config["razorpay"] = {
        "key_id": key_id,
        "secret_enc": encrypted,
        "mode": razorpay_client.mode_of(key_id),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    await _save(db, user, config)


async def disconnect_razorpay(db: AsyncSession, user: User) -> None:
    open_links = await db.scalar(
        select(func.count()).select_from(PaymentRequest).where(PaymentRequest.user_id == user.id, PaymentRequest.status == "active")
    )
    if open_links:
        raise BillingError(
            f"{open_links} payment link{'s are' if open_links != 1 else ' is'} still open. "
            "Disconnect once they are paid or have expired."
        )
    config = _config(user)
    config.pop("razorpay", None)
    await _save(db, user, config)


def _check_template(name: str, text: str) -> str:
    if name not in DEFAULT_TEMPLATES:
        raise BillingError("Unknown message.")
    text = (text or "").strip()
    if not text:
        raise BillingError("Write the message first.")
    if len(text) > TEMPLATE_MAX:
        raise BillingError(f"Keep the message under {TEMPLATE_MAX} characters.")
    tokens = re.findall(r"\{([^{}]*)\}", text)
    unknown = sorted({t for t in tokens if t not in PLACEHOLDERS})
    if unknown:
        raise BillingError("This message contains " + ", ".join("{" + u + "}" for u in unknown) + ", which isn't something Mira can fill in.")
    if re.search(r"[{}]", re.sub(r"\{[^{}]*\}", "", text)):
        raise BillingError("This message has a stray { or }. Use them only around the names in the list, like {guest_name}.")
    if name == "payment_request" and "{link}" not in text:
        raise BillingError("The payment message needs {link} so the guest can pay.")
    return text


async def save_template_draft(db: AsyncSession, user: User, name: str, text: str) -> None:
    text = _check_template(name, text)
    config = _config(user)
    templates = {k: dict(v) for k, v in (config.get("templates") or {}).items()}
    entry = templates.get(name, {})
    entry["draft"] = None if text == (entry.get("approved") or DEFAULT_TEMPLATES[name]) else text
    templates[name] = entry
    config["templates"] = templates
    await _save(db, user, config)


async def approve_template(db: AsyncSession, user: User, name: str) -> None:
    if name not in DEFAULT_TEMPLATES:
        raise BillingError("Unknown message.")
    config = _config(user)
    templates = {k: dict(v) for k, v in (config.get("templates") or {}).items()}
    entry = templates.get(name, {})
    draft = entry.get("draft")
    if not draft:
        raise BillingError("There's nothing waiting to approve.")
    templates[name] = {"approved": _check_template(name, draft), "draft": None}
    config["templates"] = templates
    await _save(db, user, config)


async def reset_template(db: AsyncSession, user: User, name: str) -> None:
    if name not in DEFAULT_TEMPLATES:
        raise BillingError("Unknown message.")
    config = _config(user)
    templates = {k: dict(v) for k, v in (config.get("templates") or {}).items()}
    templates.pop(name, None)
    config["templates"] = templates
    await _save(db, user, config)


async def set_notify_host(db: AsyncSession, user: User, enabled: bool) -> None:
    config = _config(user)
    config["notify_host_on_payment"] = bool(enabled)
    await _save(db, user, config)


# -- rendering -------------------------------------------------------------


def render(text: str, values: dict[str, str]) -> str:
    """Replaces {name} tokens with values. Anything else is left as written;
    nothing is evaluated (no attribute or index access)."""
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), text).strip()
