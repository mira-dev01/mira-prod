# REST API Reference

All routes are mounted under `/api/v1` (`app/main.py`). Unless noted, **Auth** = `get_current_user` (bearer, `Authorization: Bearer <token>`) via `app/auth/dependencies.py`. Identity itself is owned by **Clerk**, not this backend: the bearer token is a Clerk session JWT, verified against Clerk's own JWKS (derived from `CLERK_PUBLISHABLE_KEY`) rather than issued/checked by any local login endpoint — there is no `/auth/register`, `/auth/register-host`, or `/auth/login` here. `get_current_user` auto-provisions/links a local `User` row from the token's Clerk `sub` claim on first sight (`_resolve_local_user`: match by `clerk_user_id`, else backfill onto an existing row with the same email — the pre-Clerk-migration path — else create a fresh `User`). All routes scope results to the authenticated user's own data (`current_user.id`) unless stated otherwise. See [architecture.md](architecture.md) for the auth mechanism and CORS setup, [agents.md](agents.md) for the voice pipeline itself, and [database.md](database.md) for the underlying tables.

## `auth.py` — `/auth`

| Method & path | Purpose | Auth |
|---|---|---|
| `POST /auth/register-host/transcribe-intro` | Transcribes a prospective host's recorded voice-agent intro (onboarding wizard's "Add your voice agent's intro" step) via Sarvam batch STT; reuses the same helper as `/faq/gaps/{id}/answer-voice`, just unauthenticated since it may run before Clerk sign-up finishes | none |
| `POST /auth/onboarding` | Fills business/Airbnb-import fields Clerk's own sign-up form doesn't collect (name, phone, business profile, Airbnb host status, first Airbnb listing URL, agent first message), once `get_current_user` has already resolved a real Clerk-authenticated `User`; kicks off a background Bright Data scrape for the one required listing and returns a `snapshot_id` to poll via `GET /properties/import-airbnb-urls/{snapshot_id}` | required |
| `GET /auth/me` | Current user profile | required |
| `POST /auth/me/photo` | Upload/replace the host's own profile photo (multipart, Cloudinary-backed) | required |
| `POST /auth/me/banner` | Upload/replace the host's own profile banner image (multipart, Cloudinary-backed) | required |
| `PATCH /auth/me` | Update profile fields (agent customization, notification email, discount policy text, etc.) | required |

## `properties.py` — `/properties`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /properties` | List the host's properties | required |
| `POST /properties` | Create a property | required |
| `GET /properties/{id}` | Get one property (must be owned) | required |
| `PATCH /properties/{id}` | Update a property | required |
| `DELETE /properties/{id}` | Delete a property | required |
| `POST /properties/{id}/photos` | Upload one photo (multipart, re-called once per file) | required |
| `POST /properties/{id}/sync-ical` | Manually trigger iCal calendar sync for one property | required |
| `POST /properties/import` | Bulk-create/update from uploaded scraped Airbnb JSON files (advanced/legacy path) | required |
| `POST /properties/import-airbnb-urls` | Trigger a Bright Data async scrape for pasted Airbnb listing URLs → `snapshot_id` | required |
| `GET /properties/import-airbnb-urls/{snapshot_id}` | Poll scrape status; on `"ready"`, upserts properties (safe to re-poll — updates, not duplicates) | required |
| `GET /properties/{id}/gallery` | No-auth, minimal-fields (`PropertyGalleryOut`) view backing the guest-facing single-property photo page (`frontend /p/{id}/photos`) — the link `send_photos` hands a guest for one specific listing | none |
| `GET /properties/portfolio/{host_id}/gallery` | No-auth, same minimal fields, one row per property under `host_id` — backs the guest-facing "photos of all our properties" page (`frontend /p/portfolio/{host_id}/photos`), the link `send_photos` hands a guest who asked for photos without naming one listing | none |
| `POST /properties/{id}/renormalize-name` | Re-derives `display_name`/`spoken_name`/`property_type`/`property_style`/`brand` from the property's `raw_name` (LLM fallback included) — backs a dashboard "Refresh name" action for a listing the importer named badly | required |
| `POST /properties/renormalize-names` | Bulk version of the above across every one of the host's own properties; also the mechanism for backfilling `display_name`/`spoken_name` onto properties imported before that feature existed | required |

