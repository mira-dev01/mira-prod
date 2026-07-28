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

from app.integrations import cloudinary_client
from app.services.amenity_taxonomy import canonicalize_amenities
from app.services.property_normalizer import normalize_property_name

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


def _apply_normalized_name_fields(fields: dict[str, Any], normalized) -> None:
    """Copies non-None fields from a NormalizedName onto the parsed `fields`
    dict, matching this module's existing "only include what was found"
    convention. `brand` is deliberately never set here -- it needs
    cross-property lookup for the same host, resolved in
    _upsert_property_from_parsed (app/api/v1/properties.py) instead.

    `_normalizer_confidence` is a private, non-Property key the caller pops
    off before constructing/updating a Property -- it signals whether
    _upsert_property_from_parsed should also try the optional one-shot LLM
    fallback pass (only on "low" confidence, see property_normalizer.py)."""
    if normalized.display_name:
        fields["display_name"] = normalized.display_name
    if normalized.spoken_name:
        fields["spoken_name"] = normalized.spoken_name
    if normalized.property_type:
        fields["property_type"] = normalized.property_type
    if normalized.property_style:
        fields["property_style"] = normalized.property_style
    if normalized.bedroom_count is not None:
        fields["bedroom_count"] = normalized.bedroom_count
    fields["_normalizer_confidence"] = normalized.confidence


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
    description_faq = _extract_description_faq(node)
    if name:
        fields["name"] = name
        fields["raw_name"] = name
        normalized = normalize_property_name(
            name, description=description_faq["answer"] if description_faq else None
        )
        _apply_normalized_name_fields(fields, normalized)

    city = _extract_city(pdp, location_section)
    if city:
        fields["city"] = city

    max_guests = node.get("personCapacity") or pdp.get("personCapacity")
    if isinstance(max_guests, int) and max_guests > 0:
        fields["max_guests"] = max_guests

    amenities = _extract_amenities(pdp)
    if amenities:
        fields["amenities"] = amenities
        fields["amenity_tags"] = canonicalize_amenities(amenities)

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


async def parse_bright_data_listing(record: dict[str, Any], *, photo_folder: str | None = None) -> dict[str, Any]:
    """Adapts one Bright Data Airbnb Scraper API record (dataset
    gd_ld7ll037kqy322v05 -- see app/integrations/bright_data_client.py) into
    the same {"fields": ..., "faq_entries": ...} shape parse_airbnb_listing
    produces, for the same downstream property-creation code
    (app/api/v1/properties.py).

    NOT a shared parser with parse_airbnb_listing above -- Bright Data's
    schema is flat and pre-normalized (confirmed against their public sample
    dataset, github.com/luminati-io/Airbnb-dataset-samples), completely
    different from Airbnb's own nested GraphQL PDP shape that function
    expects. Reuses _to_24h/_strip_html since the underlying text (house
    rules mentioning check-in time, HTML-formatted descriptions) is the same
    kind of content either way.

    Async (unlike parse_airbnb_listing) because it re-hosts the listing's
    photos on Cloudinary via cloudinary_client.upload_images_from_urls --
    photo_folder scopes where they land (e.g. per-host) and is required to
    actually upload; omitted (None) just means fields["photos"] stays empty,
    same "skip, don't fail the import" stance as an unconfigured Cloudinary
    account.
    """
    fields: dict[str, Any] = {}

    # `name` in Bright Data's schema is often a compound string like "Villa
    # in Kecamatan Kuta Utara · ★5.0 · 2 bedrooms · 2
    # baths" -- listing_title/listing_name are closer to a clean name when
    # present.
    description = _strip_html(record.get("description_html") or record.get("description") or "")

    name = record.get("listing_title") or record.get("listing_name") or record.get("name")
    if name:
        fields["name"] = name
        fields["raw_name"] = name
        normalized = normalize_property_name(name, description=description or None)
        _apply_normalized_name_fields(fields, normalized)

    location = record.get("location") or ""
    if location:
        fields["city"] = location.split(",")[0].strip()

    if description:
        fields["usp"] = description[:280]

    house_rules_list = record.get("house_rules") or []
    if isinstance(house_rules_list, list) and house_rules_list:
        fields["house_rules"] = "\n".join(str(r) for r in house_rules_list)
        for rule in house_rules_list:
            lower = str(rule).lower()
            if "check-in" in lower or "checkin" in lower or "check in" in lower:
                parsed = _to_24h(str(rule))
                if parsed:
                    fields["check_in_time"] = parsed
            elif "check-out" in lower or "checkout" in lower or "check out" in lower:
                parsed = _to_24h(str(rule))
                if parsed:
                    fields["check_out_time"] = parsed

    location_details = record.get("location_details")
    if location_details:
        fields["neighborhood_info"] = _strip_html(record.get("location_details_html") or location_details)

    amenity_names: list[str] = []
    for group in record.get("amenities") or []:
        for item in group.get("items") or []:
            item_name = item.get("name")
            if item_name:
                amenity_names.append(item_name)
    if amenity_names:
        fields["amenities"] = amenity_names
        fields["amenity_tags"] = canonicalize_amenities(amenity_names)

    guests = record.get("guests")
    try:
        if guests is not None:
            fields["max_guests"] = int(guests)
    except (TypeError, ValueError):
        pass

    # `images` is Bright Data's list field (a0.muscache.com URLs); `image`
    # is sometimes present as a single cover-photo string instead/as well
    # -- fall back to it so a listing with only one photo still gets it.
    image_urls = record.get("images")
    if not isinstance(image_urls, list) or not image_urls:
        single = record.get("image")
        image_urls = [single] if single else []
    image_urls = [str(u) for u in image_urls if u]
    if image_urls and photo_folder:
        fields["photos"] = await cloudinary_client.upload_images_from_urls(image_urls, folder=photo_folder)

    faq_entries: list[dict[str, str]] = []
    if description:
        faq_entries.append(
            {"question": "Tell me about this property", "answer": description, "category": "description"}
        )
    if location_details:
        faq_entries.append(
            {
                "question": "What's the neighbourhood like?",
                "answer": _strip_html(record.get("location_details_html") or location_details),
                "category": "neighbourhood",
            }
        )
    if record.get("is_guest_favorite") or record.get("is_supperhost"):
        faq_entries.append(
            {
                "question": "Is this property a Guest Favorite on Airbnb?",
                "answer": "Yes, this property is recognized as a Guest Favorite on Airbnb.",
                "category": "reputation",
            }
        )

    return {"fields": fields, "faq_entries": faq_entries}
