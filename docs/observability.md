# Observability: service health, structured logs, alerts

Goal: know a service is failing from the `/admin` home page or an email, not by reading Railway logs.

## Pieces

| File | Role |
|---|---|
| `app/observability/catalog.py` | Every monitored service: key, label, group, description, `configured()` and `critical()` (on the live-call path). **Add new services here.** |
| `app/observability/health.py` | In-memory registry. `record(service, ok/warn/error)` (sync, O(1), never raises, safe on the call path), `record_probe(...)`, `evaluate()` → state transitions, `snapshot()`. |
| `app/observability/http_hook.py` | Wraps `httpx.(Async)Client.send` once and classifies each outbound request by host (Groq, OpenRouter, Anthropic, Twilio, Exotel, Resend, SearchApi, Bright Data, Clerk, Cloudinary). New call sites are covered automatically. 5xx, 401/403 and transport errors count as failures. A 429 is a *warning*: it's throttling, and for Groq the pipeline retries the next model, so it can make a service degraded but never down. Other 4xx responses are per-request problems, not outages. Admin balance lookups (`billing_clients.fetch_balance`) are excluded, because a key without billing permission isn't a service outage. |
| `app/observability/probes.py` | Free synthetic checks every 60s (account/status endpoints only — no billable requests): Redis PING, Exotel/Twilio account, Resend domains (also flags an unverified sender domain), Clerk JWKS, Anthropic/OpenRouter key, TURN TCP connect, and SearchApi/Cloudinary every 5 min. Groq comes from `main._check_llm_health`'s existing per-model check, and Postgres from the keep-alive `SELECT 1` (now every 60s). |
| `app/observability/middleware.py` | ASGI middleware: `request_id` on every log line, plus the REST API's 5xx rate. A SQLAlchemy `handle_error` hook counts connection-level DB failures. |
| `app/observability/logging_setup.py` | One log pipeline: stdlib is routed into loguru, which writes **one JSON object per line** on Railway (`LOG_FORMAT=auto` → JSON unless `ENVIRONMENT` is development/local/test). |
| `app/services/health_monitor_service.py` | Scheduled tick (10s): evaluate, write `service_incidents`, send emails, push to SSE listeners. Also the credits cycle (15 min) and the daily digest. |
| `app/voice/pipeline.py` | Sarvam STT/TTS are counted **per call**. The first `on_error` in a call records a failure immediately, so a live outage shows mid-call; later errors in that call are ignored, so one noisy call can't look like an outage. A call that ends normally without errors records a success. STT reconnects count as warnings, and a failed reconnect as an error. A pipeline crash is an error for `voice_pipeline`. |
| `GET /api/v1/admin/health`, `GET /api/v1/admin/health/stream`, `POST /api/v1/admin/health/digest` | Snapshot, live SSE feed (read with `fetch` streaming so the admin Bearer header works), and "send digest now". |
| `frontend/src/components/admin/system-health.tsx` | Status banner, grouped service tiles with a 24h strip, incident feed, per-service detail dialog (latest error, call id with a Railway log filter, Groq models, scheduler jobs). |

## States

`up` / `degraded` / `down` / `unknown` (no evidence yet) / `not_configured`. Evaluation uses a 5-minute window of real traffic plus the latest probe:

- **down**:
  - at least 4 samples with ≥50% errors, or
  - ≥5 errors and no successes, or
  - 2 consecutive failed probes.
- **degraded**:
  - ≥2 errors at ≥20% error rate, or
  - ≥3 warnings (for example recovered STT reconnects), or
  - the probe says degraded (for example the primary Groq model is failing while a fallback serves).
- **Worsening** applies immediately. **Improving** needs 2 consecutive better evaluations, about 20s.
- **No new evidence** holds the last known state. A Sarvam outage stays red until a call succeeds, rather than going green because nobody called. The tile says "Last known".
- **Credits** (`credits:<account>`) come from the same data as the Balances page, every 15 min:
  - level ok → up
  - low (≤20%) → degraded
  - critical (≤10%) or a zero balance → down
  - a failed balance API leaves the state unchanged (unknown balance is not the same as no balance)

