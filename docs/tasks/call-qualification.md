# Call Qualification / Junk-Call Detection — Task List

Full design rationale: `/Users/abhaya/.claude/plans/crispy-kindling-russell.md`. This file tracks execution — one task per unit of work, each with its own verification step to run **before** moving to the next task. Do not batch tasks without verifying in between; a broken foundation (e.g. bad migration) compounds silently otherwise.

Mark `[x]` as each task's verification passes.

---

## Task 1 — DB migration: `call_type` / `classification_confidence` / `classification_reason`

- [x] Add 3 columns to `backend/app/models/call_session.py` (`CallSession`), placed after `urgency`:
  - `call_type: Mapped[str] = mapped_column(String(32), default="UNKNOWN", server_default="UNKNOWN", index=True)`
  - `classification_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))`
  - `classification_reason: Mapped[str | None] = mapped_column(Text)`
- [x] New alembic migration in `backend/alembic/versions/`, modeled on `f3cebf679a80_add_lead_status_occasion_and_unanswered_.py`. Confirm current head first (`alembic heads`) and set `down_revision` to it — do not hardcode `8818413a6d0a` blindly, re-check at implementation time in case something landed since.
  - `upgrade()`: `op.add_column` x3 + `op.create_index('ix_call_sessions_call_type', ...)`
  - `downgrade()`: drop index + 3 columns, reverse order

**Verify before moving on:**
1. `alembic heads` → exactly one head, no branching.
2. `alembic upgrade head` runs clean against the real dev Postgres (no DB mocking, per repo convention).
3. `psql $DATABASE_URL -c "\d call_sessions"` shows all 3 new columns with correct types/defaults.
4. `alembic downgrade -1` then `alembic upgrade head` again — confirms reversibility, leaves DB at head.
5. Spot-check an existing row: `SELECT call_type FROM call_sessions LIMIT 1;` → `'UNKNOWN'`, not null/error.

---

## Task 2 — Classification schema + centralized service

- [x] New file `backend/app/schemas/call_classification.py`: `CallType` (Literal, 7 values), `QUALIFIED_CALL_TYPES` (set), `ClassificationResult` (Pydantic: `call_type`, `confidence`, `reason`).
- [x] New file `backend/app/services/call_classification_service.py`:
  - `_pre_check(transcript, duration_seconds)` — empty transcript or `duration_seconds < 4.0` → `INCOMPLETE`, else `None`.
  - LLM call structure copied from `backend/app/services/discount_policy_service.py` (`_call_groq`/`_call_anthropic`/`_call_openrouter`, JSON mode, provider-priority-then-fallback) — **not** pipecat tool-calling.
  - `_CLASSIFICATION_PROMPT` defining all 6 non-UNKNOWN categories with short examples.
  - Strict response parsing: `json.loads`, normalize/validate `call_type` against the 7 literals, clamp confidence to `[0,1]`.
  - `classify_call(transcript, duration_seconds) -> ClassificationResult` — pure function, no DB access, wraps the LLM call in `asyncio.wait_for(..., timeout=15)` inside a broad `try/except Exception` that degrades to `UNKNOWN` on any failure (never raises).

**Verify before moving on:**
1. Write and run a quick standalone script/REPL (or a pytest unit test if you want it to stick around — recommended, since this module is a pure function and trivially testable) exercising `classify_call` directly with no DB:
   - Empty string transcript → `INCOMPLETE`, confidence 1.0.
   - `duration_seconds=2.0` with some transcript → `INCOMPLETE`.
   - A realistic booking-enquiry transcript (e.g. "Hi, do you have availability for 2 nights next week, what's the price?") → `BOOKING_LEAD` (or `GENERAL_QUERY` at worst), confidence > 0.5, non-empty reason.
   - A telemarketing/spam-style transcript ("Hello sir, I am calling from XYZ insurance...") → `JUNK`.
   - Temporarily break the API key / force a provider error → `UNKNOWN`, confidence 0.0, **does not raise**.
2. Confirm no DB session is opened anywhere in this module (grep for `AsyncSession`/`db.` — should be absent).

---

## Task 3 — Persist classification: `call_service.set_call_classification`

- [x] Add `set_call_classification(db, call_session_id, classification: ClassificationResult) -> None` to `backend/app/services/call_service.py`, same style as `finalize_call_session` (fetch by id, assign 3 fields, `await db.commit()`, no-op if `call_session_id` or the row is `None`).

**Verify before moving on:**
1. Quick manual check against a real (non-prod-critical, e.g. most recent test/browser-test) `call_session_id` in the dev DB: call `set_call_classification` with a fabricated `ClassificationResult`, then `SELECT call_type, classification_confidence, classification_reason FROM call_sessions WHERE id = '...'` and confirm the values landed.
2. Call it with a `call_session_id=None` and confirm it returns cleanly with no exception and no DB write.

---

## Task 4 — Lead suppression: `lead_service.delete_for_unqualified_call`

- [x] Add `delete_for_unqualified_call(db, call_session_id) -> None` to `backend/app/services/lead_service.py`, sibling to (not modifying) `delete_if_empty` — looks up `Lead` by `call_session_id`, deletes unconditionally if found, no-op if not.

