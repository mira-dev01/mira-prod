---
name: voice-pipeline
description: Real-time voice pipeline design, guard processors, LLM routing, STT/TTS, and GOLDEN_RULES. Load when working on the pipecat pipeline, guard processors, LLM model selection, or voice call behavior.
triggers:
  - "voice pipeline"
  - "guard"
  - "pipecat"
  - "STT"
  - "TTS"
  - "sarvam"
  - "groq"
  - "GOLDEN_RULES"
  - "tool handler"
  - "silence watchdog"
  - "turn detection"
  - "escalation"
  - "LLM fallback"
edges:
  - target: context/architecture.md
    condition: when understanding how the pipeline connects to telephony, DB, and dashboard
  - target: context/decisions.md
    condition: when understanding why a guard processor exists or why concurrent STT/TTS connect was reverted
  - target: patterns/voice-pipeline-changes.md
    condition: when modifying the pipeline — adding a guard, tuning a param, adding a tool
  - target: patterns/debug-voice-call.md
    condition: when diagnosing a voice call failure from logs
last_updated: 2026-08-08
---

# Voice Pipeline

## Two Agent Modes

Both modes share `_run_pipeline` in `app/voice/pipeline.py` and the same 12 tools. Only the system prompt, first message, and whether `property_id` is fixed differ.

| Mode | Entry | Prompt builder | Scope |
|---|---|---|---|
| **Guest Support** | Property's `exophone` | `build_system_prompt` | Fixed to one property |
| **Lead Agent** | User's `lead_exophone` | `build_lead_system_prompt` | Full portfolio; `recommend_properties` qualifies then locks a property |

Browser test variants (`run_browser_voice_pipeline`, `run_browser_lead_pipeline`) exercise the same `_run_pipeline` core over WebRTC instead of Exotel, using a fixed placeholder caller number.

## Pipeline Stage Order

```
transport.input()
  → SarvamSTTService (_ReconnectingSarvamSTTService — auto-reconnects on server-side close)
  → SilenceWatchdogProcessor (silence nudge + graceful call-end arming)
  → LanguageSyncProcessor (live TTS language switch on Hindi/English detection)
  → user_aggregator
  → RedundantContextGuardProcessor (drops duplicate LLMContextFrame re-invocations)
  → StatePromptSyncProcessor (injects ConversationState as LLM context hints)
  → LLM (_FallbackGroqLLMService — health-checked model selection + live 429 fallback)
  → RepetitionGuardProcessor (drops near-duplicate sentences in degenerate completions)
  → MetaCommentaryGuardProcessor (drops narrator/stage-direction parentheticals)
  → PropertyRecommendationGuardProcessor (strips leaked UUIDs; verifies property names + prices spoken)
  → EscalationPhraseGuardProcessor (unconditionally replaces reply after escalate_to_host)
  → PrematureEndCallGuardProcessor (cancels end_call when same turn also contains a "?")
  → ResponseShapeValidatorProcessor (trims to first complete sentence on shape violations)
  → SarvamTTSService (pace=1.15; starts EN_IN; switched to HI_IN live)
  → transport.output()
  → assistant_aggregator
```

All guard processors are **pass-through by default** — zero latency on normal turns. Each activates only on its specific narrow condition.

## Guard Processors — What Each Guards Against (Confirmed Live Failures)

| Processor | File | Condition | Action |
|---|---|---|---|
| `RedundantContextGuardProcessor` | `redundant_context_guard.py` | `LLMContextFrame` with same message count as last | Drop the frame (prevents spurious second completion) |
| `RepetitionGuardProcessor` | `repetition_guard.py` | ≥60% word overlap with a prior sentence this turn, or flood of <3-word fragments | Silently drop remaining frames for this response |
| `MetaCommentaryGuardProcessor` | `meta_commentary_guard.py` | Text inside `(...)` matching waiting/listening/pause/thinking | Drop that span; legitimate parentheticals pass through |
| `PropertyRecommendationGuardProcessor` | `property_recommendation_guard.py` | After any property-aware tool call | Strip leaked UUID from text; verify reply names returned property / states correct price / avoids contradicting availability |
| `EscalationPhraseGuardProcessor` | `escalation_phrase_guard.py` | Arms on `escalate_to_host` tool call via `FunctionCallsStartedFrame` | Unconditionally replaces first text-bearing LLM response with fixed safe line — no detection step |
| `PrematureEndCallGuardProcessor` | `premature_end_call_guard.py` | Same-turn `end_call` + `"?"` in text | Calls `silence_watchdog.cancel_end_request()` so call falls through to normal silence path |
| `ResponseShapeValidatorProcessor` | `response_shape_guard.py` | >1 unconnected question, >1 greeting, duplicated escalation line, duplicated-punctuation flood, response ending mid-clause | Keep only first complete sentence |

## LLM Routing — Groq Fallback Chain

1. **Model selection**: `_pick_groq_model()` returns first model in `settings.groq_models` not marked down in `llm_health` (populated by `_check_llm_health` in `main.py` every 60s).
2. **Live 429 handling**: `_FallbackGroqLLMService` sets `max_retries=0` on the Groq client (prevents SDK retry against the same rate-limited model) and retries the next model in `settings.groq_models` immediately on `RateLimitError`.
3. **Last resort**: if all Groq models are down, `_build_llm()` falls through to `_build_openrouter_llm` (`OpenAILLMService` pointed at OpenRouter; `max_completion_tokens=900`).
4. **Key params for gpt-oss models only**: `reasoning_effort: "low"` (disables hidden chain-of-thought pass — real multi-second latency source), `reasoning_format: "hidden"` on Groq / `extra_body={"reasoning": {"exclude": True}}` on OpenRouter (prevents chain-of-thought from appearing in the reply text). Both must go via `extra_body`, not bare kwargs.
5. **`max_completion_tokens=400`** on Groq (added 2026-07-27): confirmed live that a degenerate completion reached 3072 tokens on a long noisy call; 400 bounds it while giving real headroom.

