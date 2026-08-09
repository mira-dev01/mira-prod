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

- **Entry point**: `backend/app/main.py`. FastAPI app with a `lifespan` context manager that starts an `AsyncIOScheduler` (APScheduler) for five background jobs:
  - `_scheduled_ical_sync`, every `ical_sync_interval_minutes` (default 15) — calls `sync_all_properties`.
  - `_check_llm_health`, every 60s (plus once at startup) — pings each Groq model in `settings.groq_models` and stores per-model health in the module-level `llm_health` dict, exposed read-only at `GET /api/v1/health/llm`. See [agents.md](agents.md) for how `app/voice/pipeline.py` consumes this.
  - `_check_db_health`, every 3 minutes (plus once at startup) — a trivial `SELECT 1` against the DB. Exists because Neon (serverless Postgres) suspends its compute after a few minutes idle, and the first real query after that wakes it back up at a real cost (confirmed live: this alone accounted for several seconds of the pre-greeting delay on a call after any idle gap). Keeps the connection warm well inside Neon's autosuspend window so a real call never pays that cost. Same rationale/pattern as `_check_llm_health`, applied to the DB instead of the LLM route.
  - `_scheduled_smart_pricing_refresh`, daily cron (`hour=1, minute=0` UTC ≈ 6:30am IST) — see [research-flow.md](research-flow.md)'s Smart pricing section.
  - `_scheduled_live_pricing_cache_refresh`, daily cron, staggered 15 minutes after the job above (`hour=1, minute=15`) — pre-warms the 7-day nightly-rate Redis cache; see [research-flow.md](research-flow.md)'s pricing cache section.
- **Routers**: every domain router in `app/api/v1/` is mounted under prefix `/api/v1` (see `API_PREFIX` in `main.py`): `auth`, `properties`, `bookings`, `calls`, `guests`, `technicians`, `pricing`, `analytics`, `notifications`, `leads`, `faq`, `host_discount_rules`, `voice`, plus `app/api/v1/webhooks/exotel`.
- **CORS**: `CORSMiddleware` with `allow_origins=settings.cors_allowed_origins` — always includes `FRONTEND_BASE_URL`, plus any comma-separated `CORS_EXTRA_ORIGINS`.
- **Health**: `GET /health` (plain liveness, used as the `healthCheckPath` on both Railway and Render) and `GET /api/v1/health/llm` (per-model LLM health snapshot).
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

## Deployment — current topology (as of 2026-07-21)

**Railway (backend, primary/active) + Vercel (frontend, primary/active) + Render (backend + frontend, kept running as a fallback, not actively deployed to).** This inverted from the original Render-first setup partway through the project — Railway/Vercel became the actual target hosts host testing and real Exotel calls go through, while Render was deliberately left running rather than torn down (`"do not remove render for now"`). `CORS_EXTRA_ORIGINS`/`FRONTEND_BASE_URL` on the Railway backend are what actually route photo/escalation links and CORS to the right frontend — see the per-host notes below for exactly what's set where.

### Railway (backend)

Project `mira-backend`, service linked via `backend/railway.json` (builder `DOCKERFILE`, `dockerfilePath: Dockerfile`, `healthcheckPath: /health`, restart-on-failure). Omits `deploy.startCommand` so it inherits `backend/Dockerfile`'s `CMD` (`alembic upgrade head && uvicorn ... --port ${PORT}`) rather than duplicating it and risking drift. Auto-deploys on every push to `main` via GitHub integration (Root Directory = `backend`, required since this is a monorepo — the Dockerfile's `COPY . .` needs `backend/` as its build context, not the repo root).

Public URL: `https://mira-backend-production-45e3.up.railway.app` (Railway-generated domain). **This domain has shown DNS resolution failures from some networks/resolvers** (confirmed live: failed from this development environment's default resolver and from the project owner's own home network, while resolving fine via Google's public DNS `8.8.8.8`/`1.1.1.1` and from Vercel's own edge network) — not a Railway platform outage (status page showed fully operational), and not something fixable in application code. If login/API calls mysteriously fail with no request ever reaching the backend logs, suspect this first — test by switching the affected device's DNS to `8.8.8.8`/`1.1.1.1`, or `dig <domain> @8.8.8.8` to compare against the default resolver.

