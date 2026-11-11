"""Reads the calendar dates a piece of spoken English text names.

Used by the voice layer to check that a reply about alternative dates names only
dates a tool actually verified (app/voice/property_recommendation_guard.py).
Pure functions, no I/O.

What it recognises (what the model writes for TTS):
  * "6 December", "6th of December", "December 6", "Dec 6th"
  * ranges sharing one month: "6 to 8 December", "December 6 to 8", "6-8 Dec"
  * ISO dates: "2026-12-06"
  * a bare ordinal day with no month: "the 6th" (month unknown)

What it deliberately ignores: a bare number with no ordinal and no month ("8
adults", "2 nights"), and non-English month names. Missing a date only means the
check does nothing for it; it never invents one.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
_MONTH = "(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?(?!\d)"
_TO = r"\s*(?:to|till|until|through|-|–|—)\s*"

_DAY_MONTH = re.compile(rf"(?<![\d-]){_DAY}\s+(?:of\s+)?{_MONTH}\b", re.IGNORECASE)
_MONTH_DAY = re.compile(rf"\b{_MONTH}\s+(?:the\s+)?{_DAY}", re.IGNORECASE)
_RANGE_DAY_MONTH = re.compile(rf"(?<![\d-]){_DAY}{_TO}{_DAY}\s+(?:of\s+)?{_MONTH}\b", re.IGNORECASE)
_RANGE_MONTH_DAY = re.compile(rf"\b{_MONTH}\s+(?:the\s+)?{_DAY}{_TO}{_DAY}", re.IGNORECASE)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_ORDINAL = re.compile(r"(?<![\d-])(\d{1,2})(?:st|nd|rd|th)\b", re.IGNORECASE)


@dataclass(frozen=True)
class StatedDate:
    day: int
    month: int | None = None  # None: the text gave only the day ("the 6th")
    year: int | None = None


def _ascii_digits(text: str) -> str:
    """Devanagari and other Unicode digits to ASCII, so "६" reads as 6."""
    return "".join(str(unicodedata.digit(c)) if c.isdigit() and not c.isascii() else c for c in text)


def extract_stated_dates(text: str) -> set[StatedDate]:
    text = _ascii_digits(text or "")
    found: set[StatedDate] = set()
    covered: list[tuple[int, int]] = []

    def add(day: str, month: str | None = None, year: str | None = None) -> None:
        d = int(day)
        if 1 <= d <= 31:
            found.add(StatedDate(d, _MONTHS[month.lower().rstrip(".")] if month else None, int(year) if year else None))

    for m in _ISO.finditer(text):
        month, day = int(m.group(2)), int(m.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            found.add(StatedDate(day, month, int(m.group(1))))
        covered.append(m.span())
    for m in _RANGE_DAY_MONTH.finditer(text):
        add(m.group(1), m.group(3))
        add(m.group(2), m.group(3))
        covered.append(m.span())
    for m in _RANGE_MONTH_DAY.finditer(text):
        add(m.group(2), m.group(1))
        add(m.group(3), m.group(1))
        covered.append(m.span())
    for m in _DAY_MONTH.finditer(text):
        if not any(a <= m.start() < b for a, b in covered):
            add(m.group(1), m.group(2))
            covered.append(m.span())
    for m in _MONTH_DAY.finditer(text):
        if not any(a <= m.start() < b for a, b in covered):
            add(m.group(2), m.group(1))
            covered.append(m.span())
    for m in _ORDINAL.finditer(text):
        if not any(a <= m.start() < b for a, b in covered):
            add(m.group(1))
    return found


def dates_in_ranges(ranges: list[tuple[date, date]]) -> set[date]:
    """Every date from check-in through check-out of each range (inclusive)."""
    out: set[date] = set()
    for start, end in ranges:
        day = start
        while day <= end:
            out.add(day)
            day += timedelta(days=1)
    return out


def unverified_dates(text: str, allowed: set[date]) -> set[StatedDate]:
    """The dates the text names that are NOT in `allowed`. A day with no month
    matches any allowed date with that day number; a day and month match on those
    two (the year is checked only when the text gave one)."""
    bad: set[StatedDate] = set()
    for stated in extract_stated_dates(text):
        ok = any(
            d.day == stated.day
            and (stated.month is None or d.month == stated.month)
            and (stated.year is None or d.year == stated.year)
            for d in allowed
        )
        if not ok:
            bad.add(stated)
    return bad


def spoken_range(start: date, end: date) -> str:
    """"6 December to 8 December", for text built from verified dates."""
    return f"{start.day} {start.strftime('%B')} to {end.day} {end.strftime('%B')}"
