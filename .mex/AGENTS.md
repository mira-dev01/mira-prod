---
name: agents
description: Always-loaded project anchor. Read this first. Contains project identity, non-negotiables, commands, and pointer to ROUTER.md for full context.
last_updated: 2026-11-09
---

# MIRA — AI Property Management Assistant

## What This Is

A FastAPI + Next.js SaaS that handles real-time guest phone calls for short-term rental hosts in India via a pipecat voice pipeline (Exotel telephony → Sarvam STT → Groq LLM → Sarvam TTS), with a host dashboard for leads, bookings, pricing, and escalations.

## Non-Negotiables

- **Never mock the DB in tests** — all tests hit a real PostgreSQL instance; mock-vs-prod divergence caused a real production incident.
- **Business logic lives in `app/services/`**, never in `app/api/v1/` route handlers — route handlers call service functions only.
- **Voice tool handlers return natural-language strings**, never dicts or structured types — the return value is TTS-ready and spoken aloud to the guest.
- **Recurring LLM compliance failures get a guard processor**, not just a prompt rule update — confirmed-live phrasing variants slip through prompt-only fixes; code backstops in `app/voice/` are unconditional.
- **Detached tasks go through `app/utils/background.spawn`**, never a bare `asyncio.create_task(...)` — an unreferenced task can be garbage-collected mid-flight (a test enforces this). Background LLM calls use the shared clients in `app/integrations/llm_clients.py`.
- **Never act destructively on a failure.** A failed classification (`classification_failed`) keeps the lead; a post-call step with a missing input is skipped, not run on a guess. A genuine guest opportunity must not silently disappear.
- **Tests are hermetic**: `tests/conftest.py` ignores `.env` and clears every external credential — never call a real service from a test (mark deliberate live tests `live_api`, run with `RUN_LIVE_TESTS=1`).
- **Kill and restart uvicorn after any `.env` change** — `pydantic-settings` reads `Settings()` once via `@lru_cache`; hot reload never picks up env changes.

## Commands

**Backend:**
- Dev: `uvicorn app.main:app --reload` (from `backend/`)
- Test: `pytest` (from `backend/`; needs a real local Postgres `mira_test` and Redis — no `.env` involved)
- Lint: `ruff check .` (config in `backend/ruff.toml`: real-defect rules; CI runs it)
- Dependencies: edit `requirements.txt`, then `uv pip compile requirements.txt --universal --python-version 3.12 --generate-hashes -o requirements.lock` (Docker and CI install from the lock)
- Migrate: `alembic upgrade head`
- New migration: `alembic revision --autogenerate -m "description"` (review before applying)

**Frontend:**
- Dev: `npm run dev` (from `frontend/`)
- Install: `npm install`

**Ops:**
- LLM health: `curl <backend>/api/v1/health/llm`
- Seed demo: `python3 seed_demo.py` → creates the `demo@mira.ai` host with 12 properties (sign in through Clerk; the legacy row is linked by verified email on first login)

## Scaffold Growth

After meaningful work, run GROW:
- Ground: what changed in reality?
- Record: update `ROUTER.md` and relevant `context/` files
- Orient: create or update a `patterns/` runbook if this can recur
- Write: bump `last_updated` on changed scaffold files and run `mex log` when rationale matters

The scaffold grows from real work, not just setup. See the GROW step in `ROUTER.md` for details.

## Navigation

At the start of every session, read `ROUTER.md` before doing anything else.
For full project context, patterns, and task guidance — everything is there.
