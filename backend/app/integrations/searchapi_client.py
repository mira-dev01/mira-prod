"""SearchApi.io's Airbnb engines -- three things live here:

1. fetch_comparable_nightly_rates: city-wide comparable-listing pricing,
   feeding app/services/smart_pricing_service.py's daily reference-number
   job ("what do nearby listings charge").
2. fetch_property_coordinates + fetch_listing_total_price: THIS exact
   listing's own live price for specific dates, feeding
   pricing_engine.calculate_price's exact_airbnb_pricing branch ("what does
   Airbnb actually show for my own listing today"). SearchApi's airbnb
   engine has no query parameter that filters by listing name/title (only
   location), and its dedicated airbnb_property by-ID engine returns rich
   metadata but no pricing at all -- so this is a two-step lookup: fetch the
   listing's GPS coordinates once (cached on Property, they don't change),
   then run a location search with a tight bounding_box around those
   coordinates to reliably surface that one listing with a real price.
3. fetch_nightly_rate: same bounding_box lookup as #2, but for a single
   night -- feeds smart_pricing_service's daily near-term cache-warm job
   (a rolling 7-day window of per-night rates, so a live guest call for a
   near-term date is answered from cache instead of paying the live-fetch
   latency mid-call; see pricing_engine.calculate_price and
   redis_client.py's key scheme).

Docs: https://www.searchapi.io/airbnb-api.
"""

import json
from datetime import date, timedelta

import httpx

from app.config import settings
from app.integrations import redis_client

_BASE_URL = "https://www.searchapi.io/api/v1/search"

# Cache TTLs, all well inside SearchApi's free-tier request allowance
# window -- see redis_client.py. Neither caches fetch_property_coordinates:
# that's already cached indefinitely on Property.airbnb_latitude/longitude
# (a listing's location never changes), so a second cache layer there would
# be pure overhead.
_COMPARABLE_RATES_CACHE_TTL_SECONDS = 24 * 60 * 60  # matches the daily refresh cadence
_LISTING_PRICE_CACHE_TTL_SECONDS = 60 * 60  # live per-call fetch -- short enough to stay fresh for a guest asking
# Slightly over a day -- the nightly-rate cache-warm job re-fetches the
# whole rolling window once every 24h anyway, this just avoids a narrow gap
# where yesterday's entry has already expired an hour before today's job runs.
_NIGHTLY_RATE_CACHE_TTL_SECONDS = 25 * 60 * 60


class SearchApiError(Exception):
    """Raised for any non-2xx response or unexpected shape from SearchApi.io."""


async def fetch_comparable_nightly_rates(
    city: str, check_in: date, check_out: date, adults: int = 2, timeout: float = 15.0
) -> list[float]:
    """Returns nightly INR rates for listings SearchApi.io's Airbnb engine
    returns for `city` over [check_in, check_out). Best-effort: listings
    missing a parseable price are skipped rather than raising. Cached in
    Redis (if configured) for _COMPARABLE_RATES_CACHE_TTL_SECONDS -- repeat
    calls for the same city/date pair within that window (e.g. re-running
    the daily refresh) are served from cache rather than spending another
    request against the free-tier allowance."""
    if not settings.searchapi_api_key:
        raise SearchApiError("SEARCHAPI_API_KEY is not configured")

    cache_key = f"searchapi:comparable:{city}:{check_in.isoformat()}:{check_out.isoformat()}:{adults}"
    cached = await redis_client.cache_get_json(cache_key)
    if cached is not None:
        return cached

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

    await redis_client.cache_set_json(cache_key, nightly_rates, _COMPARABLE_RATES_CACHE_TTL_SECONDS)
    return nightly_rates


async def fetch_property_coordinates(listing_id: str, timeout: float = 15.0) -> tuple[float, float] | None:
    """GPS coordinates for one specific Airbnb listing, via SearchApi's
    dedicated airbnb_property engine (a direct by-ID lookup, not a location
    search). A listing's coordinates don't change, so callers cache this
    once on Property.airbnb_latitude/longitude rather than re-fetching --
    see pricing_engine.calculate_price. Confirmed live: this engine returns
    rich listing metadata (title, amenities, host, gps_coordinates) but
    genuinely no pricing field at all, dates included -- it's a metadata-only
    endpoint, which is exactly why this only fetches coordinates and
    fetch_listing_total_price below does the actual price lookup separately.
    Returns None (never raises) on any failure or missing coordinates."""
    if not settings.searchapi_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                _BASE_URL,
                params={
                    "engine": "airbnb_property",
                    "property_id": listing_id,
                    "api_key": settings.searchapi_api_key,
                },
            )
            if response.status_code >= 400:
                return None
            data = response.json()
    except httpx.HTTPError:
        return None

    coords = (data.get("property") or {}).get("gps_coordinates") or {}
    lat, lng = coords.get("latitude"), coords.get("longitude")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        return float(lat), float(lng)
    return None


# ~110m in each direction -- tight enough that a dense area still returns a
# small, specific page of listings (confirmed live: 8-20 results, vs. 20
# generic/irrelevant results for a plain city search), wide enough to be
# robust to SearchApi's own geocoding not being pixel-exact.
_LISTING_BOUNDING_BOX_DELTA_DEGREES = 0.001


