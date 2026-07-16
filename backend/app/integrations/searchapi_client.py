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
