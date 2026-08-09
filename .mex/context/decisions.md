---
name: decisions
description: Key architectural and technical decisions with reasoning. Load when making design choices or understanding why something is built a certain way.
triggers:
  - "why do we"
  - "why is it"
  - "decision"
  - "alternative"
  - "we chose"
  - "why not"
edges:
  - target: context/architecture.md
    condition: when a decision relates to system structure
  - target: context/stack.md
    condition: when a decision relates to technology choice
  - target: context/voice-pipeline.md
    condition: when a decision relates to voice pipeline behavior or LLM routing
last_updated: 2026-08-08
---

# Decisions

<!-- When a decision changes: DO NOT delete the old entry. Mark it superseded, add the new entry above it. -->

## Decision Log

### Use Groq with health-checked multi-model fallback chain
**Date:** 2026-07-07
**Status:** Active
**Decision:** `GROQ_MODELS` holds a JSON-array priority list; `_check_llm_health` (every 60s + startup) pings each model and stores health in `llm_health` dict; `_build_llm` picks the first healthy model; `_FallbackGroqLLMService` retries the next model on a live 429 within the same turn.
**Reasoning:** `openai/gpt-oss-120b` hits per-model rate limits under call bursts even on a paid plan; a single model left real calls dead. The fallback chain routes around a rate-limited model in <60s rather than failing every call during a burst.
**Alternatives considered:** Single Groq model (rejected — rate limit kills calls), OpenRouter-first (rejected — higher latency; OpenRouter is kept as last resort only after every Groq model is down).
**Consequences:** `GROQ_MODELS` must be checked against `client.models.list()` before editing. Every model in the list must support function calling. `reasoning_effort: "low"` is gpt-oss–only and must be applied conditionally.

### Use unconditional code-level guard processors for repeated LLM compliance failures
**Date:** 2026-07-27
**Status:** Active
**Decision:** Seven `*GuardProcessor`/`*Processor` classes in `app/voice/` enforce critical GOLDEN_RULES in code, not prompt wording alone. For example, `EscalationPhraseGuardProcessor` unconditionally replaces the reply after `escalate_to_host` — no detection step.
**Reasoning:** Multiple confirmed-live failures where correctly-worded prompt rules were violated: "let me loop in the host" variants, meta-commentary spoken to guests, invented property UUIDs spoken aloud, 3072-token degenerate completions, end-call mid-question. Regex detection was whack-a-mole — new phrasing variants slipped through. Unconditional replacement removes the detection gap entirely.
**Alternatives considered:** Prompt rewriting (rejected — same compliance gap, different wording), second LLM judgment call (rejected — latency; fix must be synchronous with TTS output).
**Consequences:** Any new failure category that recurs across real calls gets its own guard processor, not just a prompt rule update. Pattern: arm on `FunctionCallsStartedFrame`, replace synchronously before TTS, never rewrite to empty.

### Pipecat STT/TTS connected sequentially (pre-connect attempt reverted)
**Date:** 2026-07-21
**Status:** Active
**Decision:** STT and TTS connect in pipeline order during `StartFrame` propagation. An explicit pre-connect attempt was reverted same-session.
**Reasoning:** `asyncio.gather(stt._connect(), tts._connect())` before `Pipeline` construction crashed every real call in production (`TaskManager is not initialized`) — pipecat assigns `task_manager` only once a service is attached to a running `PipelineWorker`, so `create_task()` inside `_connect()` before that has nothing to attach to. Reverted same-session after production outage.
**Alternatives considered:** Pre-connecting concurrently (tried — broke production), patching pipecat internals (deprioritized — high risk for ~1s gain, would need to replicate pipecat's private startup sequence).
**Consequences:** ~2-2.5s STT+TTS connect time remains in call setup cost; covered by the ringing tone (`app/voice/ringing_audio.py`). Do not attempt concurrent pre-connect again without verifying against pipecat's `task_manager` assignment lifecycle.

### Railway (backend) + Vercel (frontend) as primary; Render kept as fallback
**Date:** 2026-07-21
**Status:** Active
**Decision:** Railway hosts the backend and Vercel the frontend for all active testing and real Exotel calls. Render is kept running but not actively deployed to.
**Reasoning:** Railway/Vercel became the actual production path; Render was deliberately not torn down as a fallback if either primary fails. Railway Trial tier blocks outbound SMTP (port 587) — emails cannot be sent until either upgrading the tier or switching to an HTTP-based email API (not yet built).
**Alternatives considered:** Render-only (original setup, replaced when Railway/Vercel became primary).
**Consequences:** `CORS_EXTRA_ORIGINS`/`FRONTEND_BASE_URL` on Railway must be set to Vercel's URL. `NEXT_PUBLIC_API_BASE_URL` on Vercel must include the full scheme and end in `/api/v1` — a schemeless value silently resolves every API call as a relative path against Vercel (confirmed live, recurred twice). Railway's public domain has shown DNS resolution failures from some resolvers (works via 8.8.8.8, fails from some home networks).

### No DB mocking in tests — real Postgres only
**Date:** (early in project)
**Status:** Active
**Decision:** All tests hit a real PostgreSQL test DB. DB mocking is explicitly not used anywhere in the test suite.
**Reasoning:** Mock-vs-prod divergence caused a real past incident where mocked tests passed but the production migration failed. Real DB confirms actual schema/query behavior.
**Alternatives considered:** SQLAlchemy in-memory SQLite (rejected — different dialect, missed real migration failures), per-test DB fixtures with mocking (rejected — same root problem).
**Consequences:** Tests require a live PostgreSQL instance with `DATABASE_URL` set. Slower than mocked tests but much higher confidence on schema changes.

### Exotel WSS token as path segment, not query param
**Date:** (confirmed live 2026-07-21)
**Status:** Active
**Decision:** The Exotel webhook URL uses the token as a path segment: `wss://.../voice/exotel/ws/<EXOTEL_WEBHOOK_TOKEN>`, not `?token=<value>`.
**Reasoning:** Exotel's Voicebot Applet strips query strings from the configured WSS URL before connecting — every real Exotel connection arrived at the bare path with no query string. A `?token=` approach would have no token by the time the websocket is accepted.
**Alternatives considered:** Query param auth (tried — Exotel strips it before connecting).
**Consequences:** Token is a path segment in the route definition and must be set as part of the full WSS URL in Exotel's Voicebot Applet configuration.

### pipecat-ai pinned with explicit floor+cap (not unbounded >=)
**Date:** 2026-07-21
**Status:** Active
**Decision:** `pipecat-ai[...]>=1.5.0,<2.0` in `requirements.txt` — explicit floor, major-version cap.
**Reasoning:** Railway silently rebuilt onto 1.5.0 while local dev was still on 1.4.0 — an unbounded range (`>=0.0.60`) means every fresh `pip install` (i.e. every Railway deploy) can land on a different release than whatever was last tested, with no record of which version any deploy is running.
**Alternatives considered:** Exact pin (rejected — too brittle for patch releases), unbounded (rejected — production outage risk, confirmed live).
**Consequences:** Bump the floor deliberately when there is a real reason to upgrade. Never widen the range back to `>=0.x.y`.
