---
name: debug-voice-call
description: Diagnose a voice call failure — wrong behavior, silence, repeated output, missed tool call, or LLM routing issues. Covers log reading, guard processor triggers, and LLM model tracking.
triggers:
  - "debug call"
  - "voice call broken"
  - "mira not responding"
  - "call silent"
  - "call ended early"
  - "wrong answer"
  - "tool not called"
  - "LLM not working"
  - "429"
  - "rate limit"
  - "strategy: None"
  - "degenerate completion"
edges:
  - target: context/voice-pipeline.md
    condition: for pipeline stage order and guard processor details
  - target: context/architecture.md
    condition: for understanding the call routing and DB layer
  - target: patterns/voice-pipeline-changes.md
    condition: when the diagnosis reveals a pipeline fix is needed
last_updated: 2026-08-08
---

# Debug Voice Call

## Context

Load `context/voice-pipeline.md` before starting. Voice call failures almost always fall into one of four buckets:
1. **LLM routing / model failure** — 429, wrong model selected, OpenRouter fallback
2. **Pipeline / guard processor misfire** — guard activated on a normal turn, or failed to activate
3. **STT / TTS failure** — Sarvam connection dropped, language not detected, garbled audio
4. **Tool call failure** — wrong args, handler error, DB query failed

Read logs against the actual call being investigated. Do not infer the failure mode from config — grep the actual Railway/backend log output for that specific call's request URL, service class name, and error message.

## Step 1: Identify the Call in Logs

**Railway logs**: `railway logs --service mira-backend` (from `backend/` after `railway link`)  
**Local logs**: uvicorn stdout; loguru routes to stderr at DEBUG level

For each call, find the `WebSocket ... [accepted]` or `POST .../test/offer` entry. All log lines for that call share the same `call_session_id` (a UUID in most structured log entries).

Look for: the model that actually handled the call:
```
# Groq path:
INFO  LLM health OK (groq/openai/gpt-oss-120b, 0.342s)
# OpenRouter path:
INFO  LLM health OK (openrouter/openai/gpt-oss-120b, ...)
```

**The actual request URL tells you which path was used** — `api.groq.com` vs `openrouter.ai`. The model names can be identical across both providers.

## Step 2: Identify the Failure Mode

### Silence / No Response from Mira

**Check**: was the STT websocket alive?
- Look for `SarvamSTTService` reconnect lines: `_ReconnectingSarvamSTTService: reconnecting after server-side close`
- If Sarvam closed mid-call (code 1000, "ASR model call failed"), the reconnect subclass fires. If you see 500+ identical `ErrorFrame` lines in 4s, the reconnect didn't work — investigate `_ReconnectingSarvamSTTService`.

**Check**: did `strategy: None` appear in logs?
- `strategy: None` means pipecat's generic stuck-turn watchdog fired — the configured strategy's own logic never completed. This is a real signal, not just a slow turn. Check `SpeechTimeoutUserTurnStopStrategy` or `HybridCompletenessUserTurnStopStrategy` for the specific call.

**Check**: `RedundantContextGuardProcessor` — if it dropped a legitimate `LLMContextFrame`, Mira gets no completion trigger for that turn.

**Check**: is DB alive? (`_check_db_health` ping failures in logs = Neon suspended). First query after Neon wakes adds 2-5s.

### Call Ended Early / Prematurely

**Check**: `end_call` or `decline_irrelevant_call` tool was called by the LLM unexpectedly. Look for `FunctionCallsStartedFrame` with `function_name: "end_call"` or `decline_irrelevant_call` in logs.

**Check**: `PrematureEndCallGuardProcessor` — if it did NOT fire when it should have (same-turn `end_call` + `?`), check that the guard is wired in the pipeline.

**Check**: silence watchdog timeout — if the guest was truly silent for 9s after each nudge (×2), the call legitimately ended. Confirm by checking `TranscriptionFrame` lines before the hangup.

### Repeated / Degenerate Output (Same Sentence Many Times)

**Check**: `RepetitionGuardProcessor` — did it activate? Look for `RepetitionGuard: dropping frame (similarity=X.XX)`.

