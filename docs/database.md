# Database Schema

PostgreSQL via SQLAlchemy async (asyncpg driver). All models live in `backend/app/models/` and inherit `UUIDPkMixin` (UUID `id` primary key, `default=uuid.uuid4`) and `TimestampMixin` (`created_at`/`updated_at`, both `DateTime(timezone=True)`, server-defaulted to `now()`) from `app/models/mixins.py`. See [architecture.md](architecture.md) for the engine/session setup and [research-flow.md](research-flow.md) / [agents.md](agents.md) for how these tables are used at runtime.

## Alembic

Migrations live in `backend/alembic/versions/`. Apply with `alembic upgrade head` (run from `backend/`, needs `DATABASE_URL` resolvable).

Current head: **`6aa03c77c36f` — add airbnb_latitude/longitude to properties**.

Full history, oldest to newest:

```
<base> -> 8d937521de4c   initial schema
8d937521de4c -> 08b6a6949f77   drop vapi columns
08b6a6949f77 -> df8097774c5d   add leads and faq entries
df8097774c5d -> 750ff224edb7   add airbnb_listing_id to properties
750ff224edb7 -> 96046254326b   scope airbnb_listing_id uniqueness to per-user
96046254326b -> a1e3085944c1   add user_id to call_sessions for lead-agent call attribution
a1e3085944c1 -> 896a916936e2   add usp to properties, agent customization fields to users
896a916936e2 -> c4147f84e6a1   add neighborhood_info to properties
c4147f84e6a1 -> f3cebf679a80   add lead status/occasion and unanswered_questions
f3cebf679a80 -> d3a9f5c1b2e4   add notification_email to users
d3a9f5c1b2e4 -> e7c2a4f8d9b1   enable pg_trgm for fuzzy faq search
e7c2a4f8d9b1 -> a1b2c3d4e5f6   add host registration fields to users
a1b2c3d4e5f6 -> b7d4e6f2a913   add photos to properties
b7d4e6f2a913 -> c8e1f4a02b7d   add host memory discount policy fields and host_discount_rules table
c8e1f4a02b7d -> d4f7a91c3e5b   add guest memory fields to guest_profiles and lead.guest_profile_id
d4f7a91c3e5b -> e91a3f5c8d2b   add question_embedding to faq_entries and unanswered_questions
e91a3f5c8d2b -> f3a8c1d7e4b6   add seasonal_notes to properties
f3a8c1d7e4b6 -> baf955ef4370   add smart pricing fields to properties
baf955ef4370 -> 8818413a6d0a   add exact_airbnb_pricing to properties
8818413a6d0a -> 6aa03c77c36f   add airbnb_latitude/longitude to properties (HEAD)
```

If a session ever fails with demo-login 500s or a missing-column error, check `alembic heads` against the running DB first — a DB left behind on an old revision is a common cause (see `project_state.md` at the repo root for the 2026-07-15 incident).

## Tables

### `users` (`User`, `app/models/user.py`)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `email` | String(255), unique, not null | |
| `hashed_password` | String(255), not null | |
| `name`, `phone` | String, nullable | |
| `tier` | String(32), default `tier_1` | |
| `status` | String(32), default `active` | non-`active` users are rejected by `get_current_user` |
| `lead_exophone` | String(32), unique | dialed number that routes to the Lead Agent (portfolio-wide) |
| `business_name`, `airbnb_host_status`, `property_count_estimate` | | self-reported host registration profile, no Airbnb verification |
| `timezone` | String(64), default `Asia/Kolkata` | |
| `terms_accepted_at` | DateTime, nullable | |
| `notification_email` | String(255), nullable | overrides `email` as the escalation-email recipient when set |
| `agent_first_message`, `agent_persona`, `agent_escalation_phrase` | Text, nullable | per-host voice agent customization; `None` = Mira's default. `agent_first_message` supports `{host_name}`/`{property_name}`/`{city}`/`{guest_name}` placeholders |
| `discount_policy_text` | Text, nullable | host's free-text discount policy paragraph, re-parsed via `POST /host-discount-rules/parse` into `HostDiscountRule` rows; not itself read by the pricing engine |
| `negotiation_allowed` | Boolean, default `true` | `False` disables all negotiation for this host |
| `max_discount_percent_override` | Numeric(5,2), nullable | overrides `MAX_NEGOTIATION_DISCOUNT_PERCENT` |
| `allow_pets`, `allow_early_checkin` | Boolean, nullable | |
| `follow_up_channel_preference` | String(32), nullable | |

