# Outgoing Calls — Plan (DRAFT, awaiting specifications)

Status: planning only. Nothing here is built. Sections marked **SPEC NEEDED** are
left open for the product owner to fill in before implementation starts.

Branch: `abhaya`. No commits until explicitly approved.

---

## 1. Why this exists

Today every Mira call is **inbound**. The first outbound need is the busy-line
fallback (AI Training → Busy calls → option B-ii): the guest was told to call back
within 5 minutes, did not, and the host chose "Mira calls them".

Other candidate uses (not committed — each needs a spec):

| Candidate | Trigger | Notes |
|---|---|---|
| Busy-line callback | 5 min after "we're free, call us" with no callback | the only one already decided |
| Pre-arrival call | N hours before check-in, at a host-set time | configurable in Agent → Follow-up |
| Post-checkout feedback | after check-out | configurable in Agent → Follow-up |
| Payment reminder | link unpaid near soft-hold expiry | overlaps the 1-hour hold rule |
| Failure takeover | a call failed to connect | probably a *human* callback, not Mira — decide |

---

## 2. What exists today (verified in code)

- [backend/app/integrations/exotel_client.py](backend/app/integrations/exotel_client.py) has only `send_sms` and `hangup_call`.
  **There is no outbound dial code.**
- The voice pipeline is entered from a websocket the telephony provider opens
  (`/exotel/ws/{token}`, `/twilio/ws/{token}` in [api/v1/voice.py](backend/app/api/v1/voice.py)) into
  `run_voice_pipeline` / `run_voice_pipeline_twilio` ([voice/pipeline.py](backend/app/voice/pipeline.py)).
  It assumes the guest called us.
- Busy recovery already sends the guest a WhatsApp and tracks an availability
  follow-up ([services/recovery_service.py](backend/app/services/recovery_service.py)). It has no timer for "guest didn't call back".
- `CallCoordinator` (Redis lease) decides whether a host/property already has a
  live call. An outbound call must go through it too.
- Usage/cost metering exists ([services/usage_meter.py](backend/app/services/usage_meter.py)); no outbound telephony line item.

---

## 3. Proposed architecture

### 3.1 Job table, not fire-and-forget

A new `outbound_call_jobs` table (one row per intended call). A sweeper (same style as
the existing background loops) claims due rows with a short lease so a restart or
second worker never double-dials.

State machine:

```
scheduled -> dialing -> answered -> completed
                     \-> no_answer / busy / failed -> (retry up to N) -> gave_up -> host notified
scheduled -> cancelled   (guest called back in time, opted out, host cancelled, outside allowed hours)
```

Columns (initial): `id, user_id, property_id, lead_id, guest_profile_id, phone, reason,
scheduled_for, status, attempts, max_attempts, last_attempt_at, claimed_at,
call_session_id, cancel_reason, estimated_cost_inr, created_at`.

Durable on purpose: the 5-minute callback timer must survive a deploy.

### 3.2 Dialing

- Add `exotel_client.place_outbound_call(to, ...)` using Exotel's outbound Calls API,
  connecting the guest leg to the existing Voicebot Applet flow so the audio comes back
  through the **same websocket and pipeline**.
- The pipeline needs to know the call is outbound: add `CallSession.direction`
  (`inbound` / `outbound`) and pass the job id through the dial request so the
  websocket handler can load it. **SPEC NEEDED:** confirm Exotel account supports
  outbound-to-Voicebot flows and which caller ID (ExoPhone) is used.
- Twilio path: out of scope unless asked (Twilio Voice is a trial-only testing path).

### 3.3 Cancel-on-callback

Before every dial, and when an inbound call arrives from that number:
re-check `status`. If the guest already called back (matching phone, after the job was
created), mark the job `cancelled(callback_received)` — never ring someone who just talked to us.

### 3.4 Agent behaviour on an outbound call

A separate prompt variant, **not** a flag sprinkled through the inbound prompts:

1. Opens by saying who is calling and why ("This is Mira, calling for <host> about your enquiry for <property>"),
   and that it is an AI assistant. Waits for the guest to confirm it's a good time.