**Verify before moving on:**
1. In the dev DB, find or create a `Lead` row tied to some `call_session_id` (e.g. via the existing `update_lead` tool flow in a browser test call, or a direct insert). Call `delete_for_unqualified_call` on that `call_session_id` and confirm the row is gone (`SELECT * FROM leads WHERE call_session_id = '...'` → empty).
2. Call it again on the same (now-empty) `call_session_id` — confirm it no-ops without error.
3. Call it on a `call_session_id` that never had a lead — confirm no-op, no error.

---

## Task 5 — Wire into `on_pipeline_finished`

- [x] In `backend/app/voice/pipeline.py`'s `on_pipeline_finished` handler, insert (in order, all inside the existing `async with AsyncSessionLocal() as ...` block):
  1. `finalize_call_session(...)` — unchanged, keep as first.
  2. Compute `duration_seconds` — check what's already available in this closure (`started_at`/`ended_at` locals vs. needing the just-updated `CallSession` row); use whichever avoids inventing a second source of truth.
  3. `classification = await call_classification_service.classify_call(transcript, duration_seconds)`
  4. `await call_service.set_call_classification(db, call_session_id, classification)`
  5. Existing backfill/`delete_if_empty` branch — unchanged.
  6. New: `if classification.call_type not in QUALIFIED_CALL_TYPES: await lead_service.delete_for_unqualified_call(db, call_session_id)` — after step 5, unconditional on which branch fired.
  7. Confirm the existing fire-and-forget `guest_memory_service.update_guest_memory_from_call` task creation stays **after** this whole block, unchanged.

**Verify before moving on:**
1. Read back the diff and confirm sequencing matches: finalize → classify → persist → backfill/delete_if_empty → suppress-if-unqualified → (fire-and-forget guest memory, untouched).
2. Run a real end-to-end call via the dashboard's "Talk to Mira" browser test (`/api/v1/voice/test/offer`) — do one **short/silent** test (connect and hang up within ~2s) and one **real booking-style conversation**.
3. After each call, query the DB directly:
   - Short/silent call → `call_sessions.call_type = 'INCOMPLETE'`, and confirm no `Lead` row exists for its `call_session_id` (or was deleted if one got created).
   - Real conversation → `call_sessions.call_type` is a sensible qualified value, `classification_confidence`/`classification_reason` populated, and if a `Lead` was created via `update_lead`/`escalate_to_host` during the call, it still exists.
4. Check backend logs for the short call — confirm no unhandled exception/traceback from the classification step, and that `on_pipeline_finished` completed (no hang).

---

## Task 6 — API: `GET /calls` filter + schema fields

- [x] `backend/app/api/v1/calls.py`: add `call_type: str | None = Query(default=None)` (comma-separated), split + `CallSession.call_type.in_(types)` filter, following the exact `status_filter`/`urgency` pattern already in this file.
- [x] `backend/app/schemas/call_session.py`: add `call_type: str`, `classification_confidence: float | None`, `classification_reason: str | None` to `CallSessionOut`.

**Verify before moving on:**
1. Restart uvicorn (confirm it's actually restarted — `.py` changes need `--reload` or a manual restart per this repo's known pitfall), then check `GET /openapi.json` (or interactive `/docs`) shows the new `call_type` query param and the 3 new response fields on `CallSessionOut`.
2. `curl` (with a valid auth token) `GET /api/v1/calls?call_type=JUNK` and `GET /api/v1/calls?call_type=BOOKING_LEAD,GENERAL_QUERY,GUEST_SUPPORT,EXISTING_BOOKING` against the dev DB — confirm each returns only rows matching the requested `call_type` set, and that the returned JSON includes `call_type`/`classification_confidence`/`classification_reason`.
3. `curl GET /api/v1/calls` with no `call_type` param — confirm it still returns everything (no accidental default filter).

---

## Task 7 — Dashboard analytics: `qualified_calls` stat

- [x] `backend/app/api/v1/analytics.py`: add a `qualified_calls` count to `GET /analytics/summary`'s response, using the existing `call_filters`-list-then-count pattern plus `CallSession.call_type.in_(QUALIFIED_CALL_TYPES)`.

