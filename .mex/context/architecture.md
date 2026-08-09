---
name: architecture
description: How the major pieces of MIRA connect and flow. Load when working on system design, integrations, or understanding how components interact.
triggers:
  - "architecture"
  - "system design"
  - "how does X connect to Y"
  - "integration"
  - "flow"
  - "call flow"
  - "pipeline"
edges:
  - target: context/stack.md
    condition: when specific technology details are needed
  - target: context/decisions.md
    condition: when understanding why the architecture is structured this way
  - target: context/voice-pipeline.md
    condition: when working on the real-time voice pipeline, guard processors, or STT/TTS/LLM routing
  - target: patterns/add-endpoint.md
    condition: when adding a new REST endpoint
  - target: patterns/debug-voice-call.md
    condition: when diagnosing a voice call failure
last_updated: 2026-08-08
---

# Architecture

## System Overview

A guest phone call flows through the system as follows:

```
Exotel telephony
  → WSS /api/v1/voice/exotel/ws/<token>  (app/api/v1/voice.py)
  → run_voice_pipeline (app/voice/pipeline.py)
      resolves Property/User from dialed number via call_service
      builds ConversationState, GuestProfile, CallSession
      builds system prompt (app/prompts/system_prompt.py)
  → pipecat Pipeline:
      Sarvam STT → 7 guard processors → Groq LLM (function-calling)
      → 4 output guard processors → Sarvam TTS → Exotel transport
  → LLM tool calls → app/voice/tools.py (context binding)
                   → app/services/tool_handlers.py (business logic)
                   → PostgreSQL (Neon)
  → on_pipeline_finished: transcript saved, post-call classification + summary run
```

The dashboard (`frontend/src/app/dashboard/`) polls REST endpoints under `/api/v1` and streams notifications via SSE (`GET /api/v1/notifications/stream`). The in-browser voice test (`POST /voice/test/offer`, WebRTC) reuses the same `_run_pipeline` core as real Exotel calls.

Five APScheduler jobs run in-process inside the FastAPI lifespan: iCal sync (every 15 min), LLM health check (every 60s), DB keep-alive ping (every 3 min), smart pricing refresh (daily 01:00 UTC), live pricing cache refresh (daily 01:15 UTC).

## Key Components

- **`app/voice/pipeline.py` (`_run_pipeline`)** — builds a pipecat `Pipeline` per call, selects the LLM via health-checked fallback chain (`_pick_groq_model` → `_FallbackGroqLLMService`), wires all guard processors, owns ringing-tone task lifecycle.
- **`app/voice/tools.py` + `app/services/tool_handlers.py`** — 12 voice tools; `tools.py` binds `property_id`/`host_user_id`/`conversation_state` via factory closure; `tool_handlers.py` is the actual business logic. Tool results are natural-language strings (TTS-ready).
- **`app/prompts/system_prompt.py`** — per-call system prompt builder; contains `GOLDEN_RULES` block injected into both Guest Support and Lead Agent prompts.
- **`app/services/pricing_engine.py`** — nightly rate calc + negotiation; called by `get_pricing`/`negotiate_rate` tools. Never call directly from routes.
- **`app/services/calendar_service.py`** — iCal sync; `sync_all_properties` scheduled every 15 min; writes `Booking` rows from external ical feeds.
- **`app/main.py` lifespan** — starts 5 APScheduler jobs + fires one-shot startup tasks (iCal sync, LLM health, DB keepalive, display_name backfill).
- **`frontend/src/lib/api.ts`** — single API client for the frontend; all calls attach `Authorization: Bearer <token>` from `localStorage` (`mira_token` key); throws `ApiError` on non-2xx.
- **`app/config.py` (`Settings`)** — single pydantic-settings class, read once via `@lru_cache`; never hot-reloaded; `.env` changes require a full process restart.

## External Dependencies

- **PostgreSQL (Neon serverless)** — primary DB; auto-suspends after inactivity; `_check_db_health` pings every 3 min to keep the connection warm. Shared across Railway and Render (same `DATABASE_URL`).
- **Exotel** — telephony; routes live calls via WSS to `/api/v1/voice/exotel/ws/<EXOTEL_WEBHOOK_TOKEN>`; token is a path segment (Exotel strips query strings). Separate HTTP webhook for call-status metadata at `POST /api/v1/webhooks/exotel/call-status`.
- **Sarvam AI** — STT (`codemix` mode: Hindi/English/Hinglish, no translation) + TTS (`bulbul:v3`, `roopa` speaker, `pace=1.15`). `LanguageSyncProcessor` switches TTS language live when the guest switches languages.
- **Groq** — primary LLM; `openai/gpt-oss-120b` as first choice; fallback chain via `GROQ_MODELS` JSON array; health-checked every 60s; `max_completion_tokens=400`; `reasoning_format="hidden"` to suppress chain-of-thought from the reply text.
- **Redis** — pricing cache for 7-day nightly rates. `REDIS_URL` **not currently set on Railway** — cache is fully wired but inert in production.
- **Twilio** — WhatsApp sandbox for guest/host messages; `TWILIO_ESCALATION_TEMPLATE_SID` gives escalations a "Go to Dashboard" button. Sandbox only reaches numbers that have texted "join `<code>`" first; 24h customer-service window applies.
- **SearchApi.io** — live Airbnb pricing for properties with `exact_airbnb_pricing=true`; daily city-wide refresh + per-call fetch. Unset = falls back to computed pricing.
- **Cloudinary** — re-hosts property photos so they survive source-listing edits.
- **Bright Data** — Airbnb listing scrape for import. Unset = import fails with a clear user-facing error.
- **Railway + Vercel** — primary deployment (backend + frontend). Render kept running as fallback but not actively deployed to.

## What Does NOT Exist Here

- No background worker / job queue (Celery, RQ, etc.) — all scheduled work runs in-process via APScheduler inside FastAPI's lifespan.
- No real-time push from backend to dashboard — notifications are polled or streamed via SSE from the DB, not pushed via a persistent WebSocket from the API server.
- No file storage abstraction layer — Cloudinary is called directly from `app/integrations/cloudinary_client.py`.
- No admin panel — `founder-console/` is a minimal separate directory; the main `frontend/` dashboard is host-facing only.
- No completed booking-finalization flow — Mira qualifies leads and escalates to the host; there is no tool that confirms/finalizes a booking. Verbal accepts must go through `escalate_to_host`.
