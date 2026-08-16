# Building Intelligence — Task List

Context: MIRA today stores full transcripts + structured metadata per call, runs two one-shot
post-call LLM jobs (classification, summary) that are display-only, and has exactly three narrow,
deliberately-scoped feedback paths into future behavior (per-guest `GuestProfile` memory, the
human-gated FAQ-gap pipeline, and `ConversationQuality`'s single `pending_style_correction`
bridge). Nothing aggregates outcomes *across* guests, and nothing feeds historical conversion data
back into pricing/negotiation. This file plans four implementations that close parts of that gap,
each modeled on the one pattern already proven safe in this codebase: **aggregate silently, surface
to a human, require explicit approval before anything reaches a live call.** None of these
implementations make the agent autonomously self-modify.

Full background/audit: see the "Learning/insight systems" analysis in this project's conversation
history (2026-08-15/16). This file tracks execution.

**Format**: every implementation is exactly two tasks — an **Implementation** task and a **PR
Review** task. The review task is a real gate, not a formality: it must be done in a fresh
sub-agent invocation acting as a senior engineer reviewing a PR with no memory of writing the code,
checking correctness against this file's stated intent, not just "does it run." Do not start the
next implementation until the current pair's review task is checked off and any findings are
resolved. Mark `[x]` only when both tasks in a pair are done and clean.

Order matters: implementations 1 and 2 are foundational (they produce the correlated, persisted
data implementations 3 and 4 read). Do not reorder.

---

## Implementation 1 — Guard/quality telemetry persistence

