"""Fetches and parses Airbnb (or any) iCal export feeds into booking date ranges."""

from dataclasses import dataclass
from datetime import date, datetime

import httpx
from icalendar import Calendar


@dataclass
class ICalEvent:
    uid: str
    check_in: date
    check_out: date
    summary: str | None = None


def parse_ical(raw: str | bytes) -> list[ICalEvent]:
    """Parse raw .ics text into a list of booking events.

    Airbnb's exported iCal marks each reservation as a VEVENT spanning
    check-in (DTSTART) to check-out (DTEND), with a stable UID we use for
    idempotent upserts.
    """
    cal = Calendar.from_ical(raw)
    events: list[ICalEvent] = []

    for component in cal.walk("VEVENT"):
        dtstart = component.get("dtstart")
        dtend = component.get("dtend")
        uid = component.get("uid")
        if dtstart is None or dtend is None or uid is None:
            continue

        check_in = _to_date(dtstart.dt)
        check_out = _to_date(dtend.dt)
        if check_in >= check_out:
            continue

        events.append(
            ICalEvent(
                uid=str(uid),
                check_in=check_in,
                check_out=check_out,
                summary=str(component.get("summary")) if component.get("summary") else None,
            )
        )

    return events


def _to_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value


async def fetch_ical(url: str, timeout: float = 15.0) -> list[ICalEvent]:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
    return parse_ical(response.text)