CLI usage: `railway login` (interactive OAuth, run manually, not from an agent session), then `railway variables`/`railway logs`/`railway deployment list`/`railway up` once linked (`railway link` from `backend/`). Env vars — same set as `config.py` documents (`DATABASE_URL`, `FRONTEND_BASE_URL`, `JWT_SECRET_KEY`, `LLM_PROVIDER`, `GROQ_*`, `SARVAM_API_KEY`, `EXOTEL_*`, `TWILIO_*`, `SMTP_*`, `SEARCHAPI_API_KEY`, `REDIS_URL`, etc.) — set via `railway variables --set KEY=VALUE` or the dashboard, none checked into `railway.json`. `PORT` is injected automatically.

**Known Railway-specific constraints (Trial-tier network policy, confirmed live):**
- **Outbound SMTP (port 587) is blocked.** Every `aiosmtplib.send(...)` call (escalation emails, photo-request emails) times out (`SMTPConnectTimeoutError: Timed out connecting to smtp.gmail.com on port 587`) — confirmed via Railway logs on a real call. This is not a bug in `email_client.py`; it's the platform blocking the port outright. Real fix is switching to an HTTP-based email API (Resend, SendGrid, Postmark, etc.) instead of raw SMTP — not yet built. Upgrading off the Trial tier may lift this (unconfirmed, worth testing before building the HTTP-API fix).
- `REDIS_URL` is **not currently set** on Railway — see the Redis note under [research-flow.md](research-flow.md)'s pricing cache section; the caching code path is fully wired but inert in production until this is provisioned.

### Vercel (frontend)

Project deployed from `frontend/`, public URL `https://mira-prod-two.vercel.app`. Auto-deploys on push to `main`. `NEXT_PUBLIC_API_BASE_URL` is a Vercel dashboard env var — **must include the scheme and end in `/api/v1`** (e.g. `https://mira-backend-production-45e3.up.railway.app/api/v1`); a schemeless value silently resolves every API call as a relative path against Vercel's own origin instead of the backend (confirmed live, recurred more than once this project). Being a `NEXT_PUBLIC_*` var, changing it requires a full rebuild/redeploy, not just a dashboard save + reload.

### Render (backend + frontend, kept as fallback)

Defined in `render.yaml` at the repo root, two services (`mira-backend`, Docker runtime; `mira-frontend`, Node runtime) — see the file itself for exact config. `DATABASE_URL` points at the same Neon Postgres as Railway (shared DB across all three hosts, not per-host). Not actively deployed to as part of normal work — kept running so it isn't a hard dependency to restore if Railway/Vercel need to be abandoned. If reactivating: `BACKEND_BASE_URL`/`FRONTEND_BASE_URL`/`NEXT_PUBLIC_API_BASE_URL` need re-pointing (they were moved to Railway/Vercel's URLs), and its own env var set (most are `sync: false`, set manually in the dashboard) is likely stale relative to what Railway currently has.

### After first deploy (any host)

1. Point `BACKEND_BASE_URL`/`NEXT_PUBLIC_API_BASE_URL` at the backend's actual public URL (latter must include scheme and end in `/api/v1`).
2. Point `FRONTEND_BASE_URL` on the backend at the frontend's actual public URL (also include scheme — see the Vercel note above for what happens if you don't).
3. Run `alembic upgrade head` (or let the startup hook do it).
4. Set `TURN_URL`/`TURN_URL_TLS`/`TURN_USERNAME`/`TURN_CREDENTIAL` if browser voice tests need to work in production.

## End-to-end data flow: a guest phone call

