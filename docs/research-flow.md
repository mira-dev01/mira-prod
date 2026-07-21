# Pricing, Negotiation, Lead Qualification & Airbnb Import

Covers `backend/app/services/pricing_engine.py`, `discount_policy_service.py`, `lead_service.py`, `airbnb_import.py`, `smart_pricing_service.py`, and `app/integrations/bright_data_client.py`/`searchapi_client.py`. See [database.md](database.md) for the underlying tables (`pricing_rules`, `host_discount_rules`, `leads`, `properties`' smart-pricing columns) and [agents.md](agents.md) for how the voice agent's `get_pricing`/`negotiate_rate`/`update_lead` tools call into this.

## Price calculation (`pricing_engine.calculate_price`)

Entry point for the `get_pricing` tool. Returns a `PriceBreakdown` dataclass (`nights`, `base_total`, `discount_percent`, `discount_amount`, `total`, `per_night_avg`). No synthetic markup (no weekend surge, cleaning fee, or tax) for any property, on or off `exact_airbnb_pricing` — every property quotes its stored rate (or a live Airbnb fetch, see below) as-is.

0. **Live exact-price fetch** (only if `Property.exact_airbnb_pricing` is true and `airbnb_listing_id` is set): before the flat-rate math below, resolves this exact listing's live price for the guest's actual requested dates.
   - **Cache-first for near-term dates**: `_sum_cached_nightly_rates(listing_id, check_in, check_out)` checks Redis for a per-night rate covering *every* night in the requested range (`searchapi_client.nightly_rate_cache_key`, `~25h` TTL). If the whole range is covered, this is instant and costs zero live API calls — see the daily pre-warm job below. If even one night in the range is missing from cache, this returns `None` and falls through to a live fetch (never quotes a partial/incomplete total).
   - **Live fetch, only for dates outside the cached window** — a two-step SearchApi lookup (`searchapi_client.py`):
     - **Coordinates**, cached permanently on `Property.airbnb_latitude`/`airbnb_longitude` once resolved (a listing's location doesn't change) — `fetch_property_coordinates(listing_id)` calls SearchApi's dedicated `airbnb_property` engine, a direct by-listing-ID lookup. That engine returns rich metadata (title, amenities, host, GPS) but no pricing at all, confirmed live across multiple checks — it exists purely to get an exact fix on where this listing is. Costs 1 SearchApi credit, but only ever the first time for a given listing.
     - **Price** — `fetch_listing_total_price(lat, lng, listing_id, check_in, check_out)` runs a `bounding_box` search (±0.001° around the cached coordinates) on SearchApi's `airbnb` search engine and picks out the matching listing's price for those exact dates. A plain city-wide search (`q=<city>`) does **not** reliably surface one specific listing — confirmed live, a real 20-listing city search never included the specific listing being priced, and even a much more specific query string didn't change the results (the `q` param geocodes a location, it isn't a title/keyword filter). The tight bounding box does, reliably, across multiple real date ranges tested. Also Redis-cached (`_LISTING_PRICE_CACHE_TTL_SECONDS`, 1h) so a second question about the same out-of-window dates within that hour is free.
   - On any failure — no coordinates resolvable, listing not bookable for these exact dates (Airbnb's search only returns available listings, same as searching directly on airbnb.com), SearchApi rate-limited/down, no API key configured — falls straight through to the flat `base_price` math below. Never blocks a quote on this succeeding.

   **Daily pre-warm job** (`smart_pricing_service.refresh_live_pricing_cache`, scheduled in `main.py` — see [architecture.md](architecture.md)): once a day, fetches and Redis-caches every `exact_airbnb_pricing` property's per-night rate for a rolling `LIVE_PRICING_CACHE_WINDOW_DAYS = 7`-day window, deduped by `airbnb_listing_id` (the same Airbnb listing is sometimes imported under multiple `Property` rows for the same host — confirmed live — no reason to fetch an identical listing's price twice). This is what makes the cache-first branch above actually hit for the common case (a guest asking about the next week), rather than every pricing question paying live-fetch latency mid-call. Runs on an unconditional cron (`app/main.py`, 1:15 UTC daily) regardless of whether `REDIS_URL` is set — see credit accounting below.

   **✅ Live in production as of 2026-07-21** (Upstash) — see `project_state.md` for the verification (a real cache-miss-then-hit pricing quote against production, 5.1s → 1.6s, confirmed key written to Redis).

   **SearchApi credit accounting** (confirmed by reading `searchapi_client.py`, not estimated):
   - **A Redis cache hit costs 0 SearchApi credits** — pure Redis `GET`, SearchApi is never called.
   - **A live fetch (cache miss) costs 1 credit** — the `airbnb` bounding-box price lookup — *once coordinates are already resolved* for that listing (they're cached permanently on `Property.airbnb_latitude`/`airbnb_longitude` after the first successful resolution, never re-fetched).
   - **The very first-ever live fetch for a given property costs 2 credits**: 1 for the one-time `airbnb_property` coordinate lookup + 1 for the price lookup.
   - **The daily pre-warm job costs ~7 credits per `exact_airbnb_pricing` property per day** (1 credit × `LIVE_PRICING_CACHE_WINDOW_DAYS` nights, coordinates already resolved by then). This cost is **not new** — the job has run on its unconditional cron since it was built, spending these credits even while `REDIS_URL` was unset and every result was silently discarded (`cache_set_json` no-op). Now that Redis is live, the same daily spend actually populates the cache instead of being wasted, and live guest pricing questions for dates inside the 7-day window drop to 0 credits instead of costing 1-2 each. Net effect: total daily SearchApi spend is roughly unchanged, but it no longer scales with guest call volume for near-term dates.
1. **Base total** (skipped if step 0 succeeded): `Property.base_price × nights`, flat, no surge.
2. **Length-of-stay discount** (only if `apply_discounts=True`): `_length_of_stay_discount_percent` queries active `PricingRule` rows for the property with `rule_type="length_of_stay"`, taking the max `discount_percent` among rules whose `condition["min_nights"] <= nights`. Applies regardless of `exact_airbnb_pricing`/live-fetch — this is a host-configured negotiation lever, not markup.
3. **Total**: `base_total - discount_amount`.

`negotiate_rate` and `check_calendar` call `calculate_price`/share this same live-fetch path, so they inherit it automatically — no separate wiring needed there.

### `exact_airbnb_pricing` — why it exists

Per-property toggle (property edit dialog, "Quote live Airbnb Smart Pricing"; `PropertyUpdate.exact_airbnb_pricing`) for hosts on Airbnb's own Smart Pricing, whose real rate changes daily/per-date — a static `Property.base_price` can't represent that. When on: `calculate_price` fetches and quotes this listing's actual live Airbnb price for the guest's requested dates (step 0 above) instead of the stored `base_price`. It also gates whether SearchApi is used for this property at all — both this live per-listing lookup and the city-comparable daily job below are scoped to `exact_airbnb_pricing == True` properties, since not every host is on Airbnb Smart Pricing and SearchApi's free-tier request allowance is small. `base_price` is kept as the same-call fallback value, not deleted — it's what gets quoted on any live-fetch failure, so it should still be kept roughly current.

## Smart pricing (comparable market reference, `smart_pricing_service.py`)

Separate from both `exact_airbnb_pricing`'s live per-listing fetch above **and** the nightly-rate cache-warm job described in that same section (`refresh_live_pricing_cache`, also in this file) — this is a **daily, city-wide, informational** number, not a live per-call fetch, and never feeds into `calculate_price`.

- `refresh_smart_pricing(db)`, scheduled via APScheduler in `app/main.py` (`smart_pricing_refresh`, cron `hour=1, minute=0` UTC ≈ 6:30am IST): one `searchapi_client.fetch_comparable_nightly_rates(city, check_in, check_out)` call per **distinct city**, scoped to `exact_airbnb_pricing == True` properties only (not per property, and not for properties that never use SearchApi anyway), for a fixed `today + 14 days`, 2-night window re-queried daily so the comparison stays apples-to-apples day over day.
- Result: `median(nightly_rates)` for that city written to every qualifying property in that city — `Property.smart_price_estimate`, `smart_price_sample_size`, `smart_price_updated_at`.
- Surfaced on the Pricing dashboard page as a "Your price vs. Market price (Airbnb)" table — reference only, host decides what to do with it.
- `SearchApi.io` requests **must** pass `currency=INR` explicitly — confirmed live that it defaults to USD otherwise, which silently produced nonsense ~₹50-150/night "smart prices" (a $150 listing read as ₹150) until this was added to both `fetch_comparable_nightly_rates` and `fetch_listing_total_price`.

`apply_discounts` defaults to `True` in the function signature, but the voice tool (`app/voice/tools.py`) always passes it explicitly, and the prompt (`GOLDEN_RULES`) mandates calling with `apply_discounts=false` first — never leading with a discounted quote.

The tool response (`tool_handlers.handle_get_pricing`) is phrased as one natural sentence with the total first, then a secondary "if asked" itemized breakdown — deliberately not read out by default (sounds like reciting a receipt on a phone call).

## Negotiation (`pricing_engine.negotiate_rate`)

Entry point for the `negotiate_rate` tool. Tier-1/Tier-3-stub rule-based negotiation — not the full PriceLabs/competitor-monitoring engine described in the product spec (that's genuinely Tier 3, weeks 13-20 scope).

1. Computes `asking_price` via `calculate_price(..., apply_discounts=False)`.
2. Resolves the host's negotiation policy (`_get_host_negotiation_policy`, see Host discount policy below). If `negotiation_allowed` is `False`, returns a refusal (`refused=True`) with no discount, and points the guest to the host.
3. Determines whether the guest is a **repeat guest**: prefers the real Guest Memory signal (`GuestProfile.total_stays >= 2`, host-scoped, via `_is_repeat_guest_for_host`) over the LLM-supplied `guest_loyalty` argument (`"returning"`/`"frequent"`); falls back to the LLM's claim only if no guest profile resolves.
4. Picks a `discount_percent`:
   - Repeat guest + a `repeat_guest_same_host` rule exists → that rule's percent.
   - Else a `guest_requests` rule exists → that rule's percent.
   - Else a hardcoded fallback: `{"new": 0, "returning": 5, "frequent": 10}[guest_loyalty] + 10`.
5. Caps at `min(policy.max_discount_percent, discount_percent)` — `policy.max_discount_percent` is either `User.max_discount_percent_override` or the global `MAX_NEGOTIATION_DISCOUNT_PERCENT` (15.0%).
6. `floor_price = asking_price × (1 - max_discount_percent / 100)`.
7. Outcome:
   - `guest_offer is None` (guest asked Mira to name a price) → propose `floor_price` directly, `accepted=True`.
   - `guest_offer >= floor_price` → accept the guest's own offer as-is.
   - Otherwise → counter with `floor_price`, `accepted=False`.

`_get_host_negotiation_policy` and `_is_repeat_guest_for_host` both fail closed to today's pre-existing global-constant behavior on any lookup error (no `host_id`, DB error, no approved rules) — `negotiate_rate` must never error, hang, or silently apply a 0%/100% discount, since it runs live mid-call.

## Host discount policy (`host_discount_rules` ↔ `pricing_rules`)

Two independent discount mechanisms that both feed into pricing, at different scopes:

- **`PricingRule`** (per-property, table `pricing_rules`) — only `rule_type="length_of_stay"` is currently read, by `calculate_price`. Host-authored directly via `POST /pricing/rules`.
- **`HostDiscountRule`** (per-host, table `host_discount_rules`) — read by `negotiate_rate` via `_get_host_negotiation_policy`, keyed by `trigger_type` (`no_ask` / `guest_requests` / `repeat_guest_same_host` / custom). Derive-on-read: `pricing_engine` looks these up by `host_id` directly rather than materializing a row per property, so editing one rule applies everywhere immediately.

`HostDiscountRule` rows are produced by `discount_policy_service.parse_discount_policy_text()` — an LLM extraction call (one-shot JSON extraction, deliberately **not** built on `app/voice/pipeline.py`'s `_build_llm()`/pipecat services, since those are wired for the streaming function-calling voice pipeline and aren't a fit for a low-frequency, single-shot REST-triggered call). Provider selection: Anthropic if configured, else Groq, else OpenRouter, else raises `DiscountPolicyParseError`. The extraction prompt instructs the model to only extract rules the host actually stated — never invent a trigger or percentage.

Every parsed rule lands with `status="pending_validation"` (`build_pending_rules`) — `negotiate_rate` only ever reads `status="approved"` rows. A host must review/approve drafts (via `PATCH /host-discount-rules/{id}`) in the dashboard's AI Training validation tab before they take effect. A parse failure raises rather than silently falling back to inventing a rule.

## Lead qualification (`lead_service.py`)

`lead_service.py` does **not** implement scoring/qualification logic itself — that logic lives entirely in the prompt (`LEAD_AGENT_INSTRUCTIONS` in `app/prompts/system_prompt.py`, see [agents.md](agents.md)): the LLM decides `lead_temperature` (hot/warm/cold) based on whether travel dates are finalized and a specific property is chosen, and calls `update_lead` to persist it. `lead_service.py` is purely the CRM persistence layer the `update_lead`/`escalate_to_host` tools call into:

- **`upsert_lead(db, user_id, call_session_id, **fields)`** — finds an existing `Lead` by `call_session_id` (unique per session) or creates one; applies only the non-`None` fields passed. Called repeatedly through a single call as the agent learns more (silent, not narrated to the guest).
- **`backfill_lead(db, call_session_id, **fields)`** — call-teardown only. Fills only currently-blank fields (`not getattr(lead, key)`) on an *existing* lead — the caller's phone (from Exotel) and the discussed property name. Never creates a lead, and never overwrites anything the guest actually stated during the call.
- **`delete_if_empty(db, call_session_id)`** — safety net for a lead created by a stray tool call on a connection that never became a real conversation; deletes it if none of `guest_name`/`phone`/`email`/`check_in`/`lead_temperature` are set.
- **`list_leads`/`get_owned_lead`** — read paths for the dashboard's Leads page (`GET /leads`, `GET /leads/{id}`).

Leads are deliberately **not** created up front when a call connects — see the note in `app/voice/pipeline.py`'s `_run_pipeline` — to avoid phantom "unknown guest" rows from browser/ICE reconnects. A lead only comes into existence from real qualification data during the call.

## Airbnb import: two paths, one convergence point

Both import paths ultimately call `_upsert_property_from_parsed()` in `app/api/v1/properties.py` — matches an existing `Property` by `(user_id, airbnb_listing_id)` (update) or creates a new one, then syncs FAQ entries (`faq_service.sync_imported_faq_entries`, replacing exactly the auto-generated FAQ categories: `AUTO_FAQ_CATEGORIES = {"layout", "neighbourhood", "booking", "reputation", "description", "safety"}`, without touching any host-added FAQ entries in other categories). Each import runs its own DB session per record — a batch import in `import_properties` doesn't let one bad record poison the rest (an async SQLAlchemy session doesn't reliably recover mid-request after a rollback).

### Path 1: Bright Data URL-paste (primary, `POST /properties/import-airbnb-urls`)

`app/integrations/bright_data_client.py` drives Bright Data's Datasets v3 API (dataset `gd_ld7ll037kqy322v05`), a 3-step async flow:
1. `trigger_scrape(urls)` → `snapshot_id`
2. `get_snapshot_status(snapshot_id)` → polled (`"running"` / `"ready"` / `"failed"`)
3. `get_snapshot_data(snapshot_id)` → list of raw listing records once ready

**Important constraint**: Bright Data's Airbnb product only accepts individual **listing URLs** — there's no "discover every listing for this host" mode for Airbnb (unlike some of their other scrapers, e.g. Amazon/Instagram). The onboarding UI reflects this: the host pastes each listing URL, one per line. `BRIGHT_DATA_API_KEY` unset → `BrightDataError("BRIGHT_DATA_API_KEY is not configured")`, a clean error rather than a crash.

`airbnb_import.parse_bright_data_listing(record)` adapts Bright Data's flat JSON schema into the shared `{"fields": ..., "faq_entries": ...}` shape.

### Path 2: JSON-upload (advanced, `POST /properties/import`)

Older, manual path — the host uploads scraped listing JSON files directly (one file per listing, one per Airbnb room). `airbnb_import.parse_airbnb_listing(raw)` parses Airbnb's own nested GraphQL PDP (listing page) response shape directly: extracts name/city/amenities/house-rules/check-in-out times from deeply nested `sections`/`pdp` structures, and generates auto-FAQ entries (layout/bedroom counts, neighbourhood description, cancellation policy, etc.) via helpers like `_extract_layout_faq`, `_extract_neighbourhood_faq`, `_extract_cancellation_faq`. Defensive throughout — every lookup degrades to `None`/`[]` rather than raising, since Airbnb's schema shifts over time and scrapes vary by listing type.

### Why two separate parsers

`parse_bright_data_listing()` and `parse_airbnb_listing()` are **not a shared parser** — Bright Data's flat JSON schema is completely different from Airbnb's own nested GraphQL PDP shape the JSON-upload path expects. They only converge downstream, at `_upsert_property_from_parsed()`, once both have produced the same `{"fields": ..., "faq_entries": ...}` output shape.

The Properties page in the dashboard surfaces both: **"Import from Airbnb"** (Bright Data URL-paste, primary) and **"Import from file (advanced)"** (older JSON-upload, kept for power users who already have a scrape file).
