# Pricing, Negotiation, Lead Qualification & Airbnb Import

Covers `backend/app/services/pricing_engine.py`, `discount_policy_service.py`, `lead_service.py`, `airbnb_import.py`, and `app/integrations/bright_data_client.py`. See [database.md](database.md) for the underlying tables (`pricing_rules`, `host_discount_rules`, `leads`) and [agents.md](agents.md) for how the voice agent's `get_pricing`/`negotiate_rate`/`update_lead` tools call into this.

## Price calculation (`pricing_engine.calculate_price`)

Entry point for the `get_pricing` tool. Returns a `PriceBreakdown` dataclass (`nights`, `base_total`, `weekend_nights`, `cleaning_fee`, `tax_amount`, `discount_percent`, `discount_amount`, `total`, `per_night_avg`).

1. **Nightly rate**: `_nightly_rate(base_price, day)` — `Property.base_price × WEEKEND_SURGE_MULTIPLIER` (config `weekend_surge_multiplier`, default `1.2`) for Friday/Saturday/Sunday nights (`WEEKEND_WEEKDAYS = {4, 5, 6}`), otherwise the plain base price. Summed per night across the stay → `base_total`.
2. **Length-of-stay discount** (only if `apply_discounts=True`): `_length_of_stay_discount_percent` queries active `PricingRule` rows for the property with `rule_type="length_of_stay"`, taking the max `discount_percent` among rules whose `condition["min_nights"] <= nights`.
3. **Cleaning fee**: flat `settings.default_cleaning_fee_inr` (default ₹800), added after the discount.
4. **Tax**: `settings.default_tax_percent` (default 12%) applied to `(base_total - discount_amount + cleaning_fee)`.
5. **Total**: `base_total - discount_amount + cleaning_fee + tax_amount`.

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
