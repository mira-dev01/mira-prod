# REST API Reference

All routes are mounted under `/api/v1` (`app/main.py`). Unless noted, **Auth** = `get_current_user` (JWT bearer, `Authorization: Bearer <token>`) via `app/auth/dependencies.py`. All routes scope results to the authenticated user's own data (`current_user.id`) unless stated otherwise. See [architecture.md](architecture.md) for the auth mechanism and CORS setup, [agents.md](agents.md) for the voice pipeline itself, and [database.md](database.md) for the underlying tables.

## `auth.py` — `/auth`

| Method & path | Purpose | Auth |
|---|---|---|
| `POST /auth/register` | Create a basic user account, returns a JWT | none |
| `POST /auth/register-host` | Full host registration (business profile, optional `ical_url`); triggers a Bright Data Airbnb import in the background if listing URLs are given | none |
| `POST /auth/login` | Email+password → JWT | none |
| `GET /auth/me` | Current user profile | required |
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

See [research-flow.md](research-flow.md) for the two import paths' internals.

## `bookings.py` — `/bookings`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /bookings` | List bookings across the host's properties | required |
| `POST /bookings` | Create a booking | required |
| `DELETE /bookings/{id}` | Cancel a booking | required |
| `POST /bookings/check-availability` | Check a property's availability for a date range | required |

## `calls.py` — `/calls`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /calls` | List call sessions; filters: `status`, `urgency`, `limit`, `start_date`, `end_date`, `include_test_calls` (default excludes browser-test calls) | required |
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

## `host_discount_rules.py` — `/host-discount-rules`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /host-discount-rules` | List the host's discount rules (any status) | required |
| `POST /host-discount-rules/parse` | LLM-parse `discount_policy_text` into draft `pending_validation` rules | required |
| `PATCH /host-discount-rules/{id}` | Edit/approve a rule (only `status="approved"` rows are used by negotiation) | required, must own the rule |
| `DELETE /host-discount-rules/{id}` | Delete a rule | required, must own the rule |

See [research-flow.md](research-flow.md) for how these rules feed `negotiate_rate`.

## `analytics.py` — `/analytics`

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /analytics/summary` | Dashboard stat cards: total/completed/escalated calls, open notifications, pipeline value, open leads, answer rate. Params: `days` (default 30), `start_date`/`end_date` (override `days`), `include_test_calls` | required |
| `GET /analytics/timeseries` | Bucketed time series for one metric: `total_calls`, `completed_calls`, `escalated_calls`, `pipeline_value`, `open_leads`. Params: `metric`, `start_date`, `end_date`, `include_test_calls` | required |

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
| `WS /voice/exotel/ws/{token}` | Real Exotel call websocket (raw-PCM media protocol). Configured directly in the Exotel Voicebot Applet as `wss://<backend>/api/v1/voice/exotel/ws/<EXOTEL_WEBHOOK_TOKEN>` -- token is a PATH segment, not a query param: Exotel's Voicebot Applet strips query strings from the configured WSS URL before connecting (confirmed live) | `token` path segment (`EXOTEL_WEBHOOK_TOKEN`), not JWT |
| `POST /voice/test/offer` | WebRTC offer/answer signaling for the in-dashboard "talk to Mira" test. Omit `property_id` for the portfolio-wide Lead Agent; include it for Guest Support on one property | required |
| `GET /voice/test` | Standalone browser test page (mic in/speaker out), opened with `?token=<JWT>&property_id=<optional>` | `token` query param (JWT), not a bearer header |

See [agents.md](agents.md) for what runs behind these endpoints.

## `webhooks/exotel.py` — `/webhooks/exotel`

| Method & path | Purpose | Auth |
|---|---|---|
| `POST /webhooks/exotel/call-status` | Exotel's call-status/passthru callback (call lifecycle: busy/no-answer/failed, recording URL). Independent of the live voice websocket — used for `call_sessions` logging via `call_service.attach_exotel_call` | `token` query param (`EXOTEL_WEBHOOK_TOKEN`), verified via `verify_webhook_token` |

## `GET /health` and `GET /api/v1/health/llm`

Not domain-grouped — defined directly in `app/main.py`.

| Method & path | Purpose | Auth |
|---|---|---|
| `GET /health` | Plain liveness check (Render's `healthCheckPath`) | none |
| `GET /api/v1/health/llm` | Per-Groq-model health/latency snapshot from the last periodic check (see [agents.md](agents.md)) | none |
