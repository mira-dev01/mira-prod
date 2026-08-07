from datetime import date, timedelta

from app.integrations import redis_client
from app.integrations.searchapi_client import nightly_rate_cache_key
from app.models.pricing_rule import PricingRule
from app.models.negotiation_rule import NegotiationRule
from app.services.pricing_engine import calculate_price, minimum_stay_nights_violation, negotiate_rate


class _FakeRedis:
    """Same in-memory fake as tests/test_redis_client.py -- avoids needing
    a real Redis server just to test the cache-first branch in
    calculate_price."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value


def _install_fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(redis_client, "_client", fake)
    monkeypatch.setattr(redis_client, "_client_initialized", True)
    return fake


def _next_weekday(start: date, weekday: int) -> date:
    """weekday: Monday=0 ... Sunday=6"""
    delta = (weekday - start.weekday()) % 7
    return start + timedelta(days=delta)


async def test_flat_rate_no_weekend_surge(test_property, db_session):
    # No synthetic markup (weekend surge, cleaning fee, tax) for any
    # property -- base_total is always exactly base_price * nights,
    # weekday or weekend, until/unless a live Airbnb Smart Pricing fetch
    # overrides it (see test_exact_airbnb_pricing_falls_back_without_api_key
    # below for that path with no API key configured).
    monday = _next_weekday(date.today(), 0)
    friday = monday + timedelta(days=4)
    sunday = friday + timedelta(days=2)

    weekend = await calculate_price(db_session, test_property, friday, sunday)
    weekday = await calculate_price(db_session, test_property, monday, monday + timedelta(days=2))

    assert weekend.nights == 2
    assert weekend.base_total == round(float(test_property.base_price) * 2, 2)
    assert weekday.base_total == weekend.base_total


async def test_exact_airbnb_pricing_falls_back_to_base_price_without_api_key(test_property, db_session):
    # No SEARCHAPI_API_KEY configured in the test environment -- even with
    # exact_airbnb_pricing on and a listing id set, calculate_price must
    # never error or block; it falls back to the same flat base_price math
    # as every other property (see pricing_engine.calculate_price and
    # searchapi_client.fetch_property_coordinates/fetch_listing_total_price,
    # both of which return None with no key configured).
    test_property.exact_airbnb_pricing = True
    test_property.airbnb_listing_id = "123456789"
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    breakdown = await calculate_price(db_session, test_property, monday, monday + timedelta(days=2))
    assert breakdown.base_total == round(float(test_property.base_price) * 2, 2)


async def test_calculate_price_uses_fully_cached_nightly_rates_with_no_api_key(test_property, db_session, monkeypatch):
    # No SEARCHAPI_API_KEY in the test environment (same as the
    # without-api-key test above) -- proves this path is genuinely served
    # from the Redis cache alone, since a live fetch would be impossible
    # here and would otherwise fall back to base_price instead.
    _install_fake_redis(monkeypatch)
    test_property.exact_airbnb_pricing = True
    test_property.airbnb_listing_id = "123456789"
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    await redis_client.cache_set_json(nightly_rate_cache_key("123456789", monday), 4000.0, 3600)
    await redis_client.cache_set_json(nightly_rate_cache_key("123456789", monday + timedelta(days=1)), 4500.0, 3600)

    breakdown = await calculate_price(db_session, test_property, monday, monday + timedelta(days=2))
    assert breakdown.base_total == 8500.0
    assert breakdown.base_total != round(float(test_property.base_price) * 2, 2)


async def test_calculate_price_falls_back_when_nightly_cache_incomplete(test_property, db_session, monkeypatch):
    # Only ONE of the two required nights is cached -- must not quote a
    # partial total; falls through to the live-fetch path, which (no API
    # key in tests) lands on the same flat base_price as every other
    # property.
    _install_fake_redis(monkeypatch)
    test_property.exact_airbnb_pricing = True
    test_property.airbnb_listing_id = "123456789"
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    await redis_client.cache_set_json(nightly_rate_cache_key("123456789", monday), 4000.0, 3600)
    # Second night deliberately left uncached.

    breakdown = await calculate_price(db_session, test_property, monday, monday + timedelta(days=2))
    assert breakdown.base_total == round(float(test_property.base_price) * 2, 2)


async def test_length_of_stay_discount_applied(test_property, db_session):
    db_session.add(
        PricingRule(
            property_id=test_property.id,
            rule_type="length_of_stay",
            condition={"min_nights": 5},
            discount_percent=10,
        )
    )
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    six_nights_later = monday + timedelta(days=6)

    breakdown = await calculate_price(db_session, test_property, monday, six_nights_later, apply_discounts=True)
    assert breakdown.discount_percent == 10
    assert breakdown.discount_amount > 0

    no_discount = await calculate_price(db_session, test_property, monday, six_nights_later, apply_discounts=False)
    assert no_discount.discount_percent == 0


async def test_negotiate_rate_accepts_reasonable_offer(test_property, db_session):
    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)

    result = await negotiate_rate(db_session, test_property, monday, wednesday, guest_offer=999999, guest_loyalty="new")
    assert result.accepted is True


async def test_negotiate_rate_counters_lowball_offer(test_property, db_session):
    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)

    result = await negotiate_rate(db_session, test_property, monday, wednesday, guest_offer=1, guest_loyalty="new")
    assert result.accepted is False
    assert result.counter_offer > 1


# Phase 6 (Negotiation engine): NegotiationRule's stay-pricing rule types --
# host-authored, multi-property pricing rules (minimum-stay, length-of-stay
# discounts, early check-in/late checkout fees, freeform concessions). A
# host with zero approved rules must negotiate/price byte-identically to
# today -- same fail-closed guarantee established for the discount_* rule
# types in test_negotiate_rate_host_policy.py.


async def test_no_rules_configured_is_byte_identical_to_before(test_property, db_session, test_user):
    """The single most important guarantee: a host with zero
    NegotiationRule rows (every existing host, day one) sees NO change
    to calculate_price/minimum_stay_nights_violation at all."""
    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)

    breakdown = await calculate_price(db_session, test_property, monday, wednesday, host_id=test_user.id)
    assert breakdown.early_checkin_fee == 0.0
    assert breakdown.late_checkout_fee == 0.0

    violation = await minimum_stay_nights_violation(db_session, test_user.id, test_property.id, monday, wednesday)
    assert violation is None


async def test_length_of_stay_via_property_pricing_rule_applies(test_property, db_session, test_user):
    """A host-authored, multi-property length_of_stay rule (via the new
    AI-Training-tab flow) must apply the same as an existing per-property
    PricingRule -- same rule_type, second source, resolved with the same
    "take the best" logic, never double-applied."""
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="length_of_stay",
            condition={"min_nights": 5},
            discount_percent=12,
            property_ids=[str(test_property.id)],
            status="approved",
        )
    )
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    six_nights_later = monday + timedelta(days=6)

    breakdown = await calculate_price(
        db_session, test_property, monday, six_nights_later, apply_discounts=True, host_id=test_user.id
    )
    assert breakdown.discount_percent == 12


async def test_length_of_stay_pending_rule_is_not_applied(test_property, db_session, test_user):
    """Only status="approved" rows take effect -- a pending_validation draft
    (the default on parse, before host review) must never affect a live
    quote."""
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="length_of_stay",
            condition={"min_nights": 5},
            discount_percent=50,
            property_ids=[str(test_property.id)],
            status="pending_validation",
        )
    )
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    six_nights_later = monday + timedelta(days=6)

    breakdown = await calculate_price(
        db_session, test_property, monday, six_nights_later, apply_discounts=True, host_id=test_user.id
    )
    assert breakdown.discount_percent == 0


async def test_length_of_stay_rule_not_scoped_to_this_property_is_ignored(test_property, db_session, test_user):
    """A rule approved for a DIFFERENT property (not in property_ids) must
    not leak into this property's price -- confirms property_ids scoping is
    actually enforced, not just present in the schema."""
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="length_of_stay",
            condition={"min_nights": 5},
            discount_percent=50,
            property_ids=["00000000-0000-0000-0000-000000000000"],
            status="approved",
        )
    )
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    six_nights_later = monday + timedelta(days=6)

    breakdown = await calculate_price(
        db_session, test_property, monday, six_nights_later, apply_discounts=True, host_id=test_user.id
    )
    assert breakdown.discount_percent == 0


async def test_minimum_stay_nights_general_floor(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="minimum_stay_nights",
            condition={"min_nights": 3},
            property_ids=[str(test_property.id)],
            status="approved",
        )
    )
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    violation = await minimum_stay_nights_violation(
        db_session, test_user.id, test_property.id, monday, monday + timedelta(days=2)
    )
    assert violation == 3

    ok = await minimum_stay_nights_violation(
        db_session, test_user.id, test_property.id, monday, monday + timedelta(days=3)
    )
    assert ok is None


async def test_minimum_stay_nights_weekend_only_floor(test_property, db_session, test_user):
    """weekend_min_nights only applies when the stay actually includes a
    Friday or Saturday night -- a purely-weekday stay must be unaffected."""
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="minimum_stay_nights",
            condition={"weekend_min_nights": 2},
            property_ids=[str(test_property.id)],
            status="approved",
        )
    )
    await db_session.commit()

    friday = _next_weekday(date.today(), 4)
    one_weekend_night = await minimum_stay_nights_violation(
        db_session, test_user.id, test_property.id, friday, friday + timedelta(days=1)
    )
    assert one_weekend_night == 2

    monday = _next_weekday(date.today(), 0)
    purely_weekday = await minimum_stay_nights_violation(
        db_session, test_user.id, test_property.id, monday, monday + timedelta(days=1)
    )
    assert purely_weekday is None


async def test_early_checkin_fee_only_applied_when_requested(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="early_checkin_fee",
            condition={"fee": 1500},
            property_ids=[str(test_property.id)],
            status="approved",
        )
    )
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)

    not_requested = await calculate_price(db_session, test_property, monday, wednesday, host_id=test_user.id)
    assert not_requested.early_checkin_fee == 0.0

    requested = await calculate_price(
        db_session, test_property, monday, wednesday, host_id=test_user.id, requested_early_checkin=True
    )
    assert requested.early_checkin_fee == 1500.0
    # The fee must never be silently folded into total/base_total -- those
    # stay exactly the stay-price math, unaffected by the fee.
    assert requested.total == not_requested.total


async def test_malformed_condition_value_is_ignored_not_crashed_on(test_property, db_session, test_user):
    """Self-review fix: condition is a host-editable JSONB dict (directly
    PATCH-able, not just LLM-parsed) -- a non-numeric value must never crash
    calculate_price/minimum_stay_nights_violation on the live call path.
    Confirms _condition_number's fail-closed "ignore this rule" behavior for
    every rule type that reads a condition value, not just one."""
    db_session.add_all(
        [
            NegotiationRule(
                host_id=test_user.id,
                rule_type="early_checkin_fee",
                condition={"fee": "not a number"},
                property_ids=[str(test_property.id)],
                status="approved",
            ),
            NegotiationRule(
                host_id=test_user.id,
                rule_type="minimum_stay_nights",
                condition={"weekend_min_nights": "two"},
                property_ids=[str(test_property.id)],
                status="approved",
            ),
            NegotiationRule(
                host_id=test_user.id,
                rule_type="length_of_stay",
                condition={"min_nights": None},
                discount_percent=10,
                property_ids=[str(test_property.id)],
                status="approved",
            ),
        ]
    )
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)

    # Must not raise -- malformed rules are simply ignored (treated as "does
    # not apply"), same as no rule being configured at all.
    breakdown = await calculate_price(
        db_session, test_property, monday, wednesday, apply_discounts=True, host_id=test_user.id, requested_early_checkin=True
    )
    assert breakdown.early_checkin_fee == 0.0
    assert breakdown.discount_percent == 0.0

    violation = await minimum_stay_nights_violation(db_session, test_user.id, test_property.id, monday, wednesday)
    assert violation is None


async def test_late_checkout_fee_only_applied_when_requested(test_property, db_session, test_user):
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="late_checkout_fee",
            condition={"fee": 800},
            property_ids=[str(test_property.id)],
            status="approved",
        )
    )
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)

    requested = await calculate_price(
        db_session, test_property, monday, wednesday, host_id=test_user.id, requested_late_checkout=True
    )
    assert requested.late_checkout_fee == 800.0


async def test_custom_concession_never_lowers_the_existing_floor(test_property, db_session, test_user):
    """A property-scoped 'custom' concession only ever helps the guest --
    never overrides a MORE generous host-wide policy with a less generous
    property-specific one."""
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="custom",
            discount_percent=1,  # deliberately tiny -- must not lower the existing 10% floor below
            label="Tiny goodwill discount",
            property_ids=[str(test_property.id)],
            status="approved",
        )
    )
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)

    result = await negotiate_rate(
        db_session, test_property, monday, wednesday, guest_offer=1, guest_loyalty="new", host_id=test_user.id
    )
    # guest_loyalty="new" with no NegotiationRule configured resolves to
    # the existing hardcoded 10% floor (see pricing_engine.negotiate_rate) --
    # the 1% custom rule must not push it below that.
    assert result.counter_offer > 1


async def test_custom_concession_above_default_ceiling_actually_applies(test_property, db_session, test_user):
    """Self-review fix: a custom rule MORE generous than
    MAX_NEGOTIATION_DISCOUNT_PERCENT (15%, the default ceiling with no
    User.max_discount_percent_override set) must actually raise
    the ceiling itself, not just the resolved discount_percent that then
    gets re-clamped back down to 15% -- the entire point of a property-
    specific concession is to exceed the portfolio-wide default."""
    db_session.add(
        NegotiationRule(
            host_id=test_user.id,
            rule_type="custom",
            discount_percent=30,
            label="Big goodwill discount for this villa",
            property_ids=[str(test_property.id)],
            status="approved",
        )
    )
    await db_session.commit()

    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)

    result = await negotiate_rate(
        db_session, test_property, monday, wednesday, guest_offer=None, guest_loyalty="new", host_id=test_user.id
    )
    # guest_offer=None -> Mira proposes floor_price directly. At the default
    # 15% ceiling, floor_price would be asking_price * 0.85; the 30% custom
    # rule must push the actual floor below that.
    default_ceiling_floor = round(result.asking_price * 0.85, 2)
    assert result.counter_offer < default_ceiling_floor


async def test_property_pricing_rule_lookup_failure_falls_back_to_no_rules(
    test_property, db_session, test_user, monkeypatch
):
    """Same fail-closed discipline as _get_host_negotiation_policy -- a DB
    error looking up NegotiationRule (_approved_property_pricing_rules'
    own try/except) must never block/error a live call, just behave as if
    no rules were configured. Scoped to minimum_stay_nights_violation
    specifically, since it calls ONLY _approved_property_pricing_rules --
    calculate_price's own PricingRule query (a separate, pre-existing,
    already-tested code path) would also break under a blanket
    db.scalars monkeypatch, which isn't what this test is about."""
    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated DB error")

    monkeypatch.setattr(db_session, "scalars", _boom)

    monday = _next_weekday(date.today(), 0)
    wednesday = monday + timedelta(days=2)
    violation = await minimum_stay_nights_violation(db_session, test_user.id, test_property.id, monday, wednesday)
    assert violation is None