See [research-flow.md](research-flow.md) for the two import paths' internals.

## `bookings.py` — `/bookings`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /bookings` | List bookings across the host's properties; optional `property_id` query param scopes to one property | required |
| `POST /bookings` | Create a booking | required |
| `DELETE /bookings/{id}` | Cancel a booking | required |
| `POST /bookings/check-availability` | Check a property's availability for a date range | required |

## `calls.py` — `/calls`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /calls` | List call sessions; filters: `status`, `urgency`, `call_type` (comma-separated, e.g. `BOOKING_LEAD,GUEST_SUPPORT` — maps 1:1 to the dashboard Calls page's dropdown, undefined = no filter at all, every call including JUNK/INCOMPLETE/UNKNOWN), `limit`, `start_date`, `end_date`, `include_test_calls` (default excludes browser-test calls) | required |
| `GET /calls/{id}` | Get one call session (transcript, summary, etc.) | required |

## `guests.py` — `/guests`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /guests` | List guest profiles (Guest Memory); filters: `start_date`, `end_date` | required |
| `GET /guests/{id}` | Get one guest profile | required |
| `GET /guests/{id}/detail` | Guest profile + related call/lead history | required |
| `PATCH /guests/{id}` | Update a guest profile (name, notes, preferences) | required |

## `technicians.py` — `/technicians`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /technicians` | List technicians across the host's properties | required |
| `POST /technicians` | Add a technician (property, specialty, phone) | required |
| `DELETE /technicians/{id}` | Remove a technician | required |

## `pricing.py` — `/pricing`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /pricing/rules` | List `PricingRule` rows; filters: `start_date`, `end_date` | required |
| `POST /pricing/rules` | Create a per-property pricing rule (e.g. `length_of_stay`) | required |
| `DELETE /pricing/rules/{id}` | Delete a pricing rule | required |
| `POST /pricing/quote` | Get a price quote for a property/date-range/guest-count (dashboard-facing equivalent of the voice `get_pricing` tool) | required |

## `negotiation_rules.py` — `/negotiation-rules`

Successor to the old `host_discount_rules.py`/`/host-discount-rules` module — one merged `NegotiationRule` table now covers both the former host-wide discount triggers and the former per-property stay-pricing rules (`length_of_stay`, `minimum_stay_nights`, `early_checkin_fee`, `late_checkout_fee`, `custom`), plus an optional staged negotiation ladder (`stages`, Phase 4D).

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /negotiation-rules` | List the host's negotiation rules (any status) | required |
| `POST /negotiation-rules/parse` | LLM-parse free-text `policy_text` into draft `pending_validation` rules; also saves the raw text onto `User.discount_policy_text` for re-editing later | required |
| `PATCH /negotiation-rules/{id}` | Edit/approve/reject a rule in the AI Training validation tab — only `status="approved"` rows are read by `pricing_engine`/negotiation | required, must own the rule |
| `DELETE /negotiation-rules/{id}` | Delete a rule | required, must own the rule |

See [research-flow.md](research-flow.md) for how these rules feed `negotiate_rate`.

## `analytics.py` — `/analytics`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /analytics/summary` | Dashboard stat cards: total/completed/escalated calls, open notifications, pipeline value, open leads, answer rate. Params: `days` (default 30), `start_date`/`end_date` (override `days`), `include_test_calls` | required |
| `GET /analytics/timeseries` | Bucketed time series for one metric: `total_calls`, `completed_calls`, `escalated_calls`, `pipeline_value`, `open_leads`. Params: `metric`, `start_date`, `end_date`, `include_test_calls` | required |
| `GET /analytics/recovery` | Busy Call Recovery funnel/KPIs (Opportunities page's Recovery Analytics card): `busy_calls` (one per rejected call attempt, from `Notification(channel="busy_recovery")` — not `Lead`, which dedups repeat attempts from the same guest), `recovered` (distinct leads with a `busy_recovery_reply` notification — historical-only since the WhatsApp production cutover removed the interactive reply flow that produced this channel; reads 0 for any lead created after the cutover), `converted`/`lost` (recovery leads with `status="booked"`/`"closed"`), `avg_recovery_time_seconds` (first rejection → guest's first reply, per lead, averaged), `avg_host_response_seconds` (notification created → first marked read, via `Notification.responded_at`), `recovery_rate`/`conversion_rate`, and a `funnel` array (`busy_calls`→`recovered`→`converted`). Scoped by `Lead.user_id` (not `property_id.in_(owned_property_ids)`) since a reply notification can have `property_id=NULL`. Params: `days` (default 30), `start_date`/`end_date` (override `days`) | required |
| `GET /analytics/quality-events` | Cross-call guard/validator-firing analytics (which validators fired, how often), bucketed over time. Param: `bucket` (`week`/`month`, default `week`) | required |
| `GET /analytics/objection-insights` | Conversion rate by `CallSummary.objection_tags` — read-only/informational, no automatic write-back into `NegotiationRule`/`PricingRule`/pricing. Params: `start_date`/`end_date` | required |

