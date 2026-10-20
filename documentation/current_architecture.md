# Current Architecture (as of 2026-09-22)

The clearest, single technical description of how Mira works **today**. This file is the
authoritative architecture overview; for full detail, follow the links into `docs/` rather than
expecting this file to repeat them. See [project_state.md](project_state.md) for what's
implemented vs. in-progress vs. planned, and [../CLAUDE.md](../CLAUDE.md) for constraints/invariants
a future coding session must respect.

**Scope note on "current"**: everything described below is real, working code, committed and
merged into `main`/`shagun` as of this date (the Redis-backed `CallCoordinator`/Busy Call
Recovery/WhatsApp-reply subsystem that was flagged uncommitted as of 2026-08-09 has since landed —
no subsystem described in this file is uncommitted local-only work anymore).

**Since 2026-08-09**: §2 gained the low-confidence transcript guard; §4b's live handoff now covers
Lead Agent (portfolio-wide) calls too, plus a new guest-initiated `request_host_transfer` tool;
§7's pricing/negotiation tools gained vague-timeline (nights-only) quoting. See
[project_state.md](project_state.md)'s "Recent fixes" log for the full dated history — this file
only reflects the current end-state, not the path taken to get here.

---

## 1. High-level call flow

```
Incoming Call (Exotel or Twilio)
    ↓
Telephony ingress (websocket)
    ↓
Call/Host/Property Resolution  (dialed number → Property.exophone or User.lead_exophone)
    ↓
CallCoordinator.acquire_or_reject  (Redis lease: is this host/property already on a live call?)
    ├── START_PIPELINE
    │       ↓
    │   Voice Pipeline (pipecat)
    │       ↓
    │   STT → guards/state → LLM → tools → guards → TTS
    │       ↓
    │   on_pipeline_finished: transcript, classification, summary, lead backfill, guest memory
    │
    └── BUSY_RECOVERY
            ↓
        Ringing/busy audio played, call hung up (no pipeline built)
            ↓
        RecoveryService.handle_busy_recovery (fire-and-forget)
            ↓
        GuestProfile + Lead (recovery_reason="BUSY_CALL") + Notification (channel="busy_recovery")
            ↓
        Guest WhatsApp menu + Host WhatsApp alert
            ↓
        Dashboard (Opportunities page, Live Requests, Recovery Analytics)
```

Two telephony vendors are wired to the identical pipeline core: Exotel (`run_voice_pipeline`,
primary/production) and Twilio Voice (`run_voice_pipeline_twilio`, added so real-call testing can
continue when Exotel credits run out — see `app/integrations/twilio_voice.py`). Two browser-test
entry points (`run_browser_voice_pipeline`, `run_browser_lead_pipeline`) exercise the same
`_run_pipeline` core over WebRTC with a fixed placeholder caller identity, for the dashboard's
"talk to Mira" test UI. All four call into the same `_run_pipeline`/`_run_pipeline_inner` — there
is exactly one pipeline implementation, not one per vendor.

Twilio is **also** used, separately, for real WhatsApp delivery (`app/integrations/twilio_client.py`,
a real WhatsApp Business number) — this is unrelated to Twilio Voice and uses different
credentials/endpoints. See §5.

## 2. Voice pipeline (live call)

