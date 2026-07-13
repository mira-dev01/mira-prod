# Mira Memory Architecture — Implementation Plan

Five memory layers, broken into shippable subtasks. Grounded in the current
schema/code (file:line refs throughout) — nothing here duplicates what
already exists.

Ground truth that shapes every section below:
- One host = one `User` row (`backend/app/models/user.py`). `Property.user_id`,
  `Lead.user_id`, `FaqEntry.user_id` all FK to `users.id`. No separate `Host` model.
- `GuestProfile` (`backend/app/models/guest_profile.py:9-18`) already exists —
  `phone` (unique), `name`, `total_stays`, `preferences` (JSONB), `notes` — but
  is barely read (only `total_stays`/`name` touched in `system_prompt.py:232-238`)
  and has **no host_id/property_id scoping, no call summaries, no lead_status
  link**. This is an extension, not a new table.
- Pricing config today is 100% global constants
  (`pricing_engine.py:19` `MAX_NEGOTIATION_DISCOUNT_PERCENT`, loyalty-bonus map
  at `:120-124`) plus property-scoped `PricingRule` rows
  (`rule_type="length_of_stay"` only — the engine ignores `weekend_surge`/
  `loyalty`/`last_minute` even though the frontend offers them). No host-level
  override exists anywhere.
- FAQ gap-answering already works correctly end-to-end today — new `FaqEntry`
  rows are `status="verified"` immediately, no caching blocks same-call
  pickup (`faq_service.py:286-320`, confirmed via `search_faq_entries`
  `:21-64`). The only real gap is **semantic paraphrase matching** (currently
  trigram similarity ≥0.35 against the literal stored question text).
- Settings page (`frontend/src/app/dashboard/settings/page.tsx`) already uses
  a `Tabs` component with 5 tabs (Workspace / Voice AI / Billing / API / Team,
  last two are `ComingSoonTab` placeholders) — precedent for adding tabs, but
  the new validation surface below is recommended as its own top-level nav
  item instead (see section 0).
- Sidebar nav (`frontend/src/components/sidebar-nav.tsx:26-36`) is a flat
  array — adding a new top-level tab is a one-line addition.
- **Section 2 (Conversation Memory) is now a confirmed bug fix, not
  speculative.** Host-reported: after a guest selects a property in a
  Lead Agent (portfolio-wide) call, follow-up questions can still return
  answers from other properties. Root-caused via full audit (2026-07-13):
  no vector/RAG layer exists anywhere in this codebase to fix — `search_faq`
  is Postgres ILIKE + trigram similarity. The actual causes are (a)
  `LEAD_AGENT_INSTRUCTIONS` never tells the model to pass `search_faq`'s
  already-existing `faq_property_id` argument once a property is chosen
  (it does tell the model this for `check_calendar`/`get_pricing`), and
  (b) `recommend_properties` has no property-scoping concept and is wired
  into every call unconditionally, including Guest Support. See section 2
  for the full trace and fix.

---

## 0. Cross-cutting decisions to lock in before coding

- [ ] **Nav placement**: new "Host Preferences & Knowledge" validation surface —
      **new top-level nav item** (e.g. "AI Training"
      or "Agent Memory") which read
      as static config. Settings stays for static preferences; the new tab
      is for things requiring host *action* (approve/edit/reject).
- [ ] Decide retention window for guest-linked data (`GuestProfile.notes`,
      call summaries) — this is caller-linked PII. Suggest 12 months rolling,
      configurable later, hard requirement before shipping to real hosts.
- [ ] Confirm: all "learned" content (discount placeholders, FAQ auto-drafts,
      seasonal notes) is **host-approved before it affects agent behavior**.
      No silent self-updating pricing or facts. This is the one rule that
      spans every section below.

### 0.1 Non-negotiable guardrail: the live agent must not get slower, worse, or less reliable

The agent is working well right now. Every task below touches something on
or adjacent to the live-call path (`pipeline.py`, `system_prompt.py`,
`tool_handlers.py`, `pricing_engine.py`). None of this ships by "looks
right in code" — each task carries its own hard performance/quality bar,
not just a functional one.

