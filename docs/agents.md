# Voice Agent Design

Covers the real-time voice pipeline (`backend/app/voice/`) and prompt design (`backend/app/prompts/system_prompt.py`). See [architecture.md](architecture.md) for how a call reaches the pipeline in the first place, and [research-flow.md](research-flow.md) for the pricing/negotiation math the tools call into.

## Two agent modes

Both modes share the same pipeline builder (`_run_pipeline` in `app/voice/pipeline.py`) and the same 9 tools; only the system prompt, first message, and whether `property_id` is fixed differ.

| Mode | Entry point | System prompt builder | Scope |
|---|---|---|---|
| **Guest Support** | one property's `exophone` | `build_system_prompt` | Fixed to one `Property`; `recommend_properties` is explicitly disabled in the prompt (would surface unrelated properties). |
| **Lead Agent** | host's portfolio-wide `User.lead_exophone` | `build_lead_system_prompt` | No property pre-selected; qualifies the guest and recommends across the host's full portfolio via `recommend_properties`, then "locks" onto whichever property the guest shows interest in. |

Both browser-test variants (`run_browser_voice_pipeline`, `run_browser_lead_pipeline`) exercise the identical code path over WebRTC instead of Exotel, using a fixed placeholder identity `BROWSER_TEST_CALLER_NUMBER` for the guest.

### Property "locking" in Lead Agent calls

`app/voice/conversation_state.py` — a plain dataclass, `ConversationState(selected_property_id, selected_property_name)`, tracked programmatically alongside the LLM's own context rather than trusting the model to keep re-supplying a `property_id`. `lock_property()` is called from `check_calendar`, `get_pricing`, and `negotiate_rate`'s tool wrappers whenever they run. `recommend_properties` and `search_faq` both read `state.selected_property_id` as a fallback/guard:
- `recommend_properties` refuses to re-run with no new criteria once a property is locked (returns a natural-language redirect instead of calling the DB).
- `search_faq`'s `faq_property_id` fallback chain is: LLM-supplied arg → `state.selected_property_id` → the call's own fixed `property_id` (Guest Support) → `None` (portfolio-wide).

Guest Support calls never touch this — `property_id` is already fixed before the pipeline is built.

## Pipeline stages

`_run_pipeline` (`app/voice/pipeline.py`) builds a pipecat `Pipeline`:

```
transport.input() → SarvamSTTService → user_aggregator → LLM → SarvamTTSService → transport.output() → assistant_aggregator
```

- **STT**: `SarvamSTTService`, `mode="codemix"` — transcribes Hindi/English/Hinglish as spoken, no translation.
- **LLM**: built by `_build_llm()` — see Groq fallback section below. Function-calling into the 9 tools in `app/voice/tools.py`.
- **TTS**: `SarvamTTSService`, `pace=1.15` (slightly faster than the 1.0 default, tuned for phone-call cadence).
- **Turn strategy**: `SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.9)` by default, or `HybridCompletenessUserTurnStopStrategy` if `TURN_DETECTION_STRATEGY=hybrid_experimental` — see Turn detection below. Deliberately not pipecat's default `LocalSmartTurnAnalyzerV3` (local ONNX inference), which competes for CPU with the real-time audio loop on a dev box.
- **VAD**: `SileroVADAnalyzer(params=VADParams(confidence=0.85, min_volume=0.7))` — raised from pipecat defaults (0.7/0.6) so background noise or a second nearby voice doesn't trigger a false interruption that cuts off in-progress TTS (~1.3s dead air per Sarvam TTS reconnect, confirmed via call logs).
- **Metrics**: `PipelineParams(enable_metrics=True, enable_usage_metrics=True)` — per-stage TTFB and exact prompt/completion token counts logged per call, the basis for real $ cost tracking.

### Bot speaks first

