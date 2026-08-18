from datetime import date, timedelta

from app.models.booking import Booking
from app.models.property import Property
from app.services.calendar_service import (
    is_available,
    next_available_window,
    partial_availability,
    partial_availability_for_candidates,
)


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


async def test_partial_availability_full_when_no_bookings_in_window(test_property, db_session):
    today = date.today()
    result = await partial_availability(
        db_session, test_property.id, today, today + timedelta(days=10), nights=3
    )
    assert result.status == "full"
    assert result.conflicting_bookings == []


async def test_partial_availability_none_when_booking_fully_covers_window(test_property, db_session):
    today = date.today()
    window_start = today
    window_end = today + timedelta(days=10)
    db_session.add(
        Booking(property_id=test_property.id, check_in=window_start, check_out=window_end, status="confirmed")
    )
    await db_session.commit()

    result = await partial_availability(db_session, test_property.id, window_start, window_end, nights=3)
    assert result.status == "none"
    assert result.conflicting_bookings == [(window_start, window_end)]


async def test_partial_availability_partial_with_sufficient_gap_and_correct_conflicting_dates(
    test_property, db_session
):
    """Guest's window is wider than one conflicting booking -- confirms
    'partial' status AND that the specific conflicting dates come back
    correctly, matching the task's exact example phrasing use case ('a
    booking on this property from October 3rd to 5th')."""
    today = date.today()
    window_start = today
    window_end = today + timedelta(days=10)
    conflict_start = today + timedelta(days=3)
    conflict_end = today + timedelta(days=5)
    db_session.add(
        Booking(property_id=test_property.id, check_in=conflict_start, check_out=conflict_end, status="confirmed")
    )
    await db_session.commit()

    result = await partial_availability(db_session, test_property.id, window_start, window_end, nights=3)
    assert result.status == "partial"
    assert result.conflicting_bookings == [(conflict_start, conflict_end)]


async def test_partial_availability_none_when_no_sufficient_gap_remains(test_property, db_session):
    """Same conflicting booking as the partial case above, but nights is
    long enough that neither remaining gap (3 days before, 5 days after) can
    fit it -- confirms 'none' isn't just 'zero conflicts', it's 'no
    sufficient gap', per this task's own status design decision."""
    today = date.today()
    window_start = today
    window_end = today + timedelta(days=10)
    conflict_start = today + timedelta(days=3)
    conflict_end = today + timedelta(days=5)
    db_session.add(
        Booking(property_id=test_property.id, check_in=conflict_start, check_out=conflict_end, status="confirmed")
    )
    await db_session.commit()

    result = await partial_availability(db_session, test_property.id, window_start, window_end, nights=6)
    assert result.status == "none"


async def test_partial_availability_ignores_non_confirmed_bookings(test_property, db_session):
    today = date.today()
    window_start = today
    window_end = today + timedelta(days=10)
    db_session.add(
        Booking(
            property_id=test_property.id,
            check_in=window_start,
            check_out=window_end,
            status="cancelled",
        )
    )
    await db_session.commit()

    result = await partial_availability(db_session, test_property.id, window_start, window_end, nights=3)
    assert result.status == "full"
    assert result.conflicting_bookings == []


async def test_partial_availability_ignores_booking_entirely_outside_window(test_property, db_session):
    today = date.today()
    window_start = today + timedelta(days=20)
    window_end = today + timedelta(days=25)
    # A confirmed booking well before the window -- must not show up at all.
    db_session.add(
        Booking(
            property_id=test_property.id,
            check_in=today,
            check_out=today + timedelta(days=3),
            status="confirmed",
        )
    )
    await db_session.commit()

    result = await partial_availability(db_session, test_property.id, window_start, window_end, nights=2)
    assert result.status == "full"
    assert result.conflicting_bookings == []


async def test_partial_availability_for_candidates_batched_single_query(test_user, db_session):
    """Mirrors unavailable_property_ids' own batching test discipline --
    correct per-property classification across a candidate set from ONE
    query, not N round trips."""
    full = Property(user_id=test_user.id, name="Full", base_price=3000, max_guests=2, exophone="+918011129201")
    partial = Property(user_id=test_user.id, name="Partial", base_price=3000, max_guests=2, exophone="+918011129202")
    none_ = Property(user_id=test_user.id, name="None", base_price=3000, max_guests=2, exophone="+918011129203")
    db_session.add_all([full, partial, none_])
    await db_session.commit()
    for p in (full, partial, none_):
        await db_session.refresh(p)

    today = date.today()
    window_start = today
    window_end = today + timedelta(days=10)
    conflict_start = today + timedelta(days=3)
    conflict_end = today + timedelta(days=5)
    db_session.add_all(
        [
            Booking(property_id=partial.id, check_in=conflict_start, check_out=conflict_end, status="confirmed"),
            Booking(property_id=none_.id, check_in=window_start, check_out=window_end, status="confirmed"),
        ]
    )
    await db_session.commit()

    query_count = 0
    original_execute = db_session.execute

    async def _counting_execute(*args, **kwargs):
        nonlocal query_count
        query_count += 1
        return await original_execute(*args, **kwargs)

    db_session.execute = _counting_execute
    try:
        results = await partial_availability_for_candidates(
            db_session, [full.id, partial.id, none_.id], window_start, window_end, nights=3
        )
    finally:
        db_session.execute = original_execute

    assert query_count == 1
    assert results[full.id].status == "full"
    assert results[partial.id].status == "partial"
    assert results[partial.id].conflicting_bookings == [(conflict_start, conflict_end)]
    assert results[none_.id].status == "none"


async def test_partial_availability_for_candidates_empty_input_no_query(db_session):
    results = await partial_availability_for_candidates(db_session, [], date.today(), date.today(), nights=1)
    assert results == {}