**Verify before moving on:**
1. `curl GET /api/v1/analytics/summary` (auth'd) — confirm `qualified_calls` appears in the response and is a sane number (`<=  total_calls`).
2. Cross-check by hand: run the equivalent `SELECT count(*) FROM call_sessions WHERE user_id = ... AND call_type IN (...)` directly against the DB and confirm it matches the API's number.
3. Confirm `pipeline_value`/`open_leads`/`total_calls`/`completed_calls` are unchanged in value/shape from before this task (no regression) — compare against a `curl` taken before this task's edit if possible, or just sanity-check they still look reasonable.

---

## Task 8 — Frontend: new tone variants (`purple`, `orange`)

- [x] Before touching frontend code: check `frontend/AGENTS.md` — this Next.js version has non-standard APIs vs. training data; skim `node_modules/next/dist/docs/` if anything here touches routing/data-fetching conventions.
- [x] `frontend/src/lib/tone.ts`: add `"purple"` and `"orange"` to `StatusTone`, with one entry each in `toneClassName`, `toneDotClassName`, `toneBadgeVariant`, `toneCssVar`.
- [x] `frontend/src/app/globals.css`: add `--status-purple`/`--status-purple-bg`, `--status-orange`/`--status-orange-bg` custom properties and `.badge-status-purple`/`.badge-status-orange` classes, matching the exact structure of the existing `.badge-status-live`/`-pending`/`-progress` blocks.

**Verify before moving on:**
1. `npm run build` (or `tsc --noEmit`) in `frontend/` — confirm no type errors from the `StatusTone` union expansion (this will surface any place that exhaustively switches over `StatusTone` and needs a new case).
2. Temporarily render a `<StatusChip status="test" tone="purple" />` and `tone="orange"` somewhere reachable (e.g. a scratch spot on an existing page, or Storybook if present) and visually confirm both render distinct, sensible colors in light **and** dark mode, then remove the scratch usage.

---

## Task 9 — Frontend: Call Logs badges + Type column

- [x] Define `callTypeTone`/`callTypeLabel` maps (per the color/label table in the plan) in `calls-table.tsx` or a new `frontend/src/lib/call-type.ts`.
- [x] Add a "Type" column to `calls-table.tsx` (non-`compact` mode only), rendering `<StatusChip>` via the new maps.
- [x] `frontend/src/lib/types.ts`: add `call_type`, `classification_confidence`, `classification_reason` to the `CallSessionOut` type; export `CallType` union.
- [x] `frontend/src/lib/api.ts`: add `callType?: string` param to `calls.list`, threaded as `call_type` in the query string.

**Verify before moving on:**
1. `npm run dev`, open the Call Logs page for a host account with a mix of real call types (use the test calls from Task 5, plus any historical ones now defaulted to `UNKNOWN`).
2. Visually confirm: all 7 `call_type` values render with the correct color per the plan's table (BOOKING_LEAD/GENERAL_QUERY green, GUEST_SUPPORT blue, EXISTING_BOOKING purple, INCOMPLETE orange, UNKNOWN grey, JUNK red), correct label text, in both light and dark mode.
3. Confirm the Overview page's compact "Recent calls" card (which shares `calls-table.tsx`) did **not** grow the new Type column (compact mode should hide it) — no layout regression there.

---

## Task 10 — Frontend: filter tabs + Hidden Filters chip

- [x] `frontend/src/app/dashboard/calls/page.tsx`: add the filter-tab list (All Calls / Qualified Calls / Booking Leads / Guest Support / Existing Guests / Incomplete / Junk / Unknown) mapping to `call_type` query values (comma-joined for "Qualified Calls").
- [x] Add `showUnknown`/`showJunk`/`showIncomplete` state, a `Popover`-based "Hidden Filters (N)" chip with 3 `Switch`+`Label` pairs (reuse existing `popover.tsx`/`switch.tsx` — no new UI primitive), modeled on `leads/page.tsx`'s `isEmptyLead`/`showEmpty` pattern.
- [x] Filtering applied server-side (effective `call_type` list passed into `api.calls.list`), not client-side over-fetch.
- [x] Disable/grey the chip when the active tab already targets Unknown/Junk/Incomplete directly.

**Verify before moving on:**
1. `npm run dev` — confirm default view ("All Calls", nothing expanded) hides Unknown/Junk/Incomplete calls, and the chip reads "Hidden Filters (3)".
2. Expand the chip, toggle "Show Junk" on — confirm Junk calls appear without a page reload glitch, and the chip count updates to "Hidden Filters (2)".
3. Click the "Junk" tab directly — confirm it shows only Junk calls and the Hidden Filters chip is disabled/greyed for this view.
4. Click "Qualified Calls" — confirm it shows the union of BOOKING_LEAD/GUEST_SUPPORT/EXISTING_BOOKING/GENERAL_QUERY only.
5. Confirm network tab shows the `call_type` query param actually being sent to `/api/v1/calls` (server-side filtering, not client-side).

---

## Task 11 — Full end-to-end regression pass

- [x] Run backend test suite: `cd backend && pytest` (real Postgres, no mocking, per repo convention) — confirm no existing tests broke.
- [x] Manual pass through the whole flow one more time: make a junk-ish test call and a real booking-style test call via "Talk to Mira", confirm both land correctly in Call Logs (badges, filters) and only the qualified one shows in the Leads page.
- [x] Confirm dashboard Overview stats (`pipeline_value`, `open_leads`, `total_calls`) still look correct and unaffected for an existing host account.
- [x] Re-read the "Accepted edge case" note in the plan (Section 4) — confirm you understand the tiny Lead-visibility race window and are not trying to "fix" it as part of this task list (it's intentionally accepted).

**Verify before closing out:**
1. `pytest` green.
2. No console errors in the browser during the manual pass.
3. Git diff reviewed end-to-end for anything unintended (e.g. stray debug prints, leftover scratch `StatusChip` test usage from Task 8).
