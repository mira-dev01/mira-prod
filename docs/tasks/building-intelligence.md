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

- [ ] `backend/app/schemas/call_summary.py`: add `objection_tags: list[str]` (default `[]`) to
  `CallSummary` — a controlled vocabulary, not free text (matching this file's own existing
  "prefer a controlled list over prose substring matching" lesson). Define the allowed tags as a
  literal/enum near the schema, e.g. `PRICE_TOO_HIGH`, `DATES_UNAVAILABLE`, `LOCATION_MISMATCH`,
  `AMENITY_MISSING`, `POLICY_MISMATCH` (min stay / pets / cancellation), `HOST_UNRESPONSIVE`,
  `GUEST_STOPPED_RESPONDING`, `NO_OBJECTION` (explicit "call had no friction" value, not just an
  empty list, so a genuinely-fine call is distinguishable from "the model didn't extract
  anything"). Keep the list short and reviewed against a handful of real transcripts before
  finalizing — do not guess the taxonomy from first principles alone.
- [ ] `backend/app/services/call_summary_service.py`: extend `_SUMMARY_PROMPT` (lines 48-89) to
  instruct the model to select zero-or-more tags from the fixed vocabulary based on the transcript,
  output alongside the existing fields in the same JSON response. Extend the strict
  parsing/validation step (same file) to validate each returned tag against the controlled
  vocabulary and drop/ignore anything that doesn't match exactly — never pass through
  free-text/hallucinated tags, matching this service's existing "never raises, degrades cleanly"
  discipline.
- [ ] Confirm `CallSession.ai_summary` (JSONB, `call_session.py:59`) needs no migration — it's
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
3. Run one real end-to-end call via "Talk to Mira" with a clear objection (e.g. push back on price
   during negotiation) → after the call, `SELECT ai_summary FROM call_sessions WHERE id = '...'`
   and confirm `objection_tags` is present and sane.
4. Confirm the existing summary fields (`outcome`, `key_details`, etc.) are unchanged in shape/
   behavior — no regression to the dashboard's Call Details page rendering (check it manually,
   `frontend`'s call-detail view for this call renders without error).
5. Confirm no second LLM call was introduced — read the diff and confirm `summarize_call` still
   makes exactly one provider call per invocation.

### Task 2.2 — PR Review

- [ ] Fresh-context review, senior-engineer-on-a-real-PR posture, verify against the actual diff.
- [ ] Confirm the tag vocabulary is genuinely controlled end-to-end: check the parsing code path,
  not just the prompt instructions — an LLM can and will ignore prompt-level constraints
  sometimes, so the enforcement must be in code (a set-membership filter), not just wording.
- [ ] Confirm this is additive to the existing JSON schema/prompt, not a rewrite that risks
  regressing `outcome`/`key_details`/`missing_information` extraction quality — diff the prompt
  before/after and confirm the existing instructions are intact.
- [ ] Confirm `objection_tags` survives round-trip through `CallSession.ai_summary` JSONB with no
  serialization issue (e.g. confirm it isn't silently dropped by whatever Pydantic
  `model_dump()`/`dict()` call feeds `set_call_summary`).
- [ ] Confirm zero new LLM round-trips — re-check call count/latency claim independently (e.g. via
  logs from the real test call in verification step 3, confirming only one provider request fired
  for that call's summarization).
- [ ] Confirm the taxonomy was actually checked against real transcripts (per the implementation
  task's instruction not to guess it from first principles) rather than invented purely
  speculatively — ask for/inspect the transcripts used.
- [ ] File findings, if any, and resolve them before marking this pair complete.

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

- [ ] New function `quality_event_analytics(db, user_id, bucket: str = "week") -> dict` in
  `backend/app/services/call_service.py` (or a new `call_quality_service.py` if
  `call_service.py` is already large — check its current line count at implementation time and
  decide; prefer the existing file if it stays reasonably sized), modeled directly on
  `faq_service.faq_gap_analytics` (`faq_service.py:384-424`): most-frequent `rule` values by
  count, broken down by `severity`, over a time bucket, scoped to `user_id` via a join through
  `call_sessions.user_id`. Reuse `DateRange`/bucketing helpers from `app/api/v1/common.py` — the
  same ones `faq_gap_analytics` already uses — rather than inventing new date-bucketing logic.
- [ ] New endpoint `GET /api/v1/analytics/quality-events` in `backend/app/api/v1/analytics.py`,
  following the exact auth/response pattern already used by the other `/analytics/*` endpoints in
  this file (lines 25-433) — same dependency injection for the current user, same response-model
  conventions.
- [ ] Add a Pydantic response schema for this endpoint in `backend/app/schemas/` matching the shape
  `faq.py`'s gap-analytics response schema uses, for consistency.

**Verify before moving on:**
1. With real `call_quality_events` rows from implementation 1's test calls in the dev DB, call
   `quality_event_analytics` directly and confirm the counts/grouping match a hand-written
   `SELECT rule, severity, count(*) FROM call_quality_events ce JOIN call_sessions cs ON
   ce.call_session_id = cs.id WHERE cs.user_id = '...' GROUP BY rule, severity` run directly
   against Postgres.
2. `curl GET /api/v1/analytics/quality-events` (auth'd) — confirm it returns the same shape/values,
   confirm it's scoped correctly to the authenticated user (a second host account with no quality
   events returns an empty/zero result, not another host's data — check this explicitly, it's a
   tenancy-isolation correctness bug if it leaks).
3. Confirm the endpoint handles the zero-data case (no `call_quality_events` rows at all for this
   user) cleanly — empty list/zero counts, not a 500.
4. Confirm existing `/analytics/summary`, `/analytics/timeseries`, `/analytics/recovery` endpoints
   are unaffected (no shared code path broken) — `curl` each and compare against pre-change output.

### Task 3.2 — PR Review

- [ ] Fresh-context review, senior-engineer-on-a-real-PR posture.
- [ ] Confirm tenancy isolation explicitly — this is the single highest-severity thing to get wrong
  in a new analytics endpoint; re-verify with two real host accounts, not just by reading the code.
- [ ] Confirm the aggregation query is reasonably indexed (uses the `(rule, severity)` index added
  in implementation 1, not a full table scan) — check via `EXPLAIN` against the dev DB if the table
  has enough rows to make that meaningful, otherwise confirm the query shape is sane by inspection.
- [ ] Confirm this genuinely mirrors `faq_gap_analytics`'s bucketing/response conventions rather
  than inventing a parallel, subtly-different date-bucketing scheme — diff the two functions side
  by side.
- [ ] Confirm no write path was accidentally introduced — this task is read-only by design; any
  endpoint here that mutates `call_quality_events` or `call_sessions` is out of scope and a red
  flag for scope creep.
- [ ] File findings, if any, and resolve them before marking this pair complete.

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

### Task 4.1 — Implementation

- [ ] New function `objection_conversion_analytics(db, user_id, bucket: str = "month") -> dict` in
  `backend/app/services/lead_service.py` (it's fundamentally a `Lead.status`-vs-`CallSession
  .ai_summary->objection_tags` join, so it belongs with lead analytics, not call-quality
  analytics): for each `objection_tag` value seen in `call_sessions.ai_summary->>'objection_tags'`
  (JSONB query) joined to that call's `Lead.status`, compute conversion rate (`status = 'booked'`
  ÷ total calls carrying that tag) over the time bucket, scoped to `user_id`. Include an overall
  baseline conversion rate in the same response for comparison. Handle the "a call can carry
  multiple tags" case explicitly (a call with both `PRICE_TOO_HIGH` and `DATES_UNAVAILABLE` counts
  toward both tags' denominators) — decide and document this rather than leaving it implicit.
- [ ] New endpoint `GET /api/v1/analytics/objection-insights` in `backend/app/api/v1/analytics.py`,
  same conventions as implementation 3's endpoint.
- [ ] Frontend: a read-only card/section on the dashboard's Analytics or Pricing page (check
  `frontend/src/app/dashboard/` for the most natural existing home — likely `pricing/page.tsx`
  or an analytics page if one exists; check `frontend/AGENTS.md` first per this repo's own
  convention before touching frontend code) showing the top 2-3 objection tags by volume, each
  with its conversion rate vs. baseline, clearly labeled as informational — no button that
  "applies" anything automatically. This is a display-only card, reusing existing stat-card/
  chart components rather than inventing new ones (check `frontend/src/components/stat-card.tsx`
  and whatever sparkline/chart primitives already exist first).

**Verify before moving on:**
1. With real tagged calls (from implementation 2's test calls, plus a few more manually varied
   ones — at minimum one `PRICE_TOO_HIGH` call that didn't book and one clean call that did book,
   via the dashboard's manual `Lead.status` update) in the dev DB, call
   `objection_conversion_analytics` directly and hand-verify the conversion-rate math against a
   direct SQL query.
2. Confirm the multi-tag-per-call handling matches what was decided/documented — construct a call
   with two tags and confirm it's counted in both buckets, not just one or double-counted
   incorrectly in the baseline.
3. `curl GET /api/v1/analytics/objection-insights` (auth'd) — confirm tenancy isolation with a
   second host account (same check as implementation 3, same severity).
4. `npm run dev` — visually confirm the new card renders correctly with real data, in both light
   and dark mode, and confirm it reads as informational (no actionable-looking button/control that
   implies automatic application).
5. Confirm zero-data host accounts (no tagged calls yet) render a sensible empty state, not an
   error or a misleading "0% conversion" that looks like a real signal from no data.
6. Grep `pricing_engine.py` and `negotiation_rule.py` and confirm neither was touched by this
   task — this implementation must have zero write path into live pricing/negotiation logic.

### Task 4.2 — PR Review

- [ ] Fresh-context review, senior-engineer-on-a-real-PR posture.
- [ ] Confirm, explicitly and by reading the diff (not by trusting this file's description), that
  no code path in this implementation writes to `NegotiationRule`, `PricingRule`, or any table
  `pricing_engine.py` reads for live decisions — this is the one invariant in this entire task
  list that must not slip, given the "no autonomous pricing changes" framing above.
- [ ] Confirm the conversion-rate math is correct, specifically the multi-tag denominator handling
  and small-sample-size cases (e.g. a tag with only 1-2 calls shouldn't be presented with the same
  visual confidence as one with 50 — check whether the implementation added any indication of
  sample size next to the rate; if not, flag it as a finding, since a 100%-vs-0% rate off n=1 is
  actively misleading to a host).
- [ ] Confirm tenancy isolation again, independently.
- [ ] Confirm the frontend card is unambiguously informational — no dark pattern where an
  insight reads as an instruction ("we recommend lowering your price by X%") rather than a
  correlation ("calls with this objection converted at X% vs Y% baseline"). This distinction
  matters given no human review/approval step gates what the host sees, unlike the FAQ pipeline's
  gate on what reaches a future *call*.
- [ ] Confirm this task didn't silently grow scope into implementation 3's endpoint/service (i.e.
  confirm it's additive, not a refactor of shared code that risks regressing the guard-analytics
  surface).
- [ ] File findings, if any, and resolve them before marking this pair complete.

---

## Closing regression pass (after all four implementations)

- [ ] `cd backend && pytest` — full suite green, real Postgres, no mocking.
- [ ] Re-read `CLAUDE.md`'s "Critical invariants" section and confirm none were violated across all
  four implementations — in particular the `ConversationQuality`/behavioral-feedback-loop
  invariant and the "do not duplicate existing services" instinct (implementations 3/4 should read
  as siblings of `faq_service.py`'s pattern, not parallel reinventions).
- [ ] Confirm `documentation/project_state.md` gets a short update noting these four features now
  exist, so this doesn't become the next audit's "surprising gap."