Relationships: `properties`, `leads`, `faq_entries`, `discount_rules` (all cascade delete-orphan).

### `properties` (`Property`, `app/models/property.py`)

Unique on `(user_id, airbnb_listing_id)`.

| Column | Type | Notes |
|---|---|---|
| `user_id` | UUID FK → users, cascade delete | |
| `name`, `city` | String | |
| `exophone` | String(32), unique | dialed number that routes to Guest Support for this one property |
| `base_price` | Numeric(10,2), default 0 | nightly rate before surge/discounts |
| `ical_url` | String(1024), nullable | for calendar sync (`app/services/calendar_service.py`) |
| `usp` | String(280), nullable | one-line distinguishing description, led with in the system prompt |
| `house_rules`, `neighborhood_info` | Text, nullable | authoritative free text for the voice agent's local-area/policy answers |
| `faq` | JSONB list, default `[]` | legacy inline FAQ (see `faq_service.search_legacy_property_faq`) — separate from the `faq_entries` table |
| `amenities` | JSONB list, default `[]` | |
| `photos` | JSONB list, default `[]` | Cloudinary-hosted URLs, re-hosted so they survive source-listing edits/removal |
| `check_in_time`, `check_out_time` | String(8), defaults `14:00`/`11:00` | |
| `max_guests` | Integer, default 4 | |
| `seasonal_notes` | JSONB list, default `[]` | `{note, start_month, end_month}` entries; surfaced in the prompt only when the current month falls in range (wraparound ranges like Nov–Feb are valid — `start_month > end_month`) |
| `airbnb_listing_id` | String(64), indexed | unique per-user, not globally; captured at Bright Data import time, also the key used to fetch this listing's live price (see below) |
| `smart_price_estimate`, `smart_price_sample_size`, `smart_price_updated_at` | Numeric/Integer/DateTime, all nullable | daily comparable-listing median for this property's city, refreshed by `smart_pricing_service.py` — informational only, shown on the Pricing dashboard page, never fed into `calculate_price` |
| `exact_airbnb_pricing` | Boolean, default `false` | when true, `pricing_engine.calculate_price` fetches this exact listing's live price for the exact requested dates (via `airbnb_listing_id` + SearchApi.io) instead of `base_price` math, and skips weekend-surge/cleaning-fee/tax markup entirely — for hosts on Airbnb Smart Pricing whose listed price is already final. See [research-flow.md](research-flow.md) |
| `airbnb_latitude`, `airbnb_longitude` | Numeric(9,6), nullable | this listing's GPS coordinates, resolved once via SearchApi's `airbnb_property` engine and cached here permanently (a listing's location doesn't change) — used to build the tight `bounding_box` search that reliably finds this exact listing for a live price fetch. `NULL` until the first `exact_airbnb_pricing` live fetch for this property succeeds |

Relationships: `owner` (User), `bookings`, `call_sessions`, `technicians`, `pricing_rules`, `notifications` — all cascade delete-orphan on the property.

### `bookings` (`Booking`, `app/models/booking.py`)

Unique on `(property_id, source_uid)`. iCal-synced calendar data — **no price field**; not a payment/booking-confirmation record.

| Column | Type | Notes |
|---|---|---|
| `property_id` | UUID FK → properties, cascade delete | |
| `guest_phone`, `guest_name` | String, nullable | |
| `check_in`, `check_out` | Date, not null | |
| `platform` | String(32), default `airbnb` | |
| `source_uid` | String(255), nullable | dedup key from the iCal feed |
| `status` | String(32), default `confirmed` | |

### `call_sessions` (`CallSession`, `app/models/call_session.py`)

