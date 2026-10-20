from datetime import date
from pathlib import Path

import respx
from httpx import Response
from sqlalchemy import select

from app.integrations.ical_client import parse_ical
from app.models.booking import Booking
from app.services.calendar_service import is_available, sync_property_ical

FIXTURE = Path(__file__).parent / "fixtures" / "sample.ics"


def test_parse_ical_extracts_events():
    events = parse_ical(FIXTURE.read_text())
    assert len(events) == 2
    assert events[0].uid == "airbnb-reservation-aaa111"
    assert events[0].check_in == date(2026, 8, 1)
    assert events[0].check_out == date(2026, 8, 5)


@respx.mock
async def test_sync_property_ical_upserts_bookings(test_property, db_session):
    test_property.ical_url = "https://airbnb.com/calendar/ical/fake.ics"
    await db_session.commit()

    respx.get(test_property.ical_url).mock(return_value=Response(200, text=FIXTURE.read_text()))

    count = await sync_property_ical(db_session, test_property)
    assert count == 2

    assert await is_available(db_session, test_property.id, date(2026, 8, 2), date(2026, 8, 3)) is False
    assert await is_available(db_session, test_property.id, date(2026, 8, 6), date(2026, 8, 9)) is True


@respx.mock
async def test_sync_property_ical_is_idempotent_on_uid(test_property, db_session):
    test_property.ical_url = "https://airbnb.com/calendar/ical/fake2.ics"
    await db_session.commit()

    respx.get(test_property.ical_url).mock(return_value=Response(200, text=FIXTURE.read_text()))

    await sync_property_ical(db_session, test_property)
    await sync_property_ical(db_session, test_property)

    from sqlalchemy import select

    bookings = (await db_session.scalars(select(Booking).where(Booking.property_id == test_property.id))).all()
    assert len(bookings) == 2  # second sync updates existing rows, doesn't duplicate


@respx.mock
async def test_sync_property_ical_removes_bookings_dropped_from_feed(test_property, db_session):
    # Regression: a guest cancelling on Airbnb removes their event from the
    # feed, but sync_property_ical only ever upserted -- the cancelled
    # booking's dates stayed blocked in Mira forever even after Airbnb
    # released them, since nothing ever reconciled removals.
    test_property.ical_url = "https://airbnb.com/calendar/ical/fake3.ics"
    await db_session.commit()

    route = respx.get(test_property.ical_url)
    route.mock(return_value=Response(200, text=FIXTURE.read_text()))
    await sync_property_ical(db_session, test_property)

    shrunk_ics = FIXTURE.read_text().replace(
        "BEGIN:VEVENT\nDTSTART:20260810\nDTEND:20260812\nUID:airbnb-reservation-bbb222\nSUMMARY:Reserved\nEND:VEVENT\n",
        "",
    )
    route.mock(return_value=Response(200, text=shrunk_ics))
    count = await sync_property_ical(db_session, test_property)
    assert count == 1

    bookings = (await db_session.scalars(select(Booking).where(Booking.property_id == test_property.id))).all()
    assert len(bookings) == 1
    assert bookings[0].source_uid == "airbnb-reservation-aaa111"
    assert await is_available(db_session, test_property.id, date(2026, 8, 10), date(2026, 8, 12)) is True
