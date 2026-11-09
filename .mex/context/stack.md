---
name: stack
description: Technology stack, library choices, and reasoning. Load when working with specific technologies or making decisions about libraries and tools.
triggers:
  - "library"
  - "package"
  - "dependency"
  - "which tool"
  - "technology"
  - "version"
edges:
  - target: context/decisions.md
    condition: when the reasoning behind a tech choice is needed
  - target: context/conventions.md
    condition: when understanding how to use a technology in this codebase
  - target: context/architecture.md
    condition: when understanding how technologies connect in the system
last_updated: 2026-11-09
---

# Stack

## Core Technologies

- **Python 3.12** — backend language
- **FastAPI ≥0.115** — async web framework; all route handlers use `async def`; `APIRouter` per domain under `app/api/v1/`
- **SQLAlchemy ≥2.0 (asyncio)** + **asyncpg ≥0.30** — ORM with async engine; `AsyncSession` everywhere; `postgresql+asyncpg://` URL required (rewritten automatically by `config.py`)
- **PostgreSQL** — Neon (serverless) in Railway's `production` environment, Supabase Postgres (session-mode pooler) in `dev`; connection pool sized via `DB_POOL_*`
- **Alembic ≥1.14** — DB migrations; `alembic upgrade head` runs at deploy startup via Dockerfile `CMD`
- **Next.js (App Router) + TypeScript** — frontend; pages under `frontend/src/app/dashboard/`
- **Tailwind CSS + shadcn/ui** — frontend styling and component library
- **pydantic ≥2.9 + pydantic-settings ≥2.6** — data validation everywhere; all config via single `Settings` class

## Key Libraries

- **pipecat-ai ==1.6.0** (exact pin; the whole dependency tree is locked in `backend/requirements.lock`) (with extras `websocket,webrtc,anthropic,groq,sarvam`) — real-time voice pipeline; explicit floor+cap because Railway silently rebuilt onto a newer version mid-project with no record of which version any deploy was running. Bump the floor deliberately when upgrading.
- **APScheduler ≥3.10 (`AsyncIOScheduler`)** — 5 in-process scheduled jobs run inside FastAPI's `lifespan` context manager; not a separate worker process.
- **pyjwt ≥2.9 + bcrypt ≥4.2** — JWT (HS256) auth + password hashing; `app/auth/` only.
- **httpx ≥0.27** — async HTTP client for integration calls (Bright Data, SearchApi, etc.).
- **httpx ≥0.27 → Resend** — host email goes through Resend's HTTPS API (`app/integrations/email_client.py`); SMTP (aiosmtplib) was removed because Railway blocks outbound SMTP ports at the network level.
- **icalendar ≥6.0** — iCal parsing for external booking calendar sync.
- **redis ≥5.2** — pricing cache client; `REDIS_URL` not provisioned on Railway (cache inert in production).
- **opencv-python ≥4.13** — required by pipecat's `SmallWebRTCTransport` (not declared as its own dependency — missing this causes import crash).
- **ruff ≥0.7** — linter + formatter for backend Python.
- **pytest ≥8.3 + pytest-asyncio ≥0.24** — async test runner; all tests hit a real test DB.
- **cloudinary ≥1.41** — photo hosting; re-hosts Airbnb photos via `app/integrations/cloudinary_client.py`.

## What We Deliberately Do NOT Use

- **No DB mocking in tests** — all tests hit a real PostgreSQL instance. Mock-vs-prod divergence caused a real production incident.
- **No Redux or global state manager** — frontend state is React Context (`AuthProvider`) only; no Zustand, Recoil, or Redux.
- **No class components** — React hooks only in the Next.js frontend.
- **No Redis for sessions** — JWT stored in `localStorage` (`mira_token` key); Redis is pricing cache only.
- **No raw `os.environ` reads** outside `app/config.py` — all env vars go through the `Settings` class.

## Version Constraints

- **pipecat-ai**: pinned `==1.6.0`, and every other package is pinned (with hashes) in `backend/requirements.lock`, which the Dockerfile and CI install from. Bump deliberately: edit `requirements.txt`, regenerate the lock (`uv pip compile … --generate-hashes`), test a real call.
- **Groq model IDs**: change without notice (e.g. `llama-3.3-70b-versatile` removed 2026-06-17). Verify `GROQ_MODELS` against `client.models.list()` before editing. Every model in the list **must support function calling**.
- **`reasoning_effort: "low"` param**: only valid for `gpt-oss` models (other Groq models return 400). Applied conditionally via `"gpt-oss" in model` check in three places: `_check_llm_health`, `_build_llm`, `_build_openrouter_llm`.
