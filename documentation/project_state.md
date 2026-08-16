# Project State

Living snapshot for session continuity — not a chronological log. See `CLAUDE.md` and `docs/` for stable reference material. The section immediately below (**Status summary**) is the current authoritative snapshot, verified directly against source on 2026-08-09 (a documentation-only architecture sync pass — no application code was changed to produce it). Everything under "Recent fixes" further down is the historical, reverse-chronological log this summary was derived from — preserved, not superseded.

## Active branch

`abhaya` (current working branch as of 2026-08-09). `main` is current HEAD of the merged history; see git log for the latest merged commit.

## Status summary (2026-08-09)

See [current_architecture.md](current_architecture.md) for the full technical picture behind every line below.

### Currently implemented

- **Voice pipeline core** (Exotel + Twilio Voice + 2 browser-test entry points, one shared `_run_pipeline`) — STT/LLM/tools/TTS, full guard chain, streaming response output (not buffered — see below). Committed on `main`.
- **Conversation architecture**: `ConversationState` (facts/slots/goal), `ConversationStyle` (hysteresis-smoothed language/tone via `StyleEngine`), `ConversationQuality` (observational validator output) — three separate types, committed on `main`. `StyleComplianceMonitor` (streaming, no LLM regeneration) replaced the old buffering `ResponseComplianceProcessor`, also committed.
- **Streaming response architecture**: every guard from `RepetitionGuardProcessor` through `ResponseShapeValidatorProcessor` forwards `LLMTextFrame`s immediately as they stream from the LLM, rather than buffering a full response before deciding whether to forward it. This is real and committed, not aspirational.
- **Response validation without hidden regeneration**: every corrective mechanism (7 guards + `StyleComplianceMonitor`) either deterministically rewrites/truncates already-generated text, or nudges the *next* turn's prompt — nothing calls the LLM a second time mid-turn to "fix" a response.
- **Conversational flexibility / ambiguous-date handling / multi-property recommendations**: implemented — refinement turns ("something cheaper", "closer to Candolim"), relative budget/guest-count resolution, amenity accumulation across turns, match-reason/comparison-note explanations, recommendation diversity rotation, availability pre-filtering. See the 2026-08-05 entries below for the detailed build history.
- **Language/Hinglish adaptation**: implemented at two layers — `LanguageSyncProcessor` (live TTS voice switch, per-utterance) and the newer `ConversationStyle`/`StyleEngine` (hysteresis-smoothed, multi-turn language-family commitment surfaced into the prompt). Explicit guest-stated language preference (`update_lead(preferred_language=...)`) and host-level policy (`User.agent_language_policy`) both wired.
- **`CallCoordinator`**: Redis-backed lease/concurrency authority, fully implemented — `acquire_or_reject`/`renew`/`release`/`transfer`, atomic Lua scripts, fail-open-with-loud-logging on Redis outage. Wired into both `run_voice_pipeline` (Exotel) and `run_voice_pipeline_twilio`, including the periodic lease-renewal background task (a previously-shipped-but-uncalled gap, now fixed and called). **Uncommitted** — see below.
- **Redis-based call leasing**: complete, not partial. `CallLease` (Postgres) is fully superseded — staged for removal, confirmed nothing writes to it anymore. **Uncommitted.**
- **Busy Call Recovery**: fully implemented end-to-end — `RecoveryService`, guest+host WhatsApp (template + plain-text fallback), inbound WhatsApp reply routing (`whatsapp_reply_service.py`) with a 72h/reusable-status-guarded lead-resolution window, Recovery Analytics (`GET /analytics/recovery`). **Uncommitted.**
- **Lead preservation**: three independent, verified mechanisms — in-call `update_lead`/`escalate_to_host`, the `ensure_lead_for_engagement` system-level safety net (fires on `get_pricing`/`negotiate_rate`/`check_calendar` regardless of whether the LLM calls `update_lead`), and Busy Call Recovery's own lead creation for calls that never reach the LLM at all.
- **WhatsApp recovery / conversation continuation**: implemented — the guest-facing recovery menu (Property/Pricing/FAQs/Photos/Talk-to-host/Something-else), defined once (`whatsapp_reply_service._MENU_OPTIONS`) and shared by both the outbound send and inbound parser so they can't drift apart.
- **Dashboard opportunities/recovery UI**: `frontend/src/app/dashboard/opportunities/page.tsx` + `opportunities-card.tsx`/`opportunity-list.tsx`/`recovery-analytics-card.tsx`, real-time via `use-notification-stream.ts`. **Uncommitted**, alongside the backend it reads from.
- **Host notifications**: `Notification` model extended with `lead_id` (indexed) + `responded_at`, new channels `busy_recovery`/`busy_recovery_reply` alongside existing `whatsapp`/`escalation`/`system`. **Uncommitted** (model/service changes only — the `whatsapp`/`escalation`/`system` channels themselves are pre-existing and committed).
- **Cross-call analytics/learning surfaces** (`docs/tasks/building-intelligence.md`, 2026-08-16) — four pieces closing the gap between "MIRA stores call data" and "MIRA surfaces patterns across calls," each read-only/human-facing, none autonomously feeding back into live pricing/negotiation or the voice pipeline: (1) `CallQualityEvent` — persists `ConversationQuality`'s guard/validator firings per call (previously discarded at call-end) for cross-call querying, written from `on_pipeline_finished` only, `ConversationQuality`'s own live/read-side behavior untouched; (2) `CallSummary.objection_tags` — a controlled 8-value vocabulary (`PRICE_TOO_HIGH`/`DATES_UNAVAILABLE`/etc./`NO_OBJECTION`) extending the existing one-shot post-call summarization prompt, zero new LLM calls; (3) `GET /analytics/quality-events` — guard-firing frequency analytics, modeled directly on `faq_service.faq_gap_analytics`'s delegation pattern; (4) `GET /analytics/objection-insights` + a Pricing-page card — conversion rate by objection tag vs. baseline, with a resolved/unresolved breakdown (a tag fires whether or not the objection was overcome) and a low-sample-size caveat below 5 calls. All four implemented+reviewed; **uncommitted**. One known gap: the Implementation 4 frontend card's actual rendering was never visually confirmed (see Known limitations below).

### In progress / uncommitted (implemented and tested, not yet merged to `main`)

Per `git status`/`git diff --stat HEAD` on 2026-08-09, the entire Redis-lease/Busy-Call-Recovery/WhatsApp-reply subsystem is real, working code in the local working tree but has not been committed: `app/models/call_lease.py`, `app/services/call_coordinator.py`, `app/services/recovery_service.py`, `app/services/whatsapp_reply_service.py`, `app/api/v1/webhooks/whatsapp.py`, `app/utils/webhook_auth.py`, associated Alembic migrations (`356d5c923c77`, `3fae82f7b3d0`, `6384600c83f2`, `7a236ad1ffd1`), the frontend opportunities pages/components, and matching test files. `docs/agents.md`/`docs/database.md`/`docs/api.md` already describe this subsystem as current (they were written/updated alongside the code) — treat them as accurate regardless of commit status, but be aware a fresh `git clone` of `main` alone would not yet include any of it.

By contrast, `ConversationStyle`/`ConversationQuality`/`StyleComplianceMonitor`/the streaming response-output rewrite **are already committed** on `main` (see commits `5a8e1bc`, `1b2b36f`, and later) — only the Redis-lease/recovery/WhatsApp-reply work above is uncommitted.

### Known limitations

- `CallLease` (Postgres table/model/migration) still physically exists in the schema, staged for removal — a future session could mistakenly assume it's active without reading `call_coordinator.py`'s own docstring first.
- Twilio WhatsApp is Sandbox-only (not a Meta-approved WhatsApp Business number) — 24h customer-service-window constraint applies to both Busy Call Recovery sends and ordinary `send_whatsapp`/`send_photos`/escalation sends.
- No `tiktoken`-exact token counts anywhere in this codebase's own measurements — all prompt-size figures in this file and `agent-conversation-improvement.md` are `chars/4` approximations, consistently flagged as such where they appear.
- No real/browser voice call has been placed against several of the conversation-architecture phases in this environment (Clerk-only auth + WebRTC not scriptable here) — verification for those relies on direct construction-path runs against real DB data plus `pytest`, not an actual audio call. See `agent-conversation-improvement.md`'s per-phase sign-offs for exactly which.
- `search_faq`'s `UnansweredQuestion` gap-logging under-reports for any property with a known `property_id`, since `full_property_context()` always returns *something* as a last resort (documented pre-existing tradeoff, not revisited).
- The Pricing page's new "Objection insights" card (`docs/tasks/building-intelligence.md` Implementation 4) has never been visually confirmed in a browser — same root cause as the pre-existing "no real/browser voice call" limitation above (Clerk dev-mode login isn't scriptable here without risking a real Google OAuth navigation; two automated attempts did exactly that and were stopped deliberately). Everything else about the card (backend query correctness, tenancy isolation, `tsc --noEmit`, close reading of the JSX for interpolation/conditional bugs) was verified; only actual rendering/layout/dark-mode was not.

### Known risks

- The entire Redis-lease/Recovery/WhatsApp-reply subsystem being uncommitted means it exists only in this working tree — not backed up via git history, not on any other branch, not deployed. A lost/reset working tree would lose real, tested, working code with no recovery path other than this session's own history.
- `CallLease`'s staged-removal state depends on human follow-through ("drop it in a later cleanup phase") — if that phase never happens, the dead table/model persists indefinitely as a documentation/maintenance hazard (low severity, not urgent).
- Redis is a single point of failure for busy-call protection specifically (not for the live call itself, which fails open) — if Redis is down for an extended period, double-booking protection is silently degraded for that entire window, discoverable only via log-based alerting on `lease_redis_unavailable`, not any dashboard signal today.

### Next priorities

- Commit the Redis-lease/Busy-Call-Recovery/WhatsApp-reply working tree (currently the single largest gap between what's real and what's on `main`).
- Decide and execute the `CallLease` Postgres table drop, once the Redis-backed path has run in production long enough to trust (per its own staging docstring).
- A real live/browser voice call to close out the outstanding verification gaps flagged throughout `agent-conversation-improvement.md`.
- Re-run a fresh transcript-based catalogue audit (Phase 7.1-equivalent) now that the conversation-style/quality/streaming rewrite has shipped, to confirm the original catalogue items (C1-C6, H1-H2) still stay fixed under the new architecture.

---

## Recent fixes

**2026-08-10 — Conversation attention/salience tracking: repetition + recency weighting, feeding both
the LLM-facing state summary and amenity ranking. Deliberately narrower than "attention mechanism" as
an ML term implies -- no neural attention layer, no LLM-based emphasis classification; purely
deterministic, derived from tool-call activity, same discipline every other `ConversationState` field
already follows (confirmed via explicit AskUserQuestion scoping before writing any code: repetition +
recency only, no emphasis-word/sentiment text classification).**
- **`Salience` + `ConversationState.attention: dict[str, Salience]`** (`app/voice/conversation_state.py`)
  -- a small, generic primitive (count + last_turn per string key, half-life-decayed score), not
  amenity- or slot-specific. `touch_attention(key)`/`attention_score(key)`/`advance_turn()` are the
  only entry points; `attention` itself is never mutated directly. Half-life defaults to 6 turns,
  reusing `conversation_style.py`'s own `DEFAULT_ROLLING_WINDOW` constant for consistency rather than
  inventing a second arbitrary number.
- **Turn counter**: `ConversationState.turn_index`, advanced from `ConversationStyleProcessor`
  (`app/voice/conversation_style.py`) on every real guest `TranscriptionFrame` -- reuses that
  processor's own existing "one real utterance = one turn" firing point (already used for its own
  local hysteresis counter) rather than adding a new pipeline stage just to increment one shared
  counter.