| Column | Type | Notes |
|---|---|---|
| `exotel_call_id` | String(64), unique | |
| `user_id` | UUID FK → users, `SET NULL` | always set when the host is known — Lead Agent calls have `property_id=NULL` but still belong to a host; **dashboard queries must scope by `user_id`, not property ownership**, or Lead Agent calls become invisible |
| `property_id` | UUID FK → properties, `SET NULL`, nullable | `NULL` for Lead Agent calls |
| `guest_profile_id` | UUID FK → guest_profiles, `SET NULL` | |
| `lead_id` | UUID FK → leads, `SET NULL`, nullable, `use_alter` | the `Lead` this call reads/writes to — may be a lead this call created, or one **reused** from an earlier call by the same returning guest (see `lead_service._get_or_create_lead_for_call`). Deliberately a separate FK from `Lead.call_session_id` (see below) — many `call_sessions` can share one `lead_id`. `use_alter=True` because this and `Lead.call_session_id` form a circular FK between the two tables |
| `caller_number` | String(32), nullable | raw signaling-level number, or `browser-test` placeholder |
| `recording_url` | String(1024), nullable | |
| `transcript` | Text, nullable | |
| `ai_summary` | JSONB, nullable | structured, host-facing summary (`schemas/call_summary.py`'s `CallSummary` shape: booking snapshot, conversation summary, outcome, host actions, key details, missing info) generated by `call_summary_service.summarize_call` via `on_pipeline_finished` once the call ends — `NULL` until then |
| `status` | String(32), default `in_progress` | |
| `urgency` | String(16), nullable | **never written anywhere in the app** — always reads as unset; escalation urgency lives on `Notification` instead |
| `revenue_attributed` | Numeric(10,2), default 0 | **no writer anywhere in the app** — always 0; see `analytics.py`'s `pipeline_value` metric, which uses `Lead.budget` instead |
| `started_at`, `ended_at` | DateTime, nullable | |

Computed properties (not columns): `duration_minutes`, `guest_name` (prefers `Lead.guest_name`, falls back to `GuestProfile.name`), `guest_phone` (prefers `Lead.phone`, falls back to `caller_number`). Note the `property` relationship shadows Python's `@property` builtin within the class body — the module aliases `from builtins import property as python_property` to work around it.

### `leads` (`Lead`, `app/models/lead.py`)

`call_session_id` is unique — at most one lead was ever *created by* a given call session. This no longer means "the only call associated with this lead", though: a returning guest's follow-up call, while this lead is still `status` `open`/`contacted`, reuses this same row via `CallSession.lead_id` (see above) instead of creating a new one — `call_session_id` just records where it was born. Once the host marks a lead `booked`/`closed`, it's no longer reusable; the next call from that guest starts a fresh `Lead`. See `lead_service._get_or_create_lead_for_call` for the full lookup/reuse/safety-guard logic (including the name-conflict guard for a shared/family phone).

| Column | Type | Notes |
|---|---|---|
| `user_id` | UUID FK → users, cascade delete | |
| `call_session_id` | UUID FK → call_sessions, `SET NULL`, unique, nullable | the call that **originally created** this lead — never repointed afterward |
| `guest_profile_id` | UUID FK → guest_profiles, `SET NULL`, nullable | not every lead resolves to a guest profile |
| `guest_name`, `phone`, `email` | String, nullable | |
| `check_in`, `check_out` | Date, nullable | |
| `num_guests` | Integer, nullable | |
| `purpose_of_stay` | String(255), nullable | |
| `budget` | Numeric(10,2), nullable | guest-stated nightly budget — the basis for the `pipeline_value` analytics metric |
| `preferred_location` | String(255), nullable | |
| `properties_discussed`, `questions_asked`, `support_requests` | JSONB list, default `[]` | `properties_discussed` stores human-readable **names**, never raw property IDs (resolved server-side in `tool_handlers._resolve_property_names` if the model echoes a UUID) |
| `lead_temperature` | String(16), nullable | `hot`/`warm`/`cold` — **qualification**, set by the voice agent only |
| `lead_source` | String(64), default `voice_call` | |
| `conversation_summary` | Text, nullable | |
| `next_follow_up` | String(255), nullable | |
| `escalated`, `transferred_to_host` | Boolean, default `false` | |
| `status` | String(16), default `open` | `open`/`contacted`/`booked`/`closed` — **host-managed follow-up lifecycle**, distinct from `lead_temperature`. The voice agent never sets this; only the dashboard's Leads page edit dialog does. Overview's "Open Leads" card = count where `status == "open"` |
| `occasion` | String(255), nullable | free text (not an enum) — guest phrasing for a birthday/anniversary/honeymoon/etc, recorded verbatim, never host-facing suggestions |

### `guest_profiles` (`GuestProfile`, `app/models/guest_profile.py`)

Unique on `(phone, host_id)` — the same phone number calling two different hosts gets two independent profiles, never shared/leaked across hosts. Cross-call, per-host continuity (Guest Memory) — deliberately does **not** duplicate `Lead.status`/`lead_temperature`/`occasion` (those stay single-sourced per `Lead`); this table aggregates *across* a guest's calls to one host.

| Column | Type | Notes |
|---|---|---|
| `host_id` | UUID FK → users, cascade delete, nullable at column level | nullable only for pre-Guest-Memory legacy rows; every new row must set it |
| `phone` | String(32), indexed, not null | |
| `name` | String(255), nullable | |
| `total_stays` | Integer, default 0 | `0` means this row was just created for the current call — a genuinely first-time caller |
| `preferences` | JSONB dict, default `{}` | |
| `notes` | Text, nullable | |
| `last_property_id` | UUID FK → properties, `SET NULL` | |
| `preferred_language` | String(32), nullable | inferred at call-end from the transcript, not guest-declared |
| `last_outcome`, `last_follow_up` | String, nullable | |
| `last_call_at` | DateTime, nullable | |
| `conversation_summaries` | JSONB list, default `[]` | short summaries pulled directly from each call's `Lead.conversation_summary` — never raw transcripts, never a new LLM summarization call. Entry shape: `{call_session_id, property_id, property_name, date, summary, lead_temperature}`; capped in the write path |

### `host_discount_rules` (`HostDiscountRule`, `app/models/host_discount_rule.py`)

Structured, host-approved discount rules derived from `User.discount_policy_text` via LLM extraction (`POST /host-discount-rules/parse`). Derive-on-read by `pricing_engine` (looked up by `host_id` directly, not materialized per property) — see [research-flow.md](research-flow.md).

| Column | Type | Notes |
|---|---|---|
| `host_id` | UUID FK → users, cascade delete | |
| `trigger_type` | String(64), not null | `no_ask` / `guest_requests` / `repeat_guest_same_host` / a custom label |
| `discount_percent` | Numeric(5,2), not null | |
| `source` | String(16), default `ai_parsed` | |
| `status` | String(32), default `pending_validation` | `pricing_engine.negotiate_rate` only ever reads `status="approved"` rows |
| `raw_source_text` | Text, nullable | |

### `pricing_rules` (`PricingRule`, `app/models/pricing_rule.py`)

Per-property pricing rules (distinct from host-level `HostDiscountRule`).

| Column | Type | Notes |
|---|---|---|
| `property_id` | UUID FK → properties, cascade delete | |
| `rule_type` | String(64), not null | e.g. `length_of_stay`, `loyalty_discount`, `occupancy_discount` — only `length_of_stay` is currently consumed by `pricing_engine.calculate_price` |
| `condition` | JSONB dict, default `{}` | e.g. `{"min_nights": 5}` for a `length_of_stay` rule |
| `discount_percent` | Numeric(5,2), not null, default 0 | |
| `active` | Boolean, default `true` | |

### `faq_entries` (`FaqEntry`, `app/models/faq_entry.py`)

Verified, host-authored FAQ knowledge base (distinct from `Property.faq`, the legacy inline JSON list).

| Column | Type | Notes |
|---|---|---|
| `user_id` | UUID FK → users, cascade delete | |
| `property_id` | UUID FK → properties, cascade delete, nullable | `NULL` = portfolio-wide entry |
| `question`, `answer` | Text, not null | |
| `category` | String(64), nullable | |
| `status` | String(16), default `pending` | |
| `verified_by` | String(255), nullable | |
| `question_embedding` | JSONB list, nullable | computed once at verification time; plain float array, not pgvector (production DB extension availability unverified, and per-host comparison sets are small enough for in-Python cosine similarity). `NULL` = no semantic match possible for this entry, never an error |

### `unanswered_questions` (`UnansweredQuestion`, `app/models/unanswered_question.py`)

Logged by `handle_search_faq` only when the property (or the question, portfolio-wide) resolves to nothing at all — no `FaqEntry`, no legacy `Property.faq`, and no known `property_id` to fall back to `faq_service.full_property_context()` for. Powers the FAQ Learning Engine (`GET /faq/gaps`). One row per occurrence; frequency is computed at query time via `GROUP BY normalized_question`, not an incrementing counter.

**Known gap**: once a `property_id` is known, `search_faq` always returns `full_property_context()` as a last resort (every on-file fact for that property — see [agents.md](agents.md)), so this table no longer captures "guest asked something the property's raw fields didn't actually answer" — only "this property was completely unknown to the system." A host's FAQ Learning Engine page will under-report gaps for known properties as a result; not revisited as of 2026-07-17.

Indexes: `(user_id, normalized_question, status)` and `(user_id, property_id)`.

| Column | Type | Notes |
|---|---|---|
| `user_id` | UUID FK → users, cascade delete | |
| `property_id` | UUID FK → properties, `SET NULL`, nullable | |
| `call_session_id` | UUID FK → call_sessions, `SET NULL`, nullable | |
| `question` | Text, not null | verbatim guest question |
| `normalized_question` | String(500), not null | trimmed/lowercased, used for grouping and for finding all rows to mark answered together |
| `status` | String(16), default `pending` | |
| `resolved_faq_entry_id` | UUID FK → faq_entries, `SET NULL`, nullable | |
| `question_embedding` | JSONB list, nullable | same semantics as `FaqEntry.question_embedding` |

### `technicians` (`Technician`, `app/models/technician.py`)

| Column | Type | Notes |
|---|---|---|
| `property_id` | UUID FK → properties, cascade delete | |
| `name` | String(255), not null | |
| `specialty` | String(32), not null | `plumbing`/`electrical`/`ac`/`wifi`/`lock`/`general` |
| `phone` | String(32), not null | |
| `rating` | Numeric(2,1), default 5.0 | |

### `notifications` (`Notification`, `app/models/notification.py`)

Backs the dashboard's Live Requests feed (polled/streamed via `GET /notifications` and `GET /notifications/stream`). Also the interim stand-in for a real WhatsApp Business API — see [agents.md](agents.md).

| Column | Type | Notes |
|---|---|---|
| `property_id` | UUID FK → properties, cascade delete, nullable | `NULL` for Lead Agent-originated notifications |
| `call_session_id` | UUID FK → call_sessions, cascade delete, nullable | |
| `channel` | String(32), not null | `whatsapp` \| `escalation` \| `system` |
| `urgency` | String(16), default `low` | |
| `message` | Text, not null | |
| `status` | String(16), default `new` | |

Computed property `property_name` (not a column) — requires eager-loading the `property` relationship since it can run after the fetching session has closed.

## Common pitfalls specific to this schema

- `CallSession.urgency` and `CallSession.revenue_attributed` are dead columns — nothing writes them. Don't build a new feature assuming they're populated; use `Notification` (urgency) or `Lead.budget` (pipeline value) instead.
- Dashboard queries involving Lead Agent calls must filter by `CallSession.user_id`, not by joining through `property_id` — Lead Agent calls have `property_id IS NULL`.
- `Lead.status` and `Lead.lead_temperature` are two independent fields with different owners (dashboard vs. voice agent) — never conflate them.
- A `Lead` can now span multiple `CallSession`s (a returning guest's follow-up calls while still `open`/`contacted` reuse the same lead) — any code reading `Lead.call_session_id` to mean "the one call this lead is about" is now wrong; use `CallSession.lead_id` to find which lead a given call is currently linked to, and never assume a 1:1.
- `lead_service.delete_for_unqualified_call`/`delete_if_empty` must never delete a *reused* lead just because the one follow-up call that triggered them classified poorly or added nothing — they check `Lead.call_session_id == call_session_id` (this call originated it) before deleting; a reused lead is only ever detached (`CallSession.lead_id = None`), never removed.
