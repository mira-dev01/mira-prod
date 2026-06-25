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
