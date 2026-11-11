"""Test services that behave like the real ones and can be made to fail on demand: a payment provider
behind the same interface as razorpay_client, and a log standing in for WhatsApp. No real money, no real
guests. Everything here is inert unless settings.simulator_enabled is on AND (for payments) a host's saved
key id starts with "sim_".

What can be done to it (see app/api/v1/simulator.py):
  * a link can be paid, fail an attempt (the link stays open for another try, like a real gateway),
    be cancelled, or expire;
  * "payment", "messaging" and "calendar" can each be switched DOWN, so the agent's recovery from a tool
    outage can be watched.

The real payment flow (dates held, links polled, a paid link turned into a confirmed booking and a receipt)
runs unchanged on top of this: only the provider underneath is pretend.
"""

import logging
import secrets
import uuid

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.integrations.razorpay_client import MIN_AMOUNT_PAISE, PaymentLink, RazorpayError, to_paise
from app.models.simulator import SimMessage, SimPaymentLink, SimulatorFlag

logger = logging.getLogger(__name__)

SIM_KEY_PREFIX = "sim_"
TARGETS = ("payment", "messaging", "calendar")
OUTCOMES = ("paid", "failed", "cancelled", "expired", "reset")


class SimulatedOutage(Exception):
    """A pretend service is switched down. Raised where the real one would fail."""


def is_sim_key(key_id: str | None) -> bool:
    return bool(settings.simulator_enabled and key_id and key_id.startswith(SIM_KEY_PREFIX))


async def is_down(target: str) -> bool:
    """Whether a pretend service is switched down. Costs nothing when the simulator is off."""
    if not settings.simulator_enabled:
        return False
    async with AsyncSessionLocal() as db:
        flag = await db.get(SimulatorFlag, target)
        return bool(flag and flag.enabled)


async def set_down(target: str, down: bool) -> None:
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r}")
    async with AsyncSessionLocal() as db:
        flag = await db.get(SimulatorFlag, target)
        if flag is None:
            db.add(SimulatorFlag(key=target, enabled=down))
        else:
            flag.enabled = down
        await db.commit()


async def flags() -> dict[str, bool]:
    async with AsyncSessionLocal() as db:
        rows = {f.key: f.enabled for f in (await db.scalars(select(SimulatorFlag))).all()}
    return {target: rows.get(target, False) for target in TARGETS}


# -- the payment provider (same shape as razorpay_client) -----------------------------------------------


def _link(row: SimPaymentLink) -> PaymentLink:
    return PaymentLink(
        id=row.id,
        short_url=f"{settings.backend_base_url.rstrip('/')}/api/v1/sim/pay/{row.id}",
        status=row.status,
        payment_id=row.payment_id,
        amount_paise=row.amount_paise,
        last_attempt=row.last_attempt,
    )


async def verify_credentials() -> None:
    if await is_down("payment"):
        raise RazorpayError("Online payment is temporarily unavailable.")


async def create_payment_link(*, amount_rupees, description: str) -> PaymentLink:
    if await is_down("payment"):
        raise RazorpayError("Online payment is temporarily unavailable.")
    paise = to_paise(amount_rupees)
    if paise < MIN_AMOUNT_PAISE:
        raise RazorpayError("That amount is too small to collect online.")
    row = SimPaymentLink(id=f"sim_{uuid.uuid4().hex[:16]}", amount_paise=paise, description=description[:500])
    async with AsyncSessionLocal() as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return _link(row)


async def fetch_payment_link(link_id: str) -> PaymentLink:
    if await is_down("payment"):
        raise RazorpayError("Online payment is temporarily unavailable.")
    async with AsyncSessionLocal() as db:
        row = await db.get(SimPaymentLink, link_id)
        if row is None:
            raise RazorpayError("We couldn't find this booking.", status_code=404)
        return _link(row)


async def cancel_payment_link(link_id: str) -> PaymentLink:
    async with AsyncSessionLocal() as db:
        row = await db.get(SimPaymentLink, link_id)
        if row is None:
            raise RazorpayError("We couldn't find this booking.", status_code=404)
        if row.status == "created":
            row.status = "cancelled"
            await db.commit()
        return _link(row)


async def set_outcome(link_id: str, outcome: str) -> PaymentLink:
    """What the guest does on the pay page (or a tester does for them)."""
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}")
    async with AsyncSessionLocal() as db:
        row = await db.get(SimPaymentLink, link_id)
        if row is None:
            raise RazorpayError("We couldn't find this booking.", status_code=404)
        if outcome == "paid":
            if row.status == "paid":
                raise RazorpayError("This booking is already paid.")
            if row.status in ("cancelled", "expired"):
                raise RazorpayError("This reservation was cancelled." if row.status == "cancelled" else "This link has expired.")
            row.status, row.last_attempt, row.payment_id = "paid", None, f"sim_pay_{secrets.token_hex(6)}"
        elif outcome == "failed":
            if row.status == "created":
                row.last_attempt = "failed"
        elif outcome == "cancelled":
            if row.status == "created":
                row.status = "cancelled"
        elif outcome == "expired":
            if row.status == "created":
                row.status = "expired"
        elif outcome == "reset" and row.status != "paid":
            row.status, row.last_attempt = "created", None
        await db.commit()
        await db.refresh(row)
        return _link(row)


# -- WhatsApp stand-in ---------------------------------------------------------------------------------------


async def record_message(to_phone: str, body: str, kind: str = "text") -> str:
    """"Sends" a message: logged for the tester to read. Raises SimulatedOutage when messaging is down
    (the message is logged as failed, so it can be seen that it was tried)."""
    down = await is_down("messaging")
    async with AsyncSessionLocal() as db:
        row = SimMessage(to_phone=to_phone, body=body, status="failed" if down else "sent", kind=kind)
        db.add(row)
        await db.commit()
        sid = f"sim_msg_{row.id.hex[:12]}"
    if down:
        raise SimulatedOutage("WhatsApp is switched down for this test")
    return sid


async def recent_messages(limit: int = 50) -> list[dict]:
    async with AsyncSessionLocal() as db:
        rows = (await db.scalars(select(SimMessage).order_by(SimMessage.created_at.desc()).limit(limit))).all()
        return [
            {"id": str(r.id), "to": r.to_phone, "status": r.status, "kind": r.kind, "body": r.body, "at": r.created_at.isoformat()}
            for r in rows
        ]