async def _fetch_listing_price_uncached(
    latitude: float, longitude: float, listing_id: str, check_in: date, check_out: date, adults: int, timeout: float
) -> float | None:
    """Shared bounding_box lookup behind fetch_listing_total_price and
    fetch_nightly_rate below -- no caching here, each caller applies its own
    cache key/TTL since they cache at different granularities (a whole
    requested range vs. one night). Returns None (never raises) if the
    listing isn't found for these dates (including genuinely unavailable --
    see fetch_listing_total_price's docstring) or on any request failure."""
    if not settings.searchapi_api_key:
        return None

    delta = _LISTING_BOUNDING_BOX_DELTA_DEGREES
    bounding_box = [[latitude + delta, longitude + delta], [latitude - delta, longitude - delta]]
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                _BASE_URL,
                params={
                    "engine": "airbnb",
                    "bounding_box": json.dumps(bounding_box),
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


async def fetch_listing_total_price(
    latitude: float,
    longitude: float,
    listing_id: str,
    check_in: date,
    check_out: date,
    adults: int = 2,
    timeout: float = 15.0,
) -> float | None:
    """Live total price for ONE specific Airbnb listing over [check_in,
    check_out), for hosts on Airbnb's own Smart Pricing (rate changes daily/
    per-date, so no static Property.base_price can stay accurate -- see
    pricing_engine.calculate_price's exact_airbnb_pricing branch).

    A plain city-wide search (q=<city>) returns a generic, unfiltered page
    of listings and this exact listing is frequently not on it at all --
    confirmed live, a real 20-listing Colva search never included a real
    Colva listing being priced. A tight bounding_box around the listing's
    own coordinates (Property.airbnb_latitude/longitude, from
    fetch_property_coordinates above) does reliably include it, confirmed
    live across multiple date ranges -- as long as the listing is actually
    bookable for the requested dates (Airbnb's search only returns
    available listings for the given dates, same as searching on
    airbnb.com directly; an unavailable listing legitimately won't appear
    for those dates regardless of how tight the search is, and this
    correctly falls back to Property.base_price rather than treating that
    as an error).

    Returns None (never raises) if the listing isn't found for these dates
    or on any request failure -- callers fall back to Property.base_price
    rather than blocking a live pricing quote on this. Cached in Redis (if
    configured) for _LISTING_PRICE_CACHE_TTL_SECONDS -- get_pricing and
    negotiate_rate are both commonly called back-to-back for the same
    property/dates within one call, and this avoids spending the free-tier
    allowance twice on what's effectively the same question. A None result
    (not found/unavailable) is deliberately NOT cached -- that's usually a
    transient rate-limit/availability state, not worth remembering for an
    hour and blocking a later, possibly-successful lookup.

    pricing_engine.calculate_price tries a cache of pre-fetched nightly
    rates first (fetch_nightly_rate below, via the daily cache-warm job) and
    only falls through to this live per-range call when that cache doesn't
    fully cover the requested dates -- see calculate_price for that ordering.
    """
    cache_key = f"searchapi:listing_price:{listing_id}:{check_in.isoformat()}:{check_out.isoformat()}:{adults}"
    cached = await redis_client.cache_get_json(cache_key)
    if cached is not None:
        return cached

    price = await _fetch_listing_price_uncached(latitude, longitude, listing_id, check_in, check_out, adults, timeout)
    if price is not None:
        await redis_client.cache_set_json(cache_key, price, _LISTING_PRICE_CACHE_TTL_SECONDS)
    return price


def nightly_rate_cache_key(listing_id: str, night: date) -> str:
    """Shared with pricing_engine.calculate_price, which reads these same
    keys directly (rather than calling fetch_nightly_rate, which would
    trigger a live fetch on a cache miss -- calculate_price wants to know
    definitively whether the cache covers a request, not fetch live for
    each missing night one at a time)."""
    return f"searchapi:nightly_rate:{listing_id}:{night.isoformat()}"


async def fetch_nightly_rate(
    latitude: float, longitude: float, listing_id: str, night: date, adults: int = 2, timeout: float = 15.0
) -> float | None:
    """This listing's live rate for ONE specific night, via the same
    bounding_box lookup as fetch_listing_total_price above (queried as a
    1-night stay, so the returned total IS the nightly rate). Only called
    from smart_pricing_service's daily cache-warm job, which pre-fetches a
    rolling window of near-term nights so a live guest call for one of
    those dates can be answered from cache -- see
    pricing_engine.calculate_price. Cached under nightly_rate_cache_key
    for _NIGHTLY_RATE_CACHE_TTL_SECONDS; a None result (unavailable that
    night) is not cached, same reasoning as fetch_listing_total_price."""
    cache_key = nightly_rate_cache_key(listing_id, night)
    cached = await redis_client.cache_get_json(cache_key)
    if cached is not None:
        return cached

    price = await _fetch_listing_price_uncached(
        latitude, longitude, listing_id, night, night + timedelta(days=1), adults, timeout
    )
    if price is not None:
        await redis_client.cache_set_json(cache_key, price, _NIGHTLY_RATE_CACHE_TTL_SECONDS)
    return price
