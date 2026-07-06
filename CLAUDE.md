# MIRA — Codebase Guide

MIRA is an AI-powered property-management assistant for short-term rental hosts in India. It handles guest calls (via Exotel telephony), lead qualification across a host's portfolio, booking/calendar management, rule-based pricing & negotiation, and a real-time "talk to Mira" voice-test UI in the dashboard.

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

## Backend

### Running locally

```bash
cd backend
cp .env.example .env   # fill in secrets
python -m uvicorn app.main:app --reload
```

### Key env vars (`backend/app/config.py`)

| Var | Notes |
|---|---|
| `DATABASE_URL` | Auto-rewritten to `postgresql+asyncpg://` scheme on startup |
| `LLM_PROVIDER` | `groq` (default) / `anthropic` / `openrouter` |
| `GROQ_MODEL` | Configured default/first choice, `openai/gpt-oss-120b` |
| `GROQ_MODELS` | JSON-array string; the fallback chain `_build_llm()` actually walks (see LLM fallback below) |
| `OPENROUTER_MODEL` | Used when `LLM_PROVIDER=openrouter`; last-resort fallback otherwise |
| `TURN_URL` | Primary TURN relay (UDP). Must start with `turn:`/`turns:` — validated at startup. Leave unset if no TURN server. |
| `TURN_URL_TLS` | Optional TURNS-over-TCP:443 relay for mobile networks that block UDP (see Voice/WebRTC). Same username/credential as `TURN_URL`. |
| `TURN_USERNAME` / `TURN_CREDENTIAL` | Required if any `TURN_URL*` is set |
| `SARVAM_API_KEY` | STT + TTS for voice pipeline |
| `SARVAM_TTS_MODEL` / `SARVAM_TTS_SPEAKER` | `bulbul:v3` / `roopa` |
| `EXOTEL_*` | Telephony integration |
| `CORS_EXTRA_ORIGINS` | Comma-separated extra CORS origins (e.g. hit a deployed backend from a local frontend). `FRONTEND_BASE_URL` is always allowed. |

### LLM provider + Groq multi-model fallback

- `LLM_PROVIDER=groq` (default) walks the `GROQ_MODELS` chain in priority order: `["openai/gpt-oss-120b", "llama-3.1-8b-instant", "openai/gpt-oss-20b"]`.
- A periodic health check (`_check_llm_health` in `app/main.py`, every **60s** + once at startup) pings each model with a 1-token request. This does double duty: keeps the route warm (kills cold-start latency on Render) **and** marks a model down on a 429 (free-tier tokens-per-minute cap) so `_pick_groq_model()` in `pipeline.py` skips it.
- `_FallbackGroqLLMService` also handles a 429 that happens **live, mid-call, between health checks**: it retries the next model in the chain immediately in the same turn. It sets `max_retries=0` on the OpenAI client so the SDK doesn't blindly re-hit the same rate-limited model first.
- `reasoning_effort: "low"` is applied only to `gpt-oss` models (other models 400 on it). It disables gpt-oss's hidden chain-of-thought pass — a real source of multi-second latency on a call.
- Per-model health/latency is exposed read-only at `GET /api/v1/health/llm`.
- If every Groq model is down, it falls through to OpenRouter as a last resort.

### Database

- SQLAlchemy async (asyncpg driver), PostgreSQL
- Migrations: `alembic upgrade head`
- Models in `app/models/`; schemas (Pydantic) in `app/schemas/`

### Pricing & negotiation

- `pricing_engine.calculate_price` — base rate × weekend surge (Fri/Sat/Sun, `WEEKEND_SURGE_MULTIPLIER`), plus cleaning fee (`DEFAULT_CLEANING_FEE_INR`) and tax (`DEFAULT_TAX_PERCENT`). Length-of-stay discounts come from per-property `PricingRule` rows (`rule_type="length_of_stay"`, `min_nights` condition).
- `pricing_engine.negotiate_rate` — computes a floor price from a max discount (capped at `MAX_NEGOTIATION_DISCOUNT_PERCENT`, plus a small loyalty bonus) and accepts/counters the guest's offer. The LLM only reaches this via the `negotiate_rate` tool — it never negotiates freehand.

