# MIRA — Codebase Guide

MIRA is an AI-powered property-management assistant for short-term rental hosts. It handles guest calls (via Exotel telephony), lead qualification, booking management, and a real-time voice-test UI in the dashboard.

---

## Repo layout

```
mira-prod/
├── backend/          FastAPI + Python 3.12
│   ├── app/
│   │   ├── api/v1/   REST endpoints (one file per domain)
│   │   ├── auth/     JWT auth (dependencies, utils)
│   │   ├── config.py pydantic-settings; all env vars documented here
│   │   ├── database.py  SQLAlchemy async engine + session factory
│   │   ├── integrations/ Exotel client, iCal fetcher
│   │   ├── models/   SQLAlchemy ORM models
│   │   ├── prompts/  LLM system-prompt builders
│   │   ├── schemas/  Pydantic request/response schemas
│   │   ├── services/ business logic (calendar sync, lead service, …)
│   │   ├── utils/    shared helpers
│   │   └── voice/    pipecat pipeline + tool handlers
│   ├── alembic/      DB migrations
│   ├── tests/        pytest (all async, hits a real test DB — no DB mocking)
│   └── requirements.txt
└── frontend/         Next.js (TypeScript, Tailwind, shadcn/ui)
    └── src/
        ├── app/dashboard/  per-domain pages (properties, bookings, calls, …)
        ├── components/     shared UI components
        ├── hooks/          custom React hooks
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
| `DATABASE_URL` | Auto-rewritten to `asyncpg` driver scheme on startup |
| `LLM_PROVIDER` | `groq` / `anthropic` / `openrouter` |
| `TURN_URL` | Must start with `turn:` or `turns:` — validated at startup. Leave unset if no TURN server. |
| `TURN_USERNAME` / `TURN_CREDENTIAL` | Required only if `TURN_URL` is set |
| `SARVAM_API_KEY` | STT + TTS for voice pipeline |
| `EXOTEL_*` | Telephony integration |

### Database

- SQLAlchemy async (asyncpg driver), PostgreSQL
- Migrations: `alembic upgrade head`
- Models in `app/models/`; schemas (Pydantic) in `app/schemas/`

### Tests

```bash
cd backend
pytest
```

Tests require a real PostgreSQL database — **do not mock the DB**. The test DB URL is set in `pytest.ini` / the test env.

### Voice / WebRTC

- Real calls: Exotel websocket at `/api/v1/voice/exotel/ws`
- Browser test: WebRTC offer/answer at `/api/v1/voice/test/offer`; test page at `/api/v1/voice/test?token=<JWT>`
- Pipeline built with [pipecat-ai](https://github.com/pipecat-ai/pipecat); `SmallWebRTCConnection` wraps aiortc
- ICE servers: two Google STUN servers always included; TURN added if `TURN_URL` is set. `TURN_URL` must use `turn:` or `turns:` scheme — anything else is logged as an error and skipped to prevent a 500.

---

## Frontend

### Running locally

```bash
cd frontend
npm install
npm run dev
```

Talks to `NEXT_PUBLIC_API_BASE_URL` (set in `.env.local`).

### Stack

- Next.js (App Router), TypeScript, Tailwind CSS, shadcn/ui
- API client in `src/lib/api.ts`
- Auth via JWT stored in context (`src/lib/auth-context.tsx`)

---

## Deployment (Render)

- Backend: Docker (`backend/Dockerfile`)
- Frontend: Node build (`npm run build && npm run start`)
- Config in `render.yaml`; secrets that have no default (API keys, TURN creds) must be set manually in the Render dashboard after first deploy
- DB is a Render-managed Postgres; `DATABASE_URL` is injected automatically

### After first deploy

1. Copy the backend's public URL into `BACKEND_BASE_URL` and `NEXT_PUBLIC_API_BASE_URL`
2. Copy the frontend's public URL into `FRONTEND_BASE_URL`
3. Run `alembic upgrade head` (or let the startup hook do it)

---

## Common pitfalls

- **`ValueError: malformed uri: invalid scheme`** in voice endpoint — `TURN_URL` is set to an `https://` or bare hostname. Fix: use `turn:host:port` format, or delete the env var entirely.
- **ICE timeout on Render** — STUN-only works on localhost but not on cloud hosts where UDP is blocked. A TURN server is required for browser voice tests in production.
- **`postgres://` vs `postgresql+asyncpg://`** — handled automatically by the `_use_asyncpg_driver` validator in `config.py`.
- **`GROQ_MODEL` deprecation** — `llama-3.3-70b-versatile` was removed by Groq on 2026-06-17; default is now `openai/gpt-oss-120b`.