## STT Details

- `_ReconnectingSarvamSTTService` subclasses pipecat's `SarvamSTTService` with reconnect-on-failure. Confirmed live: Sarvam closed the STT websocket server-side mid-call (code 1000, transient backend failure), causing 500+ identical ErrorFrames in 4s with the guest's audio silently dropped.
- `mode="codemix"` — transcribes Hindi/English/Hinglish as spoken; no translation. Language detected per utterance from `TranscriptionFrame`.

## TTS Details

- `SarvamTTSService`, `bulbul:v3`, `roopa` speaker, `pace=1.15`.
- `LanguageSyncProcessor` watches `TranscriptionFrame.language` and pushes `TTSUpdateSettingsFrame` (delta-only) live when the guest switches language — EN_IN → HI_IN or back.

## VAD Parameters

| Param | Value | Notes |
|---|---|---|
| confidence | 0.85 | raised from pipecat default 0.7 — reduces false interruptions from background noise |
| min_volume | 0.7 | raised from 0.6 — same |
| start_secs | 0.35s | raised from pipecat default 0.2s (2026-07-23) — 0.2s fires on mic bumps, not real speech |

`create_vad_analyzer` in `app/voice/vad.py` loads the ONNX session once at process startup (not per call) — avoids ~2s `SileroVADAnalyzer.__init__` cost per call.

## Turn Detection

- **Default (production)**: `SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.9)` — 0.9s validated middle ground; 0.6s cut guests off, 1.4s added dead air.
- **Experimental** (`TURN_DETECTION_STRATEGY=hybrid_experimental`): `HybridCompletenessUserTurnStopStrategy` — extends 0.9s base by 0.7s (up to 2.8s cap) on trailing conjunction/comma/short utterance heuristic. Experimental only; not in `render.yaml`.

## Silence Watchdog

`SilenceWatchdogProcessor` sits immediately after STT. Two behaviors:
1. **Silent caller**: nudges after 9.0s silence, ends call after 2nd unanswered nudge. Blank/whitespace transcripts never reset the timer.
2. **Graceful end-of-call**: `end_call` and `decline_irrelevant_call` tools call `request_end_after_current_turn()` — the next `BotStoppedSpeakingFrame` fires `EndWorkerFrame`. If the guest speaks again while pending, the request is cancelled.

## Bot Speaks First (Greeting)

- First message (`User.agent_first_message`, templated with `{host_name}/{property_name}/{city}/{guest_name}`) is pre-seeded into LLM context as an assistant turn.
- Spoken via `worker.queue_frame(TTSSpeakFrame(first_message))` on `on_client_connected`, guarded by a `greeting_sent` one-shot flag.
- **Never** push `TTSSpeakFrame` directly into `llm`/`tts` stages. **Never** let the LLM generate the opening line live.

## Ringing Tone (Exotel calls only)

`app/voice/ringing_audio.py` — plays a synthesized ringback tone (`assets/ringing_tone_8000.wav`) by writing Exotel-shaped JSON frames directly onto the raw websocket, bypassing pipecat. Looped indefinitely; cancelled and awaited in exactly three places in `run_voice_pipeline`/`_run_pipeline`/`_run_pipeline_inner` before the real transport starts writing.

## 12 Voice Tools

Bound via `build_voice_tools()` factory closure. All results are natural-language strings. `call_session_id`, `property_id`, `host_user_id`, `conversation_state` are never passed by the LLM — bound at construction time.

| Tool | Handler | Key constraint |
|---|---|---|
| `check_calendar` | `handle_check_calendar` | Also locks property in Lead Agent mode |
| `get_pricing` | `handle_get_pricing` | Call with `apply_discounts=false` first; `apply_discounts=true` or `negotiate_rate` only after guest pushback |
| `negotiate_rate` | `handle_negotiate_rate` | Never volunteer a discount unprompted |
| `recommend_properties` | `handle_recommend_properties` | Numbered newline-separated list (not `\|`-joined); Goa region expands to locality names |
| `search_faq` | `handle_search_faq` | Tiered: verified FaqEntry → legacy faq JSON → full property context block |
| `escalate_to_host` | `handle_escalate_to_host` | In-app notification + detached SMTP email + Twilio WhatsApp; arms EscalationPhraseGuard |
| `send_whatsapp` | `handle_send_whatsapp` | Twilio sandbox; 24h customer-service window applies |
| `send_photos` | `handle_send_photos` | Cloudinary gallery link via WhatsApp + email fallback |
| `dispatch_technician` | `handle_dispatch_technician` | Falls back to host notification if no technician on file |
| `update_lead` | `handle_update_lead` | Silent CRM update; call whenever a new field is learned |
| `end_call` | (silence watchdog) | Arms watchdog for graceful hangup after closing line |
| `decline_irrelevant_call` | (silence watchdog) | Arms watchdog for junk/spam call — never touches Lead |

## GOLDEN_RULES (key constraints for the LLM)

Defined in `app/prompts/system_prompt.py`. Critical rules:
- Never invent tool call arguments — always ask for missing dates/guest count
- No markdown, no narrator text, no turn labels, one question per response
- `get_pricing(apply_discounts=false)` before any discount
- After verbal price accept: immediately `update_lead(lead_temperature="hot")` + `escalate_to_host`
- FAQ-first: any property question must go through `search_faq`; only answer from what it returned
- Never say "let me loop in the host" (enforced in code by `EscalationPhraseGuardProcessor`)
- Never quote ₹0 as a price (enforced in code by returning a "no rate, escalate" string)
