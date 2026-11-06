"""Rent by the room vs the entire place -- one property, two ways to book it.

A property is ONE row with ONE identifier and ONE calendar. It is always
rentable as the entire place (the existing base_price / max_guests /
bedroom_count). A host can additionally switch on renting it by the room
(Property.room_rental_enabled + the room_* columns), in which case a guest can
book some of its rooms instead, at the room rate.

Nothing here runs for a property with room_rental_enabled False -- every
function degrades to "entire place only", which is exactly how every property
behaved before this existed.

What lives here: the vocabulary (the two scopes), the deterministic choice of
which one suits a guest (party -> entire place; a night stay for a few people
-> rooms), the wording used for a booking's scope, and the pricing view that
lets the unchanged pricing/negotiation code price a room stay. What does NOT
live here: availability (calendar_service.scope_availability) and the spoken
tool results (tool_handlers).
"""

import math
import re
from dataclasses import dataclass
from typing import Any

ENTIRE_PLACE = "entire_place"
ROOMS = "rooms"
STAY_SCOPES = (ENTIRE_PLACE, ROOMS)

# A group coming to celebrate or gather wants the whole place, whatever the
# head count -- the noise, the shared spaces and the privacy are the point.
# Deliberately a short list of unambiguous words: the guest's own stated
# purpose is the only input, never a guess at what they "probably" mean.
_PARTY_PURPOSE_RE = re.compile(
    r"\b(party|parties|partying|celebrat\w*|birthday|bachelor\w*|bachelorette|wedding|reception|reunion|"
    r"get[- ]?together|gathering|function|event|offsite|off-site|shoot|anniversary)\b",
    re.IGNORECASE,
)


def is_party_purpose(purpose_of_stay: str | None) -> bool:
    return bool(purpose_of_stay and _PARTY_PURPOSE_RE.search(purpose_of_stay))


def offers_rooms(property_: Any) -> bool:
    """True only when the host has switched room rental on AND filled in what
    a room needs (a price, a room count, a per-room capacity). A half-filled
    tab must not make the agent offer a room it cannot price."""
    return bool(
        getattr(property_, "room_rental_enabled", False)
        and property_.room_base_price
        and float(property_.room_base_price) > 0
        and property_.rentable_room_count
        and property_.rentable_room_count > 0
        and property_.room_max_guests
        and property_.room_max_guests > 0
    )


def rooms_needed(property_: Any, guests: int) -> int:
    return max(1, math.ceil(guests / property_.room_max_guests))


def room_capacity(property_: Any) -> int:
    return property_.rentable_room_count * property_.room_max_guests


@dataclass(frozen=True)
class ScopeRecommendation:
    scope: str
    rooms: int | None  # only for scope == ROOMS
    reason: str  # a fact about the property/party, safe to say aloud


def recommend_scope(
    property_: Any, num_guests: int | None, purpose_of_stay: str | None
) -> ScopeRecommendation | None:
    """Which way to book this property suits this guest. None means "not
    decided" -- the property is not rented by the room, or the agent does not
    yet know enough (neither the party size nor the occasion): ask, don't
    guess. Pure and deterministic, so the same guest always gets the same
    answer and a test can pin it."""
    if not offers_rooms(property_):
        return None
    if is_party_purpose(purpose_of_stay):
        return ScopeRecommendation(
            ENTIRE_PLACE, None, "a party or celebration needs the whole place to themselves"
        )
    if not num_guests or num_guests <= 0:
        return None
    if num_guests > room_capacity(property_):
        return ScopeRecommendation(
            ENTIRE_PLACE, None, f"{num_guests} guests is more than the rooms alone sleep ({room_capacity(property_)})"
        )
    rooms = rooms_needed(property_, num_guests)
    if rooms >= property_.rentable_room_count:
        return ScopeRecommendation(
            ENTIRE_PLACE, None, f"{num_guests} guests would need every room, so the whole place is the better deal"
        )
    return ScopeRecommendation(ROOMS, rooms, f"{num_guests} guests fit in {rooms} room{'s' if rooms != 1 else ''}")


def scope_label(scope: str | None, rooms: int | None = None) -> str:
    """How a stay's scope is said -- on the calendar and out loud."""
    if scope == ROOMS:
        n = rooms or 1
        return f"{n} room{'s' if n != 1 else ''}"
    return "the entire place"


def booking_scope_label(booking: Any) -> str:
    return scope_label(getattr(booking, "stay_scope", ENTIRE_PLACE), getattr(booking, "rooms_booked", None))


def validate_room_request(property_: Any, rooms: int | None, num_guests: int | None) -> str | None:
    """A guest-safe reason a by-the-room request cannot be served, or None."""
    if not offers_rooms(property_):
        return f"{property_.name} is only rented as the entire place -- it isn't available by the room."
    n = rooms or 1
    if n > property_.rentable_room_count:
        return (
            f"{property_.name} has {property_.rentable_room_count} room"
            f"{'s' if property_.rentable_room_count != 1 else ''} that can be rented separately, "
            f"which is fewer than {n}."
        )
    if num_guests and num_guests > n * property_.room_max_guests:
        return (
            f"{n} room{'s' if n != 1 else ''} at {property_.name} sleep{'s' if n == 1 else ''} up to "
            f"{n * property_.room_max_guests} guests, which is fewer than {num_guests}."
        )
    return None


class _RoomPricingView:
    """A read-only stand-in for a Property whose nightly rate is the ROOM rate
    for `rooms` rooms. pricing_engine.calculate_price / negotiate_rate only
    ever read attributes off the property they are given, so handing them this
    prices a room stay through the exact same code (length-of-stay offers, the
    host's negotiation policy, the non-positive-price guard) without a second
    pricing implementation and without touching that code.

    Live Airbnb pricing is switched off here: that fetch is for the whole-
    listing price of the entire place and means nothing for a room. Anything
    not overridden below reads straight through to the real property."""

    def __init__(self, property_: Any, rooms: int):
        self._property = property_
        self.rooms = rooms
        self.base_price = float(property_.room_base_price or 0) * rooms
        self.exact_airbnb_pricing = False
        self.airbnb_listing_id = None
        # Negotiation messages name the property they were computed for; say
        # which part of it, so "that's our best price" can't be mistaken for
        # the entire-place rate.
        self.name = f"{property_.name} ({scope_label(ROOMS, rooms)})"

    def __getattr__(self, name: str) -> Any:
        return getattr(self._property, name)


def priced_property(property_: Any, scope: str | None, rooms: int | None) -> Any:
    """The object to hand to the pricing code for this scope: the property
    itself for the entire place (so that path is byte-for-byte what it always
    was), a room-rate view for rooms."""
    if scope == ROOMS and offers_rooms(property_):
        return _RoomPricingView(property_, rooms or 1)
    return property_