## `notifications.py` — `/notifications`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /notifications` | List notifications (Live Requests feed source) | required |
| `POST /notifications/{id}/read` | Mark a notification read | none applied at the route (no `current_user` dependency present) |
| `GET /notifications/stream` | Server-sent/polling stream of notifications | required |

## `leads.py` — `/leads`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /leads` | List leads; filters: `start_date`, `end_date` | required |
| `GET /leads/service-requests` | List service-request feed items (a separate request-feed concept from `Lead`, see `app/services/request_feed_service.py`); param `include_dismissed` (default `False`) | required |
| `POST /leads/service-requests/dismiss` | Bulk-dismiss service requests by `call_session_ids`; returns `{"dismissed": <count>}` | required |
| `GET /leads/{id}` | Get one lead | required |
| `PATCH /leads/{id}` | Update a lead — this is the **only** place `Lead.status` (open/contacted/booked/closed) is ever set; the voice agent never touches it | required |

## `faq.py` — `/faq`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /faq` | List verified `FaqEntry` rows | required |
| `POST /faq` | Create a verified FAQ entry directly | required |
| `PATCH /faq/{id}` | Update a FAQ entry | required |
| `DELETE /faq/{id}` | Delete a FAQ entry | required |
| `GET /faq/gaps` | List unanswered-question gaps, grouped by normalized question; filters: `property_id`, `start_date`, `end_date` | required |
| `GET /faq/gaps/analytics` | Gap analytics: most-frequent / by-property / over-time; param `bucket` (`week`/`month`) | required |
| `POST /faq/gaps/{gap_id}/answer` | Convert a gap into a verified `FaqEntry` (text answer); marks every row sharing the normalized question as answered | required |
| `POST /faq/gaps/{gap_id}/answer-voice` | Same, but answer given as an audio upload (transcribed via Sarvam batch STT) | required |

See [agents.md](agents.md) for how `search_faq` logs gaps during a live call.

## `voice.py` — `/voice`

| Method & path | Purpose | Auth |
|---|---|---|
| `POST /voice/transcribe` | General-purpose "dictate into this field" transcription for dashboard text fields (mic button, `use-dictation.ts`) — reuses the same Sarvam batch STT helper as `/faq/gaps/{id}/answer-voice` and `/auth/register-host/transcribe-intro` | required |
| `WS /voice/exotel/ws/{token}` | Real Exotel call websocket (raw-PCM media protocol). Configured directly in the Exotel Voicebot Applet as `wss://<backend>/api/v1/voice/exotel/ws/<EXOTEL_WEBHOOK_TOKEN>` -- token is a PATH segment, not a query param: Exotel's Voicebot Applet strips query strings from the configured WSS URL before connecting (confirmed live) | `token` path segment (`EXOTEL_WEBHOOK_TOKEN`), not JWT |
| `POST /voice/twilio/incoming/{token}` | Twilio's "A call comes in" webhook for the separate Twilio Voice test entrypoint (real-call testing on Twilio's free trial when Exotel credits run out — entirely independent of Exotel routing/pipeline code). Returns TwiML pointing Twilio at `WS /voice/twilio/ws/{token}` | `token` path segment (`TWILIO_VOICE_WEBHOOK_TOKEN`), not JWT |
| `WS /voice/twilio/ws/{token}` | Twilio Voice media-stream websocket, reached via the TwiML the incoming-call webhook returns; runs the same pipeline as the Exotel path (`run_voice_pipeline_twilio`) | `token` path segment (`TWILIO_VOICE_WEBHOOK_TOKEN`), not JWT |
| `POST /voice/test/offer` | WebRTC offer/answer signaling for the in-dashboard "talk to Mira" test. Omit `property_id` for the portfolio-wide Lead Agent; include it for Guest Support on one property | required |
| `GET /voice/test` | Standalone browser test page (mic in/speaker out), opened with `?token=<JWT>&property_id=<optional>` | `token` query param (JWT), not a bearer header |

