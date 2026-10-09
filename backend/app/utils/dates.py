from datetime import date, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def today_ist() -> date:
    """Business "today" for every guest-facing date decision. Never
    date.today(): the server runs in UTC, which is still the previous day
    in India until 05:30 IST -- the prompt's own date anchor
    (system_prompt._today_anchor) is IST, so every code-level check must be
    too or the two disagree for five and a half hours a day."""
    return datetime.now(IST).date()