## Alerts (Resend, from `RESEND_FROM_EMAIL`)

Alerts are sent only where `HEALTH_ALERTS_ENABLED` is true. That defaults to production only, because dev and production watch the same third-party accounts. Both environments still show live status and record incidents.

- **Down** → immediate email. The subject is prefixed `URGENT` when the service is on the call path.
- **Degraded** for ≥ `HEALTH_DEGRADED_ALERT_MINUTES` (10) → one warning email.
- **Still down** after `HEALTH_ALERT_REMINDER_MINUTES` (120), with new failures since the last email → reminder. Credits are the exception: there are no 2-hourly reminders for credits, since the daily digest repeats them.
- **Credits emails** are worded as credits: "credits critically low — recharge now", "running low", "topped up".
- **Recovered** → a "resolved" email, sent only if something was emailed for that incident.
- **Dedupe**: each incident sends each kind of email at most once, tracked in `service_incidents`. A redeploy mid-outage continues the same incident instead of re-alerting.
- **Daily digest** at 03:30 UTC (09:00 IST): credits left (with days left at current burn), incidents in the last 24h, and each service's current state and 24h availability.

## Database deadlocks (`app/observability/db_deadlock.py`)

When Postgres breaks a deadlock it aborts one transaction (SQLSTATE `40P01`) and rolls back **everything that transaction had written**; the caller just sees a database error. Each one now emails `ALERT_EMAILS` with: the code path that ran it (outermost call first), the call/host/request it ran for, what Postgres said (which two processes were waiting on which), the statement that failed with its values, and the writes the transaction held when it was rolled back.

- **How it sees them:** a `handle_error` listener on the shared engine (so it covers every query, with no change to any caller), plus a `before_cursor_execute` listener that remembers the last 25 statements of the transaction in progress per DB connection. That memory is cleared on begin/commit/rollback, so a transaction that finished is never reported as lost. Only INSERT/UPDATE/DELETE are listed as lost writes.
- **What it never does:** raise, swallow or alter the exception (the caller gets the original error, exactly as before), or do I/O on the query path. The email is a detached task.
- **Where it emails:** same rule as the other health alerts (`HEALTH_ALERTS_ENABLED`, production by default), through the same Resend path — so if Resend itself failed recently the shared back-off applies. The structured log line `db_deadlock_detected` (full text, filterable on `event=db_deadlock`) is written everywhere, always.
- **Throttling:** one email per code path + statement per 10 minutes; later ones are counted and the next email says how many it stands for. In-process, so several workers can each send one.
- **Privacy:** values are cut at 200 characters, and a statement that touches a `password`/`token`/`secret`/`credential`/`api_key`/`otp`/`code_hash` column — or payment credentials (`billing_config`, anything Razorpay, payment links) — has its values withheld. Everything else (guest phone numbers, names, message text) is shown — it goes to admins only.
- **Limits:** it reports what was lost; it does not retry. The lost-writes list is the last 25 statements of the transaction, and only for statements issued through this engine.

## Razorpay

`api.razorpay.com` is on the passive httpx hook and in the catalog (`razorpay`, telephony group), like every external service — with one difference. It is called with each **host's own keys**, so a 401/403 or other 4xx is that host's wrong key or bad request, not Razorpay being unwell; the hook counts only 5xx, timeouts and connection errors against it. A host's bad keys are reported to that host instead (an in-app alert, at most once every six hours, and the Billing tab).

## External watchdog (outside the app, needs a one-time setup)

Nothing inside the backend can email when the backend itself is down or failing to deploy. Set up a free uptime monitor (UptimeRobot or Better Stack):

- **Check**: `GET https://<production backend>/health` every 1–5 min.
- **Pass condition**: HTTP 200 with the keyword `"status":"ok"`.
- **Alert contacts**: both admin emails.

## Gaps (deliberate)

- **Sarvam and Bright Data have no free health endpoint.** They're monitored from real traffic plus their credit balance only.
- **One process per environment is assumed.** The registry is in-memory. With multiple replicas, each would evaluate (and email) on its own, so add a Redis leader lock to `health_monitor_service.tick` first.
- **Availability counts only an incident's time in `down`.** Degraded time doesn't count against it.