- **`set_slot` auto-touches attention, but only on a genuine value CHANGE, not every call.** A real bug
  caught before it shipped: `recommend_properties`'s wrapper (`app/voice/tools.py`) calls
  `set_slot("num_guests", effective_num_guests)` on *every* call, including calls where
  `effective_num_guests` was silently backfilled from `state.slots` itself because the model omitted
  the field -- that's bookkeeping, not the guest restating anything, and would have inflated the
  repetition signal on nearly every turn if touched unconditionally. `set_slot` now compares the new
  value against what's already stored and only touches attention when it actually differs (including
  the first-ever set) -- covered by
  `test_set_slot_does_not_touch_attention_when_value_unchanged` in the new
  `tests/test_conversation_state_attention.py`.
- **Amenities get their own explicit touch** (`app/voice/tools.py`'s `recommend_properties` wrapper),
  since they accumulate as a list rather than overwrite and so can't piggyback on `set_slot`'s
  per-field semantics -- only amenities present in *that call's raw* `required_amenities` argument are
  touched, never the full accumulated `effective_amenities` list (which would re-touch every
  previously-mentioned amenity on every subsequent call). Canonicalized via
  `amenity_taxonomy.canonicalize_amenity` at touch time so "pool"/"private pool"/"swimming pool" all
  accumulate onto one attention entry instead of three separately-weaker ones.
- **Prompt-facing consumer** (`app/voice/state_prompt_sync.py`'s `_format_slots`, now takes the full
  `ConversationState` instead of just `state.slots`): known slots are ordered by attention score
  (most emphasized/most recently restated first, not `_SLOT_LABELS`' fixed dict order), and a slot
  restated 2+ times gets an explicit `"(guest has restated this Nx -- weigh it heavily)"` annotation.
- **Ranking-facing consumer**: `filter_builder.apply_amenity_boost` gained an optional
  `amenity_weights: dict[str, float]` parameter (keyed by canonical amenity name, default 1.0 per
  amenity when unset/missing -- every existing caller passing nothing reproduces the original flat
  match-count ranking exactly, confirmed by
  `test_apply_amenity_boost_no_weights_reproduces_flat_match_count_ranking`). Threaded straight through
  `sql_search.run_sql_search` -> `orchestrator.recommend_properties` -> `tool_handlers.
  handle_recommend_properties`, same explicit-optional-parameter shape `check_in`/`check_out` already
  established for "real data ConversationState has that isn't part of the LLM-facing
  `RecommendPropertiesArgs` schema." Built in `app/voice/tools.py` from
  `1.0 + state.attention_score(f"amenity:{canonical}")` for every amenity in the accumulated set.
  **Important, deliberately-tested asymmetry**: because scoring is additive with a weight floor of 1.0,
  a property matching MORE requested amenities always still outranks one matching fewer, regardless of
  weighting -- attention only ever breaks a tie among properties matching the SAME number of requested
  amenities (a first version of the end-to-end test asserted the stronger, false claim that a single
  heavily-emphasized amenity could outrank two flatly-matched ones; caught by hand-computing the actual
  weighted sums before trusting the assertion, not by a failing test -- see
  `test_recommend_properties_amenity_attention_breaks_a_match_count_tie_by_emphasis`'s docstring in
  `tests/test_recommend_properties_refinement.py` for the corrected, mathematically-valid scenario).
- **Untouched**: `ranking.py`, `semantic_search.py`, `calendar_service.py`, every other
  `ConversationState` field/method, `ConversationStyle`/`ConversationQuality` (attention is a
  `ConversationState` fact -- HOW-Mira-speaks and validator/quality data were both explicitly ruled out
  as the wrong home per the three-type discipline in `CLAUDE.md`/`current_architecture.md` §6 before
  writing any code).
- 26 new tests: 12 in new `tests/test_conversation_state_attention.py` (Salience scoring/decay,
  touch_attention/attention_score/advance_turn, set_slot's change-detection gate including the
  backfill-noise case), 4 in `test_state_prompt_sync.py` (ordering, annotation, annotation thresholds,
  backfill-not-annotated), 3 in `test_property_retrieval_filter_builder.py` (no-weights parity,
  weighted-tiebreak, missing-weight-defaults-to-one), 3 end-to-end in
  `test_recommend_properties_refinement.py` (attention only touched for newly-mentioned amenities,
  canonicalization dedup across repeated calls, the real tie-break ranking flip through the full
  tools.py -> ... -> filter_builder chain). Full `pytest`: 919 passed (925 baseline pre-existing minus
  the same historically-documented pre-existing failures, all confirmed unrelated by inspecting each
  failure directly -- a phone-format assertion, an FAQ-fallback-text assertion, environment-only
  failures needing real network/DB-pool/SMTP/embedding-API access unavailable in this environment),
  zero regressions.

**2026-08-05 — Recommendation conversations ("Phase X"): support guest refinement turns ("something
cheaper", "anything with a pool?", "closer to Candolim", "larger", "more premium", "pet friendly")
without restarting retrieval. Two real, user-approved decisions grounded this phase, not silently
picked in code:**
- **`is_premium: bool` (new `Property` column, host-set only, never LLM-inferred)** grounds "something
  more premium" in a real fact instead of an LLM guess about which property "feels" nicer -- chosen
  over a numeric tier or a price-percentile-derived approach (explicit AskUserQuestion decision).
  Migration `0a8ae066bf5c_add_is_premium_to_properties.py`, hand-trimmed to exclude unrelated
  autogenerate drift (a `refresh_tokens` table drop, an `ix_host_discount_rules_host_id` index drop, a
  `saturday_minimum_stay_enabled` column drop -- same precedent as
  `8818413a6d0a_add_exact_airbnb_pricing_to_properties.py`). Exposed via `PropertyUpdate`/`PropertyOut`
  (not `PropertyCreate` -- same set-after-creation precedent as `exact_airbnb_pricing`).
- **`required_amenities` converted from a hard SQL `WHERE` filter to a soft ranking preference**
  (`apply_amenity_boost` in `filter_builder.py`) -- an explicit, deliberate retrieval-semantics change,
  escalated via AskUserQuestion rather than silently decided, because the old hard filter structurally
  prevented a partial-match property from ever being returned or spoken about. Per explicit product
  direction: if a property has a pool but isn't pet friendly, say so explicitly so the guest can decide
  -- never silently exclude it. `sql_search.py` widens its candidate pool to 10 (from 3) whenever
  `required_amenities` is set, since the boost has nothing to promote if the SQL query already capped
  to the 3 cheapest before ranking ever ran; re-capped to 3 after boosting.
- **Amenities ACCUMULATE across calls, never replace** (also an explicit, deliberate decision, not
  silently picked): a guest asking for "pool" then later "pet friendly" almost certainly means both --
  `tools.py`'s `recommend_properties` wrapper now merges `state.slots["required_amenities"]` with
  whatever the model passes this call, same backfill discipline Phase 1.4 already established for
  `num_guests`/`budget`, now also extended (backfill-on-omission, not merge) to `preferred_location`/
  `purpose_of_stay` -- previously written to state but never read back, so a follow-up refinement call
  omitting them silently dropped earlier criteria.
- **`amenity_checklist_note(required_amenities, real_canonical_amenities)` in `card.py`** -- states both
  present AND missing amenities explicitly ("has pool but not pet friendly") for a genuinely partial
  match (2+ requested, neither all-matched nor all-missing); no-ops for a single amenity (already
  covered by `match_reasons_for_card`'s own clause) or an all-matched/all-missing card. New
  `PropertyCard.amenity_checklist: str` field, backfilled via `dataclasses.replace` in
  `context_builder.py` (real full `amenity_tags` aren't available until after the base card is built),
  joined into the same spoken clause as `match_reasons`/`comparison_note` in `pitch_formatter.py` --
  never a second sentence.
- **`cheaper_than_shown`/`larger_than_shown`/`more_premium_than_shown` (new `RecommendPropertiesArgs`
  booleans)** -- relative refinement signals resolved into real numbers by two new
  `ConversationState` methods, `resolve_cheaper_budget()` (10% below the cheapest shown) and
  `resolve_larger_num_guests()` (one above the largest shown), never LLM-invented figures. Scoped to
  search the FULL portfolio using the derived threshold (explicit AskUserQuestion decision), not
  restricted to only already-shown properties. An explicit absolute value given the same call always
  wins outright over the relative flag. `apply_premium_boost` in `filter_builder.py` ranks
  `is_premium=True` properties first without ever dropping non-premium ones (a host with zero premium
  properties set still gets a normal result, just no reordering).
- **New `GOLDEN_RULES` clause** (`system_prompt.py`, shared by both modes): refinement is additive to
  everything already established this call, never a replacement; use the relative booleans instead of
  inventing a rupee figure or guest count; pass every accumulated amenity, not just the newest; speak
  the amenity checklist (present vs. missing) explicitly so the guest can decide. Token cost: this
  phase's addition alone is ~1,677 chars (**~419 tokens**) on top of the pre-phase `GOLDEN_RULES` of
  ~34,202 chars (~8,550 tokens) -- current total 35,879 chars (~8,969 tokens), measured directly via
  `len(GOLDEN_RULES)`, not assumed.
- **Untouched, confirmed via `git status`**: `orchestrator.py`, `ranking.py`, `semantic_search.py`,
  `calendar_service.py`, `StatePromptSyncProcessor`, all 7 guards, and every existing `ConversationState`
  field/method beyond the two new resolvers. `filter_builder.py`, `sql_search.py`, and
  `context_builder.py` WERE deliberately touched, as approved.
- **A real pre-existing test caught encoding the OLD hard-filter behavior, fixed rather than left
  broken**: `test_tool_handlers.py::test_recommend_properties_filters_by_required_amenity` asserted a
  non-matching property was excluded entirely -- exactly the behavior this phase deliberately changed.
  Renamed to `test_recommend_properties_ranks_required_amenity_match_first_without_excluding_others` and
  updated to assert the amenity-matching property ranks first while the non-matching one is still
  returned, not silently left red.
- **Repeated frozen-dataclass breakage from the two new required `PropertyCard` fields
  (`is_premium`, `amenity_checklist`)**, same class of fix as every prior phase: every direct
  `PropertyCard(...)` construction site across `test_property_card_match_reasons.py`,
  `test_property_card_comparison_notes.py`, `test_property_recommendation_guard.py` (shared `_card()`
  helpers), and `test_property_card_and_pitch_formatter.py` (10 inline sites) needed both new fields
  added.
- 33 new tests: 4 in `test_conversation_state.py` (resolver methods), 6 in new
  `test_property_card_amenity_checklist.py` (every branch), 7 in `test_property_retrieval_filter_builder.py`
  (`apply_premium_boost` x3, `apply_amenity_boost` x4), 2 in `test_property_retrieval_sql_search.py`
  (wider-pool amenity soft-match, and a baseline confirming the pool stays at 3 when no amenities are
  requested), 10 in new `test_recommend_properties_refinement.py` (wrapper-level: location/purpose
  backfill, amenity accumulation, amenity soft-match end-to-end, cheaper/larger/premium resolution,
  explicit-value-wins-over-relative-flag, lock-backstop treats relative flags as new criteria), 2 in
  `test_system_prompt.py` (new clause presence in both modes), 1 in `test_properties_api.py`
  (`is_premium` PATCH round-trip + defaults false), 1 rewritten in `test_tool_handlers.py` (see above).
  Full `pytest`: 657 passed (624 baseline + 33 new, one rewritten in place), same 5 pre-existing
  failures, zero regressions.

**2026-08-05 — Recommendation engine v2 ("Phase 4"): only the "why not that one?" / tradeoff-reasoning
gap was implemented -- everything else in the original 7-item request (candidate ranking, diversity,
explanation quality, ranking confidence) was already fully built and shipped under
`agent-conversation-improvement.md`'s own Phase 2 (2026-08-01, 42 tests, confirmed live in the current
code via direct grep before writing anything) -- re-touching any of it would have duplicated working,
tested business logic and violated "do not redesign."** New, deterministic, additive:
- **`comparison_notes(cards)` in `card.py`** -- for each PropertyCard in a `recommend_properties`
  result, one clause naming its clearest real difference from the CHEAPEST other card in the same set
  ("₹1,000 more than Palm Retreat a night" / "sleeps 4 more than Palm Retreat"). Same discipline
  `match_reasons_for_card` already established: grounded only in real already-known fields, never an
  LLM-guessed or fabricated comparison -- this is what lets the model answer a guest's own "why not the
  other one?"/"what's the difference?" follow-up with a real fact instead of inventing or misstating
  one, the same class of failure `property_recommendation_guard.py`'s existing price/capacity fidelity
  checks already guard against for single-card facts. Thresholds are percentage-based for price (15%)
  and flat for guest count (2 more) -- a trivial gap (₹200, 1 guest) isn't worth voicing. Only ONE
  clause per card (price checked before capacity), cheapest-as-baseline (not an every-pair matrix) for
  determinism. No-ops (returns `{}`) on fewer than 2 cards or a non-positive cheapest price (defensive;
  `filter_builder.py` already excludes zero-price properties upstream in the real call path).
- **`PropertyCard.comparison_note: str`** (new required field, same pattern as `match_reasons` --
  defaults to `""` at `build_property_card`, backfilled via `dataclasses.replace` where sibling-card
  context is actually available).
- **`pitch_formatter.format_property_pitch_line`**: `comparison_note` joins the SAME clause
  `match_reasons` already renders into (never a second sentence) -- the existing voice-friendly
  discipline applies just as much to a comparison as to a match reason.
- **`context_builder.build_recommendation_result`**: wires `comparison_notes` in at the exact point
  `match_reasons`/`confidence_for_result` are already computed -- **deliberately skipped when
  `combo_note` is set**, since that path's cards are smaller units meant to be booked TOGETHER
  (`ranking.diversify_leading_candidates` already excludes this same path for the identical reason) --
  a price/capacity comparison there would misleadingly frame two complementary units as competing
  alternatives.
- **New `GOLDEN_RULES` clause** (`system_prompt.py`, shared by both modes): when a guest directly asks
  to compare already-recommended options, answer using the real difference `recommend_properties`
  already returned, never invent one or just pick a favorite. Confirmed via grep there was zero
  existing coverage for this before the change.
- **Untouched, confirmed via `git status`/`git diff --stat`**: `orchestrator.py`, `filter_builder.py`,
  `sql_search.py`, `semantic_search.py`, `ranking.py`, `calendar_service.py` (the retrieval core),
  `ConversationState`, `StatePromptSyncProcessor` (this session added zero lines to either -- the
  `conversation_state.py` diff in this working tree is entirely from a prior session), all 7 guards.
- **Real pre-existing-test breakage caught and fixed, not left broken**: `PropertyCard` is a frozen
  dataclass with every field required (same discipline `match_reasons` already established, precisely
  to avoid a mutable-default hazard) -- adding `comparison_note` as a new required field meant every
  test that constructs a `PropertyCard` directly (not via `build_property_card`) needed the new field
  added at its own construction site. Found via running the affected test files immediately after
  the dataclass change, not discovered later: `test_property_card_match_reasons.py`,
  `test_property_recommendation_guard.py` (one shared `_card()`/`_card()` helper each, one-line fix),
  `test_property_card_and_pitch_formatter.py` (8 separate inline construction sites, fixed
  individually).
- Token cost: `GOLDEN_RULES` grew 33,453 -> 34,211 chars (+758 chars, **+190 tokens, +2.3%**) -- smaller
  than Phase 2's addition (+351) or Phase 3's (+228), a modest single-clause addition. The
  `comparison_notes` mechanism itself adds no static system-prompt cost at all -- it only appears
  inside a `recommend_properties` tool-result message, and only when ≥2 candidates come back with a
  real, meaningful difference between them.
- 15 new tests: 10 in new `tests/test_property_card_comparison_notes.py` (one per branch/edge case --
  empty list, single card, cheapest-gets-no-note, meaningful price gap, small price gap falling through
  to capacity, meaningful capacity gap, no-meaningful-difference-at-all, one-clause-price-before-capacity,
  non-positive cheapest price, three-cards-all-compared-against-the-single-cheapest), 4 in
  `test_property_card_and_pitch_formatter.py` (pitch-line rendering alone/joined-with-match-reasons,
  end-to-end wiring through the real `build_recommendation_result` chain, the combo-note exclusion
  specifically), 1 in `test_system_prompt.py` (new clause present in both prompt modes -- assertion
  strings checked against the actual raw wrapped source before writing them this time, avoiding the
  exact line-wrap-mismatch mistake caught during Phase 2/3's own self-reviews). Full `pytest`: 619
  passed (604 baseline + 15 new), same 5 pre-existing failures, zero regressions.

**2026-08-05 (same day, self-review correction) — two real correctness bugs in `comparison_notes`,
both caught by a principal-engineer-style self-review before either reached a real call:**
- **The capacity-comparison branch only ever checked for a POSITIVE guest-count gap, silently
  producing no note at all whenever the pricier option was also the SMALLER one** -- a common real
  listing shape (a large cheap family villa vs. a small pricier boutique unit). Confirmed live in the
  review: `Family Villa (₹5,000, sleeps 8)` vs. `Boutique Suite (₹5,100, sleeps 2)` -- only a 2% price
  gap (below the meaningful-price threshold, so it fell through to the capacity check), and the
  capacity check produced NOTHING, despite "pricier AND sleeps 6 fewer" being arguably the single
  clearest tradeoff this whole feature exists to surface. Every test written for this function varied
  `max_guests` only in the direction where the pricier card had equal-or-more capacity -- none tested
  the inverse, so this shipped past its own test suite undetected, the same class of process gap
  flagged in a prior session's review. **Fixed**: capacity gap is now compared by `abs()` against the
  threshold, with the spoken direction ("more"/"fewer") flipped to match reality.
- **`comparison_notes` read `card.base_price` directly with no awareness that `exact_airbnb_pricing`
  properties' stored `base_price` can be stale or a placeholder** (their real price comes from a live
  SearchApi fetch at `get_pricing` time instead -- confirmed by re-reading `filter_builder.py`
  directly, which already lets these properties through regardless of `base_price` for exactly this
  reason). This meant a stale/placeholder number could end up spoken as a real price comparison
  ("₹3,000 more a night") -- the exact class of failure `handle_get_pricing`/`handle_negotiate_rate`'s
  own base_price=0 guard already exists to prevent (`docs/agents.md`'s documented 2026-07-23 incident:
  a `base_price=0` property quoted as "free of charge"), reopened in this new code path with no
  equivalent guard. **Fixed, deliberately without adding a new `PropertyCard` field** (which would have
  meant touching every direct `PropertyCard(...)` test-construction site again, a bigger footprint than
  necessary): `comparison_notes` now takes an optional `unreliable_price_ids: frozenset[uuid.UUID]`
  parameter -- a flagged card is never used as the price baseline and never has a price claim built
  about it (only a capacity comparison, which is trustworthy regardless of price), while `context_builder
  .build_recommendation_result` computes this set from the real `Property.exact_airbnb_pricing` values
  it still has in scope before the `Property` -> `PropertyCard` conversion, so no new field or
  duplicated name-fallback logic was needed to thread the signal through.
- Also fixed a wrong field-name reference in a `pitch_formatter.py` comment (`card.comparison_notes` ->
  the actual field is `card.comparison_note`, singular; `comparison_notes` is the plural function name
  in `card.py`).
- 6 new tests: 5 in `test_property_card_comparison_notes.py` (pricier-but-smaller gets a "fewer" note;
  an unreliable-priced card is excluded from ever being the price baseline; a flagged card never gets a
  price claim built about itself; a flagged card can still get a capacity note; all-cards-unreliable
  fails open to no notes at all), 1 end-to-end test in `test_property_card_and_pitch_formatter.py`
  confirming `build_recommendation_result` actually threads `Property.exact_airbnb_pricing` through to
  `comparison_notes`, not just the pure function in isolation. Full `pytest`: 625 passed (619 + 6 new),
  same 5 pre-existing failures, zero regressions. Both fixes are fully backward-compatible for every
  existing caller (`unreliable_price_ids` defaults to an empty `frozenset`) -- all 10 pre-existing
  `comparison_notes` tests still pass unmodified.

**2026-08-05 — Intelligent slot collection ("Phase 3"): extended two existing prompt mechanisms, no new
state/mechanism.** Scope was narrowed during planning after confirming slot *extraction* is already
entirely an LLM responsibility by design (every tool-arg schema in `schemas/tool.py` types slots as
final structured values -- `num_guests: int`, `check_in: date`, never free text -- so there's no
code-level NLU layer to build without duplicating what the LLM already does per turn); multi-slot
extraction and re-asking-only-missing-info already work today via `update_lead`'s multi-field schema
and `ConversationState`'s existing slot/goal tracking; location phrasing ("near Baga", "walking
distance from the beach") is already handled downstream by `filter_builder.py`'s fuzzy locality/
landmark matching, contingent only on the LLM passing the phrase through -- none of that needed
touching. "Slot confidence" was scoped out entirely per explicit user instruction, consistent with
this plan's own existing non-goal ("No LLM-self-reported confidence score... would itself be an
ungrounded, hallucination-shaped output," `agent-conversation-improvement.md`'s Non-goals section) --
dates/guest-counts have no meaningful confidence gradient once the LLM has extracted them into an
`int`/`date`, so there was no real, groundable signal left to add.
- **Extended the existing NEVER-RE-ASK "extract indirectly" clause** (`app/prompts/system_prompt.py`,
  `GOLDEN_RULES`, shared by both prompt modes) -- previously covered only 3 narrow shapes ("we are 10
  friends" -> num_guests=10, "next weekend" -> a date, "our budget is tight" -> a signal without a
  number). Added: composite/implicit guest counts ("my wife and I" -> 2, "2 adults and a kid" -> 3,
  with the actual arithmetic spelled out -- count everyone mentioned, +1 for "I"/"me"); explicit
  confirmation that vague-sounding location phrasing ("near Baga", "walking distance from the beach")
  is already a real, usable answer, not something needing a follow-up question; budget-as-ceiling
  phrasing ("under 8k") already gives an exact number usable directly, no need to ask the guest to
  restate it as a fixed amount.
- **Extended `_today_anchor()`** (same file) with one more pre-computed pattern: "first weekend of
  October"-style phrasing names a month rather than being relative to today, so it isn't covered by
  the existing this/next-weekend pre-computation. Since which month a guest will actually name isn't
  known at prompt-build time, the fix hands the model a worked METHOD (first Saturday of the named
  month, roll to next year if that month already passed) plus one concrete example computed from the
  real current date -- same "hand it a fact, don't make it calculate" reasoning the this/next-weekend
  logic already uses, extended to the one date shape it didn't cover.
- **Measured token cost** (Standing Rule 1's own bar, chars/4 approximation): `GOLDEN_RULES` alone grew
  8,142 -> 8,370 tokens (+228, +2.8%); the full assembled Guest Support prompt (includes
  `_today_anchor()`) grew 8,823 -> 9,466 tokens (+643, +7.3%); the full Lead Agent prompt grew
  10,000 -> 10,642 tokens (+642, +6.4%). Larger than Phase 2's ~4.5% addition, driven mainly by the
  worked date-example paragraph -- reported plainly rather than rounded down, since Standing Rule 1
  explicitly requires measuring, not assuming acceptable.
- **Caught and fixed during test-writing, not left in**: 3 of the first 5 new tests failed on first run
  -- not logic bugs, but the exact same class of mistake as a prior session's own note (Phase 2's
  "line-wrap mismatch" lesson): assertion strings written as if the source were one continuous string,
  when the raw triple-quoted source wraps mid-phrase across lines (including once mid-quote, "my wife
  and\n  I", which also reads worse as prose) or has case-sensitivity mismatches against a `.lower()`
  comparison. All fixed by either adjusting the assertion to a same-line substring or, for the
  mid-quote wrap, rewording the source itself so the phrase reads cleanly on one line either way.
- **Untouched, confirmed via `git status`**: `ConversationState`, `StatePromptSyncProcessor`,
  `app/services/property/retrieval/**`, all 7 guard files, `tools.py` -- nothing in this phase needed
  a new mechanism, only more complete instructions inside two functions that already existed.
- 5 new tests in `test_system_prompt.py`, matching the existing `test_golden_rules_covers_*` naming
  convention plus the existing `_FixedDatetime` fixed-clock pattern for the two date-anchor tests (one
  same-year case, one December-into-January year-rollover case -- actually exercising both branches of
  the new year-rollover logic, not just the common case). Full `pytest`: 604 passed (599 baseline + 5
  new), same 5 pre-existing failures, zero regressions.

**2026-08-05 (same day, self-review correction) — two real bugs in the `_today_anchor()` worked example
and the location-phrasing clause above, both caught by a principal-engineer-style self-review before
they shipped further, not by a live call:**
- **The month-anchor worked example never actually demonstrated the rollover rule it was teaching, for
  11 of 12 months of the year.** The first version picked the NEXT calendar month as its example (e.g.
  "September" when today is in August) -- but the next calendar month is, by construction, almost never
  a month that has "already passed this year" (the one exception being December, where the next month
  rolls into January of next year). So the prose correctly stated the rollover rule ("if that month has
  already passed this year, it means next year"), but the one concrete example handed to the model only
  ever showed the easy, same-year case -- for any call NOT placed in December, the model got zero worked
  example of the actual harder arithmetic it was being told to do. Fixed: the example now picks the month
  BEFORE today's month, resolved to NEXT year -- the real case the rule exists for (a booking date can't
  resolve into the past, so an earlier-in-calendar month named by a guest always means next year). This
  correctly demonstrates the rollover branch in 11 of 12 months; January is the one exception (no earlier
  month within the same year to roll over), which now explicitly falls back to December of the same year
  -- a real, still-useful example, just one that doesn't happen to need the rollover branch, documented
  as such rather than left as an unexplained special case. Both fixed-clock tests rewritten to match:
  `test_today_anchor_named_month_example_demonstrates_the_rollover_it_teaches` (June clock -> May 2027)
  and `test_today_anchor_january_has_no_earlier_month_this_year_to_roll_over` (new, January clock ->
  December same year, replacing the old December-clock test which is no longer the interesting case
  under the corrected logic).
- **"Walking distance from the beach" was asserted as a working preferred_location/near_landmark example
  without actually being verified against the real matching code -- it doesn't reliably route anywhere.**
  Traced through `filter_builder.py` directly: `preferred_location` matches via `ilike` against
  `Property.city`/`neighborhood_info` (a place name, not a feature-distance description); `near_landmark`
  matches via `matches_landmark()`'s `difflib.SequenceMatcher` fuzzy comparison against a landmark's own
  `name` field, whose own docstring example is a specific named venue ("Thalassa") -- a generic phrase
  like "walking distance from the beach" would score near-zero against any real landmark name, so the
  only path it could ever match through is a coincidental verbatim substring inside a property's free-text
  `neighborhood_info`. Since retrieval is off-limits to touch this phase, the honest fix was narrowing the
  clause to only the example that's actually verified to work end-to-end -- "near Baga" (a real locality,
  genuinely matched by `preferred_location`'s existing `ilike` logic) -- and dropping both "walking
  distance from the beach" and "somewhere quiet away from town" (neither is a place name either), rather
  than asserting two match paths that don't exist. `test_golden_rules_treats_location_phrasing_as_a_usable_answer`
  renamed to `test_golden_rules_treats_a_named_locality_as_a_usable_answer` and rewritten to assert the
  dropped phrase is genuinely absent, not just that the kept one is present.
- Token cost after both fixes: `GOLDEN_RULES` 8,370 -> 8,363 tokens (-7, the dropped location examples
  roughly offset other wording), Guest Support prompt 9,466 -> 9,458 (-8), Lead Agent 10,642 -> 10,636
  (-6) -- net cost vs. the original pre-Phase-3 baseline essentially unchanged from before this
  correction. Full `pytest`: 604 passed (same count -- 2 tests rewritten in place, 1 renamed, no net
  new/removed), same 5 pre-existing failures, zero regressions.

**2026-08-05 — Conversation robustness pass ("Phase 2"): topic-switch/answer-first-then-return-to-flow
state + two GOLDEN_RULES wording clauses. Narrowed from an 8-item request after auditing what already
existed** (interruptions/barge-in: already confirmed working, no task needed per
`agent-conversation-improvement.md` Phase 6.4; user corrections: already fully covered by Phase 6.2's
existing clause + `ConversationState.set_slot`'s overwrite mechanics; most of "repairs": covered by
pre-existing filler/incomplete-sentence rules) — only genuinely uncovered ground was implemented:
- **`interrupted_goal` field + `mark_detour()`** (`app/voice/conversation_state.py`): a guest asking
  something tangential (routed through `search_faq`, the one tool that never itself advances
  `conversation_goal`) while a real in-progress flow (`checking_availability`/`negotiating`/
  `collecting_lead_contact`) is happening now records what was interrupted, so the model can be told to
  return to it — previously `conversation_goal` was a single current-value field with no history, so a
  detour silently overwrote what was in progress with nothing left to resume it. Same tool-call-is-the-
  signal discipline as every other field (no new LLM classification): `mark_detour()` only records
  anything when the *current* goal is one of the three resumable ones (a tangential question while still
  just browsing isn't interrupting anything committed yet); cleared automatically the moment any real
  progress resumes (`_recompute_goal`, the same place every other goal transition already happens),
  since every tool that would resume the flow already calls `set_slot`/`lock_property` first.
- **`search_faq` wrapper** (`app/voice/tools.py`): now calls `state.mark_detour()` after its result —
  the only place this signal can originate, since `search_faq` is the tangential-question tool.
- **`StatePromptSyncProcessor`** (`app/voice/state_prompt_sync.py`): surfaces `interrupted_goal` (when
  set) as one added hint line, reusing `_GOAL_HINTS`' own phrasing so it reads consistently with the
  existing "Current objective" line rather than introducing a second vocabulary. True no-op (zero added
  tokens) when unset, same guarantee every other line in this block already has.
- **Two new `GOLDEN_RULES` clauses** (`app/prompts/system_prompt.py`, shared by both prompt modes):
  (1) answer-first-then-return-to-flow — confirmed via grep there was zero existing coverage for a guest
  switching topics mid-flow within scope (existing "topic" rules were only about declining out-of-scope
  topics, or bridging between *planned* steps like recommendation→pricing, neither of which covers a
  genuine detour); (2) conversational-memory-callback wording — how to naturally reference something
  already established (`state.slots`/`quoted_price`), distinct from the existing NEVER-RE-ASK rule
  (which is about not re-asking a question, not about voicing a callback). Measured cost:
  `GOLDEN_RULES` grew from 31,378 to 32,781 chars (**+1,403 chars, ~+351 tokens at chars/4, ~+4.5%**) —
  comparable to a single prior Phase 6 addition (Phase 6.1 was ~167 tokens, Phase 6.2 ~150), not a
  runaway addition, and Groq-prefix-cached like the rest of the static prompt.
- **Deliberately not touched**: no guard file — none of the three new pieces of work are a factually-
  verifiable claim a guard could check against real tool output (the pattern every existing guard uses);
  these are stylistic/pragmatic behaviors, the same class of thing Standing Rule 3 already says stays
  prompt-only where no code path can enforce it structurally. `app/services/property/retrieval/**`
  untouched — confirmed via `git status` showing zero changes there.
- 10 new tests: 4 in `test_conversation_state.py` (`mark_detour`'s own logic — records/no-ops/clears/
  freezes-on-escalation-or-closing), 2 in `test_voice_tools.py` (the real `search_faq` wrapper actually
  calls `mark_detour()`), 2 in `test_state_prompt_sync.py` (the hint line appears/stays absent), 2 in
  `test_system_prompt.py` (both new clauses present in both prompt modes). Full `pytest`: 607 passed
  (597 baseline + 10 new), same 5 pre-existing failures, zero regressions.

**2026-08-05 (same day, self-review correction) — `interrupted_goal`/`mark_detour()` removed: the
trigger signal was wrong, not just imprecise.** A principal-engineer-style self-review of the entry
above found the `search_faq`-as-detour-signal design was built on an incorrect premise: `GOLDEN_RULES`
itself mandates `search_faq` as the *required* first stop for any property/support question
(`system_prompt.py:182`, Lead Agent workflow step 8) — on-topic or not — not just for tangents. A guest
mid-negotiation asking "does it have a pool?" is directly relevant to the negotiation, but routes
through the identical `search_faq` call as a genuine off-topic detour, and both were being flagged as
"you were interrupted, return to X" identically. This meant the mechanism misfired on what is likely
the *majority* of real `search_faq` calls (on-topic questions during an active flow), not an edge case.
Confirmed no reliable replacement signal exists either — `search_faq`'s own arguments don't distinguish
"tangential" from "relevant to the property already under discussion," since both usually resolve to
the same locked `property_id`. Also confirmed all 6 tests added for this feature only covered the two
cases the design *intended* to handle (resumable-goal-detour, browsing-goal-no-op) — zero coverage of
the actual broken case, so the bug shipped past its own test suite undetected.
- **Reverted, not patched**: `interrupted_goal`/`mark_detour()`/`_RESUMABLE_GOALS` fully removed from
  `app/voice/conversation_state.py`; the `state.mark_detour()` call removed from `search_faq`'s wrapper
  in `app/voice/tools.py`; the `interrupted_hint` line removed from `app/voice/state_prompt_sync.py`.
  Considered narrowing the trigger (e.g. only count it as a detour if `search_faq` resolves to a
  *different* property than the one locked) but rejected it — the actual failure case (an on-topic
  question about the SAME property mid-negotiation) would still misfire under that narrower rule, so it
  wouldn't have fixed the real bug, only a rarer one. Per Standing Rule 3's own carve-out ("where a
  prompt change alone is genuinely sufficient... called out explicitly with the reason"), kept only the
  `GOLDEN_RULES` answer-first-then-return-to-flow clause, which needs no state backing and doesn't
  misfire — it's advisory prose, same class as every other GOLDEN_RULES clause with no code-level
  guard. Also removed one dangling sentence from that clause ("If your instructions below say you were
  in the middle of something...") that referenced the now-removed state hint.
- 8 now-dead tests removed (4 in `test_conversation_state.py`, 2 in `test_voice_tools.py`, 2 in
  `test_state_prompt_sync.py`, plus the now-unused `ConversationState` import in `test_voice_tools.py`).
  The 2 tests for the surviving prompt-only clauses (`test_system_prompt.py`) were unaffected — they
  assert on prompt text only, no state dependency. Full `pytest`: 599 passed (597 original baseline + 2
  surviving new tests), same 5 pre-existing failures, zero regressions. Repo-wide grep confirms zero
  remaining references to `interrupted_goal`/`mark_detour`/`_RESUMABLE_GOALS` anywhere.
- Real lesson for next time this pattern comes up: before wiring a new `ConversationState` field to a
  tool-call signal, explicitly check whether that tool is *exclusively* used for the triggering scenario
  or also for the normal/legitimate case — `search_faq` looked like a clean "this must be a tangent"
  signal by elimination (the one tool that doesn't itself advance `conversation_goal`), but "doesn't
  advance the goal" and "only fires for detours" turned out to be different properties.

**2026-08-05 — Architecture-debt cleanup pass (docs/how-it-works.md's Refactoring Plan, "Phase 1"),
docs-only + small mechanical code changes, no feature work, no pipeline behavior change**:
- **Groq prompt-cache section reordering** (`app/prompts/system_prompt.py`): both `build_system_prompt`
  and `build_lead_system_prompt` previously appended per-call-unique sections (caller phone, guest
  memory, active booking) BEFORE the static property/portfolio block, meaning no two calls to the same
  property/host ever shared a cache-able prefix on Groq's prefix-based prompt cache. Reordered so the
  static content comes first and per-call-unique sections are appended last — pure statement reordering,
  zero content change. This was already scoped (not implemented) as of the 2026-07-22 entry below;
  implemented now. `test_system_prompt.py` (74 tests) confirmed to have no order-dependent assertions
  before the change, all still passing after.
- **Guard intervention logging** (6 files: `repetition_guard.py`, `meta_commentary_guard.py`,
  `property_recommendation_guard.py`, `escalation_phrase_guard.py`, `premature_end_call_guard.py`,
  `response_shape_guard.py`): of the 7 pipeline guards, only `redundant_context_guard.py` logged when it
  intervened — the other 6 acted silently, making "did guard X fire at all" invisible without manually
  reading Railway logs for a specific call's timestamp window. Added one `logger.warning(...)` call at
  each guard's actual intervention point(s), matching `redundant_context_guard.py`'s existing convention
  exactly (guard name + what it did, via loguru; metadata only, never the raw text a guard dropped or
  overrode — corrected during self-review, `meta_commentary_guard.py` initially logged the dropped span
  verbatim, inconsistent with the other 5). No guard's pass-through/non-intervening behavior changed —
  confirmed via all 85 guard tests still passing. **Partial, not full, closure of the observability gap**:
  none of the new log calls carry `call_session_id` (the guard classes aren't constructed with one), so
  "how often did guard X fire this week / on call Y specifically" still requires manually correlating log
  timestamps to a call's known window — only "did it fire at all" is now answerable via a text search.
  See `docs/how-it-works.md` Debt #6 for the full caveat.
- **Lifecycle vocabulary cross-reference** (`app/voice/conversation_state.py`, `app/models/lead.py`):
  `ConversationGoal` (in-call, discarded at hangup), `Lead.lead_temperature` (LLM-set via `update_lead`,
  no validation), and `Lead.status` (host-set, dashboard-only) each track a different partial view of
  "how far along is this booking," with nothing reconciling them. Added a documentation-only mapping
  comment near `ConversationGoal` explaining the correspondence, plus a one-line cross-reference on
  `Lead.lead_temperature` — no schema change, no migration, no behavior change. A genuine unified field
  would need a real migration/backfill decision; explicitly out of scope for this pass.
- **Phase 7 verification status re-confirmed, not advanced**: attempted to watch the local backend log
  during a real test call specifically to make progress on `agent-conversation-improvement.md`'s Phase
  7.1/7.4 (both blocked on live call data) — no call activity reached the monitored local process at all
  (only the routine health-check heartbeat), most likely because the call went to Railway rather than
  local (no ngrok tunnel running). Documented in `agent-conversation-improvement.md`'s Phase 7 section as
  a re-confirmed-still-blocked data point, not fabricated as progress.
- **`docs/how-it-works.md` Technical Debt table updated**: items #2 (Phase 4a — actually already closed,
  the doc previously said "build status not confirmed"), #3 (lifecycle enum), #7 (prompt cache ordering)
  updated to reflect what's now actually resolved vs. still genuinely open.
- Full `pytest` suite: 597 passed before this pass, confirmed still 597 passed / same 5 pre-existing
  failures after (`test_call_includes_duration_and_lead_name_phone`,
  `test_escalate_to_host_also_saves_lead_so_it_isnt_left_empty`,
  `test_search_faq_logs_gap_when_no_verified_answer`, `test_is_complete_short_but_punctuated`,
  `test_ice_servers_stun_only_by_default`) — zero regressions.
- **Not done in this pass, deliberately**: a DB-backed guard-event table (logging-only was chosen as the
  smaller, lower-risk version — see `docs/how-it-works.md`'s Refactoring Plan for the tradeoff); a real
  unified booking-lifecycle enum/migration (cross-reference comment only, per above); Phase 7.1/7.4/7.5
  themselves (still require real call data, not achievable from this environment).

Last 12 commits (on `main`) as of 2026-07-27:
- `60e9fc4` reccoment_property() fixes
- `07c6396` changes in escalation phrase, max completion tokens added, remove property id from recommend_properties(), added a repetition guard
- `3bff6c6` changes lead display hierarchy
- `10be02e` silent watchdog cancel end request fix
- `1782b8f` Merge pull request #32 from shagunverma-04/main
- `f0e99e0` remove stale pricing logic, escalation phrase fixed, silentwatchdog fixed
- `4a0c3e1` Fix alembic multiple-heads deploy blocker
- `3ce8f76` Merge branch 'main' into shagun
- `2265b76` changed clerk auth ui, optional fields, created profile page
- `172e84d` Merge pull request #31 from shagunverma-04/abhaya
- `fa90a10` cleaning build
- `98c2e56` Merge pull request #30 from shagunverma-04/abhaya

**2026-07-27 — Clerk-only auth migration (context for anything below needing a browser session)**: `POST /api/v1/auth/login` (the old demo-login JWT mint) is gone entirely on both local and production backends — 404 on both. The dashboard now requires a real Clerk sign-in (Google or email); there is no test-credential/OTP path available in a sandboxed browser. This broke the session's prior "mint a JWT, drive the dashboard directly" verification method for anything requiring a real session — worked around via direct DB queries (`AsyncSessionLocal`/`select()` against the real Neon Postgres DB) and `npx tsc --noEmit` instead, for the frontend changes below.

**2026-07-27 — Live Requests sort was ranking a closed, stale lead above the guest's actual latest call**: `frontend/src/app/dashboard/leads/page.tsx` sorted both the Kanban board (`leadsForColumn`) and the table view (`contentLeads`) by `created_at` — when a `Lead` row was first inserted, not when it was last actually touched. A lead is created once but can be updated again much later by a real subsequent call about the same guest; sorting by `created_at` let a lead that happened to be *created* slightly later rank above one *created* earlier but *updated* far more recently. Confirmed live and reproduced directly against the real DB: "Deepika / Mocha" (`created_at` 17:17, status `closed`) ranked above "Deepika / Blush-Romantic" (`created_at` 16:17, but `updated_at` 20:53 from a real subsequent call, status `open`) — the guest's actual latest activity was buried under an older, closed request. Fixed: both sort call sites now sort by `updated_at` descending.

**2026-07-27 — Pricing dashboard cleanup**: removed the entire "Pricing rules" card (`frontend/src/app/dashboard/pricing/page.tsx`) — its rule-creation form never sent `condition` (so `min_nights` was always `None`, meaning even `length_of_stay`, the only rule type the backend pricing engine actually reads, silently did nothing when created via this UI), and `weekend_surge`/`loyalty`/`last_minute` rule types were never read by the backend at all. Also removed stale `weekend_nights`/`cleaning_fee`/`tax_amount` fields from the Quote calculator's result display and the `PriceBreakdown` TS type — leftover from the earlier synthetic-markup-removal work (`pricing_engine.PriceBreakdown` has never included these fields since then). Kept: Smart pricing card, Negotiation policy card, a slimmed Quote calculator (Nights/Base total/Discount/Total only). Backend pricing logic itself is unchanged — this was purely removing dead/misleading UI, not a behavior change; see `docs/research-flow.md`'s pricing section, already accurate.

**2026-07-27 — four live-call bugs, traced through actual Railway logs and the real production DB (not guessed), three now backed by code-level guards since prompt-only fixes for this class of bug have repeatedly failed to stick**:
- **`recommend_properties` result string corrupted real property names** ("Pause Project" — the shared brand suffix, not a unit name — got read back as if it were the property, e.g. "Here are some options: Pause Project at 3,500 rupees per night..."). Root cause found by reproducing against the real DB: property names are full imported Airbnb titles that routinely contain a literal `|` themselves (e.g. `"Azure 1bhk | 5 mins walk to beach | Pause Project"`), and `handle_recommend_properties` (`tool_handlers.py`) joined multiple properties' lines with `" | "` — splitting that combined string back apart tore individual names into fragments at every `|` inside them, not just between properties. Fixed at the source: switched to a numbered, newline-separated format (`\n` can never appear in these single-line DB fields, so it can't collide) — `property_recommendation_guard.py`'s parser updated to match.
- **A guest who explicitly asked for South Goa got recommended a North Goa (Siolim) property** ("Olive"). Root cause: Olive's `neighborhood_info` mentions `"Dabolim (South Goa airport) and Madgao railway station are both 75 mins away"` — a travel-time reference, not a location claim — and the location filter matched the literal phrase "South Goa" against that free text as if it were evidence the property itself is in South Goa. Fixed: a recognized Goa-region query (north/south) now matches only against the actual expanded locality list (`_GOA_NORTH_LOCALITIES`/`_GOA_SOUTH_LOCALITIES`), never the raw region phrase against `neighborhood_info` free text.
- **`(Waiting for guest response)` — a narrator/stage-direction parenthetical — got spoken/shown as part of a reply**, right after a real question. Same underlying failure GOLDEN_RULES already bans (narrator/meta text describing the call instead of just having it), a new phrasing shape the existing prompt rule didn't literally cover. New `app/voice/meta_commentary_guard.py` (`MetaCommentaryGuardProcessor`): streams text through with zero added latency by default, only holds text back while inside an open `(...)`, and drops the span if it matches known meta-commentary language (waiting/listening/pause/thinking/etc.) — legitimate parentheticals pass through untouched.
- **"Let me loop in the host"/"let me open the host" kept recurring despite the existing guard** — traced to the guard's own design: it only rewrote a reply if it matched a specific regex (`loop...host`), and "let me open the host" (the original live complaint from earlier this project) never matched that pattern at all — each fix only ever covered the exact wording already seen, an inherently incomplete approach. Rewrote `escalation_phrase_guard.py`: it no longer tries to detect bad phrasings at all — it **unconditionally replaces** the entire first reply after `escalate_to_host` fires with a fixed safe line ("Okay, I've noted your details -- the host will follow up with you on WhatsApp shortly to confirm."), regardless of what the model said. There is no longer a detection step that can have a coverage gap.

**2026-07-27 — a 3072-completion-token degenerate reply, and its actual root cause**: a real call produced a single LLM turn that paraphrased the same clarifying question dozens of times back to back (`completion tokens: 3072` in Railway logs, vs. tens of tokens for a normal reply), all spoken to the guest. Initially misdiagnosed as an OpenRouter reasoning-leak (same "gpt-oss-120b" model is configured as `OPENROUTER_MODEL`, the last-resort fallback) — **this was wrong and corrected after actually checking the Railway logs for that exact call**: the request went straight to `api.groq.com` via `_FallbackGroqLLMService` (never touched `openrouter.ai`), and `reasoning_format="hidden"` was working correctly on that same completion (a small `reasoning tokens: 60` was separately reported, not embedded in content) — this was the model's actual answer content degenerating into repetition, a distinct failure mode from a reasoning leak. Two real fixes landed:
  - **`max_completion_tokens=400` added to the Groq path** (`pipeline.py` — previously uncapped entirely; only the OpenRouter fallback had a cap before this). Bounds how much a single degenerate completion can ever generate.
  - **New `app/voice/repetition_guard.py`** (`RepetitionGuardProcessor`) — the actual guarantee against repetition itself, since a token cap alone doesn't prevent repeats *within* the budget. Streams text through immediately by default (zero added latency on normal turns), and cuts a response short, silently, the moment it detects either a near-duplicate sentence (≥60% word overlap with something already said this turn) or a flood of degenerate short fragments (the live ".. .. .." case) — the reply just ends early instead of repeating.
  - The OpenRouter `extra_body={"reasoning": {"exclude": True}}` fix (mirroring Groq's `reasoning_format="hidden"`) was still added as real defense-in-depth for the genuine 429-fallback path, just doesn't explain this particular transcript.
- Full pipeline order as of this session (`pipeline.py`): `transport.input() → stt → silence_watchdog → language_sync → user_aggregator → redundant_context_guard → llm → repetition_guard → meta_commentary_guard → property_recommendation_guard → escalation_guard → premature_end_call_guard → tts → transport.output() → assistant_aggregator`. See `docs/agents.md` for the full per-stage writeup.
- 39+ new/updated tests across `test_property_recommendation_guard.py`, `test_repetition_guard.py`, `test_meta_commentary_guard.py`, `test_escalation_phrase_guard.py`, `test_pipeline_llm.py`, `test_tool_handlers.py` — all passing; only the pre-existing unrelated failures below remain.
- **Not yet done, needs explicit go-ahead (production/external action)**: the Twilio escalation "Go to Dashboard" WhatsApp button (`TWILIO_ESCALATION_TEMPLATE_SID`) still points at a stale URL from before the Render→Vercel frontend migration — its button URL is baked in at Content Template *creation* time (`scripts/create_escalation_template.py`), not resolved per-message, and nobody re-ran the script after the move. Fix is re-running that script against the current `FRONTEND_BASE_URL` (already correctly `https://mira-prod-two.vercel.app` on Railway) and updating `TWILIO_ESCALATION_TEMPLATE_SID` — deliberately not done automatically since it's a live Twilio/production config change.

**2026-07-15**: DB was 6 Alembic migrations behind (stuck well before `f3a8c1d7e4b6`), causing demo login to 500. Fixed via `alembic upgrade head` in `backend/`. Current head: `f3a8c1d7e4b6` (add seasonal_notes to properties) — see `docs/database.md` for the full migration history.

**2026-07-15 (later same day)**:
- Fixed escalation emails landing in spam: `email_client.py` now sends multipart text+HTML with proper `From` display name, `Date`, and `Message-ID` headers (missing headers were the immediate cause; real SPF/DKIM/DMARC on the sending domain is still the durable fix — see the header comment in `email_client.py`). New `email_templates.py` renders an HTML email in Mira's palette with "Open Dashboard" + "Message Guest on WhatsApp" (wa.me link) CTAs.
- Built the `send_photos` voice tool end-to-end: guest asks to see photos → LLM calls `send_photos` → queues a WhatsApp-stand-in notification with a link to a new no-auth gallery page (`frontend/src/app/p/[propertyId]/photos`, backed by `GET /api/v1/properties/{id}/gallery`). Reuses the existing Cloudinary `Property.photos` field rather than a host-maintained Drive folder — one link either way, but this can't drift out of sync with what's actually on file.
- Built `founder-console/` — a fully separate Next.js app (own `package.json`, port 4000, passcode-gated via `FOUNDER_PASSCODE`) showing live LLM model health (`GET /api/v1/health/llm`) plus a static external-API cost reference table. Real per-call cost metering isn't wired up anywhere in the backend yet — the cost table is a planning reference, not live spend.

**2026-07-16/17**: Fixed several live-call bugs found via real test calls:
- Double-greeting guard on `on_client_connected` in `pipeline.py` (browser test double-connects are still possible; the underlying two-offer cause wasn't fully root-caused).
- `TURN_DETECTION_STRATEGY` switched from `hybrid_experimental` back to `vad_fixed` locally — the experimental strategy was producing garbled/truncated turns, cut-off names, and spurious escalations.
- `system_prompt.py`: "this weekend"/"next weekend" were wrongly treated as the same dates — now distinct; added a rule to speak dates naturally ("18th of July") instead of raw ISO; added a rule not to re-ask for a name/phone already given earlier in the call; broadened the "no simulated dialogue" rule to catch any turn-label pattern (`"User says"`, `"User:"`, etc.), not just the one exact phrasing it knew about before.
- `faq_service.py`/`tool_handlers.py`: `search_faq` only ever queried the `FaqEntry` table, never `Property.neighborhood_info` — a Lead Agent call had zero visibility into neighborhood info (only Guest Support's system prompt injects it directly), so genuinely on-file local-area answers were escalating instead of resolving. Added `neighborhood_info` as a third fallback tier.
- Twilio WhatsApp Sandbox wired up end-to-end (`twilio_client.py`, `TWILIO_*` config): `send_whatsapp`/`send_photos` now send real WhatsApp, and `escalate_to_host` now also WhatsApps the host (`User.phone`) using a `twilio/call-to-action` Content Template (`scripts/create_escalation_template.py`) for a real "Go to Dashboard" button instead of a raw link + ugly link-preview card.
- **Exotel WSS URL fix**: Exotel's Voicebot Applet strips query strings from the configured WSS URL before connecting (confirmed live via ngrok inspector — real Exotel connections arrived with no query string at all, while the identical URL worked fine tested directly). `EXOTEL_WEBHOOK_TOKEN` moved from a `?token=` query param to a path segment: `/api/v1/voice/exotel/ws/{token}`. **Render's `EXOTEL_VIRTUAL_NUMBER`/Voicebot Applet config in production still needs updating to the new path-based URL** — this was fixed and tested via local + ngrok, not yet re-verified against the deployed Render backend.

**2026-07-17 (pilot customer — Pause Projects, hello@pauseprojects.in)**: Real test calls surfaced two systemic bugs, both fixed account-wide, not per-property:
- **`search_faq` coverage gap**: replaced the narrow per-field keyword-matching fallbacks (`search_neighborhood_info`/`search_amenities`, added earlier same day) with one comprehensive `faq_service.full_property_context()` — when nothing matches in `FaqEntry`/legacy `Property.faq`, the model gets the property's *entire* on-file detail block (house rules, amenities, neighborhood info, check-in/out times, max guests, active seasonal notes) to read the answer out of, instead of hoping a keyword overlap catches the guest's exact phrasing. Keyword matching had already missed two real, on-file answers (a neighborhood question, then a pool question) by the time this was replaced. Golden rule updated accordingly: escalate only when the answer truly isn't in what search_faq returned, not just because the result doesn't look like a clean single answer. Known tradeoff: `UnansweredQuestion` gap-logging (feeds the FAQ Learning Engine dashboard) no longer fires whenever a property is known, since this tier always returns *something* now — pre-existing tradeoff from the earlier neighborhood_info/amenities fallbacks, just now broader in scope. Not revisited today.
- **Stale/static pricing on hosts using Airbnb Smart Pricing**: `Property.base_price` is a static number that can't stay accurate for a host whose real Airbnb rate changes daily. Root-caused via two real mismatches (Terra ₹4,498 stored vs ₹4,099 actual; Whyt ₹5,400 vs ₹5,599 actual). Fix: new `Property.exact_airbnb_pricing` bool (migration `8818413a6d0a`) plus `searchapi_client.fetch_listing_total_price()` — when set, `pricing_engine.calculate_price` fetches this exact listing's live price for the exact requested dates from SearchApi.io (matched by `Property.airbnb_listing_id`, captured at Bright Data import time) instead of using `base_price` math, with `base_price` surviving only as a same-call fallback if the live fetch fails. `negotiate_rate` inherits this automatically (calls `calculate_price` internally). Verified live across multiple properties/date ranges (different date ranges on the same property correctly returned different live prices). Enabled for all 15 Pause Projects properties. `exact_airbnb_pricing` also skips MIRA's own weekend-surge/cleaning-fee/tax stacking (added earlier same day for Terra) — for a host whose Airbnb price is already final, no additional markup on top. Exposed as a per-property toggle in the property edit dialog ("Quote exact Airbnb price"). **Real limitation found afterward**: the live fetch only works if the listing actually appears in the city's search results for that date range — confirmed live that 2 of 4 test properties weren't found (even 3 pages deep), silently falling back to the static `base_price` with no signal to anyone. See Open design questions below.
- Switched Exotel routing (`User.lead_exophone = '01141189038'`) from the demo account to Pause Projects — pure DB write (`call_service.get_user_by_lead_number` looks up the dialed number against this column at call time), no Exotel-side or code change needed. Demo account's `lead_exophone` is now unset.
- Refreshed all of `docs/` + this file for session-continuity — see each doc's own content for what changed; nothing left stale as of this pass.

**2026-07-18/19 — deployment topology shift**: Backend moved from Render to Railway as primary (Render kept live as fallback, not actively deployed — do not remove); frontend moved from Render to Vercel as primary. See "Deployment — current topology" in `docs/architecture.md` for the full picture, including two production-only gotchas found during the move:
- **Vercel + schemeless `NEXT_PUBLIC_API_BASE_URL`** — this env var is baked in at build time (not runtime), so a value missing `https://` silently produces broken relative-path API calls in the deployed build with no build-time error. Confirmed and fixed.
- **Railway DNS-resolution quirk** — the custom Railway domain intermittently fails to resolve depending on network/resolver; the underlying `*.up.railway.app` domain is unaffected. Documented as a known caveat, not resolved (Railway-side infra behavior, not app code).
- Local `backend/.env`'s `FRONTEND_BASE_URL` was still pointing at the stale Render frontend URL (used to build links in escalation emails/WhatsApp messages) — corrected to the Vercel URL.

**2026-07-19/20 — pricing engine + guest/property matching fixes** (see `docs/research-flow.md` for full mechanics):
- Documented (pre-existing, not new this pass) the 7-day nightly-rate Redis cache-first logic in `pricing_engine`, plus the daily pre-warm job — the docs previously and incorrectly said pricing was "never cached." **Currently inert in production**: confirmed via `railway variables --json` that `REDIS_URL` is not set on Railway, so every cache read/write silently no-ops (by design — `redis_client.py` fails open) and every pricing call pays full latency. Provisioning Redis on Railway (or pointing at an external instance) would activate this with no code changes.
- Fixed North/South Goa property-matching: `recommend_properties` previously required an exact locality string match; added `_GOA_NORTH_LOCALITIES`/`_GOA_SOUTH_LOCALITIES` lookup lists so "Anjuna"/"Baga" etc. correctly resolve to North Goa properties and "Colva"/"Palolem" etc. to South Goa, without the guest or host needing to use the literal region name.
- Fixed inconsistent group-of-6 (or any over-capacity request) handling: `handle_recommend_properties` now separates the location/budget filter (`base_stmt`) from the guest-count filter (`stmt`); when the count filter returns zero results but the base filter doesn't, the tool now returns a `combo_note` instructing the model to suggest combining two smaller units instead of just saying "no properties available."
- Added phone-number validation: `_phone_confirmation_warning` appends a warning to the tool result whenever a captured phone number isn't exactly 10 digits, wired into `update_lead`, `send_whatsapp`, `send_photos` — catches misheard/truncated numbers before they're used to actually contact someone.
- Cleaned up a duplicate demo/test account in Azure (unrelated one-off).

**2026-07-20/21 — three live-call prompt bugs, found via real call transcripts and fixed in `system_prompt.py` GOLDEN_RULES** (all confirmed live before the fix, see `docs/agents.md` for the exact callouts):
- MIRA invented `check_calendar` tool-call arguments not present in the conversation (fabricated specific check-in/check-out dates the guest never said). New rule: never invent tool-call arguments for `check_calendar`/`get_pricing`/`negotiate_rate` — only use dates/values the guest actually provided.
- A literal narrator/meta-text string (`"---This is the end.---"`) leaked into spoken output on a real call. Extended the existing "no turn labels" rule into a broader "no narrator/meta text" rule.
- A guest saying "hello" mid-call (e.g. after a brief silence) caused MIRA to repeat a full previous answer (e.g. an attractions list) verbatim instead of just re-engaging. Extended the "don't repeat greeting on hello" rule to cover any repeated content, and sharpened the re-ask rule to explicitly cover the guest's immediately-preceding message.
- Added `test_golden_rules_forbid_inventing_tool_call_arguments`, `test_golden_rules_forbid_narrator_meta_text`, `test_golden_rules_hello_mid_call_never_repeats_last_answer` to `test_system_prompt.py` (49 tests total, all passing).

**2026-07-21 — frontend bugs, WhatsApp/email root-causing, STT/TTS latency attempt (reverted), and a discussion session**:
- Fixed `RightPanel` footer/Save button scrolling off-screen on long forms (10-photo properties) — `Drawer.Popup` now scrolls only its middle content div (`min-h-0 flex-1 overflow-y-auto`), not the whole popup, keeping the footer pinned.
- Fixed the "Airbnb Smart Pricing" toggle not responding to direct clicks on the switch pill itself (only the label text worked) — root-caused to `base-ui/react`'s `SwitchRoot` calling `preventDefault()` and manually dispatching its own click to a hidden native `<input>`; added a defensive `onClick` on the row `<div>` that checks `event.defaultPrevented`/whether the click landed on the label before toggling, so both the label and the switch itself work regardless of the exact click target.
- Root-caused (not fixed — both require external account/infra setup, not code) "photos not sent via WhatsApp or email":
  - **WhatsApp**: confirmed via direct Twilio Messages API query that the same number gets `status: "delivered"` shortly after opting in (texting "join `<code>`") but `status: "undelivered"` (error 63016) once Twilio's 24-hour customer-service session window lapses — this is on top of, not instead of, the existing sandbox opt-in requirement. Documented as **⚠️ Currently broken in production** in `docs/agents.md`.
  - **Email**: confirmed via Railway logs (`aiosmtplib.errors.SMTPConnectTimeoutError`) that Railway's Trial tier blocks outbound SMTP port 587 entirely — escalation emails cannot send from Railway regardless of `SMTP_*` config correctness. Documented as **⚠️ Currently broken in production** in `docs/agents.md`.
- Attempted a call-connect-to-greeting latency fix: connecting Sarvam STT and TTS concurrently (`asyncio.gather`) instead of sequentially, based on real Exotel call-log timestamps showing them connecting one after another. Deployed (`335c468`), immediately broke every real call (`Exception: SarvamTTSService#0: TaskManager is not initialized` — pipecat only assigns `task_manager` once a service is attached to a running `Pipeline`, which happens after both services are constructed, so `create_task()` inside the concurrent `_connect()` had nothing to attach to). Reverted same-session (`676f661`), verified the revert was actually live on Railway via `deploymentId` matching before confirming to the user. Root cause and full timeline documented in `docs/agents.md` under "Call-connect-to-greeting latency," with an explicit recommendation against re-attempting without local/staging call testing first. A DB-query-parallelization alternative was discussed (~100-300ms realistic gain, needs a separate `AsyncSession`, real concurrency-safety risk) but not implemented — **user explicitly deprioritized this whole avenue for now** ("nobody picks up on the first ring anyway, we can look for a solution later").
- Discussion-only session (no code changes) covering: SearchApi credit usage/caching/correctness, Redis setup status (confirmed unset in prod, see above), greeting delay breakdown, prompt leakage on long calls, WhatsApp Business integration workflow, onboarding auto-fill flow, a future support-ticketing system, RBAC architecture, read-only iCal sync, and whether upgrading Railway's paid tier would help latency (assessment: tier upgrade addresses CPU/memory/uptime, not the sequential-connect + cold-start latency sources actually diagnosed — no action taken).
- Refreshed all of `docs/` + this file for session-continuity.

**2026-07-21 (later same day) — Redis provisioned and verified live in production**: chose Upstash's free tier over Railway's own Redis plugin (Railway has no persistent free tier for add-ons — a Redis plugin would draw on paid usage/trial credit; Upstash's free tier comfortably covers this app's caching volume at zero cost). `REDIS_URL` (a `rediss://` TLS connection string) set in both `backend/.env` (local) and Railway's production variables — Railway auto-redeployed on the variable change. Verified genuinely active end-to-end, not just configured:
- Local: `cache_get_json`/`cache_set_json` round-tripped a test value against the real Upstash instance.
- Production: temporarily enabled `exact_airbnb_pricing` on a demo property with a real `airbnb_listing_id`, called `POST /pricing/quote` twice with identical dates — 1st call 5.1s (cache miss → live SearchApi fetch, `searchapi:listing_price:...` key confirmed written to Upstash via a direct scan), 2nd call 1.6s (cache hit, same price, no SearchApi round trip). Reverted the property's flag back to `false` afterward — no production data left changed.
- `pytest tests/test_redis_client.py` — 8/8 passing.
- Mechanics confirmed by reading the code (not just docs): the daily pre-warm job (`smart_pricing_service.refresh_live_pricing_cache`, `LIVE_PRICING_CACHE_WINDOW_DAYS = 7`) caches a **per-night** rate (each night queried individually as its own 1-night stay, so real day-of-week pricing is captured) for a rolling 7-day-ahead window, refreshed daily. `pricing_engine._sum_cached_nightly_rates` sums whichever specific nights a guest asks about (2-night, 3-night, any combination) as long as every night in the range is inside that window — answers instantly and accurately for those. The moment any single requested night falls outside the 7-day window, the cache path is abandoned entirely (not partially used) and it falls through to a live per-range SearchApi fetch (`fetch_listing_total_price`, same ~5s latency as before Redis existed), which itself falls back to the static `base_price` on any failure. Known theoretical gap, not yet observed as a real mismatch: since each cached night is priced as a standalone 1-night stay, a length-of-stay discount Airbnb only applies when multiple nights are searched together wouldn't be reflected in the summed cached total.

**SearchApi credit accounting** (worked out from the code, see `docs/research-flow.md` for the full breakdown): a Redis cache hit costs **0 credits** (pure Redis read, SearchApi never called). A live fetch (cache miss) costs **1 credit** once a property's Airbnb coordinates are already resolved — they're cached permanently in the DB after the first-ever successful lookup, so only the very first live fetch for a brand-new property costs **2 credits** (1 for the one-time coordinate lookup + 1 for the price lookup) — this is what "every fetch was taking two credits" was measuring before Redis existed. The daily pre-warm job costs **~7 credits per `exact_airbnb_pricing` property per day** (1 per cached night) — this was already being spent on its unconditional cron even while Redis was unprovisioned, just discarded every time (`cache_set_json` no-op). Now that Redis is live, that same daily spend is actually used: total daily SearchApi spend is roughly unchanged, but live guest pricing questions for dates inside the 7-day window now cost 0 credits instead of 1-2 each, so spend no longer scales with call volume for near-term dates.

**2026-07-22 — corrected stale "free-tier" Groq references + prompt-caching finding (discussion, mostly)**:
- The account has been on a **paid Groq plan since 2026-07-07**, but comments/docs across `config.py`, `main.py`, `pipeline.py`, `system_prompt.py`, and `CLAUDE.md` still described the fallback chain / health-check machinery as working around a "free-tier TPM cap." Corrected all Groq-specific references (commit on 2026-07-22, `6d1b4a6` on the pre-font-merge history) — comment/doc text only, no behavior change. SearchApi.io and OpenRouter "free-tier" references left as-is (those are unchanged).
- **Groq supports automatic server-side prompt caching on `gpt-oss-120b`** (verified against Groq docs): prefix-based, 50% discount on cached input tokens, cached tokens don't count toward rate limits, 2h TTL, tool schemas are part of the cacheable prefix, and `usage.prompt_tokens_details.cached_tokens` is exposed (pipecat already logs it as "cache read input tokens"). **Finding, not yet fixed**: in `system_prompt.py`, `caller_phone_section`/`guest_memory_section`/`active_booking_section` (all per-call-unique) currently sit *before* the static property/portfolio content, so that byte-identical property block can never get a cross-call cache hit — only `GOLDEN_RULES` + `today_anchor` do. The fix is pure section reordering (move the per-call-unique sections to the end); scoped and explained to the user, **not implemented** — see Open design questions.

**2026-07-22/23 — telephony-vendor cost/fit research (discussion, no code)**: user is exploring cheaper Exotel alternatives (Exotel feels expensive at early-stage volume). Key findings, grounded in reading pipecat's own serializers: pipecat ships **pre-built serializers for both Exotel and Plivo** (`.venv/.../pipecat/serializers/{exotel,plivo}.py`) — so Plivo is a near drop-in (new WS route + Plivo XML "answer URL" endpoint + a client module; the agent/prompt/LLM stack is untouched). TeleCMI/PIOPIY is technically viable (has a WebSocket AI-streaming API) but has **no pre-built serializer and no public protocol docs** — higher risk. CallHippo/MyOperator are call-center SaaS with their own bots, not raw-streaming telephony — not real alternatives. LiveKit provides no PSTN itself (needs a SIP trunk like Plivo underneath) and would mean rewriting the whole pipeline off pipecat onto LiveKit Agents — different, much bigger decision. Cost at the user's stated volume (300 min/mo, 17 properties, 1 host): **Plivo ≈ ₹430/mo** (₹250 number rental + 300×₹0.60 inbound) vs. Exotel's tiered prepaid packages (≥₹9,999/mo) — Plivo is dramatically cheaper at this volume unless the user is actually on Exotel PAYG. Concurrency: Plivo India default 50 concurrent calls (plenty at this scale); the real concurrency unknown is MIRA's own single-replica Railway backend + Sarvam limits, not the telco. No migration started — research only.

**2026-07-23 — a run of real test-call bug fixes (all from live Exotel/browser transcripts + Railway/local logs)**:
- **Silence watchdog too aggressive**: `SilenceWatchdogProcessor` default was 5.0s. Confirmed via its own log line that a guest composing a Hindi dates/availability sentence hadn't finished by 5s (real answer landed 4s *after* the nudge fired, mid-thought). Raised to **9.0s** in `pipeline.py` (`SilenceWatchdogProcessor(timeout_seconds=9.0)`).
- **VAD interrupting the guest too readily**: pipecat's `VADParams.start_secs` default (0.2s) fires an interruption after only a fifth of a second of sound — closer to a mic bump than speech. Raised to **0.35s** in `_VAD_PARAMS`. Flagged in-code as needing a real call to confirm 0.35s is right, not just plausible.
- **Filler-only turns re-asking the question**: a guest saying "Hmm" while thinking got the same question re-asked in different words 4s later. Added a GOLDEN_RULE: a filler/thinking-sound turn ("Hmm", "um", "haan") with no real answer is not a cue to restate the question — stay quiet or say "Take your time."
- **Tool results going unspoken**: `recommend_properties` returned real results and the model's next line skipped straight to "Those sound good — do any of them stand out?" without ever naming a property (happened twice in one call; logs confirmed no interruption/dropped-frame — a genuine prompt-compliance gap). Added a GOLDEN_RULE requiring tool results to actually be spoken before any reaction to them.
- **"Let me loop in the host" regression — now has a code-level backstop**: the exact banned phrase recurred yet again despite GOLDEN_RULES citing it as its own counterexample. Prompting alone has now failed on this repeatedly, so added `app/voice/escalation_phrase_guard.py` — `EscalationPhraseGuardProcessor` sits between `llm` and `tts`, inert for every normal turn, and only buffers+scans the first text-bearing response at/after `escalate_to_host` fires (armed by the tool itself, same pattern as `silence_watchdog`'s `end_call` wiring). On a banned-phrase match it swaps the whole utterance for the rule's prescribed safe line before TTS. 4 new tests in `test_escalation_phrase_guard.py`.
- **₹0 quoted as a real price**: a property with `base_price=0` (and `exact_airbnb_pricing=False`, so no live fetch even attempted) was quoted to a guest as "zero rupees for the night." Logs confirmed the agent called `get_pricing` correctly and faithfully read back what the engine returned — the bug was the engine returning a 0 as if it were a quote. `handle_get_pricing`/`handle_negotiate_rate` now refuse any non-positive total and return a directive to not say a number, never claim free/zero, and `escalate_to_host`. 2 new tests. **Data follow-up still needed** — see Known issues.
- Local dev backend was restarted with stdout piped to a file so live local test calls can be traced directly (browser-test page is real mic-in WebRTC — the sandbox can't inject audio, so real calls from the user are needed to generate LLM turns to inspect).

**2026-07-23 — external provider account issues surfaced during testing (all confirmed by direct API/log probes, none fixable in code — require account/billing action)**:
- **Sarvam TTS out of credits** — live calls connect (ICE + STT succeed) but TTS fails immediately with `TTS Error: No credits available` and the pipeline gives up. Guest hears silence. Needs a Sarvam top-up.
- **SearchApi out of monthly credits** — confirmed via a direct search call returning `429 "You have used all of the searches for the month."` This is why `exact_airbnb_pricing` properties silently fall back to (often stale) `base_price`. Needs a plan upgrade on SearchApi.io.
- **Exotel API auth failing** — a direct read-only Exotel API call with the configured `EXOTEL_API_KEY`/`EXOTEL_API_TOKEN` returns `401 Authentication failed`. Consistent with inbound calls not ringing / producing zero backend logs (the call never reaches the webhook). Needs checking the Exotel dashboard for account/billing status or regenerating the API key/token, then updating the Railway vars.

**2026-07-24 — broken Alembic tracking found + fixed, and minimum-stay enforcement built**:
- **`alembic_version` was pointing at an orphaned revision (`7a7297081aaa`)** that doesn't exist in any committed migration file — every ORM query touching `Property` (and thus the whole Properties tab) was failing with `UndefinedColumnError`. Root cause of the immediate breakage: a `minimum_nights` model field was added without the migration being generatable (autogenerate itself failed, unable to resolve the DB's broken current-revision pointer), so the model and the live schema disagreed. Confirmed via direct schema introspection that the actual DB *was* current with the full migration chain (through `d8a1f47c2b6e`, "add lead_id to call_sessions") — the version pointer was just stale/orphaned, not a real schema gap. Fixed by directly `UPDATE`-ing the `alembic_version` table to `d8a1f47c2b6e` (pure metadata, no DDL), then adding `minimum_nights` via a real migration (`5161e38a221b`) and stamping to it (column had already been added manually via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` during the emergency fix, so no re-run needed). **If `alembic current`/`alembic upgrade` ever fails with "can't locate revision" again**: don't guess-fix — introspect `information_schema.columns` against what the full `alembic history` chain expects, confirm they actually match, and only then correct the `alembic_version` row directly.
- Built minimum-stay enforcement: `Property.minimum_nights` (default 1, migration `5161e38a221b`), checked in `handle_check_calendar` — a request for fewer nights than the property's minimum now gets a clear "minimum stay of N nights" response instead of a false "available." Exposed in `PropertyCreate`/`PropertyUpdate`/`PropertyOut` and the property edit dialog. Verified against Olive (Pause Projects), which genuinely has a 2-night Airbnb minimum — set correctly, not a test artifact.
- Investigated a reported Olive iCal date mismatch (July 1/11/16/17 shown blocked locally but open on Airbnb) — found no actual discrepancy at check time (local `bookings` table matched the live iCal feed exactly, 0 stale rows, all 4 dates correctly available). Most likely a timing issue against the 15-minute auto-sync cadence, not a bug; confirmed the sync path is genuinely read-only by construction (`ical_client.fetch_ical` only ever does an HTTP GET; `sync_property_ical` only writes to our own `bookings` table, no write path to the source calendar exists anywhere in the code).

## Known issues / in-flight work

- **⚠️ External provider accounts need topping up / fixing (blocking real calls right now, none code-fixable)**:
  - **Sarvam TTS out of credits** — calls connect but produce no voice. Top up Sarvam.
  - **SearchApi out of monthly credits** (`429`) — `exact_airbnb_pricing` silently falls back to `base_price`. Upgrade the SearchApi plan.
  - **Exotel API returns `401 Authentication failed`** — inbound phone calls don't ring / leave no backend logs. Check the Exotel dashboard (account/billing) or regenerate `EXOTEL_API_KEY`/`EXOTEL_API_TOKEN` and update the Railway vars.
- **⚠️ Data: 8 properties have `base_price = 0`, all with `exact_airbnb_pricing = False`** — so they never even attempt a live price and (before the 2026-07-23 guard) would quote ₹0 to guests. The code guard now refuses to quote a ₹0, but the underlying data is still wrong. Two are on the real pilot account `hello@pauseprojects.in` ("2 BHK/3 BED Luxury Private Pool Villa with view", "Cabana 1bhk"); the rest are on demo/test accounts. Real nightly rates need setting in the dashboard.
- **VAD `start_secs=0.35` and silence-watchdog `timeout=9.0s` need live confirmation** — both changed 2026-07-23 based on real call evidence, but the exact numbers were chosen as "plausible, not yet validated." Worth a real test call to confirm interruptions feel better without the bot now feeling sluggish, and that 9s doesn't feel like too long a silence before the nudge.
- **Redis is live in production (Upstash)** — verified end-to-end 2026-07-21 (see entry above). Not yet observed: whether the daily `_scheduled_smart_pricing_refresh`/`_scheduled_live_pricing_cache_refresh` jobs pre-warm cleanly against real Redis — worth checking Railway logs after a scheduled run.
- **Escalation emails cannot send from Railway** — Trial-tier outbound SMTP port 587 is blocked (confirmed via `aiosmtplib.errors.SMTPConnectTimeoutError` in Railway logs). In-app notification still fires; only the email leg fails. See `docs/agents.md` email section.
- **WhatsApp escalations/photos only deliver within Twilio's 24h sandbox session window** — beyond sandbox opt-in (texting "join `<code>`", a one-time step), Twilio's customer-service session expires after 24h of inactivity (error 63016, confirmed via direct Messages API query); falls back to the in-app notification only. Needs either re-opt-in prompting or moving off the sandbox to a real WhatsApp Business number.
- **Call-connect-to-greeting latency (~4.5-6s)** — root-caused to sequential Sarvam STT→TTS connection plus Neon cold-start; a concurrent-connect attempt broke production and was reverted (`335c468`/`676f661`). Explicitly deprioritized by the user for now ("nobody picks up on the first ring anyway"); a safer DB-query-parallelization alternative was scoped but not built. See `docs/agents.md` "Call-connect-to-greeting latency."
- **Railway custom domain intermittent DNS-resolution failures** — the underlying `*.up.railway.app` domain is unaffected; workaround is using that domain directly if the custom domain fails to resolve. Infra-side, not app code.
- `TURN_DETECTION_STRATEGY=hybrid_experimental` (`app/voice/turn_strategies.HybridCompletenessUserTurnStopStrategy`) is experimental, local-only, and **not yet verified end-to-end on a real live call**. Production defaults to `vad_fixed` and is unaffected regardless of this branch's local `.env` setting. Intentionally omitted from Railway's env config.
- `[DEBUGTURN]` debug logging still present in `turn_strategies.py` — safe to strip once the hybrid strategy is confirmed working live.
- Pre-existing, unrelated test failures (not touched this session): `test_calls_api.py::test_call_includes_duration_and_lead_name_phone`, `test_tool_handlers.py::test_escalate_to_host_also_saves_lead_so_it_isnt_left_empty` (phone normalization), `test_tool_handlers.py::test_search_faq_logs_gap_when_no_verified_answer`, `test_turn_strategies.py::test_is_complete_short_but_punctuated`.
- **Twilio escalation "Go to Dashboard" button still points at a stale (pre-Vercel) URL** — `TWILIO_ESCALATION_TEMPLATE_SID` was created back when `FRONTEND_BASE_URL` pointed at the old Render frontend; its button URL is baked in at Content Template creation time, not resolved per-message, so the env var already being correct doesn't fix it. Needs `scripts/create_escalation_template.py` re-run against Twilio + `TWILIO_ESCALATION_TEMPLATE_SID` updated on Railway — not done yet, since it's a live production/external-system change requiring explicit go-ahead (see 2026-07-27 entry above).
- **Clerk-only auth (2026-07-27)**: `/api/v1/auth/login` (demo-login JWT mint) is gone on both local and production — 404 on both. Any future session-level dashboard verification (browser-driven) needs a real Clerk sign-in; there's no sandboxed-browser workaround. Direct DB queries + `tsc --noEmit` were used as substitutes this session, not a general replacement.
- **Property display names are still full imported Airbnb titles, not clean short names** (2026-07-27) — e.g. `"Azure 1bhk | 5 mins walk to beach | Pause Project"`, `"Olive-Wake up by the forest @ Pause Project 1bhk"`. The delimiter-collision bug that mangled these into garbage (see 2026-07-27 entry above) is fixed, but the underlying names themselves are still the full descriptive title including the shared "Pause Project" brand suffix — reads awkwardly when spoken aloud on a call, even though it's now spoken *correctly*. A short/clean display-name field (separate from the full Airbnb title kept for reference) would be a real future improvement, not attempted this session.

## Open design questions

- **Groq prompt-caching section reordering (scoped, not built)** — Groq's automatic prefix-based prompt caching on `gpt-oss-120b` gives 50% off cached input tokens and skips rate limits, but only for the exact-match prefix. In `system_prompt.py`, `caller_phone_section`/`guest_memory_section`/`active_booking_section` (per-call-unique) sit before the static property/portfolio block, so that block never gets a cross-call cache hit. Fix is pure reordering — move those three per-call-unique sections to the end, after all static content. Zero content change, zero risk, would let Groq cache the (byte-identical) property/portfolio content across different calls. Worth pulling real `cache read input tokens` from Railway logs first to size the win before doing it. **Not implemented.**
- **Telephony migration Exotel → Plivo (researched, not started)** — Plivo is a near drop-in (pipecat ships a Plivo serializer; agent stack untouched) and ~₹430/mo vs. Exotel's ≥₹9,999/mo prepaid packages at the current ~300 min/mo volume. Would need a new WS route, a Plivo-XML answer-URL endpoint, a small client module, and a call-status webhook. Before committing: confirm whether the account is actually on an Exotel package vs. PAYG (a PAYG move might close most of the cost gap without a migration), and real-call-test Plivo's audio quality/latency and clean hang-up. See the 2026-07-22/23 research entry above.
- **`exact_airbnb_pricing` live-fetch reliability** — **Resolved and live.** Redis is now provisioned (Upstash) and verified end-to-end in production (see the 2026-07-21 (later) entry above): the 7-day per-night pricing cache + daily pre-warm job actively serve real guest pricing questions, falling through to a live per-range SearchApi fetch (then `base_price`) only for dates outside the 7-day window. The original search-ranking/pagination reliability concern is now moot for anything inside the cached window. Remaining open item, not a reliability concern: whether Airbnb applies a multi-night length-of-stay discount that the per-night-cached sum wouldn't capture — untested, flagged in the entry above.
- **Payment gateway integration for booking confirmation** — unchanged this session. Phase 1 (manual host approval of a guest-submitted payment screenshot) not yet built. Phase 2 (real gateway + webhook) researched: Razorpay ~2% flat domestic, 0 AMC/setup, ₹100 chargeback; Cashfree ~1.75-2.25%, ₹4,999/yr AMC, ₹150 chargeback; PayU ~2-2.5%, ₹200 chargeback. UPI itself is 0% MDR under RBI rules on all three, though gateways still charge a ~2% "platform fee" on UPI-via-bank-account flows — confirm current numbers directly with each provider before committing. No `Booking` price/payment-status columns exist yet (see `docs/database.md`) — needs a schema decision (split vs. full-upfront, proof-of-payment storage) before building.