1. **Telephony ingress**: Exotel's Voicebot Applet is configured with a static websocket URL, `wss://<backend_base_url>/api/v1/voice/exotel/ws/<EXOTEL_WEBHOOK_TOKEN>` (`app/api/v1/voice.py`, route `exotel_voice_ws`) — token is a path segment, not a `?token=` query param, since Exotel's Voicebot Applet strips query strings from the configured WSS URL before connecting (confirmed live: every real Exotel connection arrived at the bare path with no query string, while the same URL worked fine tested directly). Exotel connects directly — no separate HTTP handshake round-trip.
2. **Call routing**: `run_voice_pipeline` (`app/voice/pipeline.py`) looks up the dialed number against `Property.exophone` (Guest Support, one property) or `User.lead_exophone` (Lead Agent, portfolio-wide) via `call_service.get_property_by_number`/`get_user_by_lead_number`. No match on either → the websocket is closed.
3. **Concurrency check**: `CallCoordinator.acquire_or_reject` (`app/services/call_coordinator.py`, Redis-backed) decides whether this host/property already has a live call. A rejection routes to Busy Call Recovery (WhatsApp re-engagement, no pipeline built) instead of step 4 below — see [documentation/current_architecture.md](../documentation/current_architecture.md) for the full diagram and contract; this file only covers the accepted-call path.
5. **Session setup**: a `GuestProfile` is resolved/created from the caller's number (`call_service.get_or_create_guest_profile`), a `CallSession` row is created (`call_service.get_or_create_call_session`), and the per-call system prompt + first message are built (`app/prompts/system_prompt.py` — see [agents.md](agents.md)).
6. **Voice pipeline**: `_run_pipeline` wires Sarvam STT → Groq/Anthropic/OpenRouter LLM (function-calling) → Sarvam TTS through a pipecat `Pipeline`/`PipelineWorker`. Tool calls the LLM makes (`app/voice/tools.py`) call into unchanged business logic in `app/services/tool_handlers.py`.
7. **Tool side effects hit the DB**: e.g. `update_lead`/`escalate_to_host` write `Lead`/`Notification` rows; `escalate_to_host` also fires a detached (`asyncio.create_task`) SMTP email to the host (`app/integrations/email_client.py`).
8. **Call teardown**: on `on_pipeline_finished`, the transcript is assembled from the LLM context and `call_service.finalize_call_session` persists it; the caller's phone/property are backfilled onto the lead (`lead_service.backfill_lead`), or an empty lead is deleted (`lead_service.delete_if_empty`). Guest Memory is updated in a detached task afterward. The `CallCoordinator` lease acquired in step 3 is released in the same teardown path (`finally` block), regardless of how the call ended.
9. **Separately, Exotel's own call-status webhook** (`POST /api/v1/webhooks/exotel/call-status`, token-verified) delivers call lifecycle metadata (recording URL, busy/no-answer/failed) independent of the live voice websocket, via `call_service.attach_exotel_call`.
10. **Dashboard visibility**: the host's dashboard (`frontend/src/app/dashboard/`) polls/streams `Notification` rows (Live Requests feed, `GET /api/v1/notifications` and `GET /api/v1/notifications/stream`), and reads `Lead`/`CallSession` rows via their respective REST endpoints — see [api.md](api.md).

Two other entry points reuse this identical flow rather than a separate implementation: **Twilio Voice** (`run_voice_pipeline_twilio`, `app/integrations/twilio_voice.py`) is a fallback telephony path for real-call testing when Exotel credits run out — same `CallCoordinator`/`_run_pipeline` core, different websocket ingress. For the in-dashboard "talk to Mira" browser test (no real phone call), `app/api/v1/voice.py`'s `POST /voice/test/offer` (WebRTC signaling) and `GET /voice/test` (standalone test page) drive `run_browser_voice_pipeline`/`run_browser_lead_pipeline` instead of `run_voice_pipeline`, reusing the same `_run_pipeline` core (browser-test calls skip `CallCoordinator` — see `documentation/current_architecture.md`).
