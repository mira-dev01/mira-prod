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
last_updated: 2026-11-09
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
                   → PostgreSQL (Neon in production, Supabase in dev)
  → guest disconnects: call lease released at once (_CallLeaseGuard)
  → on_pipeline_finished: isolated post-call steps (_PostCallSteps) — transcript saved, classification, summary, tickets, telemetry, lead backfill
```

The dashboard (`frontend/src/app/dashboard/`) polls REST endpoints under `/api/v1` and streams notifications via SSE (`GET /api/v1/notifications/stream`). The in-browser voice test (`POST /voice/test/offer`, WebRTC) reuses the same `_run_pipeline` core as real Exotel calls.

Twelve APScheduler jobs run in-process inside the FastAPI lifespan, on the same event loop as live call audio: iCal sync (15 min), LLM health (60s), DB keep-alive + pool-pressure check (60s), busy-callback sweep (60s), payment sweep (60s), stuck-call reconcile (10 min), smart pricing refresh (daily 01:00 UTC), live pricing cache refresh (daily 01:15 UTC), and the service-health monitor (tick, probes, credits, daily digest). Only one process may run them.

## Key Components

- **`app/voice/pipeline.py` (`_run_pipeline`)** — builds a pipecat `Pipeline` per call, selects the LLM via health-checked fallback chain (`_pick_groq_model` → `_FallbackGroqLLMService`), wires all guard processors, owns ringing-tone task lifecycle.
- **`app/voice/tools.py` + `app/services/tool_handlers.py`** — 12 voice tools; `tools.py` binds `property_id`/`host_user_id`/`conversation_state` via factory closure; `tool_handlers.py` is the actual business logic. Tool results are natural-language strings (TTS-ready).
- **`app/prompts/system_prompt.py`** — per-call system prompt builder; contains `GOLDEN_RULES` block injected into both Guest Support and Lead Agent prompts.
- **`app/services/pricing_engine.py`** — nightly rate calc + negotiation; called by `get_pricing`/`negotiate_rate` tools. Never call directly from routes.
- **`app/services/calendar_service.py`** — iCal sync; `sync_all_properties` scheduled every 15 min; writes `Booking` rows from external ical feeds.
- **`app/main.py` lifespan** — starts the 12 APScheduler jobs, fires one-shot startup tasks via `background.spawn`, runs the Take Call Redis listener, and on shutdown closes the shared LLM clients.
- **`frontend/src/lib/api.ts`** — single API client for the frontend; all calls attach `Authorization: Bearer <token>` where the token is Clerk's session token (registered via `setTokenGetter`); throws `ApiError` on non-2xx.
- **`app/auth/dependencies.py`** — `get_current_user` verifies the Clerk JWT (RS256, `iss`/`azp`/`exp`/`iat`/`sub`; JWKS fetch in a thread) and links a Clerk identity to a local `User` only by a Clerk-verified email.
- **`app/utils/background.py`, `app/integrations/llm_clients.py`** — GC-safe detached tasks; shared, time-limited LLM clients.
- **`app/config.py` (`Settings`)** — single pydantic-settings class, read once via `@lru_cache`; never hot-reloaded; `.env` changes require a full process restart.

## External Dependencies

- **PostgreSQL** — Neon (serverless, auto-suspends after inactivity) in Railway's `production` environment; Supabase (session-mode pooler, port 5432) in `dev`. `_check_db_health` pings every 60s (keeps the connection warm, is the health probe, warns on pool pressure). The SQLAlchemy pool is set explicitly (`DB_POOL_*`); behind a session-mode pooler every pooled connection is a pooler slot.
- **Exotel** — telephony; routes live calls via WSS to `/api/v1/voice/exotel/ws/<EXOTEL_WEBHOOK_TOKEN>`; token is a path segment (Exotel strips query strings). Separate HTTP webhook for call-status metadata at `POST /api/v1/webhooks/exotel/call-status`.
- **Sarvam AI** — STT (`codemix` mode: Hindi/English/Hinglish, no translation) + TTS (`bulbul:v3`, `roopa` speaker, `pace=1.15`). `LanguageSyncProcessor` switches TTS language live when the guest switches languages.
- **Groq** — primary LLM; `openai/gpt-oss-120b` as first choice; fallback chain via `GROQ_MODELS` JSON array; health-checked every 60s; `max_completion_tokens=400`; `reasoning_format="hidden"` to suppress chain-of-thought from the reply text.
- **Redis (Upstash)** — three separate uses, three modules: TTL cache (`redis_client.py`: SearchApi pricing, query embeddings; fails open), `CallCoordinator` call leases (`redis_lease_client.py`; correctness-bearing), and the Take Call pub/sub signal (`redis_signal_client.py`; best-effort).
- **Twilio** — WhatsApp delivery from a real WhatsApp Business number (off the sandbox since 2026-09-12); `TWILIO_ESCALATION_TEMPLATE_SID` gives escalations a "Go to Dashboard" button. The 24h customer-service window still applies to freeform messages. Twilio Voice is also wired as a fallback telephony path.
- **Resend** — host email (escalations, call summaries, admin login codes) over HTTPS; Railway blocks outbound SMTP.
- **SearchApi.io** — live Airbnb pricing for properties with `exact_airbnb_pricing=true`; daily city-wide refresh + per-call fetch. Unset = falls back to computed pricing.
- **Cloudinary** — re-hosts property photos so they survive source-listing edits.
- **Bright Data** — Airbnb listing scrape for import. Unset = import fails with a clear user-facing error.
- **Railway + Vercel** — the deployment (backend + frontend; Railway has `dev` and `production` environments). Render is no longer used (`render.yaml` deleted 2026-11-09). See `docs/deployment.md`.

## What Does NOT Exist Here

- No background worker / job queue (Celery, RQ, etc.) — all scheduled work runs in-process via APScheduler inside FastAPI's lifespan.
- No real-time push from backend to dashboard — notifications are polled or streamed via SSE from the DB, not pushed via a persistent WebSocket from the API server.
- No file storage abstraction layer — Cloudinary is called directly from `app/integrations/cloudinary_client.py`.
- The internal operator panel lives in the same frontend at `/admin` (own emailed-code login, `app/api/v1/admin*.py`); `founder-console/` is a separate minimal directory.
- No fully automated booking-finalization: Mira qualifies leads, can send a Razorpay payment link that holds the dates (`send_payment_link`, only off a firm — non-estimate — quote), and otherwise escalates to the host (`escalate_to_host`).