- [ ] **Hard token budget for anything injected into the system prompt.**
      Guest-memory summaries (1.3) and host-policy/persona overrides (4.5)
      are the two new things competing with `GOLDEN_RULES` + property FAQs
      for context. Set a concrete cap (e.g. guest-memory block ≤ 60 tokens,
      host-override block ≤ 40 tokens) before writing the prompt-builder
      code, not after. Measure actual token count of the assembled prompt
      before vs. after each change (`tiktoken`-style count or the LLM
      provider's own tokenizer) — "feels short" is not a pass condition.
- [ ] **No new synchronous DB call may be added to the live turn-taking
      path without an explicit latency budget and a fallback.** Concretely:
      - Guest-profile lookup (1.3) happens once at call-start — fine in
        principle, but must be measured (see 0.2) against the existing
        call-start latency baseline, not assumed free.
      - `HostDiscountRule` lookup inside `negotiate_rate` (4.4) is the
        highest-risk item in this entire plan: it runs **live, mid-call**,
        in the same tool-call path already tuned around Groq TPM limits and
        the 0.9s turn-timeout. It must degrade to today's global-constant
        behavior (`MAX_NEGOTIATION_DISCOUNT_PERCENT`, hardcoded loyalty map)
        on any lookup failure, timeout, or missing host policy — never
        block, error, or silently negotiate for free. Same rule as the
        existing `BRIGHT_DATA_API_KEY`/`SMTP_*` "don't crash, don't block"
        pattern already established in this codebase.
      - Any new cache (section 6) must fail open to a direct DB read, never
        fail closed into a missing-data error the guest can hear.
- [ ] **Every task that touches `pipeline.py`, `system_prompt.py`,
      `tool_handlers.py`, or `pricing_engine.py` is a protected-path change
      per CLAUDE.md's own "Never regress the voice agent" rule** (see the
      existing `tasks.md` Standing Rule 2, which already enforces this for
      the parallel dashboard-redesign effort — same rule applies here).
      Concretely: additive-only where possible (new nullable columns, new
      optional prompt sections gated on data being present), and any actual
      behavior change (pricing math, negotiation ceiling, prompt content)
      needs a before/after comparison, not just a diff read.
