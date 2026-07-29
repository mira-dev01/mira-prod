# How MIRA Actually Works — A Function-Level Deep Dive

This doc exists for one purpose: so you can trace *exactly* what code runs, in what order, for a real thing that happens on a call — not just "the architecture," but the actual function calls, file by file. The other docs in `docs/` are the stable reference (schema, API surface, high-level design); this one is the narrative walkthrough.

Read it in this order:
1. **Trace A** — a guest asks for properties in a specific place (the most common thing that happens on a call).
2. **Trace B** — the agent escalates something to the host.
3. **Logging & observability** — everywhere anything gets written down, and why.
4. **Guardrails** — every code-level safety net in the pipeline, and every prompt-level rule, mechanically explained.
5. **File-by-file reference** — every file in `backend/app/`, what it's for, and its key functions.

Cross-references: [architecture.md](architecture.md) (deployment/infra), [agents.md](agents.md) (pipeline/prompt design — this doc assumes you've skimmed it), [database.md](database.md) (schema), [research-flow.md](research-flow.md) (pricing math), [api.md](api.md) (REST surface).

---

## Trace A: "Do you have anything in South Goa?"

This is the `recommend_properties` path — a Lead Agent call (portfolio-wide, not tied to one property). Every step below is a real function, in call order.

### 1. The call arrives (before any of this)

- Exotel's Voicebot Applet opens a WebSocket to `POST/WS /api/v1/voice/exotel/ws/{token}` — routed in `app/api/v1/voice.py`, handler at line ~105.
- `parse_telephony_websocket` (pipecat) reads the initial Exotel "start" event off the socket and builds a `CallData` (call ID, stream ID, dialed number, caller number).
- `run_voice_pipeline(websocket, call_data)` (`app/voice/pipeline.py:775`) takes over from here.
- **Ring tone starts immediately**: `asyncio.create_task(play_ringing_tone(...))` (`app/voice/ringing_audio.py`) loops a synthesized ringback tone directly onto the raw websocket — there's no pipeline yet to push a frame into, and the guest would otherwise hear dead air for the ~4-6s the next steps take.
- Inside `run_voice_pipeline`, still before any pipeline exists:
  - `call_service.get_property_by_number(db, dialed_number)` — is this dialed number a specific property's `exophone`? If yes, this is a **Guest Support** call.
  - If not, `call_service.get_user_by_lead_number(db, dialed_number)` — is it a host's portfolio-wide `lead_exophone`? If yes, this is a **Lead Agent** call (our case — the guest asked about "South Goa," not one specific property).
  - `call_service.get_or_create_guest_profile(db, caller_number, host_user_id)` — resolves/creates the `GuestProfile` row for this phone number, host-scoped (see [database.md](database.md)).
  - `lead_service.get_active_booking(...)` — checks for an existing `status="booked"` `Lead` for this guest, so the system prompt can mention "you already have a confirmed booking" if relevant.
  - `call_service.get_or_create_call_session(...)` — creates the `CallSession` row (this is the row that becomes a "Call" in the dashboard).
  - `build_lead_system_prompt(lead_user, properties, guest, active_booking, caller_phone=caller_number)` (`app/prompts/system_prompt.py`) — builds the full system prompt: `LEAD_AGENT_INSTRUCTIONS` + `GOLDEN_RULES` + persona/escalation-phrase customization + guest-memory section + active-booking section + the full property portfolio list (name, `property_id`, city, price, guest capacity — see the delimiter-collision fix in the file-reference section below for why `property_id` matters here).
  - `lead_first_message_for(lead_user)` — the fixed, host-authored greeting text.
- `_run_pipeline(...)` (`pipeline.py:385`) builds the actual pipecat `Pipeline` — STT/TTS services, the six guard processors, tools, turn-detection strategy — and hands the transport the same websocket. Right before `runner.run()` starts consuming it, the ring-tone task is cancelled and awaited (so there's never a window where two things write to the same socket).

### 2. The guest speaks

- Audio arrives on `transport.input()`, flows to `_ReconnectingSarvamSTTService` (`pipeline.py:219`, a thin subclass of pipecat's `SarvamSTTService` that reconnects on a dead-websocket error instead of dying silently for the rest of the call).
- STT emits `TranscriptionFrame`s. `SilenceWatchdogProcessor` (`app/voice/silence_watchdog.py`) sees every one — a real (non-blank) transcript resets its silence timer and cancels any pending hangup.
- `LanguageSyncProcessor` (`app/voice/language_sync.py`) watches the detected language per transcript; a language change pushes a `TTSUpdateSettingsFrame` so the eventual reply is spoken in the right voice.
- `user_aggregator` (pipecat's `LLMUserAggregatorParams`, using `SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.9)`) decides when the guest has actually finished their turn (0.9s of silence after speech, tuned from real-call evidence — see [agents.md](agents.md)) and pushes an `LLMContextFrame` with the updated message list.
- `RedundantContextGuardProcessor` (`app/voice/redundant_context_guard.py`) checks: is this genuinely a new context (more messages than last time), or a spurious re-fire from pipecat's own deferred-push machinery? If spurious, drops it — the LLM never sees it, no wasted completion.

### 3. The LLM decides to call `recommend_properties`

- The `llm` stage (`_build_llm()` — see the Groq section of [agents.md](agents.md)) gets the context, including all 12 tool schemas (`app/voice/tools.py`), and decides to call `recommend_properties(preferred_location="South Goa")` based on `LEAD_AGENT_INSTRUCTIONS`' workflow rules in the system prompt.
- pipecat's function-calling machinery (`llm_service.py`, not app code) pushes a `FunctionCallsStartedFrame` naming `recommend_properties` downstream through the pipeline, then actually runs the tool.

### 4. The tool itself runs

- `app/voice/tools.py`'s `recommend_properties` closure (bound to `host_user_id` etc. via `build_voice_tools`) runs:
  - First checks `state.selected_property_id` (`ConversationState`, `app/voice/conversation_state.py`) — is a property already "locked" for this call with no new criteria given? Not the case here (`preferred_location` is new information), so it proceeds.
  - Opens a fresh `AsyncSessionLocal()` and calls `tool_handlers.handle_recommend_properties(db, args, host_user_id)`.
- `handle_recommend_properties` (`app/services/tool_handlers.py:582`):
  - Builds a `SELECT` on `Property` scoped to `host_user_id`.
  - **Location filtering**: `_goa_region_localities("South Goa")` recognizes this as a Goa-region query and returns `_GOA_SOUTH_LOCALITIES` (Colva, Margao, Palolem, etc.). The `WHERE` clause matches `Property.city`/`Property.neighborhood_info` against those actual locality names — **not** the literal phrase "South Goa" against free text (that used to false-positive on North Goa properties whose `neighborhood_info` happens to mention "Dabolim (South Goa airport)" as a travel-time reference — fixed 2026-07-27, see `project_state.md`).
  - Orders by `base_price ASC`, limits to 3. If a `num_guests` filter would return zero but the base filter wouldn't, falls back to smaller units and adds a `combo_note` suggesting the guest book two together.
  - Formats the result: a numbered, **newline**-separated list (not `" | "`-joined — real imported property names can contain a literal `|`, and joining/splitting on that used to tear names apart; fixed the same day as the location-filter bug). Each line: `"N. {name} in {city}: ₹{price}/night, sleeps {guests}, {amenities}{usp} (property_id: {uuid})"`.
  - Returns that whole string as the tool result.
- Back in `tools.py`: before calling `params.result_callback(result)`, the wrapper calls `property_recommendation_guard.record_tool_result("recommend_properties", result)` — stashing the parsed property names/prices on the guard *before* the callback triggers the next LLM completion (see Guardrails section for why this ordering matters, not a race).
- `result_callback(result)` appends this as a `tool` role message to the context and triggers the LLM's next completion — the reply that will actually narrate the options to the guest.

### 5. The reply gets guarded before it's ever spoken

The next completion's frames flow: `llm → repetition_guard → meta_commentary_guard → property_recommendation_guard → escalation_guard → premature_end_call_guard → tts`. For this turn, `property_recommendation_guard` is the one that matters:
- It's armed (from the `FunctionCallsStartedFrame` in step 3) for exactly this one response.
- It buffers the response's text, then:
  - Strips any `property_id` UUID that leaked into the spoken text (regex, `strip_property_ids`).
  - Checks whether the reply actually names at least one of the properties from the parsed tool result. If yes, passes the (ID-stripped) text through as-is. If **no** — the model reacted without naming anything, a confirmed recurring failure — it **overrides the entire reply** with a guaranteed-correct line built directly from the parsed options (`_fallback_recommendation_text`).
- `repetition_guard` (upstream of this) would have already cut the response short if it detected the same clarifying-question pattern repeating within this turn; `meta_commentary_guard` would have stripped any `"(Waiting for guest response)"`-style aside.

### 6. TTS speaks it, the guest hears it

- `SarvamTTSService` synthesizes the (now-guarded) text, `pace=1.15`, in whatever language `LanguageSyncProcessor` last set.
- Audio flows out `transport.output()` back through Exotel to the guest's phone.
- `assistant_aggregator` appends the spoken text back into the LLM context as an `assistant` message, so the model's own memory of the call matches what was actually said (not what it originally generated before guarding, if those differed).

### 7. What gets written down (jumps ahead to teardown — see Logging section for the full picture)

Nothing is persisted mid-call from this specific tool call except the in-memory `ConversationState`/LLM context. The `Lead` row (with `properties_discussed`, etc.) only gets written when `update_lead` is called (usually right after, per `LEAD_AGENT_INSTRUCTIONS`) or backfilled at call end.

---

## Trace B: Escalating to the host

Say the guest verbally agrees to book, or has an issue only the host can resolve.

### 1. The LLM decides to escalate

`LEAD_AGENT_INSTRUCTIONS`/`GOLDEN_RULES` in `system_prompt.py` require: the moment a guest verbally accepts a price and wants to proceed, `update_lead(lead_temperature="hot", ...)` then `escalate_to_host` in the same turn — there is no tool that "finalizes" a booking; a host must confirm. The LLM calls both tools.

### 2. `update_lead` runs first

`app/voice/tools.py`'s `update_lead` closure → `tool_handlers.handle_update_lead` → `lead_service.upsert_lead(db, host_user_id, call_session_id, guest_profile_id=..., **fields)` (`app/services/lead_service.py:100`) — finds or creates the `Lead` row for this `call_session_id` and applies only the non-`None` fields passed. This can be called many times across a call; each call only ever adds information, never blanks something already known.

### 3. `escalate_to_host` runs

- `tools.py`'s `escalate_to_host` closure → `tool_handlers.handle_escalate_to_host(db, args, call_session_id, host_user_id, guest_profile_id=...)`:
  - Resolves the `Property` (`_get_property`).
  - Builds a plain message string: `f"Escalation for {property_.name}: {args.reason}"` plus summary/guest-phone if given.
  - `notification_service.create_notification(db, channel="escalation", property_id=..., call_session_id=..., urgency=args.urgency, message=...)` — writes the `Notification` row. **This is what the dashboard's Live Requests feed actually reads** — polling/streaming this table, not re-deriving anything from the call live.
  - `lead_service.upsert_lead(...)` again — captures `escalated=True` and whatever phone/summary this specific call already has, so an escalated call is never left with an empty CRM lead even if the model only made this one call and skipped `update_lead`.
  - Fires two **detached** (`asyncio.create_task`, not awaited) side effects, so neither can add latency to the live call turn:
    - **WhatsApp to the host**: `_send_escalation_whatsapp` → `app/integrations/twilio_client.py`'s `send_whatsapp_template` (if `TWILIO_ESCALATION_TEMPLATE_SID` is set — a pre-created `twilio/call-to-action` Content Template with a real "Go to Dashboard" button) or `send_whatsapp_message` (plain text fallback). Twilio Sandbox constraints apply — see [agents.md](agents.md)'s notifications section.
    - **Email to the host**: `_send_escalation_email` → `app/integrations/email_client.py`'s `send_email`, HTML body from `email_templates.build_escalation_email_html`. Currently broken on Railway's Trial tier (SMTP port 587 blocked) — see [agents.md](agents.md).
- Returns a natural-language string to the LLM — this is what the model reads back to the guest, which is why `escalation_phrase_guard` exists (see Guardrails): what the model *tries* to say next isn't trusted, it's unconditionally replaced.

### 4. The reply gets guarded

`escalation_guard` (armed by the same `FunctionCallsStartedFrame` naming `escalate_to_host`) buffers the next response and **unconditionally replaces it** with a fixed safe line, regardless of what the model generated — see Guardrails section for why this is unconditional, not pattern-matched.

### 5. What the host actually sees

- **Dashboard "Live Requests"**: reads `Notification` rows (`GET /notifications`, `app/api/v1/notifications.py`), filtered to the host's own properties.
- **WhatsApp**: a message on their phone (if Twilio Sandbox constraints are satisfied) with a "Go to Dashboard" button (or bare link if the template isn't configured).
- **Email**: an inbox message (if SMTP isn't blocked at the platform level).
- **Leads Kanban**: the `Lead` row now has `escalated=True` and shows up in whichever status column it's in — a host manually drags it to `"booked"`/`"closed"` (`PATCH /leads/{id}`, the *only* place `Lead.status` is ever set — the voice agent never touches it).

---

## Logging & observability — everywhere anything gets recorded

There is no single "log" — there are several distinct, purpose-built recording mechanisms. Here's the complete map.

### 1. Structured application logs (`loguru`/stdlib `logging`)

Every file above uses `logger.info`/`logger.warning`/`logger.debug`/`logger.exception` liberally, and Railway captures stdout/stderr as the deployment's log stream (`railway logs`). Notable, intentional logging choices:
- **`enable_metrics=True, enable_usage_metrics=True`** on `PipelineWorker` (`pipeline.py`) — pipecat itself logs per-stage TTFB (`"<stage> TTFB: N.NNNs"`) and exact prompt/completion token counts per LLM call (`"<stage> prompt tokens: X, completion tokens: Y"`) for every single completion. This is the actual, verified $-cost basis, not an estimate.
- **`_check_llm_health`** (`app/main.py`, every 60s) logs `"LLM health OK (groq/<model>, <latency>s)"` or the failure, and the `_FallbackGroqLLMService`'s live-429 retry logs `"Groq model %s hit a live 429 mid-call, trying next in chain"` — this is how you trace which provider/model actually handled a given completion (see the CLAUDE.md pitfall about not inferring this from config alone).
- **`get_chat_completions`** (`app/services/openai/base_llm.py`, pipecat, not app code) logs `"Generating chat from context [...]"` with the full message list — the single most useful line for reconstructing exactly what the model saw for any given turn, by timestamp.
- The `[DEBUGTURN]`-prefixed lines in `turn_strategies.py` (experimental `hybrid_experimental` strategy only) and `strategy: <ClassName>`/`strategy: None` in pipecat's own turn-stop logging — `strategy: None` specifically means the generic ~5s stuck-turn watchdog fired, not any registered strategy (a real signal, not noise).

### 2. Per-call structured records (the DB)

- **`CallSession`** (`app/models/call_session.py`) — one row per call, created at call start (`call_service.get_or_create_call_session`), finalized at call end (`call_service.finalize_call_session`, sets `status`/`ended_at`/`duration_seconds`/`transcript`). `transcript` is the full `role: content` text, assembled from `context.messages` inside `on_pipeline_finished` (`pipeline.py:615`) — tool-call turns (`content=None`) are skipped.
- **`CallSession.call_type`** — set once, end-of-call, by `call_classification_service.classify_call(transcript, duration_seconds)` (a separate one-shot LLM call, Groq→Anthropic→OpenRouter fallback, never built on the streaming voice pipeline). This is the single exhaustive signal request_feed_service and the dashboard's classification-dependent views key off — see `app/services/call_classification_service.py`.
- **`CallSession.ai_summary`** (JSONB) — set by `call_summary_service.summarize_call`, same one-shot-after-call-ends shape, structured `CallSummary` (booking snapshot, 3-5 sentence summary, outcome, host actions, key details, missing information) — rendered on the dashboard's Call Details page instead of a plain paragraph.
- **`Lead`** (`app/models/lead.py`) — the CRM record. Written mid-call by `update_lead`/`escalate_to_host` (`lead_service.upsert_lead`), backfilled at call end (phone, property name, guest name — only blank fields, never overwrites), deleted if empty (`delete_if_empty`) or if the call was ultimately classified as non-qualifying (`delete_for_unqualified_call`) — the end-of-call classification overrides whatever live tool calls did, since the full-transcript review is more informed than in-call judgment.
- **`Notification`** (`app/models/notification.py`) — written by `notification_service.create_notification` from `escalate_to_host`/`send_whatsapp`/`send_photos`. This is a record of *what was sent*, not the delivery mechanism itself — the dashboard's Live Requests feed reads only this table.
- **`GuestProfile`** (`app/models/guest_profile.py`) — cross-call guest memory (host-scoped by phone), updated fire-and-forget after call teardown by `guest_memory_service.update_guest_memory_from_call` — aggregates the `Lead.conversation_summary` the agent already wrote during the call; never calls an LLM itself.
- **`UnansweredQuestion`** (`app/models/unanswered_question.py`) — logged by `search_faq`'s tiered fallback (`faq_service`) only when the property itself is unknown (a known, documented gap — see [database.md](database.md)); feeds the FAQ Learning Engine dashboard tab.
- **`PricingRule`/`HostDiscountRule`** — not call logs, but host-configured logic read at pricing/negotiation time (see [research-flow.md](research-flow.md)).

### 3. Live metrics/health endpoints

- `GET /api/v1/health/llm` — read-only snapshot of the module-level `llm_health` dict, refreshed every 60s by `_check_llm_health`.
- `GET /health` — DB connectivity check (`_check_db_health`, also runs every 3 min as a Neon-cold-start-avoidance keepalive).

### 4. What is *not* logged anywhere (by design)

- Raw audio is never persisted — only the derived text transcript.
- Guardrail interventions (a repetition cut, a stripped meta-comment, a replaced escalation line) are **not** written to any DB table — they only show up in the application log stream (loguru) if the guard itself logs a warning, and in the `CallSession.transcript` as the *post-guard* text (since the transcript is assembled from the aggregator's context, which reflects what was actually spoken, not the model's raw un-guarded generation). If you need to know a guard fired on a specific real call, the Railway log stream for that call's timestamp window is the only source — there's no "guard intervention" table.

---

## Guardrails — every safety net, mechanically

Two categories: **code-level** (deterministic, in the pipeline, can't be talked around) and **prompt-level** (`GOLDEN_RULES` in `system_prompt.py` — the model is instructed, but instruction-following isn't 100%, which is why the code-level guards exist for the failure modes that recurred anyway).

### Code-level guards (pipeline order: `redundant_context_guard → llm → repetition_guard → meta_commentary_guard → property_recommendation_guard → escalation_guard → premature_end_call_guard → tts`)

| Guard | File | Triggers on | What it does |
|---|---|---|---|
| `RedundantContextGuardProcessor` | `redundant_context_guard.py` | Every `LLMContextFrame`, before the LLM | Drops a spurious re-fire (same message count as last time) from pipecat's own deferred context-push — prevents a wasted/duplicate completion that could end a call with no real guest reply in between. |
| `RepetitionGuardProcessor` | `repetition_guard.py` | Every LLM response, streaming | Passes text through immediately (zero latency) by default. Tracks sentences within the current response; cuts the rest of the response, silently, the moment it detects a near-duplicate sentence (≥60% word overlap with something already said this turn) or a flood of degenerate short fragments. Pairs with `max_completion_tokens=400` on the Groq LLM settings, which bounds generation length but doesn't by itself guarantee no repeats. |
| `MetaCommentaryGuardProcessor` | `meta_commentary_guard.py` | Every LLM response, streaming | Passes text through by default; only holds text back while inside an open `(...)`, and drops the span if it matches narrator/stage-direction language (waiting/listening/pause/thinking/etc.) — e.g. `"(Waiting for guest response)"`. Legitimate parentheticals pass through untouched. |
| `PropertyRecommendationGuardProcessor` | `property_recommendation_guard.py` | `FunctionCallsStartedFrame` naming `recommend_properties`/`get_pricing`/`check_calendar`/`negotiate_rate`/`search_faq`/`send_photos`/`dispatch_technician` | For the one response right after: strips any leaked `property_id` UUID from the text; for `recommend_properties` specifically, overrides the whole reply with a guaranteed-correct line (built from the tool's own real result) if the model's reply never actually named a property. |
| `EscalationPhraseGuardProcessor` | `escalation_phrase_guard.py` | `FunctionCallsStartedFrame` naming `escalate_to_host` | **Unconditionally replaces** the entire first reply after with a fixed safe line, regardless of what the model said — no phrase-detection step, so no phrasing variant can slip through. |
| `PrematureEndCallGuardProcessor` | `premature_end_call_guard.py` | `FunctionCallsStartedFrame` naming `end_call` | If that same turn both calls `end_call` and asks a real question (a `"?"` anywhere in the text), calls `silence_watchdog.cancel_end_request()` so the call falls through to the normal silence-nudge path instead of hanging up before the guest can answer. Never rewrites the text. |
| `SilenceWatchdogProcessor` | `silence_watchdog.py` | Every turn (sits earlier, right after STT) | Nudges a silent guest, hangs up after two unanswered nudges; also the actual hangup mechanism for `end_call`/`decline_irrelevant_call` (they arm it via `request_end_after_current_turn()` rather than ending the call directly, since neither tool knows whether TTS has finished the preceding line yet). |

**Non-pipeline (data-validation) guards**, in `app/services/tool_handlers.py`:
- **₹0 price guard**: `handle_get_pricing`/`handle_negotiate_rate` refuse to return any non-positive total as if it were a real quote — returns a directive to say no number and escalate instead.
- **Phone-number sanity check**: `_phone_confirmation_warning` appends a warning to the tool result whenever a captured phone number isn't exactly 10 digits (`update_lead`/`send_whatsapp`/`send_photos`), catching a misheard/truncated number before it's used to actually contact someone.

### Prompt-level rules (`GOLDEN_RULES`, `app/prompts/system_prompt.py`)

Injected into both `GUEST_SUPPORT_INSTRUCTIONS` and `LEAD_AGENT_INSTRUCTIONS`, underneath host customization (persona/first-message/escalation-phrase overrides — a host can personalize tone without disabling a safety rail). Full current list, see [agents.md](agents.md) for the complete text of each: never invent tool-call arguments; no narrator/meta text; mid-call "hello" never repeats the last answer; never re-ask something the guest already gave (including their immediately preceding message); pricing order (`apply_discounts=false` first); never invent a competitor-match discount; occasion handling never invents host-facing suggestions; escalation-after-verbal-accept; voice-specific formatting (no markdown, one question per turn, no simulated dialogue); dates resolved via a pre-computed anchor, never raw ISO; filler-only turns don't trigger a re-ask; speak tool results before reacting to them; the "loop in the host" ban; never quote ₹0.

**Why some rules also have a code backstop and others don't**: a rule gets a code-level guard specifically when it recurred live *despite* the prompt rule existing — the guard isn't a replacement for the rule, it's evidence the rule alone wasn't sufficient for that specific failure shape. Rules with no code backstop haven't (yet) shown that pattern.

---

## File-by-file reference (`backend/app/`)

### `app/api/v1/` — REST endpoints, one file per domain (all require auth except webhooks)

| File | Covers |
|---|---|
| `auth.py` | Clerk-backed auth (`/auth/me`, profile updates — the old demo `/auth/login` JWT mint is gone). |
| `properties.py` | CRUD + Airbnb import (both paths — see `research-flow.md`) + `_upsert_property_from_parsed` (shared convergence point) + gallery endpoint. |
| `bookings.py` | `Booking` CRUD, read-mostly (no payment/price columns yet — see `database.md`). |
| `calls.py` | Call list/detail for the dashboard's Calls page. |
| `leads.py` | Lead list/detail/`PATCH` — the only place `Lead.status` is set. |
| `faq.py` | Host-managed FAQ CRUD, verification, gap review (`FaqGap`/`UnansweredQuestion`). |
| `guests.py` | Guest Memory read endpoints for the dashboard's Guests page. |
| `host_discount_rules.py` | Approve/edit `HostDiscountRule` drafts (see `research-flow.md`'s discount-policy section). |
| `notifications.py` | List/mark-read `Notification` rows; also the SSE/poll stream backing Live Requests. |
| `pricing.py` | `PricingRule` CRUD (`length_of_stay` only actually read) + `/pricing/quote` (direct `calculate_price` call, backs the dashboard's Quote calculator). |
| `technicians.py` | `Technician` CRUD. |
| `voice.py` | Exotel WS endpoint, browser-test WebRTC offer endpoint, `/transcribe` (Guest Memory answer-audio transcription support). |
| `webhooks/exotel.py` | `exotel_call_status` — Exotel's call-status callback (not the audio WS; a separate lightweight status ping). |
| `common.py` | Shared `DateRange` parsing used across several list endpoints. |
| `analytics.py` | Dashboard stat cards/timeseries — aggregates over `CallSession`/`Lead`/`Notification`. |

### `app/auth/`

- `dependencies.py` — FastAPI dependency that validates a Clerk session and resolves the current `User`.

### `app/voice/` — the real-time pipeline (see Traces A/B above for how these compose)

| File | Role |
|---|---|
| `pipeline.py` | `_run_pipeline`/`_run_pipeline_inner` build the pipecat `Pipeline`; `run_voice_pipeline`/`run_browser_*_pipeline` are the entry points; `_build_llm`/`_build_openrouter_llm`/`_FallbackGroqLLMService`/`_pick_groq_model` are the Groq fallback chain (see `agents.md`). |
| `tools.py` | `build_voice_tools()` — the 12 pipecat "direct functions" the LLM can call, closures bound to `call_session_id`/`property_id`/`host_user_id`/`conversation_state`/`silence_watchdog`/`property_recommendation_guard`. Delegates all real logic to `app/services/tool_handlers.py`. |
| `conversation_state.py` | `ConversationState` — tracks which property is "locked" in a Lead Agent call, programmatically, alongside the LLM's own context. |
| `silence_watchdog.py` | See Guardrails table above. |
| `escalation_phrase_guard.py` | See Guardrails table above. |
| `repetition_guard.py` | See Guardrails table above. |
| `meta_commentary_guard.py` | See Guardrails table above. |
| `property_recommendation_guard.py` | See Guardrails table above. |
| `premature_end_call_guard.py` | See Guardrails table above. |
| `redundant_context_guard.py` | See Guardrails table above. |
| `language_sync.py` | `LanguageSyncProcessor` — switches live TTS language on detected-language change. |
| `turn_strategies.py` | `HybridCompletenessUserTurnStopStrategy` — experimental, local-only turn-detection alternative (`TURN_DETECTION_STRATEGY=hybrid_experimental`). |
| `vad.py` | `create_vad_analyzer` — shares one pre-compiled Silero ONNX session across calls (building it fresh per call costs ~2s). |
| `ringing_audio.py` | `play_ringing_tone` — the pre-pipeline ring tone, see Trace A step 1. |

### `app/prompts/system_prompt.py`

- `build_system_prompt` (Guest Support) / `build_lead_system_prompt` (Lead Agent) — the two entry points, both layering `GOLDEN_RULES` under mode-specific instructions and host customization. Helper sections: `_persona_and_escalation_sections`, `_guest_memory_section`, `_active_booking_section`, `_caller_phone_section`, `_today_anchor`. `first_message_for`/`lead_first_message_for` resolve the greeting template.

### `app/services/` — business logic

| File | Role |
|---|---|
| `tool_handlers.py` | The actual implementation behind every voice tool — `handle_check_calendar`, `handle_get_pricing`, `handle_dispatch_technician`, `handle_send_whatsapp`, `handle_send_photos`, `handle_escalate_to_host`, `handle_negotiate_rate`, `handle_recommend_properties`, `handle_update_lead`, `handle_search_faq`. Also the ₹0-price guard and phone-number sanity check. |
| `pricing_engine.py` | `calculate_price`, `negotiate_rate` — see `research-flow.md` for the full mechanics (live Airbnb fetch, Redis cache, discount rules). |
| `smart_pricing_service.py` | `refresh_smart_pricing` (daily city-comparable job), `refresh_live_pricing_cache` (daily per-listing pre-warm job) — both scheduled from `main.py`. |
| `discount_policy_service.py` | `parse_discount_policy_text` — one-shot LLM extraction of a host's typed-out negotiation policy into `HostDiscountRule` drafts (`status="pending_validation"` until a host approves). |
| `lead_service.py` | `upsert_lead`, `backfill_lead`, `backfill_lead_from_engagement`, `delete_if_empty`, `delete_for_unqualified_call`, `list_leads`, `get_owned_lead`, `get_active_booking` — see Trace B and Logging section above. |
| `guest_memory_service.py` | `update_guest_memory_from_call` — fire-and-forget post-call aggregation onto `GuestProfile`, no LLM call. |
| `call_service.py` | `get_property_by_number`/`get_user_by_lead_number` (dialed-number routing), `get_or_create_guest_profile`, `get_or_create_call_session`, `attach_exotel_call`, `set_call_classification`, `set_call_summary`, `finalize_call_session` — the DB-facing half of Trace A step 1 and call teardown. |
| `call_classification_service.py` | `classify_call` — one-shot post-call `CallType` labeling, gates `Lead` visibility. |
| `call_summary_service.py` | `summarize_call` — one-shot post-call structured `CallSummary`. |
| `faq_service.py` | `search_faq_entries`, `search_legacy_property_faq`, `full_property_context` (the comprehensive fallback tier), `sync_imported_faq_entries`, `list_faq_gaps`/`_merge_semantically_similar_gaps`/`_attach_suggested_answers` (FAQ Learning Engine), `answer_faq_gap`. |
| `calendar_service.py` | `is_available`, `next_available_window`, `sync_property_ical`/`sync_all_properties` — read-only iCal sync into the local `Booking` table (never writes back to the source calendar). |
| `technician_service.py` | `find_technician` — best-rated technician match for a property+specialty. |
| `notification_service.py` | `create_notification`, `list_notifications`, `mark_read` — see Logging section above. |
| `request_feed_service.py` | `list_service_requests`, `bulk_dismiss` — read-only, keys off `CallSession.call_type` to split Service Requests vs. Booking Requests without re-deriving from `Lead`/`Notification`. |
| `embedding_service.py` | `get_embedding`, `cosine_similarity`, backfill helpers — OpenRouter-backed embeddings for FAQ-gap semantic dedup. |
| `airbnb_import.py` | `parse_bright_data_listing`/`parse_airbnb_listing` — two independent parsers converging on the same `{"fields", "faq_entries"}` shape, see `research-flow.md`. |

### `app/integrations/` — external API clients

| File | Role |
|---|---|
| `exotel_client.py` | Exotel REST API calls (outbound number lookups etc., separate from the WS audio path). |
| `twilio_client.py` | `send_whatsapp_message`, `send_whatsapp_template`, `create_call_to_action_template` — see Trace B. |
| `email_client.py` | `send_email` — plain SMTP, currently blocked on Railway Trial tier (see `agents.md`). |
| `email_templates.py` | `build_escalation_email_html`, `build_photos_email_html` — HTML rendering for the two email types. |
| `ical_client.py` | `fetch_ical` — read-only HTTP GET of a property's Airbnb iCal feed. |
| `cloudinary_client.py` | Photo upload/URL handling for `Property.photos`. |
| `bright_data_client.py` | `trigger_scrape`/`get_snapshot_status`/`get_snapshot_data` — Bright Data's async scrape flow, see `research-flow.md`. |
| `searchapi_client.py` | `fetch_property_coordinates`, `fetch_listing_total_price`, `fetch_comparable_nightly_rates` — SearchApi.io calls, see `research-flow.md`'s credit-accounting section. |
| `redis_client.py` | `cache_get_json`/`cache_set_json` — fails open (no-ops) if `REDIS_URL` is unset, used by the pricing cache. |
| `clerk_client.py` | Clerk session/user verification backing `app/auth/dependencies.py`. |

### `app/models/` — SQLAlchemy ORM (see `database.md` for the full schema reference)

`user.py`, `property.py`, `call_session.py`, `lead.py`, `guest_profile.py`, `notification.py`, `booking.py`, `pricing_rule.py`, `host_discount_rule.py`, `faq_entry.py`, `unanswered_question.py`, `technician.py`, `mixins.py` (shared `TimestampMixin` etc.).

### `app/schemas/` — Pydantic request/response + tool-arg schemas

One file per domain, mirroring `models/` — `tool.py` specifically holds every voice tool's argument schema (`RecommendPropertiesArgs`, `GetPricingArgs`, `EscalateToHostArgs`, etc., including `_normalize_phone`), `call_classification.py`/`call_summary.py`/`faq_gap.py` back the one-shot post-call LLM extractions.

### Root-level

- `config.py` — `Settings` (pydantic-settings), every env var, `_use_asyncpg_driver` validator (`postgres://`→`postgresql+asyncpg://`), `groq_models` fallback-chain list.
- `database.py` — async SQLAlchemy engine + `AsyncSessionLocal` factory.
- `main.py` — FastAPI app, `lifespan` (scheduler setup), the four scheduled jobs (`_scheduled_ical_sync`, `_check_llm_health`, `_check_db_health`, `_scheduled_smart_pricing_refresh`, `_scheduled_live_pricing_cache_refresh`), `/health` and `/api/v1/health/llm`.

---

## If you want to trace a *different* real call

The pattern is always the same:
1. Find the tool the LLM called (`app/voice/tools.py`) — that's the entry point.
2. Read the matching `handle_*` in `app/services/tool_handlers.py` for what actually happens.
3. Check whether a guard in the Guardrails table above is armed by that tool's `FunctionCallsStartedFrame` — if so, that's what the guest actually hears, not necessarily what the model generated.
4. Check the Logging section for which DB tables/log lines that specific action touches.
