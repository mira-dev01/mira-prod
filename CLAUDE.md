# MIRA — Codebase Guide

MIRA is an AI-powered property-management assistant for short-term rental hosts in India. It handles guest calls (via Exotel telephony), lead qualification across a host's portfolio, booking/calendar management, rule-based pricing & negotiation, and a real-time "talk to Mira" voice-test UI in the dashboard.

Detailed docs live in `docs/`:
- [docs/architecture.md](docs/architecture.md) — system architecture, backend/frontend structure, deployment topology, auth, end-to-end call data flow.
- [docs/agents.md](docs/agents.md) — the voice agent/pipeline design: agent modes, pipecat pipeline stages, tools, prompt rules, Groq fallback, turn detection.
- [docs/database.md](docs/database.md) — full schema reference and Alembic migration history.
- [docs/research-flow.md](docs/research-flow.md) — pricing/negotiation logic, host discount policy, lead qualification, Airbnb import.
- [docs/api.md](docs/api.md) — REST endpoint reference, grouped by domain.
- [docs/how-it-works.md](docs/how-it-works.md) — function-level walkthrough: two full traces (property search, host escalation), a complete logging/observability map, every guardrail mechanically explained, and a file-by-file reference for all of `backend/app/`.

And in `documentation/`:
- [documentation/current_architecture.md](documentation/current_architecture.md) — the single clearest technical picture of how Mira works **today**: call flow diagram, CallCoordinator/Redis lease contract, Busy Call Recovery, conversation architecture, database boundaries, invariants. Read this first for anything touching concurrency, recovery, or the conversation-state/style/quality trio.
- [documentation/project_state.md](documentation/project_state.md) — living snapshot: what's implemented vs. uncommitted vs. planned, known limitations/risks, next priorities. Its "Status summary" section at the top is the current authoritative status; everything below it is a historical log.

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
│   │   ├── auth/     JWT auth (dependencies, security/utils)
│   │   ├── config.py pydantic-settings; all env vars documented here
│   │   ├── database.py  SQLAlchemy async engine + session factory
│   │   ├── integrations/ Exotel client, iCal fetcher
│   │   ├── models/   SQLAlchemy ORM models
│   │   ├── prompts/  LLM system-prompt builders (guest-support + lead-agent)
│   │   ├── schemas/  Pydantic request/response + tool-arg schemas
│   │   ├── services/ business logic (calendar, pricing_engine, lead, faq, …)
│   │   ├── utils/    shared helpers
│   │   └── voice/    pipecat pipeline + tool wrappers (+ assets/)
│   ├── alembic/      DB migrations
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
cp .env.example .env   # fill in secrets
python -m uvicorn app.main:app --reload

# Frontend
cd frontend
npm install
npm run dev   # talks to NEXT_PUBLIC_API_BASE_URL, baked in at build time

