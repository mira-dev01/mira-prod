"""Daily "smart pricing" refresh -- compares each property's rate against
live comparable Airbnb listings in the same city via SearchApi.io (see
app/integrations/searchapi_client.py), for the host's own reference. Wired
into main.py's scheduler to run once a day; deliberately never feeds into
pricing_engine's get_pricing/negotiate_rate math automatically.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from statistics import median

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.integrations.searchapi_client import (
    SearchApiError,
    fetch_comparable_nightly_rates,
    fetch_nightly_rate,
    fetch_property_coordinates,
)
from app.models.property import Property

logger = logging.getLogger(__name__)

# Kept deliberately small -- each night in the window is its own SearchApi
# call, once per distinct listing, every day. A full month (30) would be
# ~4x the request cost for diminishing benefit, since most real guest
# pricing questions are about near-term dates. Anything a guest asks about
# outside this window still gets a live per-call fetch (see
# pricing_engine.calculate_price) -- this only removes that latency for the
# common case.
LIVE_PRICING_CACHE_WINDOW_DAYS = 7


async def refresh_smart_pricing(db: AsyncSession) -> dict[str, int]:
    """One SearchApi.io call per distinct city (not per property) to stay
    frugal against the free-tier request allowance. Scoped to
    exact_airbnb_pricing properties only -- not every host is on Airbnb
    Smart Pricing, and SearchApi shouldn't be spent on properties that
    never use its results (pricing_engine.calculate_price only ever
    consults SearchApi for exact_airbnb_pricing properties either way).
    Returns a small summary dict for logging: {"cities_queried": n,
    "properties_updated": n, "errors": n}."""
    if not settings.searchapi_api_key:
        return {"cities_queried": 0, "properties_updated": 0, "errors": 0}

    properties = list(
        (
            await db.scalars(
                select(Property).where(Property.city.is_not(None), Property.exact_airbnb_pricing.is_(True))
            )
        ).all()
    )
    by_city: dict[str, list[Property]] = {}
    for property_ in properties:
        by_city.setdefault(property_.city, []).append(property_)

    # A fixed few-days-out window, re-queried daily -- comparable listings'
    # prices shift with lead time same as the host's own would, so "always
    # check ~2 weeks out" keeps the comparison apples-to-apples day over day
    # rather than drifting toward last-minute or far-future rates.
    check_in = date.today() + timedelta(days=14)
    check_out = check_in + timedelta(days=2)

    properties_updated = 0
    errors = 0
    for city, city_properties in by_city.items():
        try:
            nightly_rates = await fetch_comparable_nightly_rates(city, check_in, check_out)
        except SearchApiError as e:
            logger.warning("Smart pricing fetch failed for city=%s: %s", city, e)
            errors += 1
            continue

        if not nightly_rates:
            continue

        estimate = round(median(nightly_rates), 2)
        for property_ in city_properties:
            property_.smart_price_estimate = estimate
            property_.smart_price_sample_size = len(nightly_rates)
            property_.smart_price_updated_at = datetime.now(timezone.utc)
            properties_updated += 1

    await db.commit()
    logger.info(
        "Smart pricing refresh: %d cities queried, %d properties updated, %d errors",
        len(by_city),
        properties_updated,
        errors,
    )
    return {"cities_queried": len(by_city), "properties_updated": properties_updated, "errors": errors}


async def refresh_live_pricing_cache(db: AsyncSession, days_ahead: int = LIVE_PRICING_CACHE_WINDOW_DAYS) -> dict[str, int]:
    """Pre-fetches and Redis-caches each exact_airbnb_pricing property's
    live per-night rate for a rolling near-term window, so a real guest
    call asking about one of those dates is answered from cache
    (pricing_engine.calculate_price._sum_cached_nightly_rates) instead of
    paying live-fetch latency mid-call. Distinct from refresh_smart_pricing
    above -- that's a once-daily informational reference number for an
    entire city; this is the actual data get_pricing/negotiate_rate/
    check_calendar consult for guest-facing quotes.

    Deduped by airbnb_listing_id, not per Property row -- the same Airbnb
    listing is sometimes imported under multiple Property rows for the
    same host (confirmed live), and there's nothing to gain re-fetching an
    identical listing's price twice. Coordinates get backfilled onto every
    duplicate row in the group either way, so none are left stale.

    Returns a small summary dict for logging: {"listings_queried": n,
    "nights_cached": n, "errors": n}."""
    if not settings.searchapi_api_key:
        return {"listings_queried": 0, "nights_cached": 0, "errors": 0}

    properties = list(
        (
            await db.scalars(
                select(Property).where(
                    Property.exact_airbnb_pricing.is_(True), Property.airbnb_listing_id.is_not(None)
                )
            )
        ).all()
    )
    by_listing_id: dict[str, list[Property]] = {}
    for property_ in properties:
        by_listing_id.setdefault(property_.airbnb_listing_id, []).append(property_)

    nights_cached = 0
    errors = 0
    for listing_id, listing_properties in by_listing_id.items():
        coords = next(
            (
                (p.airbnb_latitude, p.airbnb_longitude)
                for p in listing_properties
                if p.airbnb_latitude is not None and p.airbnb_longitude is not None
            ),
            None,
        )
        if coords is None:
            coords = await fetch_property_coordinates(listing_id)
            if coords is None:
                errors += 1
                continue

        for property_ in listing_properties:
            if property_.airbnb_latitude is None or property_.airbnb_longitude is None:
                property_.airbnb_latitude, property_.airbnb_longitude = coords
        await db.commit()

        latitude, longitude = float(coords[0]), float(coords[1])
        for i in range(days_ahead):
            # A None result here just means this specific night isn't
            # bookable/found (SearchApi only returns available listings for
            # the dates queried) or a transient request failure -- not
            # counted as an "error" in the summary below, since an
            # unavailable night is an expected, normal outcome, not a bug.
            # pricing_engine falls back to a live per-range fetch (and then
            # to base_price) for any night this leaves uncached.
            night = date.today() + timedelta(days=i)
            rate = await fetch_nightly_rate(latitude, longitude, listing_id, night)
            if rate is not None:
                nights_cached += 1

    logger.info(
        "Live pricing cache refresh: %d listings queried, %d nights cached, %d errors",
        len(by_listing_id),
        nights_cached,
        errors,
    )
    return {"listings_queried": len(by_listing_id), "nights_cached": nights_cached, "errors": errors}
