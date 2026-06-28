"""Parses a single Airbnb listing PDP JSON (as scraped from the listing's
GraphQL response, e.g. via an external scraper saving `output/raw/<room_id>.json`)
into the fields needed to create/update a Property, plus a set of FAQ
knowledge-base entries (neighbourhood, layout, cancellation policy, Guest
Favorite status, etc.) so the voice agent's search_faq tool can answer as
many guest questions as possible straight from the scrape.

Defensive throughout: real scrapes vary by listing type and Airbnb changes
this schema over time, so every lookup degrades to None/[] rather than
raising, and the caller decides what counts as good enough to import.
"""

import html
import re
from typing import Any

_TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_COUNT_RE = re.compile(r"(\d+)\s+(bedroom|bed|bathroom)s?", re.IGNORECASE)

# Categories this importer generates -- the API layer re-syncs exactly these
# categories on every import (delete + recreate) so re-uploading a refreshed
# scrape doesn't pile up duplicate FAQ entries, while leaving any FAQ entries
# the host added by hand (different categories) untouched.
AUTO_FAQ_CATEGORIES = {"layout", "neighbourhood", "booking", "reputation", "description", "safety"}


def _to_24h(time_str: str) -> str | None:
    match = _TIME_RE.search(time_str)
    if not match:
        return None
    hour, minute, meridiem = match.groups()
    hour = int(hour)
    minute = int(minute) if minute else 0
    if meridiem.lower() == "pm" and hour != 12:
        hour += 12
    if meridiem.lower() == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def _strip_html(text: str | None) -> str:
    if not text:
        return ""
    unescaped = html.unescape(text)
    with_breaks = re.sub(r"<br\s*/?>", "\n", unescaped, flags=re.IGNORECASE)
    return _TAG_RE.sub("", with_breaks).strip()


def _find_section(sections: list[dict], section_id: str) -> dict:
    for entry in sections:
        if entry.get("sectionId") == section_id:
            return entry.get("section") or {}
    return {}


def _extract_name(node: dict, pdp: dict) -> str | None:
    name = (((node.get("description") or {}).get("name") or {})).get("localizedString")
    if name:
        return name
    title = (((pdp.get("title") or {}).get("content") or {})).get("localizedString")
    return title


def _extract_city(pdp: dict, location_section: dict) -> str | None:
    city = pdp.get("localizedLocation")
    if city:
        return city
    subtitle = location_section.get("subtitle")
    if subtitle:
        return subtitle.split(",")[0].strip()
    return None


def _extract_amenities(pdp: dict) -> list[str]:
    amenities_block = pdp.get("amenities") or {}
    names: list[str] = []
    seen: set[str] = set()
    for group_key in ("previewAmenitiesGroups", "seeAllAmenitiesGroups"):
        for group in amenities_block.get(group_key) or []:
            for item in group.get("amenities") or []:
                title = item.get("title")
                if title and item.get("available") and title not in seen:
                    seen.add(title)
                    names.append(title)
    return names


def _extract_house_rules_and_times(policies: dict) -> tuple[str | None, str | None, str | None]:
    flat_rules = [item.get("title", "") for item in policies.get("houseRules") or []]

    check_in_time = check_out_time = None
    for title in flat_rules:
        if check_in_time is None and "check-in" in title.lower():
            check_in_time = _to_24h(title)
        if check_out_time is None and "checkout" in title.lower():
            check_out_time = _to_24h(title)

    lines: list[str] = []
    for group in policies.get("houseRulesSections") or []:
        group_title = group.get("title")
        items = [item.get("title", "") for item in group.get("items") or []]
        if group_title and items:
            lines.append(f"{group_title}:\n" + "\n".join(f"- {item}" for item in items))
    house_rules = "\n\n".join(lines) if lines else ("\n".join(flat_rules) or None)

    return house_rules, check_in_time, check_out_time


def _extract_layout_faq(pdp: dict, sections: list[dict]) -> dict | None:
    items = (pdp.get("overview") or {}).get("items") or []
    counts: dict[str, int] = {}
    for item in items:
        match = _COUNT_RE.search(item)
        if match:
            counts[match.group(2).lower()] = int(match.group(1))

    sleep_section = _find_section(sections, "SLEEPING_ARRANGEMENT_WITH_IMAGES")
    arrangement = sleep_section.get("arrangementDetails") or []

    parts = []
    if counts:
        parts.append(", ".join(f"{count} {label}{'s' if count != 1 else ''}" for label, count in counts.items()))
    if arrangement:
        room_lines = [f"{room.get('title', 'Room')}: {room.get('subtitle', '')}".strip(": ") for room in arrangement]
        parts.append("Sleeping arrangement -- " + "; ".join(room_lines))

    if not parts:
        return None
    return {
        "question": "How many bedrooms, beds, and bathrooms does this property have?",
        "answer": ". ".join(parts) + ".",
        "category": "layout",
    }