- [ ] **A real call must be placed and judged before and after each
      risky task**, not just typechecked/unit-tested. "Verification" in
      this plan always means: run a real or browser-test call
      (`/api/v1/voice/test/offer`, per CLAUDE.md's Voice/WebRTC section),
      not just `pytest` passing — pytest covers correctness of business
      logic, not perceived latency or conversational quality.

### 0.2 Standing verification protocol (applies after every single task below)

Every task subsection in this plan (1.1, 1.2, 1.3, … 6.x) gets this same
closing checklist appended — written out once here, referenced by name
("Standard verification") in each section rather than repeated in full
each time:

1. **Correctness**: `cd backend && pytest` green (real DB, per CLAUDE.md —
   never mock it). Any new/changed model needs its migration applied to
   the test DB first.
2. **No regression on unrelated tools**: re-run (or confirm CI already
   covers) `tests/test_pipeline_llm.py` (Groq fallback coverage) — a change
   to `system_prompt.py` or `tool_handlers.py` risk-touches this even when
   the diff looks unrelated.
3. **Latency check, only for tasks touching the live-call path** (1.2, 1.3,
   2.1, 4.4, 4.5, 6.x): place a real test call via
   `/api/v1/voice/test/offer` before and after the change; compare
   perceived response latency turn-by-turn. If precise timing
   instrumentation doesn't already exist, add temporary logging around the
   new lookup/injection, capture a number, then decide if it's acceptable
   — don't ship a live-path change with an unmeasured latency claim.
4. **Prompt-size check, only for tasks that add prompt content** (1.3,
   4.5): print/log the final assembled system prompt's token count before
   and after: confirm it's within the budget set in 0.1.
5. **Fallback/failure-path check, only for tasks with a new DB
   dependency in a tool call** (4.4 especially): simulate the lookup
   failing or returning nothing (e.g. a host with no `HostDiscountRule`
   rows yet) and confirm the call still completes using today's default
   behavior — never a guest-facing error or hang.
6. **Host-approval gate check, only for tasks in section 3/4 that create
   AI-derived content** (3.2, 4.2): confirm the new data lands with
   `status="pending_validation"` and is NOT read by any live-call code
   path until a host explicitly approves it — grep the read-path code to
   confirm this, don't just trust the write-path code.
7. **Sign-off**: record ✅/❌ + one-line note per item above directly under
   the task in this file before moving to the next task — same operational
   discipline as the existing `tasks.md` tracker for the redesign effort.

---

## 1. Guest Memory (extend existing `GuestProfile`)

### 1.1 Schema
- [ ] Add columns to `GuestProfile`: `host_id` (FK→users, required),
      `last_property_id` (FK→properties, nullable), `preferred_language`
      (str, inferred Hindi/English/Hinglish ratio or dominant tag),
      `lead_status` / `last_outcome` / `sentiment` (mirror, don't duplicate —
      see below), `last_follow_up` (date), `last_call_at` (timestamp).
- [ ] Change uniqueness from `phone` alone to composite
      `(phone, host_id)` — same guest calling two different hosts on Mira
      must not collide.
- [ ] Add `conversation_summaries` (JSONB list of
      `{call_session_id, date, summary, outcome}` — short LLM-generated
      1-2 liners, never raw transcript, matching the existing
      `escalate_to_host` email pattern of summary-not-transcript).
- [ ] **Do not duplicate `Lead.status`/`lead_temperature`/`occasion`.**
      `GuestProfile` should hold a `lead_ids` back-reference (via
      `Lead.guest_profile_id` FK, new column) so status/temperature/occasion
      stay single-sourced on `Lead` rows (one per property-call context) and
      `GuestProfile` aggregates *across* leads rather than re-storing fields.
- [ ] Alembic migration for all of the above.

### 1.2 Population (write path)
- [ ] After a call ends (`pipeline.py` `on_pipeline_finished`, `:337-365`,
      same place `ai_summary`/transcript are already finalized), add a
      fire-and-forget task (`asyncio.create_task`, same pattern as the
      escalation email in `tool_handlers.py`) that:
      - Upserts `GuestProfile` by `(caller_number, host_user_id)`.
      - Generates a short call summary (reuse whatever produces
        `CallSession.ai_summary` today — check if a summarization call
        already exists before adding a new LLM call).
      - Appends to `conversation_summaries`, bumps `total_stays`,
        `last_call_at`, `last_property_id`.
- [ ] Never block the live call on this — must run after pipeline teardown,
      not mid-call.

### 1.3 Read path (prompt injection)
- [ ] In `build_system_prompt` / `build_lead_system_prompt`
      (`system_prompt.py:202-241`, `:312-341`), replace the current minimal
      `guest.total_stays`-only block (`:232-238`) with a compact summary
      pulled from `GuestProfile`: last property discussed, dominant
      preference tags, last outcome — capped at a short paragraph (this
      competes with `GOLDEN_RULES` + property FAQs for context budget on
      every turn, so keep it terse, not a transcript dump).
- [ ] Lookup happens once at call-start (already where `guest` is fetched
      in `run_voice_pipeline`, `pipeline.py:400-479`) — no new per-turn cost.

### 1.4 Frontend
- [ ] `frontend/src/app/dashboard/guests/page.tsx` already exists — check
      current content and extend it to surface the new fields
      (`preferred_language`, `last_outcome`, `sentiment`, summaries list)
      rather than building a new page.

### 1.5 Verification (Standard verification, §0.2 — items 1, 3 required; 2 recommended since prompt-builder is touched)
- [ ] Migration applied to test DB, `pytest` green.
- [ ] Real test call placed before/after 1.2/1.3 land; confirm no
      perceptible added delay at call-start and no change in mid-call
      responsiveness.
- [ ] Confirm guest-memory writes genuinely never block/slow call teardown
      (check the `asyncio.create_task` is fire-and-forget, not awaited).
- [ ] Sign off with ✅/❌ + note before starting section 2.

---

## 2. Conversation Memory (in-call slot state) — includes the reported property-lock bug

**Confirmed bug (host-reported, audited 2026-07-13), now the concrete driver for
this section rather than a speculative reliability improvement:** once a guest
names/selects a property mid-conversation in a **Lead Agent (portfolio-wide)
call**, follow-up questions ("does it have a private pool," "is breakfast
included") can still return answers from other properties in the portfolio.
Root-caused via full audit of the tool/prompt layer — see exact mechanism
below. **Guest Support calls (single-property, `property_id` fixed at
dial-in) are structurally immune to this already** — `property_id` is set
once before the pipeline/tools are built and never reassigned mid-call
(`pipeline.py:400-519`), so this is purely a Lead Agent-mode + one
mode-independent-tool problem, not a general retrieval-architecture problem.

**Important scope correction vs. the original bug report**: there is no
vector search, embeddings, or RAG pipeline anywhere in this codebase to
audit — confirmed via full grep, and explicitly noted as a deliberate
decision in `system_prompt.py:3-5` ("a RAG/Pinecone pipeline is unnecessary
complexity"). `search_faq` is Postgres `ILIKE` + `pg_trgm` `similarity()`
against `FaqEntry` rows (`faq_service.py:21-64`). This section is a
prompt-instruction fix + a state-tracking addition, not a retrieval-layer
refactor.

### 2.1 Root causes (both confirmed, fix both)
- [x] **Cause A — `search_faq`'s property-scoping argument is never invoked
      by the LLM in Lead Agent mode.** `SearchFaqArgs.property_id`
      (`schemas/tool.py:135`, exposed as `faq_property_id` in
      `tools.py:321-336`) already exists and, when supplied, correctly
      scopes `search_faq_entries` to one property (`faq_service.py:51-60`).
      But `LEAD_AGENT_INSTRUCTIONS` (`system_prompt.py:258-309`) only tells
      the model to pass the chosen property's id to `check_calendar`/
      `get_pricing` (step 4, `:274-281`) — it never extends that instruction
      to `search_faq` (step 8, `:308`, is generic). Absent an explicit
      instruction, the LLM leaves `faq_property_id` unset and
      `handle_search_faq` falls back to the call's closure `property_id`,
      which is `None` for the whole Lead Agent call
      (`tool_handlers.py:329-335`) — so `search_faq_entries` runs
      portfolio-wide (`faq_service.py:61-62` skips the property-scoping
      branch entirely when `property_id is None`).
- [x] **Cause B — `recommend_properties` has no property-scoping concept at
      all and is wired into every call's tool list unconditionally**,
      Guest Support included (`build_voice_tools`,
      `pipeline.py:269`/`tools.py:338-348`, no mode branching).
      `RecommendPropertiesArgs` (`schemas/tool.py:100-104`) has no
      `property_id` field; `handle_recommend_properties`
      (`tool_handlers.py:253-286`) always queries every property owned by
      `host_user_id`. `GUEST_SUPPORT_INSTRUCTIONS` never mentions this tool
      to the model, but nothing in code stops the LLM from calling it
      anyway once inside a Guest Support call.

### 2.2 Fix A — property lock via explicit `ConversationState`, not prompt-only
- [x] Add a small in-memory `ConversationState` object per call
      (`selected_property_id`, `selected_property_name`, `booking_stage`,
      `slots_filled` dict, `last_intent`) — created alongside `context` in
      `_run_pipeline` (`pipeline.py:277-283`), passed into
      `build_voice_tools`'s closure (`:269`) the same way
      `call_session_id`/`property_id`/`host_user_id` already are.
      **Implemented** as `app/voice/conversation_state.py` (new file) —
      scoped down to just `selected_property_id`/`selected_property_name`
      + a `lock_property()` helper for now; `booking_stage`/`slots_filled`/
      `last_intent` were in the original sketch but nothing in this bug fix
      needed them yet, so they were left out rather than added speculatively
      (can be added back when a task actually needs them).
- [x] **This is the fix the user explicitly asked for over relying on the
      LLM alone**: do not solely rely on `LEAD_AGENT_INSTRUCTIONS` telling
      the model to pass `faq_property_id` every time (prompt-only fixes are
      probabilistic — the model can still forget). Instead:
      - `handle_search_faq` (and any other tool taking an optional
        property scope) reads `ConversationState.selected_property_id` as
        its fallback **before** falling back to `None`/portfolio-wide —
        i.e. the fallback chain becomes: LLM-supplied `faq_property_id` →
        `ConversationState.selected_property_id` → `None`. This makes
        correct scoping the default even if the LLM never explicitly
        passes the argument. **Implemented** in `app/voice/tools.py`'s
        `search_faq` wrapper.
      - `ConversationState.selected_property_id` gets set programmatically
        whenever a tool call resolves a specific property from the
        conversation — concretely, when `recommend_properties` or
        `check_calendar`/`get_pricing`/`negotiate_rate` is called with a
        specific `property_id` (these already require it per the audit),
        that becomes the new locked property for the rest of the call.
        No new "did the guest select this?" NLU classifier needed — the
        existing required-`property_id` tool calls already are the signal.
        **Implemented**: `check_calendar`/`get_pricing`/`negotiate_rate`
        each call `state.lock_property(args.property_id)` on success.
      - **Switching** ("I'd like to look at Ocean View instead") is
        handled the same way: the next tool call naming a *different*
        `property_id` simply overwrites `ConversationState.selected_property_id`
        — no separate "detect switch intent" logic needed, since the
        existing tool-arg mechanism already requires the LLM to name the
        new property to look anything up about it. **Verified** by
        `tests/test_property_lock.py::test_property_switch_updates_lock`.
      - Also update `LEAD_AGENT_INSTRUCTIONS` to explicitly tell the model
        to pass `faq_property_id` once a property is under discussion,
        matching the existing step-4 instruction already given for
        `check_calendar`/`get_pricing` — belt-and-suspenders with the
        state-based fallback above, not a replacement for it.
        **Implemented**: step 4 and step 8 both updated in
        `system_prompt.py`.
- [x] Lives only for call duration — never persisted directly.
      **Deferred**: feeding final state into the Guest Memory write (1.2)
      as part of the call summary isn't applicable yet since section 1
      (Guest Memory) hasn't been built — noted here for whoever picks up
      section 1 next, not forgotten.

### 2.3 Fix B — scope or gate `recommend_properties` once a property is locked
- [x] Implemented option (b) from the plan (handler-level refusal, no
      tool-list rebuild): `recommend_properties` in `app/voice/tools.py`
      checks `state.selected_property_id` and refuses with a natural-
      language message if set AND the call carries no new distinguishing
      criteria (no `preferred_location`/`budget`/`purpose_of_stay`) — this
      refinement (added during implementation, not in the original plan
      text) is what keeps the explicit switching/comparison requirement
      working: a guest naming a new area/property via `preferred_location`
      still goes through to a real search, only a bare redundant re-browse
      is blocked. See `tests/test_property_lock.py`'s
      `test_recommend_properties_refuses_redundant_rebrowse_once_locked`
      and `test_recommend_properties_allows_explicit_compare_with_new_criteria`.
- [x] Add this same guidance to `GUEST_SUPPORT_INSTRUCTIONS` explicitly
      (currently silent on `recommend_properties` entirely) so the model
      isn't tempted to call it inside a single-property call at all.
      **Implemented**: added an explicit "Do NOT call recommend_properties
      on this call" line to `GUEST_SUPPORT_INSTRUCTIONS`.

### 2.4 Non-goals
- [x] Not a DB table. Not Redis (single-process, single-call lifetime — a
      plain Python object closed over by the tool functions is sufficient
      unless the pipeline ever becomes multi-process per call, which it
      isn't today). Confirmed: implemented as a plain `@dataclass`, no
      persistence layer added.
- [x] Not a vector DB / RAG pipeline / embeddings migration — confirmed
      none exists today and none is needed to fix this bug; do not scope
      this task up into building retrieval infrastructure that isn't there.
      Confirmed: no such infrastructure was touched or added.

### 2.5 Verification (Standard verification, §0.2 — items 1, 2, 3, 5 required — this is the reported-bug fix, verify against the exact repro)
- [x] `pytest` green, including `tests/test_pipeline_llm.py` (7 passed).
      Full suite: 143 passed. 4 pre-existing failures remain
      (`test_calls_api.py::test_call_includes_duration_and_lead_name_phone`,
      `test_tool_handlers.py::test_escalate_to_host_also_saves_lead_so_it_isnt_left_empty`,
      `test_turn_strategies.py::test_is_complete_short_but_punctuated`,
      `test_voice_ice_servers.py::test_ice_servers_stun_only_by_default`) —
      confirmed via a clean `git worktree` checkout at HEAD (no changes from
      this task applied) that all 4 reproduce identically with identical
      pass/fail counts, so none are a regression from this work. Root causes
      are unrelated to the voice-tool/prompt layer touched here: a Python
      3.14/pytest-asyncio event-loop-policy incompatibility, a phone-number
      normalization assertion, a turn-completeness text heuristic, and an
      ICE-server-count assumption that doesn't match this machine's real
      `.env` TURN config. Also found and fixed one real pre-existing
      environment gap along the way (not part of this task's diff): the
      local `mira_test` DB was missing the `pg_trgm` Postgres extension
      `search_faq_entries` depends on — enabled it (`CREATE EXTENSION IF
      NOT EXISTS pg_trgm`, additive/reversible, test DB only), which fixed
      4 of the original 8 failing tests as a side effect.
- [x] **Reproduced the exact reported scenario, at the tool-call level**
      (see `backend/tests/test_property_lock.py`, all 5 tests passing):
      `test_search_faq_locks_to_property_named_via_check_calendar` sets up
      two properties with conflicting FAQ answers to the same question
      ("does it have a private pool?"), names one via `check_calendar` in
      Lead Agent mode (`property_id=None` at the closure level, matching a
      real portfolio-wide call), then calls `search_faq` exactly as the
      LLM would if it forgot to pass `faq_property_id` — confirms the
      answer returned is the *locked* property's, not the other one's.
      **Not done**: an actual live/browser voice call
      (`/api/v1/voice/test/offer`) with real speech, per the plan's letter —
      this environment has no Exotel/Sarvam credentials configured to place
      one. The tool-level test exercises the exact same code path
      (`build_voice_tools` → `search_faq` → `handle_search_faq` →
      `search_faq_entries`) that a real call would, so it's strong
      evidence, but it does not confirm LLM behavior (whether the model
      reliably calls the right tools in the right order on a real turn) —
      only that the underlying mechanism is correct when those tools are
      called. Recommend an actual browser test call before this is
      considered fully verified end-to-end.
- [x] **Reproduced the switching scenario** at the tool level:
      `test_property_switch_updates_lock` confirms a second `get_pricing`
      call naming a different property overwrites `selected_property_id`.
      Same live-call caveat as above applies.
- [x] **Reproduced Fix B**, both branches: `test_recommend_properties_refuses_redundant_rebrowse_once_locked`
      (blocked when locked + no new criteria) and
      `test_recommend_properties_allows_explicit_compare_with_new_criteria`
      (allowed through when the guest gives a new area/property, so
      "compare this with Palm Retreat" style requests still work). Also
      added `test_guest_support_call_search_faq_unaffected_by_conversation_state`
      to confirm Guest Support calls (single-property, no portfolio-wide
      concern to begin with) aren't affected by any of this.
- [x] Confirmed this was reproducibly broken *before* the fix: verified by
      reading the pre-fix code path (`handle_search_faq`'s fallback was
      `default_property_id` = the closure's fixed `property_id`, `None` in
      Lead Agent mode, with no state-based fallback at all) — the new
      `test_search_faq_locks_to_property_named_via_check_calendar` test
      would fail without the `ConversationState` fallback chain in
      `tools.py`'s `search_faq` (confirmed by temporarily reverting that one
      fallback line locally and re-running the test, which failed as
      expected, then restoring it).
- [x] Confirmed state object is genuinely per-call: `ConversationState` is
      constructed fresh inside `_run_pipeline` (one instance per call) and
      passed by reference only into that call's own `build_voice_tools`
      closure — there is no shared/global instance, so two concurrent
      calls structurally cannot share one.
- [x] Sign off: ✅ implementation + targeted tests complete and passing.
      ⚠️ Real/browser voice call verification still outstanding — no
      Exotel/Sarvam credentials available in this environment to place one.
      Recommend doing this before considering the fix production-verified,
      per §0.1's "a real call must be placed and judged" rule.

---

## 3. Knowledge Memory (FAQ learning) + new validation tab

### 3.1 Semantic dedup on gaps
- [ ] `list_faq_gaps` (`faq_service.py:145-206`) currently groups by exact
      `normalized_question` (trim/lowercase). Add an embedding-similarity
      pass so "is there parking" and "where can I park" collapse into one
      gap cluster before hitting the host.
- [ ] Pick embedding source consistent with existing infra (check if Groq/
      OpenRouter/Anthropic embeddings are already used anywhere, else add a
      lightweight local option — this needs its own small research spike,
      don't guess the provider here).

### 3.2 Auto-draft suggestions
- [ ] When a new gap is semantically close to an already-answered
      `FaqEntry`, draft a suggested answer automatically (adapt the matched
      answer's wording, or just surface the matched answer as a one-click
      "apply this" suggestion) instead of requiring the host to retype
      free text every time.
- [ ] Surface in the **new validation tab** (see 3.3) as a pending item:
      "This looks like a question you already answered — apply the same
      answer?" with edit-before-approve.

### 3.3 New "AI Training / Validations" tab
- [ ] New top-level nav entry (see 0) or new page under
      `frontend/src/app/dashboard/`. Reuses existing primitives:
      `ListRow`/`ListRowHeader`/`ListRowBody` (`components/ui/list-row.tsx`)
      for each pending item, same shape as `UnansweredQuestionsCard`
      already uses.
- [ ] Two queues on this page:
      1. **Knowledge validations** — auto-drafted FAQ answers awaiting
         host approve/edit/reject (from 3.2). Approving calls the existing
         `answer_faq_gap` path (`api.faqGaps.answer`), no new backend
         endpoint needed for this part.
      2. **Host preference validations** — discount-placeholder review
         (see 4.3) and any future free-text-derived settings.
- [ ] This becomes the single place a host reviews anything the AI
      inferred before it goes live — matches the rule in section 0.

### 3.4 Confirm existing gap→answer pipeline needs no fix
- [ ] Already verified: answering a gap creates a `status="verified"`
      `FaqEntry` with correct `property_id`/portfolio scoping, and the very
      next `search_faq` call picks it up — no caching or scoping bug exists
      today (`faq_service.py:286-320`, `:21-64`). **No backend fix needed
      here** — the "make sure the agent is wired to answer these" requirement
      is already satisfied by existing code. Only the *dedup/UX* layer (3.1–3.3)
      is new work.

### 3.5 Verification (Standard verification, §0.2 — items 1, 6 required)
- [ ] `pytest` green.
- [ ] Confirm auto-drafted answers (3.2) are `status="pending_validation"`
      and are provably NOT read by `search_faq_entries` until a host
      approves — grep `search_faq_entries`'s `status == "verified"` filter
      still gates this correctly, don't just trust the write path.
- [ ] Embedding/similarity dedup (3.1) verified against a couple of real
      paraphrase pairs from actual `UnansweredQuestion` data, not just
      synthetic examples.
- [ ] Sign off with ✅/❌ + note before starting section 4.

---

## 4. Host Memory (host-level policy — the highest-leverage gap)

### 4.1 Schema
- [ ] Add to `User` model (`user.py`) alongside existing
      `agent_persona`/`agent_first_message`/`agent_escalation_phrase`:
      - `discount_policy_text` (Text, nullable) — the host's raw free-text
        paragraph, e.g. "if guest doesn't ask, keep price as offered; if
        they ask for a discount, offer 5%; repeat customers across my
        properties get 8%."
      - `negotiation_allowed` (bool, default True)
      - `max_discount_percent_override` (nullable float — replaces the
        global `MAX_NEGOTIATION_DISCOUNT_PERCENT` constant per host when set)
      - `tone` (enum/str: luxury/friendly/formal — or fold into existing
        `agent_persona` free text if that's already expressive enough;
        check before adding a redundant field)
      - `allow_pets` / `allow_early_checkin` (bool, nullable = "no policy
        set", falls back to current behavior)
      - `follow_up_channel_preference` (str: whatsapp/call/email)
- [ ] New table `HostDiscountRule` (structured, derived from
      `discount_policy_text` by the LLM-parsing step in 4.2):
      `host_id`, `trigger_type` (enum: `"no_ask"`, `"guest_requests"`,
      `"repeat_guest_same_host"`, custom), `discount_percent`,
      `source` ("ai_parsed" | "host_edited"), `status`
      ("pending_validation" | "approved"), `raw_source_text` (traceability
      back to the paragraph that produced it).
      This is the "placeholders" the host paragraph gets broken into.
- [ ] Alembic migrations for both.

### 4.2 Paragraph → structured placeholders (LLM parsing step)
- [ ] New backend endpoint, e.g. `POST /auth/me/discount-policy/parse`:
      takes `discount_policy_text`, calls the LLM (reuse existing
      `_build_llm()` / Groq fallback chain — no new provider needed) with a
      constrained extraction prompt: "break this into
      {trigger_type, discount_percent} rules." Returns structured
      `HostDiscountRule` drafts with `status="pending_validation"`.
    - This does not write directly to live pricing — output lands in the
      validation tab (3.3/4.3) first. Matches the "no silent self-updating
      pricing" rule from section 0.
- [ ] Store `raw_source_text` per rule so the host can see *why* the AI
      derived a given placeholder when reviewing it.

### 4.3 Validation UI (same tab as 3.3)
- [ ] Host pastes/edits `discount_policy_text` somewhere on the Settings
      "Voice AI" tab (natural extension of the existing persona/escalation
      textareas at `settings/page.tsx:217-268`) or a new "Pricing Policy"
      field — triggers the parse endpoint (4.2) on save.
- [ ] Resulting draft rules show up in the new validation tab as editable
      cards: trigger description + discount % + "Approve" / "Edit" /
      "Reject" buttons. Approving flips `status="approved"`.
- [ ] Approved `HostDiscountRule` rows become the source of truth for 4.4.
      Host can always come back and edit/deactivate later (no re-parse
      required for manual edits — direct CRUD on the approved rule).

### 4.4 Wire into pricing engine + `/pricing` tab (per-property propagation)
- [ ] `pricing_engine.negotiate_rate` (`:106-151`) currently reads the
      global `MAX_NEGOTIATION_DISCOUNT_PERCENT` and hardcoded loyalty map.
      Change to: look up the host's approved `HostDiscountRule`s first;
      `negotiation_allowed=False` → tool should refuse/escalate instead of
      negotiating; `trigger_type="guest_requests"` rule's
      `discount_percent` replaces the ad-hoc floor calc for that case;
      `trigger_type="repeat_guest_same_host"` (8% in the example) checks
      **Guest Memory (section 1)** — specifically whether this guest's
      `GuestProfile` shows stays across >1 property for this host — this is
      the concrete place sections 1 and 4 connect.
- [ ] Auto-create/update `PricingRule` rows per property when a
      `HostDiscountRule` is approved, per the user's explicit requirement
      ("autoupdates the setdiscount for each property"). Needs a
      `rule_type` that maps cleanly — likely a new `rule_type` value (e.g.
      `"host_policy_derived"`) distinct from `length_of_stay`, or extend
      `_length_of_stay_discount_percent`-style logic with a parallel
      `_host_policy_discount_percent` function that
      `calculate_price`/`negotiate_rate` both consult.
    - Since `PricingRule` today has no `user_id` (property-scoped only,
      `pricing_rule.py:11-22`), decide: derive-on-read from
      `HostDiscountRule` (host_id → all their properties) rather than
      writing N duplicate `PricingRule` rows per property — avoids drift
      when the host later edits the host-level rule. Recommend derive-on-read
      unless the `/pricing` tab specifically needs to show these as
      editable per-property rows (in which case materializing them makes
      the existing UI work unmodified — worth a quick call with the user
      on which they'd rather maintain).
- [ ] `/pricing` page (`pricing/page.tsx`) should visibly show which rules
      came from host-level policy (`source="host_policy"` badge) vs.
      manually added per-property rules, so a host isn't confused about
      where a discount number came from.
- [ ] **Mandatory fallback**: if `HostDiscountRule` lookup fails, times out,
      or the host has no approved rules yet, `negotiate_rate` must fall
      back to today's exact existing global-constant behavior
      (`MAX_NEGOTIATION_DISCOUNT_PERCENT` + hardcoded loyalty map) —
      never error, never hang, never default to a 0%/100% discount. This
      is the single highest-risk behavior change in the whole plan since
      it runs live, mid-call, in the guest's negotiation path — treat it
      with the same "don't crash, don't block" discipline already used for
      `BRIGHT_DATA_API_KEY`/`SMTP_*` elsewhere in this codebase.

### 4.5 Consume in negotiation prompt/tool path
- [ ] `GOLDEN_RULES` (`system_prompt.py:83-170`) currently has one fixed
      negotiation instruction set for every host. Add per-host override
      text (persona-style injection, same mechanism as
      `_persona_and_escalation_sections`) reflecting
      `negotiation_allowed`/`tone` so a "never negotiate, luxury tone" host
      and a "friendly, allows 5%" host get genuinely different prompts, not
      just different backend math.

### 4.6 Verification (Standard verification, §0.2 — items 1, 2, 3, 4, 5, 6 all required — highest-risk section in the plan)
- [ ] `pytest` green, including `tests/test_pipeline_llm.py`.
- [ ] Real test call: negotiate a price as a guest, confirm the counter-offer
      matches the host's approved `HostDiscountRule`, and latency of the
      `negotiate_rate` tool call is measured and compared against
      pre-change baseline (log timestamps around the DB lookup).
- [ ] **Explicitly simulate the failure path**: temporarily point at a host
      with zero approved `HostDiscountRule` rows (or force the lookup to
      fail) and confirm negotiation still works exactly as it does today,
      with no visible degradation to the guest.
- [ ] Confirm prompt token count for the section 4.5 addition is within
      the budget set in §0.1.
- [ ] Confirm `HostDiscountRule` rows created via 4.2's parser are
      `status="pending_validation"` and are NOT read by `negotiate_rate`
      until `status="approved"` — grep the read path to prove this.
- [ ] Sign off with ✅/❌ + note before starting section 5.

---

## 5. Property Memory (consolidation, not a new table)

- [ ] **Do not create a new `PropertyMemory` table** — `Property`,
      `FaqEntry`, `PricingRule`, and `neighborhood_info` already cover
      amenities/policies/dynamic FAQs/nearby-places/pricing/check-in/
      parking/house-rules. Verified: `_upsert_property_from_parsed()`
      already converges both import paths onto these same tables.
- [ ] Only genuinely new piece: **seasonal notes** — nothing today models
      time-varying property facts ("pool closed in monsoon," "extra heater
      Nov–Feb"). Add `seasonal_notes` (JSONB list of
      `{note, start_month, end_month}` or similar) to `Property`, surfaced
      in `build_system_prompt` only when the call's current date falls in
      range.
- [ ] Optional cleanup (not blocking): rename/group existing fields in the
      Properties dashboard page under visual "Property Memory" section
      headers for host clarity — cosmetic, no schema change.

### 5.1 Verification (Standard verification, §0.2 — items 1, 3, 4 required)
- [ ] `pytest` green.
- [ ] Real test call during and outside a seasonal-note's date range —
      confirm the note appears in the prompt only when applicable, and
      confirm prompt token count stays within budget when a note is active.
- [ ] Sign off with ✅/❌ + note before starting section 6.

---

## 6. Caching (only where it earns its keep)

- [ ] **Per-property FAQ/pricing read cache** — `search_faq_entries` and
      `PricingRule` lookups run on essentially every relevant tool call
      within a live call. Add Redis (or in-process LRU if Redis isn't
      already in the stack — check before introducing a new dependency)
      keyed on `(property_id, query-hash)` for FAQ, `(property_id)` for
      pricing rules, short TTL, invalidated on FAQ/pricing edit endpoints.
- [ ] **Guest-profile lookup cache** — one lookup per call-start, not
      per-turn, so this is low-priority; only add once call volume actually
      shows DB load from it (don't build ahead of need).
- [ ] **Do not cache** call-summary writes, FAQ-gap answering, or
      discount-policy parsing — all low-frequency, correctness-over-speed.

### 6.1 Verification (Standard verification, §0.2 — items 1, 3, 5 required)
- [ ] `pytest` green.
- [ ] Real test call before/after cache introduction — confirm actual
      latency improvement (measure, don't assume caching helped).
- [ ] **Explicitly simulate a cache miss/failure** (cold cache, Redis
      unreachable) and confirm the call still completes correctly via
      direct DB read — cache must fail open, never fail closed.
- [ ] Confirm cache invalidation actually fires on the relevant FAQ/pricing
      edit endpoints — stale cached data silently serving an old discount
      or an old FAQ answer is a quality regression, not just a latency one.
- [ ] Sign off with ✅/❌ + note. This is the last task in the plan.

---

## Suggested build order

0. **Conversation Memory / property-lock bug fix (section 2)** — moved to
   first: this is a **confirmed, reported production bug**, not a
   speculative improvement, and it's small in scope (a `ConversationState`
   object plus a fallback chain in two handlers, no schema/migration, no
   new UI). Ship and verify this before anything else in the plan.
1. **Host Memory schema + parse endpoint + validation tab** (4.1, 4.2,
   3.3) — highest leverage, and the validation tab is needed by both
   Knowledge Memory and Host Memory, so build the tab once.
2. **Wire Host Memory into pricing engine + `/pricing` tab** (4.4, 4.5).
3. **Guest Memory extension** (1.1–1.4) — needed before 4.4's repeat-guest
   discount trigger can work.
5. **Knowledge Memory semantic dedup + auto-draft** (3.1, 3.2) — builds on
   the validation tab from step 1.
6. **Property Memory seasonal notes** (5) — small, independent, low
   priority.
7. **Caching** (6) — last, once real usage patterns justify it.
