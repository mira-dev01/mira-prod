"""Lead temperature semantics, and the deterministic floors that back up
the LLM's own judgment.

Lead.lead_temperature is still a single string column, set mostly by the
LLM through update_lead (LEAD_AGENT_INSTRUCTIONS, prompts/system_prompt.py).
This module doesn't replace that. It adds two things:

1. One written definition of each level, shared by the prompt, the tools
   and analytics:

   cold     -- General enquiry / information gathering. No clear booking
               intent.
   warm     -- Genuine interest with meaningful booking details: at least
               TWO qualification signals (dates or a stay length, guest
               count, a specific property). Dates + guest count are
               qualification, never enough on their own for hot.
   hot      -- Strong booking intent. The guest is discussing availability
               of specific dates, pricing or negotiation, i.e. what it would
               take to make this booking.
   very_hot -- Booking intent: the guest has explicitly said they want to
               go ahead (accepted a price and asked to book, asked how to
               pay or book, "please book it").

2. Raise-only floors from events the system observes directly, so a live
   LLM function-calling gap (the model negotiates a price but never calls
   update_lead) doesn't leave a pricing conversation labelled cold or blank:

   check_calendar result ...... warm (availability discussion is a
                                qualification signal); hot when the exact
                                dates came back AVAILABLE ("availability
                                confirmed" is an intent signal).
   get_pricing ................ hot (pricing requested).
   negotiate_rate ............. hot (negotiation initiated).
   update_lead fields ......... warm when >= 2 qualification signals.

   very_hot is never derived by a floor. It needs the guest's own explicit
   words, which only the LLM hears; the prompt tells it to set very_hot
   when the guest accepts and wants to proceed.

Floors only ever RAISE. merge_temperature() keeps the higher of the current
and incoming value, so neither an in-call floor nor a later LLM update can
silently drop an inquiry that already reached hot back to cold. The host can
still set any value from the dashboard (PATCH /leads/{id} writes directly,
not through this module), since the host's judgment outranks both.

Backwards compatible: hot/warm/cold keep their meaning, and every reader
that checks == "hot" was updated to treat very_hot as at least hot
(is_hot_or_above).
"""

from collections.abc import Iterable

TEMPERATURE_RANK: dict[str, int] = {"cold": 0, "warm": 1, "hot": 2, "very_hot": 3}
HOT_OR_ABOVE: tuple[str, ...] = ("hot", "very_hot")
WARM_OR_ABOVE: tuple[str, ...] = ("warm", "hot", "very_hot")


def rank(value: str | None) -> int:
    """-1 for None/unrecognised, so any real temperature outranks it."""
    if value is None:
        return -1
    return TEMPERATURE_RANK.get(value, -1)


def merge_temperature(current: str | None, incoming: str | None) -> str | None:
    """The higher of the two (raise-only). An unrecognised incoming value
    never replaces a recognised current one."""
    if incoming is None or incoming not in TEMPERATURE_RANK:
        return current
    return incoming if rank(incoming) > rank(current) else current


def is_hot_or_above(value: str | None) -> bool:
    return value in HOT_OR_ABOVE


def qualification_signal_count(
    *,
    has_dates_or_stay_length: bool,
    num_guests: int | None,
    properties_discussed: Iterable[str] | None,
) -> int:
    return sum(
        (
            bool(has_dates_or_stay_length),
            bool(num_guests),
            bool(list(properties_discussed or [])),
        )
    )


def qualification_floor(
    *,
    has_dates_or_stay_length: bool,
    num_guests: int | None,
    properties_discussed: Iterable[str] | None,
) -> str | None:
    """warm once at least two qualification signals are present, else no
    floor (None, not cold: absence of details isn't evidence of anything)."""
    count = qualification_signal_count(
        has_dates_or_stay_length=has_dates_or_stay_length,
        num_guests=num_guests,
        properties_discussed=properties_discussed,
    )
    return "warm" if count >= 2 else None


# Engagement event -> floor. Kept as data so the mapping above is the one
# thing to read, and the tool handlers just name the event.
ENGAGEMENT_FLOORS: dict[str, str] = {
    "availability_checked": "warm",
    "availability_confirmed": "hot",
    "pricing_requested": "hot",
    "negotiation_initiated": "hot",
}


def engagement_floor(event: str) -> str | None:
    return ENGAGEMENT_FLOORS.get(event)