The greeting (`first_message`) is fixed and host-authored (`User.agent_first_message`, templated via `_resolve_template` with `{host_name}`/`{property_name}`/`{city}`/`{guest_name}` placeholders — any placeholder that doesn't apply resolves to `""`). It is pre-seeded into the LLM context as an assistant turn (so the model knows it was already said — the "don't repeat greeting" rule in `GOLDEN_RULES` depends on this), and spoken via `worker.queue_frame(TTSSpeakFrame(first_message))` on the transport's `on_client_connected` event, guarded by a one-shot `greeting_sent` flag (the event can fire more than once per connection). `queue_frame` injects at the pipeline source so it flows through every real stage; pushing directly into `llm`/`tts`, or letting the LLM generate the opening line live, both failed in testing (garbled/hallucinated output, or skipped synthesis).

**Known unresolved issue**: the double-greeting guard stops the same event handler firing twice within one `_run_pipeline` call, but a double greeting was still observed live via the browser test page even with the guard active and confirmed deployed. Root cause not yet found — most likely two genuinely separate `/test/offer` POSTs (two full pipeline runs, each correctly greeting once) rather than one handler firing twice, but this wasn't confirmed against Exotel phone calls specifically. Check `/tmp/*.log` or the live backend's own stdout for two `POST .../test/offer` or `WebSocket ... [accepted]` lines close together before assuming the guard itself is broken.

### Call teardown

`on_pipeline_finished` assembles the transcript from `context.messages` and calls `call_service.finalize_call_session`, then backfills the caller's phone/property onto the `Lead` (`lead_service.backfill_lead`, fills only blank fields, never creates a lead) or deletes a near-empty lead (`lead_service.delete_if_empty`). `on_client_disconnected` calls `worker.cancel()` — without this, a call ending by the browser tab closing (rather than an explicit disconnect click) never reaches `on_pipeline_finished`, leaving the call stuck at `status="in_progress"` forever.

## Tools (`app/voice/tools.py` → `app/services/tool_handlers.py`)

Each tool is a pipecat "direct function" — name/description/parameter schema are extracted from type hints + docstring, no separate JSON schema to maintain. `call_session_id`/`property_id`/`host_user_id`/`conversation_state` are bound via the `build_voice_tools()` factory closure, not passed by the LLM.

| Tool | Purpose | Handler |
|---|---|---|
| `check_calendar` | Check property availability for given dates; also locks the property in Lead Agent calls. | `handle_check_calendar` |
| `get_pricing` | Quote total price. If `Property.exact_airbnb_pricing` is set, fetches this exact listing's live price for the exact requested dates from SearchApi.io instead of computing base+surge+fee+tax; falls back to that math if the live fetch fails. Must be called first with `apply_discounts=false`; only re-called with `apply_discounts=true` after guest pushback. | `handle_get_pricing` (→ `pricing_engine.calculate_price`, see [research-flow.md](research-flow.md)) |
| `dispatch_technician` | Notify a technician for a physical issue (plumbing/electrical/ac/wifi/lock/general); falls back to a host notification if none on file. | `handle_dispatch_technician` |
| `send_whatsapp` | Send a guest/host-facing WhatsApp message (real send via Twilio Sandbox — see below; also logged to `Notification`). | `handle_send_whatsapp` |
| `send_photos` | Send the guest a link to a property's photo gallery (one link, not individual images) over WhatsApp + a host-inbox email fallback. | `handle_send_photos` |
| `escalate_to_host` | Escalate to the host (in-app notification + fire-and-forget email); also upserts whatever lead data it already has. | `handle_escalate_to_host` |
| `negotiate_rate` | Compute a floor price and accept/counter a guest's offer. | `handle_negotiate_rate` (→ `pricing_engine.negotiate_rate`) |
| `recommend_properties` | Recommend up to 3 properties from the host's portfolio matching budget/guests/location/purpose. Guarded against redundant re-calls once a property is locked (Lead Agent only). | `handle_recommend_properties` |
| `update_lead` | Silently save/update the guest's CRM `Lead` record — called whenever any new field is learned. | `handle_update_lead` |
| `search_faq` | Tiered fallback: verified `FaqEntry` rows → legacy `Property.faq` JSON → (if a property is known) `faq_service.full_property_context()`, every on-file fact for that property in one block (house rules, amenities, neighborhood info, check-in/out, seasonal notes) for the model to read the actual answer out of. Logs a gap (`UnansweredQuestion`) only if the property itself is unknown — see the "Known gap" note in [database.md](database.md#unanswered_questions-unansweredquestion-appmodelsunanswered_questionpy). | `handle_search_faq` |

All handlers return a natural-language string — this is fed back to the LLM as the tool result and is often read back near-verbatim to the guest, so results are phrased for speech, not JSON. `ValidationError` on tool args is caught in `app/voice/tools.py` and returns a fixed re-ask string (`INVALID_ARGS_MESSAGE`) instead of erroring the turn.

### Host/guest notifications (in-app + email + Twilio WhatsApp sandbox)

`escalate_to_host`/`send_whatsapp`/`send_photos` all write to the `Notification` table (`app/services/notification_service.py`), which is what the dashboard's Live Requests feed polls/streams — this is a record of what was sent, not the delivery mechanism itself.

Real WhatsApp delivery goes through Twilio's Sandbox (`app/integrations/twilio_client.py`, `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`/`TWILIO_WHATSAPP_FROM`), not a Meta-approved WhatsApp Business number — Exotel's WhatsApp Business API requires Facebook Business Manager ID + Exotel KYC/Meta approval before any sending number exists, so Twilio's instant shared sandbox number stands in for now. The sandbox's one real constraint: it can only message numbers that have first texted "join `<code>`" to the sandbox number from WhatsApp — fine for testing against the host's own phone, not usable for arbitrary real guests until upgraded to a real WhatsApp Business number. `TWILIO_*` unset = falls back to the in-app notification only, same pattern as every other optional integration here.

`escalate_to_host` also WhatsApps the host directly (`User.phone`, separate from the guest-facing sandbox constraint above — the host is the one who joins the sandbox for this). Uses a `twilio/call-to-action` Content Template (`TWILIO_ESCALATION_TEMPLATE_SID`, created once via `scripts/create_escalation_template.py`) so the message renders a real "Go to Dashboard" **button** instead of a raw URL — WhatsApp auto-linkifies (and generates an ugly link-preview card for) any bare URL in plain text, and there's no per-message API flag to suppress that; a Content Template button is the only way around it. Falls back to a plain-text message (with a bare URL) if `TWILIO_ESCALATION_TEMPLATE_SID` isn't set. Message body uses an emoji keyed off urgency (`_URGENCY_EMOJI` in `tool_handlers.py`) plus bold labeled fields (`*Property:*`, `*Issue:*`, etc.) — plain prose was found to be unreadable when a host has several escalations stacked in one WhatsApp thread.

`handle_escalate_to_host` and `handle_send_photos` additionally fire an email (`app/integrations/email_client.py`, plain SMTP, any provider) via `asyncio.create_task` — not awaited, so a slow/misconfigured SMTP server never adds latency to the live tool call. `SMTP_*` unset = skipped silently, same pattern as `BRIGHT_DATA_API_KEY`. `send_whatsapp` itself has no email fallback — that hook is specific to `escalate_to_host`'s host-facing summary and `send_photos`'s gallery link.

## GOLDEN_RULES (`app/prompts/system_prompt.py`)

A fixed block of prompt rules injected into both `GUEST_SUPPORT_INSTRUCTIONS` and `LEAD_AGENT_INSTRUCTIONS`, layered underneath host customization (`agent_persona`, `agent_first_message`, `agent_escalation_phrase`) so a host can personalize tone without disabling a safety rail. Key constraints:

- **Pricing order**: always quote `get_pricing(apply_discounts=false)` first; only call again with `apply_discounts=true` (or `negotiate_rate` if the guest names an offer) after the guest pushes back. Never volunteer a discount unprompted.
- **Competitor comparisons**: if the guest compares price to Booking.com/MMT/Agoda or asks for a discount in English/Hindi/Hinglish, never invent a match — route through the pricing-order rule above.
- **Occasion handling**: record exactly what the guest said about a birthday/anniversary/honeymoon/etc. via `update_lead`'s `occasion`/`conversation_summary` fields; never invent host-facing suggestions ("consider offering a cake" is explicitly banned).
- **Escalation-after-verbal-accept**: the moment a guest verbally accepts a price and wants to proceed, that's a booking request requiring host approval — there is no tool that finalizes a booking. The prompt requires an immediate `update_lead(lead_temperature="hot", ...)` followed by `escalate_to_host`; a verbal "I'll lock that in" with no backend calls behind it means the host never finds out (step 7 of the Lead Agent workflow).
- **Voice-specific formatting**: no markdown, one question per response, one response per turn (never simulate the guest's side or write any turn label like `"Guest:"`/`"User says"`/`"User:"` in any position — a real, confirmed live-call failure mode, not hypothetical), concise (1-2 sentences unless reciting a requested list), never repeat the greeting or any prior sentence verbatim, no filler on interruption.
- **Dates**: resolved via a pre-computed "today anchor" (`_today_anchor()`) rather than trusting the LLM's own weekday arithmetic. `"This weekend"` and `"next weekend"` are two genuinely different weekends (not the same dates — a real bug this session), and a resolved date is always spoken back naturally ("18th of July"), never as raw `YYYY-MM-DD`.
- **Don't re-ask known info**: if the guest already gave their name/phone earlier in the same call, use it — never ask again or act like it's unknown.
- **FAQ-first**: any property/support question must go through `search_faq`. It may return a verified FAQ answer or the property's full on-file details (see the `search_faq` tool row above) — either way, only answer with what's actually in what it returned; if the specific thing asked isn't actually present, that still counts as "no verified information": say so and escalate, never answer from memory/guesswork or loosely infer from unrelated details in the result.

## Groq multi-model fallback

Config: `GROQ_MODEL` (`openai/gpt-oss-120b`, the configured default/first choice) and `GROQ_MODELS` (JSON array string, default `["openai/gpt-oss-120b", "llama-3.1-8b-instant", "openai/gpt-oss-20b"]`) — the fallback chain `_build_llm()` actually walks.

- **Health checks**: `_check_llm_health` in `app/main.py` runs every 60s (plus once at startup), pinging each Groq model with a 1-token request. Success/failure (with latency) is stored in the module-level `llm_health` dict. This does double duty: keeps the route warm (avoids Render cold-start latency) and marks a model down on a 429 (free-tier tokens-per-minute cap).
- **Model selection**: `_pick_groq_model()` in `pipeline.py` returns the first model in `settings.groq_models` not marked down in `llm_health` (falls back to the first model if health data isn't populated yet, e.g. cold start).
- **Live 429 handling**: `_FallbackGroqLLMService` (subclass of pipecat's `GroqLLMService`) overrides `create_client` to set `max_retries=0` on the underlying `AsyncOpenAI` client (otherwise the SDK's own default retry/backoff would blindly re-hit the same rate-limited model before this fallback gets a chance) and overrides `get_chat_completions` to retry the *next* model in `settings.groq_models` immediately, in the same call turn, on a `RateLimitError` — rather than waiting for the next 60s health-check pass. It starts from whichever model `_build_llm` already picked, not always `groq_models[0]`.
- **`reasoning_effort: "low"`**: applied only when `"gpt-oss" in model` (other models like `llama-3.1-8b-instant` 400 on this param). Disables gpt-oss's hidden chain-of-thought pass — a real source of multi-second latency on a live call. Applied in three places: `_check_llm_health`, `_build_llm`/`_FallbackGroqLLMService.get_chat_completions`, and `_build_openrouter_llm` (same param, since `reasoning_effort` is a property of the gpt-oss model itself, not Groq's hosting of it).
- **Last resort**: if every model in `settings.groq_models` is marked down in `llm_health`, `_build_llm()` falls through to OpenRouter (`_build_openrouter_llm`, `OpenAILLMService` pointed at `https://openrouter.ai/api/v1`, `max_completion_tokens=900` — capped because OpenRouter's free-tier credit check rejects based on the *requested* ceiling, not actual usage).
- **Exposed at**: `GET /api/v1/health/llm` — read-only snapshot of `llm_health`, for a dashboard widget.

## Turn detection strategies

Config: `TURN_DETECTION_STRATEGY` (`vad_fixed` default / `hybrid_experimental`) — experimental, `shagun` branch only, deliberately not in `render.yaml`; `main`/production is unaffected regardless of this setting.

- **`vad_fixed`** (production default): `SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.9)`. 0.9s is the validated middle ground — 0.6s cut guests off mid-thought, 1.4s added ~1.4s of dead air per turn.
- **`hybrid_experimental`**: `app/voice/turn_strategies.HybridCompletenessUserTurnStopStrategy(base_timeout=0.9)`. Runs a fast, local, non-LLM heuristic (`_is_incomplete`) on the accumulated transcript when the base 0.9s timeout fires: trailing conjunction ("and", "but", "aur", "और", determiners/prepositions like "the"/"in"/"that"), trailing comma, or a short (<3 words) unpunctuated utterance → extend the wait once by `extension_timeout=0.7`s, up to a hard `max_wait=2.8`s cap measured from the original VAD-stop moment. Falls back to identical behavior to `vad_fixed` if the transcript looks complete. No spoken filler is used during the extension — an earlier version tried `TTSSpeakFrame` push from the strategy itself, but confirmed live that this isn't ordered against the main LLM→TTS flow and could corrupt an in-progress reply.
  - **Known gotcha, already fixed**: pipecat's transcript can arrive *before* `VADUserStoppedSpeakingFrame`. Without an explicit fallback (`_handle_transcription`'s branch for `not self._vad_user_speaking and self._vad_stopped_at is None`), the strategy's timer chain never starts, and the turn only ends via pipecat's generic ~5s stuck-turn watchdog (`strategy: None` in logs), not the intended adaptive behavior.
  - `[DEBUGTURN]`-prefixed debug logging is intentionally still present for live verification; safe to strip once confirmed working end-to-end on a real call. Grep the backend log for `strategy:` to see which class actually fired for a given turn.

## VAD / TTS tuning constants (summary)

| Constant | Value | Where |
|---|---|---|
| VAD confidence / min_volume | 0.85 / 0.7 | `_VAD_PARAMS` in `pipeline.py` |
| Turn-stop timeout (vad_fixed) | 0.9s | `SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.9)` |
| Turn-stop base/extension/cap (hybrid) | 0.9s / 0.7s / 2.8s | `HybridCompletenessUserTurnStopStrategy` |
| TTS pace | 1.15 | `SarvamTTSService.Settings(pace=1.15)` |
| OpenRouter max_completion_tokens | 900 | `_build_openrouter_llm` |
