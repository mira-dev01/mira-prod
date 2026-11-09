# MIRA — Codebase Guide

MIRA is an AI-powered property-management assistant for short-term rental hosts in India. It handles guest calls (via Exotel telephony), lead qualification across a host's portfolio, booking/calendar management, rule-based pricing & negotiation, and a real-time "talk to Mira" voice-test UI in the dashboard.

Detailed docs live in `docs/`:
- [docs/architecture.md](docs/architecture.md) — system architecture, backend/frontend structure, deployment topology, auth, end-to-end call data flow.
- [docs/agents.md](docs/agents.md) — the voice agent/pipeline design: agent modes, pipecat pipeline stages, tools, prompt rules, Groq fallback, turn detection.
- [docs/database.md](docs/database.md) — full schema reference and Alembic migration history.
- [docs/research-flow.md](docs/research-flow.md) — pricing/negotiation logic, host discount policy, lead qualification, Airbnb import.
- [docs/api.md](docs/api.md) — REST endpoint reference, grouped by domain.
- [docs/observability.md](docs/observability.md) — service health monitoring (admin home page status, incidents, urgent/resolved/daily-digest emails), JSON logs, how to add a new monitored service.
- [docs/deployment.md](docs/deployment.md) — hosting (Railway backend with `dev` + `production` environments, Vercel frontend, Supabase Postgres on `dev` / Neon on `production`, Redis), which branch deploys, the pre-deploy checklist, CI, the dependency lock file.
- [docs/vapt-plan.md](docs/vapt-plan.md) — security assessment/pen-test plan: attack surface, phases, what is already fixed vs. open.
- [docs/event-loop-split-plan.md](docs/event-loop-split-plan.md) — phased plan to isolate live-call audio from API/scheduler/post-call work (not started; Phase 3's Take-Call-over-Redis part is done).
- [docs/how-it-works.md](docs/how-it-works.md) — function-level walkthrough: two full traces (property search, host escalation), a complete logging/observability map, every guardrail mechanically explained, and a file-by-file reference for all of `backend/app/`.

And in `documentation/`:
- [documentation/current_architecture.md](documentation/current_architecture.md) — the single clearest technical picture of how Mira works **today**: call flow diagram, CallCoordinator/Redis lease contract, Busy Call Recovery, conversation architecture, database boundaries, invariants. Read this first for anything touching concurrency, recovery, or the conversation-state/style/quality trio.
- [documentation/project_state.md](documentation/project_state.md) — living snapshot: what's implemented vs. uncommitted vs. planned, known limitations/risks, next priorities. Its "Status summary" section at the top is the current authoritative status; everything below it is a historical log.

At the repo root:
- [PRODUCT.md](PRODUCT.md) / [DESIGN.md](DESIGN.md) — who the host dashboard is for, and its "Visible Grid" design system (palette, type, layout blocks, rules). **Read before changing any frontend UI**; the original beige/terracotta palette is the user's and must not change.

---

## Architecture overview

MIRA has two halves that share almost nothing except the database: the **live voice pipeline**
(a call in progress — pipecat, real-time, latency-sensitive) and the **dashboard/REST API** (host
management, async, not latency-sensitive). A third, newer piece — **Busy Call Recovery** — sits
between them: it's triggered by the live-call path (a rejected call) but runs entirely outside it
(WhatsApp, async, no LLM). See [documentation/current_architecture.md](documentation/current_architecture.md)
for the full diagram and every layer's responsibility boundary — this section only calls out the
invariants a coding session must not violate.

### Critical invariants

- **A genuine guest opportunity must not silently disappear.** Three independent mechanisms exist
  for this reason — in-call `update_lead`/`escalate_to_host`, the `ensure_lead_for_engagement`
  system-level safety net (fires on `get_pricing`/`negotiate_rate`/`check_calendar` regardless of
  whether the LLM ever calls `update_lead`), and Busy Call Recovery's own lead creation for calls
  that never even reach the LLM. Do not remove or bypass any of the three without replacing what it
  guarantees.
- **Call ownership must be concurrency-safe.** `CallCoordinator` (`app/services/call_coordinator.py`)
  is the single authority on "does this host/property already have a live call?" — Redis-backed,
  atomic `SET NX`/Lua-script operations, no check-then-act race window. Never add a second place
  that decides this.
- **The lease is released the moment the guest is gone, not after post-call work.** `_CallLeaseGuard` (`app/voice/pipeline.py`) releases exactly once from whichever fires first — transport client-disconnect, the start of `on_pipeline_finished`, or `_run_pipeline`'s `finally` safety net — so a guest who redials immediately reaches Mira instead of hearing "busy" while classification/summary run. Any new teardown path must go through the guard, never call `call_coordinator.release` directly.
- **Detached work goes through `app/utils/background.spawn`, never a bare `asyncio.create_task(...)`.** The event loop only weakly references tasks, so an unreferenced one can be garbage-collected mid-flight — silently losing an escalation email, a WhatsApp, the guest-memory update or the busy-recovery message. `spawn` keeps a strong reference and logs failures. `tests/test_background_tasks_hygiene.py` fails the build if a result-discarding `asyncio.create_task`/`ensure_future` appears anywhere in `app/`. (Tasks you keep and later cancel — lease renewal, ringing tone — hold their own reference and are fine.)
- **Post-call work is isolated step by step.** `on_pipeline_finished` runs finalize → classify → summarize → promises → telemetry → lead backfill/cleanup through `_PostCallSteps` (`app/voice/pipeline.py`): a failing step logs its traceback, rolls back the shared session and the rest still run. A step whose input is missing is *skipped*, never run on a guess — lead cleanup in particular must never run on an unknown classification (it would delete a real lead as "unqualified"). Add new post-call work as another `async with steps.step(...)`.
- **Background LLM calls use the shared clients in `app/integrations/llm_clients.py`** (one client per provider per profile per event loop, explicit time limits, closed at shutdown) — never `AsyncGroq(...)`/`AsyncOpenAI(...)` per request. Post-call classification/summary additionally run under a concurrency cap and can use their own Groq model/key (`POST_CALL_*`) so a burst of finished calls cannot consume the rate limit a live call needs. The live voice LLM keeps its own client in `pipeline.py`.
- **Take Call reaches the live call across processes.** `handoff_signal.request_handoff_anywhere` sets the Event directly when the call is in this process and otherwise publishes on Redis (`app/integrations/redis_signal_client.py`, channel `mira:handoff_requested`); one listener per process (`handoff_signal.run_listener`, started in `lifespan`) turns it into a local Event. Best-effort by design — the claim is durable in Postgres first — and a delivery failure is logged (`handoff_signal_not_delivered`), not silent.
- **The pipeline must not contain business logic for concurrency coordination.**
  `app/voice/pipeline.py` only ever sees `CallCoordinator.acquire_or_reject`'s two-value
  `Decision` (`START_PIPELINE`/`BUSY_RECOVERY`) — never lease internals, never a reason code.
- **Redis cache semantics and Redis lease semantics are separate.** `app/integrations/redis_client.py`
  (optional TTL cache, fails open/no-ops silently) and `app/integrations/redis_lease_client.py`
  (CallCoordinator's correctness-bearing lease operations, explicit fail-open policy owned one
  layer up in `call_coordinator.py`) are deliberately different modules with different failure
  contracts — do not merge them or add lease-specific behavior to the cache module.
- **Validators must not introduce hidden LLM regeneration.** Every corrective mechanism in the
  pipeline (7 guards + `StyleComplianceMonitor`) either deterministically rewrites/truncates
  already-generated text, or nudges the *next* turn's prompt. Nothing calls the LLM a second time
  mid-turn to "fix" a response — the old `ResponseComplianceProcessor` did this and was removed for
  exactly that reason (multi-second dead air on a live call). If a genuinely new regeneration need
  arises, that's a deliberate architectural decision, not something to add quietly inside a guard.
- **`ConversationStyle` is responsible for *how* Mira speaks** (language, script, tone) —
  hysteresis-smoothed over a rolling window of guest turns, computed by `StyleEngine`.
- **`ConversationState` is responsible for conversation facts/state** (locked property, slots,
  recommendations shown, escalation flag, closing state, conversation goal).
- **`ConversationQuality` is observational/quality data** and must not silently become a
  behavioral feedback loop — the one exception (`pending_style_correction`, read by
  `StatePromptSyncProcessor` to ask `ConversationStyle` for a more emphatic rendering of the same
  style) is deliberate and narrow; do not add a second such bridge without equally explicit
  justification.
- **External dependency failures must not unnecessarily terminate a live guest call.** Every
  optional integration (Redis, SMTP, WhatsApp/Twilio, SearchApi, Bright Data) fails open or no-ops
  rather than raising into the call path — match this discipline for any new integration.
- **Health observation never changes behavior.** `app/observability/` only records and reports — `health.record()` is sync, I/O-free and never raises (safe on the live-call path), and nothing in it is read back into a call. New external integrations get monitored by adding a host to `http_hook.py` (if httpx) and a `ServiceDef` to `catalog.py`, not with ad-hoc try/except-and-log.
- **Nothing about a property is spoken unless it is on file.** The agent's only route to property facts is `search_faq` (`app/services/property/kb_retrieval.py`), which returns just the matching facts from the property's knowledge base (verified FAQ rows + property fields + active seasonal notes, stored as `property_chunks` rows with `chunk_type="fact"`) or an explicit "nothing on file" that is logged as a property-scoped gap for the host. The Guest Support prompt deliberately does NOT inline house rules/neighborhood/amenities/FAQ/seasonal notes, and `KnowledgeGroundingGuardProcessor` (`app/voice/kb_grounding_guard.py`) checks every spoken sentence (and `send_whatsapp` bodies) against what the agent was actually told. Never add a fallback that hands the model "everything we know" or lets retrieval fall back to model knowledge; never re-inline KB text into the prompt; keep fact rows in sync (`sync_property_facts`) from any new write path that changes a property's KB fields or FAQ rows. The same discipline covers *offers*: the Lead prompt's `Locations:` block lists the types/features each city really has (`_location_facets`), the workflow hardcodes no example stay types, and the guard's `analyze_offer` flags a spoken question naming an amenity/kind of place no property offers — never add example choices to a prompt, and never feed the portfolio corpus (`build_portfolio_text`) into the assertion-support corpus.
- **Location is matched by where a property is, not by what its text says.** `app/services/geo/` resolves the area a guest names (a state, or a place near the host's properties) and `recommend_properties` judges any property that has a point or state by position; free-text matching applies only to properties with neither, so a description that merely mentions an area can't pull in a distant property. Every step fails open to the old text match, and nothing on the call path calls an external geocoder. Distances are spoken as straight-line.
- **Do not duplicate existing services.** Busy Call Recovery deliberately reuses
  `get_or_create_guest_profile`/`upsert_lead`/`create_notification`/the Twilio WhatsApp sender
  rather than inventing parallel versions — follow the same instinct for new work.

---

## Repo layout

```
mira-prod/
├── backend/          FastAPI + Python 3.12
│   ├── app/
│   │   ├── api/v1/   REST endpoints (one file per domain) + webhooks/exotel
│   │   ├── auth/     Clerk session-token verification (dependencies.py) + admin OTP/JWT (admin.py)
│   │   ├── config.py pydantic-settings; all env vars documented here
│   │   ├── database.py  SQLAlchemy async engine + session factory
│   │   ├── integrations/ Exotel client, iCal fetcher
│   │   ├── models/   SQLAlchemy ORM models
│   │   ├── prompts/  LLM system-prompt builders (guest-support + lead-agent)
│   │   ├── schemas/  Pydantic request/response + tool-arg schemas
│   │   ├── services/ business logic (calendar, pricing_engine, lead, faq, geo/ location search, …)
│   │   ├── utils/    shared helpers
│   │   └── voice/    pipecat pipeline + tool wrappers (+ assets/)
│   ├── alembic/      DB migrations
│   ├── scripts/      one-off tools (load_gazetteer, set_property_locations, backfills, template creators)
│   ├── seed_demo.py  demo login + 12 Indian properties (see Demo account)
│   ├── tests/        pytest (all async, hits a real test DB — no DB mocking)
│   └── requirements.txt
└── frontend/         Next.js (TypeScript, Tailwind, shadcn/ui)
    └── src/
        ├── app/dashboard/  per-domain pages (properties, bookings, calls,
        │                   leads, pricing, calendar, faq, guests,
        │                   technicians, settings)
        ├── components/     shared UI (stat-card, sparkline, date-range-picker,
        │                   talk-to-mira-dialog, notifications-feed, …) + ui/
        ├── hooks/          custom React hooks (use-async, use-date-range)
        └── lib/            API client, auth context, types
```

---

## Quick start

```bash
# Backend
cd backend
# create backend/.env -- every variable is documented in app/config.py (there is no .env.example)
python -m uvicorn app.main:app --reload

# Frontend
cd frontend
npm install
npm run dev   # talks to NEXT_PUBLIC_API_BASE_URL, baked in at build time
              # Clerk sign-in does NOT work on localhost; for UI work use design preview mode:
              # NEXT_PUBLIC_UI_PREVIEW=1 npm run dev -- -p 3100  (sample data, no sign-in; see frontend/README.md)

# Tests (real Postgres required — do not mock the DB)
cd backend
pytest

# Location search by map position (one-time per database; see docs/research-flow.md)
python -m scripts.load_gazetteer --download --dir /tmp/geonames --admin1 11 12 24 25 33 39   # states to load; omit --admin1 for all India (~114 MB)
python -m scripts.set_property_locations --user-email <host email> --file scripts/data/hushstays_locations.json   # preview; add --apply
```

Deployment: backend on Railway (`dev` and `production` environments, each with its own database), frontend on Vercel — see [docs/architecture.md](docs/architecture.md) and [docs/deployment.md](docs/deployment.md) (hosting, which branch deploys, pre-deploy checklist, CI, dependency lock file). Render is no longer used.

**Tests are hermetic and CI runs them.** `backend/tests/conftest.py` rebuilds `settings` as if there were no `.env` (a developer `.env` holds real Exotel/Twilio/Groq/OpenRouter credentials; tests used to call them), so a run is identical locally and in CI and never spends money. Tests that deliberately call real APIs are marked `live_api` and run only with `RUN_LIVE_TESTS=1`. `.github/workflows/ci.yml` runs `ruff check .` (`backend/ruff.toml`: real-defect rules) and `pytest` on every push/PR. Dependencies install from `backend/requirements.lock` (exact + hashed); regenerate it with the `uv pip compile …` command in `docs/deployment.md` after editing `requirements.txt` — CI rejects a stale lock. Which branch deploys, and the pre-deploy checklist: [docs/deployment.md](docs/deployment.md).

## Key env vars (`backend/app/config.py`)

| Var | Notes |
|---|---|
| `DATABASE_URL` | Auto-rewritten to `postgresql+asyncpg://` scheme on startup. Railway `dev`: Supabase **session-mode** pooler (port 5432); `production`: Neon — see `DB_POOL_*` below for sizing |
| `LLM_PROVIDER` | `groq` (default) / `anthropic` / `openrouter` |
| `GROQ_MODEL` | Configured default/first choice, `openai/gpt-oss-120b` |
| `GROQ_MODELS` | JSON-array string; the fallback chain `_build_llm()` walks — see [docs/agents.md](docs/agents.md) |
| `OPENROUTER_MODEL` | Used when `LLM_PROVIDER=openrouter`; last-resort fallback otherwise |
| `TURN_URL` | Primary TURN relay (UDP). Must start with `turn:`/`turns:` — validated at startup. Leave unset if no TURN server. |
| `TURN_URL_TLS` | Optional TURNS-over-TCP:443 relay for mobile networks that block UDP. Same username/credential as `TURN_URL`. |
| `TURN_USERNAME` / `TURN_CREDENTIAL` | Required if any `TURN_URL*` is set |
| `SARVAM_API_KEY` | STT + TTS for voice pipeline |
| `SARVAM_TTS_MODEL` / `SARVAM_TTS_SPEAKER` | `bulbul:v3` / `roopa` |
| `EXOTEL_*` | Telephony integration |
| `CORS_EXTRA_ORIGINS` | Comma-separated extra CORS origins. `FRONTEND_BASE_URL` is always allowed. |
| `BRIGHT_DATA_API_KEY` | Airbnb listing import — see [docs/research-flow.md](docs/research-flow.md). Not set = "Import from Airbnb" fails with a clear error, doesn't crash. |
| `SEARCHAPI_API_KEY` | [SearchApi.io](https://www.searchapi.io/airbnb-api) — two uses, both scoped to `exact_airbnb_pricing` properties only (see [docs/research-flow.md](docs/research-flow.md)): (1) daily comparable-pricing refresh (`smart_pricing_service.py`), one call per city; (2) live per-listing price fetch during `get_pricing`/`negotiate_rate`/`check_calendar`, one call per pricing question. Free tier's request allowance is small and shared across both; unset = both no-op/fall back cleanly. |
| `TURN_DETECTION_STRATEGY` | `vad_fixed` (default) / `hybrid_experimental`. Local-only experiment — see [docs/agents.md](docs/agents.md). |
| `BACKCHANNEL_FILTER_ENABLED` / `BACKCHANNEL_MIN_INTERRUPT_SECS` | Full-duplex building block (default off / 0.5): while Mira is speaking, a guest sound only interrupts once it lasts this long, and a pure "hmm/haan/ok ji" is dropped instead of becoming an LLM turn. See "Full-duplex building blocks" in [docs/agents.md](docs/agents.md). Enabled on Railway `dev` only so far. |
| `BARGE_IN_DUCKING_ENABLED` / `BARGE_IN_DUCK_GAIN` | Full-duplex building block (default off / 0.3): Mira's voice drops to this fraction of volume while the guest is talking over her, instead of stopping. Enabled on Railway `dev` only so far. |
| `ECHO_CANCELLATION_ENABLED` | Full-duplex building block (default off): WebRTC echo cancellation on Exotel phone calls so Mira doesn't hear herself. Needs the optional `aec-audio-processing` package, which the Dockerfile tries to compile (a failed compile never blocks a deploy; the call just runs without it). Off everywhere until a Railway build log confirms the package installed. |
| `RESEND_API_KEY` / `RESEND_FROM_EMAIL` | Escalation + call-summary email summaries, via Resend's HTTP API (`app/integrations/email_client.py`). **Not SMTP** as of 2026-09-24 — confirmed via a from-inside-the-container connectivity test (`railway ssh`) that Railway blocks outbound SMTP ports (587/465) at the network level on both `dev` and `production`, identically (`OSError: [Errno 101] Network is unreachable`), while port 443 connects instantly — no SMTP host/provider/credential combination could ever have worked here. Resend over SendGrid: Resend has a usable free tier; the SendGrid account available is a paid one, no need for a second paid provider here. `RESEND_FROM_EMAIL` needs to be on a domain Resend has verified (SPF/DKIM) or delivery silently fails past the API-success point. Unset = escalations/summaries still create their in-app notification, just skip the email. |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_WHATSAPP_FROM` / `TWILIO_MESSAGING_SERVICE_SID` | Real WhatsApp send for `send_whatsapp`/`send_photos` via a real WhatsApp Business number (migrated off Twilio's Sandbox 2026-09-12) — see [docs/agents.md](docs/agents.md). Sender identity is `TWILIO_MESSAGING_SERVICE_SID` (Twilio's recommended production pattern, preferred when set) or `TWILIO_WHATSAPP_FROM` (a raw number) — set one or the other. No opt-in required anymore, but freeform (non-template) messages still only reach a recipient within WhatsApp's 24h customer-service window (a general WhatsApp Business API rule, not sandbox-specific — Twilio error `63016` outside it). Unset (both sender vars) = falls back to the in-app notification only. |
| `TWILIO_ESCALATION_TEMPLATE_SID` | ContentSid of the `mira_escalation` WhatsApp template (`scripts/create_escalation_template.py`) — gives escalations a real "Go to Dashboard" button. Unset = falls back to a plain-text message with a bare URL. Also reused for RecoveryService's host-facing "missed call" WhatsApp (`app/services/recovery_service.py`) — same shape, not a separate template. |
| `TWILIO_BUSY_RECOVERY_TEMPLATE_SID` | ContentSid of the `mira_busy_recovery` WhatsApp template (`scripts/create_busy_recovery_template.py`) — the numbered Property/Pricing/FAQs/Photos/Talk-to-host menu RecoveryService sends a guest whose call was rejected as busy. Unset = falls back to an equivalent plain-text message. |
| `TWILIO_WHATSAPP_WEBHOOK_TOKEN` | Shared-secret path token for the inbound WhatsApp webhook (`POST /api/v1/webhooks/whatsapp/inbound`, `app/services/whatsapp_reply_service.py`) that routes a guest's reply to the busy-recovery menu — same convention as `EXOTEL_WEBHOOK_TOKEN`. Configure as this WhatsApp Business number's "WHEN A MESSAGE COMES IN" webhook URL in the Twilio console — note the full real path is `/api/v1/webhooks/whatsapp/inbound`, not just `/webhooks/whatsapp/inbound` or any other shape; a URL pointed at the wrong path 404s silently from Twilio's perspective. |
| `TWILIO_VOICE_WEBHOOK_TOKEN` | Shared-secret path token for Twilio Voice (`app/integrations/twilio_voice.py`, `run_voice_pipeline_twilio` in `app/voice/pipeline.py`) — an entirely separate integration from Exotel telephony and from the WhatsApp number above (separate token, no code overlap), added purely so real-call testing can continue on Twilio's free trial when Exotel credits run out. Reuses `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`. |
| `PAYMENT_ENCRYPTION_KEY` | Fernet key(s) that encrypt each host's Razorpay key secret at rest (`app/utils/secrets.py`). **Required before any host can connect Razorpay** — unset, the Billing tab says payments aren't enabled and nothing is stored. Comma-separated to rotate (first encrypts, all decrypt). Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Losing it makes saved host keys unreadable (hosts re-enter them); never reuse another secret here. |
| `AUDIO_INPUT_TELEMETRY_ENABLED` / `USAGE_METERING_ENABLED` | Kill-switches (both default on) for the observation-only per-call audio telemetry (`app/voice/audio_input_observer.py`) and usage/performance metering (`app/voice/call_metrics.py`, `app/services/usage_meter.py`). Neither ever changes what Mira hears or says. |
| `ALERT_EMAILS` / `HEALTH_ALERTS_ENABLED` | Service-health alert + daily-digest recipients (default: both admins) and the on/off switch (unset = production only, since dev and prod watch the same accounts). Tuning: `HEALTH_DEGRADED_ALERT_MINUTES` (10), `HEALTH_ALERT_REMINDER_MINUTES` (120), `HEALTH_DIGEST_HOUR_UTC`/`_MINUTE_UTC` (03:30 = 09:00 IST). See [docs/observability.md](docs/observability.md). |
| `KB_GUARD_MODE` | Knowledge grounding guard: `off` / `shadow` (default — logs `kb_guard_would_block`, changes nothing) / `enforce` (replaces an unsupported spoken sentence with the not-on-file line and logs a `guard_blocked` gap). Run shadow ~a week, review false positives, then enforce. See [docs/agents.md](docs/agents.md). |
| `KB_FOUND_COSINE` / `KB_POSSIBLE_COSINE` / `KB_FOUND_COVERAGE` / `KB_POSSIBLE_COVERAGE` / `KB_EMBEDDING_TIMEOUT_SECONDS` | `search_faq` retrieval thresholds (starting points — tune with `python -m scripts.tune_kb_retrieval` against a real `OPENROUTER_API_KEY`; golden questions live in `backend/tests/kb_golden_set.py`). Embedding failure/timeout degrades to lexical-only matching, never to model knowledge. |
| `LLM_TEMPERATURE` | Sampling temperature for the voice LLM (default 0.5; negative = provider default). |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT` / `DB_POOL_RECYCLE_SECONDS` | Postgres pool per process (defaults 5 / 10 / 30s / 1800s — the first three equal SQLAlchemy's old implicit defaults). Capacity = size + overflow; against Supabase's **session-mode** pooler (port 5432) every pooled connection is a slot of the plan's limit, so keep `(size + overflow) × replicas` below it. The 60s DB health job warns (`db_pool_pressure`) at 80% checked out. |
| `POST_CALL_GROQ_MODEL` / `POST_CALL_GROQ_API_KEY` / `POST_CALL_LLM_MAX_CONCURRENCY` | Post-call classification + summary vs. the live voice LLM's Groq quota. Model and key unset = same as `GROQ_MODEL`/`GROQ_API_KEY` (no behaviour change); setting a different model (e.g. `openai/gpt-oss-20b`) puts them in a separate per-model rate-limit bucket — a quality trade-off for you to choose. Concurrency cap default 2. A Groq failure now falls back to OpenRouter when configured, instead of degrading straight to UNKNOWN. |
| `LLM_FIRST_CHUNK_TIMEOUT_SECONDS` | Default 3.0. How long a Groq model gets to produce its first streamed chunk before `_FallbackGroqLLMService` fails over to the next model in `GROQ_MODELS` (the last model gets 3×). Failover now also covers provider 5xx/404/408, connection errors and timeouts — not just 429 — so a stalled or erroring model no longer means dead air. Other 4xx are not retried. Only a 429 or a retired-model 404 marks the model "down" in `llm_health` for other callers; a 5xx, dropped connection or slow first token fails over for that turn only (so one blip can't pin everyone to a weaker fallback model). On the first failover of a turn Mira speaks a fixed "one moment" filler (`TTSSpeakFrame`, `append_to_context=False`, so the model just continues the conversation) and holds the silence watchdog (`hold_nudges`/`release_nudges`) so a slow retry is never answered with "Hello? Are you there?"; if every model fails she apologises and asks the guest to repeat (`_ReplyGap` in `app/voice/pipeline.py`). |
| `LIVE_PRICING_BUDGET_SECONDS` | Default 4.0. Hard ceiling on how long `get_pricing`/`negotiate_rate` wait for the live SearchApi.io rate (`pricing_engine._live_total_within_budget`). Past it the call is answered from the static `base_price`, flagged `is_estimate` + `live_rate_unavailable` (so no payment link is sent off it and the spoken caveat says so), while the lookup keeps running in the background and warms the cache for the guest's next question. Replaces the old unbounded 2×15s path. |
| `TAKE_CALL_TOKEN_SECRET` / `EXOTEL_WEBHOOK_TOKEN` | **Production refuses to boot** if either is blank/`change-me`, or if `TAKE_CALL_TOKEN_SECRET` is under 32 bytes (`config.py` validator). Twilio webhook tokens left at `change-me` fail *closed* in production (every request rejected) instead of crashing startup. |
| `LOG_FORMAT` | `auto` (default: one JSON object per line unless `ENVIRONMENT` is development/local/test) / `json` / `text`. JSON lines carry `call_session_id`/`host_id`/`request_id`/`service`, so Railway can filter by `@call_session_id:<id>`. |
| `ADMIN_EMAILS` / `ADMIN_JWT_SECRET` | Internal `/admin` panel login (`app/api/v1/admin_auth.py`): emailed one-time code (via Resend) → HS256 admin JWT. Separate from host Clerk auth on purpose. `ADMIN_JWT_SECRET` has no default — unset disables admin login entirely (admin routes 404). Allowlist re-checked on every request. |
| `NEON_API_KEY` / `NEON_PROJECT_ID` | Optional — Neon compute/storage usage card on `/admin/balances` (`app/integrations/billing_clients.py`). Unset = card shows "not configured". |
| `REDIS_URL` | Two independent uses (see `app/integrations/redis_client.py`'s own docstring — deliberately not merged into one module): (1) optional TTL cache for SearchApi.io pricing responses, fails open/no-ops if unset or unreachable; (2) **CallCoordinator's active-call ownership/lease mechanism** (`app/services/call_coordinator.py`, `app/integrations/redis_lease_client.py`) — Redis is the sole source of truth for "is this host/property already on a live call," Postgres no longer participates. Unlike use (1), a Redis outage here is not a silent no-op: `acquire_or_reject` fails open (the call still proceeds) but logs loudly (`lease_redis_unavailable`) as a degraded-protection signal; `renew`/`release` always fail open silently. `CallLease`/`call_leases` (the pre-migration Postgres table) still exists but is staged for removal — nothing writes to it anymore. |

---

## Demo account

`backend/seed_demo.py` creates `demo@mira.ai` / `MiraDemo2024` with 12 realistic Indian properties (Goa, Jaipur, Udaipur, Rishikesh, Coorg, Manali, Shimla, Alleppey, Bangalore, Mumbai, Nainital, Kasauli). Each property's `neighborhood_info` names its state so region queries ("properties in Kerala/Himachal") resolve — `recommend_properties` matches `preferred_location` against both `city` and `neighborhood_info`.

```bash
cd backend
DATABASE_URL="postgresql://…" python3 seed_demo.py   # additive; doesn't touch existing users
```

---

## Common pitfalls

- **`ValueError: malformed uri: invalid scheme`** in voice endpoint — a `TURN_URL*` is set to an `https://` or bare hostname. Fix: use `turn:host:port` / `turns:host:443?transport=tcp`, or delete the env var.
- **ICE timeout on cloud hosts (Railway)** — STUN-only works on localhost but not on cloud hosts where UDP is blocked. A TURN server is required for browser voice tests in production.
- **Browser test works on WiFi but fails on mobile data** — mobile carriers block/throttle UDP TURN. Set `TURN_URL_TLS` to a TURNS-over-TCP:443 relay (looks like HTTPS, gets through).
- **Voice call gets slow/again after idle, or 429 errors** — a per-model Groq rate limit on `gpt-oss-120b` (account has been on a paid Groq plan since 2026-07-07, not free tier — limits are much higher but not unlimited, especially under call bursts). The 60s health check + `_FallbackGroqLLMService` route around it automatically; check `GET /api/v1/health/llm` to see which models are marked down.
- **`postgres://` vs `postgresql+asyncpg://`** — handled automatically by the `_use_asyncpg_driver` validator in `config.py`.
- **`TypeError: connect() got an unexpected keyword argument` on deploy/alembic startup** — a `DATABASE_URL` query param asyncpg's own `connect()` doesn't accept as a kwarg got forwarded instead of stripped. Confirmed live 2026-09-19: a Neon connection string's `?sslmode=require&channel_binding=require` crashed every startup on `channel_binding` specifically — only `sslmode`/`ssl` were being stripped at the time. `channel_binding` is a libpq-level TLS channel-binding negotiation hint that asyncpg already negotiates automatically as part of its own TLS handshake, so dropping the param doesn't weaken the connection. Fixed by stripping a full `_ASYNCPG_UNSUPPORTED_QUERY_PARAMS` tuple (`config.py`), not just `sslmode`/`ssl` — if a new provider's connection string adds yet another param asyncpg's `connect()` rejects, extend that tuple rather than special-casing it again.
- **`GROQ_MODEL` deprecation** — Groq renames/deprecates model ids periodically (`llama-3.3-70b-versatile` was removed 2026-06-17). Re-check `GROQ_MODELS` against `client.models.list()` before editing, and confirm any new model supports function calling (required for tools).
- **`Module not found: react-day-picker` (frontend)** — local `node_modules` is stale after a merge that added the calendar component. Run `npm install` in `frontend/`.
- **Greeting garbled / not spoken** — don't ask the LLM to generate the opening line and don't push `TTSSpeakFrame` into `llm`/`tts` directly. Use `worker.queue_frame(TTSSpeakFrame(first_message))` on connect (see [docs/agents.md](docs/agents.md)).
- **"I changed `.env`/the code but nothing's different" (the single most common time-sink this session)** — a running `uvicorn` process only picks up `.py` file changes if started with `--reload`, and even then, **`.env` changes are never hot-reloaded** — `pydantic-settings`' `Settings()` is read once via `@lru_cache` at process start. Always fully kill and restart (`pkill -f "uvicorn app.main:app"`, then re-run) after any `.env` edit, and confirm with `curl localhost:8000/health` or an OpenAPI check (`m.app.openapi()["paths"]`) that new routes actually exist before assuming a fix didn't work. A route that "should" exist but returns `405 Method Not Allowed` (not `404`) is a strong signal you're hitting a stale process — Starlette matched the path against a different, older route's pattern (e.g. `/{property_id}`) instead.
- **A `<button>`/element with both `addEventListener(...)` and a later `.onclick = ...` fires both on click** — these are separate handler slots; assigning `.onclick` does not remove or replace an `addEventListener` listener. Bit the voice test page's connect/end-call button this way (ending a call also silently re-triggered the original connect handler, looking like an auto-restarted call). Fix: register both states through the same slot (either both `.onclick =` or explicit `removeEventListener`), never mix.
- **Voice-agent bugs from a live call log are usually more informative than they look** — `logger.debug` lines like `(strategy: HybridCompletenessUserTurnStopStrategy#0)` or `(strategy: None)` name the exact class/mechanism that fired. `strategy: None` specifically means pipecat's own generic stuck-turn watchdog closed the turn, not any registered strategy — a real signal the strategy's own logic never completed, not just "it was a bit slow." Same discipline applies to which *provider* actually handled a completion — don't infer it from config (e.g. "OPENROUTER_MODEL happens to be the same leaky model, so it must be that path"); grep the Railway log window for the exact call for the actual request URL (`api.groq.com` vs `openrouter.ai`) and the service class name (`_FallbackGroqLLMService` only exists on the Groq path). Confirmed live 2026-07-27: a plausible-sounding OpenRouter theory for a degenerate-completion bug was flat wrong once the actual logs for that exact call were pulled — the request never left Groq.
- **Banning a specific bad LLM phrasing via regex is whack-a-mole, not a fix** — a guard that only rewrites text matching a known-bad pattern (e.g. `loop...host`) will keep missing new phrasing variants of the same underlying failure ("let me open the host" never matched a `loop...host` regex) indefinitely; each "fix" only ever covers wording already observed live. When the safe replacement text is fixed and known regardless of context (e.g. escalation acknowledgements), the durable fix is to stop detecting altogether and unconditionally replace the reply after the triggering event — no detection step means no coverage gap. See `app/voice/escalation_phrase_guard.py`.
- **Joining a list of items with a delimiter that can also appear inside an individual item silently corrupts round-trip parsing** — `handle_recommend_properties` joined multiple properties with `" | "`, but real (Airbnb-imported) property names routinely contain a literal `|` themselves, so splitting the combined string back apart tore names in half at every internal `|`, not just between properties (confirmed live: a shared brand-name suffix got read back as if it were the property name). Before picking a join delimiter for machine-parsed output, confirm it can't appear in the data being joined — a newline is almost always safer than a punctuation character for free-text fields.
- **A substring match against a free-text field can true-positive on the wrong reason** — `recommend_properties`' Goa region filter matched the literal phrase "South Goa" against `neighborhood_info`, which incidentally mentions "Dabolim (South Goa airport)" as a travel-time reference on nearly every North Goa property's listing — a real North Goa property matched a South Goa query for a reason that has nothing to do with its actual location. When matching a category/region query against a free-text description field, prefer matching against a controlled list of expected values (a locality list, an enum) over a raw substring match against prose that can mention the term for unrelated reasons.
- **"Himachal" / "near Kasol" matched nothing and the agent handed a guest to the host** (confirmed live 2026-11-07) — Airbnb imports store a *village* as `Property.city` ("Pangan", "Jari") and no text ever says the state, so the free-text location match found zero properties; `recommend_properties` then returned "let me connect you with the host" and the model escalated a guest it had just quoted villas to. Fixed by matching on map position (`app/services/geo/`, `places` gazetteer, `properties.latitude/longitude/state`): a property with a point is judged by where it is, free text only applies to properties with none, and a miss now lists the states the host does cover instead of handing off. Three traps found building it: a place name is not unique (14 villages are called "Jari", and a hamlet called "Jaipur" sat nearer a villa than the city did) so a real town beats a nearby hamlet of the same name; a spoken distance must be straight-line ("about 28 km from Manali" is ~75 km by road); and `python -m scripts.load_gazetteer` must be run per database or lookups silently find nothing and the old text match runs alone.
- **Next dev server serves a stale stylesheet after editing `globals.css`** — Turbopack's on-disk cache can keep the old compiled CSS even after a restart (confirmed 2026-11-09: edited tokens never reached the page). `rm -rf frontend/.next` (gitignored build output) and restart.
- **A fully implemented, tested function with no production caller is a silent functional gap, not just dead code** — `call_coordinator.renew()` (Busy Call Recovery's lease-keepalive) had a real regression test and a docstring describing a periodic renewal loop, but nothing in `app/voice/pipeline.py` ever actually called it. `DEFAULT_LEASE_TTL` is 45s, so every real call longer than 45s was silently losing its busy-recovery lease mid-call — a second incoming call after that point would wrongly get `START_PIPELINE` instead of `BUSY_RECOVERY`, defeating the feature for most real calls, with no error, no log line, and passing tests throughout (the tests exercised `renew()` directly, never the absence of a caller). Found only by a cross-file "who actually calls this" audit, not by running the test suite. Fixed by wiring a periodic `_renew_call_lease_periodically` background task into `_run_pipeline`'s existing lease-lifecycle `finally` block (`app/voice/pipeline.py`) — see [docs/agents.md](docs/agents.md)'s "Busy Call Recovery" section. When a function's own docstring describes behavior ("renewed roughly every N seconds by a caller") that no other file's code actually performs, treat that as a bug report, not documentation.
