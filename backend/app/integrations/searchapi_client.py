"""SearchApi.io's Airbnb engine client -- comparable-listing pricing for a
city/date range, feeding app/services/smart_pricing_service.py.

Docs: https://www.searchapi.io/airbnb-api. One GET per (city, check_in,
check_out) tuple returns a page of live Airbnb listings for that search,
each with a `price` object. There's no per-property "what should THIS
listing charge" endpoint -- "smart pricing" here means comparing a host's
rate against what's actually listed nearby for the same dates, same as
Airbnb's own Smart Pricing feature does internally.
"""

from datetime import date

import httpx

from app.config import settings

_BASE_URL = "https://www.searchapi.io/api/v1/search"


class SearchApiError(Exception):
    """Raised for any non-2xx response or unexpected shape from SearchApi.io."""


async def fetch_comparable_nightly_rates(
    city: str, check_in: date, check_out: date, adults: int = 2, timeout: float = 15.0
) -> list[float]:
    """Returns nightly INR rates for listings SearchApi.io's Airbnb engine
    returns for `city` over [check_in, check_out). Best-effort: listings
    missing a parseable price are skipped rather than raising."""
    if not settings.searchapi_api_key:
        raise SearchApiError("SEARCHAPI_API_KEY is not configured")

    nights = max((check_out - check_in).days, 1)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            _BASE_URL,
            params={
                "engine": "airbnb",
                "q": city,
                "check_in_date": check_in.isoformat(),
                "check_out_date": check_out.isoformat(),
                "adults": adults,
                # Defaults to USD otherwise -- confirmed live (a Goa search
                # came back as "$155", silently treated as a rupee figure
                # until this was added, producing nonsense ~₹50-100/night
                # "smart prices"). MIRA is India-only, so always request INR.
                "currency": "INR",
                "api_key": settings.searchapi_api_key,
            },
        )
        if response.status_code >= 400:
            raise SearchApiError(f"search failed ({response.status_code}): {response.text}")
        data = response.json()

    listings = data.get("properties") or data.get("listings") or []
    nightly_rates: list[float] = []
    for listing in listings:
        price = listing.get("price") or {}
        # extracted_total_price is the numeric total for the whole stay;
        # divide by nights to get a comparable per-night rate. Some
        # responses instead carry a pre-split per-night figure under
        # price_per_qualifier -- prefer the total/nights math when both are
        # present since it's less ambiguous about what "qualifier" means.
        total = price.get("extracted_total_price")
        if isinstance(total, (int, float)) and total > 0:
            nightly_rates.append(round(total / nights, 2))
            continue
        per_night = price.get("extracted_price_per_qualifier") or price.get("extracted_rate")
        if isinstance(per_night, (int, float)) and per_night > 0:
            nightly_rates.append(round(per_night, 2))

    return nightly_rates


async def fetch_listing_total_price(
    city: str, listing_id: str, check_in: date, check_out: date, adults: int = 2, timeout: float = 15.0
) -> float | None:
    """Live total price for ONE specific Airbnb listing over [check_in,
    check_out), for hosts on Airbnb's own Smart Pricing (rate changes daily/
    per-date, so no static Property.base_price can stay accurate -- see
    pricing_engine.calculate_price's exact_airbnb_pricing branch). There's no
    per-listing pricing endpoint, so this runs the same city search as
    fetch_comparable_nightly_rates and picks out the one listing matching
    `listing_id` (Property.airbnb_listing_id, captured at Bright Data import
    time). Returns None (never raises) if the listing isn't found in that
    page of results or on any request failure -- callers fall back to
    Property.base_price rather than blocking a live pricing quote on this."""
    if not settings.searchapi_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                _BASE_URL,
                params={
                    "engine": "airbnb",
                    "q": city,
                    "check_in_date": check_in.isoformat(),
                    "check_out_date": check_out.isoformat(),
                    "adults": adults,
                    "currency": "INR",
                    "api_key": settings.searchapi_api_key,
                },
            )
            if response.status_code >= 400:
                return None
            data = response.json()
    except httpx.HTTPError:
        return None

    listings = data.get("properties") or data.get("listings") or []
    match = next((listing for listing in listings if str(listing.get("id")) == str(listing_id)), None)
    if match is None:
        return None
    total = (match.get("price") or {}).get("extracted_total_price")
    return float(total) if isinstance(total, (int, float)) and total > 0 else None
