from datetime import date, timedelta

from app.models.booking import Booking
from app.services.calendar_service import is_available, next_available_window, unavailable_property_ids


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


async def test_unavailable_property_ids_empty_when_no_ids_given(db_session):
    today = date.today()
    result = await unavailable_property_ids(db_session, [], today, today + timedelta(days=2))
    assert result == set()


async def test_unavailable_property_ids_batched_across_candidate_set(test_user, db_session):
    """Phase 2.4 (documentation/agent-conversation-improvement.md): one
    query for a small candidate set, same overlap semantics as
    is_available -- not a second, different definition of 'available'."""
    from app.models.property import Property

    booked = Property(user_id=test_user.id, name="Booked", base_price=3000, max_guests=2, exophone="+918011129101")
    open_one = Property(user_id=test_user.id, name="Open", base_price=3000, max_guests=2, exophone="+918011129102")
    db_session.add_all([booked, open_one])
    await db_session.commit()
    await db_session.refresh(booked)
    await db_session.refresh(open_one)

    today = date.today()
    db_session.add(
        Booking(
            property_id=booked.id,
            check_in=today + timedelta(days=5),
            check_out=today + timedelta(days=8),
            status="confirmed",
        )
    )
    await db_session.commit()

    result = await unavailable_property_ids(
        db_session, [booked.id, open_one.id], today + timedelta(days=6), today + timedelta(days=7)
    )
    assert result == {booked.id}


async def test_unavailable_property_ids_ignores_non_confirmed_bookings(test_user, db_session):
    from app.models.property import Property

    property_ = Property(user_id=test_user.id, name="Pine", base_price=3000, max_guests=2, exophone="+918011129103")
    db_session.add(property_)
    await db_session.commit()
    await db_session.refresh(property_)

    today = date.today()
    db_session.add(
        Booking(
            property_id=property_.id,
            check_in=today + timedelta(days=5),
            check_out=today + timedelta(days=8),
            status="cancelled",
        )
    )
    await db_session.commit()

    result = await unavailable_property_ids(
        db_session, [property_.id], today + timedelta(days=6), today + timedelta(days=7)
    )
    assert result == set()
