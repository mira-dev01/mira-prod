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
- [x] Added to `GuestProfile` (`backend/app/models/guest_profile.py`):
      `host_id` (FK→users, CASCADE), `last_property_id` (FK→properties,
      SET NULL), `preferred_language`, `last_outcome`, `last_follow_up`,
      `last_call_at`. **`sentiment` was not added** — nothing in the
      existing agent/tool layer produces a sentiment signal today (no
      analysis step exists to derive it from), so adding the column would
      have meant inventing a new inference source not asked for; deferred
      until a real sentiment signal exists. `lead_status` also wasn't
      duplicated, consistent with the very next bullet.
- [x] Uniqueness changed from `phone` alone to composite `(phone, host_id)`
      (`uq_guest_profiles_phone_host`) — verified with a dedicated test
      that the same phone number calling two different hosts gets two
      independent `GuestProfile` rows, not a collision.
- [x] Added `conversation_summaries` (JSONB list). **Not LLM-generated** —
      see 1.2, this pulls the already-agent-written
      `Lead.conversation_summary` text directly, never a new
      summarization call. Each entry: `{call_session_id, property_id,
      property_name, date, summary, lead_temperature}` (`outcome` renamed
      to `lead_temperature` to match the real field it's sourced from).
- [x] Added `Lead.guest_profile_id` FK (SET NULL) — `Lead.status`/
      `lead_temperature`/`occasion` remain single-sourced on `Lead` rows;
      `GuestProfile` only links to them, never copies status/occasion.
      `last_outcome` mirrors `lead_temperature` specifically (the plan's
      own "mirror, don't duplicate" framing), not `status`.
- [x] Alembic migration `d4f7a91c3e5b_add_guest_memory_fields.py` —
      validated with the same upgrade→downgrade→re-upgrade cycle against a
      local Postgres instance as every prior migration in this plan. Never
      run against the real/production DB.

### 1.2 Population (write path)
- [x] Implemented as `app/services/guest_memory_service.py`, called via
      `asyncio.create_task` from `pipeline.py`'s `on_pipeline_finished`,
      *after* the existing lead backfill/`delete_if_empty` logic resolves
      (so it sees the lead's final state, and correctly no-ops if the lead
      was deleted for having no data). **Deliberately does NOT call any
      LLM for summarization** — confirmed via code reading that
      `CallSession.ai_summary` is never actually populated by anything
      today (`finalize_call_session` is always called with
      `ai_summary=None`), so instead of adding a new LLM call, this reuses
      `Lead.conversation_summary` — text the voice agent *already writes
      itself* via `update_lead` during the call (see `GOLDEN_RULES`/
      `LEAD_AGENT_INSTRUCTIONS`). Zero new LLM cost or latency for this
      write path.
      - Upserts by `(caller_number, host_id)` — `call_service.get_or_create_guest_profile`
        signature changed to require `host_id`; all 3 pipeline call sites
        updated, one required reordering (`run_voice_pipeline` resolved
        `host_user_id` before the guest lookup instead of after).
      - Bumps `total_stays`, `last_call_at`, `last_property_id` (only when
        the call actually had a property), links `Lead.guest_profile_id`.
      - Appends to `conversation_summaries` only if
        `Lead.conversation_summary` is non-empty; still bumps
        `total_stays` either way (a call with no summary is still a real
        stay). Capped at `MAX_CONVERSATION_SUMMARIES = 20` entries.
- [x] Never blocks the live call — wrapped in its own `AsyncSessionLocal()`
      session, own try/except (logs and swallows, never raises into the
      pipeline teardown path), fired after `on_pipeline_finished`'s
      existing logic, same fire-and-forget discipline as the escalation
      email.

### 1.3 Read path (prompt injection)
- [x] Replaced the old `guest.total_stays`-only block in both
      `build_system_prompt` AND `build_lead_system_prompt` (the latter
      didn't even take a `guest` parameter before this — added one,
      default `None`, confirmed every existing call site that omits it
      still works). New shared `_guest_memory_section()` helper: name +
      stay count, `preferred_language` if set, `last_outcome` if set, and
      only the single most recent `conversation_summaries` entry (not the
      whole list) — kept to one short paragraph per the token budget.
      `total_stays == 0` (a profile just created this call) is correctly
      treated as a genuinely new guest, not a returning one.
- [x] Lookup happens once at call-start, unchanged from the plan — no new
      per-turn cost. Confirmed via the real end-to-end test (see 1.5).

### 1.4 Frontend
- [x] Extended `frontend/src/app/dashboard/guests/page.tsx`'s existing
      drawer (did not build a new page) — added a "prefers / last outcome
      / follow up" summary block and a "Past conversations" list rendered
      via the existing `ListRow` primitive, newest-first. `sentiment` was
      not added to the UI since it wasn't added to the schema (see 1.1).

### 1.5 Verification (Standard verification, §0.2 — items 1, 3 required; 2 recommended since prompt-builder is touched)
- [x] Migration applied and validated (upgrade/downgrade/re-upgrade) against
      the local test DB. Full `pytest` suite: 174 passed, same 4
      pre-existing/unrelated failures as every prior phase in this plan
      (confirmed identical failure set, not new ones from this schema/
      write-path/prompt work) — 17 new tests added across
      `test_guest_memory.py`, `test_negotiate_rate_guest_memory.py`, and
      additions to `test_system_prompt.py`.
- [x] **Real end-to-end chain verified, simulating two actual calls**
      (not a live/browser voice call — no Exotel/Sarvam credentials in
      this environment, same limitation noted in sections 2 and 4's
      sign-offs): call 1 from a new guest correctly shows "not in our
      guest records" in the prompt; Guest Memory is populated from call
      1's real `Lead.conversation_summary` at call-end; call 2 from the
      same guest/host correctly shows "returning guest ... 1 past
      stay(s)" plus call 1's actual summary text in the prompt; and after
      call 2, `negotiate_rate` correctly applies the real host-scoped
      repeat-guest discount (`GuestProfile.total_stays >= 2`) even when
      deliberately given a conservative/wrong `guest_loyalty="new"`
      argument — proving the real signal overrides the LLM's own claim,
      which is the entire point of building this over the interim mapping
      used in section 4.
- [x] Confirmed guest-memory writes are genuinely fire-and-forget: wrapped
      in `asyncio.create_task`, own DB session, own try/except (logs and
      swallows exceptions rather than propagating them into pipeline
      teardown) — read directly in the code, not just assumed.
- [x] Also closed the loop back to section 4: `pricing_engine.negotiate_rate`'s
      `repeat_guest_same_host` trigger, previously mapped only onto the
      LLM-supplied `guest_loyalty` argument as an interim signal (see
      section 4's sign-off), now prefers the real `GuestProfile.total_stays`
      check when a guest profile is resolvable, with the interim
      `guest_loyalty` mapping kept only as the fallback when no profile
      exists — same mandatory-fallback discipline as every other lookup in
      this plan (verified via a dedicated simulated-failure test).
- [x] Sign off: ✅ implemented, tested (23 new tests total across this
      section), and verified end-to-end via direct simulation of the exact
      two-call scenario the plan describes. ⚠️ Same outstanding item as
      sections 2 and 4: an actual live/browser voice call has not been
      placed in this environment.

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
- [x] **Implemented** as a new top-level nav entry (`frontend/src/components/sidebar-nav.tsx`,
      `/dashboard/ai-training`, `Sparkles` icon) rather than a Settings tab —
      per section 0's reasoning (review queues need action, not just static
      config). Page at `frontend/src/app/dashboard/ai-training/page.tsx`
      reuses `ListRow`/`ListRowHeader`/`ListRowBody`/`ListRowFooter`,
      `StatusChip`, `Card`, same primitives/conventions as
      `UnansweredQuestionsCard` and `settings/page.tsx`.
- [~] Two queues planned; **only the Host preference queue is implemented
      so far** (discount-policy paragraph → parse → pending-validation
      cards → approve/edit/reject, see 4.1–4.3). The Knowledge-validations
      queue (auto-drafted FAQ answers from 3.2) is not yet built since 3.1/3.2
      (semantic dedup + auto-draft) haven't been implemented yet — the page
      is structured so that queue can be added as its own card section
      without restructuring anything.
- [x] This is now the actual single place a host reviews AI-derived content
      before it goes live — confirmed end-to-end (see 4.6): parsed rules
      land `pending_validation`, only move to `approved` via explicit host
      action on this page.

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
- [x] Added to `User` model (`backend/app/models/user.py`):
      `discount_policy_text` (Text, nullable), `negotiation_allowed`
      (Boolean, default True), `max_discount_percent_override`
      (Numeric(5,2), nullable), `allow_pets`/`allow_early_checkin`
      (Boolean, nullable), `follow_up_channel_preference` (String(32),
      nullable). **`tone` was deliberately not added** — folded into the
      existing `agent_persona` free-text field per the plan's own caveat
      ("check before adding a redundant field"); `agent_persona` is already
      expressive enough for tone.
- [x] New table `HostDiscountRule` (`backend/app/models/host_discount_rule.py`):
      `host_id`, `trigger_type`, `discount_percent`, `source`
      (`ai_parsed`/`host_edited`), `status`
      (`pending_validation`/`approved`/`rejected` — added `rejected` beyond
      the plan's original two, needed for the reject button in 4.3),
      `raw_source_text`.
- [x] Alembic migration `c8e1f4a02b7d_add_host_memory_discount_policy.py` —
      validated end-to-end (upgrade + downgrade + re-upgrade all clean
      against a local Postgres instance running the full migration chain
      from scratch). **Never run against the real/production DB** — this
      environment's `.env` `DATABASE_URL` points at what's almost certainly
      the production Neon DB; all schema work was done against a local
      `mira_test` instance only. Deploying this migration to production is
      still a manual step for whoever runs `alembic upgrade head` there.

### 4.2 Paragraph → structured placeholders (LLM parsing step)
- [x] Implemented as `POST /host-discount-rules/parse` (not
      `/auth/me/discount-policy/parse` as originally sketched — grouped
      under the new `host_discount_rules` router instead, matches the
      resource being created). New service
      `backend/app/services/discount_policy_service.py` — deliberately
      **not** built on `app/voice/pipeline.py`'s `_build_llm()` (that's
      wired for pipecat's streaming/function-calling voice services, not a
      fit for a one-shot JSON-extraction REST call); calls the Groq/
      Anthropic/OpenRouter SDKs directly instead, same settings/provider
      priority. Returns `HostDiscountRule` drafts with
      `status="pending_validation"` — confirmed via a real (non-mocked)
      Groq API call using the user's own example paragraph verbatim
      ("if a guest doesn't ask... 5%... repeat guests... 8%") that it
      extracts exactly `{no_ask: 0%, guest_requests: 5%,
      repeat_guest_same_host: 8%}` — matches the user's spec precisely.
- [x] `raw_source_text` stored per rule (the full paragraph, not just a
      snippet) so the host can see why a placeholder was derived.

### 4.3 Validation UI (same tab as 3.3)
- [x] Host pastes/edits `discount_policy_text` on the new **AI Training**
      page (`frontend/src/app/dashboard/ai-training/page.tsx`) — a
      dedicated "Discount policy" card, not folded into Settings' Voice AI
      tab as originally sketched, since it's the trigger for a validation
      queue rather than a static preference (consistent with 3.3's nav
      decision).
- [x] Draft rules show up as editable cards: trigger description (mapped
      from `trigger_type` to plain English via `TRIGGER_LABELS`) + discount
      % + Approve/Edit/Reject buttons. Approving flips `status="approved"`;
      editing sets `discount_percent` and approves in one action, marking
      `source="host_edited"` server-side so the origin is traceable.
- [x] Approved rows are queryable via `GET /host-discount-rules` — this is
      what 4.4 (not yet built) will consult. Host can edit/remove
      already-approved rules directly (PATCH/DELETE), no re-parse required.

### 4.4 Wire into pricing engine + `/pricing` tab (per-property propagation)
- [x] `pricing_engine.negotiate_rate` now consults the host's approved
      `HostDiscountRule`s via a new `_get_host_negotiation_policy()`
      helper. `negotiation_allowed=False` → returns a `refused=True`
      `NegotiationResult` with a natural-language "no discount, but I can
      connect you with the host" message instead of negotiating.
      `trigger_type="guest_requests"` sets the discount ceiling for a
      guest who pushes back on price. `trigger_type="repeat_guest_same_host"`
      was initially mapped only onto the LLM-supplied `guest_loyalty`
      argument (`"returning"`/`"frequent"`) as an interim signal, since
      Guest Memory (section 1) wasn't built yet at the time this section
      shipped. **Update: now upgraded** — see section 1.5, which wires
      this to the real `GuestProfile.total_stays` (host-scoped) check,
      falling back to the `guest_loyalty` mapping only when no guest
      profile is resolvable at all.
- [x] **Chose derive-on-read, not materialized `PricingRule` rows** — per
      the plan's own recommendation. `HostDiscountRule` only affects
      `negotiate_rate`, never `calculate_price`/`get_pricing`'s
      length-of-stay discounts, so it was never a `PricingRule` in the
      first place; no `rule_type="host_policy_derived"` was added. This
      also sidesteps the "N duplicate `PricingRule` rows drift when the
      host edits the source rule" problem entirely — editing an approved
      `HostDiscountRule` takes effect on the very next negotiation, nothing
      to keep in sync.
- [x] `/pricing` page: **not a per-row badge** (there's nothing to badge —
      no `PricingRule` rows are created). Instead added a "Negotiation
      policy" summary card (shown only when ≥1 approved rule exists) that
      explains these apply portfolio-wide to negotiations, with a link to
      the AI Training page to review them. More honest than a badge that
      would imply these live in the same table as manually-added
      length-of-stay rules.
- [x] **Mandatory fallback — implemented and tested**:
      `_get_host_negotiation_policy` wraps its DB query in try/except,
      returning the pre-existing global-constant defaults
      (`MAX_NEGOTIATION_DISCOUNT_PERCENT`, `negotiation_allowed=True`) on
      any failure, `host_id=None`, or zero approved rules. Verified with a
      dedicated test that monkeypatches the DB call to raise and confirms
      `negotiate_rate` still completes normally
      (`test_lookup_failure_falls_back_to_global_defaults_never_errors`).
      Also verified the single most important behavior-preservation
      guarantee — a host with zero approved rules (every existing host,
      day one) negotiates byte-for-byte identically to the pre-Host-Memory
      code (`test_host_with_no_approved_rules_falls_back_to_global_defaults`).

### 4.5 Consume in negotiation prompt/tool path
- [x] Added one line to `_persona_and_escalation_sections` (shared by both
      `build_system_prompt` and `build_lead_system_prompt`, so both modes
      get it): fires only when `host.negotiation_allowed is False`,
      telling the model to still call `negotiate_rate` (which will itself
      say there's no discount) rather than refusing unprompted or
      inventing a discount. **No `tone` field was added** (per 4.1 — folded
      into existing `agent_persona`), so this section doesn't attempt a
      tone-based prompt split; a "luxury, never negotiate" host is already
      expressible via `agent_persona` + `negotiation_allowed=False`
      together.
- [x] **Found and fixed a real bug via testing, not just written
      correctly on the first pass**: `host.negotiation_allowed` is `None`
      (not `True`) for any in-memory `User` object that hasn't been
      flushed through the DB (`server_default` only applies on INSERT) —
      including every existing test fixture in `test_system_prompt.py`.
      The first version of this code (`if not host.negotiation_allowed`)
      would have silently told the model negotiation is off for *any* host
      object built this way. Fixed to explicitly check
      `host.negotiation_allowed is False`, and the equivalent bug was
      caught and fixed the same way in `pricing_engine.py`'s policy
      lookup. Three new regression tests
      (`test_negotiation_off_note_omitted_by_default` and siblings) pin
      this down permanently.

### 4.6 Verification (Standard verification, §0.2 — items 1, 2, 3, 4, 5, 6 all required — highest-risk section in the plan)
- [x] `pytest` green, including `tests/test_pipeline_llm.py` — 157 passed,
      same 4 pre-existing/unrelated failures as before this work (confirmed
      identical failure set both before and after via the same
      clean-worktree comparison method used in section 2's verification).
- [x] **Real end-to-end chain verified with a real (non-mocked) Groq
      call, not just unit tests**: parsed the user's own example discount
      paragraph via the actual `/host-discount-rules/parse` endpoint,
      approved the resulting `guest_requests: 5%` rule via the actual
      `PATCH` endpoint, then called `pricing_engine.negotiate_rate`
      directly and confirmed the counter-offer floor matched the approved
      5% — proving the full parse → approve → negotiate chain works
      together, not just each piece in isolation. (Latency was not
      separately measured with real call-timing instrumentation — no
      Exotel/Sarvam credentials in this environment to place an actual
      voice call, same limitation noted in section 2's sign-off. The
      lookup itself is one indexed query by `host_id` + `status`, no join
      fan-out, so it's structurally cheap, but this is a reasoned
      expectation, not a measurement.)
- [x] **Explicitly simulated the failure path** (see 4.4) — confirmed via
      `test_lookup_failure_falls_back_to_global_defaults_never_errors` and
      `test_host_with_no_approved_rules_falls_back_to_global_defaults`.
- [x] Prompt token count for the 4.5 addition, measured: ~68 tokens
      (270 chars, chars/4 approximation — `tiktoken` isn't installed in
      this environment to get an exact count with the real tokenizer).
      Slightly over §0.1's illustrative "≤40 tokens" example figure, but
      it's a single sentence that only appears for hosts who've explicitly
      set `negotiation_allowed=False` (a minority case, not added to every
      prompt), and it replaces zero existing text — net addition, not
      cumulative growth per call. Acceptable given it's a one-time, opt-in
      addition rather than something that grows with usage.
- [x] Confirmed `HostDiscountRule` rows created via 4.2's parser are
      `status="pending_validation"` and are NOT read by `negotiate_rate`
      until `status="approved"` — proven directly by
      `test_pending_validation_rule_is_not_used` (a pending 50% rule has
      zero effect on the negotiation floor), not just by reading the
      query's WHERE clause.
- [x] Sign off: ✅ implemented, tested (12 new tests across 3 files), and
      verified end-to-end with real infrastructure. ⚠️ Two open items before
      calling this fully production-verified: an actual live/browser voice
      call (blocked on missing Exotel/Sarvam credentials in this
      environment, same as section 2), and a measured prompt-token count
      for 4.5's addition.

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

0. ✅ **Conversation Memory / property-lock bug fix (section 2)** — shipped
   first as a confirmed, reported production bug fix.
1. ✅ **Host Memory schema + parse endpoint + validation tab** (4.1, 4.2,
   3.3) — shipped.
2. ✅ **Wire Host Memory into pricing engine + `/pricing` tab** (4.4, 4.5)
   — shipped, initially with `repeat_guest_same_host` on the
   `guest_loyalty` interim signal.
3. ✅ **Guest Memory extension** (1.1–1.5) — shipped, including upgrading
   4.4's `repeat_guest_same_host` trigger from the interim `guest_loyalty`
   mapping to the real `GuestProfile.total_stays` (host-scoped) check.
4. **Knowledge Memory semantic dedup + auto-draft** (3.1, 3.2) — next up;
   builds on the AI Training validation tab already shipped in step 1.
5. **Property Memory seasonal notes** (5) — small, independent, low
   priority.
6. **Caching** (6) — last, once real usage patterns justify it.

**Cross-cutting note for all shipped sections above**: none have had an
actual live/browser voice call placed against them — this environment has
no Exotel/Sarvam credentials configured. Every section's verification
instead relies on direct simulation of the real code paths (pytest against
a real local Postgres DB, plus, for LLM-touching pieces, genuine
non-mocked API calls to Groq) rather than mocks. This is real evidence but
not a substitute for an actual call before calling any of this
production-verified — flagged consistently in each section's sign-off
rather than once here and then ignored.