2. If wrong person / "not now" / "stop calling" → ends politely and records an
   opt-out where relevant. No selling.
3. Otherwise follows the normal goal engine, seeded with the lead's existing slots
   so it never re-asks what is already known (name, phone, dates, property).
4. Same guardrails as inbound: KB grounding guard, no invented facts, promises → tickets.

### 3.5 Concurrency

Outbound acquires the same `CallCoordinator` lease. If the host/property is on a live
call at dial time: reschedule (short delay, counted against attempts) rather than reject.

### 3.6 Failure handling

Dial failure / no-answer / busy → bounded retries → `gave_up` → host notified with the
guest's number so a human can call. Provider outage on dial → alert host + admins
(same channel as inbound failure alerts).

---

## 4. Host-facing configuration (Agent page)

One card, shown only if the host has enabled a capability that uses outbound calls:

- "Let Mira call guests back" (default off)
- Reasons enabled (checkboxes from §1)
- Calling window (start/end, host timezone)
- Max attempts and gap between attempts
- Monthly cap (calls or ₹) — the call stops, and the host is told, when reached
- Cost line: "≈ ₹X per answered minute" (estimate shown before enabling)

Keep minimal. Defaults chosen so most hosts only flip the first switch.

---

## 5. Cost and metering

- Add outbound telephony minutes to `usage_meter` as their own service line so the
  admin Usage page can separate inbound from outbound cost per host.
- Per-host cap enforced **before** dialing, not after.
- **SPEC NEEDED:** per-minute rate, who bears it (platform vs host pass-through), and
  whether unanswered/ringing time is billed.

---

## 6. Compliance and consent — must be settled before any real dialing

These are decisions for the product owner and legal counsel; this plan does not
assert the rules. To confirm:

- Consent basis for calling a guest who only phoned us once.
- Do-Not-Disturb registry scrubbing and permitted calling hours for the call category
  (transactional vs promotional) in India.
- Mandatory disclosure that the caller is an AI, and call-recording disclosure.
- Opt-out handling: a guest saying "don't call me" must set a durable per-number block
  checked by every future job (new table or column on `GuestProfile`).
- Data retention for outbound recordings/transcripts.

Until these are answered, ship **dial-disabled**: jobs are created and visible but a
global kill switch (`OUTBOUND_CALLS_ENABLED`, default off) prevents any real dial.

---

## 7. Observability

- Job lifecycle logged with `call_session_id`/`job_id` (JSON logs).
- Admin panel: outbound jobs by status, answer rate, cost per host, gave-up list.
- Health: add the outbound dial endpoint as a monitored service in the observability catalog.
- No behavioural feedback from metrics into live calls (existing invariant).

---

## 8. Phases

1. **Foundations:** `outbound_call_jobs` table, `CallSession.direction`, kill switch, host config card (disabled).
2. **Busy-callback timer:** create the job when the "call us back" WhatsApp is sent; cancel-on-callback; option (i) host-responds after 5 min works without any dialing.
3. **Dialing:** `place_outbound_call`, websocket handoff, outbound prompt variant, concurrency lease, retries.
4. **Metering, caps, admin views.**
5. **Other reasons** (pre-arrival, feedback, payment reminder) one at a time, each from its own spec.

Phase 2 delivers the host-facing value with no calling cost and no compliance exposure.

---

## 9. Specifications needed from the owner

- [ ] Which reasons from §1 are in scope for v1?
- [ ] Callback window: exactly how long after the WhatsApp, and is "5 min" editable by the host?
- [ ] Calling hours and timezone rule (host's vs guest's).
- [ ] Max attempts, retry gap, and what happens after the last failure.
- [ ] Opening script wording and the AI-disclosure line.
- [ ] Opt-out phrases and where the block list lives.
- [ ] Rate, cost bearer, and cap policy (§5).
- [ ] Exotel outbound capability and caller ID (§3.2).
- [ ] Compliance answers (§6).
- [ ] Should a guest who is mid-payment (link sent, unpaid) ever be called?