**Check**: `max_completion_tokens=400` is set. If the completion was >400 tokens, `RepetitionGuard` may not have kept up. Look for `completion_tokens: <N>` in usage metrics.

**Check**: `ResponseShapeValidatorProcessor` — if it activated, the response was already trimmed. Look for `ResponseShapeValidator: trimming to first sentence`.

### Wrong Answer / Hallucinated Content

**Check**: which tool was called, and what did the handler return? Look for `tool_result:` log lines.

**Check**: `PropertyRecommendationGuardProcessor` — if a property name or price in the spoken response doesn't match the tool result, this guard should have caught it. Look for `PropertyRecommendationGuard: overriding reply` in logs.

**Check**: did `search_faq` actually return the relevant information? If not, `search_faq` may have fallen through its tier chain (FaqEntry → legacy faq → full property context) without finding it.

### "Let me loop in the host" or Similar Escalation Phrasing

**Check**: `EscalationPhraseGuardProcessor` — it should unconditionally replace the reply after `escalate_to_host`. If the bad phrasing reached TTS, either:
1. The guard wasn't armed (check `FunctionCallsStartedFrame` logging)
2. The guard was superseded by another processor
3. The call predates the 2026-07-27 rewrite (check the guard's `_armed` logic)

### LLM 429 / Rate Limit

**Check**: `GET /api/v1/health/llm` — `curl <backend_url>/api/v1/health/llm`. Shows per-model health + last check time.

**Check**: `_FallbackGroqLLMService` — does it appear in logs with `retrying with model: <next_model>`? If not, fallback didn't trigger.

**Check**: all models in `settings.groq_models` — are all marked `"ok": false`? If so, OpenRouter is the last resort. Grep logs for `openrouter.ai` in the actual request URL.

**Check**: model IDs in `GROQ_MODELS` env var — Groq retires model IDs without notice (e.g. `llama-3.3-70b-versatile` removed 2026-06-17). A 404 on a model ID looks like a failure but is really a "model no longer exists" error. Verify against `groq client.models.list()`.

## Step 3: Reproduce Locally

1. Set `LOG_LEVEL=DEBUG` (or confirm loguru is at DEBUG — it is by default in `main.py`)
2. Use the browser voice test (`POST /voice/test/offer` → WebRTC) to reproduce without a real Exotel call
3. Reproduce the guest's exact input to trigger the same failure path
4. Grep the local log for the specific guard processor or tool that should have fired

## Step 4: Fix

- **LLM compliance failure that recurs across calls** → new guard processor (see `patterns/voice-pipeline-changes.md#task-add-a-guard-processor`)
- **One-off prompt compliance gap** → update GOLDEN_RULES in `app/prompts/system_prompt.py`
- **Tool handler returned wrong content** → fix `app/services/tool_handlers.py`
- **STT reconnect loop** → check `_ReconnectingSarvamSTTService` reconnect logic
- **Model retired from Groq** → update `GROQ_MODELS` env var; verify new model supports function calling

## Common Traps

- **"It must be OpenRouter because the model ID matches"** — confirmed live (2026-07-27): a plausible-sounding OpenRouter theory was flat wrong once the actual logs for that exact call were pulled. The request never left Groq. Always grep the actual request URL (`api.groq.com` vs `openrouter.ai`), not the model name.
- **"The guard must have fixed it, I see the code"** — guards arm via `FunctionCallsStartedFrame`. If the frame arrived before the guard was wired, or if the guard disarmed prematurely, the bad reply still reaches TTS. Confirm arming and disarming in logs.
- **"strategy: None means it's slow"** — no. `strategy: None` means pipecat's generic stuck-turn watchdog fired because the configured strategy's logic never completed. Investigate the strategy, not timing.
- **"Changing the prompt will fix it"** — only for novel failures not yet confirmed live. For any failure that has recurred on real calls with a correctly-worded prompt already in place, a guard processor is the right fix.

## Update Scaffold

- [ ] If a new recurring failure pattern was found: add a guard processor + update `context/voice-pipeline.md`
- [ ] If a model ID was retired: update `context/setup.md` "Common Issues" and `GROQ_MODELS` env var
- [ ] If a new diagnosis step was needed: add it to this pattern
