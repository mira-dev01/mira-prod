# Architecture

System overview for MIRA: an AI voice property-management assistant for short-term rental hosts in India. Backend is FastAPI + Python 3.12 with async SQLAlchemy/PostgreSQL; frontend is Next.js (App Router) + TypeScript. See [agents.md](agents.md) for the voice pipeline, [database.md](database.md) for schema, [research-flow.md](research-flow.md) for pricing/negotiation logic, and [api.md](api.md) for REST endpoints.

## Repo layout

```
mira-prod/
├── backend/          FastAPI + Python 3.12
│   ├── app/
│   │   ├── api/v1/   REST endpoints (one file per domain) + webhooks/exotel
│   │   ├── auth/     JWT auth (dependencies, security/utils)
│   │   ├── config.py pydantic-settings; all env vars documented here
│   │   ├── database.py  SQLAlchemy async engine + session factory
│   │   ├── integrations/ Exotel client, iCal fetcher, Bright Data, Cloudinary, email
│   │   ├── models/   SQLAlchemy ORM models
│   │   ├── prompts/  LLM system-prompt builders (guest-support + lead-agent)
│   │   ├── schemas/  Pydantic request/response + tool-arg schemas
│   │   ├── services/ business logic (calendar, pricing_engine, lead, faq, …)
│   │   ├── utils/    shared helpers
│   │   └── voice/    pipecat pipeline + tool wrappers (+ assets/)
│   ├── alembic/      DB migrations
│   ├── seed_demo.py  demo login + 12 Indian properties
│   ├── tests/        pytest (all async, hits a real test DB — no DB mocking)
│   └── requirements.txt
└── frontend/         Next.js (TypeScript, Tailwind, shadcn/ui)
    └── src/
        ├── app/dashboard/  per-domain pages (properties, bookings, calls,
        │                   leads, pricing, calendar, faq, guests,
        │                   technicians, settings)
        ├── components/     shared UI + ui/
        ├── hooks/          custom React hooks
        └── lib/            API client, auth context, types
```

## Backend

- **Entry point**: `backend/app/main.py`. FastAPI app with a `lifespan` context manager that starts an `AsyncIOScheduler` (APScheduler) for two background jobs:
  - `_scheduled_ical_sync`, every `ical_sync_interval_minutes` (default 15) — calls `sync_all_properties`.
  - `_check_llm_health`, every 60s (plus once at startup) — pings each Groq model in `settings.groq_models` and stores per-model health in the module-level `llm_health` dict, exposed read-only at `GET /api/v1/health/llm`. See [agents.md](agents.md) for how `app/voice/pipeline.py` consumes this.
