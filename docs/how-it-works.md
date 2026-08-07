# MIRA Conversational Architecture — Current State, 5 Aug

Documentation-only snapshot of MIRA's voice conversation architecture as of 2026-08-05, branch `abhaya` (post `shagun` merge). Cross-references [agents.md](agents.md) (pipeline/prompt design), [architecture.md](architecture.md) (deployment/infra), [database.md](database.md) (schema), [research-flow.md](research-flow.md) (pricing math), [api.md](api.md) (REST surface).

**Before reading this as a blank slate**: the repo also contains two detailed, largely-executed planning documents that cover most of what "improve conversational intelligence" would otherwise re-derive from scratch — `documentation/agent-conversation-improvement.md` (1560 lines, Phases 0–7, most phases already shipped: slot/goal state tracking, tool-output-fidelity guards, response-shape validation, closing-state lifecycle, recommendation diversity) and `documentation/memory-architecture-plan.md` (922 lines — guest/conversation/knowledge/host/property memory). This document cross-references both rather than restating them, and flags specifically what's *still* open.

---

## 1. Complete request lifecycle: Exotel → Pipecat → LLM → tools → TTS

```
Exotel (raw WS audio)
  │  start event
  ▼
run_voice_pipeline (pipeline.py:775)
  │  resolves Property/User/Guest, builds prompt
  │  starts looped ringback tone (ringing_audio.py) directly on the raw
  │  socket — no pipeline exists yet to push a frame into
  ▼
_run_pipeline (pipeline.py:385) builds the pipecat Pipeline, cancels+awaits
  the ring-tone task right before runner.run()
  ▼
┌────────────────────────────── per-turn pipeline ───────────────────────────────┐
│ STT (Sarvam,     Silence      Language     user_        Redundant   State      │
│ codemix)      →  Watchdog  →  Sync      →  aggregator →  Context  →  Prompt    │
│                   (nudge/       (TTS voice    (0.9s        Guard      Sync     │
│                   hangup)       switch)       timeout)   (drops       (injects │
│                                                           spurious    goal/    │
│                                                           re-fire)    slots)   │
│                                                              │                 │
│                                                              ▼                 │
│                                          LLM (Groq gpt-oss-120b, 12 tools)     │
│                                                │           │                  │
│                                    (function call)   (reply text)             │
│                                                ▼           ▼                  │
│                            tools.py → tool_handlers.py   Repetition Guard     │
│                            (DB writes: Lead,                  │               │
│                             Notification, …)             Meta-Commentary     │
│                                                Guard                          │
│                                                     │                         │
│                                          Property Recommendation Guard        │
│                                                     │                         │
│                                          Escalation Phrase Guard              │
│                                                     │                         │
│                                          Premature End-Call Guard             │
│                                                     │                         │
│                                          Response Shape Validator             │
│                                                     ▼                         │
│                                  TTS (Sarvam, pace 1.15, EN_IN/HI_IN)          │
└──────────────────────────────────────┬─────────────────────────────────────┘
                                        ▼
                          transport.output() → Exotel → guest's phone
                                        │
                                        ▼
                      assistant_aggregator appends spoken (post-guard)
                      text back into context.messages → next guest turn
```

Everything from Redundant Context Guard through Response Shape Validator is a deterministic, code-level backstop for a specific confirmed-live prompt-compliance failure — inert on the overwhelming majority of turns. The tool-call branch is the only path that touches Postgres mid-call.

### Step-by-step

1. **Exotel opens a WebSocket** to `POST/WS /api/v1/voice/exotel/ws/{token}` (`app/api/v1/voice.py`). Pipecat's `parse_telephony_websocket` reads the initial "start" event into a `CallData`.
2. **`run_voice_pipeline`** (`pipeline.py:775`) takes over, before any pipeline object exists:
   - Starts a looped ringback tone directly on the raw socket (`ringing_audio.py`) — there's no pipeline yet to push a frame into, so this bypasses pipecat entirely.
   - Routes the call: `call_service.get_property_by_number` (Guest Support, one fixed property) vs. `get_user_by_lead_number` (Lead Agent, portfolio-wide).
   - Resolves/creates `GuestProfile`, checks for an active booking, creates the `CallSession` row.
   - Builds the system prompt once (`build_system_prompt` / `build_lead_system_prompt`) and the fixed greeting text.