See [agents.md](agents.md) for what runs behind these endpoints.

## `webhooks/exotel.py` — `/webhooks/exotel`

| Method & path | Purpose | Auth |
|---|---|---|
| `POST /webhooks/exotel/call-status` | Exotel's call-status/passthru callback (call lifecycle: busy/no-answer/failed, recording URL). Independent of the live voice websocket — used for `call_sessions` logging via `call_service.attach_exotel_call` | `token` query param (`EXOTEL_WEBHOOK_TOKEN`), verified via `verify_webhook_token` |
| `GET /webhooks/exotel/call-routing` | Phase 4: the initial call-ownership Passthru applet, placed before the Voicebot applet in the Exotel console. Exotel's synchronous-Passthru contract reads the HTTP status code alone: `200` continues to the existing Voicebot applet (Mira answers), `302` continues to a Connect applet (routes to the host directly). Resolves `dialed_number` → `Property` → `resolve_effective_call_owner`; fails closed to `200` (Mira) on every error path (missing/invalid token, missing `CallSid`, unknown DID, invalid ownership config, resolver error) | `token` query param (`EXOTEL_WEBHOOK_TOKEN`) |
| `GET /webhooks/exotel/connect-routing` | Phase 8: the Connect applet's own dynamic Primary URL target — answers "which single PSTN number should Connect dial for this `CallSid`," read from `User.phone` via a DB-verified property/host chain, never from a request parameter. Reached either as (1) an initial HOST-owned call routed here directly off `call-routing`'s 302, or (2) a live Mira→host handoff (`CallSession.handoff_status="requested"`, set by `POST /take-call`). Fails closed to an empty numbers list (HTTP 200) on any unresolvable/unauthorized/invalid state | `token` query param (`EXOTEL_WEBHOOK_TOKEN`) |

There is no inbound WhatsApp webhook — the interactive numbered-menu guest
reply feature that used to receive one (Property/Pricing/FAQs/Photos/
Talk-to-host, routed via `app/services/whatsapp_reply_service.py`) was
removed in favor of a single "Mira is busy, call back in 5 minutes" message
(see `app/services/recovery_service.py`'s `_guest_recovery_whatsapp_text`).
Every WhatsApp send in this codebase is now outbound-only.

## `take_call.py` — `/take-call`

Phase 6: the secure "Take Call" action a host reaches by tapping the WhatsApp "guest is calling" link. Deliberately unauthenticated — no `get_current_user`/Clerk session — since it's opened from a mobile WhatsApp browser tab off a signed, short-lived token (`app/services/take_call_token.py`) rather than a logged-in session. Claims the handoff (`CallSession.handoff_status`: `NULL` → `"requested"`) atomically and exactly once via a `WHERE handoff_status IS NULL` UPDATE, then signals a live pipeline running in the same process if one is listening (`app/voice/handoff_signal.py`). Does not itself perform any telephony action — see `webhooks/exotel.py`'s `connect-routing` for how the actual PSTN dial-out is resolved once the claim lands. Renders plain HTML (no JS required), GET-then-POST to avoid a link-preview bot silently consuming a GET-only action link.

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /take-call?token=<token>` | Renders a confirmation page (property name, guest number, call status) — read-only, performs no write | `token` query param, signed/short-lived (`verify_take_call_token`), not JWT |
| `POST /take-call?token=<token>` | Performs the atomic handoff claim (invoked by the confirmation page's own form submit); a lost race, an already-ended call, or an already-claimed handoff all return a safe no-op page, never a second telephony action | `token` query param, signed/short-lived (`verify_take_call_token`), not JWT |

## `GET /health` and `GET /api/v1/health/llm`

Not domain-grouped — defined directly in `app/main.py`.

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /health` | Plain liveness check (`healthCheckPath` on both Railway and Render) | none |
| `GET /api/v1/health/llm` | Per-Groq-model health/latency snapshot from the last periodic check (see [agents.md](agents.md)) | none |
