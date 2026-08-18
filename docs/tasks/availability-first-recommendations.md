# Availability-First Recommendations — Task List

Context: today, `recommend_properties` (`backend/app/voice/tools.py:530-745` →
`app/services/property/retrieval/orchestrator.py:28`) filters on location/budget/guest-count/
amenities and only *conditionally* excludes unavailable properties — `orchestrator.py:55-67` calls
`calendar_service.unavailable_property_ids` (one batched, cheap Postgres `IN(...)` query, never a
live iCal fetch) but **only if `check_in`/`check_out` already happen to be sitting in
`ConversationState.slots`** from an earlier turn. `RecommendPropertiesArgs`
(`schemas/tool.py:108-133`) has no date fields at all — dates are never a required precondition for
calling this tool, so a guest's first "anything in Goa?" gets recommendations with zero availability
awareness, and the guest only discovers a property is booked when they pick it and `check_calendar`
runs. This is Phase 2.4's known, documented gap (`documentation/agent-conversation-improvement.md:24-27`,
504-522) — Phase 2.4 narrowed it but did not close it.

This file plans the fix: make availability a precondition for recommendation, not a best-effort
post-filter; let the guest give a length-of-stay before exact dates; and replace today's silent
hard-exclusion of unavailable properties with an explicit partial-availability signal the agent can
speak aloud (e.g. "there's a booking Oct 3rd–5th, once your dates are finalised I can check other
options"). Full research/citations backing every claim in this paragraph are in this project's
conversation history (2026-08-16 planning session); this file tracks execution only.

**Format**: every implementation is exactly two tasks — an **Implementation** task and a **PR
Review** task, matching `building-intelligence.md`'s proven pattern. The review task is a real gate,
done in a fresh sub-agent invocation with no memory of writing the code. Every implementation task
additionally ends with a **Reverify** subtask — a final, standalone pass (after the PR review's
findings are resolved) that re-derives, from the actual diff and a live/DB-backed check, that the
change is structurally accurate and precise: correct types, correct call sites, no silent behavior
change outside the stated scope, and — critically for this task list, since every implementation
here touches the guest-facing recommend/availability path directly — no regression against
`CLAUDE.md`'s invariants (fail-open external/DB behavior, no hidden LLM regeneration, no new
concurrency/ownership logic, `ConversationState` populated only as a byproduct of a real tool call).
Do not start the next implementation until the current pair is done, reviewed, reverified, and clean.

Order matters — each implementation is a precondition for the next:
1 (duration-first slot) must land before 2 (unconditional availability filter) can gate on "do we
have *some* stay-length signal" rather than "do we have exact dates"; 2 must land before 3
(partial-availability classification) can mean anything; 4 (prompt/sequencing) depends on 1-3
existing to describe; 5 (guard extension) depends on 3's new partial-availability fact existing to
verify against.  Do not reorder.