3. **`_run_pipeline`** (`pipeline.py:385`) constructs the actual pipecat `Pipeline`: STT, the guard chain, LLM, TTS, tools, turn-detection strategy. Right before `runner.run()`, the ring-tone task is cancelled and awaited — no window where two writers hit the same socket.
4. **Greeting**: pre-seeded into the LLM context as an assistant turn, then spoken via `worker.queue_frame(TTSSpeakFrame(first_message))` on `on_client_connected`, guarded by a one-shot flag.
5. **Guest speaks** → STT → Silence Watchdog (resets its timer) → Language Sync (may push a live TTS voice switch) → `user_aggregator` decides turn-end (0.9s post-speech silence) → Redundant Context Guard (drops a spurious pipecat re-fire) → State Prompt Sync (injects the current slot/goal summary as one system message, mutated in place) → LLM.
6. **LLM** either replies directly or calls one of 12 tools (`app/voice/tools.py` → `app/services/tool_handlers.py`, some of which delegate further into `app/services/property/retrieval/` or `pricing_engine.py`). The tool result is appended as a `tool`-role message and triggers the next completion.
7. **Guard chain** (LLM → TTS): Repetition → Meta-Commentary → Property Recommendation → Escalation Phrase → Premature End-Call → Response Shape Validator. Each is pass-through by default; each activates only around the one narrow condition it exists for.
8. **TTS** (Sarvam) synthesizes the guarded text → `transport.output()` → Exotel → guest's phone. `assistant_aggregator` appends the *actually-spoken* (post-guard) text back into context, so the model's own memory matches reality.
9. **Teardown** (`on_pipeline_finished`): assembles transcript, `call_service.finalize_call_session`, then two one-shot post-call LLM calls (`call_classification_service.classify_call`, `call_summary_service.summarize_call`) and lead backfill/cleanup.

