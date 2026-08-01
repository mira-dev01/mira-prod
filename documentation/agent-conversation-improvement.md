# Mira Conversational Behaviour — Superhost-Quality Task Sheet

Working checklist for making Mira's live voice conversation behave like an experienced Airbnb
Superhost concierge — for any host, any property, any language mix — rather than a
prompt-compliant chatbot. Execute **one task at a time**, in order within a phase; phases can be
reordered if a dependency below says otherwise. This file is the operational tracker — check items
off with dated, one-line reverify notes as you go, same convention as `tasks.md` /
`memory-architecture-plan.md`.

Research basis for every claim below: full read of `backend/app/prompts/system_prompt.py` (834
lines — every GOLDEN_RULES clause, both prompt builders, all helper sections),
`backend/app/voice/conversation_state.py`, `backend/app/voice/pipeline.py`, `backend/app/voice/
language_sync.py`, all six pipeline guard modules (`repetition_guard.py`,
`meta_commentary_guard.py`, `escalation_phrase_guard.py`, `property_recommendation_guard.py`,
`premature_end_call_guard.py`, `redundant_context_guard.py`), `backend/app/services/
tool_handlers.py`, the full `backend/app/services/property/retrieval/` package (`orchestrator.py`,
`filter_builder.py`, `sql_search.py`, `semantic_search.py`, `ranking.py`, `context_builder.py`),
`backend/app/services/property/{card,pitch_formatter}.py`, `backend/app/schemas/tool.py`,
`docs/agents.md`, and `documentation/memory-architecture-plan.md` (which already extended
`ConversationState` once, for the property-lock bug, and explicitly deferred
`booking_stage`/`slots_filled`/`last_intent` "until a task actually needs them" — this file is that
task). Cross-checked with a second, independent full pass over `pipeline.py`'s message-construction
path (`LLMContext`/`LLMContextAggregatorPair`), which surfaced two additional confirmed gaps folded
in below: in-call history is never truncated/summarized (Phase 4a), and `recommend_properties` has
no date-availability awareness at all — `RecommendPropertiesArgs` (`schemas/tool.py:100-106`) carries
no `check_in`/`check_out` fields, so a recommended property can turn out unavailable for the guest's
actual dates, discovered only later via a separate `check_calendar` call (Phase 2.4). Findings cited
inline as `file:line`.

Phase 0's own transcript pull (18 real calls, read-only against the production DB, 2026-07-31) then
grounded a failure catalogue (Phase 0.2) with dated, cited examples instead of hypothetical ones.
A subsequent review round against that catalogue reframed and added several items, each recorded
inline where it lands: `conversation_goal` tracking (Phase 1.5/1.6), recommendation diversity and
confidence-aware phrasing (Phase 2.5/2.6), a corrected framing of the language-adaptation gap around
honoring an explicit guest preference rather than banning Devanagari script (Phase 3.3/3.4), a new
Tool Output Fidelity phase generalizing `PropertyRecommendationGuardProcessor`'s existing pattern to
other tools (Phase 4b — the correct root-cause framing for catalogue items C1/C2), a Response Shape
Validator as the final pre-TTS gate (Phase 4.3 — the correct mechanism for C3, which is a
response-shape problem, not a repetition one), a confirmed non-gap on barge-in/interruption timing
(Phase 6.4), and quantitative response-quality metrics (Phase 7.5) rather than relying on manual
transcript review alone.

## Standing rules (apply to every task, no exceptions)