- **Routers**: every domain router in `app/api/v1/` is mounted under prefix `/api/v1` (see `API_PREFIX` in `main.py`): `auth`, `properties`, `bookings`, `calls`, `guests`, `technicians`, `pricing`, `analytics`, `notifications`, `leads`, `faq`, `host_discount_rules`, `voice`, plus `app/api/v1/webhooks/exotel`.
- **CORS**: `CORSMiddleware` with `allow_origins=settings.cors_allowed_origins` — always includes `FRONTEND_BASE_URL`, plus any comma-separated `CORS_EXTRA_ORIGINS`.
- **Health**: `GET /health` (plain liveness, used as Render's `healthCheckPath`) and `GET /api/v1/health/llm` (per-model LLM health snapshot).
- **Database**: `app/database.py` creates a single async engine via `create_async_engine(settings.database_url, pool_pre_ping=True, future=True, connect_args={"ssl": True} if settings.database_requires_ssl else {})`, and `AsyncSessionLocal` (an `async_sessionmaker`, `expire_on_commit=False`). `get_db()` is the FastAPI dependency that yields a session per request. ORM base class is `Base(DeclarativeBase)`.
- **Config**: `app/config.py`, a single `pydantic-settings` `Settings` class read once via `@lru_cache` (`get_settings()` → module-level `settings`). Key behaviors:
  - `_normalize_database_url` (a `model_validator(mode="before")`) rewrites a bare `postgres://`/`postgresql://` `DATABASE_URL` to `postgresql+asyncpg://` (asyncpg driver required for the async engine), and strips/normalizes any `sslmode`/`ssl` query param into the separate `database_requires_ssl` bool (asyncpg's `connect()` rejects that param passed through the URL itself).
  - `turn_url`/`turn_url_tls` are validated to start with `turn:`/`turns:` (`_validate_turn_url`) — a bad scheme raises at startup rather than failing silently mid-call.
  - `is_production` property is `environment.lower() == "production"`.
- **Migrations**: Alembic, `backend/alembic/versions/`. Run `alembic upgrade head`. See [database.md](database.md) for the current head revision and history.
- **Auth**: JWT (HS256 by default, `JWT_SECRET_KEY`/`JWT_ALGORITHM`/`JWT_EXPIRE_MINUTES`). `app/auth/dependencies.get_current_user` is the FastAPI dependency every protected route uses — decodes the bearer token (`app/auth/security.decode_access_token`), loads the `User` row, and 401s if the token is invalid/expired or the user isn't `status == "active"`. Login/register live in `app/api/v1/auth.py`.

## Frontend

- Next.js 16 App Router, TypeScript, Tailwind CSS, shadcn/ui, `react-day-picker` for calendar UI.
- **API client**: `frontend/src/lib/api.ts`. Single `API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1"` constant — baked in at build time (Next.js `NEXT_PUBLIC_*` convention), so changing it in production requires a rebuild/redeploy. The `api` object groups typed request functions by domain (`api.auth`, `api.properties`, `api.calls`, `api.guests`, `api.bookings`, `api.pricing`, `api.hostDiscountRules`, `api.technicians`, `api.notifications`, `api.analytics`, `api.leads`, `api.faq`, `api.faqGaps`), each calling a shared `request<T>()` helper that attaches `Authorization: Bearer <token>` from `localStorage` (`mira_token` key, via `getToken()`/`setToken()`/`clearToken()`) and throws `ApiError` on non-2xx. `uploadFiles`/`uploadAudio` are separate multipart helpers (no `Content-Type` set manually, so the browser fills in the correct boundary).
- **Auth context**: `frontend/src/lib/auth-context.tsx`. `AuthProvider` wraps the app, exposes `useAuth()` with `user`, `loading`, `login`, `register`, `registerHost`, `logout`, `refreshUser`. On mount, if a token exists it calls `api.auth.me()` to hydrate `user`; a failed call clears the token. `registerHost` additionally stashes a `mira_pending_import` sessionStorage key (`PENDING_IMPORT_KEY`) with the Bright Data `snapshot_id` (or an `import_error`) so the dashboard can resume polling the Airbnb import after the post-signup redirect — registration itself never blocks on that scrape.

## Deployment (Render)

Defined in `render.yaml` at the repo root, two services:

1. **`mira-backend`** — Docker runtime (`backend/Dockerfile`, build context `backend/`), plan `free`, `healthCheckPath: /health`. Env vars are listed explicitly; most secrets (`DATABASE_URL`, `FRONTEND_BASE_URL`, `BACKEND_BASE_URL`, `GROQ_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `SARVAM_API_KEY`, `EXOTEL_*`) are `sync: false` — set manually in the Render dashboard after first deploy, since Render has no Blueprint property for a service's public HTTPS URL. `DATABASE_URL` in this deployment points at Neon (external managed Postgres), not Render's own Postgres, because Render's free Postgres expires after 90 days.
2. **`mira-frontend`** — Node runtime, `buildCommand: cd frontend && npm install && npm run build`, `startCommand: cd frontend && npm run start -- -p $PORT`. Only env var is `NEXT_PUBLIC_API_BASE_URL` (`sync: false`), which must end in `/api/v1` and requires a rebuild to change (Render redeploys automatically on save).

### After first deploy

1. Copy the backend's public URL into `BACKEND_BASE_URL` and `NEXT_PUBLIC_API_BASE_URL` (the latter must end in `/api/v1`).
2. Copy the frontend's public URL into `FRONTEND_BASE_URL`.
3. Run `alembic upgrade head` (or let the startup hook do it).
4. Set `TURN_URL`/`TURN_URL_TLS`/`TURN_USERNAME`/`TURN_CREDENTIAL` if browser voice tests need to work in production.

## Deployment (Railway — backend only, optional)

Render remains the primary/canonical deploy target (`render.yaml` above). Railway is set up as an alternate host for **`mira-backend` only** — same Docker image, no frontend service — via `backend/railway.json` (builder `DOCKERFILE`, `dockerfilePath: Dockerfile`, `healthcheckPath: /health`, restart-on-failure). It intentionally omits `deploy.startCommand` so it inherits `backend/Dockerfile`'s `CMD` (`alembic upgrade head && uvicorn ... --port ${PORT}`) rather than duplicating it and risking drift.

Because this is a monorepo, the Railway service's **Root Directory must be set to `backend`** (dashboard: Service Settings → Root Directory) so it picks up `backend/railway.json` and builds with `backend/` as the Docker context — otherwise the Dockerfile's `COPY . .` would copy the wrong tree. The CLI does this automatically since `railway up`/`railway link` use the current working directory.

Setup (one-time, requires an interactive login — run these yourself, not from an agent session):
```bash
npm install -g @railway/cli   # or: brew install railway
railway login                 # opens a browser OAuth flow
cd backend
railway init                  # or `railway link` to attach to an existing project
railway up                    # first deploy
```

Env vars — same set as the Render `mira-backend` service above (`DATABASE_URL`, `FRONTEND_BASE_URL`, `BACKEND_BASE_URL`, `JWT_SECRET_KEY`, `LLM_PROVIDER`, `GROQ_API_KEY`, `GROQ_MODEL`, `GROQ_MODELS`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `SARVAM_API_KEY`, `EXOTEL_*`, etc. — see [config.py](../backend/app/config.py)) must be set manually via `railway variables --set KEY=VALUE` or the dashboard; none are checked into `railway.json`. `PORT` is injected automatically by Railway, matching the Dockerfile's `${PORT:-8000}`. After first deploy, set `BACKEND_BASE_URL` to the generated `*.up.railway.app` domain (or a custom domain) and update `NEXT_PUBLIC_API_BASE_URL` on whichever frontend deploy points at it.

## End-to-end data flow: a guest phone call

1. **Telephony ingress**: Exotel's Voicebot Applet is configured with a static websocket URL, `wss://<backend_base_url>/api/v1/voice/exotel/ws/<EXOTEL_WEBHOOK_TOKEN>` (`app/api/v1/voice.py`, route `exotel_voice_ws`) — token is a path segment, not a `?token=` query param, since Exotel's Voicebot Applet strips query strings from the configured WSS URL before connecting (confirmed live: every real Exotel connection arrived at the bare path with no query string, while the same URL worked fine tested directly). Exotel connects directly — no separate HTTP handshake round-trip.
2. **Call routing**: `run_voice_pipeline` (`app/voice/pipeline.py`) looks up the dialed number against `Property.exophone` (Guest Support, one property) or `User.lead_exophone` (Lead Agent, portfolio-wide) via `call_service.get_property_by_number`/`get_user_by_lead_number`. No match on either → the websocket is closed.
3. **Session setup**: a `GuestProfile` is resolved/created from the caller's number (`call_service.get_or_create_guest_profile`), a `CallSession` row is created (`call_service.get_or_create_call_session`), and the per-call system prompt + first message are built (`app/prompts/system_prompt.py` — see [agents.md](agents.md)).
4. **Voice pipeline**: `_run_pipeline` wires Sarvam STT → Groq/Anthropic/OpenRouter LLM (function-calling) → Sarvam TTS through a pipecat `Pipeline`/`PipelineWorker`. Tool calls the LLM makes (`app/voice/tools.py`) call into unchanged business logic in `app/services/tool_handlers.py`.
5. **Tool side effects hit the DB**: e.g. `update_lead`/`escalate_to_host` write `Lead`/`Notification` rows; `escalate_to_host` also fires a detached (`asyncio.create_task`) SMTP email to the host (`app/integrations/email_client.py`).
6. **Call teardown**: on `on_pipeline_finished`, the transcript is assembled from the LLM context and `call_service.finalize_call_session` persists it; the caller's phone/property are backfilled onto the lead (`lead_service.backfill_lead`), or an empty lead is deleted (`lead_service.delete_if_empty`). Guest Memory is updated in a detached task afterward.
7. **Separately, Exotel's own call-status webhook** (`POST /api/v1/webhooks/exotel/call-status`, token-verified) delivers call lifecycle metadata (recording URL, busy/no-answer/failed) independent of the live voice websocket, via `call_service.attach_exotel_call`.
8. **Dashboard visibility**: the host's dashboard (`frontend/src/app/dashboard/`) polls/streams `Notification` rows (Live Requests feed, `GET /api/v1/notifications` and `GET /api/v1/notifications/stream`), and reads `Lead`/`CallSession` rows via their respective REST endpoints — see [api.md](api.md).

For the in-dashboard "talk to Mira" browser test (no real phone call), `app/api/v1/voice.py`'s `POST /voice/test/offer` (WebRTC signaling) and `GET /voice/test` (standalone test page) drive `run_browser_voice_pipeline`/`run_browser_lead_pipeline` instead of `run_voice_pipeline`, reusing the same `_run_pipeline` core.