def _extract_neighbourhood_faq(location_section: dict) -> dict | None:
    for detail in location_section.get("previewLocationDetails") or []:
        text = _strip_html((detail.get("content") or {}).get("htmlText"))
        if text:
            return {"question": "What's the neighbourhood like?", "answer": text, "category": "neighbourhood"}
    return None


def _extract_cancellation_faq(raw: dict) -> dict | None:
    policies = (
        ((raw.get("presentation") or {}).get("stayProductDetailPage") or {})
        .get("sections", {})
        .get("metadata", {})
        .get("bookingPrefetchData", {})
        .get("cancellationPolicies")
        or []
    )
    if not policies:
        return None
    name = policies[0].get("localized_cancellation_policy_name")
    if not name:
        return None
    return {
        "question": "What is the cancellation policy?",
        "answer": f"This listing has a {name} cancellation policy.",
        "category": "booking",
    }


def _extract_guest_favorite_faq(node: dict) -> dict | None:
    is_favorite = (node.get("contextualizedGuestFavorites") or {}).get("isGuestFavorite")
    if not is_favorite:
        return None
    rating_count = ((node.get("listingRatingStats") or {}).get("overallRatingStats") or {}).get("ratingCount")
    answer = "Yes -- this listing is recognised as an Airbnb Guest Favorite, based on consistently great reviews."
    if rating_count:
        answer += f" It has {rating_count}+ guest reviews."
    return {"question": "Is this property a Guest Favorite on Airbnb?", "answer": answer, "category": "reputation"}


def _extract_description_faq(node: dict) -> dict | None:
    descriptions = (node.get("pdpPresentation") or {}).get("descriptions") or {}
    raw_html = (descriptions.get("longDescriptionHtml") or {}).get("localizedString")
    if not raw_html:
        return None
    text = _strip_html(raw_html)
    # The "Other things to note" / "Registration details" tail duplicates
    # house_rules -- keep just the narrative description for this FAQ entry.
    text = re.split(r"Other things to note", text, flags=re.IGNORECASE)[0].strip()
    if not text:
        return None
    return {"question": "Tell me about this property", "answer": text, "category": "description"}


def _extract_safety_faq(policies: dict) -> dict | None:
    items = [item.get("title", "") for item in policies.get("previewSafetyAndProperties") or [] if item.get("title")]
    if not items:
        return None
    return {
        "question": "What safety and property information should guests know?",
        "answer": "; ".join(items) + ".",
        "category": "safety",
    }


def parse_airbnb_listing(raw: dict[str, Any]) -> dict[str, Any]:
    """Returns {"fields": {...Property fields...}, "faq_entries": [...]}.
    Only includes Property fields it found data for -- callers should merge
    that dict over sane defaults rather than treating it as a complete
    PropertyCreate."""
    node = raw.get("node") or {}
    pdp = node.get("pdpPresentation") or {}
    sections = ((raw.get("presentation") or {}).get("stayProductDetailPage") or {}).get("sections", {}).get(
        "sections", []
    )

    location_section = _find_section(sections, "LOCATION_DEFAULT")
    policies = _find_section(sections, "POLICIES_DEFAULT")

    fields: dict[str, Any] = {}

    name = _extract_name(node, pdp)
    if name:
        fields["name"] = name

    city = _extract_city(pdp, location_section)
    if city:
        fields["city"] = city

    max_guests = node.get("personCapacity") or pdp.get("personCapacity")
    if isinstance(max_guests, int) and max_guests > 0:
        fields["max_guests"] = max_guests

    amenities = _extract_amenities(pdp)
    if amenities:
        fields["amenities"] = amenities

    house_rules, check_in_time, check_out_time = _extract_house_rules_and_times(policies)
    if house_rules:
        fields["house_rules"] = house_rules
    if check_in_time:
        fields["check_in_time"] = check_in_time
    if check_out_time:
        fields["check_out_time"] = check_out_time

    faq_entries = [
        entry
        for entry in (
            _extract_layout_faq(pdp, sections),
            _extract_neighbourhood_faq(location_section),
            _extract_cancellation_faq(raw),
            _extract_guest_favorite_faq(node),
            _extract_description_faq(node),
            _extract_safety_faq(policies),
        )
        if entry is not None
    ]

    return {"fields": fields, "faq_entries": faq_entries}
