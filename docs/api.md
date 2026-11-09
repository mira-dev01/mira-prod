# REST API Reference

All routes are mounted under `/api/v1` (`app/main.py`). Unless noted, **Auth** = `get_current_user` (a Clerk session token as `Authorization: Bearer <token>`; RS256, `iss`/`azp`/`exp` checked — see [architecture.md](architecture.md)'s Authentication) via `app/auth/dependencies.py`. All routes scope results to the authenticated user's own data (`current_user.id`) unless stated otherwise. See [architecture.md](architecture.md) for the auth mechanism and CORS setup, [agents.md](agents.md) for the voice pipeline itself, and [database.md](database.md) for the underlying tables.

## `auth.py` — `/auth`

Sign-up and sign-in are Clerk's (the old `/auth/register`, `/auth/register-host` and `/auth/login` password/JWT endpoints no longer exist). The first authenticated request from a new Clerk identity creates the local `User` row (`get_current_user`).

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /auth/me` | Current user profile | required |
| `PATCH /auth/me` | Update profile fields (agent customization, notification email, discount policy text, etc.) | required |
| `POST /auth/me/photo` · `POST /auth/me/banner` | Upload the host's profile photo / banner image (10 MB cap, image types only) | required |
| `GET /auth/me/call-hours-status` | The account's live call-routing decision right now (Mira vs. host), from the same resolver the Exotel call-routing webhook uses | required |
| `POST /auth/onboarding` | Post-signup onboarding: fills the business/Airbnb-import fields Clerk's form doesn't collect and starts the first Airbnb scrape (returns a `snapshot_id` to poll via `GET /properties/import-airbnb-urls/{snapshot_id}`). 409 if the business number is already registered | required |
| `POST /auth/register-host/transcribe-intro` | Transcribes the host's recorded voice-agent intro (onboarding step) to text via Sarvam batch STT. **Authenticated** (it used to be open to anyone), audio ≤ 8 MB, `audio/*` (or `video/webm`/`video/mp4`) only, rate-limited to 12 clips/min per user (`app/utils/rate_limit.py`) | required |

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
| `PUT /properties/transfer-numbers` | Host transfer numbers per group of properties (Settings → Your account); replaces every assignment in one transaction | required |
| `POST /properties/{id}/renormalize-name` · `POST /properties/renormalize-names` | Re-derive `display_name`/`spoken_name`/type/style/brand from the raw (Airbnb) name — one property, or all of the host's own | required, must own the property |
| `GET /properties/{id}/gallery` | Public guest-facing photo gallery data (the `send_photos` tool's link target). Exposes only `PropertyGalleryOut` fields; the property UUID is the only secret | none |

See [research-flow.md](research-flow.md) for the two import paths' internals.

## `bookings.py` — `/bookings`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /bookings` | List bookings across the host's properties | required |
| `POST /bookings` | Create a booking | required |
| `DELETE /bookings/{id}` | Cancel a booking | required |
| `POST /bookings/check-availability` | Check a property's availability for a date range | required |
| `GET /bookings/reconciliation` | "Booking details need confirmation" queue: reservations missing a final price that the host hasn't answered for, plus probable Mira matches awaiting Yes/No. Checking out within the last 90 days or later. Returns `items` (booking + property name + matched conversation), `total`, `price_missing`, `attribution_pending` | required |
| `GET /bookings/{id}/reconciliation` | Same view for one booking (Calendar booking panel) | required |
| `PATCH /bookings/{id}/price` | Host's final-price answer. `choice`: `mira_conversation` (detected price, or a corrected `final_booking_price`; stored as `price_source=host_confirmed`), `outside_mira` (`final_booking_price` required; `host_entered`), `not_confirmed_yet` (stays `pending_confirmation`, not asked again). 422 for a missing amount or blocked dates | required |
| `PATCH /bookings/{id}/attribution` | Host's "Mira match?" answer, `{is_mira_match}`. Yes → `confirmed` (lead moves to `booked`); No → `not_attributed`, and conversation-derived prices are withdrawn. 422 if the booking has no matched conversation | required |
| `PATCH /bookings/{id}/kind` | `reservation` ⇄ `blocked` (an iCal block that was really a stay, or vice versa) | required |
| `PATCH /bookings/{id}/scope` | Host correction of what a booking takes — the entire place or N rooms (an Airbnb room booking arrives as one room) | required, must own the booking |

`POST /bookings` also accepts `kind`, `final_booking_price` (→ `host_entered`, confirmed) and `lead_id` (booking created by closing a Mira lead → attribution `confirmed`). See `app/services/booking_reconciliation_service.py` for the attribution and price-detection rules.

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

(Formerly `/host-discount-rules`.)

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /negotiation-rules` | List the host's negotiation/pricing rules (any status) | required |
| `POST /negotiation-rules/parse` | LLM-parse the host's free-text policy into draft `pending_validation` rules (also saves the raw text on `User.discount_policy_text`) | required |
| `PATCH /negotiation-rules/{id}` | Edit/approve a rule (only `status="approved"` rows are read by `pricing_engine`) | required, must own the rule |
| `DELETE /negotiation-rules/{id}` | Delete a rule | required, must own the rule |

See [research-flow.md](research-flow.md) for how these rules feed `negotiate_rate`.

## `analytics.py` — `/analytics`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /analytics/summary` | Dashboard stat cards: total/completed/escalated calls, open notifications, pipeline value, open leads, answer rate. Params: `days` (default 30), `start_date`/`end_date` (override `days`), `include_test_calls` | required |
| `GET /analytics/timeseries` | Bucketed time series for one metric: `total_calls`, `completed_calls`, `escalated_calls`, `pipeline_value`, `open_leads`. Params: `metric`, `start_date`, `end_date`, `include_test_calls` | required |
| `GET /analytics/dashboard` | Everything on the host Analytics page in one response: `portfolio` (occupancy, revenue, ADR, RevPAR, upcoming revenue, `completeness`, previous-period `comparison`), `funnel`, `impact` (counts CONFIRMED attribution only), `guest_intent`, `pricing` (sample-size gated), `needs_confirmation`. Financial metrics read `Booking.final_booking_price` only. Definitions are on each function in `app/services/analytics_service.py`. Params: `start_date`/`end_date` (default last 30 days IST, max 1 year), `property_id`, `include_test_calls`, `compare` | required |
| `GET /analytics/recovery` | Busy Call Recovery funnel/KPIs (Opportunities page's Recovery Analytics card): `busy_calls` (one per rejected call attempt, from `Notification(channel="busy_recovery")` — not `Lead`, which dedups repeat attempts from the same guest), `recovered` (distinct leads with a `busy_recovery_reply` notification), `converted`/`lost` (recovery leads with `status="booked"`/`"closed"`), `avg_recovery_time_seconds` (first rejection → guest's first reply, per lead, averaged), `avg_host_response_seconds` (notification created → first marked read, via `Notification.responded_at`), `recovery_rate`/`conversion_rate`, and a `funnel` array (`busy_calls`→`recovered`→`converted`). Scoped by `Lead.user_id` (not `property_id.in_(owned_property_ids)`) since a reply notification can have `property_id=NULL`. Params: `days` (default 30), `start_date`/`end_date` (override `days`) | required |
| `GET /analytics/overview` | The Overview page's snapshot + "Needs your attention" in one request (which numbers follow the date range vs. current state: see `analytics_service.overview`) | required |
| `GET /analytics/quality-events` | Cross-call guard/validator firing analytics (read-only) | required |
| `GET /analytics/objection-insights` | Conversion rate by `CallSummary.objection_tags` (read-only, informational — no write path back into the agent) | required |

## `notifications.py` — `/notifications`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /notifications` | List notifications (Live Requests feed source) | required |
| `POST /notifications/{id}/read` | Mark a notification read | required, **must own it** — via its property, or (portfolio-wide busy-recovery rows) its lead. Another host's id gets the same 404 as a nonexistent one (it used to have no auth at all) |
| `GET /notifications/stream` | Server-sent/polling stream of notifications | required |

## `leads.py` — `/leads`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /leads` | List leads; filters: `start_date`, `end_date` | required |
| `GET /leads/{id}` | Get one lead | required |
| `PATCH /leads/{id}` | Update a lead — this is the **only** place `Lead.status` (open/contacted/booked/closed) is ever set; the voice agent never touches it | required |
| `GET /leads/service-requests` · `POST /leads/service-requests/dismiss` | List / dismiss guest service requests raised during calls | required |

## `faq.py` — `/faq`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /faq` | List verified `FaqEntry` rows | required |
| `POST /faq` | Create a verified FAQ entry directly | required |
| `PATCH /faq/{id}` | Update a FAQ entry; edits to a multi-property FAQ apply to every copy, and moving a single copy is rejected (use `/scope`) | required |
| `PUT /faq/{id}/scope` | Apply a FAQ to all properties (`apply_to_all`) or a chosen set (`property_ids`); keeps rows still wanted, deletes dropped ones, clones new ones | required |
| `DELETE /faq/{id}` | Delete a FAQ entry; `?group=true` also removes it from every property it was applied to | required |
| `GET /faq/gaps` | List unanswered-question gaps, grouped by normalized question; filters: `property_id`, `start_date`, `end_date` | required |
| `GET /faq/gaps/analytics` | Gap analytics: most-frequent / by-property / over-time; param `bucket` (`week`/`month`) | required |
| `POST /faq/gaps/{gap_id}/answer` | Convert a gap into a verified `FaqEntry` (text answer); body takes `apply_to_all` or `property_ids` (legacy `apply_to_property` still works); marks every row sharing the normalized question as answered | required |
| `POST /faq/gaps/{gap_id}/answer-voice` | Same, but answer given as an audio upload (transcribed via Sarvam batch STT) | required |

See [agents.md](agents.md) for how `search_faq` logs gaps during a live call.

## `tickets.py` — `/tickets`

What Mira told guests would happen after the call, kept open until the host does it (`app/services/ticket_service.py`).

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /tickets` | The host's tickets, newest first; `state` = `open` (default) / `resolved` (done or dismissed) / `all`; optional `property_id` | required |
| `PATCH /tickets/{id}` | Set `status` to `open` / `done` / `dismissed` | required |

## `billing.py` + `payments.py` — `/billing`, `/payments`

A host's payment setup and the payment links Mira has sent (`app/services/billing_service.py`, `payment_service.py`). Money goes from the guest straight to the host's own Razorpay account. Nothing here ever returns the Razorpay secret.

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /billing` | The Billing tab: whether the server can store keys (`encryption_ready`), Razorpay connection (`connected`, `key_id`, `mode` live/test), the two guest messages (approved text, pending `draft`), the allowed `{placeholders}`, `notify_host_on_payment`, `capability_enabled` (the *Online payments* capability is on) | required |
| `PUT /billing/razorpay` | `{key_id, key_secret}` — checked against Razorpay first, then stored (secret encrypted). 422 with a plain reason if refused, the server has no encryption key, or the Key ID changes while payment links are open | required |
| `DELETE /billing/razorpay` | Disconnect. 422 while any payment link is still open | required |
| `PUT /billing/templates/{payment_request\|payment_success}` | Save wording as a **draft**; guests keep getting the approved wording. The request message must contain `{link}`; unknown `{names}` are refused | required |
| `POST /billing/templates/{name}/approve` · `DELETE /billing/templates/{name}` | Make the draft live · back to Mira's standard wording | required |
| `PUT /billing/notify-host` | `{enabled}` — WhatsApp the host when a guest pays (the in-app alert is always sent) | required |
| `GET /payments` | The host's payment links / date holds, newest first (`status`: `active` / `paid` / `expired` / `cancelled`) | required |
| `POST /payments/{id}/cancel` | Release a hold: closes the Razorpay link, frees the dates, tells waiting guests. Refused if the guest has just paid | required |

## `policies.py` — `/policies`

The host's booking and house policies (cancellation, age limits, pets, …), host-wide or per property, and how uploaded documents become drafts (`app/services/policy_service.py`, `policy_documents.py`). Approved policies become knowledge-base facts immediately. All routes are scoped to the signed-in host; another host's ids are 404.

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /policies` | One row per topic (`state`: `everyone` / `some_differ` / `property_only` / `unset`, `override_count`, live `items`), the `drafts` waiting for review, uploaded `documents`, the host's `properties` (each with derived `labels` such as "Parties welcome"/"No pets" and `policy_ids`, the live policies it actually follows), and the `topics` list | required |
| `POST /policies` | Write a live policy `{topic, text, title?, property_id?}` (`property_id` null = host-wide). 422 if that topic already has one in that scope | required |
| `POST /policies/scoped` | `{topic, text, title?, property_ids?}` — one live policy for the whole portfolio (no `property_ids`) or for each chosen property, **all or nothing** (422 naming any property that already has one for that topic). Returns the created policies | required |
| `PATCH /policies/{id}` | Edit `text` / `title` / move to another `topic` (the title follows the move unless the host wrote their own); an active policy is re-synced to the knowledge base at once | required |
| `DELETE /policies/{id}` | Remove (a live one is removed from the knowledge base; removing a property override returns that property to the host-wide policy) | required |
| `POST /policies/{id}/approve` | Make a draft live; replaces a live policy of the same topic and scope (`replaced: true`). Optional body `{apply_to_all?, property_ids?}` overrides who it applies to while reviewing: `apply_to_all` → host-wide; `property_ids` → each chosen property's own (the draft becomes the first, the rest are copies). No body keeps the scope the document gave it | required |
| `POST /policies/approve-all` | `{document_id?}` — approve every draft, or one document's | required |
| `POST /policies/documents` | Multipart `file` (PDF, .docx, .xlsx, .csv, .txt, .md, PNG/JPG/WebP, ≤ 8 MB). Returns the `document`, the new `drafts` and `skipped` (already on file). 422 with a plain reason when it can't be read | required |

## `agent_config.py` — `/agent-config`

The host's Agent rules (what Mira collects, what makes a lead hot, how a booking is confirmed and paid). Stored partially; missing keys read as defaults. Name and phone are not configurable. See `app/schemas/agent_config.py`.

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /agent-config` | Effective config (defaults filled in) plus per-property payment overrides | required |
| `PUT /agent-config` | Replace the sections sent (`collect`, `hot_lead`, `booking`, `payment`, `busy_calls`); others untouched | required |
| `PUT /agent-config/properties/{id}/payment` | Set a property's payment override (only the fields that differ from the portfolio) | required |
| `DELETE /agent-config/properties/{id}/payment` | Clear it; the property follows the portfolio again | required |

## `voice.py` — `/voice`

| Method & path | Purpose | Auth |
|---|---|---|
| `WS /voice/exotel/ws/{token}` | Real Exotel call websocket (raw-PCM media protocol). Configured directly in the Exotel Voicebot Applet as `wss://<backend>/api/v1/voice/exotel/ws/<EXOTEL_WEBHOOK_TOKEN>` -- token is a PATH segment, not a query param: Exotel's Voicebot Applet strips query strings from the configured WSS URL before connecting (confirmed live) | `token` path segment (`EXOTEL_WEBHOOK_TOKEN`), not JWT |
| `POST /voice/test/offer` | WebRTC offer/answer signaling for the in-dashboard "talk to Mira" test. Omit `property_id` for the portfolio-wide Lead Agent; include it for Guest Support on one property | required |
| `GET /voice/test` | Standalone browser test page (mic in/speaker out), opened with `?token=<session token>&property_id=<optional UUID>`. `property_id` must be a UUID (400 otherwise); the token and ids are emitted as escaped JSON literals (they used to be pasted raw into the page — a reflected XSS); the response is `Cache-Control: no-store` + `Referrer-Policy: no-referrer`. The page then calls `POST /voice/test/offer` with that token | `token` query param (the page authenticates its own API calls) — planned: replace the long-lived session token in the URL with a short-lived ticket ([vapt-plan.md](vapt-plan.md)) |
| `POST /voice/transcribe` | General "dictate into this field" transcription (the dashboard mic button). Same limits as the other speech-to-text endpoints: ≤ 8 MB audio, 12 clips/min per user | required |
| `POST /voice/twilio/incoming/{token}` · `WS /voice/twilio/ws/{token}` | Twilio Voice fallback telephony path (TwiML webhook, then media websocket) | shared-secret `token` path segment (rejected in production while it is still `change-me`) |

See [agents.md](agents.md) for what runs behind these endpoints.

## `take_call.py` — `/take-call`

The WhatsApp "Take Call" link a host taps when a guest is calling. HTML pages, no bearer token: the link carries an HMAC-signed token (`app/services/take_call_token.py`, key `TAKE_CALL_TOKEN_SECRET` — production refuses to boot without a real one).

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /take-call?token=…` | Confirmation page. Read-only: validates the token and that the call is still active | signed `token` |
| `POST /take-call?token=…` | The atomic claim (`UPDATE … WHERE handoff_status IS NULL`), then `request_handoff_anywhere` tells the live call — directly if it runs in this process, via Redis pub/sub otherwise | signed `token` |

## `capabilities.py` — `/capabilities`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /capabilities/catalog` | The capability registry (identical for every host) | required |
| `GET /capabilities` | Every capability's activation, readiness and blockers for the authenticated host | required |
| `PATCH /capabilities/{id}` | Enable/disable, record setup progress, or update capability config; returns the full list (one change can alter another's state) | required |

## `onboarding.py` — `/onboarding`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /onboarding` · `PUT /onboarding` | Read / save the host's onboarding progress | required |
| `POST /onboarding/first-property` | Start (or re-attach to) the server-side import of the host's first Airbnb listing; the server finishes it even if the browser leaves — poll `GET /onboarding` | required |
| `POST /onboarding/complete` | Mark onboarding done | required |

## `preferences.py` — `/preferences`

Per-host UI layout, each as `GET` / `PUT` / `DELETE` (reset): `/preferences/navigation`, `/preferences/overview-widgets`, `/preferences/analytics-widgets`. All required.

## `notification_settings.py` — `/notification-settings`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /notification-settings` · `PATCH /notification-settings` | How/where the host is told about escalations and call summaries (labels, group number, email toggle) | required |
| `POST /notification-settings/preview` | Render a draft call-summary email with sample data through the real renderer | required |

## `webhooks/exotel.py` — `/webhooks/exotel`

| Method & path | Purpose | Auth |
|---|---|---|
| `POST /webhooks/exotel/call-status` | Exotel's call-status/passthru callback (call lifecycle: busy/no-answer/failed, recording URL). Independent of the live voice websocket — used for `call_sessions` logging via `call_service.attach_exotel_call` | `token` query param (`EXOTEL_WEBHOOK_TOKEN`), verified via `verify_webhook_token` |
| `GET /webhooks/exotel/call-routing` | Exotel Passthru: decides whether this inbound call is answered by Mira or routed to the host (call ownership). Fails closed toward Mira on every error path | `token` query param |
| `GET /webhooks/exotel/connect-routing` | Exotel Connect applet's dynamic target: the host number for a live Mira → host handoff, or the configured destination | `token` query param |

## `webhooks/whatsapp.py` — `/webhooks/whatsapp`

| Method & path | Purpose | Auth |
|---|---|---|
| `POST /webhooks/whatsapp/inbound` | Twilio's inbound-message callback for a guest replying to the Busy Recovery menu (see `app/services/recovery_service.py`). Routes the reply via `app/services/whatsapp_reply_service.py` — resolves the guest's existing recovery `Lead` by phone (reuses `GuestProfile`/`Lead` identity, never creates a second Lead for the same guest) and answers Property/Pricing/FAQs/Photos directly, or notifies the host for "talk to host"/free-text replies | `token` query param (`TWILIO_WHATSAPP_WEBHOOK_TOKEN`), verified via `verify_whatsapp_webhook_token` |

## `admin_auth.py` + `admin.py` — `/admin` (internal operator panel)

Not host-facing and not Clerk-authenticated. Login is an emailed one-time code for an address in `ADMIN_EMAILS`, exchanged for a 12h HS256 admin JWT (`app/auth/admin.py`); every `/admin/*` data route requires it. Frontend lives at `/admin` (`frontend/src/app/admin/`), outside the host dashboard.

| Method | Path | Notes |
|---|---|---|
| POST | `/admin/auth/request-code` | `{email}`. Same response for non-admin addresses (no allowlist disclosure). 5 codes / 15 min per address; a new code retires older ones. 503 if `ADMIN_JWT_SECRET` or Resend isn't configured. |
| POST | `/admin/auth/verify-code` | `{email, code}` → `{token, email, expires_at}`. Single-use, 10-min TTL, locked after 5 wrong attempts. |
| GET | `/admin/auth/me` | Validates the session; 403 once the address leaves the allowlist. |
| GET | `/admin/overview` · `/admin/audio` · `/admin/conversations` · `/admin/performance` · `/admin/leads` · `/admin/hosts` · `/admin/usage` | All take `start_date`, `end_date` (inclusive UTC days, default last 7) and `include_test_calls`. Cross-host aggregates from `app/services/admin_monitor_service.py`; each payload reports its own telemetry `coverage`. |
| GET | `/admin/host-roster` | Every host from the `users` table — so a host with no calls still appears — with properties, whether a number is connected, the Busy-calls mode, calls/missed/escalations in range, open tickets (split into urgent and `missed_call` "takeover"), distinct pending questions and how many have waited over 24h, unopened escalations (and whether the oldest is over 24h), cost, and a plain-language `attention` list. Hosts with something waiting sort first. Also returns `takeover`: every open `missed_call` ticket across hosts, longest-waiting first (max 50). Same range params as above. From `app/services/admin_host_service.py`. |
| GET | `/admin/hosts/{user_id}` | One host in full: the roster row, profile, their Agent settings, capabilities, per-property numbers/calls/open questions, 15 latest calls, open tickets, pending questions (oldest first, max 25), 10 latest escalations. 404 for an unknown host. |
| GET | `/admin/balances?refresh=` | Prepaid (Sarvam, Exotel: entered amount − metered spend since entry), postpaid (Groq: month spend vs budget), quota (Resend) and live provider balances (Twilio, OpenRouter, SearchApi, Bright Data, Cloudinary, Neon — `app/integrations/billing_clients.py`, cached 5 min). |
| GET / PUT | `/admin/settings/services[/{account}]` | Prepaid amounts, unit prices, USD→INR rate (`fx`). Entering a prepaid amount restarts its "spent since" clock. |
| GET | `/admin/health` · `/admin/health/stream` | Service-health snapshot for the admin home page; the stream is Server-Sent Events (one snapshot immediately, then one per monitor tick or on change) read with `fetch()` streaming because `EventSource` can't send the admin Bearer header. See [observability.md](observability.md). |
| POST | `/admin/health/digest` | Send the daily health digest to every `ALERT_EMAILS` recipient now (also the quickest way to confirm alert delivery works). |
| PUT | `/admin/settings/services/{account}` | Update one account's prepaid amount / unit prices / FX (the PUT half of the row above). |

## `GET /health` and `GET /api/v1/health/llm`

Not domain-grouped — defined directly in `app/main.py`.

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /health` | Plain liveness check (`healthcheckPath` in `backend/railway.json`) | none |
| `GET /api/v1/health/llm` | Per-Groq-model health/latency snapshot from the last periodic check (see [agents.md](agents.md)) | none |
