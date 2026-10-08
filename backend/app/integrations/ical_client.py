"""Fetches and parses Airbnb (or any) iCal export feeds into booking date ranges."""

import re
import time
from dataclasses import dataclass
from datetime import date, datetime

import httpx
from icalendar import Calendar

from app.observability import health


@dataclass
class ICalEvent:
    uid: str
    check_in: date
    check_out: date
    summary: str | None = None
    # Airbnb reservation DESCRIPTION: "Phone Number (Last 4 Digits): 1234".
    # None for blocks and for feeds that don't carry it.
    phone_last4: str | None = None


_PHONE_LAST4_RE = re.compile(r"last\s*4\s*digits\)?\s*:\s*(\d{4})", re.IGNORECASE)


def _phone_last4(description: str | None) -> str | None:
    if not description:
        return None
    match = _PHONE_LAST4_RE.search(description)
    return match.group(1) if match else None


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
                phone_last4=_phone_last4(str(component.get("description")) if component.get("description") else None),
            )
        )

    return events


def _to_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value


async def fetch_ical(url: str, timeout: float = 15.0) -> list[ICalEvent]:
    # Health (app/observability/health.py): unreachable/5xx calendar hosts
    # count against "iCal calendar sync"; a 4xx is one host's broken link,
    # not a sync outage, so it doesn't.
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        outcome = "error" if exc.response.status_code >= 500 else "ok"
        health.record("ical_sync", outcome, latency_ms=(time.monotonic() - started) * 1000, error=exc, op="fetch")
        raise
    except Exception as exc:
        health.record("ical_sync", "error", latency_ms=(time.monotonic() - started) * 1000, error=exc, op="fetch")
        raise
    health.record("ical_sync", "ok", latency_ms=(time.monotonic() - started) * 1000, op="fetch")
    return parse_ical(response.text)