# Tests (real Postgres required — do not mock the DB)
cd backend
pytest
```

See [docs/architecture.md](docs/architecture.md) for deployment (Render) details.

## Key env vars (`backend/app/config.py`)

| Var | Notes |
|---|---|
| `DATABASE_URL` | Auto-rewritten to `postgresql+asyncpg://` scheme on startup |
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
| `TURN_DETECTION_STRATEGY` | `vad_fixed` (default) / `hybrid_experimental`. Local-only experiment — see [docs/agents.md](docs/agents.md). Not in `render.yaml`. |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM_EMAIL` | Escalation email summaries. Any SMTP provider works. Unset = escalations still create the in-app notification, just skip the email. |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_WHATSAPP_FROM` | Real WhatsApp send for `send_whatsapp`/`send_photos` via Twilio's Sandbox — see [docs/agents.md](docs/agents.md). Sandbox only reaches numbers that texted "join `<code>`" to the sandbox number first; unset = falls back to the in-app notification only. |
| `TWILIO_ESCALATION_TEMPLATE_SID` | ContentSid of the `mira_escalation` WhatsApp template (`scripts/create_escalation_template.py`) — gives escalations a real "Go to Dashboard" button. Unset = falls back to a plain-text message with a bare URL. Also reused for RecoveryService's host-facing "missed call" WhatsApp (`app/services/recovery_service.py`) — same shape, not a separate template. |
| `TWILIO_BUSY_RECOVERY_TEMPLATE_SID` | ContentSid of the `mira_busy_recovery` WhatsApp template (`scripts/create_busy_recovery_template.py`) — the numbered Property/Pricing/FAQs/Photos/Talk-to-host menu RecoveryService sends a guest whose call was rejected as busy. Unset = falls back to an equivalent plain-text message. |
| `TWILIO_WHATSAPP_WEBHOOK_TOKEN` | Shared-secret path token for the inbound WhatsApp webhook (`POST /api/v1/webhooks/whatsapp/inbound`, `app/services/whatsapp_reply_service.py`) that routes a guest's reply to the busy-recovery menu — same convention as `EXOTEL_WEBHOOK_TOKEN`. Configure as this account's WhatsApp Sandbox "WHEN A MESSAGE COMES IN" webhook URL in the Twilio console. |
| `TWILIO_VOICE_WEBHOOK_TOKEN` | Shared-secret path token for Twilio Voice (`app/integrations/twilio_voice.py`, `run_voice_pipeline_twilio` in `app/voice/pipeline.py`) — an entirely separate integration from Exotel telephony and from the WhatsApp Sandbox above, added purely so real-call testing can continue on Twilio's free trial when Exotel credits run out. Reuses `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`. |
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
- **ICE timeout on Render** — STUN-only works on localhost but not on cloud hosts where UDP is blocked. A TURN server is required for browser voice tests in production.
- **Browser test works on WiFi but fails on mobile data** — mobile carriers block/throttle UDP TURN. Set `TURN_URL_TLS` to a TURNS-over-TCP:443 relay (looks like HTTPS, gets through).
- **Voice call gets slow/again after idle, or 429 errors** — a per-model Groq rate limit on `gpt-oss-120b` (account has been on a paid Groq plan since 2026-07-07, not free tier — limits are much higher but not unlimited, especially under call bursts). The 60s health check + `_FallbackGroqLLMService` route around it automatically; check `GET /api/v1/health/llm` to see which models are marked down.
- **`postgres://` vs `postgresql+asyncpg://`** — handled automatically by the `_use_asyncpg_driver` validator in `config.py`.
- **`GROQ_MODEL` deprecation** — Groq renames/deprecates model ids periodically (`llama-3.3-70b-versatile` was removed 2026-06-17). Re-check `GROQ_MODELS` against `client.models.list()` before editing, and confirm any new model supports function calling (required for tools).
- **`Module not found: react-day-picker` (frontend)** — local `node_modules` is stale after a merge that added the calendar component. Run `npm install` in `frontend/`.
- **Greeting garbled / not spoken** — don't ask the LLM to generate the opening line and don't push `TTSSpeakFrame` into `llm`/`tts` directly. Use `worker.queue_frame(TTSSpeakFrame(first_message))` on connect (see [docs/agents.md](docs/agents.md)).
- **"I changed `.env`/the code but nothing's different" (the single most common time-sink this session)** — a running `uvicorn` process only picks up `.py` file changes if started with `--reload`, and even then, **`.env` changes are never hot-reloaded** — `pydantic-settings`' `Settings()` is read once via `@lru_cache` at process start. Always fully kill and restart (`pkill -f "uvicorn app.main:app"`, then re-run) after any `.env` edit, and confirm with `curl localhost:8000/health` or an OpenAPI check (`m.app.openapi()["paths"]`) that new routes actually exist before assuming a fix didn't work. A route that "should" exist but returns `405 Method Not Allowed` (not `404`) is a strong signal you're hitting a stale process — Starlette matched the path against a different, older route's pattern (e.g. `/{property_id}`) instead.
- **A `<button>`/element with both `addEventListener(...)` and a later `.onclick = ...` fires both on click** — these are separate handler slots; assigning `.onclick` does not remove or replace an `addEventListener` listener. Bit the voice test page's connect/end-call button this way (ending a call also silently re-triggered the original connect handler, looking like an auto-restarted call). Fix: register both states through the same slot (either both `.onclick =` or explicit `removeEventListener`), never mix.
- **Voice-agent bugs from a live call log are usually more informative than they look** — `logger.debug` lines like `(strategy: HybridCompletenessUserTurnStopStrategy#0)` or `(strategy: None)` name the exact class/mechanism that fired. `strategy: None` specifically means pipecat's own generic stuck-turn watchdog closed the turn, not any registered strategy — a real signal the strategy's own logic never completed, not just "it was a bit slow." Same discipline applies to which *provider* actually handled a completion — don't infer it from config (e.g. "OPENROUTER_MODEL happens to be the same leaky model, so it must be that path"); grep the Railway log window for the exact call for the actual request URL (`api.groq.com` vs `openrouter.ai`) and the service class name (`_FallbackGroqLLMService` only exists on the Groq path). Confirmed live 2026-07-27: a plausible-sounding OpenRouter theory for a degenerate-completion bug was flat wrong once the actual logs for that exact call were pulled — the request never left Groq.
- **Banning a specific bad LLM phrasing via regex is whack-a-mole, not a fix** — a guard that only rewrites text matching a known-bad pattern (e.g. `loop...host`) will keep missing new phrasing variants of the same underlying failure ("let me open the host" never matched a `loop...host` regex) indefinitely; each "fix" only ever covers wording already observed live. When the safe replacement text is fixed and known regardless of context (e.g. escalation acknowledgements), the durable fix is to stop detecting altogether and unconditionally replace the reply after the triggering event — no detection step means no coverage gap. See `app/voice/escalation_phrase_guard.py`.
- **Joining a list of items with a delimiter that can also appear inside an individual item silently corrupts round-trip parsing** — `handle_recommend_properties` joined multiple properties with `" | "`, but real (Airbnb-imported) property names routinely contain a literal `|` themselves, so splitting the combined string back apart tore names in half at every internal `|`, not just between properties (confirmed live: a shared brand-name suffix got read back as if it were the property name). Before picking a join delimiter for machine-parsed output, confirm it can't appear in the data being joined — a newline is almost always safer than a punctuation character for free-text fields.
- **A substring match against a free-text field can true-positive on the wrong reason** — `recommend_properties`' Goa region filter matched the literal phrase "South Goa" against `neighborhood_info`, which incidentally mentions "Dabolim (South Goa airport)" as a travel-time reference on nearly every North Goa property's listing — a real North Goa property matched a South Goa query for a reason that has nothing to do with its actual location. When matching a category/region query against a free-text description field, prefer matching against a controlled list of expected values (a locality list, an enum) over a raw substring match against prose that can mention the term for unrelated reasons.
- **A fully implemented, tested function with no production caller is a silent functional gap, not just dead code** — `call_coordinator.renew()` (Busy Call Recovery's lease-keepalive) had a real regression test and a docstring describing a periodic renewal loop, but nothing in `app/voice/pipeline.py` ever actually called it. `DEFAULT_LEASE_TTL` is 45s, so every real call longer than 45s was silently losing its busy-recovery lease mid-call — a second incoming call after that point would wrongly get `START_PIPELINE` instead of `BUSY_RECOVERY`, defeating the feature for most real calls, with no error, no log line, and passing tests throughout (the tests exercised `renew()` directly, never the absence of a caller). Found only by a cross-file "who actually calls this" audit, not by running the test suite. Fixed by wiring a periodic `_renew_call_lease_periodically` background task into `_run_pipeline`'s existing lease-lifecycle `finally` block (`app/voice/pipeline.py`) — see [docs/agents.md](docs/agents.md)'s "Busy Call Recovery" section. When a function's own docstring describes behavior ("renewed roughly every N seconds by a caller") that no other file's code actually performs, treat that as a bug report, not documentation.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