Two agent modes (**Guest Support** — one fixed property, from that property's `exophone`; **Lead Agent** — portfolio-wide, from `User.lead_exophone`) share this identical pipeline builder and tool set; only the system prompt, first message, and whether `property_id` is pre-fixed differ. Browser-test variants exercise the same code path over WebRTC.

---

## 2. Where conversation state is stored

| Layer | Lifetime | Location | What it holds |
|---|---|---|---|
| LLM context (`context.messages`) | one call, in-memory, unbounded | pipecat's `LLMContext` object, mutated in place | The actual conversation transcript the model sees — system prompt (msg 0, fixed), state-summary block (msg 1, rewritten in place), then alternating user/assistant/tool turns. This *is* the model's memory within a call; nothing else feeds it "what was said." |
| `ConversationState` dataclass | one call, in-memory, plain Python object | `app/voice/conversation_state.py`, instantiated per call in `_run_pipeline`, threaded through tool closures | Structured, deterministic derivation of what's already known: `slots` (dates/guests/budget/location/phone/name), `selected_property_id` ("lock"), `recommendations_shown`, `quoted_price`, `conversation_goal`, `closing_state`, `current_spoken_language` / `explicit_language_preference`. Populated only as a side effect of which tool actually ran — never a separate classifier call. |
| State-summary system message | one call, re-derived every turn | `app/voice/state_prompt_sync.py`, `StatePromptSyncProcessor` | Renders `ConversationState` into one compact system-role message, tracked by Python object identity (not a marker field — see the P0 incident in that file's docstring) and updated in place at `context.messages[1]`. The system prompt itself (`messages[0]`) is never touched, to preserve Groq prompt-cache hits on it. |
| `Lead` row (Postgres) | persists across calls | `app/models/lead.py` | The CRM-visible half of what's collected: name/phone/email/dates/guests/budget/location/temperature/summary. Written mid-call by `update_lead`/`escalate_to_host` (`lead_service.upsert_lead`), backfilled/pruned at call end. This is durable but coarser than `ConversationState.slots` — no `conversation_goal`, no `recommendations_shown`, no `quoted_price`. |
| `GuestProfile` row (Postgres) | persists *across calls*, host-scoped by phone | `app/models/guest_profile.py` | Cross-call memory: name, `total_stays`, `preferred_language`, `last_outcome`, `conversation_summaries` (list of short per-call summaries, not raw transcript). Updated fire-and-forget post-call by `guest_memory_service.update_guest_memory_from_call` — aggregates the `Lead.conversation_summary` already written, never a fresh LLM call. |
| `CallSession.transcript` | persists, one row per call | `app/models/call_session.py` | Full `role: content` text, assembled from `context.messages` at teardown. Record only — never re-read mid-call. |

**Not a gap**: the split between "LLM context" (unstructured, what the model reasons over) and "`ConversationState`" (structured, deterministically derived) is deliberate and already documented as the fix for a specific class of bug — see `memory-architecture-plan.md` §2 and `conversation_state.py`'s own module docstring. The system prompt is built once, before `ConversationState` even exists, and is never rebuilt — `StatePromptSyncProcessor` exists specifically to bridge that gap without breaking prompt caching.

---

## 3. How booking information is currently collected

There is **no dedicated "booking" object or booking flow** — bookings are represented as a `Lead` row that a host manually promotes to `status="booked"` from the dashboard Kanban (`PATCH /leads/{id}`, the *only* place `Lead.status` is ever written — the voice agent never touches it). The separate `Booking` model (`app/models/booking.py`) is populated only by the read-only iCal sync (`calendar_service.sync_property_ical`) from each property's existing Airbnb calendar — it has no price/payment columns and nothing in the voice pipeline writes to it.

Collection mechanics, per `LEAD_AGENT_INSTRUCTIONS` (`system_prompt.py:740-822`):

1. Dates → guests → location/purpose, gated behind a "have dates been finalized?" branch that sets `lead_temperature` (hot/warm/cold).
2. `recommend_properties` called once enough is known; a property becomes "active"/"locked" the moment the guest shows interest (mechanically enforced by `ConversationState.lock_property`, not just prompt instruction).
3. Name + phone collected *only after* interest in a specific property is shown (prompt-level policy — not code-enforced timing).
4. `update_lead` called incrementally, every time any field becomes known — **this is the actual persistence step**; it upserts into `Lead` via `lead_service.upsert_lead`, additive-only (never blanks a field already set).
5. The moment a guest verbally accepts a price: `update_lead(lead_temperature="hot", …)` then `escalate_to_host` in the same turn. **There is no tool that finalizes a booking** — this is a hard architectural line: MIRA can qualify and escalate, never confirm. A host closes the loop manually (WhatsApp/dashboard) outside the pipeline entirely.

**Real gap, not addressed anywhere in the existing plans**: no payment/deposit capture, no booking-confirmation webhook, no write-back to the property's calendar. `project_state.md`'s "Open design questions" section already flags this explicitly (Razorpay/Cashfree/PayU researched, not built) — it is *known and deprioritized*, not undiscovered.

---

## 4. Whether conversation memory exists

Yes, at three distinct granularities — conflating them is the most common way to misjudge this codebase:

- **In-turn**: The LLM's own context window — unbounded, grows every turn, no summarization/truncation. Flagged as an open risk in `agent-conversation-improvement.md` Phase 4a ("In-call memory has no ceiling") — **the phase exists but its build status wasn't confirmed in this pass** (see Technical Debt).
- **In-call**: `ConversationState` — structured, deterministic, never LLM-derived. This is genuinely a form of working memory, distinct from and complementary to the raw transcript.
- **Cross-call**: `GuestProfile.conversation_summaries` + `Lead` history, surfaced into the next call's system prompt via `_guest_memory_section`/`_active_booking_section`. Real but intentionally thin — "kept to one short paragraph deliberately, since this competes with GOLDEN_RULES and property FAQs for context budget on every single turn" (comment in `system_prompt.py`).

What does **not** exist: vector/embedding-based long-term memory, semantic search over past conversations, or any RAG layer over transcript history. This is an explicit, stated non-goal in three places (`system_prompt.py`'s own module docstring, `memory-architecture-plan.md:290-297`, and the Non-goals section of `agent-conversation-improvement.md`) — "at Tier 1 call volume for a handful of properties, a RAG/Pinecone pipeline is unnecessary complexity." `embedding_service.py` does exist, but only for FAQ-gap semantic dedup, unrelated to conversation memory.

---

## 5. How prompts are constructed

`app/prompts/system_prompt.py` (885 lines) — string concatenation, not a template engine, not retrieval. Two entry points share one `GOLDEN_RULES` block (~250 lines, itself accreted from dated, cited real-call failures — nearly every clause has a "Confirmed live: …" justification inline) layered under mode-specific instructions and host customization:

| Function | Mode | Assembles |
|---|---|---|
| `build_system_prompt` | Guest Support | `GUEST_SUPPORT_INSTRUCTIONS` + `GOLDEN_RULES` + today-anchor + persona/escalation/closing overrides + caller-phone fact + one fixed property's full detail block (rules, amenities, FAQ, seasonal notes) + guest memory + active booking |
| `build_lead_system_prompt` | Lead Agent | `LEAD_AGENT_INSTRUCTIONS` + `GOLDEN_RULES` + today-anchor + persona/escalation overrides + caller-phone fact + guest memory + active booking + a compact per-property portfolio listing (name/id/city/price/capacity — amenities and USP deliberately omitted to bound token cost per turn) |

Built **once**, before the pipeline exists, and never rebuilt mid-call — this is why `StatePromptSyncProcessor` exists as a separate mechanism (§2 above) rather than the system prompt itself carrying live state. A known, unfixed finding: `caller_phone_section`/`_guest_memory_section`/`_active_booking_section` (all per-call-unique) currently sit *before* the static property block, defeating Groq's prefix-based prompt cache on that block across different calls (`project_state.md` "Open design questions" — scoped, not implemented).

---

## 6. Files controlling conversation flow

- `app/voice/pipeline.py` — pipeline construction, entry points, LLM provider fallback chain
- `app/voice/conversation_state.py` — the structured in-call memory object
- `app/voice/state_prompt_sync.py` — bridges state → LLM context
- `app/prompts/system_prompt.py` — everything the model is instructed to do
- `app/voice/silence_watchdog.py` — turn-taking timeout / hangup lifecycle
- `app/voice/turn_strategies.py` — when a guest's turn is considered "done"
- `app/voice/language_sync.py` — live language switching
- The seven guard processors (`repetition_guard.py`, `meta_commentary_guard.py`, `property_recommendation_guard.py`, `escalation_phrase_guard.py`, `premature_end_call_guard.py`, `redundant_context_guard.py`, `response_shape_guard.py`) — each corrects one shape of the LLM not following the flow correctly

---

## 7. Business logic vs. prompt engineering

| Pure business logic (no LLM involved) | Pure prompt engineering (no code logic) | Hybrid (both, deliberately) |
|---|---|---|
| `pricing_engine.py` (price/negotiation math); `calendar_service.py` (availability, iCal sync); `lead_service.py` (upsert/backfill/dedup); `notification_service.py`; `property/retrieval/{filter_builder,sql_search,ranking}.py`; all seven pipeline guards; `ConversationState`'s own goal-derivation logic | `GOLDEN_RULES` tone/formatting/warmth clauses; lead-qualification workflow ordering (prompt prose, not enforced by code except the property-lock exception); scope/decline heuristics ("is this caller relevant") | `system_prompt.py`'s `_today_anchor()` (dates computed in code, spoken by the model); `property_recommendation_guard.py` (business logic verifies what the LLM says against real tool output); `call_classification_service.py` / `call_summary_service.py` (one-shot LLM calls, but their output structurally overrides live tool-call judgments) |

---

## 8. Whether there is a state machine already

**Partial.** Not a formal state machine (no enum-transition table, no library, no illegal-transition guard) — but closer to one than a first read suggests. Two real state-like constructs exist:

- **`ConversationState.conversation_goal`** — an 11-value `Literal` type (`greeting` → `collecting_dates` → … → `closing`), recomputed by `_recompute_goal()` from a fixed priority order every time a tool fires. This is a goal/phase tracker, derived rather than explicitly transitioned — no code rejects an "invalid" transition, and the LLM is free to ignore the surfaced goal hint entirely (it's advisory prompt content, not a gate).
- **`ConversationState.closing_state`** — a genuine 3-value state machine (`open` → `farewell_pending` → `closed`, with a real reopen transition back to `open`), with actual owners for each transition (`mark_farewell_pending`/`mark_reopened`/`mark_closed`, called from `silence_watchdog.py`) and one real invariant enforced in code (a pending close can't complete if the guest speaks again).

**What's genuinely missing** for a "real" booking state machine: nothing tracks *booking* lifecycle stages (inquiry → qualified → quoted → accepted → escalated → confirmed) as a first-class transition-guarded object — that entire progression today is inferred from a combination of `Lead.status` (host-set, dashboard-only), `Lead.lead_temperature` (LLM-set via `update_lead`, no validation), and `ConversationState.conversation_goal` (in-call only, discarded at hangup). These three don't share a vocabulary and nothing reconciles them.

---

## 9. Where property search happens

**Deterministic.** `app/services/property/retrieval/` — this package is not documented elsewhere in `docs/`, which previously described `handle_recommend_properties` doing inline SQL directly. That's now stale: `handle_recommend_properties` (`tool_handlers.py`) is a one-line delegate to `orchestrator.recommend_properties`.

| File | Role |
|---|---|
| `orchestrator.py` | Pipeline: filter → SQL search → (conditional) semantic enrichment → merge/rank → format. Threads `check_in`/`check_out` through from `ConversationState.slots` (not part of the LLM-facing tool schema) to pre-exclude unavailable properties via `calendar_service.unavailable_property_ids`, fail-open on error. |
| `filter_builder.py` | Builds the base SQLAlchemy filter (host scope, budget, guest count, location — including the Goa-region-locality expansion fix documented in `CLAUDE.md`). |
| `sql_search.py` | Runs the structured query, price-ascending, with the guest-count-fallback/combo-note logic for oversized groups. |
| `semantic_search.py` | Only fires when `purpose_of_stay` is set *and* SQL under-returned (<3 results) — embedding-based, never a replacement for structured filtering, never for a purely structured query. |
| `ranking.py` | Merges SQL + semantic results; `diversify_leading_candidates` rotates the leading pick within a comparable-price band, seeded off `call_session_id`, so two similar guests don't always get the identical top property. |
| `context_builder.py` | Formats the final `RecommendationResult` (newline-separated, not `|`-joined — see the delimiter-collision fix in `CLAUDE.md`). |

Guest Support calls never reach this — `recommend_properties` is explicitly disabled in that mode's prompt (fixed to one property already).

### Trace — "Do you have anything in South Goa?" (Lead Agent, `recommend_properties`)

```
voice.py:ws_endpoint
  → pipeline.py:run_voice_pipeline
      → call_service.get_user_by_lead_number          (routes to Lead Agent)
      → call_service.get_or_create_guest_profile
      → lead_service.get_active_booking
      → call_service.get_or_create_call_session
      → system_prompt.py:build_lead_system_prompt      (built once)
      → pipeline.py:_run_pipeline → _run_pipeline_inner → runner.run()

  [guest speaks "anything in South Goa"]
  → SarvamSTT → SilenceWatchdog → LanguageSync → user_aggregator
  → RedundantContextGuard → StatePromptSync → LLM

  LLM calls recommend_properties(preferred_location="South Goa")
  → tools.py: recommend_properties closure
      → ConversationState.selected_property_id check (not locked yet)
      → tool_handlers.py:handle_recommend_properties
          → property/retrieval/orchestrator.py:recommend_properties
              → filter_builder.build_base_filters
              → sql_search.run_sql_search        (Goa-locality-expanded WHERE)
              → calendar_service.unavailable_property_ids   (fail-open)
              → [semantic_search.run_semantic_search]        (only if <3 results + purpose_of_stay)
              → ranking.merge_and_rank → diversify_leading_candidates
              → context_builder.build_recommendation_result
      → property_recommendation_guard.record_tool_result()   (BEFORE result_callback)
      → result_callback(result) → triggers next LLM completion

  next completion's frames: llm → repetition_guard → meta_commentary_guard
      → property_recommendation_guard   (verifies a real property was named;
                                          overrides reply if not)
      → escalation_guard → premature_end_call_guard → response_shape_guard
      → SarvamTTS → transport.output() → Exotel → guest
      → assistant_aggregator appends spoken (post-guard) text to context
```

---

## 10. Where booking confirmation happens

**It doesn't, anywhere in the pipeline.** This is the single clearest architectural boundary in the system, and it's intentional (§3 above): `escalate_to_host` is the terminal voice-agent action for any booking request — a WhatsApp/email notification to the host plus a `Lead` upsert with `escalated=True`. A booking is only "confirmed" once a human host manually drags the Kanban card to `status="booked"` (`PATCH /leads/{id}`, dashboard-only, `app/api/v1/leads.py`). No code path in `app/voice/` ever sets that field.

### Trace — escalating to the host

```
LLM decides (per LEAD_AGENT_INSTRUCTIONS step 7): guest verbally accepted a price
  → calls update_lead(lead_temperature="hot", …) THEN escalate_to_host, same turn

update_lead
  → tools.py closure → tool_handlers.handle_update_lead
      → lead_service.upsert_lead(db, host_user_id, call_session_id, **fields)
          (additive only — never blanks a field already known)

escalate_to_host
  → tools.py closure → tool_handlers.handle_escalate_to_host
      → _get_property()
      → notification_service.create_notification(channel="escalation", …)
             ← dashboard's Live Requests feed reads THIS table
      → lead_service.upsert_lead(escalated=True, …)                (again — belt/suspenders)
      → asyncio.create_task: _send_escalation_whatsapp  (Twilio sandbox, detached)
      → asyncio.create_task: _send_escalation_email     (SMTP, detached, currently
                                                           broken on Railway — port 587 blocked)
      → returns natural-language string to LLM

  escalation_phrase_guard armed by FunctionCallsStartedFrame(escalate_to_host)
  → unconditionally REPLACES the model's next reply with a fixed safe line,
    regardless of what the model actually generated

Host sees: dashboard Live Requests (Notification rows) + WhatsApp (if opted into
  sandbox) + email (if SMTP unblocked) + Leads Kanban card with escalated=True
  → host manually sets Lead.status="booked" via PATCH /leads/{id}  ← the ONLY
    booking-confirmation write path in the entire system
```

---

## 11. Deterministic vs. LLM-driven

| | Examples |
|---|---|
| **Deterministic** | Pricing math (`pricing_engine.py`), date anchoring (`_today_anchor`), availability (`calendar_service.py`), property filtering/ranking (`retrieval/`), which tool result gets spoken verbatim vs. overridden (all 7 guards), turn-end timing (`turn_strategies.py`), silence/hangup timers, `conversation_goal`/`closing_state` derivation, ₹0-price refusal, phone-digit-count sanity check |
| **LLM-driven** | Which tool to call and when, how to phrase everything spoken, scope/decline judgment ("is this caller relevant"), language mirroring (passive detection is code — `LanguageSyncProcessor` — but the *reply*-language choice is the model's), extracting structured slot values out of free-form guest speech before calling a tool, lead-temperature judgment |
| **One-shot LLM, post-call only** | `call_classification_service.classify_call`, `call_summary_service.summarize_call` — not built on the streaming pipeline at all, same Groq→Anthropic→OpenRouter fallback, run once after the guest has already hung up, over the full transcript |

The dominant design pattern across this whole codebase: **the LLM decides intent and phrasing; code decides and verifies fact.** Every one of the seven pipeline guards exists specifically because a "trust the prompt" version of that fact-check failed on a real call — this is the single most load-bearing architectural principle to preserve in any future change (see Refactoring Plan's guardrails).

---

## Folder map

```
backend/app/
├── api/v1/                  REST endpoints (voice.py = Exotel WS + browser-test offer)
├── prompts/
│   └── system_prompt.py     GOLDEN_RULES + both prompt builders            [flow: prompt]
├── voice/                   real-time pipecat pipeline
│   ├── pipeline.py          pipeline assembly, entry points, LLM fallback  [flow: core]
│   ├── conversation_state.py  in-call structured memory                   [flow: state]
│   ├── state_prompt_sync.py   state → LLM context bridge                  [flow: state]
│   ├── tools.py              12 tool wrappers (closures)                  [flow: tools]
│   ├── silence_watchdog.py    turn timing / hangup lifecycle              [flow: lifecycle]
│   ├── turn_strategies.py     turn-end detection strategies               [flow: lifecycle]
│   ├── language_sync.py       live TTS language switch                   [flow: i18n]
│   ├── vad.py                 shared VAD analyzer                        [infra]
│   ├── ringing_audio.py       pre-pipeline ringback tone                 [infra]
│   ├── *_guard.py / response_shape_guard.py   7 deterministic backstops  [flow: guards]
│   └── assets/
├── services/                 business logic (LLM-free unless noted)
│   ├── tool_handlers.py      the 10 handle_* functions behind the tools  [logic]
│   ├── pricing_engine.py     price/negotiation math                     [logic]
│   ├── calendar_service.py   availability / iCal sync (read-only)       [logic]
│   ├── lead_service.py       Lead upsert/backfill/dedup                 [logic]
│   ├── guest_memory_service.py  cross-call memory aggregation (no LLM)  [logic]
│   ├── call_classification_service.py  post-call, one-shot LLM         [logic+LLM]
│   ├── call_summary_service.py         post-call, one-shot LLM         [logic+LLM]
│   ├── faq_service.py        tiered FAQ fallback                       [logic]
│   ├── notification_service.py                                        [logic]
│   ├── smart_pricing_service.py  scheduled cache pre-warm jobs         [logic]
│   └── property/
│       ├── card.py, pitch_formatter.py, chunking.py
│       └── retrieval/        THE property-search subsystem              [flow: search]
│           ├── orchestrator.py   filter→SQL→semantic→rank→format
│           ├── filter_builder.py, sql_search.py, semantic_search.py
│           ├── ranking.py, context_builder.py, formatter.py
├── integrations/             external API clients (Exotel, Twilio, email, iCal, SearchApi…)
├── models/                   SQLAlchemy ORM (Lead, CallSession, GuestProfile, Property, …)
├── schemas/
│   └── tool.py                every voice tool's Pydantic arg schema     [flow: tools]
├── config.py, database.py, main.py
```

---

## Data flow

Two parallel representations of "what's happened this call" (LLM context, `ConversationState`) stay in sync via `StatePromptSyncProcessor`; only tool calls cross into Postgres mid-call. Post-call jobs read the finished transcript once, never mid-call.

```
Guest speech ──▶ TranscriptionFrame (per utterance)
                    │              │
                    │              └──▶ ConversationState (slots/goal/locked-property;
                    │                   derived, in-memory, per-call)
                    ▼                        │
        LLM context.messages                 │  StatePromptSync rewrites msg[1]
        (unbounded, in-memory,   ◀───────────┘
         grows every turn)                   │
                    │                        │  update_lead / escalate_to_host
                    ▼                        ▼
           next LLM completion      Postgres (mid-call): Lead · CallSession · Notification
                    │
                    ▼   full transcript, read once, after hangup
        Post-call (one-shot LLM): classify_call · summarize_call · GuestProfile update
```

---

## Conversation flow (Lead Agent mode)

```
greeting
  → collecting_dates → collecting_guests → collecting_location_or_purpose
      (order derived from ConversationState._SLOT_GOAL_PRIORITY;
       any slot already known from an early "I'm Priya, we're 4 people" opener
       is skipped — GOLDEN_RULES' re-ask ban)
  → recommending  (recommend_properties called)
  → awaiting_selection
  → checking_availability / negotiating  (once a property is locked)
  → collecting_lead_contact  (name, then phone — only after interest shown)
  → escalating  (verbal accept → update_lead + escalate_to_host, same turn)
  → closing     (closing_state: open → farewell_pending → closed,
                  reopens if guest speaks again before hangup completes)
```

This is the *prompt-scripted* ordering and also, since Phase 1/1.5/1.6 of `agent-conversation-improvement.md`, the `conversation_goal` value surfaced back to the model each turn as a hint (never a hard gate — the model can and does deviate, e.g. answering a spontaneous FAQ question mid-flow). "Answering a spontaneous FAQ question mid-flow" is handled by `GOLDEN_RULES`' answer-first-then-return-to-flow clause (`system_prompt.py`) alone — prompt-only, no `ConversationState` backing. A state-tracked version (`interrupted_goal`, set whenever `search_faq` fired during a resumable goal) was built and removed the same session (2026-08-05): `search_faq` turned out to be the *required* tool for any on-topic property question too, not just tangents, so the mechanism flagged ordinary in-flow questions as "interruptions" as often as real ones, with no code-checkable way to tell the two apart. Right call for now is prompt-only, per Standing Rule 3's own carve-out for behavior no code path can enforce structurally — revisit only if a real distinguishing signal is ever found.

---

## Technical debt list

| # | Item | Where | Severity |
|---|---|---|---|
| 1 | Prior versions of this doc described inline SQL in `tool_handlers.py` for property search — that's stale; the real logic moved to `app/services/property/retrieval/` at some point without a docs update. Fixed in this revision. | docs/how-it-works.md (this file) | Resolved as of this revision |
| 2 | In-call LLM context has no truncation/summarization ceiling. | pipeline.py / LLMContext usage | **CLOSED (2026-08-05).** Confirmed via direct read of `agent-conversation-improvement.md` Phase 4a: measured against real transcripts (2026-08-01) — in-call history growth is ~9% of total per-call token cost even on the longest real call observed (~25% prompt-size growth at worst), not the runaway risk this item worried about. Phase 4a.2 (compaction) explicitly and correctly not built, per that measurement — this was a "measure first" gap, not a missing feature, and it's now measured. Superseded item #7 (below) as the actual real lever, which is now also closed. |
| 3 | No unified booking-lifecycle vocabulary — `Lead.status` (host-set), `Lead.lead_temperature` (LLM-set, unvalidated), and `ConversationState.conversation_goal` (in-call only) each track a different partial view of "how far along is this booking," with nothing reconciling them and no shared enum. | models/lead.py, conversation_state.py | **PARTIALLY CLOSED (2026-08-05).** A documentation-only cross-reference mapping the three vocabularies to each other was added (`conversation_state.py`, near `ConversationGoal`; one-line pointer on `Lead.lead_temperature`) — no schema change, no migration. A genuine unified/enforced field (migration + backfill decision) remains open and out of scope for this pass; the cross-reference at least makes the relationship discoverable from any one of the three locations. |
| 4 | Escalation email is silently broken in production (Railway Trial-tier blocks outbound SMTP:587) — the send is fire-and-forget so this fails with zero signal to anyone. | app/integrations/email_client.py | OPEN — out of scope for this pass. Known, documented (CLAUDE.md, project_state.md), not yet fixed — needs an HTTP email API (Resend/SendGrid/Postmark) instead of raw SMTP |
| 5 | WhatsApp delivery is sandbox-only (24h session window, opt-in required) — no real WhatsApp Business number yet. | app/integrations/twilio_client.py | OPEN — out of scope for this pass. Known, documented, blocked on external KYC/approval, not a code issue |
| 6 | Guardrail interventions (a repetition cut, a replaced escalation line, a stripped meta-comment) are not logged to any DB table — only visible in the Railway stdout stream for that call's timestamp window, and only in `CallSession.transcript` as post-guard text. No way to query "how often did guard X fire this week." | All 7 guard modules | **PARTIALLY CLOSED (2026-08-05).** All 6 previously-silent guards (`repetition_guard.py`, `meta_commentary_guard.py`, `property_recommendation_guard.py`, `escalation_phrase_guard.py`, `premature_end_call_guard.py`, `response_shape_guard.py`) now log a structured `logger.warning` at each intervention point, matching `redundant_context_guard.py`'s pre-existing convention — a guard firing is no longer silent, where previously 6 of 7 left zero trace anywhere. This resolves "did guard X fire at all" but not the item's own harder framing: none of these `logger.warning` calls carry `call_session_id` (the guard `FrameProcessor` classes aren't constructed with one today), so "how often did guard X fire this week" or "on call Y specifically" still requires manually correlating log timestamps to a call's known start/end time in the Railway stream — the original manual-spelunking problem, just with something to grep for now instead of nothing. A DB-backed table (or threading `call_session_id` into each guard) remains open for full closure. |
| 7 | Groq prompt-cache-defeating section order in `system_prompt.py` (per-call-unique sections before the static, cacheable property block) — scoped fix identified, not implemented. | app/prompts/system_prompt.py | **CLOSED (2026-08-05).** Both `build_system_prompt` and `build_lead_system_prompt` reordered so per-call-unique sections (caller phone, guest memory, active booking) are appended after the static property/portfolio block, not before. Pure statement reordering, zero content change; `test_system_prompt.py` (74 tests) confirmed passing unmodified. |
| 8 | Two prior planning documents (`agent-conversation-improvement.md`, `memory-architecture-plan.md`) exist in the repo but are not linked from `docs/` or `CLAUDE.md` — someone starting from the stable docs alone (rather than `documentation/`) would not discover that most of the obvious "improve conversation intelligence" backlog is already scoped or built. | documentation/ vs docs/ | CLOSED as of the prior revision of this document — this file's own intro links both. |
| 9 | No payment/booking-confirmation path (§3, §10) — architecturally deliberate today, but the single largest functional gap between "qualifies a lead" and "closes a booking end-to-end." | n/a — doesn't exist yet | OPEN — out of scope for this pass (feature work, not architecture-debt cleanup). Known and explicitly deprioritized per project_state.md's Open design questions |

---

## Refactoring plan

This section proposes only what is **not already covered** by the two existing planning documents. Where a phase already exists there, this plan references it instead of restating it — re-planning already-planned work would be the opposite of useful here.

**Status as of 2026-08-05**: items 0, 1, 4 fully CLOSED; items 2 and 3 PARTIALLY CLOSED — see each item's own note for exactly what remains open. This was implemented as a scoped "architecture debt cleanup" pass: preserve existing architecture, extend existing classes/files rather than create new ones, no feature work. See `documentation/project_state.md`'s 2026-08-05 entry for the full change list and test results.

### 0. Reconcile documentation before touching code — CLOSED

Done in the prior revision of this document (cross-references to `documentation/agent-conversation-improvement.md` and `memory-architecture-plan.md` added to this file's own intro).

### 1. Confirm build status of already-planned phases before adding new ones — CLOSED

Confirmed via direct read (2026-08-05): **Phase 4a is fully closed** — measured 2026-08-01 against real transcripts, correctly decided not to build context compaction (in-call history growth is ~9% of per-call token cost, not a runaway risk), signed off in `agent-conversation-improvement.md`. **Phase 7 is genuinely partial and stays that way** — 7.2 (pytest suite) and 7.3 (token budget) are done; 7.1 (fresh-transcript failure-catalogue re-check), 7.4 (live/browser call sign-off), 7.5 (quantitative metrics) all require real post-implementation call data this environment cannot generate on its own, and are explicitly documented as blocked, not silently skipped, in `agent-conversation-improvement.md`'s Phase 7 section (re-confirmed still blocked 2026-08-05 — a real test call was attempted to watch for during this pass, but no call activity reached the monitored local backend process, most likely because the call was routed to the deployed Railway backend instead).

### 2. A shared booking-lifecycle vocabulary (Debt #3) — PARTIALLY CLOSED

Implemented as a documentation-only cross-reference (`app/voice/conversation_state.py`, near `ConversationGoal`; one-line pointer on `Lead.lead_temperature` in `app/models/lead.py`) explaining how `ConversationGoal`, `Lead.lead_temperature`, and `Lead.status` relate — no schema change, no migration, no behavior change to any tool. A genuine unified/enum-enforced field (e.g. `inquiry → qualified → quoted → accepted → escalated → confirmed → declined`, spanning all three with a real migration and backfill decision) remains open and was deliberately not built in this pass — it's prerequisite groundwork for a future payment/booking-confirmation feature (§3/§10 gap), not something this architecture-cleanup pass needed to fully resolve to make the existing three fields' relationship discoverable.

### 3. Guard-firing observability (Debt #6) — PARTIALLY CLOSED

Implemented as the smaller of two options considered: rather than a new DB table (new model, migration, write call, query surface — real scope for a cleanup pass), all 6 previously-silent guards now call `logger.warning(...)` at their intervention point(s), extending each guard's own existing file and matching `redundant_context_guard.py`'s pre-existing logging convention exactly. This closes "did guard X fire at all" (previously answerable for 0 of 6; now all 6 leave a trace in the Railway log stream). It does **not** close the item's harder framing — none of the new log calls carry `call_session_id` (the guard classes aren't constructed with one today), so "how often did guard X fire this week" or "on call Y specifically" still requires manually correlating log timestamps to a call's known window, same as before. A DB-backed table, or threading `call_session_id` into each guard so log lines are directly queryable per call, remains open for full closure.

### 4. Groq prompt-cache section reordering (Debt #7) — CLOSED

Implemented exactly as scoped: `caller_phone_section`/`_guest_memory_section`/`_active_booking_section` moved to the end of both prompt builders (`build_system_prompt`, `build_lead_system_prompt`), after the static property/portfolio content. Zero content change, pure statement reordering — `test_system_prompt.py` (74 tests) confirmed passing unmodified both before and after.

### Explicitly out of scope for this plan (unchanged)

- Anything already itemized as a phase in `agent-conversation-improvement.md` (recommendation diversity, tool-output fidelity, response-shape validation, closing-state lifecycle, language adaptation) — re-verify/complete those via that document's own tracker, not a new one.
- Anything already itemized in `memory-architecture-plan.md` (Guest/Host/Property/Knowledge memory, caching) — same reasoning.
- A new state-machine *library* or formal FSM framework replacing `ConversationState` — the existing design (derived state, tool-call-driven transitions, no illegal-transition enforcement) is a deliberate choice consistent with this codebase's "no new persistence layer, no LLM-classified state" standing rules (`agent-conversation-improvement.md`'s own Non-goals). A full FSM rewrite would contradict that established discipline without a demonstrated need.
- Payment/booking-confirmation build-out (§3/§10) — real gap, but explicitly deprioritized by the user per `project_state.md`; only the lifecycle-vocabulary cross-reference (item 2 above) was added, not the feature itself or a real unified field.

---

## If you want to trace a *different* real call

The pattern is always the same:
1. Find the tool the LLM called (`app/voice/tools.py`) — that's the entry point.
2. Read the matching `handle_*` in `app/services/tool_handlers.py` for what actually happens (it may delegate further, e.g. `handle_recommend_properties` → `app/services/property/retrieval/orchestrator.py`).
3. Check whether a guard in the Guardrails table (see [agents.md](agents.md)) is armed by that tool's `FunctionCallsStartedFrame` — if so, that's what the guest actually hears, not necessarily what the model generated.
4. Check [agents.md](agents.md)'s logging section for which DB tables/log lines that specific action touches.

---

*Sources read directly for this document: `docs/agents.md`, `documentation/project_state.md`, `documentation/agent-conversation-improvement.md` (headers + intro + coverage map), `documentation/memory-architecture-plan.md` (headers), `backend/app/voice/conversation_state.py`, `state_prompt_sync.py`, `tools.py`, `backend/app/prompts/system_prompt.py` (full), `backend/app/models/lead.py`, `call_session.py`, `backend/app/services/property/retrieval/orchestrator.py`, and a directory listing of `backend/app/voice/` and `backend/app/services/property/`. No source file outside this doc was modified.*
