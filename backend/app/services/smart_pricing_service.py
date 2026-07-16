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
from app.integrations.searchapi_client import SearchApiError, fetch_comparable_nightly_rates
from app.models.property import Property

logger = logging.getLogger(__name__)


async def refresh_smart_pricing(db: AsyncSession) -> dict[str, int]:
    """One SearchApi.io call per distinct city (not per property) to stay
    frugal against the free-tier request allowance. Returns a small summary
    dict for logging: {"cities_queried": n, "properties_updated": n, "errors": n}."""
    if not settings.searchapi_api_key:
        return {"cities_queried": 0, "properties_updated": 0, "errors": 0}

    properties = list((await db.scalars(select(Property).where(Property.city.is_not(None)))).all())
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
