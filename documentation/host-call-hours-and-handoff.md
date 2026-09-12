# Host call hours (account-global) + configurable live-ownership handoff

Status: **implemented and merged** (`ef133a6`, `d331be9`, PR #52). Part A6's cleanup — removing the
temporary `FIXED_HOST_HOURS_*` env-var override now that the account-global Settings path is the
only routing input — has also landed; see §0.2 below, now historical.

This doc is the design/implementation plan for two related changes:

1. **Dynamic, account-global host call hours** — one editable time-of-day window, set on the
   Settings page, during which inbound guest calls are routed to the host's phone instead of Mira.
   Common to every property on the account (deliberately *not* per-property).
2. **Configurable live-ownership handoff** — the existing "guest is calling Mira → host taps *Take
   Call* → Mira transfers the live call to the host's phone" flow, with (a) the spoken handoff line
   made host-configurable, and (b) a new field on the AI Training tab to edit it.

Both build on infrastructure that **already exists in the code** but is currently either shadowed by
a temporary override or hardcoded. Nothing here is greenfield telephony work.

---

## 0. What already exists (read this first)

The Call Ownership Schedule (Phases 1–4, 8) and the live Mira→host handoff / *Take Call* (Phases
5–7) are fully built. They are **only** documented in code comments today — this doc is the first
design-level writeup. Summary of the moving parts:

### 0.1 The pure decision function

`backend/app/services/call_ownership.py`

- `resolve_effective_call_owner(property_, current_time_utc) -> CallOwner` (`HOST` | `MIRA`).
  Pure: no DB, no I/O, no logging, no mutation. Same inputs → same output.
- Reads `Property.call_handling_mode` (`"MIRA"` / `"HOST"` / `"SCHEDULED"`),
  `Property.call_handling_schedule_start` / `_end` (`"HH:MM"` strings),
  `Property.timezone` (IANA id).
- `_time_in_half_open_interval(value, start, end)` handles both same-day windows (`09:00–17:00`)
  and overnight-wrap windows (`22:00–06:00`) with one comparison, no date arithmetic.
  `start == end` → empty interval → always `MIRA` (tested).
- `InvalidCallOwnershipConfigError` — raised (never a silent default) on a malformed stored config.
  In practice unreachable because `app/schemas/property.py`'s `PropertyUpdate` validators reject bad
  values at write time.

### 0.2 The TEMPORARY global override (REMOVED — historical)

`call_ownership.py` lines ~31–40 and ~135–143, `app/config.py` (`fixed_host_hours_start` /
`fixed_host_hours_end`), `render.yaml` (`FIXED_HOST_HOURS_START` / `FIXED_HOST_HOURS_END`) —
**all removed**. This section is kept for history; nothing below still applies to the current code.

- While it existed: if both env vars were set, `resolve_effective_call_owner` bypassed every
  `Property` column and forced one hardcoded `Asia/Kolkata` HOST window for every property.
- It had been active in production (`render.yaml` set `11:00` / `17:00`) — every host was on an
  11 AM–5 PM IST host window, IST-only, not editable from the dashboard, **regardless of what that
  host's own `host_call_hours_*` Settings said**.
- The per-property Call Ownership editor in the UI was `disabled` while this override was live (see
  `frontend/src/components/settings/call-ownership-card.tsx`'s `FixedHoursBanner` — that card has
  since been replaced entirely by `HostCallHoursCard`, see Part A5).
- Deleted once per-account dynamic config existed — which was exactly Part A of this
  plan.

### 0.3 Exotel call routing

`backend/app/api/v1/webhooks/exotel.py`

- `GET /webhooks/exotel/call-routing` (Phase 4) — a synchronous Passthru applet placed **before**
  the Voicebot applet in the Exotel flow (human-configured in the Exotel console, not by this
  codebase). Answers a binary decision by HTTP status: `200` → continue to Mira's Voicebot,
  `302` → continue to the Connect applet (route to host). Calls `resolve_effective_call_owner`.
  Fail-closed to `200`/MIRA on every error path.
- `GET /webhooks/exotel/connect-routing` (Phase 8) — the Connect applet's dynamic Primary URL
  target. Returns `{"destination": {"numbers": ["<host phone>"]}}`. The host phone is read from
  `User.phone` via the property → owner chain (`_resolve_and_respond`), normalized by
  `_normalize_destination_phone`. Fail-closed to an empty numbers list.

### 0.4 The "guest is calling Mira" notification + live *Take Call* handoff

- `backend/app/services/guest_calling_notification.py` — `maybe_notify_guest_calling(...)`, fired
  fire-and-forget from `app/voice/pipeline.py` right after a real `CallSession` is created for a
  MIRA-owned call. Re-checks `resolve_effective_call_owner` (must be `MIRA`), then:
  - creates an in-app `Notification` (`channel="guest_calling"`, idempotent on
    `(call_session_id, channel)`),
  - sends the host a WhatsApp (`twilio_guest_calling_template_sid`, or a plain-text fallback)
    containing a **signed, single-use *Take Call* link**.
- `backend/app/services/take_call_token.py` — `issue_take_call_token` / `verify_take_call_token`,
  HMAC-signed, short-lived, scoped to exactly one `(call_session_id, property_id, host_user_id)`.
  Secret: `settings.take_call_token_secret`.
- `backend/app/api/v1/take_call.py` — `GET /api/v1/take-call` renders a no-JS confirmation page;
  `POST /api/v1/take-call` performs the **atomic claim**
  (`UPDATE call_sessions SET handoff_status='requested' WHERE id=? AND handoff_status IS NULL AND
  status IN ('in_progress')`), then calls `handoff_signal.request_handoff`.
- `backend/app/voice/handoff_signal.py` — one `asyncio.Event` per live call, keyed by
  `call_session_id`. **In-process only** (documented limitation): works because the backend runs a
  single uvicorn worker. Multi-worker deployment would need Redis pub/sub — out of scope.
- `backend/app/voice/pipeline.py`:
  - `_HOST_HANDOFF_PHRASE = "Excuse me for a moment while the host takes up your query."`
    (hardcoded constant — **this plan makes it configurable**).
  - `_HOST_HANDOFF_END_REASON = "host_handoff"` — `EndFrame.reason` sentinel.
  - `_wait_and_trigger_handoff(worker, call_session_id)` — background task, started in
    `_run_pipeline_inner` (only when `property_id is not None`), cancelled in that function's
    `finally`. Blocks on `wait_for_handoff_request` until the host claims *Take Call*, then queues
    `TTSSpeakFrame(_HOST_HANDOFF_PHRASE, append_to_context=False)` followed by
    `EndFrame(reason=_HOST_HANDOFF_END_REASON)`.
  - `on_pipeline_finished` special-cases a host-handoff `EndFrame`: it ends the Voicebot leg
    **without hanging up the Exotel call**, so Exotel's flow continues to the Connect applet, which
    dials the host.
- `CallSession.handoff_status` — `String(16)` nullable, `app/models/call_session.py`. Intended
  value set: `NULL` / `"requested"` / `"connecting"` / `"connected"` / `"failed"`. Only
  `take_call.py` writes `"requested"` today; nothing writes the later values yet.

### 0.5 Where host settings live

- **No `Settings` / `HostSettings` / `UserSettings` model exists.** Host-account settings are
  columns on `User` (table `users`), saved via `PATCH /api/v1/auth/me` (`app/api/v1/auth.py`,
  generic `setattr` loop over `UserUpdate` fields).
- `app/config.py`'s `Settings` is pydantic-settings / env only — not per-host.
- Host phone for any transfer = `User.phone` (free text, `String(32)`, no format validator;
  sanitized only at the point of use).
- Existing agent-personalization text fields on `User`: `agent_first_message`, `agent_persona`,
  `agent_escalation_phrase` (rejects a "loop in the host" variant via `_LOOP_IN_HOST_RE`),
  `agent_voice_gender`, `agent_language_policy`. All edited on the AI Training page.

---

## Part A — Account-global host call hours

**Decision (confirmed with product):** account-global only. Retire the per-property Call Handling
UI. Keep the per-property `call_handling_*` columns in the schema as dead-but-present (same staged
-removal discipline as the `CallLease` Postgres table).

### A1. Data model — new columns on `User`

`backend/app/models/user.py`:

| Column | Type | Default | Meaning |
|---|---|---|---|
| `host_call_hours_enabled` | `Boolean` | `false` (`server_default="false"`) | Master switch. Off ⇒ Mira answers 24/7 (today's default when the env override is unset). |
| `host_call_hours_start` | `String(8)` nullable | — | `"HH:MM"` 24h. Window start — calls go to the host's phone from here. |
| `host_call_hours_end` | `String(8)` nullable | — | `"HH:MM"` 24h. Overnight wrap allowed (reuses `_time_in_half_open_interval`). |
| `host_call_hours_timezone` | `String(64)` | `"Asia/Kolkata"` (`server_default`) | IANA tz the window is evaluated in. |

**Why a dedicated `host_call_hours_timezone` and not reuse `User.timezone`:** `User.timezone` has no
IANA validator today and is used elsewhere as a display/date-anchor. A field that gates live call
routing should carry its own validated column — same reasoning `Property.timezone`'s own validator
comment gives for not reusing `User.timezone`.

**Alembic migration:** new revision, `down_revision = 'f041738fce4c'` (current head —
`add_call_quality_events`). Add all four columns with server defaults so existing rows stay valid.

### A2. Schemas — `backend/app/schemas/user.py`

- `UserUpdate`: add all four fields (`Optional`). Validators, mirroring
  `app/schemas/property.py` lines ~146–210:
  - `host_call_hours_start` / `_end`: `"HH:MM"` 24h format check. Reuse the `_HH_MM_RE` pattern
    (`^([01]\d|2[0-3]):[0-5]\d$`) — extract it to a shared util module (e.g.
    `app/utils/timewindow.py`) or duplicate it (the codebase already duplicates `_LOOP_IN_HOST_RE`).
  - `host_call_hours_timezone`: `ZoneInfo(value)` — raise `ValueError` on `ZoneInfoNotFoundError`.
  - `model_validator(mode="after")`: if this payload sets `host_call_hours_enabled=True`, require
    both `host_call_hours_start` and `host_call_hours_end` to be present in the **same** payload
    (matches the `PropertyUpdate` precedent — don't try to consult the persisted row).
- `UserOut`: add all four (the Settings form reads them back).

### A3. Resolver — `backend/app/services/call_ownership.py`

Change the signature to take the host:

```python
resolve_effective_call_owner(property_: Property, host: User, current_time_utc: datetime) -> CallOwner
```

Precedence inside the function (current, post-A6 cleanup — the env override described in earlier
drafts of this doc no longer exists):

1. **`host.host_call_hours_enabled`** — if `True`, evaluate `host_call_hours_start` /
   `_end` in `host_call_hours_timezone` using the existing `_parse_hh_mm` +
   `_time_in_half_open_interval`. Inside the window → `HOST`, outside → `MIRA`. This **replaces**
   reading `property_.call_handling_mode` entirely.
2. If `host_call_hours_enabled` is `False` → return `MIRA` unconditionally (24/7).
   **Do not fall through** to the per-property `call_handling_mode` columns — "retire per-property"
   means the resolver stops reading them.

Keep `InvalidCallOwnershipConfigError` for malformed stored values (defensive contract for direct
callers / rows written outside the API).

Update the three call sites to load and pass `host`:

| Call site | File | Note |
|---|---|---|
| `exotel_call_routing` | `app/api/v1/webhooks/exotel.py` (~line 177) | Already has `property_`; add `host = await db.get(User, property_.user_id)`. |
| `exotel_connect_routing` | `app/api/v1/webhooks/exotel.py` (~line 374) | `_resolve_and_respond` already loads the host (~line 408) — reorder so it's loaded before the resolver call. |
| `_maybe_notify_guest_calling` | `app/services/guest_calling_notification.py` (~line 120) | `host` is loaded ~line 136 — move it above the resolver call. |

All three already have fail-closed-to-MIRA `except` blocks — unchanged.

### A4. Backend API

`PATCH /api/v1/auth/me` (`app/api/v1/auth.py`) already does a generic `setattr` loop over
`UserUpdate` fields — **no endpoint code changes** once the schema fields exist.

### A5. Frontend

**Settings page** — `frontend/src/app/dashboard/settings/page.tsx`:

- **Replace** `<CallOwnershipCard />` with a new `HostCallHoursCard`
  (`frontend/src/components/settings/host-call-hours-card.tsx`):
  - A toggle bound to `host_call_hours_enabled`.
  - When on: two `<input type="time">` (start / end) + a timezone `<Select>`.
  - Timezone list: extract the curated `TIMEZONES` array currently inlined in
    `call-ownership-card.tsx` (~lines 24–35) to a shared module
    `frontend/src/lib/timezones.ts` and import it here.
  - Copy: *"During these hours, guest calls go to your phone instead of Mira. Outside them, Mira
    answers. Applies to every property on your account."* Note overnight windows (e.g. 10 PM–6 AM)
    are allowed and stay in effect across midnight.
  - Live preview line (reuse `formatHourMinute` from the old card).
  - Saves via `api.auth.updateMe({ host_call_hours_enabled, host_call_hours_start,
    host_call_hours_end, host_call_hours_timezone })` then `refreshUser()`.
- **Delete** `frontend/src/components/settings/call-ownership-card.tsx` and its import.

**Property form** — `frontend/src/components/property-form-fields.tsx`:

- Remove the entire "Call Handling" section (the `CALL_HANDLING_MODES` block + schedule `<input
  type="time">`s + timezone `<Select>`, ~lines 236–300).
- Remove the now-unused `CALL_HANDLING_MODES` constant and related imports.

**Types** — `frontend/src/lib/types.ts`:

- Add `host_call_hours_enabled` / `host_call_hours_start` / `host_call_hours_end` /
  `host_call_hours_timezone` to the `User` / `UserOut` type and the update-payload type.
- Leave `CallHandlingMode` and the `call_handling_*` fields on the `Property` type in place
  (columns still exist, `PropertyOut` still returns them) — add a
  `// deprecated: superseded by account-global host call hours` comment.

**API client** — `frontend/src/lib/api.ts`: confirm `updateMe`'s body type allows the new fields.
`properties.update` no longer needs the `call_handling_*` keys but leaving them is harmless.

### A6. Rollout / cleanup — DONE

- ~~Remove `settings.fixed_host_hours_start` / `_end`, the override branch in `call_ownership.py`,
  and the `FIXED_HOST_HOURS_START` / `_END` keys in `render.yaml`~~ — done. The account-global
  Settings path (`User.host_call_hours_*`) is now the only routing input; no env var can override or
  shadow what a host has saved. `config.py`'s comment on the (now-removed) setting points here.
- The per-property `call_handling_mode` / `call_handling_schedule_start` / `_end` columns:
  kept in schema, no writer, dropped in a later dedicated migration (`CallLease` precedent) — still
  outstanding, unrelated to the env-override removal above.

### A7. Tests

- `backend/tests/test_call_ownership.py` — extend for the host-level branch:
  - `host_call_hours_enabled=True` + now inside window → `HOST`
  - enabled + outside window → `MIRA`
  - enabled + overnight-wrap window, now in each half → correct
  - `host_call_hours_enabled=False` → `MIRA` regardless of `Property.call_handling_mode`
  - invalid `host_call_hours_timezone` / malformed time → `InvalidCallOwnershipConfigError`
  - env override set → still wins over the per-account setting
- Webhook tests — resolver now needs `host`; update fixtures.
- `backend/tests/` schema tests — `UserUpdate` validators for the new fields.

---

## Part B — Configurable live-ownership handoff

**Decision (confirmed with product):** wire up / verify the existing flow, make the phrase
host-configurable via a **new, separate** AI Training field (not a reuse of
`agent_escalation_phrase` — escalation = host follows up later; handoff = host joins the live call
now).

### B1. Verify the existing flow (deploy-time, not code)

The flow described in §0.4 is fully built. Before it can actually dial a host on a real call, the
following must be true — call these out as a deploy checklist in the PR:

- The Exotel Connect applet's dynamic Primary URL points at `/webhooks/exotel/connect-routing`
  (Exotel console, human config).
- The Passthru applet for `/webhooks/exotel/call-routing` sits **before** the Voicebot applet in
  the flow.
- The `{"destination": {"numbers": [...]}}` response shape matches what this account's Connect
  applet actually expects (flagged as unverified in `exotel.py`'s own docstring —
  `_empty_destination_response` / `_destination_response`).
- Exotel Passthru parameter names — `exotel.py` accepts `From`/`To` and `CallFrom`/`CallTo`
  aliases defensively because they're unverified against a live account. Confirm which the account
  sends.
- `TWILIO_GUEST_CALLING_TEMPLATE_SID` is set (else the plain-text fallback is used, which only
  reaches numbers that have joined the Twilio WhatsApp sandbox).
- Backend runs a **single** web worker (`handoff_signal` is in-process). If Render scales to >1,
  the signal needs Redis pub/sub — separate work, out of scope, but note it.
- `TAKE_CALL_TOKEN_SECRET` is set to a real value (defaults to `"change-me"`).

### B2. Reword the phrase + make it host-configurable

**New `User` column** (`backend/app/models/user.py`, same migration as A1):

| Column | Type | Meaning |
|---|---|---|
| `agent_handoff_phrase` | `Text` nullable | What Mira says right before transferring a live call to the host. `None` ⇒ default. |

**New default** — add next to `DEFAULT_ESCALATION_PHRASE` / `DEFAULT_CLOSING_PHRASE` in
`backend/app/prompts/system_prompt.py` (or keep in `pipeline.py`; system_prompt.py is more
consistent with the other two defaults):

```python
DEFAULT_HOST_HANDOFF_PHRASE = "Hold on — the host is available now. I'm passing the call to them."
```

Replace the hardcoded `_HOST_HANDOFF_PHRASE` constant in `pipeline.py`.

**Threading the configured value to the speak site:**

- `_wait_and_trigger_handoff(worker, call_session_id)` → add a `handoff_phrase: str` parameter.
  Use it in the `TTSSpeakFrame(...)` call (keep `append_to_context=False`).
- Its `asyncio.create_task(_wait_and_trigger_handoff(...))` call is inside `_run_pipeline_inner`,
  which today receives only `system_prompt` (a pre-built string) and `host_user_id` — **not** the
  `host` object. **Preferred fix:** add a `host_handoff_phrase: str` parameter to
  `_run_pipeline_inner` and `_run_pipeline`, resolved in the outer `_run_pipeline` where `host` is
  already loaded (the `build_system_prompt` call sites) as
  `host.agent_handoff_phrase or DEFAULT_HOST_HANDOFF_PHRASE`. Pass `DEFAULT_HOST_HANDOFF_PHRASE` for
  the Lead Agent / browser-test paths (they never register a handoff anyway —
  `handoff_registered = property_id is not None`).
  - *Rejected alt:* re-load `host` inside `_wait_and_trigger_handoff` via a fresh
    `AsyncSessionLocal()` — adds a DB hit on the handoff path for a value known at pipeline start.

**Guardrails** (mirror the existing `agent_escalation_phrase` treatment):

- `UserUpdate` validator for `agent_handoff_phrase`: reject `_LOOP_IN_HOST_RE` matches (same as the
  existing `agent_escalation_phrase` validator in `app/schemas/user.py`).
- At the **read** site (`host.agent_handoff_phrase or DEFAULT_HOST_HANDOFF_PHRASE`), also run the
  `_LOOP_IN_HOST_RE` → fall-back-to-default check, exactly as
  `system_prompt._persona_and_escalation_sections` already does for the escalation phrase —
  protects rows written before the validator existed.
- This phrase is spoken deterministically via `TTSSpeakFrame`, **not** LLM-generated, so it does
  not interact with `EscalationPhraseGuardProcessor` (which only fires after the `escalate_to_host`
  tool — a different code path). No conflict.

**`UserOut`:** add `agent_handoff_phrase`.

### B3. AI Training tab — new field

`frontend/src/app/dashboard/properties/ai-training/page.tsx`, in the "Voice agent personalization"
card, immediately **after** the existing "Escalation phrase" field:

```tsx
<div className="space-y-2">
  <Label htmlFor="agent_handoff_phrase">Live call handoff phrase</Label>
  <DictationTextarea
    id="agent_handoff_phrase"
    placeholder="e.g. Hold on — the host is available now. I'm passing the call to them."
    value={handoffPhrase}
    onValueChange={setHandoffPhrase}
  />
  <p className="text-xs text-muted-foreground">
    Said when you tap "Take Call" on a live call and Mira transfers it to your phone. Different from
    the escalation phrase above, which is for when the host follows up later.
  </p>
</div>
```

- Add `handoffPhrase` state seeded from `user?.agent_handoff_phrase ?? ""`.
- Add `agent_handoff_phrase: handoffPhrase || null` to the `updateMe` payload in
  `handleSavePersonalization`.
- `frontend/src/lib/types.ts`: add `agent_handoff_phrase: string | null` to the `User` / `UserOut`
  type + update-payload type.

The `placeholder=` attribute shows the example wording; the field is fully wired (real column, real
save), not a visual stub.

### B4. Tests

- `backend/tests/test_host_handoff.py` — assert the spoken phrase equals `host.agent_handoff_phrase`
  when set, the default when unset, and the default when a `_LOOP_IN_HOST_RE`-matching value is
  stored.
- `backend/tests/` — `UserUpdate` validator test for `agent_handoff_phrase`.

---

## Files touched (summary)

### Backend

- `app/models/user.py` — 5 new columns (`host_call_hours_*` ×4, `agent_handoff_phrase`)
- `alembic/versions/<new>.py` — migration, `down_revision = 'f041738fce4c'`
- `app/schemas/user.py` — `UserUpdate` + `UserOut` fields + validators
- `app/services/call_ownership.py` — resolver signature (`+ host: User`) + host-level branch,
  stop reading `Property.call_handling_*`
- `app/api/v1/webhooks/exotel.py` — pass `host` to the resolver (2 call sites)
- `app/services/guest_calling_notification.py` — pass `host` to the resolver
- `app/voice/pipeline.py` — `DEFAULT_HOST_HANDOFF_PHRASE`, thread `host_handoff_phrase` through
  `_run_pipeline` → `_run_pipeline_inner` → `_wait_and_trigger_handoff`
- `app/prompts/system_prompt.py` — house `DEFAULT_HOST_HANDOFF_PHRASE` + reuse `_LOOP_IN_HOST_RE`
  fallback for it
- (maybe) `app/utils/timewindow.py` — shared `_HH_MM_RE` + `_time_in_half_open_interval` if worth
  de-duplicating
- `tests/` — `test_call_ownership.py`, `test_host_handoff.py`, schema tests

### Frontend

- `src/components/settings/host-call-hours-card.tsx` — **new**
- `src/components/settings/call-ownership-card.tsx` — **delete**
- `src/app/dashboard/settings/page.tsx` — swap the card
- `src/components/property-form-fields.tsx` — remove the "Call Handling" section
- `src/app/dashboard/properties/ai-training/page.tsx` — new "Live call handoff phrase" field
- `src/lib/timezones.ts` — **new** (extracted shared list)
- `src/lib/types.ts` — new `User` fields; deprecate the `Property.call_handling_*` fields
- `src/lib/api.ts` — verify `updateMe` body type

### Config / docs

- `render.yaml` — `FIXED_HOST_HOURS_*` keys **removed**.
- `app/config.py` — the `fixed_host_hours_*` settings fields are **removed**; comment now explains
  the retirement and points here.
- `documentation/current_architecture.md`, `documentation/project_state.md` — reflect the new model
- `docs/database.md` / `docs/agents.md` / `docs/api.md` — new `User` columns, resolver change,
  `PATCH /auth/me` fields (do this when the change lands, not before)

---

## Open decisions / risks

1. ~~**`FIXED_HOST_HOURS_*` env override is active in production**~~ — **resolved**: the override has
   been removed entirely (`render.yaml` keys, `config.py` fields, and the override branch in
   `call_ownership.py` are all gone). Routing now depends solely on each host's own
   `host_call_hours_enabled/_start/_end/_timezone`, saved from the Settings page — **off by default**
   for every account (the migration's `server_default='false'`), so a host must explicitly enable it
   or Mira answers 24/7.
2. **Retiring per-property call hours is a behaviour change** for any host who set a per-property
   `SCHEDULED` mode. Given the override forces everyone to one window today, in practice no host has
   a live per-property schedule — but run `SELECT count(*) FROM properties WHERE call_handling_mode
   != 'MIRA'` before merging to confirm.
3. **Multi-worker deployment** breaks `handoff_signal` (in-process). Confirm Render runs 1 web
   worker. Out of scope to fix, in scope to note.
4. **Exotel Connect applet config is manual console work** — the live handoff won't actually dial
   the host until it's set up, regardless of the code. Deploy-time task (B1 checklist).
5. **`ensure_lead_for_engagement` / lead-preservation invariant** — a call routed to `HOST` before
   Mira is ever involved (Phase 4 `302`) produces no `CallSession`, no `Lead`. That's existing
   behaviour, not introduced here, but worth confirming product is OK that a host-hours call the
   host misses does not create a recovery Lead (Busy Call Recovery only covers the *busy* rejection
   path, not the *routed-to-host* path).

6. **Calls-tab visibility for host-hours-routed calls** — still open, and the same blocker as #4/#7
   below. The call-outcome labelling work (`CallType` outcome labels, Sep 2026) made every call
   that reaches Mira visible on the Calls tab with a correct outcome label — including busy
   rejections (`MISSED_AGENT_BUSY`, via `call_service.record_busy_rejected_call` from both the
   Exotel and Twilio `BUSY_RECOVERY` branches), silence timeouts (`UNRESPONSIVE`), pipeline
   crashes (`MISSED_SYSTEM_FAILURE`, via `_run_pipeline`'s except-block plus the periodic
   `reconcile_stuck_call_sessions` sweep in `main.py`), escalations (`ESCALATED_NO_TRANSFER`), and
   live handoffs (`TRANSFERRED_TO_HOST`). But a Phase-4-`302` call **still never creates a
   `CallSession` at all** — `exotel_call_routing` returns `302` and the Voicebot websocket is
   never opened. Recording it would mean writing a row from `exotel_call_routing` /
   `exotel_connect_routing` themselves, which fire *before* the host's leg connects, so the row
   could only say "routed to host" with no answered/missed outcome. Deferred until the Phase 2
   Exotel Connect-leg `StatusCallback` exists — the same signal `TRANSFERRED_TO_HOST_MISSED` needs
   to split a handoff into took-it vs missed-it. Until then, host-hours-routed calls are the one
   category of inbound call not on the Calls tab.

7. **`TRANSFERRED_TO_HOST_MISSED` is defined but never written** — a live handoff is recorded as
   `TRANSFERRED_TO_HOST` regardless of whether the host actually answered the Connect leg. Needs
   the Phase 2 Exotel Connect-leg `StatusCallback` (item #4's console config plus a new webhook
   endpoint that writes `handoff_status` `connected`/`failed` and the matching `call_type`). The
   frontend filter and badge for `TRANSFERRED_TO_HOST_MISSED` already exist; they just match zero
   rows today.
