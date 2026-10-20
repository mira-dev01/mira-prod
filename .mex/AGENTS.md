---
name: agents
description: Always-loaded project anchor. Read this first. Contains project identity, non-negotiables, commands, and pointer to ROUTER.md for full context.
last_updated: 2026-08-08
---

# MIRA — AI Property Management Assistant

## What This Is

A FastAPI + Next.js SaaS that handles real-time guest phone calls for short-term rental hosts in India via a pipecat voice pipeline (Exotel telephony → Sarvam STT → Groq LLM → Sarvam TTS), with a host dashboard for leads, bookings, pricing, and escalations.

## Non-Negotiables

- **Never mock the DB in tests** — all tests hit a real PostgreSQL instance; mock-vs-prod divergence caused a real production incident.
- **Business logic lives in `app/services/`**, never in `app/api/v1/` route handlers — route handlers call service functions only.
- **Voice tool handlers return natural-language strings**, never dicts or structured types — the return value is TTS-ready and spoken aloud to the guest.
- **Recurring LLM compliance failures get a guard processor**, not just a prompt rule update — confirmed-live phrasing variants slip through prompt-only fixes; code backstops in `app/voice/` are unconditional.
- **Kill and restart uvicorn after any `.env` change** — `pydantic-settings` reads `Settings()` once via `@lru_cache`; hot reload never picks up env changes.

## Commands

**Backend:**
- Dev: `uvicorn app.main:app --reload` (from `backend/`)
- Test: `pytest` (from `backend/`; requires real Postgres)
- Lint/Format: `ruff check .` / `ruff format .`
- Migrate: `alembic upgrade head`
- New migration: `alembic revision --autogenerate -m "description"` (review before applying)

**Frontend:**
- Dev: `npm run dev` (from `frontend/`)
- Install: `npm install`

**Ops:**
- LLM health: `curl <backend>/api/v1/health/llm`
- Seed demo: `python3 seed_demo.py` → `demo@mira.ai` / `MiraDemo2024`

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