1. **Reverify after every task, before moving to the next.** Minimum bar:
   - `cd backend && pytest` passes clean against a real DB (per `CLAUDE.md` — never mock the DB).
     Compare the failing-test set against the 4 pre-existing/unrelated failures already tracked in
     `documentation/project_state.md` (`test_call_includes_duration_and_lead_name_phone`,
     `test_escalate_to_host_also_saves_lead_so_it_isnt_left_empty`,
     `test_search_faq_logs_gap_when_no_verified_answer`, `test_is_complete_short_but_punctuated`) —
     any new failure is a real regression, not noise.
   - For anything touching `system_prompt.py`: print/log the assembled prompt's token count
     before vs. after (same discipline as `memory-architecture-plan.md` §0.1's token-budget rule).
     "Feels short" is not a pass condition.
   - For anything touching the live pipeline (`pipeline.py`, any guard module, `conversation_state.py`,
     `tools.py`): a real or browser-test call (`/api/v1/voice/test/offer`) judged before and after,
     not just `pytest` — pytest proves business-logic correctness, not perceived conversation quality
     or latency. If no Exotel/Sarvam credentials are available in the working environment (a
     recurring, explicitly logged limitation across every phase of `memory-architecture-plan.md`),
     say so plainly in the sign-off rather than silently skipping it — do not claim "verified" for
     something that was only unit-tested.
   - Record the result inline in this file (✅/❌ + one-line note) before checking the task off.
2. **This *is* sanctioned to touch the voice-agent path** — unlike `restructure.md`/`tasks.md`'s
   standing rule 2, which explicitly walls off `backend/app/voice/**`, `tool_handlers.py`,
   `pricing_engine.py`, `system_prompt.py`, etc. for the *dashboard* redesign effort. This plan's
   entire scope is that path. Do not let those other files' "never regress the voice agent" rule be
   read as "don't touch it" here — it means "touch it carefully, prove it still works," which is
   what the verification bar above is for.
3. **Prefer code-level enforcement over new prompt paragraphs, per the codebase's own established
   pattern.** Six guard processors already exist
   (`app/voice/{repetition,meta_commentary,escalation_phrase,property_recommendation,
   premature_end_call,redundant_context}_guard.py`) specifically because prompt-only fixes for
   several of these exact behaviour classes (repetition, narrator text, banned phrasing, silent tool
   results) were tried first and kept regressing on real calls — see `docs/agents.md` and the
   2026-07-27 entries in `project_state.md`. Every task below that risks the same
   "probabilistic compliance" failure mode is scoped as a state/guard change, not a paragraph
   added to `GOLDEN_RULES` and hoped for. Where a prompt change alone is genuinely sufficient
   (because no code path can enforce it structurally), that's called out explicitly with the reason.
   **Phase 4.3 (Response Shape Validator) and Phase 4b (Tool Output Fidelity) are this same pattern
   generalized**: `PropertyRecommendationGuardProcessor` already does exactly this for one specific
   case (verifying the model actually named a recommended property, not something it invented) —
   those two new phases extend the identical mechanism to the other structured facts (capacity,
   price, availability) and to final-response shape, rather than inventing a new approach.
4. **`ConversationState` (`app/voice/conversation_state.py`) is the mechanism for requirement #7
   (Conversation State Awareness), not a new subsystem.** It already exists, is already threaded
   through `build_voice_tools`'s closure exactly like `call_session_id`/`property_id`, and was
   already extended once (`selected_property_id`/`selected_property_name` for the property-lock
   bug, `memory-architecture-plan.md` §2.2). Phase 1 below extends the same dataclass — do not
   introduce a second state object or a DB table for in-call state (same non-goal as
   `memory-architecture-plan.md` §2.4: "not a DB table, not Redis... a plain Python object closed
   over by the tool functions is sufficient unless the pipeline ever becomes multi-process per
   call, which it isn't today"). **The point of `ConversationState` throughout this plan is to
   reduce how much the model has to re-infer from long conversational history, improving
   reliability without materially increasing prompt size** — every field added to it (Phase 1's
   slots/goal, Phase 3's language state) replaces an "ask the model to correctly recall this from
   the transcript" dependency with a small, pre-computed, always-correct fact. That's the mechanism,
   consistently, across every phase that touches it.
5. **Every task is additive/behavioural, not a rewrite.** `GOLDEN_RULES` (`system_prompt.py:103-418`)
   already correctly states most of the target behaviour in prose — filler-turn handling, re-ask
   avoidance, one-question-per-turn, Hinglish tone, escalation phrasing, natural close detection.
   Read each relevant clause before writing a new one; extend or make it enforceable in code, don't
   duplicate it.
6. Never invent host-specific or property-specific logic. Every change must generalize across any
   host's portfolio, any property type, any city — per the goal's explicit framing ("not one
   specific property or host").

---

## Phase 0 — Baseline instrumentation (do first; every later phase's verification depends on this existing)

Without a way to measure "did this actually get better," every later phase's sign-off degenerates
into "read the diff, looks right" — exactly the failure mode `memory-architecture-plan.md` §0.1
already calls out for this codebase. Build the measurement surface before changing behaviour.

- [x] **0.1 — Capture a baseline transcript set.** ✅ Done (2026-07-31). Pulled via a read-only
      script (`SELECT` only, no writes) against the real Neon production DB, per explicit user
      confirmation to query production directly rather than standing up a local DB — `CallSession`
      rows with `transcript IS NOT NULL AND length(transcript) > 400`, ranked by transcript length
      across the whole table (not just the most recent N — an early attempt limited to the most
      recent 40 rows found only 11 substantive calls, since recent rows skew short/thin; sorting by
      length across all 240 rows surfaced the real substantive set). 18 calls selected for diversity
      across `call_type` (`BOOKING_LEAD`, `GUEST_SUPPORT`, `GENERAL_QUERY`, `EXISTING_BOOKING`,
      `UNKNOWN`), spanning 2026-06-29 through 2026-07-31 (today). Transcripts saved to a local
      scratchpad directory only (never committed to the repo — real guest PII, names/phone numbers,
      appears in raw form).
      - Verify: ✅ confirmed 17/18 calls show a Hinglish/code-switching hint (Latin-script keyword or
        Devanagari-range characters), and multiple calls show 2+ properties recommended in one
        response (e.g. the 3-property list in the 2026-07-20 anniversary-trip call, catalogue item
        C3 below) — both conditions this task asked to confirm before relying on the sample.
- [x] **0.2 — Failure catalogue, built from real transcripts in the 0.1 sample plus
      `project_state.md`'s own incident log.** Every item below is either a live, currently-unfixed
      pattern observed directly in a real transcript, or a confirmed-fixed historical incident kept
      as a **before** reference point (marked accordingly) so Phase 7.1 can confirm it stays fixed.
      Call IDs are internal references into the local (uncommitted) transcript sample, not meant to
      be looked up by anyone without DB access.

      **Live/current, unfixed as of this sample (highest priority for this plan):**
      - **C1 — Recommendation violates a stated guest-count constraint, still happening.**
        Call `99dbe780…` (2026-07-29, `BOOKING_LEAD`): guest states "Four, four people" in turn 2.
        Several turns later, the agent recommends "The Cabana in South Goa... fits two guests" and
        "The Azure in Colva... sleeps two" — both violating the guest's own stated count. The guest
        has to catch it themselves: "But we are four people coming, why are you suggesting two guest
        stays?" This is the exact live gap Phase 1.4/2.4 target — `state.slots` doesn't yet exist to
        backfill a missing `num_guests` into a `recommend_properties` call that omitted it.
      - **C2 — `base_price = 0` spoken directly as "free of charge per night."** Call
        `24bdc8d7…` (2026-07-20, `BOOKING_LEAD`): the recommendation pitch line for two properties
        reads "Cabana... free of charge per night" and "...Villa... also free per night." This is a
        **different code path** than the ₹0 guard `project_state.md`'s 2026-07-23 entry already
        fixed — that fix is inside `handle_get_pricing`/`handle_negotiate_rate` only; this is
        `format_property_pitch_line`/`PropertyCard.base_price` in the **recommendation** pitch,
        reading the same underlying zero `base_price` data (`project_state.md`'s "Known issues"
        section already flags 8 such properties) but through an entirely unguarded code path. Real,
        live, unfixed gap — worth a narrow follow-up fix alongside Phase 2 (see Phase 2's task list).
      - **C3 — Multiple assistant turns concatenated into one wall of text with no guest turn in
        between**, recurring across nearly every transcript in the sample, not an isolated incident.
        Sharpest example: call `d5a808a4…` (2026-07-31, **today**): "...Which one sounds
        interesting?Got it, Abhaya. Which of those two would you like to explore further?We'll check
        availability and pricing for the one you choose. Which property would you like to go ahead
        with?" — three distinct questions run together with no punctuation/pause between them and no
        guest reply in between. Also visible in call `d6130adc…` (2026-07-30) at multiple points
        (e.g. "...Deepika, could you please share the name...मैं Mira हूँ—क्या मैं इस बुकिंग को
        आपके...रखूँ?"). This is a repetition/turn-boundary problem GOLDEN_RULES' "ONE RESPONSE PER
        TURN" rule (`system_prompt.py:210-221`) already explicitly bans, still occurring live as
        recently as today's date — a strong candidate for Phase 4's scope, and worth flagging to
        `RepetitionGuardProcessor`'s maintainers as a shape it may not currently catch (near-duplicate
        *word overlap* detection wouldn't flag two genuinely different questions concatenated
        together, only a repeated one).
      - **C4 — Goodbye said, conversation continues normally, goodbye said a second time, a second
        escalation fires for what looks like a routine follow-up.** Call `d6130adc…` (2026-07-30):
        closing line delivered ("Thanks so much for calling — have a wonderful day!") at one point,
        guest immediately asks about different dates, agent answers normally, then delivers the exact
        same closing line again a few turns later, then `escalate_to_host`'s safe line fires again
        right after. The call *does* recover functionally (the guest's new question does get
        answered), but the double goodbye and second escalation for what reads as a routine
        date-availability follow-up is exactly the "no closing_state" gap Phase 5 targets — nothing
        tracks that a farewell was already delivered once this call reopened.
      - **C5 — Guest directly asks "can you speak Hindi?" mid-call; the reply doesn't adopt Hindi.**
        Call `d5a808a4…` (2026-07-31, today): guest asks "आप हिंदी में बोल सकते हो?" mid-call; the
        agent's very next turn stays in English ("Perfect, checking in on 2 August..."). A directly
        stated language preference, not just an inferred one from code-switching, going unhonored —
        a sharper version of Phase 3's targeted gap than a passive mirroring failure would be.
      - **C6 — Mid-call "hello" gets a near-verbatim repeat of the prior answer, not the "I'm here"
        rule.** Call `4a0fac31…` (2026-07-21, `GUEST_SUPPORT`): guest says "Hello" after being given
        a neighborhood/attractions answer; the agent's next turn re-delivers essentially the same
        attractions list instead of the short acknowledgement GOLDEN_RULES' own rule
        (`system_prompt.py:266-275`) already specifies for exactly this case. Confirms this
        historically-"confirmed live" bug (per the rule's own docstring citing an earlier identical
        incident) is not fully resolved by the prose rule alone — supports Phase 4's move to make
        "already said" checks state-backed rather than prose-only.

      **Historical, already fixed — kept as before-references so Phase 7.1 can confirm they stay fixed:**
      - **H1 — "Let me loop in the host directly!" spoken verbatim.** Calls `8a4975d0…` (2026-06-29)
        and `24bdc8d7…`/`d09fca4e…` (both 2026-07-20) all show this exact banned phrase, multiple
        times per call. All three predate `EscalationPhraseGuardProcessor`'s unconditional-replacement
        fix (`project_state.md`, 2026-07-27). Confirmed **not** recurring in the two most recent
        escalation examples in this sample (`99dbe780…` 2026-07-29 and `d6130adc…` 2026-07-30 both
        show the correct fixed safe line, "Okay, I've noted your details...") — good direct evidence
        the fix holds in current calls, kept here as the baseline this plan must not regress.
      - **H2 — Degenerate `".. .. .."` fragment flood plus raw chain-of-thought leaking into spoken
        text.** Call `95ff8c30…` (2026-07-27, 04:21 UTC): hundreds of `".. .. .."` fragments in one
        turn, followed by a turn that visibly leaks planning text ("We need to recommend properties
        for 10 guests... Use recommend_properties with num_guests maybe 5?... Ask budget first.").
        This is the exact incident `project_state.md`'s 2026-07-27 entry describes fixing
        (`max_completion_tokens=400` + `RepetitionGuardProcessor`) — this call's timestamp (04:21 UTC)
        is consistent with predating that same day's fix. **Not fully clear this is closed**: call
        `b5d36092…` (2026-07-29, two days after the fix) still shows one `".. .. .."` fragment flood
        (shorter than H2's, but the same shape) — flagged explicitly as a residual-risk item for
        Phase 7.1 to specifically re-check, not assumed resolved just because a similar fix landed.
      - **Verify: every later phase should trace back to at least one item above** — Phase 1 → C1;
        Phase 2 → C1, C2; Phase 3 → C5; Phase 4 → C3, C4 (repetition angle), C6; Phase 5 → C4
        (closing-state angle); Phase 7.1 re-checks all of C1-C6 plus H1/H2's residual-risk flag on a
        fresh transcript pull. If a later phase doesn't trace to anything here, question whether it's
        actually needed before building it.
- [x] **0.3 — Prompt token budget baseline.** ✅ Measured (2026-07-31), against the real pilot host
      (Pause Projects / Siddhartha Kathpalia, 17-property portfolio — the largest real portfolio in
      the DB, so this is a realistic worst-case rather than an optimistic thin-demo-data case).
      `tiktoken` is not installed in this environment — same honesty as
      `memory-architecture-plan.md §4.6`'s own note — so these are `chars/4` approximations, not
      exact provider-tokenizer counts:
      - **Guest Support** (`build_system_prompt`, representative property = "Olive-Wake up by the
        forest @ Pause Project 1bhk", chosen as the richest-content property in the portfolio by
        house-rules+neighborhood-info+FAQ+amenities length, i.e. a realistic upper bound, not a thin
        case): **35,810 chars, ~8,952 tokens**.
      - **Lead Agent** (`build_lead_system_prompt`, full 17-property portfolio): **39,600 chars,
        ~9,900 tokens**.
      - **`GOLDEN_RULES` alone (shared by both modes) is 29,400 chars, ~7,350 tokens** — this is the
        dominant cost in both prompts: ~82% of the Guest Support total, ~91% of the Lead Agent total.
        Property/portfolio-specific content is comparatively small: ~1,159 tokens beyond instructions
        for the one richest Guest Support property, ~845 tokens beyond instructions for the entire
        17-property Lead Agent portfolio listing (expected, given `build_lead_system_prompt`'s own
        documented choice to omit amenities/USP per-property to control exactly this cost,
        `system_prompt.py:793-806`).
      - **Why this matters for every later phase**: the fixed instruction baseline is already large
        relative to any single new addition this plan proposes. A few hundred tokens of real signal
        (Phase 1.3's slots block, Phase 3.2's language-state line) is a meaningful, justified addition
        against this baseline — but it also means the *existing* `GOLDEN_RULES` block is the actual
        lever if prompt cost ever needs to come down, not anything this plan adds. Not in this plan's
        scope to shrink `GOLDEN_RULES` itself (each clause exists because of a specific confirmed-live
        bug, per Standing Rule 5) — noted here only so Phase 7.3's final check has real context for
        "is the cumulative addition acceptable," not a number in a vacuum.

---

## Phase 1 — Extend `ConversationState` to carry real slot/lifecycle state (requirement #7, #9, #10)

**Why this phase is foundational**: today `ConversationState` (`app/voice/conversation_state.py:14-24`)
tracks exactly one thing — `selected_property_id`/`selected_property_name`. Every other piece of
"what's already been collected/decided" (dates, guest count, budget, whether recommendations were
shown, whether the guest accepted one, whether escalation already happened, whether goodbye was
already said) lives *only* implicitly in the LLM's own message history — the model has to correctly
re-derive it from re-reading the transcript every single turn, which is exactly the class of failure
GOLDEN_RULES's own re-ask rules (`system_prompt.py:240-254`) already fight prompt-only, imperfectly.
`memory-architecture-plan.md:326-338` sketched exactly this (`booking_stage`, `slots_filled`,
`last_intent`) and deliberately deferred it — "can be added back when a task actually needs them."

- [x] **1.1 — Add slot-tracking fields to `ConversationState`.** ✅ Done (2026-07-31).
      `app/voice/conversation_state.py` extended with `slots: dict[str, Any]`,
      `recommendations_shown: list[dict[str, Any]]` (name/property_id/price/guests dicts, matching
      `PropertyRecommendationGuardProcessor`'s own existing shape rather than a new one),
      `guest_accepted_property_id: str | None`, `escalated: bool`, `closing_state: Literal["open",
      "farewell_pending", "closed"]`, and (pulled forward from 1.5, since both landed in the same
      pass) `conversation_goal`. New setter methods (`set_slot`, `record_recommendations`,
      `mark_checking_availability`, `mark_negotiating`, `mark_escalated`) replace direct field
      writes so every mutation path also recomputes `conversation_goal` consistently — `lock_property`
      itself now also sets `guest_accepted_property_id` when the locked property was one already
      shown via `recommend_properties`.
      - Verify: ✅ `tests/test_conversation_state.py` (10 new tests, all passing) — per-field slot
        updates, no-clobber on `None`, recommendation tracking, escalation freezing the goal,
        goal-derivation priority order, two independent conversations landing on different real
        goals, and confirmed two `ConversationState` instances never share state. `tests/
        test_property_lock.py`'s original 5 tests still pass unchanged against the extended
        dataclass (`lock_property`'s original signature/behavior preserved).
- [x] **1.2 — Wire slot capture into the tool wrappers.** ✅ Done (2026-07-31). `app/voice/tools.py`:
      `check_calendar`/`get_pricing`/`negotiate_rate` now call `state.set_slot(...)` for
      `check_in`/`check_out`/`num_guests` alongside their existing `state.lock_property(...)` call,
      plus `mark_checking_availability()`/`mark_negotiating()` respectively. `update_lead` (the
      primary slot-writing tool for plain-conversation-stated facts) writes all seven relevant
      fields including `phone`/`guest_name` — found and fixed a real gap during testing: the first
      pass didn't capture `phone`, caught by `test_update_lead_writes_slots_without_clobbering_earlier_fields`
      actually failing on first run, not assumed correct. `escalate_to_host` now calls
      `state.mark_escalated()`. `recommend_properties` records `state.record_recommendations(...)`
      and the four criteria fields via `set_slot`.
      - Verify: ✅ `tests/test_conversation_state_slot_wiring.py` (8 new tests, all passing) —
        confirmed per-field capture across `update_lead`/`check_calendar`/`escalate_to_host`, confirmed
        a `phone`-only later call never clobbers an earlier `num_guests`, confirmed dates land as ISO
        strings, confirmed escalation freezes `conversation_goal` against a later `update_lead` call.
- [x] **1.3 — Surface accumulated `state.slots` back into the prompt.** ✅ Done (2026-07-31),
      **implemented differently than originally sketched, for a real architectural reason found
      during implementation**: the plan text assumed this could be a static prompt section built at
      `build_system_prompt`/`build_lead_system_prompt` time — but `system_prompt` is built once,
      before the pipeline (and `ConversationState`) exist, and `context.messages[0]` is never
      rebuilt afterward (confirmed by reading `pipeline.py`'s actual message-construction path).
      Slots only become known **mid-call**, well after the prompt is fixed. Built a new pipeline
      processor instead — `app/voice/state_prompt_sync.py`'s `StatePromptSyncProcessor` — sitting
      right after `redundant_context_guard` (before `llm`), which injects/updates ONE additional
      system-role message right after the real system prompt, on every turn, never touching or
      rebuilding `messages[0]` itself (preserving Groq's prefix-cache hit on the real system prompt,
      the exact constraint this task's original text flagged as needing "a spike, not an assumption"
      for Phase 3.2 — resolved here in favor of option (b), a lightweight per-turn injection, with
      the state block itself carrying zero risk to the cached prefix since it's a wholly separate
      message). Updates the same message in place (tagged via a sentinel key) rather than appending
      a growing list of stale blocks.
      - Verify: ✅ `tests/test_state_prompt_sync.py` (8 new tests) — confirmed true no-op (zero
        content, zero tokens) when nothing is known yet; confirmed the real system prompt message is
        never mutated across turns; confirmed the state block updates in place rather than
        accumulating. **Token cost measured against the Phase 0.3 baseline**: a fully-populated
        realistic state block (8 slots + 2 recommendations + a goal hint) costs **~102 tokens**
        (409 chars, chars/4 approx) — about 1% of the ~8,950–9,900 token baseline; the empty/early-call
        case costs exactly 0. **Verified against real production data**, not just synthetic
        fixtures: ran the actual `build_system_prompt`/`build_voice_tools`/`StatePromptSyncProcessor`
        construction path against a real property/host from the DB (read-only), called the real
        `update_lead` and `recommend_properties` tool wrappers exactly as a live call would, and
        confirmed the state block appears correctly in the exact message list that would be sent to
        the LLM, immediately after the untouched system prompt. This same real run also independently
        reproduced catalogue item C2 live (`"...for ₹0 a night"` from a real zero-priced property) —
        expected and consistent, since Phase 2.0 (which fixes C2) hasn't been built yet.
      - ⚠️ **No live/browser voice call placed** — Clerk-only auth (confirmed via `app/auth/
        dependencies.py`) blocks a scripted authenticated session, and a real call requires actual
        WebRTC from a browser, neither achievable from this non-interactive environment (same
        limitation logged consistently across every phase of `memory-architecture-plan.md`). The
        real-data construction-path run above is the strongest available substitute — it exercises
        every real function in the actual call path (prompt builder, tool wrappers, the new
        processor) against real DB content, just not through an actual live audio/WebRTC session.
        Recommend a real test call once Sarvam credits are topped up (per `project_state.md`'s
        already-known account issue) before considering this fully production-verified.
- [x] **1.4 — Recommendation-constraint validation, made explicit and testable (requirement #10).**
      ✅ Done (2026-07-31). `app/voice/tools.py`'s `recommend_properties` wrapper now backfills
      `num_guests`/`budget` from `state.slots` whenever the tool call itself omits them, before
      constructing `RecommendPropertiesArgs` — confirmed by reading `filter_builder.py` directly that
      `apply_guest_count_filter` applies **zero** capacity filtering (not a lenient one) when
      `num_guests is None`, exactly matching catalogue item C1's real mechanism.
      - Verify: ✅ `test_recommend_properties_backfills_num_guests_from_state_slots` reproduces C1
        directly — `update_lead(num_guests=4)` in one call, then `recommend_properties(preferred_location="Goa")`
        with no `num_guests` arg at all in a later call — confirms only the property with
        `max_guests >= 4` is returned, not both. `test_recommend_properties_explicit_arg_still_wins_over_state`
        confirms an explicit argument (a genuine correction) still overrides the backfilled value.
        Also verified against real production data in the same live construction-path run as 1.3
        above: `recommend_properties` correctly filtered to guest-count-4-or-above properties with no
        `num_guests` argument passed, using only the value `update_lead` had set moments earlier.
- [x] **1.5 — Track `conversation_goal`.** ✅ Done (2026-07-31), landed together with 1.1 (see that
      task's sign-off — both extend the same dataclass in one pass). `_recompute_goal` derives the
      goal from the strongest available signal each time a setter runs: an escalation or an
      in-progress close always wins (never silently overwritten by a later slot update); a
      `recommend_properties`/`lock_property` call sets `awaiting_selection`/`checking_availability`
      directly; absent those, the goal is derived from which of `check_in`/`check_out`/`num_guests`/
      `preferred_location`/`purpose_of_stay` are still unset, in the same priority order
      `LEAD_AGENT_INSTRUCTIONS` step 2 already uses in prose.
      - Verify: ✅ `test_conversation_goal_derives_from_missing_slots_in_priority_order` and
        `test_conversation_goal_different_real_paths_land_on_different_goals` (part of the 10 tests
        in `test_conversation_state.py`) confirm two genuinely different conversation shapes (guest
        gives everything upfront vs. one field at a time) land on their own correct real goals, not a
        single hardcoded sequence.
- [x] **1.6 — Surface `conversation_goal` into the prompt.** ✅ Done (2026-07-31), via the same
      `StatePromptSyncProcessor` built for 1.3 (one processor covers both — the plan's own framing of
      1.3/1.6 as "alongside" each other in the same block turned out to mean "the same mechanism,"
      not two separate ones). `_GOAL_HINTS` maps each `conversation_goal` value to one short sentence
      appended after the slots line in the same injected system message.
      - Verify: ✅ Covered by the same `test_state_prompt_sync.py` suite as 1.3
        (`test_build_state_block_content_includes_goal_hint` specifically). Token cost is part of the
        same measured ~102-token fully-populated figure in 1.3's sign-off (the goal hint is one
        sentence within that total, not counted separately). ⚠️ Same live-call limitation as 1.3 — no
        real/browser call placed, real-data construction-path run is the substitute evidence.

**Phase 1 sign-off (2026-07-31)**: ✅ all 6 tasks implemented. New files: `app/voice/
state_prompt_sync.py`; rewritten `app/voice/conversation_state.py`; modified `app/voice/tools.py`
(slot/goal wiring across 6 tool wrappers) and `app/voice/pipeline.py` (moved `ConversationState`
construction earlier, added `state_prompt_sync` to the pipeline stage list right after
`redundant_context_guard`). 34 new tests across 3 new test files (`test_conversation_state.py`,
`test_conversation_state_slot_wiring.py`, `test_state_prompt_sync.py`), all passing. Full suite:
439 passed (up from the Phase 0 baseline of 413), same 5 pre-existing/environment-dependent
failures as before this phase (`test_call_includes_duration_and_lead_name_phone`,
`test_escalate_to_host_also_saves_lead_so_it_isnt_left_empty`,
`test_search_faq_logs_gap_when_no_verified_answer`, `test_is_complete_short_but_punctuated`,
`test_ice_servers_stun_only_by_default`) — zero new regressions. `app.main` imports cleanly; the
user's own long-running local `uvicorn --reload` process picked up every change with no crash
(confirmed via its log: `watchfiles` "1 change detected" entries with LLM health checks continuing
normally afterward). One real architectural deviation from the original plan text, explained in
1.3's sign-off: the slots/goal block is injected by a new pipeline processor
(`StatePromptSyncProcessor`) rather than folded into `build_system_prompt`, since the prompt is
built before `ConversationState` exists and is never rebuilt mid-call — this was a "spike, not an
assumption" question the plan itself flagged, now resolved. ⚠️ Outstanding: no real/browser voice
call placed (Clerk-only auth + WebRTC, neither scriptable from this environment) — substituted with
a full construction-path run against real production property/host data, exercising every real
function in the actual call path. Recommend a genuine test call once Sarvam credits are topped up.

---

## Phase 2 — Recommendation quality: explain the "why" (requirement #3, #4)

**The concrete gap**: `PropertyCard` (`app/services/property/card.py:19-29`) and
`format_property_pitch_line` (`app/services/property/pitch_formatter.py:43-56`) carry
`spoken_name`/`property_type`/`bedroom_count`/`city`/`base_price`/`max_guests`/`top_amenities`/`usp`
— purely descriptive fields. Nothing in the pitch line connects a recommendation back to *why it
matches this guest* (group size, purpose, budget, privacy). The pitch text today reads: "Ocean View
Villa, a 3-bedroom villa with pool and parking in Goa for ₹12,000 a night, sleeps 6" — accurate, but
never says *because you asked for six friends and a pool*. `GOLDEN_RULES`'s "conversational warmth"
clause (`system_prompt.py:295-331`) tells the model to sound consultative in general, but nothing
structurally ties a specific recommendation to the specific guest constraint it satisfies.

- [x] **2.0 — Close the ₹0-recommendation-pitch gap (catalogue item C2).** ✅ Done (2026-08-01).
      `filter_builder.build_base_filters` now excludes any property with `base_price <= 0` unless
      `exact_airbnb_pricing = True` (which fetches its real price live at `get_pricing` time
      regardless of `base_price`) — implemented as its own unconditional `where` clause, deliberately
      **not** folded into the budget filter, since `base_price <= 0` trivially passes any `<=` budget
      check (confirmed by testing this exact scenario, not just assumed).
      - Verify: ✅ 4 new tests in `test_property_retrieval_sql_search.py` — zero-price excluded by
        default, zero-price with `exact_airbnb_pricing=True` NOT excluded, exclusion holds even with
        an active budget filter, plus the existing `handle_get_pricing`/`handle_negotiate_rate` ₹0
        guard tests re-run unaffected (they bypass `recommend_properties` entirely). Re-confirmed
        live: **6 properties currently have `base_price <= 0`, all 6 with `exact_airbnb_pricing =
        False`** — all 6 now correctly excluded.
- [x] **2.1 — Add a `match_reasons: list[str]` field to `PropertyCard`.** ✅ Done (2026-08-01).
      Implemented as a required field (not a mutable-default trap on a frozen dataclass) plus a new
      `match_reasons_for_card(card, args)` helper in `card.py`, called from
      `context_builder.build_recommendation_result` (which now optionally takes `args`) rather than
      from `build_property_card` itself — keeps `build_property_card`'s existing signature/behavior
      unchanged for any other caller. Checks amenity → purpose → guest-count → budget, in that
      priority order (a named amenity is the most concrete/specific reason, so it wins a slot over a
      vaguer one when the 2-reason cap forces a choice), each only appended if the corresponding
      `RecommendPropertiesArgs` field was actually supplied.
      - Verify: ✅ 11 new unit tests in `test_property_card_match_reasons.py` — one per branch
        (guest-count match/no-match, budget match/no-match, purpose match/unmapped, amenity
        match/no-match), the no-criteria-given case producing `[]`, the 2-reason cap holding even
        when every criterion matches, and amenity-reason priority confirmed directly. Found and fixed
        a real bug during implementation: the field's original sketch (`= ()` as a default) would
        have been an actual mutable-default-adjacent hazard on a frozen dataclass — corrected to a
        required field before it shipped, not caught by a test but by re-reading the diff before
        running it.
- [x] **2.2 — Wire `match_reasons` into `format_property_pitch_line`.** ✅ Done (2026-08-01). One
      added clause (`" -- {reasons joined naturally}"`), never a second sentence — empty
      `match_reasons` produces no clause at all (confirmed the pre-Phase-2 pitch shape is
      byte-identical in that case). Real example produced: "Ocean View Villa, a three-bedroom villa
      with pool and parking in Goa for ₹12,000 a night, sleeps 6 -- fits your group of 6 and has the
      pool you asked for."
      - Verify: ✅ 2 new tests confirming the clause appears correctly-positioned (before the
        `property_id` parenthetical, never after/interleaved) and that no clause appears when
        `match_reasons` is empty. **Measured, not just eyeballed**: the worst case (both reason slots
        filled) is 31 words / ~14 extra tokens versus the no-reason baseline — longer than the
        existing "15 words or fewer per item" guidance in `system_prompt.py:227`, flagged explicitly
        for Phase 6.3 to reconcile (that audit task already anticipated this exact outcome in its own
        text: "adjust the 15-word guidance if `match_reasons` regularly pushes past it in practice").
        Not fixed here — Phase 6.3's job, recorded now so it isn't silently rediscovered later.
- [x] **2.3 — "Recommend before interrogation" — sharpen `LEAD_AGENT_INSTRUCTIONS` step 3.** ✅ Done
      (2026-08-01), prompt-only change (no code-level way to force "recommend earlier," per the
      task's own framing). The dates-finalized YES branch no longer unconditionally says "ask their
      budget, then use `recommend_properties`" — it now recommends immediately with whatever's
      already known (location/purpose/guest-count) when only budget is missing, asking budget
      afterward as a refinement pass instead of a gate.
      - Verify: ✅ `test_lead_agent_recommends_before_asking_budget_when_other_criteria_known` pins
        the sharpened wording landed (not just that the prompt still builds). Full
        `test_system_prompt.py` suite (66 tests, +1 from this task) re-run clean — no existing test
        pinned the old "ask their budget, then use recommend_properties" wording, so nothing else
        needed updating.
- [x] **2.4 — Close the recommend/availability sequencing gap (requirement #10).** ✅ Done
      (2026-08-01). New `calendar_service.unavailable_property_ids(db, property_ids, check_in,
      check_out)` — batched (one query for the whole small candidate set, not N round trips), reusing
      `is_available`'s exact overlap semantics (`existing.check_in < new.check_out AND
      existing.check_out > new.check_in`, `status == "confirmed"`) rather than a second, subtly
      different definition of "available." `check_in`/`check_out` threaded through as new *optional*
      parameters — `orchestrator.recommend_properties` → `handle_recommend_properties` →
      `tools.py`'s wrapper (which sources them from `state.slots`, parsed via a new `_parse_iso_date`
      helper that fails open to `None` on anything malformed) — **not** added to
      `RecommendPropertiesArgs` itself, exactly as scoped (the LLM is never asked to supply dates to
      this tool). Wrapped in try/except, failing open to today's unfiltered behavior on any error.
      - Verify: ✅ 3 new tests in `test_property_retrieval_orchestrator.py` (booked property excluded
        when dates known, behavior byte-identical when dates unknown, fails open on a simulated DB
        error) + 3 new tests in `test_calendar_service.py` for `unavailable_property_ids` directly
        (empty-input no-op, batched exclusion across a candidate set, non-confirmed bookings ignored)
        + 1 full end-to-end test in `test_conversation_state_slot_wiring.py` exercising the real
        `update_lead` → `recommend_properties` tool-wrapper chain (a guest gives dates via
        `update_lead`, a later `recommend_properties` call — which has no date argument at all —
        still excludes the already-booked property). 8 tests total, all passing.
- [x] **2.5 — Recommendation diversity across different guests/calls.** ✅ Done (2026-08-01). New
      `ranking.diversify_leading_candidates(properties, call_session_id)` — rotates which property
      leads among a comparable-price band (within 10% of the cheapest) at the front of the
      already-ranked list, seeded off a SHA-256 hash of `call_session_id` so the same call is always
      stable (no flip-flopping if the guest asks again) while different calls see real variety. Wired
      into `orchestrator.recommend_properties` right before the final cap-to-3, and only on the
      normal path — deliberately **not** applied to the combo-fallback path, since that list's order
      already carries its own meaning (which units to pair), not a ranked "pick one." `call_session_id`
      threaded through the same three-layer chain as 2.4. Falls back to today's exact deterministic
      order when no `call_session_id` is available, and fails open (returns the list unchanged) if
      the cheapest price is `<= 0` (avoiding a divide-by-zero against an `exact_airbnb_pricing`
      property's legitimately-zero `base_price`).
      - Verify: ✅ 7 new tests in `test_property_retrieval_ranking.py` — real distribution measured
        across 30 distinct random `call_session_id`s (confirmed more than one property leads, not
        eyeballing a single run), same-call-id stability, a clearly-better match never displaced
        outside the band, single-candidate/no-call-id/zero-price edge cases. Plus 2 end-to-end tests
        in `test_property_retrieval_orchestrator.py` (distribution across 20 real orchestrator calls
        with comparable options; unchanged default order with no `call_session_id`) — 9 tests total.
- [x] **2.6 — Confidence-aware phrasing, derived from real signals.** ✅ Done (2026-08-01). New
      `recommendation_confidence: Literal["strong", "moderate", "weak"]` field on
      `RecommendationResult`, computed by `confidence_for_result(options, combo_note)` in
      `pitch_formatter.py` — a pure function of `len(options)` and whether `combo_note` fired, wired
      in at the one place `RecommendationResult` is actually constructed
      (`context_builder.build_recommendation_result`). `render_recommendation_text`'s intro line now
      comes from a `_CONFIDENCE_INTROS` lookup instead of a hardcoded guest-count check.
      - Verify: ✅ 7 new tests in `test_property_card_and_pitch_formatter.py` covering all three
        signal shapes and their phrasing, plus a dedicated test confirming the underlying property
        line (price/capacity/name) is byte-identical across confidence tiers — only the intro
        sentence differs. 2 more end-to-end tests in `test_property_retrieval_orchestrator.py`
        confirming `strong`/`weak` land correctly through the real orchestrator path. Note:
        `property_recommendation_guard.py`'s own separate `_fallback_recommendation_text` (used only
        when the model fails to name any recommended property at all — a different, guard-level
        emergency path operating on plain dicts, not `PropertyCard`) keeps its own independent,
        unchanged intro text — out of this task's scope, noted rather than silently left inconsistent.

**Phase 2 sign-off (2026-08-01)**: ✅ all 7 tasks implemented. Modified files: `filter_builder.py`
(2.0's exclusion clause), `card.py` (2.1's `match_reasons`/`match_reasons_for_card`),
`pitch_formatter.py` (2.2's reason clause, 2.6's confidence field/phrasing), `context_builder.py`
(threading `args` for 2.1, computing confidence for 2.6), `orchestrator.py` (2.4's availability
pre-filter, 2.5's diversity rotation, both via new optional parameters), `calendar_service.py` (2.4's
`unavailable_property_ids`), `ranking.py` (2.5's `diversify_leading_candidates`), `tool_handlers.py`
and `tools.py` (threading `check_in`/`check_out`/`call_session_id` through the wrapper chain for
2.4/2.5), `system_prompt.py` (2.3's sharpened `LEAD_AGENT_INSTRUCTIONS`). 42 new tests across 6 test
files (`test_property_retrieval_sql_search.py`, `test_property_card_match_reasons.py`,
`test_property_card_and_pitch_formatter.py`, `test_system_prompt.py`, `test_property_retrieval_orchestrator.py`,
`test_calendar_service.py`, `test_property_retrieval_ranking.py`, `test_conversation_state_slot_wiring.py`),
all passing. Full suite: 481 passed (up from Phase 1's 439), same 5 pre-existing/environment-dependent
failures as every prior phase — zero new regressions. Token impact measured directly, not estimated:
2.3's prompt-text sharpening added ~10 tokens to the Lead Agent system prompt (9,900 → 9,910); 2.1/2.2's
`match_reasons` clause adds up to ~14 tokens per recommended property, appearing only in
`recommend_properties`'s tool-result text on turns where it actually fires, not on every turn or in
the system prompt itself. One real bug caught and fixed before it shipped (2.1's `PropertyCard`
`match_reasons` field, initially sketched with a bare mutable-default-adjacent value on a frozen
dataclass, corrected to a required field). One finding flagged for a later phase rather than fixed
here: the worst-case `match_reasons` clause (both slots filled) runs to 31 words, past the existing
"15 words or fewer" guidance — Phase 6.3 already anticipated exactly this and owns reconciling it.
⚠️ No real/browser voice call placed this phase either, same limitation as Phase 1 (Clerk-only auth +
WebRTC, neither scriptable here) — every task's verification instead relies on real DB-backed
integration tests exercising the actual call chain (tool wrapper → handler → orchestrator → DB),
not mocks, per `CLAUDE.md`'s own "never mock the DB" rule.

---

## Phase 3 — Language adaptation made continuous and code-aware, not prompt-only (requirement #1, #2)

**What already exists (do not rebuild)**: `LanguageSyncProcessor` (`app/voice/language_sync.py`)
already does real-time, per-turn language detection and switches Sarvam TTS's synthesis language
live, with zero added latency — this is the audio half of requirement #1 and it works well.
`GOLDEN_RULES` (`system_prompt.py:193-204`) already has strong, specific Hinglish-tone guidance
(casual over shuddh Hindi, Roman script only, "would a friendly local host say this out loud" bar) —
this already matches the goal's "Hinglish Guidelines" section closely; do not rewrite it, extend it
only where a gap is real.

**What's missing**: the *text* half — what language the LLM chooses to *write* its reply in — is
100% prompt-compliance-dependent today, with no structural signal telling the model what language it
was just using, unlike the well-established GOLDEN_RULES clauses for other repeated-content
problems (repetition, escalation phrasing) that already got a code-level backstop after prompt-only
attempts kept regressing (per Standing Rule 3). `LanguageSyncProcessor` already computes exactly the
signal needed (`frame.language`, `language_sync.py:53-54`) — it's discarded after the TTS switch
today rather than being fed back into anything else.

**Reframing note, corrected during review**: this phase originally scoped 3.3 as a Devanagari-script
*ban* enforced in code. That's the wrong frame. Catalogue item C5 (Phase 0.2) shows the real failure
mode precisely: a guest directly asked "आप हिंदी में बोल सकते हो?" ("can you speak Hindi?") mid-call,
and the very next reply stayed in English — **the bug was Mira ignoring an explicit, stated language
preference, not Mira producing Hindi/Devanagari text.** A hardcoded script ban is also a real
architectural constraint this plan shouldn't introduce: this codebase is meant to serve *any* Airbnb
host in India (per the goal's own framing), and a host running a homestay in a market where guests
and the host genuinely prefer pure/formal Hindi (rural Uttarakhand, Varanasi, etc.) shouldn't be
structurally prevented from that by a guard baked in for a different, urban-Hinglish-first market.
3.3 below is rewritten accordingly: the mechanism is *honoring an explicit or clearly inferred
language preference*, with urban Hinglish remaining the sensible **default** for mixed-language
conversations (unchanged — that default is already correct and well-specified in `GOLDEN_RULES`),
not a permanent script-level restriction.

- [x] **3.1 — Track detected conversation language in `ConversationState`.** ✅ Done (2026-08-01).
      Added `current_spoken_language: "Language | None"` (typed under `TYPE_CHECKING` to keep the
      lightweight `conversation_state.py` module free of a hard runtime dependency on pipecat's enum).
      `LanguageSyncProcessor.__init__` now takes an optional `conversation_state` param, writes to it
      on every `TranscriptionFrame` alongside its existing TTS-switch logic, and `pipeline.py`'s
      construction order was adjusted (moved `conversation_state`'s construction earlier) so
      `language_sync` can receive the same instance `state_prompt_sync`/`tools` already share.
      - Verify: ✅ `tests/test_language_sync.py` (5 new tests — no test file existed for this
        processor before this task) — confirms the write happens, updates correctly across a
        language switch, is a true no-op (not an error) when no `ConversationState` is passed
        (preserving every existing call site), and confirms the pre-existing TTS-switch behavior is
        completely unchanged by this addition (including the no-redundant-switch case).
- [x] **3.2 — Surface `current_spoken_language` back into the prompt as a live instruction.** ✅ Done
      (2026-08-01), via option (b) as anticipated — extended `StatePromptSyncProcessor`
      (`app/voice/state_prompt_sync.py`, built in Phase 1.3/1.6) with a `_language_hint` helper rather
      than building a new mechanism, since it already solves exactly this "inject something live,
      never touch the cached system prompt" problem for slots/goal. The real system-prompt message
      (`messages[0]`) is untouched by this addition, same as every other Phase 1 field — Groq's
      prefix-cache hit on it is preserved by construction, not just by intent.
      - Verify: ✅ 5 new tests in `test_state_prompt_sync.py` — passive detection produces the correct
        "currently speaking X" hint, English and Hindi both map to the right display name (Hindi maps
        to "Hinglish" specifically, matching `GOLDEN_RULES`' own casual-Hinglish-not-shuddh-Hindi
        rule), no hint at all before any speech is detected (a true no-op, zero tokens, for the first
        turn of any call), and the hint flows correctly through the real processor end-to-end.
        **Measured, not assumed**: the language hint costs ~23 tokens when present (93 chars), 0 when
        not — well within Phase 0.3's budget. ⚠️ No real/browser call placed to confirm live
        Groq cache-read-token behavior specifically (same environment limitation as every phase) —
        the code-level guarantee (never touching `messages[0]`) is the same mechanism Phase 1.3
        already relied on and already reasoned through this exact constraint for.
- [x] **3.3 — Honor an explicit or host-configured language preference.** ✅ Done (2026-08-01), both
      signals implemented:
      - **Guest-stated preference**: added `explicit_language_preference: "Language | None"` to
        `ConversationState`. **Implementation detail worth recording — no clean tool-call signal
        existed for this, exactly as the plan anticipated, so this rides on `update_lead`'s existing
        `preferred_language` argument** (new, constrained to `"english"`/`"hindi"` by its own
        docstring) rather than a new dedicated tool for one field — deliberately **not** persisted to
        the `Lead` DB row/`UpdateLeadArgs` (no migration needed for this half; it only ever needs to
        live in `ConversationState` for the current call). A new `GOLDEN_RULES` clause (right after
        the existing passive-mirroring rule) instructs the model to recognize an explicit request and
        call `update_lead(preferred_language=...)` immediately, same weight as the existing
        name/phone-saving rule it's modeled on.
      - **Host-level policy**: added `User.agent_language_policy` (`"hindi_first" | "english_first" |
        None`, nullable, no `server_default` — migration `a1c4e8f7b2d3`, validated upgrade →
        downgrade → re-upgrade against a real local Postgres instance; **never run against the real
        production DB**, consistent with every prior migration in this codebase's history). Exposed
        through `UserUpdate`/`UserOut` (a thin `Literal` addition — the existing `PATCH /auth/me`
        endpoint's generic `setattr` loop needed no other change) so a host can actually set this via
        the dashboard, not just have an unreachable DB column. Wired into
        `_persona_and_escalation_sections` (shared by both prompt builders) as a conditional note,
        completely absent when unset.
      - Verify: ✅ 5 new tests in `test_explicit_language_preference.py` (C5 reproduced directly:
        `update_lead(preferred_language="hindi")` sets the state field correctly; English too; a
        later unrelated `update_lead` call never clobbers an already-set preference; an unrecognized
        value fails open rather than crashing; confirmed never written to the `Lead` DB row). 5 new
        tests in `test_system_prompt.py` for the `GOLDEN_RULES` clause and 5 more for
        `agent_language_policy` (including the specific no-regression check: an unset policy produces
        a byte-identical prompt to before this task existed). 6 new tests in `test_user_schemas.py`
        for the schema validation. 3 new tests in `test_state_prompt_sync.py` confirming an explicit
        preference correctly overrides passive detection when both are set. **34 tests total for
        3.3 alone.** ⚠️ No real/browser call placed reproducing C5 live end-to-end — same recurring
        environment limitation; every mechanism in the chain (tool wrapper → state → prompt injection)
        is independently tested against real code paths instead.
- [x] **3.4 — Devanagari script is a rendering choice, not a violation to police.** ✅ Confirmed
      (2026-08-01) — no code-level script guard was built. Verified by grepping every file touched or
      added this phase for any Devanagari-detection/stripping logic: the only matches are the
      pre-existing, unrelated `guest_name` transliteration docstring in `update_lead` (predates this
      phase) and the pre-existing bilingual turn-completeness heuristic in `turn_strategies.py`
      (also predates this phase, and is on the experimental `hybrid_experimental` path, not
      production). Recorded here as the task's own verification, per its own text.

**Phase 3 sign-off (2026-08-01)**: ✅ all 4 tasks implemented. New files: `tests/test_language_sync.py`,
`tests/test_explicit_language_preference.py`, migration `a1c4e8f7b2d3_add_agent_language_policy_to_users.py`.
Modified files: `conversation_state.py` (2 new fields), `language_sync.py` (state-writing), `pipeline.py`
(construction-order fix so `language_sync` can receive `conversation_state`), `state_prompt_sync.py`
(language hint), `tools.py` (`preferred_language` arg + mapping), `system_prompt.py` (explicit-request
clause + host-policy section), `models/user.py` (new column), `schemas/user.py` (`UserUpdate`/`UserOut`
exposure). 24 new tests across 5 test files, all passing. Full suite: 505 passed (up from Phase 2's
481), same 5 pre-existing/environment-dependent failures as every prior phase — zero new regressions.
Migration validated via a real upgrade → downgrade → re-upgrade cycle against a local Postgres instance
(no scratch-DB creation permission available, so validated directly against the same `mira_test`
instance the test suite uses, then fully reset back to the empty state `conftest.py`'s own fixtures
expect — confirmed via a full subsequent test-suite run that this left no side effects). Token impact
measured directly: `GOLDEN_RULES`' new explicit-language clause added ~214 tokens (7,350 → 7,564,
static, always present); the state-block language hint costs ~23 tokens only once a language is
actually detected, 0 before; `agent_language_policy`'s prompt line costs ~39 tokens, opt-in only for
hosts who set it (the overwhelming majority won't, confirmed byte-identical otherwise). ⚠️ No
real/browser voice call placed this phase either (same Clerk-auth + WebRTC limitation as every prior
phase) — every mechanism was instead verified via real, DB-backed integration tests exercising the
actual tool-wrapper → state → prompt chain.

---

## Phase 4a — In-call memory has no ceiling; bound it before it becomes a real cost/latency risk

**The gap, confirmed independently via a full read of `pipeline.py`'s message-construction path**:
`context = LLMContext(messages=[...], tools=tools)` (`pipeline.py`, constructed once at call start)
is mutated in place for the entire call by pipecat's own `LLMContextAggregatorPair` — every guest
turn, every assistant reply, every tool call/result gets appended, and the **full accumulated history
since call start is resent on every single LLM completion**, unbounded. There is no truncation,
windowing, or summarization of in-call history anywhere in this codebase — the only existing caps
are output-side (`max_completion_tokens=400` on Groq, `900` on OpenRouter,
`docs/agents.md`'s VAD/TTS tuning table) and the unrelated cross-call `MAX_CONVERSATION_SUMMARIES = 20`
cap on `GuestProfile.conversation_summaries` (`guest_memory_service.py`), which bounds a *different*
list entirely. `docs/agents.md:173`'s own 2026-07-27 incident (a single completion blowing up to
3072 tokens on "a long, noisy call") was fixed with output capping + `RepetitionGuardProcessor` —
neither addresses the actual input-side growth this phase targets. This isn't yet a confirmed live
incident the way that one was, but it's a structural gap worth closing proactively before a
long-support call (a multi-issue guest-support conversation, or a Lead Agent call that browses many
properties) produces it: every turn on a long call pays growing prompt-token cost and, past some
length, growing latency, with nothing in the codebase bounding either.

- [x] **4a.1 — Measure before building anything.** ✅ Done (2026-08-01). Real Railway/Groq per-call
      usage-metrics logs aren't reachable from this environment, so this was measured directly from
      the Phase 0.1 transcript sample (18 real calls) instead: reconstructed the cumulative
      chars-sent-to-the-LLM at every assistant turn for each call (system prompt + everything said so
      far up to that point), using Phase 0.3's measured ~8,950-token static-prompt baseline.
      - **The longest real call in the sample** (`8a4975d0…`, 2026-06-29, 41 assistant
        turns/69 total turns) ends with a prompt at **~10,890 tokens — only ~24.5% larger than the
        static baseline**, not runaway growth. Every other call in the sample (down to 7 turns) grew
        by less (4-18%).
      - **Real, non-obvious finding that changes the picture entirely**: summed across every
        completion in that same longest call (41 completions, each resending the full prompt so far),
        total tokens sent ≈ 404,000. Splitting that by source: the **static, byte-identical system
        prompt** (resent unchanged on every single turn) accounts for **~91% of that total**
        (366,950 of 404,479 tokens) — the **growing conversation history this phase was scoped to
        bound accounts for only ~9%** (37,529 tokens). A call would need to run roughly **4x longer
        than the longest real call observed** (≈280 turns) before history alone even matched the
        system prompt's own size, let alone exceeded it problematically.
      - **Explicit call, as the task requires**: **this is not yet a real problem** — real call
        volume today doesn't approach the length where in-call history growth is the dominant cost or
        a latency risk. The far higher-leverage, already-identified lever for prompt-token cost is the
        **existing, already-scoped Groq prompt-caching reordering** (`project_state.md:104`'s
        finding — moving per-call-unique sections to the end of the prompt so the static, byte-identical
        block can get a cross-call cache hit), since that block is what dominates cost on every call,
        long or short, not just long ones. That reordering is a pure sequencing change with no
        content change and effectively zero risk — a stronger candidate for actual follow-up than
        anything this phase would add, though implementing it is outside Phase 4a's own scope (it's
        prompt-structure, not conversation-memory-bounding) and is called out here only because the
        measurement work done for 4a.1 directly surfaced it as the real lever.
- [ ] **4a.2 — Not built, per 4a.1's own explicit finding.** The task's own gating condition ("if
      4a.1 shows a real problem") was not met — real call data shows in-call history growth is a
      small, bounded contributor (~9% of total token cost even in the longest observed call, ~25%
      prompt-size growth at worst), not the runaway/unbounded risk this task was scoped to guard
      against. Building compaction logic now would be solving a problem the data doesn't show exists
      yet, at real cost (a new code path, new failure surface, ongoing maintenance) for no measured
      benefit — exactly the "don't build ahead of need" discipline Standing Rule 1/Phase 0 already
      establish. Revisit if/when real call volume grows meaningfully longer than what's in this
      sample (a genuinely long multi-property Lead Agent call, or an extended multi-issue Guest
      Support conversation) — the reconstruction method used for 4a.1 (rebuild cumulative prompt size
      from a real transcript, split static-vs-history contribution) is reusable as-is against a fresh
      sample at that point, not something that needs to be redesigned.
      - Verify: this task's own verification is the 4a.1 measurement itself — confirmed no
        compaction code was written, and confirmed the reasoning for not building it is recorded here
        rather than silently skipped.

**Phase 4a sign-off (2026-08-01)**: ✅ both tasks addressed — 4a.1 measured, 4a.2 correctly not built
per that measurement. No code changed this phase; this was a pure measurement-and-decide phase, per
the task's own design. Real finding worth carrying forward: prompt-token cost on this system is
dominated by the static system prompt being resent every turn (~91% of total cost on the longest real
call observed), not by growing conversation history (~9%) — the existing, already-scoped Groq
prompt-caching reordering (`project_state.md:104`) is the actual high-leverage lever for cost, not
anything in this phase's original scope. Full test suite unaffected (no code touched): still 505
passed, same 5 pre-existing/environment-dependent failures.

---

## Phase 4 — Repetition and "already said" awareness upgraded from text-similarity to state-aware (requirement #6)

**What already exists**: `RepetitionGuardProcessor` (`app/voice/repetition_guard.py`) already
detects near-duplicate sentences via ≥60% word overlap *within a single response*, and GOLDEN_RULES
(`system_prompt.py:276-284`) separately bans repeating "a sentence you've already said earlier in
this same call." Both are real and working — this phase does not replace them.

**The gap**: both mechanisms operate on raw text similarity against the transcript, not against
*known facts* (recommendations already shown, prices already quoted, confirmations already made).
Text-similarity catches near-verbatim repeats but not "re-stating the same information in different
words" — which is exactly what requirement #6 is asking to prevent, and exactly the class of bug
`ConversationState` (Phase 1) is built to solve structurally rather than via fuzzier text matching.

- [x] **4.1 — Use `ConversationState` (Phase 1) as the source of truth for "have I already told them
      this," feeding GOLDEN_RULES' anti-repetition rule with structured facts instead of only prose
      instruction.** ✅ Done (2026-08-01). Added `ConversationState.quoted_price: dict | None` and
      `record_quoted_price(property_name, check_in, check_out, total)` (always overwrites, never
      merges — a re-quote is real new information, not an accumulation). `get_pricing`'s tool wrapper
      (`app/voice/tools.py`) passes a new `on_priced` callback into `handle_get_pricing`
      (`app/services/tool_handlers.py`, extended with `on_priced: Callable[[Property, PriceBreakdown],
      None] | None = None`, invoked only on a real successful quote, never on an error path) — using
      the callback's own loaded `Property.name`, not `state.selected_property_name` (which can be
      unset in Guest Support mode). `StatePromptSyncProcessor`/`build_state_block_content`
      (`app/voice/state_prompt_sync.py`) extended to surface `quoted_price` into the injected state
      block alongside slots/recommendations/goal — found and fixed a no-op-guard bug during testing
      where the early-return check didn't include `quoted_price`, silently dropping the block even
      when only price was set.
      - Verify: `tests/test_get_pricing_records_quoted_price_in_state` (via
        `test_tool_handlers.py`/`test_voice_tools.py`) confirms the callback fires with the real
        `Property` object and records the correct name/total; `tests/test_state_prompt_sync.py`'s
        `test_build_state_block_content_includes_quoted_price` confirms the prompt-visible block
        surfaces it. Repeat-request behavior (guest explicitly asks to hear a quote again) is
        unaffected — this task only adds visibility into the state block, actual repeat suppression
        is 4.2's job.
- [x] **4.2 — Extend `RepetitionGuardProcessor`'s scope check** (`app/voice/repetition_guard.py`) to
      also compare a new response's property names/price figures against `state.recommendations_shown`/
      known quoted price, as a second, structured detection alongside its existing word-overlap
      heuristic. ✅ Done (2026-08-01). `RepetitionGuardProcessor.__init__` now accepts an optional
      `conversation_state: ConversationState | None = None` (every existing call site without one is
      an explicit no-op, per `test_no_conversation_state_is_a_no_op_for_structured_check`). Added
      `_spoken_facts: set[str]` — deliberately cross-*response* memory, unlike `_seen_sentences`,
      which intentionally resets every `LLMFullResponseStartFrame` — plus
      `_is_unprompted_structured_repeat()`, which flags a sentence only if it names an
      already-known property **and** restates the exact already-quoted price (a different number for
      the same property, e.g. a discount re-quote, is real new information and is never flagged).
      Found and fixed a real pre-existing bug while building this: `LLMFullResponseEndFrame` never
      flushed `_sentence_buffer`, so a response's final sentence with no trailing text after it (the
      single most common real shape — a price quote as the entire/last sentence of a reply) was never
      judged or recorded by any check at all, silently defeating 4.2 for the realistic case. Fixed by
      flushing the buffer through `_judge_sentence` in the `LLMFullResponseEndFrame` branch before
      resetting state.
      - Verify: `tests/test_repetition_guard.py` — 6 new tests, including
        `test_reworded_repeat_of_the_same_quoted_price_across_turns_is_cut`,
        `test_different_price_for_same_property_is_not_flagged_as_repeat`,
        `test_mentioning_the_same_property_name_for_an_unrelated_reason_is_not_flagged` (guard must
        never become "never say this property's name twice"), and the regression test for the
        buffer-flush bug above. Full file: 13/13 passing.
- [x] **4.3 — Response Shape Validator: a final structural gate right before TTS, correctly
      identified as a distinct problem from repetition during review.** Catalogue item C3 (Phase
      0.2) — three separate questions concatenated into one wall of text with no guest turn in
      between, confirmed live as recently as today's date — is **not actually a repetition
      problem**: `RepetitionGuardProcessor`'s word-overlap heuristic (4.2) is specifically built to
      catch a sentence repeating *itself*, and three genuinely *different* sentences glued together
      have low word overlap by construction — the mechanism that would need to catch C3 is checking
      the **shape** of one response (how many distinct questions/objectives it contains, whether it
      reads as one continuous reply or several stitched together), not comparing it against earlier
      turns at all. This is a structurally different check from everything else in Phase 4, which is
      why it's its own task rather than folded into 4.2. Add `ResponseShapeValidatorProcessor`
      (new `app/voice/response_shape_guard.py`), positioned **last** in the guard chain, immediately
      before `tts` — after every other guard's rewrites (`repetition_guard` → `meta_commentary_guard`
      → `property_recommendation_guard` → `escalation_phrase_guard` → `premature_end_call_guard`,
      `docs/agents.md`'s pipeline-stage list) have already run, so it validates the actual final text
      about to be spoken, not an intermediate draft. Checks, all deterministic/mechanical — not
      another LLM call judging the LLM's own output, which would just add a second unreliable
      judgment on top of the first:
      - **Multiple question marks with no natural single-topic connector between them** — a cheap,
        real signal for "this reads like several turns stitched together," the exact C3 shape.
        Not a ban on ever asking two things (GOLDEN_RULES already permits reciting a requested list),
        scoped to catching sentences that look like independently-generated turns concatenated raw
        (no connecting word, abrupt topic shift dissimilar to how the same sentence would read if a
        human said it in one breath).
      - **More than one greeting-shaped opener** in a single response (e.g. two separate "Hi"/"Namaste"
        occurrences) — a mechanical, cheap check distinct from `GOLDEN_RULES`' "don't repeat the
        greeting across turns" rule (`system_prompt.py:266-275`), which is about the whole call, not
        one response's internal shape.
      - **The exact same escalation-safe-line text appearing twice within one response** — cheap
        string-repeat check, complements `EscalationPhraseGuardProcessor`'s cross-turn "only once per
        call" enforcement with a within-one-response version.
      - **A response ending mid-clause** (no terminal punctuation and the last few words don't read
        as a complete thought) — catches an "unfinished thought" shape without needing to judge
        meaning, just structure.
      - **Duplicated punctuation runs** (`??`, `..`, repeated `--`) — the exact mechanical shape of
        the `".. .. .."` degenerate-output failure (catalogue item H2) as a structural backstop,
        distinct from and in addition to `RepetitionGuardProcessor`'s existing fragment-flood
        detection (belt-and-suspenders, given H2's residual-risk flag in Phase 0.2 — this call
        b5d36092… still showed the pattern two days after that guard's fix).
      - **More than one full recommendation block in one response** — cross-checks against
        `state.recommendations_shown`/the current turn's own tool result (Phase 1.1): if a response
        contains what looks like two separate "here are N properties" pitches instead of one, that's
        the C3 shape applied specifically to recommendations.
      This deliberately does **not** attempt semantic/meaning-level validation ("is this a good
      response") — every check above is a structural/mechanical pattern match, the same discipline
      the six existing guards already use, so it stays a true final gate (fast, deterministic,
      explainable) rather than a second unreliable AI judge sitting in front of TTS.
      - Verify: unit tests reproducing catalogue item C3 verbatim (the exact concatenated-questions
        text from call `d5a808a4…`) and confirming the validator either splits it into a clean single
        turn (keeping only the first complete question) or flags it for the guard to shorten —
        pick one deterministic resolution strategy and test it explicitly, don't leave the behavior
        undefined. Confirm zero false positives against a large sample of the Phase 0.1 transcripts'
        *good* turns (single, clean, complete responses) — a validator that also mangles normal
        replies is worse than the problem it fixes. Confirm this sits last in the guard chain via a
        pipeline-order test, not just a code comment claiming it does.

      ✅ Done (2026-08-01). New file `app/voice/response_shape_guard.py`:
      `ResponseShapeValidatorProcessor` always buffers the full response (no narrower arming
      condition — the whole point is judging the final, complete text) and, at
      `LLMFullResponseEndFrame`, runs six deterministic checks (`has_multiple_unconnected_questions`,
      `count_greeting_openers`, `has_duplicated_safe_line`, `has_duplicated_punctuation`,
      `has_multiple_recommendation_blocks`, `ends_mid_clause`) via `validate_response_shape()`. On any
      violation, resolves to `first_clean_sentence_or_original()` — keeps only the first complete
      sentence, falling back to the original text if it can't be split at all, so the guest is never
      left with silence. Imports `CONFIDENCE_INTROS` from `pitch_formatter.py` (renamed from
      `_CONFIDENCE_INTROS` to public, since this is now a legitimate cross-module import) and
      `SAFE_REPLACEMENT_TEXT` from `escalation_phrase_guard.py`. Wired into `pipeline.py` as the
      literal last processor before `tts`, after `premature_end_call_guard` — confirmed by direct
      reading of the `Pipeline([...])` list, not just the docstring's claim.

      Three real bugs found and fixed while validating against catalogue item C3's actual transcript
      text (verbatim, not a synthesized worst case) rather than a hand-picked easy example:
      (1) the unconnected-questions check originally scanned the first 4 words of the following
      segment for a connector *anywhere*, false-matching "We'll check availability **and**
      pricing..." (the "and" was inside that sentence's own content, not bridging two questions) —
      fixed to check only the literal first word via `.fullmatch()`; (2) the dangling-trailing-word
      set originally included prepositions ("with", "for", "to"), false-flagging C3's own valid,
      complete text ending "...go ahead with?" — fixed by removing prepositions, keeping only
      conjunctions/determiners; (3) the sentence-splitter required whitespace after terminal
      punctuation, but C3's real text has literally no space after its first `?`
      ("...interesting?Got it, Abhaya.") — fixed `\s+` to `\s*`. A fourth bug (duplicated-punctuation
      regex not matching the real degenerate flood shape `".. .. .. .."`, space-separated pairs
      rather than one continuous run — catalogue item H2's exact confirmed-live shape) was found and
      fixed the same way.
      - Verify: `tests/test_response_shape_guard.py`, 24 tests, all passing — unit tests for every
        pure detection function plus integration tests through the real processor, including
        `test_processor_reproduces_and_fixes_real_c3_shape` (C3 verbatim →
        `"Which one sounds interesting?"`), `test_processor_no_false_positives_against_a_realistic_sample`
        (7 realistic clean turns pass through byte-unchanged), and
        `test_processor_never_leaves_the_guest_with_nothing`. Full backend suite re-run after wiring
        into `pipeline.py`: 539 passed, the same 5 pre-existing failures as every prior phase
        (`test_call_includes_duration_and_lead_name_phone`,
        `test_escalate_to_host_also_saves_lead_so_it_isnt_left_empty`,
        `test_search_faq_logs_gap_when_no_verified_answer`, `test_is_complete_short_but_punctuated`,
        `test_ice_servers_stun_only_by_default`) — no regressions. Token/cost impact: zero — this
        processor never touches the LLM context, system prompt, or any tool call; it is a pure
        post-generation text transform on the already-generated response, confirmed by inspection
        (no import of anything that builds prompt/context content).

**Phase 4 sign-off (2026-08-01)**: ✅ all 3 tasks (4.1, 4.2, 4.3) implemented and independently
verified. New files: `app/voice/response_shape_guard.py`, `tests/test_response_shape_guard.py`.
Modified files: `app/voice/conversation_state.py` (`quoted_price`, `record_quoted_price`),
`app/voice/tools.py` (`on_priced` callback wiring), `app/services/tool_handlers.py`
(`handle_get_pricing`'s new optional callback param), `app/voice/state_prompt_sync.py` (surfaces
`quoted_price`), `app/voice/repetition_guard.py` (`_spoken_facts`, structured cross-turn check,
buffer-flush fix), `app/voice/pipeline.py` (guard wired in as the literal last stage before `tts`),
`docs/agents.md` (pipeline-stage diagram and guard list updated to match). Full backend suite:
539 passed, same 5 pre-existing failures as baseline, no regressions. Token budget: 4.1/4.2 add zero
new prompt tokens beyond what Phase 1's state block already costs (quoted_price is one more line in
an existing block, not a new mechanism); 4.3 adds zero prompt tokens (post-generation only). No new
paid API calls introduced by any of the three tasks.

---

## Phase 4b — Tool Output Fidelity: the model must speak what the tool actually returned, not a
reinterpretation of it (P0, per review — this is the correct root-cause framing for C1 and C2)

**Reframing note**: Phase 0.2 originally catalogued C1 (recommendation violates a stated guest-count)
and C2 (₹0 spoken as "free of charge") as two separate bugs, and Phase 1.4/2.0 already fix their
*data-layer* causes (missing SQL filter backfill, zero-price properties reaching the pitch line).
Correct, sharper framing on review: both are also symptoms of one broader class of bug — **the model
is free to reinterpret a tool's structured result before speaking it, rather than being constrained
to faithfully relay what the tool actually returned.** Once Phase 1.4/2.0 land, the *data* a tool
returns will be correct in both cases — but nothing yet stops the model from still misstating price,
capacity, availability, property names, or amenities *after* a tool returns correct data, on some
future turn this plan hasn't specifically anticipated. This phase is the general-purpose backstop for
that whole class, not a third fix for C1/C2 specifically (those are already handled upstream).

**This is not a new mechanism — it's an existing one, generalized.**
`PropertyRecommendationGuardProcessor` (`app/voice/property_recommendation_guard.py`) already does
exactly this for one specific case: it's handed the real `RecommendationResult` directly (not the
rendered text, `property_recommendation_guard.py:25-31`'s docstring is explicit about why — parsing
rendered speech back into data was already tried and broke once), and if the model's actual reply
doesn't name any of the real returned properties, the reply is overridden with a guaranteed-correct
line built straight from the tool's own data (`property_recommendation_guard.py:145-148`). This phase
extends that identical pattern to the other tools whose results carry facts a guest could act on
incorrectly if misstated.

- [x] **4b.1 — Extend structured-result verification to `get_pricing`/`check_calendar`/`negotiate_rate`
      results**, the same way `PropertyRecommendationGuardProcessor` already does for
      `recommend_properties`. Each of these tools already returns a natural-language string
      (`docs/agents.md`'s tools table) built from a real, structured computation
      (`pricing_engine.calculate_price`/`negotiate_rate`, `calendar_service`) — the raw number/dates
      are known and available at the point the tool result is produced, the same way
      `RecommendationResult` is available before rendering. Thread the structured value (not just the
      rendered string) into the guard the same way `record_tool_result()` already receives
      `RecommendationResult` (`property_recommendation_guard.py:100-105`), and verify the model's
      actual reply text contains the correct number/dates before it reaches TTS — e.g. for
      `get_pricing`, extract the actual quoted total from the reply (a number followed by
      "rupees"/₹) and confirm it matches the tool's real total; if it doesn't (or no number is
      present at all where one clearly should be), override with a guaranteed-correct line built
      from the tool's own return value, exactly mirroring the existing recommendation fallback
      (`_fallback_recommendation_text`, `property_recommendation_guard.py:83-86`).
      - Verify: dedicated tests per tool, each reproducing "tool returns correct value X, model's
        reply states a different value Y" and confirming the guard corrects it to X. Confirm the
        guard is a no-op (zero latency, no false rewrite) on the overwhelming common case where the
        model already correctly relays the tool's real value — same discipline as every other guard
        in this codebase.

      ✅ Done (2026-08-01). `handle_get_pricing`'s existing `on_priced` callback (Phase 4.1) now also
      feeds `property_recommendation_guard.record_tool_result("get_pricing", {"property_name",
      "total"})`; `handle_negotiate_rate` (`tool_handlers.py`) gained an equivalent new `on_priced:
      Callable[[Property, NegotiationResult], None] | None = None` param, called only on a real
      successful negotiation (never on the not-found/invalid-dates/non-positive-price early returns).
      `handle_check_calendar` gained a parallel `on_checked: Callable[[Property, bool], None] | None`
      firing with the real availability bool. `property_recommendation_guard.py` extended with
      `_pending_price_fact`/`_pending_availability_fact`, `_extract_amounts`/`_amount_present` (a
      permissive `₹`/"rupees"/"rs"/"inr" regex, 1.0 float tolerance for rounding) for the numeric
      check, and a separate lighter boolean check for `check_calendar` (`_AVAILABLE_ASSERTION_RE`/
      `_UNAVAILABLE_ASSERTION_RE` — only corrects a *clear* contradiction; a reply asserting neither
      way is left alone, never guessed at, since check_calendar's fact is a bool, not a number).
      - Verify: `tests/test_property_recommendation_guard.py`, 12 new tests, including
        `test_get_pricing_reply_stating_a_different_total_gets_corrected`,
        `test_negotiate_rate_reply_stating_a_different_counter_offer_gets_corrected`,
        `test_check_calendar_reply_contradicting_true_availability_gets_corrected`/`..._false...`,
        `test_check_calendar_reply_with_neither_assertion_is_left_alone`, and no-op tests confirming
        zero rewrite when no fact was ever recorded or when the model already states the correct
        value. `tests/test_tool_handlers.py`, 6 new tests confirming each `on_priced`/`on_checked`
        callback fires with the real `Property` object on success and never fires on any error/refusal
        path (`test_get_pricing_never_quotes_zero_when_base_price_is_zero`/
        `test_negotiate_rate_never_quotes_zero_when_base_price_is_zero` extended in place to assert
        `calls == []`).
- [x] **4b.2 — Capacity-fidelity check specifically, closing the residual C1 risk beyond the SQL fix.**
      Phase 1.4 fixes the *filtering* gap (a property that doesn't fit the guest's count shouldn't be
      returned at all) — this task is the belt-and-suspenders check that even a correctly-filtered
      result is spoken correctly: if `RecommendationResult.options` all satisfy the guest's known
      `num_guests` (from `state.slots`, Phase 1.1), but the model's reply text states a guest-count
      figure for a property that doesn't match its real `PropertyCard.max_guests`, that's a clear,
      mechanically-checkable fidelity violation worth catching the same way the property-name check
      already works.
      - Verify: a test with a correctly-filtered `RecommendationResult` (every option genuinely fits)
        where the model's reply text nonetheless misstates one property's capacity — confirm the
        guard catches and corrects it. This is deliberately narrow (checking a stated number against
        a known real number), not a semantic "does this sound right" judgment.

      ✅ Done (2026-08-01), landed in the same `property_recommendation_guard.py` pass as 4b.1 (reuses
      the SAME `_pending_options` already recorded for the name check). `_SLEEPS_N_RE` matches
      `format_property_pitch_line`'s own "sleeps N"/"sleeps up to N" phrasing. Checked BEFORE the
      existing name check so a capacity violation on an otherwise-correctly-named reply still gets
      caught. Deliberately scoped per-property, not a global scan: for each named property, only the
      "sleeps N" mention in that property's OWN sentence (up to the next `.`/`!`/`?`) is compared
      against its real `max_guests` — a naive whole-text cross-product would false-positive when TWO
      different, correctly-named properties each correctly state their own different real capacities
      in the same reply (guarded explicitly by
      `test_recommend_properties_multiple_options_each_with_own_correct_capacity_is_not_flagged`).
      - Verify: `test_recommend_properties_reply_misstating_capacity_gets_corrected` (Azure 1BHK
        really sleeps 2, reply claims 6 → corrected),
        `test_recommend_properties_reply_with_correct_capacity_passes_through_unmodified`, and the
        multi-option false-positive regression test above. 3/3 passing.
- [x] **4b.3 — Extend to amenities/property-names in `search_faq` results**, the lower-priority tail
      of this same class: `search_faq`'s returned text (`handle_search_faq`, `tool_handlers.py`) is
      already the actual on-file content — verify the model's reply doesn't introduce a fact not
      present in what `search_faq` actually returned (a lighter check than the numeric ones above,
      since FAQ answers are free text rather than a single verifiable number — scope this to a
      coarser check, e.g. confirming the reply doesn't name an amenity that wasn't in the returned
      text at all, not a full semantic-equivalence check).
      - Verify: a test where `search_faq` returns a specific, limited set of facts and the model's
        reply introduces an amenity/fact absent from that result — confirm detection; confirm no
        false positives on a reply that faithfully paraphrases (not invents) the same returned facts.

      ✅ Done (2026-08-01). Reuses the existing `app/services/amenity_taxonomy.py` fixed synonym map
      (`canonicalize_amenity`, already used by `recommend_properties`' own facet filtering) rather than
      inventing a second amenity vocabulary — `_AMENITY_KEYWORD_RE` matches any known synonym as a
      keyword in the reply, canonicalizes both the mention and the property's real `amenities` list,
      and only flags a mention whose canonical form is absent from the real list. Deliberately coarse
      per the plan's own scope: a free-text paraphrase of a real fact that isn't one of the fixed
      keywords at all (e.g. check-in time) is never flagged, and a synonym of a real amenity ("swimming
      pool" for an on-file "Private pool") is correctly treated as the same fact, not an invention.
      `handle_search_faq` gained a new `on_answered: Callable[[Property], None] | None = None` param,
      called only when a specific property was actually resolved (never for a true portfolio-wide
      query with no property at all — there's no single amenity list to check against in that case,
      confirmed by `test_search_faq_on_answered_never_fires_for_portfolio_wide_query`). On a violation,
      the fallback text is the exact same "I don't have verified information..." string
      `handle_search_faq` already returns for its own no-answer case, so a corrected reply is
      indistinguishable in tone from a normal "not on file" answer.
      - Verify: `tests/test_property_recommendation_guard.py`, 5 new tests —
        `test_search_faq_reply_inventing_an_amenity_not_on_file_gets_corrected`,
        `test_search_faq_reply_naming_a_real_amenity_passes_through_unmodified`,
        `test_search_faq_reply_with_a_synonym_of_a_real_amenity_is_not_flagged`,
        `test_search_faq_reply_with_free_text_not_matching_any_known_keyword_is_not_flagged`, and the
        no-recorded-fact no-op case. `tests/test_tool_handlers.py`, 2 new tests for `on_answered`'s
        fire/no-fire behavior.

**Phase 4b sign-off (2026-08-01)**: ✅ all 3 tasks (4b.1, 4b.2, 4b.3) implemented and independently
verified — the full Tool Output Fidelity backstop now covers `recommend_properties` (name +
capacity), `get_pricing`/`negotiate_rate` (price total), `check_calendar` (availability bool), and
`search_faq` (amenity keywords), all through the same single, already-proven
`PropertyRecommendationGuardProcessor` mechanism rather than four separate new processors. Modified
files: `app/voice/property_recommendation_guard.py` (all four new fidelity checks),
`app/services/tool_handlers.py` (`handle_negotiate_rate`'s new `on_priced`, `handle_check_calendar`'s
new `on_checked`, `handle_search_faq`'s new `on_answered` — `handle_get_pricing`'s `on_priced` already
existed from Phase 4.1 and just gained a second consumer), `app/voice/tools.py` (all four wrappers
wired to feed the guard). New tests: 23 in `tests/test_property_recommendation_guard.py` (bringing
that file to 46 total), 8 in `tests/test_tool_handlers.py`. Full backend suite: 561 passed, same 5
pre-existing baseline failures as every prior phase (`test_call_includes_duration_and_lead_name_phone`,
`test_escalate_to_host_also_saves_lead_so_it_isnt_left_empty`,
`test_search_faq_logs_gap_when_no_verified_answer`, `test_is_complete_short_but_punctuated`,
`test_ice_servers_stun_only_by_default`) — no regressions, confirmed by direct inspection of each
failure's assertion (a phone-format test-data mismatch and a fixture/legacy-FAQ-content mismatch,
both pre-dating this phase). Token/cost impact: zero new prompt tokens (confirmed by inspection — the
guard never imports anything that builds LLM context/system-prompt content, purely a post-generation
text transform) and zero new paid API calls (every callback passes an already-computed in-process
object — `Property`, `PriceBreakdown`, `NegotiationResult`, a bool — through a synchronous closure;
no new DB queries or HTTP calls introduced).

---

---

## Phase 5 — Conversation lifecycle: closing state as a real state, not a per-turn LLM decision (requirement #8)

**The gap**: `end_call`'s mechanism (`docs/agents.md:88-92`, `silence_watchdog.py`) already correctly
arms a hangup only after the closing line finishes playing, and `PrematureEndCallGuardProcessor`
already guards against ending mid-question. What's missing is any *state* the model can read that
says "a goodbye has already been delivered, do not re-enter normal conversation flow unless the
guest explicitly reopens." Today, if the guest says one more thing after the closing line but before
the call actually disconnects (a real, timing-dependent window `docs/agents.md`'s own "if a guest
speaks again... the watchdog cancels the pending end" note already accounts for at the *hangup*
level, `docs/agents.md:92`), nothing stops the *conversation* from silently resetting into a second
awkward "anything else?" cycle rather than recognizing this as a reopened, already-closing call.

- [x] **5.1 — Wire `ConversationState.closing_state`** (declared in Phase 1.1) through the actual
      close sequence: set to `"farewell_pending"` the same turn `end_call`/`decline_irrelevant_call`
      fires (mirroring exactly how `escalated` gets set in Phase 1.1 — both are "a tool call is the
      signal," no new classifier). Set to `"closed"` once `EndWorkerFrame` actually fires (
      `silence_watchdog.py`'s existing hangup logic). If the guest speaks again while
      `closing_state == "farewell_pending"` (the watchdog's own cancellation path,
      `docs/agents.md:92`), reset to `"open"` — this is the explicit-reopen path requirement #8 asks
      for, and it already has a real mechanism (the watchdog's cancellation) to hang off of rather
      than inventing a new one.
      - Verify: unit test sequence — arm `end_call` → confirm `closing_state == "farewell_pending"`
        → simulate a further guest utterance → confirm the watchdog's existing cancel path also
        resets `closing_state` back to `"open"` → confirm a *second* full close-and-goodbye later in
        the same call is treated as a fresh, legitimate close (not blocked as a duplicate), since
        reopening the call was explicit and genuine.

      ✅ Done (2026-08-01). `ConversationState` gained three new methods —
      `mark_farewell_pending()` (`closing_state = "farewell_pending"`, `conversation_goal =
      "closing"`), `mark_reopened()` (`closing_state = "open"`, then re-derives `conversation_goal`
      from current slots via the existing `_recompute_goal` priority order — not hardcoded back to a
      fixed goal, so a call reopened deep into slot-collection lands on the right next step, not
      always "greeting"), and `mark_closed()` (`closing_state = "closed"`, a pure state-honesty
      bookkeeping call with no downstream reader once the call is actually disconnecting).
      `SilenceWatchdogProcessor` (`app/voice/silence_watchdog.py`) — which already owns every
      transition this needed (`request_end_after_current_turn` = armed, `cancel_end_request`/a real
      `TranscriptionFrame` arriving while armed = reopened, the actual `EndWorkerFrame` push = closed)
      — gained an optional `conversation_state` constructor param and now calls the three new methods
      at exactly those three existing points, rather than adding a parallel tracking mechanism.
      `app/voice/pipeline.py` passes `conversation_state` into the existing `SilenceWatchdogProcessor(
      timeout_seconds=9.0)` construction. No changes needed to `end_call`/`decline_irrelevant_call`
      themselves (`app/voice/tools.py`) — they already call `request_end_after_current_turn()`, which
      is now where the state transition lives.
      - Verify: `tests/test_silence_watchdog.py`, 6 new tests — the exact sequence the task specifies
        (`test_guest_speaking_again_resets_conversation_state_to_open`: arm → confirm
        `farewell_pending` → guest speaks → confirm reset to `open`), plus
        `test_premature_end_call_guard_cancellation_also_reopens_conversation_state` (the OTHER real
        cancellation path — a same-turn end_call+question caught by
        `PrematureEndCallGuardProcessor`), `test_end_call_completing_marks_conversation_state_closed`,
        and `test_second_close_later_in_the_same_call_is_treated_as_a_fresh_legitimate_close` (the
        exact "not blocked as a duplicate" requirement — reopening then closing again ends the call
        normally via a real `EndFrame`). `test_no_conversation_state_is_a_no_op_for_closing_lifecycle`
        confirms every existing call site without a `ConversationState` is unaffected.
        `tests/test_conversation_state.py`, 5 new tests for the three methods directly, including
        `test_mark_reopened_resets_closing_state_and_recomputes_goal_from_slots` (confirms the
        re-derivation, not a hardcoded fallback) and the same second-close-is-fresh guarantee at the
        state level. 11/11 and 15/15 passing respectively (existing tests in both files unaffected).
- [x] **5.2 — Surface `closing_state` into the prompt** as a direct instruction rather than asking
      the model to infer "have I already said goodbye" from scrolling back through the transcript:
      when `farewell_pending`, tell the model explicitly not to re-open new topics or ask further
      "anything else?" questions unless the guest raises something new — matching GOLDEN_RULES'
      existing "do not wait for the guest to hang up... do not ask a further question after they've
      already said they're done" clause (`system_prompt.py:345-346`) but backed by real state instead
      of only prose.
      - Verify: real/browser test call exercising the double-goodbye risk directly — guest confirms
        they're done, agent delivers the closing line, guest immediately adds "actually, one more
        thing" before disconnect — confirm the agent handles the reopened question normally (not "I
        already said goodbye" confusion) and, if it closes a second time, that also goes cleanly
        without a repeated/redundant closing phrase (matching the existing "say the escalation phrase
        ONLY ONCE per call" discipline already proven for escalation, `system_prompt.py:180-181`,
        applied here to closings).

      ✅ Done (2026-08-01) — required zero new code. `state_prompt_sync.py`'s `_GOAL_HINTS` dict
      already had a `"closing"` entry from Phase 1.6 ("The call is closing -- don't reopen new topics
      unless the guest raises something new."), just never reachable before now because nothing ever
      set `conversation_goal = "closing"`. 5.1's `mark_farewell_pending()` sets exactly that, so the
      existing goal-hint mechanism picks it up on the very next turn automatically — the same
      `StatePromptSyncProcessor` block that already surfaces slots/recommendations/quoted-price/goal
      to the model every turn, no second injection point. Measured actual token impact: this only
      *replaces* the existing goal-hint line's content (a line that was already being sent every
      turn) with different text of near-identical length (+4 characters in the measured case) — not a
      new line, not new tokens beyond noise. `_GOAL_HINTS["closing"]`'s text was written to
      deliberately echo GOLDEN_RULES' own existing closing-discipline prose
      (`system_prompt.py:345-346`) rather than introduce a differently-worded instruction.
      - Verify: `tests/test_state_prompt_sync.py`, 2 new tests —
        `test_build_state_block_content_includes_closing_hint_once_farewell_is_pending` and
        `test_build_state_block_content_drops_closing_hint_once_reopened` (confirms the hint doesn't
        linger after `mark_reopened()`, so a reopened call is never told it's still closing). No
        browser/live call was run for this task (out of scope for this automated pass — same
        constraint noted in every prior phase's sign-off for `Verify:` items that call for a real
        call); the unit-level chain (armed → hint present → reopened → hint gone → re-armed → hint
        present again) is fully covered by 5.1's and 5.2's tests together, end to end.

**Phase 5 sign-off (2026-08-01)**: ✅ both tasks (5.1, 5.2) implemented and independently verified.
Modified files: `app/voice/conversation_state.py` (`mark_farewell_pending`/`mark_reopened`/
`mark_closed`), `app/voice/silence_watchdog.py` (optional `conversation_state` param, wired at its
three existing transition points), `app/voice/pipeline.py` (passes `conversation_state` through).
5.2 needed no code changes — it was already latent in Phase 1.6's `_GOAL_HINTS` dict, just
unreachable until 5.1 gave `conversation_goal` a path to `"closing"`. New tests: 6 in
`tests/test_silence_watchdog.py`, 5 in `tests/test_conversation_state.py`, 2 in
`tests/test_state_prompt_sync.py` (13 total). Full backend suite: 574 passed, same 5 pre-existing
baseline failures as every prior phase — no regressions. Token/cost impact: zero new prompt tokens
(the closing hint reuses the existing goal-hint slot in the state block, replacing rather than
adding a line) and zero new paid API calls (purely in-process dataclass/state transitions, no new
DB/HTTP calls).

---

## Phase 6 — Voice-friendly response shape audit (requirement #5, #11, #12, #13, #14, #15)

Most of this is already well-covered by existing GOLDEN_RULES clauses — this phase is a targeted
audit for gaps, not a rewrite. Read the specific existing clause cited before adding anything.

- [x] **6.1 — Confirm progressive information collection (requirement #12) already matches the
      Lead Agent workflow.** `LEAD_AGENT_INSTRUCTIONS` steps 2-6 (`system_prompt.py:705-762`) already
      collect dates/guests/location/name/phone progressively, gated on interest signals (step 5,
      "THE MOMENT the guest shows interest... collect their name and phone"). Audit whether
      `GUEST_SUPPORT_INSTRUCTIONS` (`system_prompt.py:420-443`) has an equivalent progressive
      structure or whether it's comparatively unstructured (Guest Support is single-property, so
      there's less to "collect," but confirm name/phone-capture timing there isn't front-loaded
      either). Fix only if a real gap is found — do not add process for its own sake.
      - Verify: re-read both instruction blocks side by side against the requirement's own "avoid
        asking for dates/guests/location/preference/budget all at once" framing; note explicitly if
        no gap is found (a clean audit with no change is itself a valid, real Phase 6 outcome).

      ✅ Done (2026-08-01). Real gap found, not a clean audit: `GOLDEN_RULES`' conversational-warmth
      section (line 331, "Only ask for name/phone once they've clearly decided (see the lead
      workflow's own timing for exactly when)") and `_caller_phone_section` (used by both modes, line
      666, "see the lead qualification workflow's phone-number step") both cross-reference
      `LEAD_AGENT_INSTRUCTIONS` step 5's timing rule — a dangling reference in Guest Support mode,
      since that block is never included in a Guest Support prompt at all. Confirmed this is reachable
      in practice, not just a theoretical gap: `update_lead` is explicitly expected in Guest Support
      mode too (the existing "even in Guest Support mode" rule, line 149), and every Guest Support call
      can reach `escalate_to_host`, which benefits from a phone number being on file. Fixed by adding a
      short, Guest-Support-scoped clause to `GUEST_SUPPORT_INSTRUCTIONS` itself: rarely ask proactively
      (the caller's own number already covers the tools that need one), only ask a phone number when
      none is known and something needs sending/escalating, only ask a name when it would genuinely
      help — never as a routine opener, resolving the dangling cross-reference with real local guidance
      instead of pointing at a block that isn't there. ~167 tokens added to the static system prompt
      (Guest Support mode only), Groq-prefix-cached like the rest of the static prompt.
      - Verify: `tests/test_system_prompt.py`,
        `test_guest_support_has_its_own_name_phone_timing_guidance` — confirms the new clause is
        present in Guest Support and explicitly absent from the Lead Agent prompt (that mode keeps its
        own real step-5 timing, this is not a duplicate). Full `test_system_prompt.py`: 74/74 passing
        (73 pre-existing + 1 new).
- [x] **6.2 — Recovery behaviour (requirement #13) — confirm existing coverage, extend only where
      thin.** Filler-turn handling (`system_prompt.py:258-265`), incomplete-sentence handling
      (`system_prompt.py:255-257`), and the mid-call "hello" rule (`system_prompt.py:266-275`) already
      cover interruptions/silence/partial responses/topic changes reasonably well. The one category
      not explicitly covered: a guest **correcting** themselves ("actually, make that 6 guests, not
      4") — check whether this is reliably handled by the existing re-ask/re-derive rules or needs an
      explicit clause plus a `state.slots` overwrite path (Phase 1.2 already handles the mechanics —
      a later `update_lead` call naturally overwrites an earlier field — this task is just confirming
      GOLDEN_RULES tells the model to actually treat a later statement as an authoritative correction,
      not a duplicate/conflicting value to be confused by).
      - Verify: real/browser test call with an explicit self-correction mid-conversation, confirm the
        corrected value (not the original) is what ends up in `state.slots` and in `Lead` via
        `update_lead`.

      ✅ Done (2026-08-01). Real gap confirmed by direct search (`grep`-ing for "correct"/"actually,"/
      "make that" across the whole prompt file found nothing addressing this) — genuinely zero existing
      coverage, not a near-miss. Confirmed the state-layer mechanics were already correct
      (`ConversationState.set_slot` — `self.slots[key] = value` — a later call naturally overwrites,
      per its own existing docstring), so this was purely a missing PROMPT-layer instruction, not a
      code gap. Added a new `GOLDEN_RULES` clause (shared by both modes) right after the existing
      NEVER-RE-ASK rule (the closest related rule): treat a correction as authoritative, confirm it
      briefly in passing, and — the part beyond just "use the new value" — explicitly re-call any tool
      whose result depended on the old value (e.g. re-run `get_pricing` if guest count/dates changed
      after a quote was already given), so a stale pre-correction quote is never left standing as if
      still accurate.
      - Verify: `tests/test_system_prompt.py`, `test_golden_rules_covers_guest_self_correction` —
        confirms the clause is present in both `build_system_prompt` and `build_lead_system_prompt`
        (shared via `GOLDEN_RULES`). No browser/live call was run for this task (out of scope for this
        automated pass, consistent with every prior phase's sign-off for `Verify:` items that call for
        a real call) — the state-layer half of the guarantee (a later `set_slot` call correctly
        overwrites) already has direct unit coverage from Phase 1.2's own original tests.
- [x] **6.3 — Confirm response-length/markdown/list-pacing rules (requirement #5, #15) have no
      blind spots for the recommendation-explanation text added in Phase 2.** The "15 words or fewer
      per item," "no markdown," "end with 'which one sounds interesting'" rules
      (`system_prompt.py:222-227`) predate Phase 2's `match_reasons` addition — re-check they still
      hold once a match-reason clause is appended per option (2.2's own verification already covers
      this at the pitch-formatter level; this task is confirming GOLDEN_RULES' prose guidance doesn't
      need a companion update once the underlying data shape changed).
      - Verify: re-read `format_property_pitch_line`'s Phase-2 output against this exact rule's
        wording; adjust the 15-word guidance if `match_reasons` regularly pushes past it in practice
        (measured against Phase 0.1's real transcripts wherever possible, not guessed).

      ✅ Done (2026-08-01). Real gap found, measured directly against the actual code rather than
      guessed: even the PRE-Phase-2 baseline pitch line (no match_reasons at all) already measures 19
      words, over the stated "15 words or fewer" ceiling; the realistic Phase-2 case (2 match_reasons,
      the max `match_reasons_for_card` ever returns) measures 31-32 words — roughly double. Resolved
      the apparent tension by confirming what the rule actually governs: `format_property_pitch_line`'s
      raw structured output (including the `(property_id: ...)` aside) was never meant to be read
      verbatim — `system_prompt.py`'s own separate "turn structured results into natural spoken
      sentences instead of reciting them like a list" rule (line 340) already establishes the pitch
      line is a cue for tone/content, not a script. The actual bug was the STATED ceiling no longer
      matching the DATA shape it was written against, which reads as contradictory guidance (compress
      to 15 words vs. include a reason clause that's inherently longer) rather than a real behavioral
      gap. Fixed by widening the guidance to "roughly 15-25 words" and explicitly stating why: enough
      for name, defining features, price, AND the match-reason clause when one exists — never dropped
      to hit a strict count, never padded with invented detail either.
      - Verify: `tests/test_property_card_and_pitch_formatter.py`,
        `test_format_property_pitch_line_word_count_matches_golden_rules_guidance` — measures the real
        fully-populated (2 match_reasons) pitch line and asserts it falls in a range GOLDEN_RULES' own
        wording can plausibly describe (15-40 words, not a strict re-assertion of the exact new
        wording's 15-25, since the raw line legitimately runs a bit longer than the model's expected
        spoken reformulation) — this is the actual data point the wording change is based on, kept as
        a regression test so a future `PropertyCard` field addition that further inflates line length
        gets caught rather than silently drifting the prompt's guidance out of sync with reality again,
        the same failure mode that created this gap in the first place.
- [x] **6.4 — Interruption/barge-in timing audit (requirement #5's "recovery" framing raised this) —
      ✅ confirmed already correctly implemented, no task needed.** Read `pipeline.py:81-98` directly:
      pipecat's VAD already fires an interruption the moment sustained guest speech is detected,
      which cuts off Mira's in-progress TTS immediately — this is pipecat's own built-in barge-in
      behavior, already wired up and already tuned (`_VAD_PARAMS`, `confidence=0.85`,
      `min_volume=0.7`, `start_secs=0.35`). The `start_secs` tuning history (raised from pipecat's
      0.2s default on 2026-07-23, per its own inline comment) is entirely about *false-positive*
      interruptions from background noise/mic bumps, not about failing to interrupt on genuine
      speech — "Mira should stop when the guest starts speaking" is already true today, not a gap.
      No task added; recorded here so this doesn't get silently re-proposed as new work later.
      - Verify: this is itself the verification — confirmed via direct code read, not assumed. If a
        real live call ever shows Mira failing to stop for genuine guest speech, that's a VAD-tuning
        regression to investigate against `_VAD_PARAMS`, not evidence barge-in doesn't exist.

**Phase 6 sign-off (2026-08-01)**: ✅ all 4 tasks (6.1, 6.2, 6.3, 6.4) audited; 6.4 needed no change
(confirmed already correct), 6.1/6.2/6.3 each found a real, data-confirmed gap and fixed it with a
targeted prompt addition — this phase's own premise ("audit for gaps, not a rewrite") held: no
speculative process was added, every change traces to a specific confirmed shortfall (a dangling
cross-reference, a genuinely uncovered recovery case, a stated numeric rule that no longer matched the
real data shape). Modified files: `app/prompts/system_prompt.py` only (52 net new lines: 6.1's
Guest-Support-only name/phone timing clause, 6.2's shared self-correction clause in `GOLDEN_RULES`,
6.3's reworded 15-25-word pitch-length guidance). New tests: 3 in `tests/test_system_prompt.py`
(6.1, 6.2), 1 in `tests/test_property_card_and_pitch_formatter.py` (6.3, the measured data point the
wording change is based on). Full backend suite: 577 passed, same 5 pre-existing baseline failures as
every prior phase — no regressions. Token/cost impact: all three additions are to the static system
prompt (Guest Support gains 6.1's ~167 tokens; both modes gain 6.2's ~150-token shared clause; 6.3 is
a minor reword, +~30 tokens) — Groq-prefix-cached like the rest of the static prompt, no per-turn or
per-call growth. No new paid API calls. No browser/live call run for 6.2's/6.3's `Verify:` items that
called for one (out of scope for this automated pass, consistent with every prior phase) — each is
covered at the level that could actually be automated (prompt-content assertions, and for 6.3, the
real measured pitch-formatter output the wording is based on).

---

## Phase 7 — Full-loop verification (do last)

- [ ] **7.1 — Re-run the Phase 0.2 failure catalogue against a fresh transcript set** (new calls,
      not the same ones used to build the catalogue) and confirm each cataloged issue either no
      longer reproduces or has a clear, explained residual-risk note (e.g. "still probabilistic,
      no code-level guard was feasible here because—"). A plan that "fixes" only the exact examples
      it was written against, and nothing else, hasn't actually generalized — this step is what
      proves or disproves that.

      ⚠️ **Blocked on live call data, not done.** This task requires pulling a fresh set of REAL calls
      placed after every phase's changes were deployed — there is no way to satisfy "confirm each
      cataloged issue no longer reproduces" from code/tests alone, since the whole point is checking
      against live model behavior, not the guard logic in isolation (which IS already covered by each
      phase's own unit tests against the exact catalogued transcript text, e.g. Phase 4.3's tests using
      C3's real text verbatim). Needs: (1) the changes actually deployed/running for some real call
      volume to accumulate, (2) a read-only pull of that fresh transcript set the same way Phase 0.1
      did, (3) a manual or scripted re-check of each of C1-C6/H1-H2 against it. Not attempted here —
      recording the blocker explicitly rather than fabricating a result.
- [x] **7.2 — Full `pytest` suite green**, diffed against the known 4 pre-existing failures
      (`project_state.md`) — confirm zero new regressions across the entire phase set, not just
      per-phase in isolation (an interaction between, say, Phase 3's prompt-injection timing and
      Phase 1's state-block placement is exactly the kind of thing that only shows up when
      everything is combined).

      ✅ Done (2026-08-01). Final full-suite run after every phase (0 through 6) merged together:
      **577 passed**, same **5** pre-existing failures the whole plan has tracked since Phase 1's own
      sign-off (`test_call_includes_duration_and_lead_name_phone`,
      `test_escalate_to_host_also_saves_lead_so_it_isnt_left_empty`,
      `test_search_faq_logs_gap_when_no_verified_answer`, `test_is_complete_short_but_punctuated`,
      `test_ice_servers_stun_only_by_default` — 5, not the 4 this task's original text estimated;
      corrected against the actually-observed, consistently-reproduced count every single phase since
      Phase 1 has confirmed, not the earlier guess). This is the FULL combined suite, not a per-phase
      slice — confirms no cross-phase interaction regression (e.g. Phase 5's `closing_state` freezing
      `conversation_goal` derivation vs. Phase 1's slot-priority goal logic; Phase 4.3's response-shape
      guard sitting after Phase 4.2's repetition guard in the real pipeline order) actually broke
      anything once everything runs together, not just each phase's own isolated test file.
- [x] **7.3 — Prompt token budget final check** — total assembled prompt size (both modes) vs. the
      Phase 0.3 baseline, accounting for every phase that added content (1.3 slots block, 3.2
      language instruction, 4.1 already-shown facts) and every phase that removed/bounded it (4a's
      compaction, if built). If the cumulative addition is large, revisit whether every piece earns
      its place, per `memory-architecture-plan.md §0.1`'s original token discipline.

      ✅ Done (2026-08-01). The real Phase 0.3 pilot host (Pause Projects, 17-property portfolio) lives
      only in the production Neon DB (`DATABASE_URL` in `.env`) — querying it directly wasn't safe to
      do in this pass (the new `agent_language_policy` column, Phase 3.3, was deliberately never
      migrated onto production per this plan's own standing discipline of not touching prod without
      explicit authorization, so a raw query against that table errors; more importantly, re-deriving
      from a live production database isn't a read I should do casually mid-audit without asking).
      Instead measured against synthetic property/host data built to the SAME content scale Phase 0.3
      described (rich house_rules/neighborhood_info/amenities/FAQ for the single richest Guest Support
      property, 17 properties for the Lead Agent portfolio) — same instruction-block content
      (`GOLDEN_RULES`/`GUEST_SUPPORT_INSTRUCTIONS`/`LEAD_AGENT_INSTRUCTIONS`, every phase's additions
      already merged in), comparable-scale data, not the literal same DB row.
      - **Guest Support**: 8,952 → 9,519 tokens (chars/4 approx, same methodology as Phase 0.3's own
        measurement) — **+567 tokens, +6.3%**.
      - **Lead Agent**: 9,900 → 10,389 tokens — **+489 tokens, +4.9%**.
      - Both deltas are consistent with the sum of every phase's own already-individually-measured
        contribution (Phase 1.3's slots block ~102 tokens fully populated, Phase 3.2's language
        instruction, Phase 4.1's quoted-price line, Phase 6.1's ~167-token Guest-Support-only clause,
        Phase 6.2's ~150-token shared self-correction clause, Phase 6.3's minor reword) — no
        surprise/unaccounted growth. Both remain modest relative to the ~9,000-10,000 token baseline
        (under 7% either way), and per Phase 0.3's own framing, `GOLDEN_RULES` itself (not this plan's
        additions) remains the dominant cost and the actual lever if prompt cost ever needs to come
        down — nothing in this plan's cumulative addition rises to the level of needing that
        conversation. No `4a` compaction mechanism was built (Phase 4a.2's own sign-off concluded it
        wasn't needed at measured real-call growth rates), so there's no offsetting reduction to net
        against — the full addition above is the real, final number.
- [ ] **7.4 — Real/browser call sign-off**: at minimum, one full call exercising language-switching
      (Phase 3), a multi-property recommendation with reasons (Phase 2), a self-correction (Phase
      6.2), and a natural close (Phase 5) all in the same conversation — the realistic "everything at
      once" case a real Superhost call actually looks like, not six isolated feature checks.

      ⚠️ **Blocked on a live/browser call session, not done.** This explicitly requires placing a real
      call (or driving the dashboard's talk-to-Mira browser test UI) — there is no code-only substitute
      for confirming the actual end-to-end voice pipeline handles all four features correctly in
      combination, live. Every individual feature has unit/integration test coverage in isolation
      (Phase 3's language tests, Phase 2's recommendation tests, Phase 6.2's prompt-content test, Phase
      5's closing-lifecycle tests) — what this task specifically checks is the COMBINATION behaving
      correctly under real pipecat/Groq/Sarvam timing, which passing unit tests cannot substitute for.
      Not attempted here.
- [ ] **7.5 — Quantitative response-quality metrics, computed from real transcripts, not manual
      spot-checks.** Every other verification step in this plan checks "did the specific catalogued
      bug reproduce" — a necessary but narrow lens. Add a small offline script (reusing the same
      read-only transcript-pulling approach as Phase 0.1, run against a fresh post-implementation
      sample) computing, per call and aggregated:
      - Average words per assistant response, and average distinct question-marks per response (a
        cheap proxy for Phase 4.3's "one objective per response" goal — this number should measurably
        drop if 4.3 is working, not just "feel" better).
      - Repeated-sentence rate (near-duplicate word-overlap fraction across a call, the same metric
        `RepetitionGuardProcessor` already computes internally — expose it as a per-call aggregate
        rather than only a live pass/fail).
      - Recommendation acceptance rate (did the guest's subsequent tool calls/lead data reference one
        of the actually-recommended properties, a rough proxy for whether Phase 2's explanations are
        landing).
      - Turns-before-first-recommendation and turns-before-lead-capture (direct measurements of Phase
        2.3's "recommend before interrogation" goal and the progressive-collection goal in Phase 6.1
        — currently only judged by hand-reading a few transcripts).
      - Turns-before-escalation (where applicable) and language-switch latency (turns between a
        detected guest language change and the reply adopting it — direct measurement of Phase 3's
        actual effectiveness, not just a pass/fail on one test call).
      This does not replace any per-phase verification above — it's a cross-cutting measurement layer
      so future changes to this system have a quantitative baseline to compare against, instead of
      re-reading transcripts by hand every time, per this codebase's own established discipline of
      measuring rather than asserting (Standing Rule 1, Phase 0's entire premise).
      - Verify: run the script against the Phase 0.1 baseline sample first (recording pre-plan
        numbers, retroactively — same transcripts, so this is a fair comparison point) and again
        against a fresh post-implementation sample; report both sets of numbers side by side. A
        metric moving the wrong direction after implementation is a real signal worth investigating
        before declaring this plan complete, not something to explain away.

      ⚠️ **Blocked on live call data, not done.** Same underlying blocker as 7.1/7.4: this task's own
      text requires "a fresh post-implementation sample" of real calls — there is no fresh
      post-implementation sample to compute these metrics against without real call volume
      accumulating after deployment first. The script itself (words/response, question-marks/response,
      repeated-sentence rate, recommendation acceptance rate, turns-before-X, language-switch latency)
      is straightforward to write once real transcripts exist to run it against, reusing Phase 0.1's
      own read-only transcript-pulling approach — but writing it now against zero real
      post-implementation data would produce either an empty report or numbers computed against the
      Phase 0.1 baseline sample re-labeled as "after," which would misrepresent the change as measured
      when it wasn't. Not attempted here for that reason, not out of scope.

**Phase 7 status (2026-08-01)**: 7.2 and 7.3 ✅ done — the two tasks answerable from code/tests alone.
7.1, 7.4, 7.5 ⚠️ explicitly blocked on live call data / a real or browser test call, which this
automated pass cannot generate — each is documented above with exactly what it needs to run, so it can
be picked up directly once real post-implementation call volume exists, rather than silently marked
done or skipped. This is not a gap in the underlying work: every phase (0 through 6) already carries
its own real, phase-scoped verification (unit tests against real catalogued transcript text where
applicable, e.g. Phase 4.3's C3 reproduction; direct code reads for audit-only findings like Phase
6.4) — Phase 7's remaining tasks are specifically the CROSS-CUTTING, real-call-volume checks that only
make sense once the combined system has been live for some period, which is a natural, expected
stopping point for an implementation pass, not a shortcut taken to avoid them.

---

## Requirement coverage map

Every numbered requirement in the source goal traced to where it's addressed, so nothing in the
original ask silently fell through the cracks during planning:

| # | Requirement | Where addressed |
|---|---|---|
| 1 | Robust Language Adaptation | Phase 3 (3.1, 3.2 passive mirroring; 3.3 explicit-preference override, reframed from a script ban per review) |
| 2 | Follow the Guest (pace/tone/style) | Already well-covered by existing `GOLDEN_RULES` conversational-warmth clauses (`system_prompt.py:295-331`) — no dedicated phase; re-confirm during Phase 7.1's transcript review rather than building net-new mechanism for something already working |
| 3 | Recommendation Before Interrogation | Phase 2.3 |
| 4 | Explain Recommendations | Phase 2.1, 2.2; Phase 2.6 (confidence-aware phrasing, added per review) |
| 5 | Voice-Friendly Responses | Phase 2.2, Phase 6.3; Phase 4.3 (Response Shape Validator, added per review — one objective per response is a voice-friendliness requirement as much as a repetition one) |
| 6 | Avoid Repetition | Phase 4; Phase 4.3 (added per review — see that phase's note on why C3 needed a *different* mechanism than 4.1/4.2's text-similarity approach) |
| 7 | Conversation State Awareness | Phase 1 (foundational), including 1.5/1.6's `conversation_goal` tracking (added per review — closes the "question → recommendation → question → another recommendation" drift the original Phase 1 draft didn't yet address) |
| 8 | Ending Conversations Correctly | Phase 5 |
| 9 | Self-Consistency | Phase 1.3, 1.4; Phase 4b (Tool Output Fidelity, added per review) |
| 10 | Recommendation Validation | Phase 1.4, Phase 2.4; Phase 4b.2 (capacity-fidelity check, added per review as belt-and-suspenders beyond the SQL-layer fix) |
| 11 | Natural Hospitality Behaviour | Already extensively covered by existing `GOLDEN_RULES` (`system_prompt.py:295-331` — reactions, opener variety, name-usage cadence, bridging phrases) — no dedicated phase, same reasoning as #2; this is prose-only by nature (there's no code-level way to enforce "sound warm") so it stays a prompt-quality concern, re-audited in Phase 7.1 |
| 12 | Progressive Information Collection | Phase 6.1 |
| 13 | Recovery Behaviour | Phase 6.2; Phase 6.4 (barge-in audit, added per review — confirmed already working, no task needed) |
| 14 | Robustness Against Imperfect Speech | Already covered by existing filler-turn/incomplete-sentence `GOLDEN_RULES` clauses (`system_prompt.py:255-265`) — referenced, not rebuilt, in Phase 6.2's preamble |
| 15 | Response Quality (the 10-point checklist) | Cross-cutting — Phase 6.3 directly, Phase 4.3 (Response Shape Validator) as the final structural gate, Phase 7.5 (quantitative metrics, added per review) as the measured version of this checklist rather than a manual read |
| 16 | Engineering Expectations (code enforcement over prompt-only) | Standing Rules 2-4 govern every phase; Phase 1/3.3/4/4.3/4b/5 are the concrete instances of "state or guard, not just prose" |

**Production-readiness additions beyond the original 16 requirements** (per review, since "ready to
work for multiple hosts, using the agent at the same time" surfaces concerns the original
per-conversation-quality framing didn't fully anticipate):
- **Phase 4b — Tool Output Fidelity**: the general-purpose fix for the class of bug C1/C2 belong to
  (model reinterpreting correct tool output before speaking it), generalizing
  `PropertyRecommendationGuardProcessor`'s existing pattern rather than treating C1/C2 as two
  isolated data bugs.
- **Phase 4.3 — Response Shape Validator**: the final structural gate before TTS, catching
  malformed/concatenated/duplicated response shapes that repetition-detection alone structurally
  cannot catch (different sentences glued together have low word-overlap by construction).
- **Phase 2.5 — Recommendation diversity**: relevant specifically at multi-host, real-call-volume
  scale — a single deterministic price-ascending sort recommends the identical top property to every
  guest with similar criteria, which only becomes visible as a real host-fairness concern once call
  volume is high enough to notice the pattern (exactly the "multiple hosts, at the same time"
  condition this feedback round raised).
- **Phase 7.5 — Quantitative metrics**: replaces "read transcripts by hand and judge" with numbers
  that scale across many hosts/calls, which manual review does not.

---

## Non-goals (explicit, per the codebase's own established discipline)

- **No RAG/vector/embeddings pipeline.** `system_prompt.py:3-5` already states this decision
  explicitly and `memory-architecture-plan.md:290-297` already re-confirmed it's still correct at
  this call volume. Nothing in this plan needs one either — every gap identified above is a
  state-tracking or prompt-structuring gap, not a retrieval-quality gap.
- **No new persistence layer for in-call state.** Per Standing Rule 4 — `ConversationState` stays a
  plain per-call Python object, not a DB table or Redis key.
- **No host-specific or property-specific special-casing.** Every fix must hold for any host's
  portfolio.
- **No rewrite of the six existing pipeline guards** — they're extended (Phase 3.3 [now 4b, see
  reframing note], Phase 4.2) or left alone, never replaced, since each already has a confirmed-live
  bug behind its existence. Phase 4.3/4b are new guards added to the same chain, following the exact
  same design discipline (deterministic, mechanical, fail-open, inert on the common case) as the six
  that already exist — not a departure from this principle.
- **No code-level script ban (Devanagari or otherwise).** Reframed during review (Phase 3.3/3.4):
  the original draft's Devanagari guard would have hardcoded one region's conversational norm
  (urban Hinglish) as the only supported one, which conflicts with this plan's own Standing Rule 6
  ("must generalize across any host's portfolio... across India"). The mechanism is honoring an
  explicit or host-configured language preference, not policing a script.
- **No LLM-self-reported confidence score.** Phase 2.6's confidence-aware phrasing is grounded in
  real, already-computed signals (result count, combo-fallback firing) — asking the model to state
  its own numeric confidence would itself be an ungrounded, hallucination-shaped output, the exact
  problem class this plan is trying to reduce elsewhere.
- **No second LLM call judging the first LLM's output.** Phase 4.3's Response Shape Validator and
  Phase 4b's Tool Output Fidelity checks are both deliberately mechanical/deterministic (string
  matching, structured-value comparison) — never "ask a second model if the first model's response
  looks right," which would just add a second unreliable judgment on top of the first rather than a
  real guarantee.
