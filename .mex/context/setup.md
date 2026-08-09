---
name: setup
description: Dev environment setup and commands for MIRA. Load when setting up the project for the first time or when environment issues arise.
triggers:
  - "setup"
  - "install"
  - "environment"
  - "getting started"
  - "how do I run"
  - "local development"
  - "env var"
edges:
  - target: context/stack.md
    condition: when specific technology versions or library details are needed
  - target: context/architecture.md
    condition: when understanding how components connect during setup
  - target: context/decisions.md
    condition: when understanding why a specific setup step or constraint exists
last_updated: 2026-08-08
---

# Setup

## Prerequisites

- **Python 3.12** — exact version; asyncpg and pipecat-ai have 3.12-specific behaviors
- **PostgreSQL** — local instance or Neon URL; a real running Postgres is required for `pytest` (no in-memory substitute)
- **Node.js 20+** — for the Next.js frontend
- **Git** — monorepo: `backend/` and `frontend/` are separate roots

## First-time Setup

1. **Backend deps**: `cd backend && pip install -r requirements.txt`
2. **Env file**: `cp .env.example .env` — fill in at minimum: `DATABASE_URL`, `JWT_SECRET_KEY`, `SARVAM_API_KEY`, `GROQ_API_KEY`
3. **Migrations**: `alembic upgrade head` (run from `backend/`; needs `DATABASE_URL` resolvable)
4. **Demo data** (optional): `DATABASE_URL="postgresql://..." python3 seed_demo.py` — creates `demo@mira.ai` / `MiraDemo2024` with 12 Indian properties
5. **Backend dev server**: `uvicorn app.main:app --reload` (from `backend/`)
6. **Frontend deps**: `cd frontend && npm install`
7. **Frontend dev server**: `npm run dev` (from `frontend/`; set `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1` in `frontend/.env.local`)

## Environment Variables

**Required:**
- `DATABASE_URL` — PostgreSQL connection string; auto-rewritten to `postgresql+asyncpg://` by `config.py`; `sslmode` query param stripped and converted to `database_requires_ssl` bool
- `JWT_SECRET_KEY` — JWT signing key (HS256); min 32 chars recommended
- `SARVAM_API_KEY` — STT + TTS for the voice pipeline (required for any voice call)

**Conditionally required:**
- `GROQ_API_KEY` — required when `LLM_PROVIDER=groq` (default)
- `GROQ_MODELS` — JSON array of model IDs in fallback priority order; default `["openai/gpt-oss-120b", "llama-3.1-8b-instant", "openai/gpt-oss-20b"]`; each model must support function calling
- `EXOTEL_*` (`EXOTEL_API_KEY`, `EXOTEL_API_TOKEN`, `EXOTEL_SID`, `EXOTEL_WEBHOOK_TOKEN`) — required for live phone call routing
- `TURN_URL` / `TURN_USERNAME` / `TURN_CREDENTIAL` — required for browser voice tests in production (STUN-only fails on cloud hosts where UDP is blocked); must start with `turn:` or `turns:` — validated at startup
- `TURN_URL_TLS` — optional TURNS-over-TCP:443 relay for mobile networks that block UDP

**Optional (unset = graceful degradation):**
- `REDIS_URL` — pricing cache; unset = caching inert (currently not set on Railway)
- `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_WHATSAPP_FROM` — WhatsApp delivery; unset = in-app notifications only
- `TWILIO_ESCALATION_TEMPLATE_SID` — gives escalation WhatsApp a "Go to Dashboard" button; unset = plain-text message with bare URL
- `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM_EMAIL` — email escalations; unset = email skipped (also currently broken on Railway Trial tier — port 587 blocked)
- `BRIGHT_DATA_API_KEY` — Airbnb listing import; unset = import fails with a clear error
- `SEARCHAPI_API_KEY` — live Airbnb pricing for `exact_airbnb_pricing` properties; unset = computed pricing used
- `CORS_EXTRA_ORIGINS` — comma-separated extra CORS origins beyond `FRONTEND_BASE_URL`
- `FRONTEND_BASE_URL` — always added to CORS allowed origins + used in escalation links
- `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` — last-resort LLM fallback after all Groq models are down

## Common Commands

- **Backend dev**: `uvicorn app.main:app --reload` (from `backend/`)
- **Frontend dev**: `npm run dev` (from `frontend/`)
- **Tests**: `cd backend && pytest` (requires real Postgres; no DB mocking)
- **Lint**: `cd backend && ruff check .`
- **Format**: `cd backend && ruff format .`
- **New migration**: `cd backend && alembic revision --autogenerate -m "description"` — then review the generated file before applying
- **Apply migrations**: `cd backend && alembic upgrade head`
- **Check migration state**: `cd backend && alembic heads` (compare against running DB if schema errors occur)
- **Seed demo data**: `cd backend && python3 seed_demo.py`
- **LLM health check**: `curl localhost:8000/api/v1/health/llm` — shows per-model health/latency from last 60s ping

## Common Issues

**`.env` changes not taking effect** — `pydantic-settings`' `Settings()` is read once via `@lru_cache` at process start. Kill and fully restart uvicorn after any `.env` edit: `pkill -f "uvicorn app.main:app"`. Hot reload (`--reload`) picks up `.py` file changes but never `.env` changes.

**`ValueError: malformed uri: invalid scheme`** — a `TURN_URL*` variable is set to an `https://` URL or bare hostname. Fix: use `turn:host:port` / `turns:host:443?transport=tcp`, or delete the env var entirely.

**`405 Method Not Allowed` instead of `404` for a new route** — stale uvicorn process from before the last restart. Starlette matched the path against an older route's pattern. Kill + restart completely.

**`Module not found: react-day-picker`** — run `npm install` in `frontend/` after a merge that added the calendar component.

**DB migration errors / demo login 500s** — run `alembic heads` against the running DB to check revision. Current head is `6aa03c77c36f`. A DB left on an old revision is the most common cause of missing-column errors.

**Voice call: double greeting** — most likely two separate `/test/offer` POSTs (two full pipeline runs), not the same `on_client_connected` handler firing twice. Check backend stdout for two `POST .../test/offer` lines close together.

**Railway DNS resolution failures** — Railway's generated domain has shown DNS failures from some home networks/resolvers (confirmed live). Test by switching DNS to `8.8.8.8`/`1.1.1.1`, or `dig <domain> @8.8.8.8`.