**Problem this closes**: `ConversationQuality` (`backend/app/voice/conversation_quality.py`) is
constructed fresh per call, written to by `StyleComplianceMonitor` and
`ResponseShapeValidatorProcessor`, and then silently discarded when the pipeline object goes out of
scope — never persisted, never correlated with `call_session_id`. There is currently no way to
query "how often did guard X fire this week" or "which calls had the most validator failures."
This is the raw material every other implementation in this file (especially #4) needs, so it goes
first.

**Explicitly not in scope**: no change to `ConversationQuality`'s live behavior, no new read path
back into prompt construction, no aggregation logic yet (that's #3/#4). This implementation is
storage only — it must not touch the "must not silently become a behavioral feedback loop"
invariant in any way. Persisting `ValidationResult`s after the call has ended is not a new bridge;
reading them back into a live call would be.

### Task 1.1 — Implementation

- [x] New model `backend/app/models/call_quality_event.py`: `CallQualityEvent`
  (`UUIDPkMixin`, `TimestampMixin`), table `call_quality_events`:
  - `call_session_id: Mapped[uuid.UUID]` FK → `call_sessions.id`, `ondelete="CASCADE"`, indexed.
  - `rule: Mapped[str]` (mirrors `ValidationResult.rule`).
  - `severity: Mapped[str]` (`INFO`/`WARNING`/`FAIL`).
  - `confidence: Mapped[float]`.
  - `turn_index: Mapped[int]`.
  - `metadata_json: Mapped[dict]` (JSONB, default `{}`) — mirrors `ValidationResult.metadata`; not
    named `metadata` (reserved attribute name on SQLAlchemy declarative models).
  - `processing_time_ms: Mapped[float]`.
- [x] Alembic migration in `backend/alembic/versions/`, modeled on the most recent migration under
  that directory. Confirm current head with `alembic heads` first — do not hardcode a
  `down_revision`. `upgrade()`: create table + index on `(call_session_id)` and a second index on
  `(rule, severity)` for the aggregate queries #3/#4 will run. `downgrade()`: drop both indexes,
  drop table.
- [x] New function `record_quality_events(db, call_session_id, quality: ConversationQuality) -> None`
  in `backend/app/services/call_service.py` (sibling to `set_call_classification`/
  `set_call_summary`, same style): bulk-inserts one `CallQualityEvent` row per entry in
  `quality.validations`. No-op if `quality.validations` is empty. Never raises — wrap the insert in
  a `try/except Exception` that logs and swallows, matching the fail-open discipline every other
  optional/observational write in this pipeline already follows (see `_update_guest_memory`'s own
  `try/except` in `pipeline.py` for the exact pattern to mirror).
- [x] Wire into `backend/app/voice/pipeline.py`'s `on_pipeline_finished` handler
  (`_on_finished`, starts at pipeline.py:1211). Add the call to `record_quality_events` **after**
  `set_call_summary` (pipeline.py:1359) and **before** the `if any(m.get("role") == "user" ...)`
  backfill branch (pipeline.py:1361), inside the same `async with AsyncSessionLocal() as
  finalize_db` block — same session, same inline-await treatment as classification/summary (not
  fire-and-forget like guest memory, since there's no reason to detach it: it's a local DB write
  with no external API call). Pass the existing `conversation_quality` local (constructed at
  pipeline.py:959) through to the handler if it isn't already in scope there — check its actual
  closure reachability at implementation time.

**Verify before moving on:**
1. `alembic heads` → one head. `alembic upgrade head` runs clean against the real dev Postgres (no
   mocking). `psql $DATABASE_URL -c "\d call_quality_events"` shows the expected columns/indexes.
   `alembic downgrade -1` then `alembic upgrade head` again — confirms reversibility.
2. Unit-test `record_quality_events` directly against a real test-DB session (per repo convention):
   a `ConversationQuality` with 2-3 `ValidationResult`s of mixed severity → confirm 2-3 rows land
   with correct field mapping; an empty `ConversationQuality` → confirm zero rows, no error; force
   an exception (e.g. bad `call_session_id` type) → confirm it logs and does not raise.
3. ⚠ Not run: a real end-to-end "Talk to Mira" browser call requires a live mic/WebRTC session that
   can't be driven headlessly. Substituted with direct-DB exercises covering the same code path
   (see Task 1.2 review notes below) — reviewer judged this an adequate substitute for a
   persistence-only change with no new business logic in the hot call path, but this remains a
   real gap to close opportunistically on the next live call.
4. Substituted per #3 — confirmed via direct test (`test_record_quality_events_empty_is_noop`)
   instead of a live clean call.
5. Confirmed — `git diff` on `conversation_quality.py` is empty; only new read site is post-call in
   `pipeline.py`.

**Status: done.** 4 new tests added (`backend/tests/test_call_quality_events.py`), full suite run
before/after (18 pre-existing, unrelated failures on both sides — see Task 1.2 notes — no
regression). DB left at head (`f041738fce4c`).

### Task 1.2 — PR Review

- [x] Run this review in a fresh context (new sub-agent invocation, not carried over from
  implementation) acting as a senior engineer reviewing this as a real PR — no prior memory of
  writing the code, verify claims against the actual diff rather than trusting the task
  description above.
- [x] Confirm: the new table/migration is reversible and matches the model exactly (column types,
  nullability, indexes) — re-run `alembic downgrade -1 && alembic upgrade head` independently.
- [x] Confirm: `record_quality_events` cannot raise into `on_pipeline_finished` under any input
  (empty list, malformed metadata dict, DB session already closed) — read the actual
  `try/except` and confirm the except clause is broad enough and does not silently swallow a
  programming error that should actually be visible in tests (i.e. confirm it fails open on I/O
  problems, not on logic bugs masked by too-broad a catch).
- [x] Confirm this task did **not** touch `conversation_quality.py`'s read side, did not add any
  new consumer of `ConversationQuality` inside the live call path, and did not weaken the
  "validators write, never read each other's history" boundary described in that file's own
  docstring.
- [x] Confirm the insert is scoped to the same `finalize_db` session/transaction as classification
  and summary persistence — no new engine/session pattern invented.
- [x] Confirm no N+1 or unbounded query risk: this is one bulk insert per call, not per-validation
  round trips.
- [x] Re-run this task's own verification steps 1-5 independently rather than trusting they were
  run correctly the first time.
- [x] File findings, if any, and resolve them (fix + re-verify) before marking this pair complete.

**Review verdict: approved with minor follow-ups (both resolved before closing this pair).**
Findings from the independent review, both fixed:
1. `record_quality_events` originally skipped the `session = await db.get(...); if session is None:
   return` existence pre-check its siblings (`set_call_classification`/`set_call_summary`) use,
   relying on the FK constraint to fail instead — harmless (fail-open still held) but inconsistent
   and one avoidable exception-driven rollback per stale-id call. **Fixed**: added the same
   pre-check.
2. No automated test existed for `record_quality_events`, leaving its fail-open/field-mapping
   behavior without regression protection. **Fixed**: added
   `backend/tests/test_call_quality_events.py` (4 tests: mixed-severity persistence, empty no-op,
   `None` id no-op, unknown id fails open) — all pass; full suite re-run before/after confirms the
   18 pre-existing failures (`test_database.py`, `test_email_client.py`,
   `test_embedding_service.py`, `test_ringing_audio.py`, etc. — unrelated environment/asset issues,
   not this task) are identical on both sides, i.e. zero regressions introduced.
On the live-voice-call gap (verification step 3 above): reviewer judged direct-DB verification an
adequate substitute for this specific change (pure persistence addition, no new hot-path business
logic) and did not treat it as blocking.

---

## Implementation 2 — Objection/failure tagging in post-call summary

**Problem this closes**: `call_summary_service.py`'s `summarize_call` already produces a structured
summary (outcome, reason, key_details, missing_information) but has no concept of "what objection
or friction point, if any, caused this call not to convert." Without this field, #4 (pricing/
objection insight surfacing) has nothing to aggregate — `Lead.status` alone tells you *that* a call
didn't convert, never *why*. This is the second piece of foundational raw material.

**Explicitly not in scope**: no new LLM call. This extends the *existing* one-shot summarization
call's prompt/schema — it must not add a second LLM round-trip per call (that would duplicate the
exact anti-pattern CLAUDE.md already flags around hidden regeneration/extra latency, and there's no
reason for it: the transcript is already in front of the model in the existing call).

### Task 2.1 — Implementation