### Analytics

- `GET /api/v1/analytics/summary` and `GET /api/v1/analytics/timeseries` power the dashboard's stat cards, sparklines, and date-range-filtered charts.

### Tests

```bash
cd backend
pytest
```

Tests require a real PostgreSQL database — **do not mock the DB**. The test DB URL is set in `pytest.ini` / the test env. See `tests/test_pipeline_llm.py` for the Groq fallback coverage.

### Voice / WebRTC

- Real calls: Exotel websocket at `/api/v1/voice/exotel/ws` (raw-PCM media protocol, `ExotelFrameSerializer`).
- Browser test: WebRTC offer/answer at `/api/v1/voice/test/offer`; standalone test page at `/api/v1/voice/test?token=<JWT>`. The dashboard's "talk to Mira" dialog drives this. Omit `property_id` to test the portfolio-wide **Lead Agent**; include it to test **Guest Support** for one property.
- Pipeline (`app/voice/pipeline.py`, one per call): `transport.input → Sarvam STT → user_aggregator → LLM (Groq/Anthropic/OpenRouter, function-calling) → Sarvam TTS → transport.output → assistant_aggregator`. Tool wrappers in `app/voice/tools.py` call unchanged business logic in `app/services/tool_handlers.py`.
- **Bot speaks first:** the greeting is a fixed, host-authored `first_message`, pre-seeded into context as an assistant turn and spoken via `worker.queue_frame(TTSSpeakFrame(first_message))` on `on_client_connected`. `queue_frame` injects at the pipeline source so it flows through to TTS — pushing into `llm`/`tts` directly, or asking the LLM to generate the greeting live, both failed.
- **VAD tuning:** `SileroVADAnalyzer` with `VADParams(confidence=0.85, min_volume=0.7)` (raised from defaults 0.7/0.6) so background noise / a nearby second voice doesn't trigger a false interruption that cuts off TTS. End-of-turn is `SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.9)` — 0.9s is the validated middle ground (0.6s cut people off mid-thought; 1.4s added ~1.4s dead air per turn).
- **TTS pace** is `1.15` (slightly faster than default for phone cadence).
- ICE servers: two Google STUN servers always included; `TURN_URL` (UDP) added if set, and `TURN_URL_TLS` (TURNS-over-TCP:443) added as an extra candidate for mobile carrier networks that block UDP. `_ice_servers()` is the single source of truth — the test page reuses it rather than duplicating the list. Any `TURN_URL*` with a bad scheme is logged and skipped (never a 500).

---

## Frontend

### Running locally

```bash
cd frontend
npm install
npm run dev
```

Talks to `NEXT_PUBLIC_API_BASE_URL` (set in `.env.local`). Baked in at build time — changing it requires a rebuild.

### Stack

- Next.js 16 (App Router), TypeScript, Tailwind CSS, shadcn/ui, `react-day-picker` (calendar)
- API client in `src/lib/api.ts`
- Auth via JWT stored in context (`src/lib/auth-context.tsx`)

---

## Deployment (Render)

- Backend: Docker (`backend/Dockerfile`)
- Frontend: Node build (`cd frontend && npm install && npm run build`, start with `npm run start -- -p $PORT`)
- Config in `render.yaml`; secrets with no default (API keys, TURN creds, `*_BASE_URL`) are `sync: false` and must be set manually in the Render dashboard after first deploy
- DB is a Render-managed Postgres; `DATABASE_URL` is injected automatically

### After first deploy

1. Copy the backend's public URL into `BACKEND_BASE_URL` and `NEXT_PUBLIC_API_BASE_URL` (the latter must end in `/api/v1`)
2. Copy the frontend's public URL into `FRONTEND_BASE_URL`
3. Run `alembic upgrade head` (or let the startup hook do it)
4. Set `TURN_URL` / `TURN_URL_TLS` / `TURN_USERNAME` / `TURN_CREDENTIAL` if browser voice tests need to work in production

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
- **Greeting garbled / not spoken** — don't ask the LLM to generate the opening line and don't push `TTSSpeakFrame` into `llm`/`tts` directly. Use `worker.queue_frame(TTSSpeakFrame(first_message))` on connect (see Voice/WebRTC).