Full stage-by-stage detail: [docs/agents.md](../docs/agents.md#pipeline-stages). Summary of
responsibility boundaries:

- **STT/TTS** (Sarvam) — transcription and speech synthesis only.
- **Input-side guard**: `LowConfidenceTranscriptGuardProcessor`
  (`app/voice/low_confidence_transcript_guard.py`, added 2026-09-21, recalibrated 2026-09-22) sits
  right after STT, before anything else sees the transcript. Confirmed live: a guest's city name
  was dropped entirely by STT on an utterance Sarvam itself only tagged `language_probability=0.843`
  — well-formed enough to pass every downstream filter and reach the LLM as if it were a genuine
  complete answer. Uses that same `language_probability` (a language-detection confidence, not a
  transcript-accuracy score — the only confidence-adjacent field Sarvam's codemix STT response
  exposes at all) as a threshold below which the guest's transcript text is deterministically
  replaced with a fixed clarification request ("Sorry, I didn't quite catch that -- could you say
  that again?") before it reaches the LLM. No second LLM call. **Threshold was `0.85` at launch,
  which turned out to be a critical bug**: a genuinely code-mixed Hinglish sentence is ambiguous
  between hi-IN/en-IN by construction, so correctly-transcribed Hinglish routinely scored below
  0.85, firing on ordinary conversation, not just garbled speech. Recalibrated to `0.4` the same
  day, deliberately trading away the original 0.843 edge case to stop breaking Hindi/Hinglish
  calls — still an unvalidated starting point pending real production `language_probability`
  distributions (logged on every transcript for exactly this purpose).
- **LLM** (Groq primary, multi-model fallback chain; Anthropic/OpenRouter as configured) — intent,
  reasoning, and the actual conversational response text. See §7.
- **Tools** (`app/voice/tools.py` → `app/services/tool_handlers.py`) — the only way the LLM causes
  a side effect (DB write, WhatsApp send, calendar check, pricing calc). Thirteen tools total (added
  `request_host_transfer`, 2026-09-20 — see §4b); see
  [docs/agents.md](../docs/agents.md#tools-appvoicetoolspy--appservicestool_handlerspy).
- **Guards** (`app/voice/*_guard.py`, `app/voice/response_shape_guard.py`,
  `app/voice/end_call_reliability_guard.py`) — deterministic, code-level backstops for specific,
  confirmed-live prompt-compliance failures. Every guard is a pure pattern-match or fidelity check
  against real tool output; none of them call an LLM. All are inert/pass-through on the overwhelming
  majority of turns.
- **State/style/quality processors** — see §6.

**What must never live in the pipeline**: business logic for concurrency coordination (that's
`CallCoordinator`'s job, see §3 — the pipeline only ever sees a two-value `Decision`), a second
LLM call to "fix" a bad response (guards rewrite/truncate deterministically; the one soft
correction mechanism is asking the *next* turn's prompt to try harder — see §6's
`pending_style_correction`), or direct writes to `Lead`/`Notification` from a guard (that stays in
`tool_handlers.py`/`recovery_service.py`).

## 3. CallCoordinator (concurrency ownership)

`app/services/call_coordinator.py` is the single authority on "does this host/property already
have a live call?" — nothing else. It does not send WhatsApp, does not create
`Lead`/`Notification` rows, does not know the pipeline exists beyond an opaque `Lease` handle.

- **Contract**: `acquire_or_reject(host_user_id, property_id, holder_ref)` → `(Decision, Lease | None)`,
  where `Decision` is exactly `START_PIPELINE` or `BUSY_RECOVERY`. This is the *only* function
  `pipeline.py` calls to make the decision. Lower-level primitives (`acquire`, `renew`, `release`,
  `transfer`, `is_busy`) exist for the pipeline's lease lifecycle and future consumers.
- **Storage: Redis, not PostgreSQL.** One key per `(host_user_id, property_id)` pair —
  `call_lease:{host_user_id}:{property_id}` (`NIL_PROPERTY_ID` sentinel for Lead Agent calls,
  which are host-scoped, not property-scoped) — holding a JSON value whose `token` field is the
  actual ownership/fencing credential.
  - `acquire()`: one atomic `SET key value NX EX ttl` — Redis itself rejects a concurrent SET for
    an existing key; no GET-before-SET, no application-level lock.
  - `renew()`/`release()`/`transfer()`: each one atomic Lua script that verifies the caller's
    token still matches what's currently stored before mutating anything — this is what stops a
    stale/delayed caller (e.g. a renewal arriving after its own lease already expired and was
    re-acquired by a different caller) from ever touching a lease it no longer owns.
  - Expiry is Redis TTL, full stop — no lazy "is this row still active" check, no sweep job.
- **Lease TTL and renewal**: `DEFAULT_LEASE_TTL = 45s`. `_run_pipeline` starts a background
  `_renew_call_lease_periodically` task (renews roughly every 20s) alongside the real pipeline, so
  a lease survives the full duration of a live call, not just its first 45 seconds. Cancelled in
  the same `finally` block that releases the lease on every exit path.
- **Redis outage behavior — fails open, loudly**: `acquire_or_reject` falls through to
  `START_PIPELINE` on any Redis error (a live guest call must never be rejected solely because
  Redis is down), but logs `lease_redis_unavailable` as an explicit, alertable signal that
  busy-call protection is degraded — not a silent fallback. `renew`/`release` always fail open
  silently (never allowed to terminate or endanger an in-progress call).
- **`CallLease` (PostgreSQL, `app/models/call_lease.py`) — staged for removal, not written to.**
  The pre-Redis Postgres design (partial unique index, lazy expiry) is fully superseded. The
  table/model/migration are kept in the repo only so a later, separate cleanup phase can drop them
  once the Redis-backed implementation has run in production long enough to trust. **Do not add
  new writers to this table.** See [docs/database.md](../docs/database.md#call_leases-calllease-appmodelscall_leasepy--staged-for-removal-not-written-to).

Full design rationale: `app/services/call_coordinator.py`'s own module docstring (unusually
complete — read it before touching this file) and
[docs/agents.md](../docs/agents.md#busy-call-recovery).

## 4. Busy Call Recovery

Triggered the moment `CallCoordinator` returns `BUSY_RECOVERY`: no second pipeline is built at
all. The rejected call is handled entirely outside the live-call path:

1. `app/voice/pipeline.py`'s `BUSY_RECOVERY` branch plays a short pre-recorded clip
   (`app/voice/ringing_audio.py`'s `play_busy_message`) and hangs up.
2. `RecoveryService.handle_busy_recovery` (`app/services/recovery_service.py`) fires detached
   (`asyncio.create_task`), strictly after the accept/reject decision's DB session has closed.
3. It reuses the **same primitives** a normal in-call escalation already uses — no new entity
   types: `GuestProfile` (`call_service.get_or_create_guest_profile`), `Lead`
   (`lead_service.upsert_lead`, `recovery_reason="BUSY_CALL"`, `status` stays the normal `"open"`
   default), `Notification` (`channel="busy_recovery"`), WhatsApp
   (`twilio_client.send_whatsapp_best_effort`/`send_whatsapp_template_best_effort`).
4. The guest gets a numbered WhatsApp menu (Property/Pricing/FAQs/Photos/Talk-to-host/Something-else)
   defined once in `app/services/whatsapp_reply_service.py`'s `_MENU_OPTIONS` — the single source of
   truth both the outbound send and the inbound reply parser use, so the two can never drift out of
   sync. The host gets a WhatsApp alert reusing the existing `mira_escalation` template.
5. A repeat busy-rejected caller reuses the same open `Lead`, not a new row per attempt (same
   `_get_or_create_lead_for_call` reuse logic every other lead-creation path already uses).

**Inbound WhatsApp replies** land on `POST /webhooks/whatsapp/inbound`
(`app/api/v1/webhooks/whatsapp.py`, shared-secret token auth), routed by
`app/services/whatsapp_reply_service.py` to the guest's existing recovery `Lead` (resolved by
phone number within a 72-hour window, only if the lead is still in a reusable status — a reply to
an already-booked/closed lead never reopens it). Property/Pricing/FAQs/Photos reply automatically
from the same data the voice tools use; "talk to host" and free-text replies just notify the host
— **no LLM is involved anywhere in this reply path.**

## 4b. Call ownership routing + live Take Call handoff

Built (Phases 1–8, plus the account-global host-call-hours rework —
`documentation/host-call-hours-and-handoff.md` is the full writeup; this section is a pointer). Two
distinct mechanisms, both about routing an inbound guest call to the host's own phone instead of
Mira:

1. **Time-of-day call ownership.** `app/services/call_ownership.py`'s pure
   `resolve_effective_call_owner(property_, host, current_time_utc) -> CallOwner` (`HOST` | `MIRA`)
   — evaluates the host's account-global call-hours window (overnight-wrap aware) and answers who
   owns the call *right now*. Consumed by `GET /webhooks/exotel/call-routing` (a synchronous
   Passthru applet before the Voicebot applet — `200` continues to Mira, `302` routes to the Exotel
   Connect applet) and `GET /webhooks/exotel/connect-routing` (returns `User.phone` for Connect to
   dial). Both fail closed toward MIRA.
   - Routing input: `User.host_call_hours_enabled/_start/_end/_timezone` (editable on the Settings
     page) — the **sole** input. Disabled = Mira answers 24/7. The per-property
     `Property.call_handling_*` columns are **no longer read** — kept in schema, staged for removal
     (`CallLease` precedent).
   - The former `settings.fixed_host_hours_start`/`_end` rollout override (`render.yaml`,
     11:00–17:00 IST, forced onto every host regardless of their own setting) has been **removed**.
     Routing now always reflects exactly what a host has saved on the Settings page. See
     `documentation/host-call-hours-and-handoff.md`.

2. **Live Mira→host handoff ("Take Call").** For a call already in progress with Mira:
   `app/services/guest_calling_notification.py` sends the host a WhatsApp with a signed, single-use
   *Take Call* link the moment a MIRA-owned `CallSession` exists. `app/api/v1/take_call.py`'s
   `POST` handler does an atomic claim (`CallSession.handoff_status` `NULL -> "requested"`) and
   fires `app/voice/handoff_signal.py`'s in-process `asyncio.Event`. The live pipeline's
   `_wait_and_trigger_handoff` task then speaks a fixed handoff phrase
   (`pipeline._HOST_HANDOFF_PHRASE`) and ends the Voicebot leg with
   `EndFrame(reason="host_handoff")` — `on_pipeline_finished` special-cases this to **not** hang up
   the Exotel call, so Exotel's flow continues to the Connect applet, which dials the host.
   - `handoff_signal.py` is **single-process only** (documented) — works because the backend runs
     one uvicorn worker; multi-worker would need Redis pub/sub.
   - `_HOST_HANDOFF_PHRASE` is host-configurable via `User.agent_handoff_phrase` + a "Live call
     handoff phrase" field on the AI Training tab (`system_prompt.resolve_host_handoff_phrase`
     resolves it once at pipeline start, with the same loop-in-host guard the escalation phrase
     has). Default: "Hold on — the host is available now. I'm passing the call to them."
   - **As of 2026-09-21, handoff is registered for every call, not just property-scoped ones**
     (`pipeline.py`'s `handoff_registered` is now unconditionally `True`). Previously a Lead Agent
     (portfolio-wide) call had no handoff listener at all — `handoff_registered = property_id is
     not None` — so a guest asking to be transferred on such a call fell through to a plain
     escalation with no real transfer attempted, and even fixing that fallback alone would have
     left `request_handoff()` signaling into a call nothing was listening to. The routing webhook
     (`connect-routing`) already keyed its decision off `CallSession.user_id`, not `property_id`,
     specifically so this was reachable once the listener gap closed.
   - **Guest-initiated transfer**: the `request_host_transfer` tool (`handle_request_host_transfer`,
     `app/services/tool_handlers.py`, added 2026-09-20) lets the guest explicitly ask to be
     connected to the host/a human, right now — distinct from `escalate_to_host`'s
     notify-and-continue path. It makes the same atomic `handoff_status` claim `take_call.py` makes
     and signals `request_handoff` directly; the routing webhook can't tell a guest-initiated
     handoff from a host-initiated one, by design. Falls back to a plain `escalate_to_host` call if
     the host has no usable phone number, or there's no live `call_session_id` to claim a handoff
     against (e.g. a browser test call) — `property_id` is *not* a gate on this path anymore, only
     used to enrich the escalation fallback's args when present.

Both mechanisms deliberately keep the pipeline out of the routing decision: it only ever learns of
a handoff via the `handoff_signal` Event, never re-derives ownership.

## 5. Lead safety / lead preservation

The invariant this section exists to satisfy: **a genuine guest opportunity must not silently
disappear.** Three independent mechanisms each cover a different failure mode:

- **In-call**: `update_lead`/`escalate_to_host` (LLM-driven, the normal path).
- **System-level safety net** (`lead_service.ensure_lead_for_engagement`): creates/backfills a
  `Lead` the moment a guest engages meaningfully with a specific property + dates
  (`get_pricing`/`negotiate_rate`/`check_calendar`), **independent of the LLM ever calling
  `update_lead`**. Exists because real booking calls were traced going through a full price
  negotiation and ending with zero `Lead` row — a live LLM function-calling reliability gap, not a
  prompt-clarity one.
- **Busy Call Recovery** (§4): a call that never even reached the LLM (rejected before a pipeline
  was built) still produces a `Lead`+`Notification`+guest WhatsApp — the opportunity isn't lost
  just because the line was busy.

Countervailing safety: `lead_service.delete_if_empty` / `delete_for_unqualified_call` clean up
near-empty/junk leads, but only ever delete a lead **this exact call originated**
(`Lead.call_session_id == call_session_id`) — a *reused* lead from an earlier call is only ever
detached, never deleted, even if the current call classifies poorly.

## 5b. Availability-first recommendations

`recommend_properties` used to filter only on budget/guests/location/purpose/amenities, with zero
calendar awareness — a property could be recommended, the guest could react positively, and only
then (via a separate `check_calendar` call once they picked one) would an actual availability
conflict surface. `docs/tasks/availability-first-recommendations.md` closes this — see that file
for full implementation/review/reverify detail; short summary:

- Once dates or a stay length (`nights`, a `ConversationState` slot the LLM is instructed to ask
  for *before* pressing for an exact check-in date on a vague window) are known,
  `orchestrator.recommend_properties` classifies each SQL-filtered candidate via
  `calendar_service.partial_availability_for_candidates` (one batched query, fail-open on error,
  matching Phase 2.4's original fail-open discipline) as `"full"` (zero conflicts — recommended
  normally), `"none"` (no viable gap — excluded), or `"partial"` (a real conflict exists but the
  property might still work — held out of the main list, surfaced separately via
  `RecommendationResult.partially_available` with the real conflicting dates).
- `LEAD_AGENT_INSTRUCTIONS` tells the LLM `recommend_properties` is already availability-aware and
  not to separately pre-screen candidates with `check_calendar` first; `check_calendar` is reserved
  for re-confirming the one property the guest actually commits to, against their final exact
  dates, even if an earlier classification (against a looser window) already looked clean.
- `PropertyRecommendationGuardProcessor` (§7 below) deterministically verifies a spoken reply
  against a `"partial"` property's real data — never a false "it's available" claim, never a wrong
  conflicting date, and never an omission (the property named with the real dates left unstated,
  the one failure shape real-LLM adversarial testing actually reproduced live, on a small fallback
  model, never on production's `gpt-oss-120b`) — the same "verify against structured tool output,
  correct deterministically, never a second LLM call" pattern this guard already used for every
  other tool fact.

## 6. Conversation architecture

Three deliberately separate types, not one state blob — see
[docs/agents.md](../docs/agents.md#conversation-architecture-three-separate-concerns-not-one-state-blob)
for full detail:

| Type | Owns | Read by |
|---|---|---|
| `ConversationState` (`app/voice/conversation_state.py`) | Facts: locked property, slots, recommendations shown, escalation flag, closing state, conversation goal | Tool wrappers, `StatePromptSyncProcessor` |
| `ConversationStyle` (`app/voice/conversation_style.py`) | HOW Mira speaks: language family, script, tone (hysteresis-smoothed over a rolling window of guest turns) | `StatePromptSyncProcessor` (via `render_style_block()`) |
| `ConversationQuality` (`app/voice/conversation_quality.py`) | Validator/system-health metrics only | Nothing behavioral, except one narrow bridge (below) |

**The one permitted quality→behavior bridge**: `ConversationQuality.pending_style_correction`
(set by `StyleComplianceMonitor` on a confirmed style mismatch) is read by exactly one consumer,
`StatePromptSyncProcessor`, which asks `ConversationStyle` for a more emphatic rendering of the
*same* style block on the next turn — never a validator-authored instruction, never a different
style, never a rewrite of what was already said. This is deliberate and narrow; do not add a
second such bridge without the same justification.

**No hidden LLM regeneration.** `StyleComplianceMonitor` replaced the old
`ResponseComplianceProcessor`, which used to buffer a full response and, on failure, make a second
non-streaming LLM call to regenerate it (multi-second dead air, on every turn it fired). Nothing in
the current pipeline calls the LLM a second time mid-turn to fix a response — correction is either
a deterministic guard rewrite/truncation of already-generated text, or a nudge to the *next* turn's
prompt.

## 7. LLM / streaming / tool architecture

- **Provider**: Groq primary (`gpt-oss-120b` + fallback chain via `GROQ_MODELS`), Anthropic/OpenRouter
  configurable, OpenRouter as last resort if every Groq model is marked down. Full detail:
  [docs/agents.md](../docs/agents.md#groq-multi-model-fallback).
- **Streaming**: real, native LLM token streaming through the pipeline — every guard from
  `RepetitionGuardProcessor` through `ResponseShapeValidatorProcessor` forwards `LLMTextFrame`s
  immediately as they arrive rather than buffering a full response first (see §2 and
  [docs/agents.md](../docs/agents.md#pipeline-stages)'s streaming-discipline note). This is a
  genuine architectural property of the current pipeline, not aspirational.
- **Tools**: `app/voice/tools.py` defines 13 pipecat "direct functions" (name/schema derived from
  type hints + docstring); each delegates to a handler in `app/services/tool_handlers.py` that
  contains the actual business logic. Tool wrappers are where `ConversationState` gets read/written
  (locking a property, recording slots) — handlers themselves stay state-agnostic. Full tool table:
  [docs/agents.md](../docs/agents.md#tools-appvoicetoolspy--appservicestool_handlerspy).
- **Vague-timeline pricing** (2026-09-18): `get_pricing`/`negotiate_rate` accept `check_in`+
  `check_out` **or** `nights` (never both, never neither — enforced by a pydantic model validator),
  so a guest with only an approximate timeline ("first week of October") can get a real quote from
  a stay length alone before an exact check-in date is settled, instead of the model repeatedly
  pressing for one. A nights-only quote is flat `base_price × nights` (no live Airbnb fetch — there
  are no real dates to fetch a rate for) and skips the weekend-minimum-stay rule (unevaluable
  without real calendar dates; re-checked once `check_calendar` runs against the guest's eventual
  exact dates). `PriceBreakdown.is_estimate=True` on this path tells the model to caveat the number
  as an estimate rather than a firm quote.

## 8. Database boundaries

Full schema: [docs/database.md](../docs/database.md). Summary of what lives where:

- **PostgreSQL** — all durable business data: `User`, `Property`, `Lead`, `GuestProfile`,
  `CallSession`, `Notification`, `FaqEntry`, `HostDiscountRule`, `PricingRule`, `Technician`,
  `Booking`. This is the system of record for everything a host sees on the dashboard.
- **Redis** — two independent, non-overlapping uses (`app/integrations/redis_client.py`'s own
  docstring is explicit that these must stay separate modules with different failure contracts):
  1. **Optional TTL cache** (`cache_get_json`/`cache_set_json`) for SearchApi.io pricing responses.
     Fails open/no-ops silently on any Redis error or missing `REDIS_URL` — callers always fall
     through to a live fetch.
  2. **CallCoordinator lease state** (`app/integrations/redis_lease_client.py`) — correctness-bearing,
     not fail-open at the Redis-operation level (see §3's fail-open policy, which lives one layer up
     in `call_coordinator.py`, not in the Redis client itself).
- **`CallLease` (Postgres)** — staged for removal, not written to (§3).

## 9. Important invariants

- A genuine guest opportunity must not silently disappear (§5).
- Call ownership must be concurrency-safe, with a single authority (`CallCoordinator`) and no
  check-then-act race window (§3).
- The pipeline must not contain business logic for concurrency coordination — it only ever sees
  `CallCoordinator`'s two-value `Decision`.
- Time-of-day call-ownership routing is decided *only* by `call_ownership.resolve_effective_call_owner`
  (pure, one function) and the live handoff is signalled *only* via `handoff_signal`'s in-process
  Event — the pipeline never re-derives ownership for itself (§4b).
- Redis cache semantics (fail-open, optional) and Redis lease semantics (correctness-bearing,
  explicit fail-open policy owned by `call_coordinator.py`) are separate and must not be merged
  into one client module.
- Validators (`StyleComplianceMonitor`, and any future validator) must not introduce hidden LLM
  regeneration — correction is deterministic rewrite/truncation or a next-turn prompt nudge only.
- `ConversationStyle` is responsible for *how* Mira speaks; `ConversationState` is responsible for
  conversation facts; `ConversationQuality` is observational and must not become a silent
  behavioral feedback loop beyond the one documented bridge (§6).
- External dependency failures (Redis, SMTP, WhatsApp/Twilio, SearchApi, Bright Data) must not
  unnecessarily terminate a live guest call — every optional integration in this codebase fails
  open/no-ops rather than raising into the call path.
- Do not duplicate existing services — e.g. Busy Call Recovery deliberately reuses
  `get_or_create_guest_profile`/`upsert_lead`/`create_notification`/the Twilio WhatsApp sender
  rather than inventing parallel versions.

## 10. Common failure modes

See [CLAUDE.md](../CLAUDE.md)'s "Common pitfalls" section for the full, actively-maintained list.
The ones most relevant to the subsystems described in this file:

- A Redis outage degrades busy-call protection (fails open, logged loudly) rather than rejecting
  live calls — check for `lease_redis_unavailable` in logs if concurrent-call protection seems
  absent.
- A lease that's never renewed expires at 45s — if `_renew_call_lease_periodically` isn't running
  (e.g. a future refactor drops the background task), every call longer than 45s silently loses
  busy-call protection with no error and no failing test (this exact regression happened once
  already — see `CLAUDE.md`'s pitfall entry on it).
- `CallLease` (Postgres) existing in the schema does not mean it's active — check
  `call_coordinator.py`'s own docstring/imports before assuming Postgres is involved in call
  concurrency at all.

## 11. Development rules for modifying this architecture

- Treat `app/voice/pipeline.py`, `app/services/call_coordinator.py`,
  `app/services/recovery_service.py`, and the conversation-state/style/quality trio as a protected
  path — same "never regress the voice agent" discipline documented in `CLAUDE.md` and
  `documentation/tasks.md`/`restructure.md`'s own standing rules for the dashboard-redesign effort.
- Prefer code-level enforcement (a guard/processor) over a new prompt paragraph for anything that's
  a factually-verifiable claim against real tool/state output — this codebase's own guard suite
  exists because prompt-only fixes for this class of bug repeatedly regressed. Reserve prompt-only
  changes for genuinely stylistic/pragmatic behavior no code path can structurally verify.
  ([docs/agents.md](../docs/agents.md#golden_rules-apppromptssystem_promptpy) documents which is
  which.)
  - **Do not implement bug fixes or feature work by editing this file or `project_state.md` alone.**
    These are documentation; the fix belongs in the code, and the doc update should follow it.
- Before adding a new state field, decide which of the three types (§6) it actually belongs to —
  do not default to `ConversationState` for something that's really a style or quality signal.
- Any new correction mechanism must not call the LLM a second time mid-turn. If a genuinely new
  regeneration need arises, that's an explicit architectural decision requiring its own review, not
  something to add quietly inside a guard.