- [x] `backend/app/schemas/call_summary.py`: add `objection_tags: list[str]` (default `[]`) to
  `CallSummary` — a controlled vocabulary, not free text (matching this file's own existing
  "prefer a controlled list over prose substring matching" lesson). Define the allowed tags as a
  literal/enum near the schema, e.g. `PRICE_TOO_HIGH`, `DATES_UNAVAILABLE`, `LOCATION_MISMATCH`,
  `AMENITY_MISSING`, `POLICY_MISMATCH` (min stay / pets / cancellation), `HOST_UNRESPONSIVE`,
  `GUEST_STOPPED_RESPONDING`, `NO_OBJECTION` (explicit "call had no friction" value, not just an
  empty list, so a genuinely-fine call is distinguishable from "the model didn't extract
  anything"). Keep the list short and reviewed against a handful of real transcripts before
  finalizing — do not guess the taxonomy from first principles alone.
- [x] `backend/app/services/call_summary_service.py`: extend `_SUMMARY_PROMPT` (lines 48-89) to
  instruct the model to select zero-or-more tags from the fixed vocabulary based on the transcript,
  output alongside the existing fields in the same JSON response. Extend the strict
  parsing/validation step (same file) to validate each returned tag against the controlled
  vocabulary and drop/ignore anything that doesn't match exactly — never pass through
  free-text/hallucinated tags, matching this service's existing "never raises, degrades cleanly"
  discipline.
- [x] Confirm `CallSession.ai_summary` (JSONB, `call_session.py:59`) needs no migration — it's
  already a schemaless JSONB column storing `CallSummary`'s shape, so the new field lands
  automatically once the Pydantic schema changes. Explicitly verify this assumption at
  implementation time rather than trusting this note — if anything reads `ai_summary` with a
  strict/partial column-projection query instead of loading the whole JSONB blob, that would need
  checking.

**Verify before moving on:**
1. Unit test `summarize_call` directly (no DB, per this service's existing pure-function
   convention) against a handful of hand-written transcripts, one per tag: a price-objection
   transcript → `PRICE_TOO_HIGH` present; a dates-unavailable transcript → `DATES_UNAVAILABLE`; a
   smooth booking transcript with no friction → `NO_OBJECTION` and no other tags; an ambiguous/junk
   transcript → confirm it degrades cleanly (empty list or `NO_OBJECTION`, not an exception).
2. Confirm invalid/hallucinated tag values from a malformed LLM response are filtered out, not
   stored — force a fake response with a made-up tag string and confirm it's dropped.
3. Substituted a direct `summarize_call` invocation against the real configured provider (Groq,
   per `.env`) with 4 hand-written transcripts (price objection, dates-unavailable, smooth booking,
   junk/telemarketing) instead of a live "Talk to Mira" browser call — same reasoning as
   Implementation 1's Task 1.1 step 3 (no new hot-path business logic, and this specifically
   exercises the real LLM call, which a DB-only test can't). Results: price → `['PRICE_TOO_HIGH']`;
   dates → `['DATES_UNAVAILABLE']`; smooth → `['NO_OBJECTION']`; junk → `[]` (model correctly
   declined to force a bad-fit tag, matching the prompt's explicit instruction). Also verified via
   direct dev-DB round-trip (`set_call_summary` → raw JSONB → re-parsed `CallSummary`) that
   `objection_tags` persists correctly end-to-end.
4. Confirmed — `outcome`/`conversation_summary` came back sensible and unregressed across all 4 real
   LLM calls above. Grepped every reader of `ai_summary` (`request_feed_service.py`,
   `guests.py`, the two Pydantic schemas) — all either load the full JSONB blob or read named keys,
   none do partial-column projection that a new key could break. Frontend `CallSummary` type
   (`frontend/src/lib/types.ts`) updated with `objection_tags: string[]`; `call-summary-card.tsx`
   renders named fields individually (no exhaustive key iteration), so it's unaffected;
   `npx tsc --noEmit` clean.
5. Confirmed — `summarize_call` → `_call_llm_with_fallback` (called once) → exactly one provider
   call (`_call_groq`/`_call_anthropic`/`_call_openrouter`, first matching branch returns
   immediately). Only the prompt string and `_parse_summary_response` were edited; the call-count
   structure is untouched.

**Status: done.** 5 new unit tests added (`backend/tests/test_call_summary_service.py`, 11 total)
covering valid-tag passthrough, hallucinated-tag filtering, dedup, missing-field default, and
`NO_OBJECTION`. Full suite run before/after: same 18 pre-existing, unrelated failures both times,
zero regressions.

### Task 2.2 — PR Review

- [x] Fresh-context review, senior-engineer-on-a-real-PR posture, verify against the actual diff.
- [x] Confirm the tag vocabulary is genuinely controlled end-to-end: check the parsing code path,
  not just the prompt instructions — an LLM can and will ignore prompt-level constraints
  sometimes, so the enforcement must be in code (a set-membership filter), not just wording.
- [x] Confirm this is additive to the existing JSON schema/prompt, not a rewrite that risks
  regressing `outcome`/`key_details`/`missing_information` extraction quality — diff the prompt
  before/after and confirm the existing instructions are intact.
- [x] Confirm `objection_tags` survives round-trip through `CallSession.ai_summary` JSONB with no
  serialization issue (e.g. confirm it isn't silently dropped by whatever Pydantic
  `model_dump()`/`dict()` call feeds `set_call_summary`).
- [x] Confirm zero new LLM round-trips — re-check call count/latency claim independently (e.g. via
  logs from the real test call in verification step 3, confirming only one provider request fired
  for that call's summarization).
- [x] Confirm the taxonomy was actually checked against real transcripts (per the implementation
  task's instruction not to guess it from first principles) rather than invented purely
  speculatively — ask for/inspect the transcripts used.
- [x] File findings, if any, and resolve them before marking this pair complete.

**Review verdict: approve with follow-ups, one finding fixed before closing this pair.**
Independent review (fresh sub-agent, own dev-DB round-trip test, own 10-transcript taxonomy stress
test against the real provider) confirmed: controlled-vocabulary enforcement is genuinely in code
(proved by feeding a made-up tag + case-mismatched tag + duplicate through `_parse_summary_response`
directly); prompt diff is purely additive; JSONB round-trip clean (`model_dump()` flows the new
field through automatically, no special-casing needed); zero new LLM round-trips (the diff never
touches `summarize_call`/`_call_llm_with_fallback`); full suite same 18 pre-existing failures, no
regressions.

Findings:
1. **Fixed.** Taxonomy instability on calls with no genuine booking intent (misdial/spam/off-topic):
   reviewer's own 5x-repeated wrong-number transcript flip-flopped between `[]`, `['NO_OBJECTION']`,
   and once an outright wrong `['GUEST_STOPPED_RESPONDING']` — the prompt defined `NO_OBJECTION` for
   "smooth calls" but never said what applies to calls that were never a booking conversation at
   all, so the model guessed inconsistently. **Fix**: `_SUMMARY_PROMPT` now explicitly scopes
   `objection_tags` to calls with genuine stay/booking intent, and instructs an empty list (not
   `NO_OBJECTION`, not a forced tag) for spam/wrong-number/off-topic calls; also tightened
   `GUEST_STOPPED_RESPONDING`'s definition to exclude a normal goodbye. Re-ran the reviewer's exact
   stress transcript 5x post-fix → stable `[]` every time. Re-ran the original 4 category transcripts
   plus a genuine mid-booking dropped-call case → all still correctly tagged
   (`PRICE_TOO_HIGH`/`DATES_UNAVAILABLE`/`NO_OBJECTION`/`GUEST_STOPPED_RESPONDING` respectively) — no
   regression from the wording change. Not covered by an automated test: content-correctness of an
   LLM's real response can't be pinned by a unit test without faking the response (which would only
   test parsing, not the actual bug) — this class of fix is verified live against the provider, same
   as the original taxonomy check in step 3 above, and should be spot-checked again if the prompt is
   ever touched further.
2. **Flagged for Implementation 4, not fixed here (out of scope for this task's diff)**: a tag like
   `PRICE_TOO_HIGH` fires on both a lost negotiation and a successfully-negotiated booking (confirmed
   live: a "guest objected to price, Mira discounted, guest booked anyway" transcript still tagged
   `PRICE_TOO_HIGH`) — correct per the field's literal definition ("what friction occurred"), but
   Implementation 4's planned "conversion rate for calls tagged X" metric will understate a real
   objection's damage if won and lost negotiations both count toward the same tag's denominator.
   Implementation 4's task should decide explicitly how to handle this (e.g. exclude
   `outcome.status` "Booking Confirmed"/"Booking Likely" from the objection denominator, or add a
   separate resolved/unresolved dimension) rather than silently inheriting the ambiguity.
3. Corrected: 5 new tests, not 6 as originally logged above.

---

## Implementation 3 — FAQ-gap-style aggregate: guard/quality weekly digest

**Problem this closes**: implementation 1 makes guard-firing data queryable per call, but nothing
aggregates it across calls into something a host or you would actually look at. This mirrors
`faq_service.py`'s `list_faq_gaps`/`faq_gap_analytics` pattern exactly (group, rank by frequency,
expose via an endpoint) — the only genuinely proven cross-call aggregation pattern already in this
codebase — applied to the new `call_quality_events` table instead of `unanswered_question`.

**Explicitly not in scope**: no automatic action taken on this data. This is a read-only analytics
surface, same as `faq_gap_analytics` is read-only until a host acts on it via the separate
`POST /faq/gaps/{gap_id}/answer` endpoint. No such "resolve" action exists for quality events in
this implementation — that would be a future, separately-justified addition.

### Task 3.1 — Implementation

- [x] New function `quality_event_analytics(db, user_id, bucket: str = "week") -> dict` in
  `backend/app/services/call_service.py` (or a new `call_quality_service.py` if
  `call_service.py` is already large — check its current line count at implementation time and
  decide; prefer the existing file if it stays reasonably sized), modeled directly on
  `faq_service.faq_gap_analytics` (`faq_service.py:384-424`): most-frequent `rule` values by
  count, broken down by `severity`, over a time bucket, scoped to `user_id` via a join through
  `call_sessions.user_id`. Reuse `DateRange`/bucketing helpers from `app/api/v1/common.py` — the
  same ones `faq_gap_analytics` already uses — rather than inventing new date-bucketing logic.
  Note: `faq_gap_analytics` itself doesn't actually use `DateRange` (only a `bucket` string param
  validated by regex, same as this implementation ended up using) — `DateRange` is a separate
  helper used by `list_faq_gaps`/`analytics_summary` for start/end filtering, not by the
  bucketed-analytics functions. Matched `faq_gaps_analytics`'s actual param shape instead.
  No `by_property` breakdown was added (unlike `faq_gap_analytics`'s three breakdowns) — the task
  spec only calls for most-frequent + time trend; a by-property view wasn't asked for and would be
  scope creep without a stated need.
- [x] New endpoint `GET /api/v1/analytics/quality-events` in `backend/app/api/v1/analytics.py`,
  following the exact auth/response pattern already used by the other `/analytics/*` endpoints in
  this file (lines 25-433) — same dependency injection for the current user, same response-model
  conventions. Note: `analytics.py`'s three existing endpoints all inline their SQLAlchemy queries
  directly (its own `analytics_recovery` docstring states "this file has never had one [a service
  layer]") — this endpoint deliberately breaks that local precedent and delegates to
  `call_service.quality_event_analytics` instead, because the task's explicit instruction was to
  mirror `faq_gaps_analytics` (`app/api/v1/faq.py`), which itself delegates to
  `faq_service.faq_gap_analytics` — matching the named model pattern took priority over the
  file's own inline-query habit, and delegating keeps the aggregation independently
  unit-testable the same way `faq_gap_analytics` is.
- [x] Add a Pydantic response schema for this endpoint in `backend/app/schemas/` matching the shape
  `faq.py`'s gap-analytics response schema uses, for consistency.

**Verify before moving on:**
1. Verified directly against the real dev DB: created 4 `CallQualityEvent` rows across 2
   throwaway `CallSession`s, called `quality_event_analytics` directly, and cross-checked its
   `most_frequent` output byte-for-byte against a hand-written `SELECT rule, severity, count(*)
   FROM call_quality_events ce JOIN call_sessions cs ON ce.call_session_id = cs.id WHERE
   cs.user_id = '...' GROUP BY rule, severity ORDER BY count DESC` — outputs matched exactly.
   Cleaned up throwaway rows afterward.
2. Verified tenancy isolation twice: once directly against `quality_event_analytics` with two real
   `User` rows from the dev DB (`user_b`'s result was `[]` despite `user_a` having data), and again
   through the real HTTP layer (`test_quality_events_analytics_scoped_to_authenticated_host` in
   `tests/test_analytics_api.py`, using two real `User` rows + `auth_headers_for`) — both confirm
   no leak.
3. Confirmed via `test_quality_events_analytics_empty_for_host_with_no_events` — `most_frequent`
   and `over_time` both `[]`, HTTP 200, not a 500.
4. Ran the full `analytics`-tagged test selection (`pytest -k analytics`, 16 tests across
   `test_analytics_api.py`/`test_analytics_recovery.py`/`test_faq_gaps.py`/`test_calls_api.py`'s
   analytics test) — all pass unaffected. Full suite also re-run: same 18 pre-existing, unrelated
   failures as Implementations 1 and 2, zero new ones.

**Status: done.** 3 new tests added to `tests/test_analytics_api.py` (empty case, grouping
correctness, tenancy isolation). `EXPLAIN` on the aggregation query confirmed both index scans are
used (`ix_call_sessions_user_id`, `ix_call_quality_events_call_session_id` from Implementation 1) —
no sequential scan.

### Task 3.2 — PR Review

- [x] Fresh-context review, senior-engineer-on-a-real-PR posture.
- [x] Confirm tenancy isolation explicitly — this is the single highest-severity thing to get wrong
  in a new analytics endpoint; re-verify with two real host accounts, not just by reading the code.
- [x] Confirm the aggregation query is reasonably indexed (uses the `(rule, severity)` index added
  in implementation 1, not a full table scan) — check via `EXPLAIN` against the dev DB if the table
  has enough rows to make that meaningful, otherwise confirm the query shape is sane by inspection.
- [x] Confirm this genuinely mirrors `faq_gap_analytics`'s bucketing/response conventions rather
  than inventing a parallel, subtly-different date-bucketing scheme — diff the two functions side
  by side.
- [x] Confirm no write path was accidentally introduced — this task is read-only by design; any
  endpoint here that mutates `call_quality_events` or `call_sessions` is out of scope and a red
  flag for scope creep.
- [x] File findings, if any, and resolve them before marking this pair complete.

**Review verdict: approve, no follow-ups needed.** Independent review (fresh sub-agent) re-derived
tenancy isolation from scratch against the real dev DB (two new throwaway `User` rows, confirmed
`user_b` gets `[]` despite `user_a` having data), re-ran `EXPLAIN` independently and confirmed both
index scans, hand-wrote its own aggregation cross-check with its own 7-row dataset (not reusing the
implementer's data or SQL), confirmed the `DateRange`-vs-`bucket`-param claim by reading `faq.py`
directly, and confirmed the `bucket` string is safely parameter-bound (not raw SQL interpolation)
despite the endpoint-layer regex already restricting it to `week`/`month`. On the one real judgment
call in this task (delegating to `call_service.py` vs. following `analytics.py`'s own inline-query
habit): reviewer independently agreed with the choice, reasoning that matching the *named pattern
being copied* (`faq_gap_analytics`, itself service-layer and independently testable) matters more
than matching the *local file's own habit*, and noted the other three `analytics.py` endpoints are
arguably the ones due for a similar refactor later. Full suite re-run independently: same 18
pre-existing failures by exact name, zero new ones.

---

## Implementation 4 — Objection-correlated pricing insight surface (human-gated)

**Problem this closes**: this is the piece the original audit flagged as the real gap — nothing
currently correlates *why* calls don't convert with pricing/negotiation behavior. This
implementation adds an aggregate, host-facing insight surface ("calls tagged `PRICE_TOO_HIGH`
converted at X% vs. Y% overall this month") built on implementation 2's `objection_tags` and
`Lead.status`. It explicitly does **not** feed anything back into `pricing_engine.py`'s live
math automatically — that would violate the same principle `smart_pricing_service.py` already
established deliberately ("never feeds into pricing_engine's get_pricing/negotiate_rate math
automatically") and would turn a display insight into an unreviewed autonomous pricing change,
which given this handles real negotiation with real guests, is a correctness/trust risk this task
list explicitly declines to take. The output is information a host reads and can act on manually
(e.g. by editing their own `NegotiationRule` policy text, the existing host-authored mechanism) —
not a system that edits `NegotiationRule` rows itself.

**Explicitly not in scope**: any write path from this analytics surface back into
`NegotiationRule`/`PricingRule`/`pricing_engine.py`. If a future task wants to propose that, it
needs its own explicit, separately-reviewed design — do not fold it into this task under the
assumption it's a natural next step.

**Known finding carried over from Implementation 2's review (must be addressed in Task 4.1, not
silently inherited)**: `objection_tags` fires on a call regardless of whether the objection was
resolved — confirmed live: a "guest objected to price, Mira discounted, guest booked anyway"
transcript still gets tagged `PRICE_TOO_HIGH`, same as a call where the guest walked away over
price. If Task 4.1's conversion-rate math treats every `PRICE_TOO_HIGH`-tagged call as one
denominator bucket without accounting for `outcome.status`, a successfully-negotiated price
objection will dilute the metric and understate how much that objection actually costs. Task 4.1
must decide explicitly (and document the decision in its own diff, not just here) whether to: (a)
exclude calls with a converting `outcome.status` (e.g. "Booking Confirmed"/"Booking Likely") from
the objection-tag denominator, or (b) keep them in but add a resolved/unresolved dimension to the
metric. Silently doing neither and presenting a flat "conversion rate by tag" number is the failure
mode to avoid.

### Task 4.1 — Implementation

- [x] New function `objection_conversion_analytics(db, user_id, bucket: str = "month") -> dict` in
  `backend/app/services/lead_service.py` (it's fundamentally a `Lead.status`-vs-`CallSession
  .ai_summary->objection_tags` join, so it belongs with lead analytics, not call-quality
  analytics): for each `objection_tag` value seen in `call_sessions.ai_summary->>'objection_tags'`
  (JSONB query) joined to that call's `Lead.status`, compute conversion rate (`status = 'booked'`
  ÷ total calls carrying that tag) over the time bucket, scoped to `user_id`. Include an overall
  baseline conversion rate in the same response for comparison. Handle the "a call can carry
  multiple tags" case explicitly (a call with both `PRICE_TOO_HIGH` and `DATES_UNAVAILABLE` counts
  toward both tags' denominators) — decide and document this rather than leaving it implicit.
  Deviations from the literal spec, both deliberate:
  - Signature takes `date_range: DateRange | None` (the `app/api/v1/common.py` helper
    `analytics_summary` already uses), not `bucket: str`. This function's output has no
    time-series/trend dimension, so a `bucket` granularity param would be dead weight — a
    start/end window filter is what "over the time bucket" in the spec actually needs.
  - Implemented with raw parameterized SQL (`sqlalchemy.text`, still fully bound params, never
    string-interpolated) for the tag-unnesting query specifically, not SQLAlchemy Core's
    `.table_valued()`. Confirmed live against the real dev DB that asyncpg's prepared-statement
    path cannot resolve `jsonb_array_elements_text`'s result column when cross-joined alongside
    a second explicit `JOIN` (raised `UndefinedColumnError: column anon_1.tag does not exist` at
    `PREPARE` time) — true both for `table_valued`'s default comma-join and for an explicit
    `JOIN ... ON true`/`ON :param`. The one combination that actually executes is literal SQL
    `JOIN jsonb_array_elements_text(...) AS alias(column_name) ON true` with the column name
    declared inside the function-call alias, which `.table_valued()` has no option to emit.
  - Resolved-vs-unresolved handling (the carried-over finding from Implementation 2's review):
    kept every tagged call in the denominator — the literal "conversion rate for calls with this
    friction" question — and additionally reports `resolved_count`/`unresolved_count` per tag as
    a visible breakdown, rather than silently excluding converted calls (a narrower question) or
    silently hiding the distinction. `NO_OBJECTION` excluded from `by_tag` (it belongs in
    baseline only, not as an "objection").
- [x] New endpoint `GET /api/v1/analytics/objection-insights` in `backend/app/api/v1/analytics.py`,
  same conventions as implementation 3's endpoint. Typed response schema
  (`ObjectionConversionAnalyticsOut`/`ObjectionTagStats`/`BaselineStats` in
  `app/schemas/objection_analytics.py`), not `list[dict]` like Implementation 3's — the shape here
  is fixed and small enough that typing it is a clear improvement, not a deviation in spirit.
- [x] Frontend: read-only card on `frontend/src/app/dashboard/pricing/page.tsx`, placed directly
  after the existing "Smart pricing" card — same file already has the exact precedent to mirror
  (informational-only correlation card, "never applied automatically" framing, same
  Card/Table structure). Top 3 tags by volume, each showing conversion rate vs. baseline with a
  percentage-point delta, plus an explicit "(low sample)" caveat for any tag under 5 calls (this
  addresses Task 4.2's own checklist concern about a 100%-vs-0% rate off n=1 looking as confident
  as one off n=50 — no fixed "statistically significant" threshold is claimed, just a visible
  low-confidence flag). No button or control that applies anything — purely a table.

**Verify before moving on:**
1. Verified directly against the real dev DB: constructed 5 tagged `CallSession` rows (2x
   `PRICE_TOO_HIGH` one booked/one not, `DATES_UNAVAILABLE` unbooked, one multi-tag booked call,
   one `NO_OBJECTION` booked call) plus one untagged (`ai_summary=None`) call, called
   `objection_conversion_analytics` directly, and asserted the exact expected numbers
   (`PRICE_TOO_HIGH`: 3 total/2 resolved/1 unresolved; `DATES_UNAVAILABLE`: 2 total/1/1; baseline:
   5 total/3/2 — matching hand-calculated expectations exactly). First two attempts left orphaned
   test rows behind after the raw-SQL rewrite iteration (mid-script errors before cleanup ran) —
   caught and cleaned up via a direct DB audit (`WHERE exotel_call_id IS NULL AND created_at >=
   ...`) before the final clean run, which wrapped cleanup in `finally`.
2. Confirmed via the same test above and via `test_objection_insights_conversion_rate_and_multi_tag_handling`
   (`tests/test_analytics_api.py`) — the multi-tag call counts toward both `PRICE_TOO_HIGH` and
   `DATES_UNAVAILABLE`'s numerator/denominator, not double-counted in baseline (baseline counts
   calls, not tag-occurrences).
3. Confirmed via `test_objection_insights_scoped_to_authenticated_host` — two real `User` rows,
   `other_user`'s response is empty despite `test_user` having tagged data.
4. **Not completed as originally specified.** Attempted via Playwright against the real dev
   servers (backend `uvicorn --reload` + `next dev`), but this app's Clerk login (development
   mode) offers "Continue with Google" alongside email/password, and two automated attempts
   navigated into Google's actual live OAuth consent screen instead of Clerk's own email flow (a
   selector-targeting issue, not a CAPTCHA/bot-check as first suspected) — stopped deliberately
   rather than keep iterating against a real external auth provider. All seeded demo-account test
   data and scratch scripts were cleaned up; dev server was stopped. User was asked directly how
   to proceed and chose to skip visual verification and record it as a known gap rather than
   retry or self-verify. **This card's actual rendering (layout, dark mode, copy clarity) has
   never been visually confirmed by anyone as of this task's completion.**
5. Confirmed via `test_objection_insights_empty_for_host_with_no_tagged_calls` — `by_tag: []`,
   `baseline: {total_calls: 0, ..., conversion_rate: null}` (not `0` or `0%`, which would read as
   a real computed rate rather than "no data"), HTTP 200.
6. Confirmed: `git diff --stat` against `pricing_engine.py`/`negotiation_rule.py`/`pricing_rule.py`
   is empty — zero lines touched.

**Status: implementation done, one verification step incomplete (see #4 above — no visual
confirmation of the frontend card).** 3 new backend tests added (`tests/test_analytics_api.py`).
`npx tsc --noEmit` clean. Full backend suite: same 18 pre-existing failures, zero regressions
(1393 passed vs. 1390 before this task's tests, +3 new).

### Task 4.2 — PR Review

- [x] Fresh-context review, senior-engineer-on-a-real-PR posture.
- [x] Confirm, explicitly and by reading the diff (not by trusting this file's description), that
  no code path in this implementation writes to `NegotiationRule`, `PricingRule`, or any table
  `pricing_engine.py` reads for live decisions — this is the one invariant in this entire task
  list that must not slip, given the "no autonomous pricing changes" framing above.
- [x] Confirm the conversion-rate math is correct, specifically the multi-tag denominator handling
  and small-sample-size cases (e.g. a tag with only 1-2 calls shouldn't be presented with the same
  visual confidence as one with 50 — check whether the implementation added any indication of
  sample size next to the rate; if not, flag it as a finding, since a 100%-vs-0% rate off n=1 is
  actively misleading to a host).
- [x] Confirm tenancy isolation again, independently.
- [x] Confirm the frontend card is unambiguously informational — no dark pattern where an
  insight reads as an instruction ("we recommend lowering your price by X%") rather than a
  correlation ("calls with this objection converted at X% vs Y% baseline"). This distinction
  matters given no human review/approval step gates what the host sees, unlike the FAQ pipeline's
  gate on what reaches a future *call*.
- [x] Confirm this task didn't silently grow scope into implementation 3's endpoint/service (i.e.
  confirm it's additive, not a refactor of shared code that risks regressing the guard-analytics
  surface).
- [x] File findings, if any, and resolve them before marking this pair complete.

**Review verdict: approve with one finding, resolved.** Independent review (fresh sub-agent)
re-derived every invariant from scratch: confirmed the zero-write-path invariant by both an empty
`git diff --stat` against `pricing_engine.py`/`negotiation_rule.py`/`pricing_rule.py` and a grep for
any indirect reference; confirmed SQL-injection safety by reading the f-string construction
directly (only SQL keywords/placeholder names are interpolated, all user-controlled values flow
through the bound `params` dict); built an independent dataset (different shape from both the
implementer's and the automated tests') including a specific adversarial 3-tag-at-once case and
non-"booked" `Lead.status` values, hand-verified against its own independently-written SQL;
confirmed tenancy isolation with fresh accounts; read the frontend JSX closely enough to rule out
interpolation bugs a screenshot would have caught, and endorsed the `LOW_SAMPLE_THRESHOLD = 5`
framing as reasonable, appropriately hedged language (not a false-precision p-value claim).

One finding: the reviewer's own independent attempt to reproduce the documented reason for using
raw SQL instead of `.table_valued()` (an asyncpg `UndefinedColumnError` at PREPARE time) did **not**
reproduce on their end, despite matching SQLAlchemy/asyncpg versions against the same DB — raising
a real concern that the docstring's explanation could be steering future maintainers wrong. Rather
than dismiss either side, re-ran the exact failing case independently: 8/8 consistent, deterministic
reproduction on a fresh connection each time, ruled out both a Neon-pgbouncer/pooled-endpoint
explanation (this connection string uses Neon's direct, non-`-pooler` host) and a
session/rollback-state artifact (fresh session per attempt). The discrepancy between the two
environments was not root-caused. **Resolved by softening the docstring** from an unqualified
"confirmed" claim to one that states the reproduction is consistent and deterministic *in this
environment*, explicitly flags the reviewer's contrary result, states what was ruled out, and
clarifies the raw-SQL implementation is being kept because it's simpler and already proven correct
— not asserted as the only path that can ever work. This is a documentation-accuracy fix; the
shipped code (the raw-SQL version) was independently verified correct and safe regardless of which
explanation for choosing it holds up.

On the incomplete visual-verification step (#4 above): reviewer's independent judgment, after
reading the full JSX closely enough to check for the specific bug classes a screenshot would catch
(broken conditionals, `undefined` leaking into interpolated text, wrong Tailwind usage) and finding
none, was that this is a reasonable, non-blocking gap to accept for this specific kind of change —
not a general license to skip visual checks on future UI work.

---

## Closing regression pass (after all four implementations)

- [x] `cd backend && pytest` — full suite green, real Postgres, no mocking. 18 pre-existing,
  unrelated failures present at the start of this task list (confirmed via a baseline run with
  this session's changes stashed, before Implementation 1 began) and stable across every run
  through all four implementations — same 18 by exact name each time, 1393 passed at the end
  (vs. 1378 at baseline), +15 new tests (4 + 5 + 3 + 3) across the four implementations, zero
  regressions.
- [x] Re-read `CLAUDE.md`'s "Critical invariants" section and confirm none were violated across all
  four implementations — in particular the `ConversationQuality`/behavioral-feedback-loop
  invariant and the "do not duplicate existing services" instinct (implementations 3/4 should read
  as siblings of `faq_service.py`'s pattern, not parallel reinventions).
  - `ConversationQuality`: `conversation_quality.py` has zero diff across all four implementations
    (confirmed via `git diff` each time). Implementation 1 reads it only post-call, from
    `on_pipeline_finished`, after the object has already stopped receiving writes — no new
    consumer was added to the live call path, and the one documented `pending_style_correction`
    bridge is untouched.
  - "Do not duplicate existing services": Implementations 3 and 4 both explicitly model themselves
    on `faq_service.faq_gap_analytics`/`app/api/v1/faq.py`'s delegation pattern rather than
    inventing a parallel aggregation style — confirmed by both implementations' own PR reviews,
    which diffed the new code against `faq_gap_analytics` side by side.
  - No autonomous pricing/negotiation write path: `pricing_engine.py`/`negotiation_rule.py`/
    `pricing_rule.py` have zero diff across all four implementations (confirmed via
    `git diff --stat`, independently re-checked in Implementation 4's review).
  - External dependency failures don't terminate a live call: not applicable to any of these four
    implementations directly (none add a new external integration), but the one place this
    principle's spirit applies — `record_quality_events` (Implementation 1) writing from
    `on_pipeline_finished` — fails open with a broad `try/except` + rollback, verified live with a
    forced FK-violation.
- [x] Confirm `documentation/project_state.md` gets a short update noting these four features now
  exist, so this doesn't become the next audit's "surprising gap."