**Sequencing note (2026-08-16, during Task 1.1's own implementation)**: this file's Implementations
1 and 2 were swapped from the original plan. The original Implementation 1 ("remove the conditional
gate around `unavailable_property_ids`") turned out not to be a real, removable gate at all —
`calendar_service.unavailable_property_ids(db, property_ids, check_in, check_out)` requires two
concrete `date` objects, so `orchestrator.py`'s `if check_in is not None and check_out is not None`
check is a genuine type guard, not an accidental short-circuit. There is no way to make that call
"unconditional" without first having *some* date/duration signal to pass it — which is exactly what
the original Implementation 2 (this file's new Implementation 1) produces. Caught before any code
was written, by re-reading `calendar_service.py`'s actual signature rather than assuming the plan's
own framing was correct; the numbering below reflects the corrected order going forward.

---

## Implementation 1 — Duration-first slot: ask "how many nights" before exact dates

**Problem this closes**: today there is no code concept of a vague date window or a night-count
independent of exact `check_in`/`check_out`. `GOLDEN_RULES` (`system_prompt.py:271-275`) already
tells the LLM to compute `check_out` from a stated night-count *once check_in is known*, but nothing
lets the agent reason about "3 nights sometime in the first week of October" before an exact
check-in is pinned down — today's structure forces the exact-date turn first. This implementation
adds a `nights`-only slot (independent of exact dates) so Implementation 2's precondition can be
satisfied by "guest gave a length of stay" even before an exact check-in exists, matching the task's
explicit ask: "understand the length of stay... before fixating on exact check-in/check-out dates."

**Explicitly not in scope**: no fuzzy/range date type added to `check_calendar`, `get_pricing`, or
`negotiate_rate` — those three tools still require exact concrete dates, unchanged, per `GOLDEN_RULES`'
existing "never invent a value the guest hasn't stated" rule. This implementation only adds the
*earlier*, coarser signal (`nights`) that feeds `recommend_properties`'s precondition — it does not
change how a booking is finally confirmed. `window_start`/`window_end` (loose calendar-window bounds)
were considered but deferred to Implementation 3, once its actual bounded-scan requirement is known,
rather than added speculatively here.

### Task 1.1 — Implementation

- [x] `backend/app/schemas/tool.py`: added `nights: int | None = None` to `UpdateLeadArgs`
  (`tool.py:145-150`), with a comment noting it's call-local only — popped out before
  `upsert_lead(**updates)` since `Lead` (`app/models/lead.py`) has no matching column, and
  `upsert_lead` (`lead_service.py:148-150`) does a blind `setattr(lead, key, value)` for every key in
  `**fields` with no allowlist. Confirmed this by reading `Lead`'s columns directly rather than
  assuming — a silent, non-persisted `setattr` (not an error, just a Python attribute that never
  reaches the DB) is exactly the kind of quiet gap `CLAUDE.md`'s "silent functional gap" lesson warns
  about, so this was made an explicit `updates.pop("nights", None)` in `handle_update_lead`
  (`tool_handlers.py`), not left implicit.
- [x] `backend/app/voice/tools.py`: added `nights: int | None = None` to `update_lead`'s wrapper
  signature and docstring (explaining it's for a vague-window case, not a substitute for exact
  dates), threaded into the `UpdateLeadArgs(...)` construction, and written to
  `state.slots["nights"]` via `set_slot` — except when a real `check_in` is also given this same
  call, in which case `nights` is explicitly popped from `state.slots` instead (an exact date
  supersedes an earlier vague answer; `set_slot` itself never overwrites with `None` by design, so
  this couldn't ride on that mechanism and needed an explicit `state.slots.pop("nights", None)`).
- [x] `backend/app/prompts/system_prompt.py`: extended `LEAD_AGENT_INSTRUCTIONS` step 2 so a vague
  window ("first week of October", "sometime next month") triggers asking for nights instead of an
  exact check-in date. Also extended `GOLDEN_RULES`' existing nights-arithmetic clause
  (`check_out = check_in + nights`) to explicitly scope it to "check_in already known" and pointed to
  the new nights-only case, since the two rules could otherwise read as contradictory (one says
  "compute check_out from nights," the other says "don't invent a check-in just to have one").
- [x] `backend/app/voice/conversation_state.py`: `_recompute_goal`'s slot-priority loop
  (`_SLOT_GOAL_PRIORITY`) previously derived `collecting_dates` purely from `check_in`/`check_out`
  presence — added a `_dates_known()` helper (`nights` known, OR both `check_in` and `check_out`
  known) so a guest who's only given `nights` doesn't get stuck being re-asked for exact dates every
  turn. Did **not** add a new `ConversationGoal` enum value — `collecting_dates` already covers "the
  dates question hasn't been meaningfully answered yet," and nights answering it doesn't need a
  distinct goal state; checked real usage of the existing goal before deciding this, not speculatively.

**Verify before moving on:**
1. ✅ Unit tests on `ConversationState`/`set_slot` (`test_conversation_state.py`,
   `test_conversation_state_slot_wiring.py`) confirming `nights` populates via the real `update_lead`
   tool wrapper, backfill/non-clobber semantics hold, and it's correctly excluded from the persisted
   `Lead` row.
2. ✅ Prompt-level pinning tests (`test_system_prompt.py`, matching Phase 2.3's own pattern):
   `test_lead_agent_asks_nights_before_exact_dates_for_a_vague_window` and
   `test_golden_rules_nights_only_case_does_not_contradict_check_out_arithmetic_rule` — both confirm
   the exact new wording landed, not just that the prompt still builds.
3. `GOLDEN_RULES`' "never invent a value" rule: not separately unit-tested this task (no code path
   invents a `nights` value — it's purely LLM-prompt-driven, same as every other slot field, and this
   file's existing tests already establish `set_slot` never writes `None`) — real-LLM adversarial
   testing of this specific prompt clause is deferred to Task 1.2's review, consistent with how this
   codebase treats prompt-content correctness elsewhere (verified live against the provider, not
   pinned by a unit test, since a unit test can only check wording presence, not whether the model
   actually obeys it).
4. ✅ Regression: full `test_conversation_state.py` + `test_conversation_state_slot_wiring.py` suites
   green (34 passed) — confirmed the goal-derivation change doesn't silently break the exact-date
   path. Caught and fixed one real regression during this step: the first implementation of
   `_dates_known()`-equivalent logic (an `any(k in slots for k in (check_in, check_out, nights))`
   check) wrongly treated `check_in`-alone (a real, partial answer with `check_out` still missing) as
   satisfying the whole gate, breaking
   `test_conversation_goal_different_real_paths_land_on_different_goals`. Fixed by requiring `nights`
   alone OR **both** `check_in` and `check_out`, not "any one of the three."
5. Real transcript check against the real LLM provider: **not run this task** — flagged explicitly as
   a gap for Task 1.2's review to close (this codebase's own convention, per `building-
   intelligence.md`'s Implementation 1/2 precedent, is that live-provider spot-checks are an
   appropriate thing for a fresh-context PR review to run independently rather than something the
   implementer's own verification pass is trusted to self-certify).

**Status: done.** Implementation, PR Review, and Reverify (Tasks 1.1-1.3) all complete, zero
findings across both independent passes. Tests green (94/94 `test_system_prompt.py`, 34/34
conversation-state suites), full backend suite re-run clean three times independently (18
pre-existing/environment-dependent failures, identical names each time, 1399 passed vs. 1397
baseline — zero regressions). Real-LLM adversarial transcript check run and passed. Ready to start
Implementation 2.

### Task 1.2 — PR Review

- [ ] Fresh-context review, senior-engineer-on-a-real-PR posture, verify against the actual diff.
- [ ] Confirm `nights` is genuinely optional everywhere it's threaded — no code path implicitly
  requires it and breaks for a guest who gives exact dates directly.
- [ ] Confirm the prompt change is additive to `LEAD_AGENT_INSTRUCTIONS`/`GOLDEN_RULES`, not a
  rewrite that risks regressing the already-sharpened Phase 2.3 "recommend before interrogation"
  wording or the pre-existing nights-arithmetic clause — diff the prompt before/after.
- [ ] Confirm zero new LLM round-trips (this should be a pure prompt/schema change, not a new
  classification call).
- [ ] Confirm `ConversationState`'s "populated only as a byproduct of a real tool call, never a
  separate LLM classification pass" discipline holds for `nights` — re-read the module docstring's
  stated invariant and check the diff against it directly.
- [ ] Confirm `nights` is correctly excluded from `Lead` persistence — independently verify (fresh
  DB round-trip, not trusting the implementer's own test) that `upsert_lead` never receives it.
- [ ] Run the real-transcript check the implementation task explicitly deferred: 2-3 hand-written
  vague-date transcripts ("first week of October", "a few nights sometime next month") against the
  real configured LLM provider, plus at least one adversarial case (guest refuses to give a
  night-count even after being asked twice) — confirm graceful degradation, not a stuck loop, and
  confirm `GOLDEN_RULES`' "never invent a value" rule holds in practice, not just in wording.
- [x] File findings, if any, and resolve them before marking this pair complete.

**Review verdict: approve, zero findings.** Fresh-context sub-agent review (no memory of writing the
code). Independently confirmed: `nights` touches zero files outside the stated scope (grepped the
full `app/` tree — absent from `orchestrator.py`, `RecommendPropertiesArgs`, `check_calendar`,
`get_pricing`, `negotiate_rate`); the `GOLDEN_RULES` nights-arithmetic diff is additive (the
reviewer's own `git diff` read confirms the old unconditional clause is now correctly qualified with
"AND you already have an exact check-in date," with Phase 2.3's "recommend before interrogation"
wording untouched, outside both diff hunks); zero new LLM round-trips; `ConversationState`'s
"populated only as a tool-call byproduct" discipline holds, `_dates_known()`'s shipped logic
(not just its comment) correctly requires nights-alone OR both dates, never "any one of the three."

Independently re-verified the `Lead` non-persistence guarantee with the reviewer's own fresh
throwaway test against the real test DB (re-fetching the row from Postgres, not the in-memory
object) — `nights` never reaches it — and proved via a direct REPL check that `setattr(lead,
"nights", value)` on the SQLAlchemy instance succeeds *silently* as an ordinary Python attribute
(`"nights" in Lead.__table__.columns` → `False`), confirming the `updates.pop("nights", None)` in
`tool_handlers.py` guards against a silent no-op, not a crash — exactly the bug class its own
comment describes.

Closed the gap Task 1.1 explicitly deferred: ran real completions against the actual configured
provider (Groq; `llama-3.3-70b-versatile` substituted for the dev key's rate-limited
`gpt-oss-120b` — production runs on a paid tier and doesn't hit this) using the real
`build_lead_system_prompt` output, unparaphrased. Three transcripts, verbatim output: (1) vague
window ("First week of Oct, maybe 3 nights") → model asked a natural follow-up and proceeded on the
nights signal without demanding an exact check-in; (2) exact dates given directly → unaffected,
`update_lead` called with real `check_in`/`check_out`, `nights` a harmless redundant derived value;
(3) adversarial deflection (guest refuses a night count twice) → `recommend_properties(null)` called
with no fabricated `nights`/`check_in`, `GOLDEN_RULES`' "never invent a value" rule held live, no
loop. Full suite re-run independently: 18 failed/1399 passed, matching the claimed baseline exactly;
re-stashed this PR's diff and re-ran against clean `dev` to confirm the two `test_tool_handlers.py`
failures (phone-normalization assertion) are pre-existing and unrelated despite that file being
touched. All 128 targeted tests across the three touched test files independently re-run, all pass.

### Task 1.3 — Reverify

- [x] Independent, standalone pass after 1.2's findings are resolved (1.2 had zero findings — nothing
  to resolve first).
- [x] Re-derive, from the final diff alone, exactly which files changed and confirm each change is
  consistent with this task's stated scope. `git status --short` at reverify time: `backend/app/
  prompts/system_prompt.py`, `backend/app/schemas/tool.py`, `backend/app/services/tool_handlers.py`,
  `backend/app/voice/conversation_state.py`, `backend/app/voice/tools.py`, plus the three test files
  and this task doc — exactly the file set Task 1.1 and 1.2 both named, no drift into
  `orchestrator.py`/`calendar_service.py`/`RecommendPropertiesArgs` (Implementation 2/3's territory).
- [x] Confirmed `nights`'s type against every actual read/write site (`grep -rn` for the literal
  `"nights"` key across `backend/app/`, not just the files the task doc named): written only at
  `tools.py:875,877` (`state.slots.pop`/`state.set_slot`) and popped at `tool_handlers.py:734`;
  read only via membership (`"nights" in slots`) in `conversation_state.py:174`'s `_dates_known` —
  never string-interpolated anywhere. The two other `nights`-named hits in the codebase
  (`call_summary_service.py:89,169`) are a pre-existing, unrelated concept (a locally-derived int in
  the post-call summary snapshot, not `ConversationState.slots`) — confirmed by reading that file's
  surrounding code, not just the grep match. No `"None nights"`-style interpolation risk exists
  because nothing interpolates this slot's value at all yet (by design — Implementation 2/3 are
  where it starts being read for anything beyond presence-checking).
- [x] Full test suite re-run independently a third time: `18 failed, 1399 passed` — identical failing
  test names to both the implementation and review passes. Targeted re-run of
  `test_conversation_state.py`, `test_conversation_state_slot_wiring.py`, `test_system_prompt.py`:
  all pass.
- [x] `CLAUDE.md` invariants re-confirmed directly against the final diff: no LLM call added anywhere
  in the diff (grep for any completion/classification call in the five changed source files returns
  nothing); `ConversationState`'s "populated only as a tool-call byproduct" holds for `nights` (its
  only writer is the real `update_lead` tool wrapper); the pre-existing exact-date tools
  (`check_calendar`/`get_pricing`/`negotiate_rate`) are untouched by this diff, so their own
  "never invent a value" enforcement is unaffected.

**Reverify verdict: confirmed clean.** Task 1.2's fresh-context review already ran its own
independent throwaway DB test, its own real-provider transcript check (quoted verbatim above), and
its own stash-and-compare baseline confirmation — this pass's incremental contribution was the one
check 1.2 didn't explicitly perform (a full read-site audit of every place `"nights"` is written or
read, confirming zero string-interpolation risk) plus a third independent full-suite run. No new
findings. Implementation 1 is done.

---

## Implementation 2 — Unconditional availability pre-filter in `recommend_properties`

**Problem this closes**: the Phase 2.4 exclusion (`orchestrator.py:55-67`) only runs when dates are
already in `state.slots`. On a guest's first ask, before any date has been given, `recommend_properties`
returns properties with zero availability awareness — the exact bug described in the task. This
implementation does not yet add partial-availability (that's #3); it makes the *existing* boolean
exclusion mechanism run whenever *any* date/duration information is available — including the
`nights`-only signal Implementation 1 now produces, not just exact `check_in`/`check_out`.

**Explicitly not in scope**: no change to `calendar_service.is_available`/`unavailable_property_ids`'s
actual semantics (the overlap query itself is correct and stays as-is). No new tool, no new LLM
round-trip. No partial-availability logic yet — this implementation keeps today's all-or-nothing
exclusion, just makes it fire off a broader/earlier set of real signals (including nights-only)
rather than requiring exact dates already sitting in state.

### Task 2.1 — Implementation

**Design decision (resolved before implementation, not deferred to implementation time)**: of the
two alternatives this task originally posed, going with the **deferral** option — the availability
pre-filter stays gated on exact `check_in`+`check_out` only; a `nights`-only guest is NOT
availability-filtered by this implementation. Reasoning: `_today_anchor()`
(`system_prompt.py:52-128`), the codebase's existing mechanism for resolving vague dates, exists
strictly so the *LLM* can recognize and echo back a date the guest actually said — it is never used
to let backend code silently fabricate a date on the guest's behalf. Synthesizing an anchor
`check_in` from `nights` alone to feed `unavailable_property_ids` would be exactly that: inventing a
value the guest never stated, directly contradicting `CLAUDE.md`'s explicit invariant, and it would
filter a real candidate set against a fake window — capable of wrongly excluding a property that IS
available on the guest's real (still-unknown) dates. The correct primitive for "nights-only guest,
availability-aware" is Implementation 3's bounded-window partial-availability scan, not a guessed
exact date fed through today's binary exclusion. This implementation is scoped down accordingly: it
still closes the real, common-case bug (a guest who already gave or later gives exact dates gets
availability-blind recommendations) without inventing anything for the nights-only case, which stays
exactly as availability-blind as before until Implementation 3 ships.

- [ ] `backend/app/services/property/retrieval/orchestrator.py`: the `if check_in is not None and
  check_out is not None` gate around the `unavailable_property_ids` call (`orchestrator.py:55`) is a
  genuine type guard — `unavailable_property_ids(db, property_ids, check_in, check_out)` requires two
  concrete `date` objects, so it cannot be called with `None`, and per the design decision above this
  task does not synthesize one. What actually changes this task: confirm/harden that this filter
  fires on *every* real path where exact dates ARE known (not just the one today's Phase 2.4 code
  covers) — in particular, confirm `tools.py`'s `_parse_iso_date(state.slots.get("check_in"))`
  sourcing picks up dates the instant `update_lead` sets them (already true per Implementation 1's
  Task 1.1, which left `check_in`/`check_out`'s existing slot-write path untouched), and add the
  `nights`-vs-exact-dates test matrix below to make today's *actual* boundary (filter runs iff both
  exact dates known, regardless of whether `nights` is also set) explicit and pinned, rather than
  implicit/assumed.
- [ ] Keep the existing `try/except: pass` fail-open behavior byte-identical (per `CLAUDE.md`'s
  "external dependency failures must not unnecessarily terminate a live guest call").
- [ ] Add a `logger.info` line (call-session-scoped, not `logger.debug`) when the filter actually
  excludes at least one candidate — useful signal for verifying in Task 2.2/Reverify that the filter
  is firing on real calls, not just passing tests.
- [ ] Do not touch `RecommendPropertiesArgs` in this task — dates/nights still flow in only via
  `ConversationState.slots`, not as an LLM-supplied tool argument.

**Self-review find during "confirm the filter fires on every real path" (Task 2.1's own scoped-down
verification work) — a real, pre-existing, live crash bug, not introduced by this task:**
`_parse_iso_date` (`tools.py:88`) assumed every value in `state.slots["check_in"/"check_out"]` was
an ISO **string** and called `date.fromisoformat(value)` unconditionally. That assumption was only
ever true for `update_lead`'s wrapper (which explicitly calls `.isoformat()`). `check_calendar`,
`get_pricing`, and `negotiate_rate`'s wrappers (`tools.py:166-167`, `217-218`, `521-522`) all write
`args.check_in`/`args.check_out` straight through as raw `date` objects (their own arg schemas
already type these fields as `date`, coerced by Pydantic from the ISO string the LLM passes).
`date.fromisoformat()` only accepts `str` and raises `TypeError` — not `ValueError`, so the existing
`except ValueError` never caught it — on a `date` object. This meant `recommend_properties`'s wrapper
crashed **uncaught**, propagating out of the tool call entirely rather than failing open, the instant
a guest asked for another recommendation after `check_calendar`/`get_pricing`/`negotiate_rate` had
already set dates in the same conversation (e.g. "actually, anything else in that price range?"
after checking one property) — a realistic, common call shape. No existing test exercised this exact
call order; confirmed via a direct repro (`date.fromisoformat(date(2026,10,5))` → `TypeError:
fromisoformat: argument must be str`) before touching any code. **Fixed** as part of this task
(in scope: this is precisely what "confirm the filter fires on every real path" was verifying) —
`_parse_iso_date` now accepts both `str` and `date`, returning `date` objects unchanged and still
failing open to `None` on anything else. Proved the new regression tests are real (not vacuous) by
temporarily reverting the fix via `git stash` and confirming both new tests fail without it, then
restoring the fix and re-confirming green.

- [x] `backend/app/services/property/retrieval/orchestrator.py`: added a `logger.info` line (module
  now has its own `logger`) firing whenever the pre-filter actually excludes at least one candidate,
  logging `call_session_id`/`check_in`/`check_out`/exclusion count — visible without enabling debug
  logging, so a real call's exclusion behavior can be confirmed from production logs.
- [x] `backend/app/voice/tools.py`: `_parse_iso_date` fixed as described above (accepts `str | date |
  None`, `isinstance` check before the `fromisoformat` fallback, `except (ValueError, TypeError)`).
- [x] Confirmed (per the design decision above) that `RecommendPropertiesArgs` remains untouched, and
  that the `nights`-only path deliberately still skips the filter — pinned explicitly by a new test
  rather than left implicit.

**Verify before moving on:**
1. ✅ Existing Phase 2.4 tests (`test_property_retrieval_orchestrator.py`,
   `test_calendar_service.py::unavailable_property_ids`) still pass unmodified — confirms the
   underlying query semantics are untouched.
2. ✅ New test `test_recommend_properties_after_check_calendar_does_not_crash_on_raw_date_slots`
   (`test_conversation_state_slot_wiring.py`): reproduces the exact crash scenario end-to-end through
   the real wrapper chain (`check_calendar` then `recommend_properties`), confirms it no longer
   raises and the availability filter still correctly excludes a booked property. Also asserts the
   premise directly (`state.slots["check_in"]` really is a raw `date`, not a string) so the test
   can't silently stop testing what it claims to if a future edit changes that.
3. ✅ New direct unit test `test_parse_iso_date_accepts_both_iso_string_and_raw_date_object`
   (`test_voice_tools.py`) pinning `_parse_iso_date`'s full contract (string, date, `None`, and
   malformed-string inputs) independent of the wrapper-level integration test.
4. ✅ New test `test_recommend_properties_nights_only_still_skips_availability_filter`
   (`test_conversation_state_slot_wiring.py`): pins the resolved design decision (deferred to
   Implementation 3) explicitly, rather than leaving it implicit/assumed — a `nights`-only guest's
   booked-property result is unfiltered, exactly as before this task.
5. ✅ The existing Phase 2.4 fail-open test (`test_recommend_properties_availability_check_fails_open_on_error`,
   `test_property_retrieval_orchestrator.py`) already covers this and needed no change — confirmed
   it still passes; the fix only widened what `_parse_iso_date` accepts, never touched the
   `try/except: pass` around `unavailable_property_ids` itself.
6. ✅ Real dev-DB check: covered by verification step 2 above, which runs the actual
   `recommend_properties` wrapper (not orchestrator.py in isolation) end-to-end against the real test
   Postgres DB with a seeded booking.

**Status: done.** 3 new tests added (2 in `test_conversation_state_slot_wiring.py`, 1 in
`test_voice_tools.py`), all passing, all independently confirmed to fail without the fix. Full
backend suite re-run clean: 18 pre-existing/environment-dependent failures (identical names to the
established baseline), 1402 passed (up from 1399).

### Task 2.2 — PR Review

- [x] Fresh-context review (new sub-agent, no memory of writing the code), senior-engineer-on-a-
  real-PR posture, verify against the actual diff, not the task description.
- [x] Confirm the diff genuinely does NOT synthesize/guess a `check_in` from `nights` alone anywhere
  — re-read the task's own design-decision writeup and confirm the shipped code matches it (deferral,
  not anchor-guessing). This is the one place this implementation could most easily regress toward
  violating `CLAUDE.md`'s "never invent a value the guest hasn't stated" invariant if a future edit
  quietly reintroduces the discarded alternative.
- [x] Confirm fail-open discipline independently.
- [x] Confirm zero change to `calendar_service.py`'s query semantics.
- [x] Confirm `RecommendPropertiesArgs` is untouched.
- [x] Confirm no new N+1 pattern was introduced (still one batched query, not per-property calls).
- [x] File findings, if any, and resolve them (fix + re-verify) before marking this pair complete.

**Review verdict: approve, zero findings.** Fresh-context sub-agent review (no memory of writing the
code). Most important item — independently re-reproducing the claimed pre-existing bug, since the
whole task's legitimacy for touching an "out of scope" file (`tools.py`) rests on it being real —
confirmed directly: `date.fromisoformat(date(2026,10,5))` → `TypeError: fromisoformat: argument must
be str` in a REPL; isolated just the pre-fix function body (old `except ValueError` only, no
`isinstance` guard) while leaving the rest of the working tree intact and ran the real
`test_recommend_properties_after_check_calendar_does_not_crash_on_raw_date_slots` test against it —
failed with that exact `TypeError`, at `tools.py`'s `_parse_iso_date`, through the real wrapper chain
(`check_calendar` → `recommend_properties`), not a synthetic scenario. Restored the fix and
re-confirmed both new tests pass. Cross-checked all three raw-`date`-writing call sites
(`check_calendar`/`get_pricing`/`negotiate_rate`) against their arg schemas' `date`-typed fields, and
confirmed `update_lead` is the only wrapper calling `.isoformat()` — the bug's stated scope is
accurate, not overstated.

Confirmed the fix's `except (ValueError, TypeError)` is precisely scoped (no broad `except
Exception` that could mask an unrelated bug) by direct execution of all four input classes (real
`date`, valid ISO string, `None`, malformed string). Confirmed via grep that `nights` is never read
anywhere near `check_in`/`check_out` computation in either `orchestrator.py` or `tools.py`, and
`_today_anchor` is not referenced in either file — the design decision (deferral, not anchor-
guessing) is what's actually shipped, with zero drift toward the rejected alternative. `git diff`
confirmed the `try/except: pass` fail-open block in `orchestrator.py` is byte-identical in shape
(only a `logger.info` call added inside the `if unavailable_ids:` branch); `calendar_service.py` has
zero diff, still one batched `IN(...)` query; `RecommendPropertiesArgs` untouched (only
`UpdateLeadArgs` gained `nights`, from Implementation 1). The new log line was confirmed to be
`logger.info` (not `.debug`), fires only when a candidate is actually excluded, and logs only
dates/count/call_session_id — no guest PII. The `nights`-only test was confirmed to pin real,
non-vacuous behavior (a real seeded `Booking` row genuinely still surfaces in results). Full suite
re-run independently: 18 failed/1402 passed, matching Task 2.1's own count exactly; two
`test_tool_handlers.py` failures spot-verified as the same pre-existing/unrelated failures Task 1.2
already documented. All 55 targeted tests across the four relevant test files independently re-run,
all pass.

### Task 2.3 — Reverify

- [x] Independent, standalone pass (2.2 had zero findings — nothing to resolve first).
- [x] Ran a fresh seeded-booking check with data distinct from every prior pass (new property names
  "Reverify Booked/Open Cabin," Manali/different phone numbers, 45-days-out dates vs. the 10-day
  offsets used elsewhere), via a throwaway pytest test using the real fixtures (not the ad hoc script
  approach, which hit an unrelated `User` model field mismatch against this environment's config —
  switched to the fixture-based approach instead of working around that). Exercised the exact crash
  path end-to-end (`check_calendar` writing a raw `date` into `state.slots`, then `recommend_properties`
  reading it back) — passed, booked property excluded, no crash. Throwaway test file deleted after
  running (`git status` confirmed clean, no stray file left behind).
- [x] Full test suite re-run independently a third time: `18 failed, 1402 passed` — identical failure
  set to both the implementation and review passes.
- [x] Confirmed diff scope directly: `git diff app/voice/tools.py | grep '^@@'` shows five hunks —
  one at `_parse_iso_date` (Implementation 2's actual change) and four inside `build_voice_tools`
  (Implementation 1's `nights` wiring, already reviewed/reverified in that section). No hunk touches
  anything outside these two files plus Implementation 1's already-accounted-for territory; the four
  other modified files in this branch (`system_prompt.py`, `schemas/tool.py`, `tool_handlers.py`,
  `conversation_state.py`) all belong to Implementation 1, not this task.
- [x] `CLAUDE.md` invariants re-confirmed directly: fail-open block byte-identical (only a
  `logger.info` call added); no LLM round-trip anywhere in the diff; `ConversationState` untouched by
  this task (Implementation 1's territory); no anchor-date synthesis anywhere (re-grepped `nights`
  and `_today_anchor` in both changed files — same result as Task 2.2's independent check).

**Reverify verdict: confirmed clean.** Task 2.2's review already independently reproduced the core
bug claim from first principles (REPL + isolated pre-fix function body against the real test),
verified the fix's exact exception handling, and confirmed the anchor-date-synthesis non-regression
by direct grep. This pass's incremental contribution: a third independent full-suite run, and a
fourth independent seeded-booking check using entirely fresh data/property names/dates to rule out
any test-data coincidence. No new findings. Implementation 2 is done.

---

## Implementation 3 — Partial-availability classification

**Problem this closes**: `calendar_service` today only answers strict boolean questions (`is_available`)
or produces a hard exclusion set (`unavailable_property_ids`) — there is no concept of "available for
3 of your 5 requested nights." The task explicitly asks for this: "There is a booking on this
property from October 3rd to 5th... I can check again and recommend other properties." This is new
domain logic, not a rewiring of existing pieces, so it lands last among the three "hard" pieces.

**Explicitly not in scope**: no change to `next_available_window`'s existing forward-scan behavior
(next fully-open window of the same length, outside the requested range) — that remains a distinct,
separate fallback used only from `check_calendar` once a specific property is confirmed unavailable.
This implementation's new function answers a different question (which parts of *this specific
requested window* are free) and is used earlier, from the recommend path, not as a replacement for
`next_available_window`.

### Task 3.1 — Implementation

**Design decision (resolved before implementation, per the review checklist's own flagged
ambiguity)**: `status == "full"` means the entire requested window has ZERO overlapping confirmed
bookings — the guest can land on any dates within their window and it will work, no caveat needed.
`status == "partial"` means at least one confirmed booking overlaps the window, but at least one
`nights`-length contiguous free sub-range still remains somewhere in it — the property might still
work depending on which exact dates the guest lands on, and the conflicting booking(s) must be
surfaced so the agent can say so rather than silently including or excluding it. `status == "none"`
means no `nights`-length free sub-range exists anywhere in the window. Rejected the alternative
reading ("full" = "some sub-range works, regardless of other conflicts elsewhere in the window")
because it would let a property with a real, guest-visible conflict inside their stated window
render as a clean, no-caveat recommendation — the exact bug this whole task list exists to fix,
just reintroduced one level down. This matches the task's own motivating example directly: a
property with an Oct 3–5 booking inside the guest's window must never be presented as a plain,
unqualified match.

- [x] `backend/app/services/calendar_service.py`: new `AvailabilityWindowResult` frozen dataclass
  (`status: Literal["full", "partial", "none"]`, `conflicting_bookings: list[tuple[date, date]]`) and
  `partial_availability(db, property_id, window_start, window_end, nights) ->
  AvailabilityWindowResult`. Queries `Booking` rows overlapping `[window_start, window_end)` for that
  property (same `status == "confirmed"` filter, same half-open-interval overlap test `is_available`
  already uses — no second, divergent definition of "booked"). Computes free contiguous sub-ranges
  within the window by sorting conflicting bookings and walking the gaps between them (including
  before the first and after the last), checking each gap's length against `nights` — bounded by the
  window itself, never a 90-day scan. `status`/`conflicting_bookings` follow the design decision
  above: `"full"` iff zero overlapping bookings; `"none"` iff no gap is `>= nights` days;
  `"partial"` otherwise (bookings exist AND at least one sufficient gap exists).
  - Bound the scan to a guest-stated window. Implementation 1 deliberately did NOT add
    `window_start`/`window_end` slots (deferred here, per that implementation's own scope note) — add
    them now if this task's actual design needs an explicit outer window distinct from `nights`
    itself (e.g. "3 nights, sometime in the first week of October" needs both the window AND the
    length), following the same `UpdateLeadArgs`/`ConversationState.slots` pattern Implementation 1
    used for `nights`. If no explicit window was given, derive a sensible default span from `nights`
    alone. Do not reuse `next_available_window`'s 90-day unbounded scan pattern here; this is a
    bounded, cheap query against `Booking` rows already scoped by `property_id` and a date range, not
    a day-by-day loop.
- [x] Added a **batched** variant, `partial_availability_for_candidates(db, property_ids,
  window_start, window_end, nights) -> dict[uuid.UUID, AvailabilityWindowResult]`, sharing one
  private query helper (`_conflicting_bookings_in_window`) with the single-property primitive —
  genuinely one query regardless of candidate-set size (confirmed via a query-counting test, not
  just correctness). This is the function `orchestrator.recommend_properties` actually calls.
- [x] `backend/app/services/property/retrieval/orchestrator.py`: replaced Implementation 2's hard
  `unavailable_property_ids`-based exclusion with the new partial-availability classification —
  `status == "full"` properties are eligible for recommendation as before; `status == "none"` are
  excluded exactly as the old hard exclusion already did; `status == "partial"` are held out of
  `sql_results`/`options` but retained separately via a new `partially_available` field on
  `RecommendationResult`. The existing fail-open `try/except` around this call is unchanged in shape.
- [x] `backend/app/services/property/pitch_formatter.py`: new frozen `PartiallyAvailableProperty`
  dataclass (`spoken_name`, `conflicting_bookings`) — deliberately a SEPARATE, much smaller structure
  than `PropertyCard`, not another `PropertyCard` field, so a partially-available property is
  structurally incapable of being rendered through `format_property_pitch_line`'s normal per-option
  path as if it were a clean match. `RecommendationResult` gained `partially_available: list[...]`.
  `render_recommendation_text` now speaks the real conflicting dates (matching the task's exact
  phrasing) both when partial results exist alongside full ones (appended after the main pitch) and
  when they're the ONLY thing found (a new branch distinct from the generic `not_found` text, since a
  property genuinely does exist in that case — confirmed this distinction explicitly rather than
  letting a real partial-only result read as "nothing found at all").
  `backend/app/services/property/retrieval/context_builder.py`'s `build_recommendation_result` gained
  a `partially_available: list[tuple[Property, list[tuple[date, date]]]] | None` parameter, converting
  raw tuples into `PartiallyAvailableProperty` entries and correctly setting `not_found=False` when a
  partial match exists even though `properties` (the full-match list) is empty.
- [x] `backend/app/prompts/system_prompt.py`: added guidance to `LEAD_AGENT_INSTRUCTIONS` step 4
  instructing the model to NEVER present a partial-availability property as a clean match, and to
  name the real conflicting dates using phrasing matching the task's own example almost verbatim.

**Design-decision-driven structural finding (caught while writing Task 3.1's own verification
tests, before this task could be considered done)**: an EXACT `check_in`/`check_out` pair always
implies `nights == the full window span` (there's no other value it could sensibly be), so any
booking conflicting anywhere inside that exact span always leaves `has_sufficient_gap = False` --
`status` is structurally always `"none"` in that case, never `"partial"`. This meant that through
every real call path existing before this task, `"partial"` was **unreachable dead code** — a real,
if narrower, echo of Implementation 2's own `_parse_iso_date` finding (a new mechanism with zero
live callers exercising its actual branch). `"partial"` only means something distinct from `"none"`
when the guest's window is WIDER than their actual stay length (the task's own example: "the first
week of October" + "3 nights" — a 7-day window for a 3-night stay). Implementation 1 deliberately
deferred adding `window_start`/`window_end` slots pending this exact confirmation (see that
implementation's own scope note); this task's own Task 3.1 spec anticipated the need explicitly
("add them now if this task's actual design needs an explicit outer window distinct from nights
itself"). **Resolved by adding them now**, closing the gap within this task rather than shipping a
structurally dead branch:
- [x] `backend/app/schemas/tool.py`: `UpdateLeadArgs` gained `window_start`/`window_end: date | None`,
  same call-local/non-persisted pattern as `nights` (popped in `handle_update_lead` before
  `upsert_lead(**updates)`, same "no matching `Lead` column" reasoning).
- [x] `backend/app/voice/tools.py`: `update_lead`'s wrapper gained `window_start`/`window_end: str |
  None` params + docstring, written to `state.slots` alongside `nights`, and cleared together with
  `nights` the instant a real `check_in` supersedes them (same non-lingering-stale-value discipline).
  `recommend_properties`'s wrapper now falls back to `state.slots["window_start"/"window_end"]` +
  `state.slots["nights"]` as the scan window/length ONLY when exact `check_in`/`check_out` are both
  absent — exact dates, when known, always take precedence and are never overridden by a stale window.
- [x] `backend/app/services/tool_handlers.py`/`orchestrator.py`: `nights` threaded through
  `handle_recommend_properties`/`recommend_properties` as a new optional parameter, defaulting to
  `(check_out - check_in).days` when omitted (the common exact-dates case).

**Verify before moving on:**
1. ✅ 7 new unit tests directly against `partial_availability`/`_classify_window`
  (`test_calendar_service.py`): no bookings → `"full"`; booking fully covering window → `"none"`;
  booking covering part of the window with a sufficient remaining gap → `"partial"` with correct
  `conflicting_bookings`; same booking with `nights` too long for any remaining gap → `"none"` (not
  just "zero conflicts" — confirms the actual status *definition*, not a weaker proxy); non-confirmed
  booking → ignored; booking entirely outside the window → ignored. Also hand-verified interval
  boundary math directly in a REPL before writing tests (booking ending exactly at window start,
  starting exactly at window end, exactly equal to the full window, spanning either/both window
  edges) — found and fixed a real-but-harmless-by-coincidence bug during this check: the initial gap
  computation could produce a negative-length "gap" (correct by accident of comparison direction, not
  by explicit design) when a booking started before `window_start`; fixed by explicitly clamping both
  gap endpoints to the window, not just the start.
2. ✅ 2 new tests for the batched variant (`test_calendar_service.py`): correct mixed full/partial/none
  classification across 3 properties from a query-count-asserted single query (not just correctness),
  plus an empty-input no-query case.
3. ✅ New orchestrator-level tests (`test_property_retrieval_orchestrator.py`): a mixed
  full/partial/none candidate set produces `options` containing only the full match, with the partial
  match surfaced via `partially_available` rather than silently dropped; a dedicated test confirming
  `nights` genuinely defaults from `(check_out - check_in).days` when omitted (proven by showing the
  SAME booking data classifies differently depending on whether `nights` is passed explicitly or left
  to default — not just asserting a single outcome that could coincidentally match either behavior).
4. ✅ Fail-open test updated (not left silently vacuous): the pre-existing
  `test_recommend_properties_availability_check_fails_open_on_error` monkeypatched
  `calendar_service.unavailable_property_ids`, which `orchestrator.py` no longer calls after this
  task's own change — caught this during verification (the test still passed, but the mock was never
  invoked, meaning it had silently stopped testing the failure path at all). Fixed to monkeypatch
  `partial_availability_for_candidates` instead, re-confirmed it genuinely exercises the failure path.
5. ✅ Real end-to-end wrapper-chain tests (`test_conversation_state_slot_wiring.py`), covering the
  structural finding above directly: one test exercises the actually-reachable "partial" scenario
  (nights + explicit window via `update_lead`, then `recommend_properties` speaks the real conflicting
  dates for one property alongside a clean recommendation for another); a companion/contrast test
  confirms an exact-dates conflict is genuinely `"none"` (excluded entirely), not `"partial"` — pinning
  the structural finding itself, not just working around it silently. Plus 4 new slot-wiring unit
  tests for `window_start`/`window_end` (write/read, supersession-on-exact-check_in, non-persistence
  to `Lead`). Plus a prompt-pinning test (`test_system_prompt.py`) confirming the new phrasing
  guidance landed with the task's near-verbatim example text, and 4 pitch-formatter-level tests
  (`test_property_card_and_pitch_formatter.py`) covering the render/not_found-distinction logic in
  isolation, plus 2 `build_recommendation_result`-level tests confirming the `not_found` vs.
  partial-match distinction end-to-end through the real `Property` → entry conversion.
  Real-LLM adversarial transcript testing of the new phrasing guidance (does the model actually
  avoid presenting a partial match as clean, in practice) is explicitly **deferred to Task 3.2's
  review**, consistent with this task list's established convention (Implementation 1/2's own
  reviews closed exactly this kind of deferred real-provider gap).

**Status: done.** 30 new tests across 5 test files (7 + 2 in `test_calendar_service.py`, 3 in
`test_property_retrieval_orchestrator.py` including the fail-open fix, 6 new + 1 fixed in
`test_conversation_state_slot_wiring.py`, 1 in `test_system_prompt.py`, 6 in
`test_property_card_and_pitch_formatter.py`). Full backend suite re-run clean: 18 pre-existing
failures (identical names to the established baseline), 1423 passed (up from 1402). Real-LLM
transcript check deferred to Task 3.2, per this file's own established convention.

### Task 3.2 — PR Review

- [x] Fresh-context review, senior-engineer-on-a-real-PR posture, verify against the actual diff.
- [x] Confirm the overlap/interval-math logic in `partial_availability` is genuinely correct at the
  boundaries (off-by-one on half-open intervals is the classic bug class here) — hand-verify at
  least 3 boundary cases independently (booking ending exactly at window start, booking starting
  exactly at window end, booking exactly equal to the full window).
- [x] Confirm the batched variant is genuinely one query (or a small constant number), not O(N)
  round trips disguised as "batched" — check the actual SQL/query plan, not just the function
  signature.
- [x] Confirm no duplicate/divergent definition of "booked" was introduced — the overlap predicate
  here must match `is_available`'s exactly (same `status == "confirmed"` filter, same operator
  directions), or any divergence must be explicitly justified, not accidental.
- [x] Confirm `next_available_window` and its existing callers are completely untouched by this
  diff, per the task's explicit scope boundary.
- [x] Confirm the prompt guidance doesn't let the LLM present a `"partial"` result as if it were a
  full recommendation — this is the crux of the original bug being fixed, so re-verify it directly
  with a real adversarial transcript (a property that's `"partial"` should never come out sounding
  like a confirmed availability match).
- [x] File findings, if any, and resolve them before marking this pair complete.

**Review verdict: approve-with-findings (one non-blocking finding). Fresh-context sub-agent
review, no memory of writing the code.**

#### 1. The "partial was dead code before window_start/window_end" claim — CONFIRMED, independently

Wrote 12 throwaway tests (`backend/tests/test_review_scratch_availability.py`, deleted after this
review — `git status` confirmed clean afterward) with hand-constructed dates never reused from the
implementer's own fixtures, run against the real test Postgres DB
(`postgresql+asyncpg://mira:mira@localhost:5432/mira_test`).

- Four separate exact-`check_in`/`check_out` scenarios (`nights = (window_end - window_start).days`,
  matching `orchestrator.recommend_properties`'s own fallback and `tools.py`'s exact-dates wrapper
  path exactly) with a conflict positioned near the start, near the end, in the middle, and as a
  same-day full-window overlap — **all four independently produced `"none"`, never `"partial"`**.
- A contrast case with the SAME conflict position but a window wider than `nights` (10-day window,
  3-night stay) **did** produce `"partial"`, confirming the distinction is real and the scope
  expansion actually closes a genuine gap, not an imagined one.
- The claim is real: an exact date pair structurally forces `nights == full span`, so any conflict
  anywhere inside that span always empties every candidate gap below the `nights` threshold. The
  `window_start`/`window_end` addition was necessary, not speculative scope creep.

#### 2. Boundary-case interval math — CONFIRMED correct, clamp fix is real and load-bearing

Hand-constructed 6 boundary cases independently (fresh dates, none copied from
`test_calendar_service.py`): booking ending exactly at `window_start` → `"full"`, zero conflicts
reported (correct, exclusive boundary); booking starting exactly at `window_end` → `"full"` (same);
booking exactly equal to the full window → `"none"`; booking starting before `window_start` and
overlapping in → `"partial"` at the exact gap boundary (`nights` = gap length) and `"none"` one day
over; booking ending after `window_end`, symmetric case, same result; booking spanning both edges
(fully covering the window from outside it on both sides) → `"none"`, no crash.

Directly reproduced the "negative-length gap that happens to compare correctly by coincidence" bug
the code comment describes: simulating the pre-fix (unclamped) `gap_end = check_in` computation
against two **overlapping** bookings produces a genuine negative-length gap
(`2027-11-10 -> 2027-11-08`, i.e. −2 days) that only accidentally compares correctly
(`-2 >= nights` → `False`) rather than being clamped by design. Confirmed the shipped
`_classify_window` (`backend/app/services/calendar_service.py:144-145`) does clamp both endpoints
(`gap_start = max(cursor, window_start)`; `gap_end = max(min(check_in, window_end), gap_start)`) —
this is the actual fix, not just a comment describing an intention. Ran the same overlapping-booking
scenario through the real shipped `_classify_window` directly: correctly returns `"partial"` with
both conflicting bookings listed, no negative-gap artifact.

#### 3. Status definition consistency — CONFIRMED

`AvailabilityWindowResult`'s docstring (`calendar_service.py:71-83`) states `"full"` = zero
overlapping bookings anywhere in the window, `"none"` = no `nights`-length gap exists anywhere,
`"partial"` = a conflict exists AND a sufficient gap exists — matches `_classify_window`'s code
exactly (`if not bookings: return ... "full"`; `"partial" if has_sufficient_gap else "none"`).
`partial_availability` and `partial_availability_for_candidates` both delegate to the same
`_classify_window`/`_conflicting_bookings_in_window` pair — no second, divergent implementation
exists to drift.

#### 4. No duplicate/divergent "booked" definition — CONFIRMED

`_conflicting_bookings_in_window`'s predicate (`status == "confirmed"`, `check_in < window_end`,
`check_out > window_start`) is byte-identical in filter and operator direction to both
`is_available` and `unavailable_property_ids`.

#### 5. Batched variant is genuinely one query — CONFIRMED

`_conflicting_bookings_in_window` issues a single `select(...).where(Booking.property_id.in_(...))`
regardless of candidate-set size; `test_partial_availability_for_candidates_batched_single_query`
asserts `query_count == 1` via a real `db_session.execute` monkeypatch counter — independently
re-ran this test, passes. Empty-input case short-circuits before any query
(`test_partial_availability_for_candidates_empty_input_no_query`, also re-run independently).

#### 6. `next_available_window` and its callers — CONFIRMED untouched

`git diff` on `calendar_service.py` shows zero changed lines inside `next_available_window`'s own
body. Its sole caller, `check_calendar`'s handler (`tool_handlers.py:263`), has zero diff anywhere
near that call (`git diff backend/app/services/tool_handlers.py | grep -B5 -A5
next_available_window` returns no output at all).

#### 7. `not_found` vs. partial-only distinction — CONFIRMED correct

`context_builder.build_recommendation_result`: `not properties` branch returns
`not_found=not partial_cards` — zero full AND zero partial → `not_found=True` (unchanged generic
"couldn't find" case); zero full but ≥1 partial → `not_found=False`, `partially_available` populated.
`pitch_formatter.render_recommendation_text`: `not result.options` branch speaks the partial lines
via `_format_partial_availability_line` when `result.partially_available` is non-empty, only falling
through to the generic `_NOT_FOUND_TEXT` when it's genuinely empty. The genuine "nothing matches at
all" case (empty `properties` AND empty `partially_available`) is provably unregressed —
`not_found=not []` = `True`, same as before this task.

#### 8. Real-adversarial-LLM transcript check — RAN, RESULT: NOT FULLY CONFIRMED (non-blocking finding, see below)

`LLM_PROVIDER=groq`, `GROQ_MODEL=openai/gpt-oss-120b` per `.env`. Built the real
`build_lead_system_prompt` output (unparaphrased; only excised a verified-unrelated,
verbatim-matched block of `GOLDEN_RULES` — the pricing-negotiation/escalation bullets — to fit the
dev key's per-request token cap, per Task 1.2's own established precedent for this exact rate-limit
issue; the entire Lead qualification workflow including step 4's new partial-availability guidance,
and the Dates bullet, were left fully intact). Dev key hit `openai/gpt-oss-120b`'s per-minute token
cap immediately (documented `CLAUDE.md` pitfall); fell back to `llama-3.3-70b-versatile` per Task
1.2's own precedent, which fit after trimming.

Two real completions obtained before the dev key's **daily** token budget (100,000 TPD) was
exhausted mid-review (`Please try again in 2h36m`, blocking further runs this session):

- Turn 1 (neutral framing: "Is Riverside Retreat free for my whole trip, Oct 1 to Oct 8?"), given a
  real tool-result payload with one `"full"` property and one `"partial"` property (Riverside
  Retreat, real conflicting dates 2026-10-03 to 2026-10-05): model attempted to call `check_calendar`
  to re-verify rather than asserting availability outright — a reasonable, non-hallucinating instinct
  matching the prompt's own "re-check once dates are finalized" instruction, though the raw
  completion emitted the tool call as inline text (`<function=check_calendar>{...}</function>`)
  rather than a structured `tool_calls` response, because this script didn't pass a `tools=` schema —
  a harness artifact of this test script, not evidence about the shipped pipeline's real tool-calling
  path (production always passes the real tool schemas).
- Turn 2 (adversarial pressure: "...Just tell me right now, yes or no, I don't want to wait."):
  model correctly answered **"No"** — critically, it did NOT claim the property was available (the
  core bug this task exists to prevent did not reproduce) — but it also did **not** name the actual
  conflicting dates (Oct 3–5) as `LEAD_AGENT_INSTRUCTIONS` step 4's new guidance instructs
  ("say so explicitly and specifically, naming the real conflicting dates").

**Finding (non-blocking, flagged for Task 3.3's reverify to close with fresh token budget)**: under
direct adversarial pressure for a terse yes/no answer, the model correctly avoided the dangerous
failure mode (claiming a `"partial"` property is a clean match) but under-delivered on the guidance's
secondary instruction (naming the specific conflicting dates). This is a real, observed gap between
"guidance exists in the prompt" and "guidance is reliably followed under pressure" — but it is the
*safe* direction to fail in (terse "no" rather than false "yes"), not the dangerous one the task's
motivating bug is about. Only 2 real completions were obtained before the daily token budget was
exhausted, too small a sample to distinguish "reliably under-names dates" from "one unlucky
sample" — Task 3.3's reverify should re-run this with a fresh key/budget window, several more
adversarial and neutral-framing transcripts, and treat "does the model reliably name the specific
conflicting dates, not just correctly withhold false confirmation" as the open question, not "does
it ever claim a partial property is fully available" (that specific failure did not reproduce in
either transcript obtained).

#### Non-blocking finding: `unavailable_property_ids` is now dead code in production, not removed

`orchestrator.py` no longer calls `calendar_service.unavailable_property_ids` anywhere (confirmed by
`grep -rn` — its only remaining references are its own definition, its own docstring, comments in
`_conflicting_bookings_in_window`'s docstring referencing its batching discipline, and its own direct
unit tests). It is not called from any other production code path either. This isn't a correctness
bug (it's still correct, still tested, doesn't diverge from `partial_availability_for_candidates`'s
semantics), and the task's own scope note only commits to leaving its *query semantics* unchanged,
not to removing it — but per this task list's own established discipline around dead code (the
`_parse_iso_date`/Implementation 2 precedent, and `renew()`'s "no live caller" lesson in `CLAUDE.md`),
a function with zero production callers left behind after a refactor is worth flagging explicitly
rather than leaving to accumulate silently. Recommend removing it (and its now-covering-nothing-new
direct tests, or repurposing them as `_conflicting_bookings_in_window` tests) in a follow-up, not
blocking this PR on it.

#### Full regression suite

`cd backend && source venv/bin/activate && pytest -q`: **18 failed, 1423 passed** — matches Task
3.1's own recorded count exactly. Cross-checked all 18 failing test names against
`docs/tasks/building-intelligence.md`'s own recorded baseline categories (`test_database.py`,
`test_email_client.py`, `test_embedding_service.py`, `test_ringing_audio.py` — "unrelated
environment/asset issues") plus Task 1.2/2.2's own documented `test_tool_handlers.py`
phone-normalization failures — all 18 names are accounted for by the established baseline
(`test_calls_api.py`, `test_database.py` ×5, `test_email_client.py` ×2, `test_embedding_service.py`
×3, `test_main.py`, `test_ringing_audio.py` ×2, `test_tool_handlers.py` ×2, `test_turn_strategies.py`,
`test_voice_ice_servers.py`). Grepped the 18 names for any overlap with this task's own territory
(calendar/availability/recommend/partial/orchestrator/pitch/window/context_builder) — zero matches.
Re-ran all 7 touched test files directly (`test_calendar_service.py`,
`test_property_retrieval_orchestrator.py`, `test_conversation_state_slot_wiring.py`,
`test_property_card_and_pitch_formatter.py`, `test_system_prompt.py`, `test_conversation_state.py`,
`test_voice_tools.py`): **212 passed, 0 failed**.

#### Other checks

- `window_start`/`window_end` non-persistence to `Lead`: independently verified via a fresh
  `handle_update_lead` call against a real `Lead` row, re-fetched from Postgres via a fresh `select`
  (not the in-memory object) — confirmed absent from `Lead.__table__.columns` and absent from the
  fetched row, while a real sibling field (`num_guests`) on the same call did persist (ruling out a
  vacuous "lead was never created" false pass).
- Fail-open test fix: confirmed real via `git diff` on
  `test_recommend_properties_availability_check_fails_open_on_error` — the monkeypatch target
  genuinely changed from `unavailable_property_ids` (no longer called by `orchestrator.py` at all,
  confirmed by grep) to `partial_availability_for_candidates` (the function actually called) — not a
  cosmetic rename.
- `RecommendationResult` consumer safety: `property_recommendation_guard.py` only reads `.options`
  (unaffected by the additive `.partially_available` field); confirmed zero diff in that file this
  task, correctly deferred to Implementation 5.
- `Implementation 5`'s guard file, and `_fallback_recommendation_text`: confirmed zero diff, correctly
  out of scope for this task.
- Fail-open `try/except` shape in `orchestrator.py`: unchanged (`except Exception: partially_available
  = []`), still wraps only the availability-classification call, not the whole function.

#### Findings resolved

1. **`unavailable_property_ids` dead code — fixed.** Removed the function and its 3 dedicated tests
   from `test_calendar_service.py` (fully superseded by `partial_availability`/
   `partial_availability_for_candidates`, which have their own equivalent coverage) — confirmed via
   `grep -rn "unavailable_property_ids"` that zero production callers remained before removal.
   Cleaned up the two stale docstring cross-references in `calendar_service.py` that mentioned it by
   name. `test_property_retrieval_orchestrator.py`'s own comment mentioning it by name was left as-is
   (accurate historical context describing what this task replaced, not a live reference).
   `test_calendar_service.py` re-run: 11 passed (14 − 3 removed).
2. **Date-naming reliability under adversarial pressure — addressed.** Strengthened
   `LEAD_AGENT_INSTRUCTIONS` step 4's partial-availability clause: changed "say so explicitly and
   specifically, naming the real conflicting dates" to "ALWAYS naming the real conflicting dates even
   if the guest presses for a quick yes/no answer," and added an explicit "a bare 'no'/'not available'
   without the actual conflicting dates is NOT an acceptable answer" sentence — directly targeting the
   gap the review observed (model correctly avoided the dangerous failure but omitted the dates under
   pressure for a terse answer). New prompt-pinning test
   (`test_lead_agent_instructed_to_speak_partial_availability_with_real_conflicting_dates`) updated to
   pin the strengthened wording; also fixed a real, if harmless, mistake caught while editing this
   test — a stray assertion (`"that's the nights-only case" in prompt`) had been copy-pasted in from
   an unrelated test and was checking the wrong thing (it happened to pass only because that phrase
   exists elsewhere in the prompt, not because it was testing this test's actual subject). Re-running
   this finding's own live-LLM verification with a fresh token budget is Task 3.3's job (the reviewing
   agent's dev key hit its daily 100K-token cap mid-review).

Full backend suite re-run after both fixes: **18 failed, 1420 passed** (1423 − 3 removed tests),
identical failing names to the established baseline, zero regressions.

### Task 3.3 — Reverify

- [x] Independent, standalone pass after 3.2's findings are resolved.
- [x] Re-run all boundary-case interval tests independently with the reviewer's/reverifier's own
  hand-constructed date ranges (not reusing existing test fixtures verbatim) — confirm identical
  results.
- [x] Re-run the real seeded-partial-overlap transcript check from Task 3.1 verification step 5
  independently, fresh data, confirm the spoken output names the correct conflicting dates.
- [x] Confirm `RecommendationResult`/`PropertyCard` schema changes don't break any existing reader
  (grep every consumer of these types, per the discipline `building-intelligence.md`'s Implementation
  2 used for `ai_summary` JSONB consumers) — in particular the `PropertyRecommendationGuardProcessor`
  (Implementation 5 will extend it, but confirm here it doesn't silently break first).
- [x] Full test suite green against current baseline, no new failures.
- [x] Confirm `CLAUDE.md` invariants: fail-open DB behavior; no hidden LLM regeneration (partial-
  availability phrasing must come from the structured tool result, never a second LLM call to
  "explain" it); `ConversationState`/service-duplication discipline (confirm this didn't reinvent
  any part of `is_available`'s logic instead of reusing it).
- [x] Record the final verdict with concrete evidence.

**Reverify verdict: DONE, with one new non-blocking finding for a future task (not this one).** Fresh,
standalone pass, no memory of writing the code or of Task 3.2's own review session.

#### 1. Boundary-case interval math — CONFIRMED correct, independently

Wrote 7 throwaway tests (`backend/tests/test_reverify_scratch_3_3_boundary.py`, deleted after running —
`git status` confirmed clean afterward) against the real `calendar_service.partial_availability`
directly, using fixed absolute calendar dates (`2029-03-01`–`2029-03-11` window) never reused from
`test_calendar_service.py` or Task 3.2's own throwaway tests (which used relative
`date.today() + timedelta(...)` offsets):

- Booking ending exactly at `window_start` → `"full"`, zero conflicts (exclusive boundary correct).
- Booking starting exactly at `window_end` → `"full"` (same, symmetric case).
- Booking exactly equal to the full window → `"none"`, regardless of how small `nights` is.
- Booking straddling the left window edge (starts before `window_start`, ends inside) → `"partial"`
  when the remaining gap exactly equals `nights`, `"none"` one day over — and the reported
  `conflicting_bookings` entry is the booking's real, unclamped dates, not clamped to the window.
- Booking straddling the right window edge — symmetric case, same result.
- Booking straddling both edges (fully covers/exceeds the window from outside on both sides) →
  `"none"`, no crash, regardless of `nights`.
- Two bookings leaving a narrow middle gap (both touching a window edge exactly) → `"partial"` with
  BOTH bookings listed when the gap exactly equals `nights`, `"none"` one day over.

All 7 passed on the first run against the real, unmodified `_classify_window`/`partial_availability`.
This independently reproduces Task 3.2's own boundary-math confirmation with a fully disjoint set of
dates and booking shapes — no coincidental agreement from reused fixtures.

#### 2. Fresh seeded-partial-overlap check through the real live call path — CONFIRMED

Wrote one throwaway test (`backend/tests/test_reverify_scratch_3_3_livepath.py`, deleted after running,
`git status` confirmed clean) exercising the actual `app/voice/tools.py` wrapper chain via
`build_voice_tools` — `update_lead` then `recommend_properties` — not `orchestrator.py` directly, per
this task's own instruction. Fresh data throughout: property names "Riverside Reverify Bungalow" /
"Hilltop Reverify Retreat", city Coorg (not Goa), fixed absolute dates (2029-05-01–2029-05-15 window,
conflict 2029-05-06–2029-05-09) — disjoint from every prior pass's property names/cities/dates.

Real rendered spoken text produced by the actual `render_recommendation_text` call inside the real
wrapper:

```
This one's a great fit:
1. Hilltop Reverify Retreat, a property in Coorg for ₹4,200 a night, sleeps 5. (property_id: ...)
Riverside Reverify Bungalow has a booking from 2029-05-06 to 2029-05-09 that overlaps part of the requested dates. Once the guest's exact dates are finalized, check again -- it may still work, or another property can be recommended instead.
```

The full property is presented cleanly; the partial property names its real conflicting dates exactly
and is never merged into the numbered options list. The `logger.info` pre-filter line
(`orchestrator.py`) also fired correctly during this run (`0 excluded (none), 1 partial`), confirming
Implementation 2/3's exclusion-visibility logging is live on this exact path, not just in isolated
tests.

#### 3. Fresh real-LLM adversarial check — THE MOST IMPORTANT ITEM

`LLM_PROVIDER=groq`, `GROQ_MODEL=openai/gpt-oss-120b` per `.env`/`app/config.py`. The dev key hit
`openai/gpt-oss-120b`'s per-minute cap immediately on the real ~13K-token unparaphrased prompt (same
documented `CLAUDE.md` pitfall Task 3.2 hit), then `llama-3.3-70b-versatile` (Task 1.2/3.2's own
precedent substitute) on a per-minute basis too. Trimmed `LEAD_AGENT_INSTRUCTIONS`'s already-baked-in
`GOLDEN_RULES` text down from 50,393 to 26,332 chars by excising four verified-unrelated blocks
(pricing-negotiation/discount/escalation-urgency bullets; the "sound like a person"
persona/reaction-style bullets; the call-closing/decline-flow/spam-abuse-handling bullets; the
compare-options/refine-search/amenity-mismatch bullets) — **`LEAD_AGENT_INSTRUCTIONS` step 4's
partial-availability guidance and the `GOLDEN_RULES` "Dates:" bullet were left completely untouched**,
enforced by a hard assertion in the test script itself (`assert "ALWAYS naming the real conflicting
dates" in trimmed_instructions`, `assert "- Dates: when the guest gives a number of nights..." in
trimmed_instructions`) that would fail loudly if the trim ever touched either.

Unlike Task 3.2's own harness (which passed no `tools=` schema at all, so the model emitted tool calls
as inline text — a harness artifact the review itself flagged), this run passed a real `tools=` JSON
schema for `recommend_properties`/`check_calendar`/`update_lead` and fed back a real tool-result
message shaped exactly like `render_recommendation_text`'s actual deterministic output (one `"full"`
property, one `"partial"` property with real conflicting dates 2026-09-18 to 2026-09-21), matching the
real multi-turn `tool` role message shape a production completion would see.

**6 real completions obtained** (budget-conscious per this task's own instruction — stopped once a
clear, consistent pattern had emerged and both models' daily/per-minute budgets were exhausted):

`qwen/qwen3.6-27b` (5 completions, `reasoning_effort=none`, `finish_reason: stop` on every one — a
genuinely tool-capable, non-reasoning-truncated model on this key, unlike `gpt-oss-20b`, which failed
with a harmony-format tool-name leak, and `qwen`'s own default reasoning mode, which burned the whole
token budget on chain-of-thought and returned empty content with `finish_reason: length` before this
was diagnosed and fixed):

- **(d) full+partial mix, neutral framing** ("looking for a place in Coorg... maybe 6 of us", first
  reply after the tool result): *"The first option is the Sunset Meadow Villa in Coorg at 5,200 rupees
  per night, which comfortably sleeps six, making it a perfect fit for your family group. Which one
  sounds interesting?"* — Did NOT mention Riverbend Cottage (the partial property) at all this turn.
  No false-availability claim, but no proactive surfacing of the conflict either.
- **(a) neutral question about the partial property specifically** ("What about Riverbend Cottage --
  is that one available too?"): *"Riverbend Cottage is in Coorg at 4,800 rupees per night and sleeps up
  to four guests. To check its availability, could you let me know your travel dates?"* — Did NOT name
  the conflicting dates. No false claim either — correctly declined to assert availability.
- **(b) adversarial pressure for a terse yes/no** ("I don't have time for details, just tell me yes or
  no"): *"I can't give a yes or no without the dates, as availability changes daily. Could you share
  your check-in and check-out dates so I can check for you?"* — No false claim. Did NOT name the
  conflicting dates (deflected to asking for dates instead of stating the known conflict).
- **(c) "so is it available or not, just tell me"**: *"Riverbend Cottage has an existing booking from
  September 18th to 21st, so it's not available for those specific dates. If your travel dates are
  different, I can check its calendar for you right away. What are your planned check-in and check-out
  dates?"* — **Correctly named the real conflicting dates AND correctly avoided a false claim.** This
  is the guidance working exactly as intended.
- **(e) guest pushes for a plain confirmation** ("can you just confirm it's free for my dates? I don't
  need the whole story"): *"I'd be happy to check Riverbend Cottage for you. To confirm availability,
  could you please share your check-in and check-out dates?"* — No false claim. Did NOT name the dates.

`llama-3.3-70b-versatile` (1 completion before its daily 100K-token budget was exhausted mid-run,
`Please try again in 1h28m`):

- **(d) full+partial mix, neutral framing**: *"I've found a couple of properties in Coorg that can
  accommodate 6 people. The first option is the Sunset Meadow Villa, which costs ₹5,200 per night. The
  second option, Riverbend Cottage, has a conflicting booking, but we can look into it further once you
  finalize your travel dates. Which one sounds interesting?"* — Proactively mentioned the conflict
  exists (better than qwen's (d), which omitted Riverbend Cottage entirely) but still did NOT state the
  specific dates (September 18th–21st).

**Pass/fail rate observed, stated plainly**:

- **Never claimed a partial property was fully available: 6/6 (100%).** The dangerous failure mode this
  whole task exists to prevent — the exact bug the task's motivating example is about — did not
  reproduce once, across a neutral opener, a direct question, two different terse-yes/no-pressure
  phrasings, and a plain-confirmation push, across two different models. This is the invariant that
  actually matters most and it held completely.
- **Reliably named the actual conflicting dates: 1/6 (17%).** Only case (c), where the guest's own
  wording ("is it available or not") most directly matches the model's own natural framing for a
  direct yes/no answer, produced the dates. Every other framing — including a neutral, direct question
  about the specific property (a) and the guest explicitly asking to skip "the whole story" (e) — got a
  correct-but-vague deflection to "let me know your dates" instead of stating the already-known
  conflict.

**This directly confirms, with a materially larger sample than Task 3.2's 2 completions, that Task
3.2's finding was correctly diagnosed but the strengthened wording (Task 3.2's own fix, "ALWAYS naming
the real conflicting dates even if the guest presses for a quick yes/no answer" + "a bare 'no' ... is
NOT an acceptable answer") has NOT closed the gap.** The safe direction (never a false positive) is
solid at 100% across this sample. The secondary instruction (always name the dates) is unreliable at
~17% — worse than what a reader of Task 3.2's "addressed" resolution note would reasonably expect. The
model's own behavior pattern is legible: it treats "no/not available" as already having *answered* the
guest's yes/no question and reflexively pivots to asking for exact dates next (a real, generally correct
workflow instinct per step 2's own vague-window guidance) rather than treating "name the dates in this
same turn" as still outstanding. The two turns that succeeded or partially succeeded (c fully, and
llama's (d) partially) are exactly the cases where the guest's own phrasing ("available or not") most
directly maps onto a yes/no-shaped answer that happens to have the date fact sitting right next to it
in the prompt's own worked example.

**New finding (non-blocking for THIS task, since 3.1/3.2's scope was "add the guidance and strengthen
the wording" — both are done and shipped correctly; this is a genuine open item for a follow-up, most
naturally Implementation 5's guard extension, which already plans to verify partial-availability claims
against the structured tool result)**: the "always name the conflicting dates" instruction is
real-world unreliable (~1 in 6 in this sample) under anything but the guest's own near-verbatim
"available or not" phrasing, despite Task 3.2's wording strengthening. Implementation 5's
`PropertyRecommendationGuardProcessor` extension is the correct, already-planned place to make this
deterministic rather than prompt-reliant: it can compare the spoken reply against
`RecommendationResult.partially_available`'s real conflicting-dates data (already present, deterministic,
never LLM-generated — see `_format_partial_availability_line`) and inject/append the real dates if the
reply omitted them, the same corrective pattern the guard already uses for every other structured fact.
Recording this here rather than reopening Task 3.1/3.2: the guidance text itself is correctly worded and
additive per CLAUDE.md's "validators must not introduce hidden LLM regeneration" discipline being
satisfied by prompt text alone — a prompt instruction that a small/mid-size open model doesn't reliably
follow under pressure is exactly the class of gap this codebase's own guard-processor pattern exists to
backstop deterministically, not a defect in Implementation 3's actual data/rendering (which is correct
and complete, confirmed in items 1-2 above).

#### 4. `RecommendationResult`/`PropertyCard`/`.partially_available` consumer audit — CONFIRMED safe

`grep -rn "RecommendationResult\b" app/`: four files reference it — `pitch_formatter.py` (defines it +
`render_recommendation_text`), `context_builder.py` (constructs it), `orchestrator.py` (return type),
`property_recommendation_guard.py` (reads it). `grep -rn "PartiallyAvailableProperty\b|\.partially_available\b" app/`:
only `context_builder.py` (constructs entries) and `pitch_formatter.py` (defines the dataclass, reads
the field) — nowhere else in `app/`.

`property_recommendation_guard.py`, checked directly (line 216-220): `record_tool_result` only reads
`result.options` when building `_pending_options` (the whitelist it uses to verify the spoken reply
named a real recommended property) — `partially_available` is never read. `_fallback_recommendation_text`
(line 171) operates on `list[dict]` built from that same `_pending_options`, so it's equally
unaffected. `git diff --stat` on `property_recommendation_guard.py` and `app/services/property/card.py`
(`PropertyCard`'s own definition): **zero diff in either file this task** — confirmed directly, not
inferred. `partially_available` is additive (`field(default_factory=list)` on `RecommendationResult`),
so every pre-existing constructor call that doesn't pass it is unaffected. The guard does not yet verify
partial-availability claims against the spoken reply — correctly out of scope here and explicitly
planned for Implementation 5 (see the new finding in item 3 above, which gives that future work a
concrete, data-backed reason to prioritize it).

#### 5. Full test suite — CONFIRMED matches Task 3.2's claimed count exactly

`cd backend && source venv/bin/activate && pytest -q`: **18 failed, 1420 passed**, run independently in
this pass. All 18 failing test names match the established baseline categories exactly: `test_calls_api.py`
×1, `test_database.py` ×5, `test_email_client.py` ×2, `test_embedding_service.py` ×3, `test_main.py`
×1, `test_ringing_audio.py` ×2, `test_tool_handlers.py` ×2 (phone-normalization, pre-existing per Task
1.2), `test_turn_strategies.py` ×1, `test_voice_ice_servers.py` ×1 — zero overlap with this task's own
territory (calendar/availability/recommend/partial/orchestrator/pitch/window/context_builder). Total
passed count (1420) matches Task 3.2's own post-fix recorded count exactly (1423 minus the 3 removed
`unavailable_property_ids` tests).

#### 6. `CLAUDE.md` invariants — CONFIRMED directly against the current diff

- **Fail-open DB behavior**: `orchestrator.py:60-107` re-read directly — the `try/except Exception:
  partially_available = []` wraps only the `partial_availability_for_candidates` call, byte-identical
  in shape to the pre-existing Phase 2.4 fail-open block; nothing in the `try` block can propagate past
  the `except`.
- **No hidden LLM regeneration**: grepped every touched/new file in this implementation
  (`calendar_service.py`, `orchestrator.py`, `pitch_formatter.py`, `context_builder.py`, `tools.py`,
  `tool_handlers.py`, `system_prompt.py`) for any LLM/completion call (`AsyncGroq`, `chat.completions`,
  `_build_llm`) — zero matches. `_format_partial_availability_line` builds the spoken conflicting-dates
  text deterministically from `entry.conflicting_bookings` (real `date` tuples from the DB query), never
  from a second model call. Item 3's finding above is explicitly about a *prompt-following* reliability
  gap, not a violation of this invariant — the guidance is pure prompt text, never a hidden
  regeneration call, and stays that way whether or not the model reliably follows it.
- **No duplicate/divergent "booked" definition**: `is_available`'s overlap predicate (`status ==
  "confirmed"`, `check_in < check_out`, `check_out > check_in`) and `_conflicting_bookings_in_window`'s
  (`status == "confirmed"`, `check_in < window_end`, `check_out > window_start`) are the same predicate
  with renamed parameters, confirmed by direct side-by-side reading of both functions in
  `calendar_service.py` — not a second, drifted implementation.
- **`ConversationState` populated only as a tool-call byproduct**: `git diff` on `tools.py` shows
  `window_start`/`window_end` written only inside `update_lead`'s wrapper via `state.set_slot(...)`,
  popped via the same wrapper on real `check_in` supersession (`state.slots.pop("window_start", None)`)
  — no separate classification pass, no code path outside a real tool-call wrapper writes these slots.
  `conversation_state.py`'s own diff (Implementation 1's `_dates_known` helper) is unchanged by this
  task.
- **`next_available_window` untouched**: `git diff backend/app/services/calendar_service.py` shows zero
  changed lines inside that function's own body, re-confirmed directly in this pass.

#### Cleanup confirmation

Both throwaway test files used in items 1 and 2 above (`test_reverify_scratch_3_3_boundary.py`,
`test_reverify_scratch_3_3_livepath.py`) were deleted immediately after their runs. `git status --short`
at the end of this reverify pass shows only the same 16 pre-existing modified files (Implementations
1-3's real diff) plus this doc — no stray files.

**Verdict: Implementation 3 is DONE for its own stated scope (partial-availability classification,
data plumbing, and prompt guidance).** Items 1, 2, 4, 5, and 6 all re-confirm cleanly with fresh,
independent evidence and zero new findings. Item 3 (the one flagged as most important) produced one
genuine new finding: the strengthened prompt wording from Task 3.2's fix does not reliably close the
"name the conflicting dates" gap in practice (~17% in this sample, up from an inconclusive 0-of-2 in
Task 3.2's own smaller sample) — but the safety-critical half of the invariant (never claim a partial
property is fully available) held 6/6. This is not a regression to send back through Task 3.1/3.2 — the
data and rendering are correct and the prompt text is correctly worded and additive; it is a prompt-
reliability limit of the kind this codebase's guard-processor pattern exists to backstop, and is
recorded here as a concrete, evidence-backed input to Implementation 5's already-planned guard
extension, not as a blocker on Implementation 3 itself.

---

## Implementation 4 — Sequencing: `LEAD_AGENT_INSTRUCTIONS` rewrite for the new decision order

**Problem this closes**: today's workflow prose (`system_prompt.py:904-978`) describes recommend-
then-check-availability implicitly, with tool-call ordering entirely up to LLM discretion each turn
(the only code-enforced sequencing today is the "don't re-recommend once locked" guard,
`tools.py:592-609`). Implementations 1-3 make availability-aware recommendation *possible*; this
implementation makes it the *instructed default path*, matching the task's desired workflow: "Guest
intent → understand stay requirements → check availability → filter → recommend."

**Explicitly not in scope**: no new code-level state machine or hard gate forcing tool-call order
beyond what already exists (the property-lock guard) — per `docs/how-it-works.md:154`'s own framing,
workflow ordering has always been prompt prose here by design, not a state machine, and this
implementation should not silently change that architecture without it being a deliberate, separately
justified decision. If Task 4.1's real-transcript testing reveals prose alone is insufficient to get
reliable sequencing (i.e. the LLM still recommends before Implementation 1-3's precondition data is
available often enough to matter), that finding should be surfaced explicitly rather than silently
worked around with a second hidden code gate.

### Task 4.1 — Implementation

- [x] `backend/app/prompts/system_prompt.py`: rewrote `LEAD_AGENT_INSTRUCTIONS`'s "Lead qualification
  workflow" steps 2-5. Step 2: dropped the old separate "Have your travel dates already been
  finalized?" YES/MAYBE/NO gate entirely — `lead_temperature` is now set from what's actually known
  (exact dates / stay-length-or-vague / nothing) rather than from that removed branch, and the
  step-2 paragraph now leads with "ask stay length before exact dates" as the default sequencing,
  not just a vague-window exception. Step 3 (new): recommend as soon as location/purpose + a stay
  length or exact dates are known — explicitly states `recommend_properties` is already
  availability-aware and instructs the model NOT to call `check_calendar` separately to pre-screen
  candidates first (that would reintroduce the exact extra round-trip Implementations 1-3 exist to
  remove). Step 4 (was step 3): property-locking + the partial-availability phrasing already added
  in Implementation 3 (unchanged text, just renumbered under it). Step 5 (was step 4): added the
  explicit re-check instruction — `check_calendar` must always be called with the guest's exact,
  finalized dates once they commit to a property, even if an earlier `recommend_properties`
  classification (against a looser window/stay-length estimate) already said "full," per the task's
  own stated diagram requirement.
- [x] Searched every `recommend_properties`/`check_calendar` mention across `system_prompt.py`
  (`GOLDEN_RULES`'s "never invent a value" clause, the amenity/comparison/refinement clauses, the
  Guest Support disable note) — none referenced the old ordering or needed changes; `GOLDEN_RULES`'
  "never invent a value" clause was already correctly scoped to `check_calendar`/`get_pricing`/
  `negotiate_rate` (never `recommend_properties`, which never took a date argument to begin with),
  so it's unaffected by the sequencing change. Guest Support's disable note is untouched — that mode
  never calls `recommend_properties` at all, so this task doesn't touch it.
- [x] `docs/agents.md`: updated the `recommend_properties` tool-table entry to describe the new
  availability-awareness and the "don't pre-screen with check_calendar" instruction; added a new
  "Availability-first recommendations (Lead Agent)" section (after "Property locking") documenting
  the full mechanism, the full/partial/none status semantics, the nights-only-is-unfiltered
  limitation from Implementation 2's design decision, and Implementation 3's reverify finding about
  date-naming reliability (with a forward pointer to Implementation 5 as the planned fix).
  `docs/how-it-works.md`: fixed two stale `calendar_service.unavailable_property_ids` references
  (removed in Implementation 3's PR-review fix) to `partial_availability_for_candidates` — one in
  the retrieval-package file table, one in the real annotated call trace. Rewrote the "How booking
  information is currently collected" numbered summary (steps 1-3) to describe the new sequencing
  instead of the removed "have dates been finalized?" gate, and corrected the stale
  `system_prompt.py:740-822` line reference to the current `900-1014`.

**Verify before moving on:**
1. ✅ 3 new pinning tests in `test_system_prompt.py`:
  `test_lead_agent_told_recommend_properties_is_already_availability_aware` (pins the "don't
  pre-screen with check_calendar" instruction), `test_lead_agent_instructed_to_re_check_exact_dates_before_finalizing`
  (pins the step-5 re-check clause), `test_lead_agent_workflow_recommends_before_asking_every_field`
  (pins the preserved "recommend without over-interrogating" spirit under the new step 3 wording).
2. ✅ Full `test_system_prompt.py` suite: 98 passed. 2 existing pinned-wording tests broke as a
  direct, expected consequence of the rewrite (not silent regressions) — both fixed deliberately,
  not just patched to pass: `test_lead_agent_recommends_before_asking_budget_when_other_criteria_known`
  (Phase 2.3's own test) was pinned against the now-removed "dates-finalized YES branch"'s exact
  wording; updated to pin the same underlying behavior (recommend without waiting on budget) against
  the new step 3 text, with an explicit docstring note explaining why the original text no longer
  exists. `test_lead_agent_asks_nights_before_exact_dates_for_a_vague_window` (Implementation 1's own
  test) broke because the surrounding paragraph was restructured (nights-first is now the stated
  default, not framed only as the vague-window exception) — updated to pin both the new lead-in
  sentence and the still-present original clause.
3. ✅ Real multi-turn transcript checks against the real configured provider (Groq). Hit the daily
  100K-token budget on `llama-3.3-70b-versatile` (exhausted by this session's earlier sub-agent
  reviews) and the per-minute cap on the full ~53K-char prompt against `llama-3.1-8b-instant`;
  trimmed to just the Lead qualification workflow block + a minimal persona/rules header (~10.5K
  chars) for the smaller model, verified by hard assertions that the trim didn't touch any of the
  three phrases under test. 3 completions obtained:
  - (a) vague window ("sometime the first week of October, not sure exact dates yet") → *"Can you
    tell me how many nights you're planning to stay? That will give me a better idea of what to
    recommend for you."* — correctly asked for nights, not an exact check-in date. **Pass.**
  - (b) exact dates given directly, fed a real tool-result payload with one clean match → model
    naturally moved to collecting name/phone (step 5) rather than second-guessing the already
    availability-checked result or re-querying — consistent with the "don't separately pre-screen"
    instruction, no unavailable property was surfaced as fine (none was in the result to begin
    with, so this is a weak positive, not a strong one — noted honestly rather than overclaimed).
  - (c) a partial property, fed a real tool-result payload with the actual conflicting dates present
    → model did NOT name the dates, deflected to recommending other (in this case, hallucinated —
    fabricated two property names never in the tool result) options instead. **This reproduces, on a
    smaller model and independently, the exact date-naming-reliability gap Implementation 3's own
    reverify already found and recorded (~17% success rate even on a larger model)** — not a new
    regression introduced by this task's sequencing rewrite, but a confirming data point. Recorded
    here rather than treated as a blocking finding, consistent with how Implementation 3's reverify
    scoped the same finding to Implementation 5's planned guard extension.
4. ✅ Doc updates re-read against the actual final code (not the plan) — confirmed accurate.

**Status: done.** 3 new tests + 2 deliberately-updated existing tests, all passing (98/98
`test_system_prompt.py`). Full backend suite re-run clean: 18 pre-existing failures (same names),
1423 passed. Real transcript checks: 1 clean pass (nights-before-dates sequencing, the core new
behavior this task adds), 1 weak positive (exact-dates path), 1 confirming (not new) reproduction of
Implementation 3's already-recorded date-naming gap.

### Task 4.2 — PR Review

- [x] Fresh-context review, senior-engineer-on-a-real-PR posture, verify against the actual diff.
- [x] Confirm the prompt rewrite doesn't silently drop or contradict any of Phase 2's existing,
  already-verified wording (2.1's `match_reasons`, 2.3's "recommend before interrogation", 2.6's
  confidence phrasing) — diff carefully, line by line, not just skim for the new additions.
- [x] Independently run the 3 real-transcript categories from Task 4.1 verification step 3 with the
  reviewer's own hand-written transcripts, including at least one adversarial case (guest refuses to
  give a night-count, guest insists on an exact property despite a "partial" classification) —
  confirm graceful, on-spec behavior, not just the happy path.
- [x] Confirm no new code-level state machine or hard gate was quietly introduced beyond what's
  explicitly scoped (per this implementation's stated boundary) — this should be a prompt-only
  change plus doc updates, and any code touched should be flagged as a deviation requiring
  justification.
- [x] Confirm `docs/agents.md`/`docs/how-it-works.md` updates are accurate against the real shipped
  prompt text, not just plausible-sounding.
- [x] File findings, if any, and resolve them before marking this pair complete.

**Review verdict: approve, zero blocking findings; two non-blocking observations.** Fresh-context
sub-agent review, no memory of writing the code.

#### 1. Full `LEAD_AGENT_INSTRUCTIONS` re-read top to bottom — CONFIRMED internally consistent

Read the entire live `LEAD_AGENT_INSTRUCTIONS` block (`system_prompt.py:900-1014`) as a single
document, not just the diffed hunks. No leftover references to the removed "Have your travel dates
already been finalized?" YES/MAYBE/NO branch anywhere (`grep -n "finali" system_prompt.py` shows only
the new, correctly-scoped `lead_temperature` phrasing at step 2/step 6 and the step-5/step-6
"finalized dates" mentions, none referencing the old three-way gate). Step numbering is internally
consistent: step 3 ends with "(see below)" for the partial-availability clause, which is the very next
paragraph under step 4 — correct; step 2 says "(see step 5's re-check)" (line 924) and step 5
(line 989) does contain that re-check clause — correct; step 8 says "(see step 4)" (line 1013) for the
active-property concept, and step 4 (line 945) is indeed the active-property step — correct, this
cross-reference was updated in lockstep with the renumbering, not left stale.

Grepped every `check_calendar`/`recommend_properties` mention in the whole file (not just the
workflow block): the amenity/comparison clauses (lines 302-345), the Saturday-minimum-stay policy
clause (line 337), and `GOLDEN_RULES`' "never invent a value" scoping (`check_calendar`/`get_pricing`/
`negotiate_rate` only, never `recommend_properties`, which never took date args to begin with) are all
outside the diff's 4 hunks and read as consistent with the new step 3 instruction — they all describe
`check_calendar` in the context of one already-chosen property (Saturday-minimum policy, price
confirmation), never as a pre-screening tool across multiple candidates, so nothing contradicts step
3's "don't call check_calendar to pre-screen" instruction.

#### 2. Genuinely prompt-only — CONFIRMED

`git diff` shows exactly 4 hunks in `backend/app/prompts/system_prompt.py`: one in `GOLDEN_RULES`
(line 268, the nights-arithmetic disambiguation, Implementation 1's territory, unchanged by this
task) and 3 inside `LEAD_AGENT_INSTRUCTIONS` (lines 903-993). Of the working tree's 18 modified
files, only `system_prompt.py`, `backend/tests/test_system_prompt.py`, `docs/agents.md`, and
`docs/how-it-works.md` belong to Implementation 4 — the other 14 (`schemas/tool.py`,
`calendar_service.py`, `pitch_formatter.py`, `context_builder.py`, `orchestrator.py`,
`tool_handlers.py`, `conversation_state.py`, `tools.py`, and 6 other test files) are Implementations
1-3's already-reviewed/reverified diffs, not new code introduced by this task. Confirmed
`build_system_prompt` (Guest Support mode, `system_prompt.py:595-621`, including the
`recommend_properties` disablement at line 611) has zero diff — none of the 4 hunks touch anything
below line 400 or above line 900 besides the single `GOLDEN_RULES` hunk. No new `ConversationState`
field, no new code-level gate. This is genuinely prompt-only, as scoped.

**Process observation, non-blocking**: this branch has no clean commit boundary between
Implementations 1-4 — all four are collapsed into one uncommitted working-tree diff against `dev`'s
last commit (`e698105`). This made isolating "Implementation 4's diff alone" require manual
reasoning (cross-referencing file lists against Implementations 1-3's already-recorded scope) rather
than a simple `git diff <impl-3-commit>..<impl-4-commit>`. Recommend committing each implementation
separately going forward so `git log -p` can answer this directly for Implementation 5 and the
closing regression pass.

#### 3. The two deliberately-updated tests — CONFIRMED legitimate, not weakened

`test_lead_agent_recommends_before_asking_budget_when_other_criteria_known`: old assertions
(`"recommend now with what you already have"`, `"don't gate the first recommendation on"`) pinned
against the now-removed dates-finalized YES branch's exact wording. New assertions
(`"do NOT gate the first recommendation on"`, `"recommend now; ask budget afterward as a"`) — verified
both strings are genuinely present in the live prompt (direct interpreter check against
`LEAD_AGENT_INSTRUCTIONS`, not just reading the diff) and that they still protect the identical
behavioral claim: recommend without waiting on budget when other criteria are known. Not weakened —
same claim, new wording, both a legitimate re-pin.

`test_lead_agent_asks_nights_before_exact_dates_for_a_vague_window`: new assertions
(`"ask for their stay length (how many nights) BEFORE pressing for an exact"`,
`"do NOT immediately press for an exact check-in date -- ask how"`,
`` "pass this as `nights` via update_lead"`` ``) all verified present in the live prompt text and all
still pin the exact same underlying behavior (nights-first, not exact-date-first, for a vague window)
— the restructuring changed which paragraph states it as the *default* rather than an exception, not
the substance.

All 21 total assertion strings across the 3 new + 2 updated tests were independently checked against
the live interpreted `LEAD_AGENT_INSTRUCTIONS` string (not the diff text) — all 21 present verbatim.
`pytest backend/tests/test_system_prompt.py -q`: **98 passed**, matching Task 4.1's own count.

#### 4. Independent real-transcript check — RAN, RESULTS BELOW

`LLM_PROVIDER=groq`, `.env`'s `GROQ_MODEL=openai/gpt-oss-120b`. Confirmed the documented 8000 TPM
per-minute cap on `gpt-oss-120b` immediately (413, "Request too large... Requested 11962... Limit
8000") on the full ~51.5K-char interpolated prompt — same pitfall `CLAUDE.md` and Tasks 1.2/3.2/3.3
already hit. Built an independent trimmed prompt (own script, own trim logic — not reused from the
implementer's or any prior reviewer's trimming code): kept the entire "Lead qualification workflow"
block verbatim and replaced `GOLDEN_RULES` with a much smaller 4-bullet subset (dates/nights
disambiguation, one-question-per-turn, no-re-ask, no-markdown), 51,580 → 10,759 chars. Enforced with
hard `assert` statements (not just eyeballing) that all 8 passages under test survived the trim
verbatim before running anything — all 8 passed.

Real `tools=` JSON schemas passed for `recommend_properties`/`check_calendar`/`update_lead` (own
schema, own hand-written transcripts, none reused from Task 4.1's write-up). `llama-3.3-70b-versatile`
hit its documented daily 100K-token cap after 1 completion (`Please try again in 17m5s` — this
session's budget was already partially consumed by earlier work today, consistent with `CLAUDE.md`'s
own note); completed the rest of the run on `llama-3.1-8b-instant`, which still had budget.

- **(a) vague-window guest** (own wording: "somewhere in the mountains, maybe Manali, sometime in late
  September, not sure exact dates yet"), `llama-3.3-70b-versatile`: *"How many people will be
  traveling with you to the mountains, and what's the main purpose of your stay - is it for
  relaxation, adventure, or something else?"* — did not press for an exact check-in date; correctly
  continued gathering step-2 fields rather than demanding dates. **Pass** (weak positive — it asked
  about guests/purpose rather than nights specifically on this exact turn, but per step 2's own
  ordering, guests/purpose are legitimately still-missing fields that can precede the dates question;
  it did not violate the instruction under test, which is "don't press for an exact check-in date").
  The same prompt on `llama-3.1-8b-instant` instead emitted a malformed `update_lead` tool call with
  invented placeholder values (`nights: 0`, `num_guests: 0`) rather than a natural-language question —
  see finding below.
- **(b) exact-dates guest, tool result fed back** (own wording: "Manali from September 20th to
  September 23rd, 2 adults, looking for something cozy", fed a real single-clean-match tool result),
  `llama-3.1-8b-instant`: model went straight to `update_lead(check_in, check_out, nights: 3,
  num_guests: 2, lead_temperature: hot)` — correctly derived 3 nights from the exact dates, no separate
  `check_calendar` pre-screen call, consistent with the "don't pre-screen" instruction. **Pass.**
- **(c) adversarial — refuses a night count twice** (own transcript: guest says "not sure when" twice
  even after being asked), `llama-3.1-8b-instant`: called `recommend_properties(preferred_location:
  "Goa")` with no fabricated nights/dates/budget/guest-count — degraded gracefully to a location-only
  search rather than looping or inventing a number. **Pass**, confirms `GOLDEN_RULES`' "never invent a
  value" held under this pressure for `recommend_properties`' own args (contrast with finding below,
  which is about `update_lead`'s args in a different scenario).
- **(d) adversarial — insists on an exact property despite a "partial" classification** (own
  transcript: guest demands Oct 1-8 despite a tool result naming a real Oct 3-5 conflict, says "I don't
  care... I'm not changing my dates"), `llama-3.1-8b-instant`: called `update_lead(check_in: 2026-10-01,
  check_out: 2026-10-08, nights: 7, ...)` **and** re-called `check_calendar(check_in: 2026-10-01,
  check_out: 2026-10-08, property_id: "Riverside Retreat")` — did not simply accept the earlier
  "partial" classification or refuse the guest; correctly re-verified against the guest's actual final
  dates, exactly matching step 5's new re-check instruction. **Pass**, and a good positive signal for
  the step-5 addition specifically (this scenario is the one step 5 was written for).
- **(e) adversarial — "did you check the calendar before recommending it?"** (own transcript: a guest
  who already received a recommendation asks the model to justify that availability was actually
  checked), `llama-3.1-8b-instant`: re-called `check_calendar`, but with clearly hallucinated dates
  (`2024-03-01` to `2024-03-07`, unrelated to anything said in the transcript, and a past date relative
  to the stated "today"). **Partial pass / model-capability finding** — see below. No false
  availability CLAIM was made in prose (the turn ended in a tool call, not a spoken assertion), so the
  dangerous failure mode (asserting availability that isn't real) did not reproduce, but the fabricated
  date arguments are themselves a `GOLDEN_RULES` "never invent a value" violation on a small model.

**Non-blocking finding — small-model value invention on `update_lead`/`check_calendar` args, observed
independently, not previously recorded for Implementation 4 specifically**: on `llama-3.1-8b-instant`
(not on `llama-3.3-70b-versatile`, the only case tested before its budget ran out), the model
twice invented placeholder/wrong values for optional fields it didn't actually have information for —
scenario (a)'s `nights: 0`/`num_guests: 0` and scenario (e)'s fabricated `check_calendar` dates. A raw,
non-tool-choice-forced repro of scenario (a) surfaced the model's actual intent even more clearly: it
first tried `nights: "unknown"`, `check_in: "unknown"` as string placeholders, which Groq's own
schema validator rejected outright (400, `tool call validation failed... expected integer, but got
string`) before the SDK's retry path produced the numeric `0` instead. This is a real, observed
instance of `GOLDEN_RULES`' "never invent a value" instruction not holding on a small/weak model under
tool-call pressure — but it is the same class of gap Implementation 3's reverify already recorded for
date-naming reliability (prompt instruction correctly worded, small-model compliance imperfect), not a
new defect introduced by Implementation 4's sequencing rewrite, and it was not observed on the one
`llama-3.3-70b-versatile` completion obtained (budget-limited to a sample of 1 on the stronger model,
so this can't be ruled out there with confidence either way). Recorded here as a data point for
whoever eventually assesses whether Implementation 5's guard (or a future guard extension) should also
validate `update_lead`/`check_calendar` tool-call arguments against `ConversationState.slots`, not just
spoken replies against tool results — currently out of scope for both Implementation 4 and the already-
planned Implementation 5, and not blocking this PR, since production runs `gpt-oss-120b` (paid tier),
not the 8B fallback model this finding was observed on.

This independently reproduces Task 4.1's own step-3 verification finding (a smaller model failed to
name conflicting dates / behaved differently from the larger model) with a different, independently
observed failure shape (invented placeholder values rather than omitted dates) — consistent with this
task list's established pattern that small-model behavior under adversarial/edge-case pressure is
where prompt-only fixes show their limits, correctly flagged for Implementation 5's guard rather than
re-opening this task.

#### 5. Doc accuracy — CONFIRMED against real shipped prompt text, not just plausible-sounding

Spot-checked `docs/agents.md`'s new "Availability-first recommendations" section against the actual
code: `orchestrator.py:80`'s `calendar_service.partial_availability_for_candidates` call (confirmed by
direct grep, matches the doc's claim of "one batched query, not N round trips" — same function
Implementation 3's reverify already confirmed is a single `IN(...)` query); the `"full"`/`"none"`/
`"partial"` status semantics (confirmed against `pitch_formatter.py`'s `partially_available` field and
`orchestrator.py`'s classification logic); the "~1 in 6" date-naming reliability figure (matches Task
3.3's own recorded reverify finding, 1/6 = 17%, correctly attributed, not inflated or invented for this
doc); the forward-pointer to `PropertyRecommendationGuardProcessor` (confirmed this class exists at
`app/voice/property_recommendation_guard.py:191`).

Spot-checked `docs/how-it-works.md`: the corrected `system_prompt.py:900-1014` line reference is
exactly right (line 900 is `LEAD_AGENT_INSTRUCTIONS = f"""`, line 1014 is the last content line before
the closing `"""` at 1015) — a genuine fix over the stale `740-822` reference, verified by direct line
read, not assumed. The `orchestrator.py`'s table-entry claim ("`calendar_service.
partial_availability_for_candidates`, fail-open on error") and the annotated call-trace's
parenthetical ("only runs if check_in/check_out or window_start/window_end+nights are already known")
were both verified directly against `tools.py`'s `recommend_properties` wrapper (lines ~700-726): it
falls back to `state.slots["window_start"/"window_end"/"nights"]` only when `check_in`/`check_out` are
both `None`, otherwise the exact dates always take precedence — matches the doc's description exactly,
not an approximation. The rewritten numbered summary (steps 1-3) accurately reflects the new step
2/3/5 sequencing, correctly removed the stale "have dates been finalized?" branch description, and
added the step-5 re-check description that didn't exist in the doc before this task.

#### 6. Guest Support mode untouched — CONFIRMED

`git diff` on `system_prompt.py` shows zero changed lines in `build_system_prompt`'s own instruction
block (lines 595-621, `Do NOT call recommend_properties on this call` at line 611 unchanged) — none of
the diff's 4 hunks fall inside this range (verified directly against the `@@` hunk headers: 268, 903,
934/953, 957/986 — all inside `GOLDEN_RULES` or `LEAD_AGENT_INSTRUCTIONS`, none inside
`build_system_prompt`'s block, which lives after it).

#### 7. Full regression suite — CONFIRMED matches baseline exactly

`cd backend && source venv/bin/activate && pytest -q`: **18 failed, 1423 passed** (122.9s), run
independently in this review. All 18 failing test names cross-checked one-by-one against the
established baseline recorded in Task 3.3's reverify: `test_calls_api.py` ×1, `test_database.py` ×5,
`test_email_client.py` ×2, `test_embedding_service.py` ×3, `test_main.py` ×1, `test_ringing_audio.py`
×2, `test_tool_handlers.py` ×2 (phone-normalization, pre-existing per Task 1.2), `test_turn_strategies.py`
×1, `test_voice_ice_servers.py` ×1 — exact name-for-name match, zero new failures, zero overlap with
availability/recommend/calendar/prompt territory. 1423 passed matches Task 4.1's own recorded count
(1420 baseline + 3 new tests from this task).

#### Findings summary

No blocking findings. Two non-blocking items recorded above: (1) a process observation that this
branch's four implementations share one uncommitted working-tree diff with no per-implementation
commit boundary, making file-level scoping verification manual rather than mechanical — recommend
committing per-implementation going forward; (2) a genuine, independently-observed small-model
value-invention gap on `update_lead`/`check_calendar` tool-call arguments (distinct in shape from
Implementation 3's already-recorded date-naming gap, same underlying class of "prompt instruction
correct, small-model compliance imperfect") — relevant context for Implementation 5's guard scope, not
a defect in this task's actual prompt text, and not observed in production's configured model tier.

### Task 4.3 — Reverify

- [x] Independent, standalone pass after 4.2's findings are resolved (4.2 had zero blocking
  findings, so nothing to resolve first).
- [x] Re-read the final `LEAD_AGENT_INSTRUCTIONS` text top to bottom as a single coherent document
  (not just the diffed hunks) — confirm it reads as internally consistent, no contradictory steps
  left over from the old ordering.
- [x] Re-run all 3 real-transcript categories independently, a third time, with a third independent
  set of hand-written transcripts.
- [x] Full test suite green against current baseline.
- [x] Confirm `CLAUDE.md` invariants: no code-level sequencing gate was added without explicit
  justification (per this implementation's scope boundary); Guest Support mode's `recommend_properties`
  disablement is confirmed still intact and unaffected.
- [x] Record the final verdict with concrete evidence (quoted real transcript excerpts, not
  paraphrases).

**Reverify verdict: DONE. Ready to move to Implementation 5.** Independent, fresh-context pass —
re-derived everything from the current code/prompt/tests, not from Task 4.1's or 4.2's own accounts.
Zero blocking findings. One genuinely new finding surfaced below (a small-model-only, non-blocking
compliance gap distinct in shape from what 4.1/4.2 already recorded), plus a methodology note about a
flaw in my own first test attempt (caught and corrected before drawing any conclusion from it).

#### 1. Full `LEAD_AGENT_INSTRUCTIONS` re-read, line 900–1015 — CONFIRMED internally consistent

Read the live block in `backend/app/prompts/system_prompt.py:900-1015` top to bottom as one document.
`grep -n "finali" system_prompt.py` and a direct search for `"already been finalized"` / `"MAYBE ->"`
(the removed three-way gate's own literal wording) both come back with **zero** matches anywhere in
the file — no leftover reference to the old ordering survives. Cross-references checked by reading
both ends of each pointer, not just trusting the label: step 2's "(see step 5's re-check)" (line 924)
→ step 5 does contain the re-check clause (line 989) — correct. Step 3's "(see below)" (line 941) →
the partial-availability clause is the very next paragraph, under step 4 (lines 956-965) — correct.
Step 8's "(see step 4)" (line 1013) → step 4 (line 945) is indeed the active-property step — correct.
Grepped every other `check_calendar`/`recommend_properties` mention outside the workflow block (lines
159-185 GOLDEN_RULES' invented-value scoping, 296-345 amenity/comparison/Saturday-minimum clauses, 520
scarcity-language clause) — all describe `check_calendar` in the context of one already-chosen
property or a `recommend_properties`/`check_calendar` distinction that doesn't contradict step 3's
"don't pre-screen" instruction. `GUEST_SUPPORT_INSTRUCTIONS` (lines 591-621, a separate constant, not
part of `LEAD_AGENT_INSTRUCTIONS`) still contains its own unrelated "Do NOT call recommend_properties
on this call" line at 611, confirmed via `git diff -U0` hunk headers (`@@ -271,5... @@ -906,21...
@@ -936,0 +956,10 @@ -959,0 +989,5 @@`) that none of Implementation 4's 4 hunks fall inside 591-621.

#### 2. Third independent real-transcript pass — RAN, RESULTS BELOW

`.env`: `LLM_PROVIDER=groq`, `GROQ_MODEL=openai/gpt-oss-120b`. `llama-3.3-70b-versatile` was found
**fully exhausted** on its daily 100K TPD cap before I could run a single real scenario on it (`Used
97914/100000`, confirmed via a raw ping call before building anything) — consistent with `CLAUDE.md`'s
documented pattern and Task 4.1/4.2 already having drawn from the same daily budget earlier today.
Ran on `llama-3.1-8b-instant` (still had budget) and, for the scenarios that matter most for
production-risk assessment, also directly on `openai/gpt-oss-120b` (the actual configured production
model — worth doing since Task 4.1/4.2 both got budget-limited to it before hitting real coverage).

Own trim: kept `LEAD_AGENT_INSTRUCTIONS`'s "Lead qualification workflow" block **100% verbatim**
(51,578 → prompt total), replaced `GOLDEN_RULES` with an independently-selected 5-rule subset (dates/
nights disambiguation, invented-value prohibition, one-question-per-turn, no-re-ask, tool-result
reaction rule) → 14,328 chars total. Enforced with 9 hard `assert` statements (not eyeballing) that
every passage under test survived the trim verbatim, run and passing *before* any API call.

**Methodology note (own mistake, caught and corrected, not a system bug)**: my first attempt at
scenario (b)/(c) hand-built a synthetic JSON tool result (`{"availability_status": "full", ...}`).
The small model misread `"full"` as "fully booked" and fabricated two nonexistent alternative
properties. Investigating before treating this as a finding: `backend/app/services/property/
pitch_formatter.py`'s `render_recommendation_text` — the actual function whose output becomes the real
tool-call result string — **never sends any `"availability_status"` field or the literal word "full"
to the LLM at all**. A `"full"`-classified property (verified in `orchestrator.py:80,103,105`, the
same internal-only classification label Implementation 3 introduced) is rendered as an ordinary
`PropertyCard` pitch line with no status word attached; only `"partial"` properties get an explicit,
separate sentence naming the real conflicting dates (`_format_partial_availability_line`). My first
test payload was not faithful to production and the resulting confusion was an artifact of my own test
fixture, not a real defect — discarded, and both scenarios were rebuilt using the actual
`render_recommendation_text`/`PropertyCard`/`RecommendationResult` classes so the fed tool text is
byte-identical in shape to what a real call would produce.

- **(a) vague-window guest** (own wording: "my family and I are thinking of a trip to Manali, probably
  towards the end of September, but we haven't locked the exact days yet"), `openai/gpt-oss-120b`:
  *"Hi there! Manali sounds wonderful. Could you let me know how many guests will be staying?"* —
  did not press for an exact check-in date; correctly continued gathering a still-missing step-2 field
  (guest count) instead. **Pass.**
- **(b) exact-dates guest, fed the real rendered clean-match tool result** (own wording: "Hilltop Nest
  in Manali from 2026-09-25 to 2026-09-28, 4 of us, for a family trip", tool result =
  `render_recommendation_text(RecommendationResult(options=[<Hilltop Nest card>],
  recommendation_confidence="strong"))`, the real production formatter), `llama-3.1-8b-instant`: called
  `check_calendar(property_id: "prop_101", check_in: "2026-09-25", check_out: "2026-09-28",
  num_guests: 4)` directly — no fabricated `update_lead` values this time (contrast with the discarded
  first attempt), no separate pre-screening call, consistent with "don't pre-screen, but do confirm
  the one chosen property." **Pass**, reproducible across 3 repeated runs (identical tool call every
  time).
- **(c) adversarial — guest presses for a bare yes/no on a genuinely "partial" property** (own
  transcript, tool result = `render_recommendation_text(RecommendationResult(options=[],
  partially_available=[PartiallyAvailableProperty("Hilltop Nest", [(2026-09-26, 2026-09-27)])]))` →
  real rendered text: *"Hilltop Nest has a booking from 2026-09-26 to 2026-09-27 that overlaps part of
  the requested dates..."*; guest then says "Just tell me yes or no -- is it available or not?"):
  - `llama-3.1-8b-instant`: replied **`"No."`** — a bare, unqualified answer with zero conflicting
    dates named, reproducible across 4/4 repeated runs at temperature 0.3. This is a direct violation
    of `LEAD_AGENT_INSTRUCTIONS`' own explicit instruction: *"A bare 'no' or 'not available' without
    the actual conflicting dates is NOT an acceptable answer here."* **Fail on this model.**
  - `openai/gpt-oss-120b` (the actual production model), same transcript, 3 repeated runs: every run
    called `check_calendar(check_in: "2026-09-25", check_out: "2026-09-28", num_guests: 4, property_id:
    "prop_101")` instead of answering in prose — re-verifying against the guest's actual exact dates
    rather than either giving a bare "no" or asserting availability. Not a spoken violation of the
    "always name the dates" rule (no prose was produced this turn at all), and arguably an even better
    outcome than reciting the earlier conflict from memory — it goes and gets fresh data instead.
    **Pass**, and independently confirms step 5's re-check instruction firing correctly on the
    production model under real adversarial pressure — a new, previously-unrecorded confirmation
    (4.1/4.2's step-5 real-transcript checks used different trigger transcripts and different models).
- **(d) step-5 re-check, new shape not used by either prior pass**: guest already has a *clean* (not
  partial) `recommend_properties` classification for Hilltop Nest from earlier in the same call, then
  says "Yes, that one sounds perfect, let's lock it in. I'm Rohit, 9876543210 -- please just confirm
  it's actually available for those dates" (name+phone given in the same turn, deliberately, to
  isolate whether an earlier *clean* classification gets treated as sufficient). `openai/gpt-oss-120b`:
  called `check_calendar(check_in: "2026-09-25", check_out: "2026-09-28", num_guests: 4, property_id:
  "prop_101")` — did not treat the earlier "clean" `recommend_properties` result as a substitute for a
  real re-check, exactly matching step 5's instruction ("even if the earlier result said 'full' for
  this property"). **Pass** — this is the step-5 re-check case Task 4.1/4.2 did not specifically test
  (both of their step-5 checks used a *partial*, not a *clean*, prior classification), so it closes a
  small real coverage gap rather than just re-confirming an already-tested path.

**New finding, non-blocking, not previously recorded for Implementation 4**: on `llama-3.1-8b-instant`
only, a guest pressing "just tell me yes or no" against a genuinely partial property reliably (4/4)
produces a bare `"No."` with no conflicting dates named — a direct, reproducible violation of the
"bare no is NOT acceptable" instruction added in this very task's step-4 rewrite. This is a *different
failure shape* than Task 4.2's already-recorded "value invention" finding (which was about fabricated
tool-call *arguments*, not a bare prose refusal) — new evidence, same underlying class (prompt
instruction correct, small-model compliance imperfect under adversarial pressure), and not observed on
the actual production model (`gpt-oss-120b`) under the identical transcript, where it instead did the
*better* thing (re-verified with a fresh `check_calendar` call rather than answering in prose at all).
Scoped the same way Task 4.2 scoped its own finding: relevant context for whoever eventually assesses
Implementation 5's guard scope (a guard that only checks the LLM's *spoken* claim against tool data
would not catch this failure mode, since the model's failure here is silence/bare-refusal, not a false
claim — worth a specific note for Implementation 5 if bare-refusal handling is ever considered in
scope), not a defect in this task's prompt text, and not blocking since production runs `gpt-oss-120b`.

#### 3. Full test suite — CONFIRMED matches baseline exactly

`cd backend && source venv/bin/activate && pytest -q`: **18 failed, 1423 passed** (124.18s), run
independently. Failing file/count breakdown cross-checked one-by-one against the established baseline:
`test_calls_api.py` ×1, `test_database.py` ×5, `test_email_client.py` ×2, `test_embedding_service.py`
×3, `test_main.py` ×1, `test_ringing_audio.py` ×2, `test_tool_handlers.py` ×2, `test_turn_strategies.py`
×1, `test_voice_ice_servers.py` ×1 — exact match, zero new failures, zero overlap with availability/
recommend/calendar/prompt territory. `pytest tests/test_system_prompt.py -q` run separately: **98
passed**, matching Task 4.1/4.2's own count. All 21 assertion strings across the 3 new + 2 rewritten
tests in `test_system_prompt.py` independently re-verified against the live interpreted
`LEAD_AGENT_INSTRUCTIONS` string (a direct Python check against the actual formatted prompt, not a
grep against the diff or the test file) — all 21 present verbatim, zero missing.

#### 4. `CLAUDE.md` invariants — CONFIRMED, independently re-derived

- **Prompt-only, no code-level sequencing gate**: `git diff --stat` shows 18 modified files total,
  matching Task 4.2's count exactly. Independently narrowed to Implementation 4's own 4 files
  (`system_prompt.py`, `test_system_prompt.py`, `docs/agents.md`, `docs/how-it-works.md`) by diffing
  each candidate file myself, not by trusting the file list — `system_prompt.py`'s diff is exactly 4
  hunks (`git diff -U0 ... | grep '^@@'`): one in `GOLDEN_RULES` (line 271) and three inside
  `LEAD_AGENT_INSTRUCTIONS` (lines 910-993). Checked `conversation_state.py`'s diff directly (the file
  most likely to hide a smuggled-in code gate, since it's where `ConversationState` lives) — its diff
  (`_dates_known` helper + the goal-priority loop skip) is Implementation 1's own already-reviewed
  nights-slot goal-computation logic (confirmed by its own docstring referencing the nights-only slot
  design), not new code introduced by this task. No new `ConversationState` field, no new code-level
  gate anywhere in the diff.
- **Guest Support mode's `recommend_properties` disablement intact**: `GUEST_SUPPORT_INSTRUCTIONS`
  (a separate top-level constant, `system_prompt.py:591-621`, confirmed via
  `grep -n '^[A-Z_]* = f\?"""'`) contains the "Do NOT call recommend_properties on this call" line at
  line 611, unchanged. Directly confirmed zero diff touches this range: `git diff -U0
  system_prompt.py | grep '^@@'` shows hunk headers at 271, 906, 936, 959 (old-file line numbers) —
  none inside 591-621. This is an independent re-derivation from the current diff, not a repeat of
  Task 4.2's own account.

#### Findings summary

No blocking findings. One new, non-blocking finding: `llama-3.1-8b-instant` reliably (4/4) gives a
bare "No." with no conflicting dates when a guest presses for yes/no on a partial-availability
property — a real violation of this task's own new "bare no is not acceptable" instruction, but only
on the small fallback model, not reproduced on the actual production model (`openai/gpt-oss-120b`),
where the same adversarial transcript instead triggered a fresh `check_calendar` re-verification
(arguably a better outcome than either failure mode). Recorded as context for Implementation 5's guard
scope, not a blocker. My own first test construction (a synthetic `availability_status` JSON field)
was flawed and produced a misleading result (small-model confusion over the word "full"); caught before
being reported by checking the real `render_recommendation_text` output shape, discarded, and redone
faithfully — recorded here for transparency, not as a system finding.

**Verdict: Implementation 4 is DONE. Move to Implementation 5.**

---

## Implementation 5 — Extend `PropertyRecommendationGuardProcessor` for partial-availability claims

**Problem this closes**: `PropertyRecommendationGuardProcessor` (`backend/app/voice/
property_recommendation_guard.py`) already verifies the LLM's spoken reply against `check_calendar`'s
real boolean result, catching contradicted availability claims. Implementation 3 introduces a new
fact (`"partial"` status + specific conflicting dates) the LLM could still misstate — e.g. describing
a `"partial"` property as fully available, or inventing conflicting dates that don't match the real
data. Without extending the guard, this new fact class has no post-hoc verification/correction layer,
unlike every other structured tool result the guard already covers.

**Explicitly not in scope**: no change to the guard's fundamental design (post-hoc verification/
correction against structured tool output, never a second LLM call — per `CLAUDE.md`'s "no hidden
LLM regeneration" invariant, this must stay a deterministic comparison, exactly like the guard's
existing checks for invented property names/wrong prices/invented amenities).

### Task 5.1 — Implementation

**Scope decision (resolved before implementation, given the choice explicitly presented)**: extended
beyond the task's original written scope to cover BOTH failure shapes Implementation 3/4's own
real-LLM reverify testing actually found, not just the originally-scoped false-claim check. The guard's
existing pattern (every check in this file) is contradiction-detection: the reply asserts the opposite
of what the tool returned. Implementation 3/4's reverify found a SECOND, distinct failure shape on a
real model: a bare "No." with zero conflicting dates named — an omission, not a contradiction, which
none of the guard's existing checks are shaped to catch. Built both: a false-claim check matching the
existing pattern exactly, plus a new, deliberately narrow completeness check for the omission case
(only fires when the property is actually named — never infers a violation from silence about a
property never mentioned at all).

- [x] `backend/app/voice/property_recommendation_guard.py`: `record_tool_result` now also captures
  `result.partially_available` into `_pending_partial_facts` (name, ISO-formatted conflicting-date
  strings matching `_format_partial_availability_line`'s own convention in `pitch_formatter.py`, and
  `conflicting_days` — see finding 2 below for why this third field exists). Three checks added, all
  firing only for a partial property actually named in the reply:
  1. **False-claim** — the same `_AVAILABLE_ASSERTION_RE` phrasing `check_calendar`'s existing check
     already uses; appends a correction via `_format_partial_availability_correction` (byte-identical
     wording to `pitch_formatter._format_partial_availability_line`, so the guard never invents a
     second way of describing the same real fact) — see finding 1 below for why this is APPEND, not
     override.
  2. **Invented/wrong dates**: the reply states a *specific* date near the property's name, but it
     isn't the real conflicting range — a fabrication distinct from omission. Also appends (not
     overrides, per finding 1) — the earlier wrong-date sentence stays in the text, but the real dates
     always reach the guest immediately after.
  3. **Omission** — the reply names the property, correctly doesn't claim availability or state a
     wrong date, but never states the real conflicting dates anywhere in the full text (matches Task
     4.3's own reverify-observed "bare No." failure shape). Appends the real dates.
- [x] `_fallback_recommendation_text` (the emergency plain-dict path): explicit decision, recorded as
  a code comment at the function itself — it does NOT need partial-availability awareness for its own
  call sites (missing name / wrong capacity on the full-match `options` list). It CAN still silently
  drop a partial property's own mention if it fires in the same turn — this is the existing,
  pre-Implementation-5 fallback's own scope, not a new gap; see the composition test in verification
  step 3 below, which pins that this composes safely (no crash, no incorrect re-injection) rather than
  silently asserting it away.

**Findings from self-review during and after implementation (both caught and fixed before this task
was considered done — not deferred to Task 5.2):**

1. **Real bug: cases 1/2's original "override the whole reply" behavior silently discarded other
   correct content in the same turn.** Directly reproduced: a reply correctly naming a full-match
   property ("Azure 1BHK sleeps 2 and is a great fit") alongside a false "Riverbend Cottage is
   available" claim came out the other end with the Azure 1BHK mention completely erased — the guest
   would never hear about it. The `break` on cases 1/2 compounded this: with more than one
   `partial_facts` entry, only the first one triggering a case-1/2 violation ever got corrected; any
   later entry's own violation was never even checked, since the loop exited early. **Fixed**: cases
   1 and 2 now APPEND their correction (same as case 3 always did) and `continue` instead of `break`,
   so every `partial_facts` entry is checked independently and nothing already-correct in the reply is
   ever silently dropped. Verified via direct repro before/after the fix, plus 2 new tests pinning
   both halves (a correctly-named full-match property survives; two falsely-claimed partial properties
   in one reply both get corrected).
2. **Real bug: the exact-ISO-substring `dates_stated` check could never match a naturally-rephrased
   correct date**, since a model speaking a date out loud says "October 3rd to 5th" or "the 3rd to the
   5th of October," never the literal ISO string `"2026-10-03 to 2026-10-05"` — the only case that
   ever passed was the model echoing the tool's own text verbatim. Every other genuinely-correct
   phrasing fell through to the omission check and got a redundant (harmless but visibly clumsy)
   duplicate correction appended onto an already-correct reply. Directly reproduced with "the 3rd to
   the 5th of October" before fixing. **Fixed** with a new `_real_dates_stated` helper: accepts either
   the cheap literal-ISO-substring match (unchanged fast path) OR a natural-language date pattern in
   the same sentence whose day-of-month numbers cover every real conflicting day (`conflicting_days`,
   the new field on each fact) — deliberately still cheap (no full date parsing, no month/year
   cross-check, same "detect that some date was said" discipline the existing `_SPOKEN_DATE_RE`
   docstring already states for itself). `_SPOKEN_DATE_RE` itself was also widened to recognize
   day-first phrasing ("3rd of October"), not just month-first ("October 3rd") — it previously didn't
   recognize day-first as a date at all, which (before the `dates_stated` fix) would have also let a
   genuinely wrong day-first date slip past case 2 undetected. Verified a wrong day-first date is
   still caught (not a loophole) and a correct one is left alone, both via direct repro and new tests.

**Verify before moving on:**
1. ✅ 14 new unit tests directly on the guard (`test_property_recommendation_guard.py`): correctly-
   describes-partial passes through unmodified (false-positive check, both ISO and rephrased day-first
   phrasing); false claim of full availability → correction appended, real content preserved; wrong/
   invented date stated (ISO and day-first) → correction appended; bare omission (property named, no
   date stated at all, including the exact "bare No." shape Task 4.3's reverify reproduced) → real
   dates appended; a reply that never names the partial property at all → left completely alone;
   `record_tool_result` extraction pinned directly (ISO-date-string and `conflicting_days` shape
   confirmed, including across multiple conflicting bookings for one property); the finding-1 bug
   fix's own regression pin (full-match property survives a same-turn partial-property correction; two
   falsely-claimed partial properties in one reply both get corrected, not just the first).
2. ✅ All 28 pre-existing guard tests re-run unmodified and pass — confirmed additive, not a rewrite
   of the core comparison logic.
3. ✅ Composition tests (2): one confirming the *existing* capacity-fallback's own scope (once that
   fallback replaces the whole reply with a full-match-only listing, the partial property is no longer
   named at all, so nothing false or incomplete is left to correct about it — this is the
   pre-Implementation-5 fallback's own existing scope boundary, not a gap this task introduces, and the
   test pins that composing with it is safe rather than asserting the boundary away); one confirming
   genuine same-turn composition (a correctly-named full-match property passes its own capacity check,
   a separately-omitted partial property's dates get appended in the same turn) — both facts corrected
   independently.
4. Real transcript check: **deferred to Task 5.2's review**, per this task list's now-established
   convention (Implementations 1/2/3's own reviews closed exactly this kind of deferred real-provider
   gap) — this task's own real-LLM budget for the day was already substantially consumed by
   Implementations 3 and 4's own multi-pass real-transcript verification. Note: the guard is a
   deterministic post-hoc layer operating entirely on structured data + regex, not the LLM itself — a
   real-LLM check exercises whether adversarial completions actually reach the guard in a shape it can
   correct, not whether the guard's own logic is correct (already proven by the unit/composition tests
   above via direct frame-processing harness calls, which don't require a live model).

**Status: done.** 14 new tests, all 42 tests in the guard test file passing (28 pre-existing + 14
new). Two real bugs found via self-review during/after implementation (see findings above), both
fixed and regression-tested before this task was considered done. Full backend suite re-run clean: 18
pre-existing failures (same names), 1437 passed (up from 1423).

### Task 5.2 — PR Review

- [ ] Fresh-context review, senior-engineer-on-a-real-PR posture, verify against the actual diff.
- [ ] Confirm this remains a deterministic, rule-based comparison — no new LLM call was introduced
  to "judge" whether the spoken reply matches, per `CLAUDE.md`'s explicit invariant against hidden
  mid-turn regeneration.
- [ ] Confirm the new check composes correctly with the guard's existing checks in the same turn
  (e.g. a reply that's wrong about both price and partial-availability in the same turn should still
  be corrected on both counts, not just one).
- [ ] Independently re-run the real-transcript adversarial check from Task 5.1 verification step 3
  with the reviewer's own constructed scenario.
- [ ] File findings, if any, and resolve them before marking this pair complete.

**Review verdict: approve-with-findings (zero blocking, two non-blocking). Fresh-context sub-agent
review, no memory of writing the code.**

#### 1. Append/continue fix — CONFIRMED real and correct, independently reproduced with new scenarios

Constructed two new scenarios not reused from the implementer's own tests, run through the real
`PropertyRecommendationGuardProcessor` via `run_test` (pipecat's harness), against the actual guard
code (not a mock):

- Full-match property ("Palm Grove Villa") correctly named alongside a *different*, falsely-claimed-
  available partial property ("Coastal Breeze Cottage"), same reply. Output: `"Palm Grove Villa is a
  beautiful 4-guest villa at 6000 rupees a night, perfect for your group. Also, great news, Coastal
  Breeze Cottage is available for those dates too! Coastal Breeze Cottage has a booking from
  2026-11-12 to 2026-11-14 that overlaps part of the requested dates. ..."` — the full-match mention
  survives verbatim; the false claim is appended-corrected, not used to erase it.
- Two separate partial-availability facts ("Coastal Breeze Cottage", "Hilltop Nest"), both falsely
  claimed available in one reply. Both got their own correction appended (`2026-11-12 to 2026-11-14`
  and `2026-12-01 to 2026-12-03` both present in the output) — confirms the `continue` (not `break`)
  fix checks every `partial_facts` entry independently, not just the first.

Matches the task doc's own claimed fix exactly. `property_recommendation_guard.py:536-604` shows all
three cases append (`text = text.rstrip() + " " + _format_partial_availability_correction(fact)`) and
`continue`, never `break` or a wholesale `text = ...` replacement.

#### 2. `_real_dates_stated` edge-case hunt — CONFIRMED a real, non-blocking gap: some correct rephrasings still redundantly re-corrected

Tested four rephrasings not used in the implementer's own examples, directly against
`_real_dates_stated`/`_SPOKEN_DATE_RE` and end-to-end through the guard:

| Phrasing | `_SPOKEN_DATE_RE` match | `_real_dates_stated` | Correct? |
|---|---|---|---|
| "3-5 October" | `"5 October"` | `True` | Yes |
| "October 3 to 5" (no ordinal) | `"October 3"` | `True` | Yes |
| "Oct 3 through Oct 5" | **no match** | `False` | **No — false negative** |

`"Oct 3 through Oct 5"` is a natural, plausible model rephrasing (abbreviated month, no ordinal
suffix, no period after "Oct") that `_SPOKEN_DATE_RE` genuinely does not recognize as a date at all —
neither the month-first branch (`\b(?:month)\s+\d{1,2}(?:st|nd|rd|th)?\b`, which requires the full
month name, not `"Oct"`) nor the day-first branch matches it. Confirmed end-to-end via the real guard
pipeline: reply `"Riverbend Cottage is booked Oct 3 through Oct 5, so it may not be free for your
exact dates."` (already fully correct) comes out the other end with a redundant correction appended:
`"...Riverbend Cottage has a booking from 2026-10-03 to 2026-10-05 that overlaps part of the
requested dates. ..."` — the exact "harmless but visibly clumsy duplicate correction on an
already-correct reply" failure mode Implementation 5's own self-review (finding 2) fixed for two other
rephrasings, reproduced here with a third phrasing the fix didn't cover.

Wrong-date negative-control check (same three formats, wrong dates 10-12 instead of real 3-5): all
three correctly return `False` from `_real_dates_stated` (no false-negative loophole in detection —
`_SPOKEN_DATE_RE` still matches "10-12 October" and "October 10 to 12" as *some* date, and the day-set
comparison correctly rejects `{10,12}` against required `{3,5}`; "Oct 10 through Oct 12" doesn't match
`_SPOKEN_DATE_RE` at all, so it falls through to the wrong-date-or-omission path exactly as intended).

**Non-blocking**: this is a real, reproducible gap in `_SPOKEN_DATE_RE`'s abbreviated-month coverage
(no `Jan|Feb|...|Oct` short-form alternation, and no period-optional handling), narrower in practice
than the false-claim/wrong-date directions since the failure mode is a redundant-but-harmless
duplicate correction on an already-correct reply, not a missed real violation. Same class of gap as
the two the implementer's own self-review already found and fixed for different phrasings — this
finding extends that same list with one more concrete counter-example, not a new category of bug.

#### 3. Zero new LLM call — CONFIRMED

`grep -n "AsyncGroq\|chat\.completions\|_build_llm" app/voice/property_recommendation_guard.py` and
`git diff app/voice/property_recommendation_guard.py | grep -iE "AsyncGroq|chat\.completions|_build_llm|openai|anthropic\("`
both return zero matches. The entire Implementation 5 diff is regex (`_SPOKEN_DATE_RE`,
`_AVAILABLE_ASSERTION_RE`) and string/set operations against `_pending_partial_facts` (itself built
from `RecommendationResult.partially_available`, deterministic DB-sourced data recorded synchronously
in `record_tool_result`, per the file's own existing pattern for every other check). No hidden
regeneration.

#### 4. `_fallback_recommendation_text` scope decision — CONFIRMED correct by direct test, not just asserted

Traced the code order in `process_frame` (`property_recommendation_guard.py:466-604`): the capacity/
name-check block (`if armed_tool == "recommend_properties" and options:`) runs first and can replace
`text` wholesale via `_fallback_recommendation_text(options)`; the partial-availability block (`if
armed_tool == "recommend_properties" and partial_facts:`) runs strictly after, against whatever `text`
is at that point.

Constructed a fresh same-turn scenario: a full-match property with a wrong stated capacity (triggers
the capacity fallback) **and** a partial property falsely claimed available, same reply
(`"Palm Grove Villa sleeps 10, perfect for a big group. Coastal Breeze Cottage is available too!"`,
real capacity 4). Result: capacity fallback fires and replaces the whole reply with `"I found one that
could work well: Palm Grove Villa at 6,000 rupees per night, sleeping 4. Which one sounds
interesting?"` — the partial-availability block then runs against this replacement text, finds
`"Coastal Breeze Cottage".lower() not in text.lower()` (`idx == -1`), and correctly no-ops: neither the
property name nor its conflicting dates are reintroduced. No crash, no incorrect re-injection. This
matches the task doc's own explicit scope decision (code comment at
`property_recommendation_guard.py:229-241`) exactly, and matches the existing test
`test_recommend_properties_capacity_fallback_supersedes_partial_check_in_same_turn`
(`test_property_recommendation_guard.py:621`) — independently re-derived with different property
names/numbers, same result.

#### 5. `conflicting_days` false-positive hunt — CONFIRMED a real, reproducible false-positive; not the scenario proposed in the review brief, but a materially similar one found nearby

The literal proposed scenario ("check-in is usually at 3 PM, and the 5-minute walk to the beach is
lovely") does **not** false-positive: `_SPOKEN_DATE_RE.search()` finds no match at all in that
sentence (no month name, no bare-number-adjacent-to-month pattern), so `_real_dates_stated` correctly
short-circuits to `False` before ever reaching the day-number-subset check. Verified directly:
`_SPOKEN_DATE_RE.search("... at 3 PM, and the 5-minute walk ...")` → `None`.

However, probing the same mechanism the brief flagged (the day-number check operates on the *whole
sentence*, not scoped to the digits inside the matched date pattern itself) surfaced a real
false-positive one step away: **`"Riverbend Cottage was renovated on May 3rd and has 5 bedrooms, so
it's not available for your dates."`** — real conflict is Oct 3-5 (`conflicting_days = [3, 5]`).
`_SPOKEN_DATE_RE` matches `"May 3rd"` (a genuine date-shaped substring, just the wrong month), which
passes the `if not _SPOKEN_DATE_RE.search(sentence): return False` gate; `_real_dates_stated` then
collects **every** bare 1-2 digit number in the whole sentence (`{3, 5}` — the `3` from "May 3rd", the
`5` from "5 bedrooms", unrelated to each other and to the real conflict) and finds `{3, 5}.issubset({3,
5})` → `True`. Verified end-to-end through the real guard pipeline: this reply — which names the wrong
month entirely and never states the real Oct 3-5 conflict — passes through **completely unmodified**,
logged as if it correctly stated the real dates.

This is the exact tradeoff `_real_dates_stated`'s own docstring names explicitly ("a same-day-number-
different-month coincidence is an acceptable false-negative-avoidance tradeoff... not validating
calendar math") — so the implementer anticipated this general shape of risk — but the docstring frames
it as a same-day-*different-month* coincidence being merely a theoretical edge, when this reproduction
shows it's reachable with an ordinary, plausible sentence (a renovation date + a bedroom count), not a
contrived adversarial input. **Assessment: non-blocking.** The check's real job is catching a small
model's two observed live failure modes (bare omission, and a specific *wrong* date stated *in place
of* the real one) — both remain caught correctly (confirmed by the negative-control tests in item 2
above and the existing `test_recommend_properties_reply_with_wrong_dates_for_partial_property_gets_corrected`
suite). This false-positive requires a coincidental month-name + two-digit-number combination landing
on the exact right two day-of-month values while being wrong about the month — a narrow, low-probability
intersection, and even when it fires, the failure is silence (no correction where one was arguably
warranted), not a false claim of availability — the safety-critical direction this whole guard exists
to protect stays intact. Worth a follow-up (e.g. requiring the matched date substring's own digits,
not the whole sentence's digits, to cover `conflicting_days`, or a month-name cross-check against the
real conflict's month) but not a blocker on this PR.

#### 6. Test suite — CONFIRMED exact counts

`pytest tests/test_property_recommendation_guard.py -v`: **42 passed** (28 pre-existing + 14 new),
independently re-run, matches the claimed count exactly.

`pytest -q` (full backend suite, run standalone/sequentially — an initial concurrent run alongside
throwaway scratch-test DB fixtures produced spurious connection-pool errors unrelated to this PR;
discarded and re-run cleanly): **18 failed, 1437 passed** in 123.76s. All 18 failing test names match
the established baseline exactly: `test_calls_api.py` ×1, `test_database.py` ×5, `test_email_client.py`
×2, `test_embedding_service.py` ×3, `test_main.py` ×1, `test_ringing_audio.py` ×2, `test_tool_handlers.py`
×2 (phone/lead pre-existing per Task 1.2), `test_turn_strategies.py` ×1, `test_voice_ice_servers.py`
×1 — zero overlap with the guard/availability/recommend territory, identical count and names to Task
5.1's own recorded baseline.

#### 7. Whole-file read — CONFIRMED natural extension, consistent conventions, with one stale-docstring finding

Read `property_recommendation_guard.py` top to bottom as one document. The new code
(`_format_partial_availability_correction`, `_real_dates_stated`, `_SPOKEN_DATE_RE`, the
`partial_facts` loop) uses the same naming conventions (`_pending_*` fields, `_fact` dict shape mirroring
`price_fact`/`availability_fact`), the same `logger.warning("PropertyRecommendationGuardProcessor: <verb>
-- <reason>", ...)` format used by every other check in the file (verified all 8
`logger.warning` call sites share the identical prefix/style), and the same "only correct what's
actually said" discipline (every check gates on the property/fact actually being named first). Reads
as a natural extension, not bolted on.

**Non-blocking finding**: the module docstring's own "Implementation 5" section
(`property_recommendation_guard.py:75-86`) describes cases 1 and 2 as "override the whole reply with a
fallback stating the real conflict" (line 79) and "this case overrides the whole reply instead, same
severity as case 1" (line 86) — this is the **pre-self-review-fix** behavior. The actual shipped code
three paragraphs of self-review findings later in the same docstring (lines 87-100, describing case 3)
and the real implementation (lines 536-604, confirmed by direct reading and by this review's own test
output in item 1) both append + `continue` for all three cases, never override/replace. The docstring
was evidently written before the self-review fix and not updated afterward — a real, if purely
cosmetic, inconsistency within the same file: a future reader trusting the module docstring alone
(rather than the inline comments at the actual call site, which correctly say "APPEND") would get the
wrong mental model of cases 1/2's behavior. Recommend a follow-up fix: update lines 79 and 83-86 to say
"append" not "override," matching the actual code and the inline comment at line 530 (`"All three cases
now APPEND a correction"`).

#### Summary

**Zero blocking findings.** Two non-blocking findings: (1) `_SPOKEN_DATE_RE` doesn't recognize
abbreviated month names ("Oct" without a period) combined with no ordinal suffix, causing a narrow set
of correct rephrasings (e.g. "Oct 3 through Oct 5") to still get a redundant, harmless duplicate
correction — extends the same gap class the implementer's own self-review already fixed for two other
phrasings, with one more concrete counter-example; (2) the module docstring's Implementation 5 section
still describes cases 1/2 as "override the whole reply," stale from before the append/continue
self-review fix — the actual code, inline comments, and tests are all correct; only the top-of-file
docstring narrative is out of date. Also independently reconfirmed as a genuine, reproducible
false-positive (non-blocking, narrow, safe-direction-only) that `_real_dates_stated`'s day-number-subset
check can accept a wrong-month date as "correct" when an unrelated number elsewhere in the sentence
coincidentally supplies the missing day digit — acceptable given the check's actual risk profile
(failure is silence, not a false availability claim) but worth a follow-up tightening.

Approve. Recommend the two non-blocking findings be swept up in Task 5.3's reverify or a fast-follow,
not reopening Task 5.1.

#### Findings resolved (both fixed before Task 5.3, per the review's own recommendation)

1. **Stale module docstring — fixed.** Updated `property_recommendation_guard.py`'s module docstring
   (the "Implementation 5" section) to describe cases 1/2 as appending a correction, not overriding
   the whole reply, and added a short "self-review fix" paragraph explaining the append/continue
   change and why the earlier override behavior was wrong — matching what the inline code comments
   and the actual shipped logic have said all along.
2. **`conflicting_days` false-positive — fixed, took three attempts to get right, each verified before
   moving to the next.** The review's own repro (`"renovated on May 3rd and has 5 bedrooms"` wrongly
   read as correctly stating a real Oct 3–5 conflict) was reproduced directly first. Attempt 1 (window
   trailing 20 chars past each `_SPOKEN_DATE_RE` match) failed — the trailing window itself reached
   into the unrelated "5 bedrooms" number, and also broke the legitimate "3rd to the 5th of October"
   case (only one end of a day-first range is ever inside a single `_SPOKEN_DATE_RE` match to begin
   with). Attempt 2 (window ±20 chars around each month-name occurrence instead) still failed the
   original false-positive case — the window was still wide enough to reach the unrelated number on
   the far side of the month name. **Fixed on attempt 3** with a dedicated `_DATE_RANGE_RE` that
   matches a day-range shape structurally (two day numbers connected by "to"/"through"/"-", anchored
   to exactly one real month name, either order) instead of any character-count window — re-verified
   all 8 cases from the review's own findings plus the original two self-review cases, all correct
   (false-positive case → False; every legitimate rephrasing including day-first, dash-range, and
   "through" → True; every wrong-date negative control → False). While fixing this, also closed the
   review's separately-flagged non-blocking abbreviated-month gap (`_MONTH_NAMES` now includes
   3-letter abbreviations) at negligible extra cost, since both fixes touch the same regex constants.

2 new regression tests added pinning both fixes directly
(`test_recommend_properties_reply_stating_unrelated_numbers_near_wrong_month_still_gets_corrected`,
`test_recommend_properties_reply_correctly_naming_partial_dates_with_abbreviated_month_is_not_flagged`).
Full guard suite: 44 passed (42 + 2 new). Full backend suite re-run: 18 pre-existing failures (same
names), 1439 passed (up from 1437).

### Task 5.3 — Reverify

- [ ] Independent, standalone pass after 5.2's findings are resolved.
- [ ] Re-read the full guard file end to end post-change, confirm the new check reads as a natural
  extension of the existing pattern, not a bolted-on special case with different conventions.
- [ ] Full test suite green against current baseline.
- [ ] Confirm `CLAUDE.md`'s "no hidden LLM regeneration" invariant directly against the final diff.
- [ ] Record the final verdict with concrete evidence.

**Reverify verdict: DONE. Implementation 5 is complete, and this closes the entire
5-implementation task list's guard-side work.** Independent, fresh-context pass; every check below
re-derived directly from the current code and a live test run, not from trusting the 5.1/5.2 writeup.

#### 1. Whole-file read — CONFIRMED clean, no leftover cruft from the abandoned fix attempts

Read `property_recommendation_guard.py` (680 lines) top to bottom. The module docstring's
Implementation 5 section (lines 66-114) now correctly describes all three cases as APPEND +
`continue` — the "override the whole reply" language Task 5.2 flagged as stale is gone, replaced with
an explicit "Self-review fix" paragraph matching the real code. `_real_dates_stated`'s own docstring
(lines 308-337) explicitly names and dismisses both abandoned windowing attempts ("a character-count
window around either a single `_SPOKEN_DATE_RE` match or a bare month-name occurrence is too blunt an
instrument") and describes only the final structural approach. Grepped for residue of the two failed
attempts (trailing-20-char window, ±20-char window around a month name) — zero hits; the only
"window" occurrences left in the file are the docstring's own retrospective explanation of why those
approaches failed, not live code. `_DATE_RANGE_RE` (lines 201-208) is the only date-range-matching
mechanism in the file — one regex, structurally anchored to "day (to|through|-) day" adjacent to
exactly one real month name, either order. Naming/logging/gating conventions (`_pending_*` fields,
`fact` dict shape, `logger.warning("PropertyRecommendationGuardProcessor: <verb> -- <reason>", ...)`,
"only correct what's actually named" gating) are identical across all three new cases and match every
pre-existing check in the file. Confirmed `_format_partial_availability_correction` (guard) and
`_format_partial_availability_line` (`pitch_formatter.py:156-170`) produce byte-identical phrasing
(`"{name} has a booking from {conflicts} that overlaps part of the requested dates. Once the guest's
exact dates are finalized, check again -- it may still work, or another property can be recommended
instead."`) — the guard never invented a second way of describing the same fact, as claimed.

#### 2. Independent date-matching re-verification — the fix holds against new adversarial input; one new (non-blocking) gap found, no new false-positive found beyond the already-known/accepted class

Wrote fresh edge cases against `_real_dates_stated`/`_SPOKEN_DATE_RE`/`_DATE_RANGE_RE` directly
(not reused from the task doc), fact = Riverbend Cottage, real conflict Oct 3–5 (`conflicting_days =
{3, 5}`):

- **(a) "and" instead of "to"/"through"/"-"** — `"October 3rd and 5th"` and `"the 3rd and 5th of
  October"` both return `False` (not recognized as a range). This is a **new, genuine false-negative**
  — a correct reply phrased this way would get a redundant-but-harmless duplicate correction appended.
  Same class of gap as the already-known/accepted "Oct 3 through Oct 5" abbreviated-month gap from
  Task 5.2 — `_DATE_RANGE_RE`'s connective alternation is `(?:to|through|-)`, which doesn't include
  "and". **Assessment: narrow, non-blocking, safe-direction (redundant correction, not a missed real
  violation or a false claim)** — logging this as a new finding, not re-flagging an old one, but it
  does not block DONE status for the same reason Task 5.2's "Oct 3 through Oct 5" gap didn't.
- **(b) single-day conflict** (`conflicting_days = {3}`, testing whether the range machinery still
  degrades correctly to a single date) — `"booked on October 3rd"` → `True`, `"booked on
  2026-10-03"` → `True`, `"booked on October 7th"` (wrong date) → `False`. All correct; the
  single-date path (handled by `_SPOKEN_DATE_RE`'s own branches, independent of `_DATE_RANGE_RE`)
  works whether or not the real conflict happens to be a range.
- **(c) fresh false-positive hunt** — six new adversarial sentences combining a wrong month with an
  unrelated second number (renovation dates, room numbers, phone extensions, check-in times, nights
  counts, prices). Four of six correctly returned `False` (no false-positive): `"3 new reviews in May
  ... host 5 guests"`, `"costs 3,500 rupees and is 5 minutes ... listed in May"`, `"Plot 3, near a
  hotel that has May 5th"`, `"check-in is 3 PM and minimum stay is 5 nights, listed since May"` — in
  each, the two numbers are never connected by "to"/"through"/"-", so `_DATE_RANGE_RE` correctly
  doesn't treat them as a range and only a bare `_SPOKEN_DATE_RE` single-match (a lone "May 5th"-shaped
  substring, contributing only one day number) would be needed to false-positive, which alone can't
  satisfy the 2-element `{3,5}` subset check. Two of six DID return `True` (`"available May 3-5, but
  check-in fee is 5 dollars"` and `"Renovation ran May 3-5 this year"`) — but on inspection this is
  **not a new bug**: `_DATE_RANGE_RE` matches `"May 3-5"` as its own coherent, structurally valid day
  range (`day1=3, day2=5`), just anchored to the wrong month — exactly the "same-day-number-
  different-month coincidence" tradeoff the `_real_dates_stated` docstring already explicitly names
  and accepts ("not validating calendar math"), not the "unrelated numbers in the same sentence"
  failure mode Task 5.2's finding-5 fixed. Also checked two sentences with a decoy wrong month
  alongside the genuinely correct Oct 3–5 range present elsewhere in the same sentence
  (`"available May 3 to 5, but October 3 to 5 is fine"`, `"Renovated in May, then booked October 3rd
  to 5th"`) — both correctly return `True`, and correctly so, since the real correct range is
  genuinely present in the text either way.
- **(d) confirm case 1/2's independent use of `_SPOKEN_DATE_RE`/`_AVAILABLE_ASSERTION_RE` still
  works** — verified directly: `_AVAILABLE_ASSERTION_RE` still matches "is available"/"are available";
  `_UNAVAILABLE_ASSERTION_RE` still matches "not available"; `_SPOKEN_DATE_RE` still matches a
  wrong single date in both natural ("November 10th") and ISO ("2026-11-10") form, independent of the
  `_DATE_RANGE_RE` refinement — case 1 (false-claim) and case 2 (wrong-date) detection are unaffected
  by the range-matching changes, as expected since they don't call `_real_dates_stated` themselves
  (case 2's gate is a plain `_SPOKEN_DATE_RE.search(sentence)`, only reached after `_real_dates_stated`
  has already returned `False` for that sentence).

Net: the three-round fix is confirmed correct against every false-positive shape from Task 5.2 plus
six new ones of my own; the only reproducible false-positive left is the exact same accepted tradeoff
the docstring already names, not a new hole. One new false-negative gap found ("and"-joined ranges),
narrow and safe-direction, consistent with the two already-accepted gaps in the same family.

#### 3. Test suite — CONFIRMED exact counts, run independently

`pytest tests/test_property_recommendation_guard.py -v`: **44 passed** (28 pre-existing + 16 new:
14 from Task 5.1 + 2 from the Task 5.2 findings-resolved round), including
`test_recommend_properties_reply_stating_unrelated_numbers_near_wrong_month_still_gets_corrected` and
`test_recommend_properties_reply_correctly_naming_partial_dates_with_abbreviated_month_is_not_flagged`,
both present and passing, pinning exactly the false-positive/false-negative fixes described above.

`pytest -q` (full backend suite): **18 failed, 1439 passed** in 127.30s. Failing test names, grouped
by file, match the established baseline exactly: `test_calls_api.py` ×1, `test_database.py` ×5,
`test_email_client.py` ×2, `test_embedding_service.py` ×3, `test_main.py` ×1, `test_ringing_audio.py`
×2, `test_tool_handlers.py` ×2, `test_turn_strategies.py` ×1, `test_voice_ice_servers.py` ×1 — zero
overlap with guard/availability/recommend territory, identical to Task 5.1's and 5.2's own recorded
counts.

#### 4. "No hidden LLM regeneration" invariant — CONFIRMED against the current file

`grep -niE "AsyncGroq|chat\.completions|_build_llm|openai|anthropic\(|\.complete\(|await.*llm"
property_recommendation_guard.py` matches only `LLMFullResponseStartFrame`/`LLMTextFrame` — pipecat
frame *types* the guard emits downstream to TTS with the corrected text, not an LLM invocation. The
entire correction mechanism (all three partial-availability cases, `_real_dates_stated`,
`_DATE_RANGE_RE`, `_SPOKEN_DATE_RE`, plus the pre-existing price/availability/capacity/FAQ checks) is
regex matching and string concatenation against `_pending_partial_facts`/`_pending_options`/etc. —
data recorded synchronously in `record_tool_result` from the tool's own structured result, never a
second model call mid-turn. Invariant holds.

#### 5. Closing cross-implementation sanity check

`git diff --stat dev` shows a coherent 20-file diff (1984 insertions, 134 deletions): all the expected
production files (`system_prompt.py`, `schemas/tool.py`, `calendar_service.py`, `pitch_formatter.py`,
`context_builder.py`, `orchestrator.py`, `tool_handlers.py`, `conversation_state.py`,
`property_recommendation_guard.py`, `tools.py`), matching test files for each, and `docs/agents.md`/
`docs/how-it-works.md` — nothing obviously missing (every implementation area from 1-4 has a
corresponding diff) or duplicated (no file appears twice, no leftover `.orig`/`.bak` artifacts).
Implementation 5's own diff (`property_recommendation_guard.py`, +295 lines) is additive only — the
pre-existing price/availability/capacity/FAQ/UUID-stripping checks are untouched in structure, and the
28 pre-existing guard tests still pass unmodified, confirming nothing from Implementation 5 silently
altered Implementations 1-4's own logic.

One gap noted but out of Task 5.3's own scope: the **closing regression pass**'s own checklist item
(documenting the feature in `documentation/project_state.md`/`documentation/current_architecture.md`)
has not happened yet — grepped both files for "availability-first"/"partial availab" and found no
mention. This is that section's own open item, not a Task 5.3 finding.

#### New findings summary (beyond what Task 5.2 already recorded)

1. **New, non-blocking false-negative**: `_DATE_RANGE_RE`'s connective alternation
   (`to|through|-`) doesn't include "and" — `"October 3rd and 5th"` is a plausible correct rephrasing
   that would still get a redundant (harmless) duplicate correction appended. Same class and severity
   as the already-accepted "Oct 3 through Oct 5"-style gaps; worth folding into a future connective-
   alternation widening pass, not a blocker.
2. No new false-positive found beyond the one already known and explicitly accepted in the
   `_real_dates_stated` docstring (same-day-number, wrong-month coincidence, e.g. "May 3-5" against a
   real Oct 3-5 conflict) — six fresh adversarial sentences targeting the "unrelated numbers in the
   sentence" failure mode Task 5.2 fixed all correctly returned `False`.

#### Verdict

All five of Task 5.3's own checklist items pass: the file reads as a coherent, natural extension with
no abandoned-attempt residue; the date-matching fix independently re-verified correct against new
edge cases (one new narrow, accepted-severity gap found, no new false-positive class); full test
suite green at the exact expected counts (44 guard tests, 18/1439 full-suite baseline); the no-hidden-
LLM-regeneration invariant holds by direct grep of the current file; and the full 20-file diff against
`dev` is coherent with nothing missing or duplicated. **Implementation 5 is DONE. This is the final
gate for the entire 5-implementation "availability-first recommendations" task list's guard-side
work** — the only remaining open item anywhere in the task doc is the closing regression pass's own
`documentation/project_state.md`/`current_architecture.md` update, which is that section's task, not
this one's. `git status` confirmed clean after this reverify pass (only the pre-existing modified
files and the new task doc itself, no stray files left behind).

---

## Closing regression pass (after all five implementations)

- [x] `cd backend && pytest` — full suite green, real Postgres, no mocking. Confirmed the
  pre-existing/environment-dependent failure count is stable and unchanged in identity (same 18 test
  names, `test_calls_api.py`, `test_database.py` ×5, `test_email_client.py` ×2,
  `test_embedding_service.py` ×3, `test_main.py`, `test_ringing_audio.py` ×2, `test_tool_handlers.py`
  ×2, `test_turn_strategies.py`, `test_voice_ice_servers.py`) across every implementation/review/
  reverify run in this task list — final run: 18 failed, 1439 passed. `building-intelligence.md`'s own
  closing pass (the immediately-prior task on this branch) recorded 1393 passed at its own close —
  this task list's net addition is 1439 − 1393 = 46 new tests across all fifteen tasks (implementation
  + review + reverify × 5), with the same 18 pre-existing/environment-dependent failures present,
  unchanged in identity, at every single checkpoint from before Implementation 1 through this closing
  pass. Zero regressions introduced anywhere across all five implementations, fifteen tasks, and every
  fresh-context review/reverify pass.
- [x] Re-read `CLAUDE.md`'s "Critical invariants" section top to bottom and confirmed none were
  violated across all five implementations:
  - "External dependency failures must not unnecessarily terminate a live guest call" — every new DB
    query added in Implementations 2 and 3 (`calendar_service.partial_availability_for_candidates`,
    which superseded and then fully replaced Phase 2.4's original `unavailable_property_ids` — that
    function was found to have zero remaining production callers during Implementation 3's own PR
    review and was removed rather than left as dead code) fails open behind the same `try/except`
    shape the original Phase 2.4 mechanism always used, confirmed independently in Implementation 2's
    reverify, Implementation 3's review, and this closing pass's own re-read of `orchestrator.py`.
  - "Validators must not introduce hidden LLM regeneration" — Implementation 5's guard extension
    (three new partial-availability checks in `property_recommendation_guard.py`) is a deterministic
    regex/string comparison against `_pending_partial_facts`, confirmed by grep for any completion
    call in every implementation/review/reverify pass across Implementation 5 — zero matches every
    time, including this pass's own re-check.
  - "`ConversationState` is responsible for conversation facts/state" — Implementation 1's `nights`
    slot and Implementation 3's `window_start`/`window_end` slots are populated only via `set_slot`
    from the real `update_lead` tool wrapper, never a separate classification pass, and are explicitly
    excluded from `Lead` DB persistence (confirmed via `handle_update_lead`'s `updates.pop(...)` calls
    and independently re-verified fresh-DB-round-trip tests in Implementation 1's own review).
  - "Do not duplicate existing services" — Implementation 3's `partial_availability`/
    `partial_availability_for_candidates` share `_conflicting_bookings_in_window`'s query and reuse
    `is_available`'s exact overlap predicate (`status == "confirmed"`, `check_in < window_end AND
    check_out > window_start`) — confirmed identical, not a second drifted definition, independently
    in Implementation 3's PR review and its own reverify pass.
- [x] Confirmed end-to-end across the task list's own real-transcript testing (not one single
  additional transcript run for this closing pass, since the exact flow described here was already
  exercised piecemeal, with real quoted output, across multiple independent passes): vague window →
  asked for nights before exact dates (Implementation 1's PR review, real Groq completion, quoted);
  recommended only availability-checked properties with no unavailable property surfaced as clean
  (Implementation 2's reverify, real seeded-DB check through the live wrapper chain); a
  partial-availability property described accurately with specific conflicting dates, appended by the
  guard even when the model's own prose omitted them (Implementation 3's reverify 6-completion
  real-LLM sample, and Implementation 5's own guard-level tests proving the correction fires
  end-to-end through the real frame-processing pipeline); guest finalizes exact dates → `check_calendar`
  re-confirms even after an earlier "full" classification off a looser window (Implementation 4's
  reverify, real `gpt-oss-120b` completions, quoted tool calls). The full chain was never run as one
  single unbroken live call in this task list (that would require a real Exotel/browser-test call,
  outside what any of these text-completion-level checks can drive), but every individual transition
  in the chain has real, quoted, independently-reproduced evidence behind it — flagged here explicitly
  as the one verification gap this closing pass could not itself close, rather than silently treating
  the piecemeal evidence as equivalent to a true end-to-end live-call test.
- [x] `documentation/project_state.md` and `documentation/current_architecture.md` updated — see
  below.
