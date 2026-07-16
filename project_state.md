# Project State

Living snapshot for session continuity — not a chronological log. See `CLAUDE.md` and `docs/` for stable reference material.

## Active branch

`shagun`

## Recent fixes

Last 10 commits:
- `44a2471` Merge pull request #13 from shagunverma-04/abhaya
- `f03bec9` memory setup + image carousel for properties
- `fb1063d` Add Property Memory: seasonal notes surfaced only when currently in effect
- `ee6af71` Add Knowledge Memory: semantic FAQ-gap dedup and auto-draft suggestions
- `c650a3b` Add Guest Memory: cross-call guest continuity, host-scoped repeat-guest discounts
- `74b9a52` Wire Host Memory discount policy into negotiate_rate and GOLDEN_RULES
- `6da15ce` Add Host Memory: discount policy parsing + AI Training validation tab
- `1470edd` Fix Lead Agent property scoping: lock selected property across search_faq/recommend_properties
- `2ec8e91` echo cancellation implemented + kanban board, calendar, etc improved
- `78d2905` Merge pull request #12 from shagunverma-04/abhaya

**2026-07-15**: DB was 6 Alembic migrations behind (stuck well before `f3a8c1d7e4b6`), causing demo login to 500. Fixed via `alembic upgrade head` in `backend/`. Current head: `f3a8c1d7e4b6` (add seasonal_notes to properties) — see `docs/database.md` for the full migration history.

**2026-07-15 (later same day)**:
- Fixed escalation emails landing in spam: `email_client.py` now sends multipart text+HTML with proper `From` display name, `Date`, and `Message-ID` headers (missing headers were the immediate cause; real SPF/DKIM/DMARC on the sending domain is still the durable fix — see the header comment in `email_client.py`). New `email_templates.py` renders an HTML email in Mira's palette with "Open Dashboard" + "Message Guest on WhatsApp" (wa.me link) CTAs.
- Built the `send_photos` voice tool end-to-end: guest asks to see photos → LLM calls `send_photos` → queues a WhatsApp-stand-in notification with a link to a new no-auth gallery page (`frontend/src/app/p/[propertyId]/photos`, backed by `GET /api/v1/properties/{id}/gallery`). Reuses the existing Cloudinary `Property.photos` field rather than a host-maintained Drive folder — one link either way, but this can't drift out of sync with what's actually on file.
- Built `founder-console/` — a fully separate Next.js app (own `package.json`, port 4000, passcode-gated via `FOUNDER_PASSCODE`) showing live LLM model health (`GET /api/v1/health/llm`) plus a static external-API cost reference table. Real per-call cost metering isn't wired up anywhere in the backend yet — the cost table is a planning reference, not live spend.

## Known issues / in-flight work

- `TURN_DETECTION_STRATEGY=hybrid_experimental` (`app/voice/turn_strategies.HybridCompletenessUserTurnStopStrategy`) is experimental, local-only, and **not yet verified end-to-end on a real live call**. Production defaults to `vad_fixed` and is unaffected regardless of this branch's local `.env` setting. `render.yaml` intentionally omits the var.
- `[DEBUGTURN]` debug logging still present in `turn_strategies.py` — safe to strip once the hybrid strategy is confirmed working live.
- Pre-existing, unrelated test failure: `test_escalate_to_host_also_saves_lead_so_it_isnt_left_empty` in `tests/test_tool_handlers.py` fails on phone normalization (`+919999999999` → stored as `9999999999`) — confirmed present before today's changes too, not something this session touched.

## Open design questions

- **Daily Airbnb smart-pricing search API integration** — not yet built. No comp/market-price data source is wired up; needs a decision on where comparable pricing data comes from (Bright Data re-scrape of the property's own listing vs. a dedicated market-rate API) before implementation.
- **Payment gateway integration for booking confirmation** — Phase 1 (manual host approval of a guest-submitted payment screenshot) not yet built. Phase 2 (real gateway + webhook) researched: Razorpay ~2% flat domestic, 0 AMC/setup, ₹100 chargeback; Cashfree ~1.75-2.25%, ₹4,999/yr AMC, ₹150 chargeback; PayU ~2-2.5%, ₹200 chargeback. UPI itself is 0% MDR under RBI rules on all three, though gateways still charge a ~2% "platform fee" on UPI-via-bank-account flows — confirm current numbers directly with each provider before committing. No `Booking` price/payment-status columns exist yet (see `docs/database.md`) — needs a schema decision (split vs. full-upfront, proof-of-payment storage) before building.
