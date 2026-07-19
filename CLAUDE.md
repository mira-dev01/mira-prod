# MIRA — Codebase Guide

MIRA is an AI-powered property-management assistant for short-term rental hosts in India. It handles guest calls (via Exotel telephony), lead qualification across a host's portfolio, booking/calendar management, rule-based pricing & negotiation, and a real-time "talk to Mira" voice-test UI in the dashboard.

Detailed docs live in `docs/`:
- [docs/architecture.md](docs/architecture.md) — system architecture, backend/frontend structure, deployment topology, auth, end-to-end call data flow.
- [docs/agents.md](docs/agents.md) — the voice agent/pipeline design: agent modes, pipecat pipeline stages, tools, prompt rules, Groq fallback, turn detection.
- [docs/database.md](docs/database.md) — full schema reference and Alembic migration history.
- [docs/research-flow.md](docs/research-flow.md) — pricing/negotiation logic, host discount policy, lead qualification, Airbnb import.
- [docs/api.md](docs/api.md) — REST endpoint reference, grouped by domain.

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
| `TWILIO_ESCALATION_TEMPLATE_SID` | ContentSid of the `mira_escalation` WhatsApp template (`scripts/create_escalation_template.py`) — gives escalations a real "Go to Dashboard" button. Unset = falls back to a plain-text message with a bare URL. |

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
- **Voice call gets slow/again after idle, or 429 errors** — Groq free-tier TPM cap on `gpt-oss-120b`. The 60s health check + `_FallbackGroqLLMService` route around it automatically; check `GET /api/v1/health/llm` to see which models are marked down.
- **`postgres://` vs `postgresql+asyncpg://`** — handled automatically by the `_use_asyncpg_driver` validator in `config.py`.
- **`GROQ_MODEL` deprecation** — Groq renames/deprecates model ids periodically (`llama-3.3-70b-versatile` was removed 2026-06-17). Re-check `GROQ_MODELS` against `client.models.list()` before editing, and confirm any new model supports function calling (required for tools).
- **`Module not found: react-day-picker` (frontend)** — local `node_modules` is stale after a merge that added the calendar component. Run `npm install` in `frontend/`.
- **Greeting garbled / not spoken** — don't ask the LLM to generate the opening line and don't push `TTSSpeakFrame` into `llm`/`tts` directly. Use `worker.queue_frame(TTSSpeakFrame(first_message))` on connect (see [docs/agents.md](docs/agents.md)).
- **"I changed `.env`/the code but nothing's different" (the single most common time-sink this session)** — a running `uvicorn` process only picks up `.py` file changes if started with `--reload`, and even then, **`.env` changes are never hot-reloaded** — `pydantic-settings`' `Settings()` is read once via `@lru_cache` at process start. Always fully kill and restart (`pkill -f "uvicorn app.main:app"`, then re-run) after any `.env` edit, and confirm with `curl localhost:8000/health` or an OpenAPI check (`m.app.openapi()["paths"]`) that new routes actually exist before assuming a fix didn't work. A route that "should" exist but returns `405 Method Not Allowed` (not `404`) is a strong signal you're hitting a stale process — Starlette matched the path against a different, older route's pattern (e.g. `/{property_id}`) instead.
- **A `<button>`/element with both `addEventListener(...)` and a later `.onclick = ...` fires both on click** — these are separate handler slots; assigning `.onclick` does not remove or replace an `addEventListener` listener. Bit the voice test page's connect/end-call button this way (ending a call also silently re-triggered the original connect handler, looking like an auto-restarted call). Fix: register both states through the same slot (either both `.onclick =` or explicit `removeEventListener`), never mix.
- **Voice-agent bugs from a live call log are usually more informative than they look** — `logger.debug` lines like `(strategy: HybridCompletenessUserTurnStopStrategy#0)` or `(strategy: None)` name the exact class/mechanism that fired. `strategy: None` specifically means pipecat's own generic stuck-turn watchdog closed the turn, not any registered strategy — a real signal the strategy's own logic never completed, not just "it was a bit slow."
