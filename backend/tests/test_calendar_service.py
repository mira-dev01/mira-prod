from datetime import date, timedelta

from app.models.booking import Booking
from app.services.calendar_service import is_available, next_available_window


async def test_is_available_true_when_no_bookings(test_property, db_session):
    today = date.today()
    assert await is_available(db_session, test_property.id, today, today + timedelta(days=2)) is True


async def test_is_available_false_when_overlapping(test_property, db_session):
    today = date.today()
    db_session.add(
        Booking(
            property_id=test_property.id,
            check_in=today + timedelta(days=5),
            check_out=today + timedelta(days=8),
            platform="airbnb",
            status="confirmed",
        )
    )
    await db_session.commit()

    # overlaps the existing booking
    assert await is_available(db_session, test_property.id, today + timedelta(days=6), today + timedelta(days=7)) is False
    # adjacent but non-overlapping (check_out == existing check_in) should be available
    assert await is_available(db_session, test_property.id, today + timedelta(days=2), today + timedelta(days=5)) is True


async def test_next_available_window_skips_blocked_range(test_property, db_session):
    today = date.today()
    db_session.add(
        Booking(
            property_id=test_property.id,
            check_in=today,
            check_out=today + timedelta(days=10),
            platform="airbnb",
            status="confirmed",
        )
    )
    await db_session.commit()

    window = await next_available_window(db_session, test_property.id, today, nights=2)
    assert window is not None
    start, end = window
    assert start >= today + timedelta(days=10)
    assert (end - start).days == 2
