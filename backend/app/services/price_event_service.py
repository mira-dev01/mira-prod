"""Durable price history (app/models/price_event.py).

Written from the live call path (get_pricing / negotiate_rate), so it
follows usage_meter.py's strict fail-open discipline: every write runs
detached, in its own DB session, and never raises. A failed insert can't
roll back or expire the tool handler's own session, and it adds no latency
to the guest's turn.
"""

import asyncio
import logging
import uuid
from datetime import date

from app.models.price_event import PriceEvent

logger = logging.getLogger(__name__)

PRICE_TYPES = ("initial_quote", "counter_offer", "accepted_offer", "final_price")

# Strong references so detached tasks aren't garbage-collected mid-flight.
_background_tasks: set[asyncio.Task] = set()


async def record_price_event(
    *,
    user_id: uuid.UUID,
    price_type: str,
    source: str,
    price: float,
    property_id: uuid.UUID | None = None,
    call_session_id: uuid.UUID | None = None,
    booking_id: uuid.UUID | None = None,
    list_price: float | None = None,
    guest_offer: float | None = None,
    nights: int | None = None,
    check_in: date | None = None,
    check_out: date | None = None,
    currency: str = "INR",
) -> None:
    """One row in its own session. Never raises."""
    if price is None or price <= 0:
        return
    from app.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            db.add(
                PriceEvent(
                    user_id=user_id,
                    property_id=property_id,
                    call_session_id=call_session_id,
                    booking_id=booking_id,
                    price_type=price_type,
                    source=source,
                    price=round(float(price), 2),
                    list_price=round(float(list_price), 2) if list_price is not None else None,
                    guest_offer=round(float(guest_offer), 2) if guest_offer is not None else None,
                    currency=currency,
                    nights=nights,
                    check_in=check_in,
                    check_out=check_out,
                )
            )
            await db.commit()
    except Exception:
        logger.exception("Failed to record price event type=%s call_session_id=%s", price_type, call_session_id)


def record_price_event_detached(**kwargs) -> None:
    """Fire-and-forget record_price_event. Safe from any async code path;
    a no-op outside a running event loop or without a host user_id."""
    if kwargs.get("user_id") is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(record_price_event(**kwargs))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def drain_pending_writes() -> None:
    """Await every in-flight detached write. For tests and graceful shutdown;
    never called on the live call path."""
    if _background_tasks:
        await asyncio.gather(*list(_background_tasks), return_exceptions=True)
