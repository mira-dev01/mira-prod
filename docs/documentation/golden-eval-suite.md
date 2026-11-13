# Golden test eval suite

Status: **design only, nothing built yet**. This doc exists to answer two questions: what
should the suite actually check, and how does it get *run*, mechanically, every time a
prompt or codebase change lands.

## Why this exists

Every prompt tweak or code change today gets validated by manually placing a test call and
eyeballing the transcript. That doesn't scale and doesn't catch regressions in behavior that
isn't the specific thing you just changed. This suite is meant to be a repeatable, scripted
set of conversations run against the real pipeline, each asserting the agent still does the
right thing — not just "didn't crash."

## The mechanism — don't build this from scratch

**Finding: pipecat (already a dependency) ships a complete behavioral-eval framework at
`pipecat.evals`** (`harness.py`, `scenario.py`, `judge.py`, `transport.py`, `suite.py`, plus a
`pipecat eval` CLI). This is exactly the shape of tool this suite needs, and it isn't wired
into MIRA at all today (`grep -n "RTVI" app/voice/pipeline.py app/api/v1/voice.py` returns
nothing) — the pipeline never emits RTVI events, which is the one piece of integration work
required before any of this can run.

### What pipecat's eval framework already gives you

- **Scenario format**: a YAML file per test case — a scripted list of user turns, each with
  expectations:
  ```yaml
  name: pricing_no_upsell_before_asked
  turns:
    - user: "What's the rate for Terra for 2 nights, 2 guests, Oct 10-12?"
      expect:
        - event: function_call
          calls:
            - name: get_pricing
              args: { apply_discounts: false }
        - event: response
          eval: "states a single total price as one sentence, does not itemize base rate/cleaning fee/taxes separately, does not mention early check-in or late checkout"
    - user: "Can you do better on the price?"
      expect:
        - event: function_call
          calls:
            - name: negotiate_rate
        - event: response
          eval: "presents a revised price, does not say a specific discount was volunteered unprompted"
  ```
- **Two ways to assert on the bot's reply**:
  - `text_contains` — exact substring match, for hard rules (e.g. must never contain "loop in").
  - `eval: "<natural language criterion>"` — graded by a judge LLM (`pipecat.evals.judge.EvalJudge`), for semantic checks a substring can't express ("doesn't itemize fees", "names the real conflicting dates"). The judge is any OpenAI-compatible LLM — Groq works directly, no new provider needed.
- **`function_call` assertions** — check the tool actually called, and its arguments, in any order, e.g. confirming `get_pricing` was called with `apply_discounts: false` on the first quote.
- **Two input/output modalities**:
  - **Text mode** (default): skips STT/TTS entirely, sends turns as text, judges the LLM's text output directly. Fast, free, zero audio flakiness — this should be the mode for ~90% of the suite (pricing rules, escalation logic, tool-arg correctness, lead qualification, all the things that live in the prompt/tool layer, not the audio layer).
  - **Audio mode**: synthesizes the user's turn as real speech (a local TTS), feeds it through the bot's actual STT, and judges the bot's actual synthesized audio (via a local STT pass). Slower, costs real STT/TTS calls, but is the only way to catch STT/TTS-specific bugs (accents, a provider outage, TTS mispronunciation). Reserve this for a small handful of true end-to-end smoke cases, not the full suite.
- **Run modes**:
  - `pipecat eval run scenario1.yaml scenario2.yaml --bot-url <ws-url>` — runs scenarios against an **already-running** bot at that URL. This is the literal answer to "test against our deployed server": point `--bot-url` at a running MIRA instance and it drives real conversations against it.
  - `pipecat eval suite manifest.yaml` — spawns bot processes locally per a manifest (with concurrency control) and runs the full matrix; this is the CI-friendly mode (no persistent server needed, runs cold on every PR).
- Caching (`--cache-dir` / `--no-cache`), latency budgets per event (`within_ms`), audio recording for debugging (`--record-dir`), and a live terminal dashboard (`--audio`, `--debug`) all come free.

### The integration gap — what needs building before any of this runs

MIRA's pipeline needs an **eval-mode entrypoint**, separate from the real Exotel/Twilio/browser-test paths so nothing here touches real call handling:

1. A new script, e.g. `backend/scripts/run_eval_bot.py`, that builds the **same** pipeline MIRA already builds in production — same `build_system_prompt`/`build_lead_system_prompt`, same LLM service construction, same tool wrappers (`app/voice/tools.py`) — but wired through `pipecat.evals.transport.EvalTransport` (a `SingleClientWebsocketServerTransport` subclass built for exactly this) instead of the real Exotel/Twilio/WebRTC transport.
2. Add an `RTVIProcessor`/`RTVIObserver` to that pipeline instance (eval-mode only) so `function_call`, `llm_response`, `tts_response`, and transcription events actually get emitted — this is the one-line-conceptually, real-work-in-practice piece, since nothing in `pipeline.py` speaks RTVI today.
3. Expose it as a WebSocket route (mirrors the existing pattern in `app/api/v1/voice.py`'s browser-test entrypoint) — something like `wss://<backend>/api/v1/voice/eval/ws`, gated the same way the browser test path already is (or a separate shared-secret token, same discipline as `EXOTEL_WEBHOOK_TOKEN`/`TWILIO_VOICE_WEBHOOK_TOKEN` elsewhere in this codebase) so it's never reachable by a real caller.
4. Decide test data: point the eval bot at a fixed seeded host/property set (the existing `seed_demo.py` demo account is the obvious choice — stable, already has 12 realistic properties across price points and locations) so scenarios don't depend on whatever real data happens to exist.

Once that route exists, **"how do I run this every time" has a real, concrete answer**:

- **Locally, fast, on every prompt change**: `pipecat eval suite eval/manifest.yaml` — spawns the eval bot against your local backend + local/seeded DB, runs the whole scenario set in seconds-to-low-minutes (text mode), no deploy needed.
- **Against a real deployed environment** (this is what "test after every deploy" from the earlier list becomes for real): `pipecat eval run eval/scenarios/*.yaml --bot-url wss://mira-backend-dev.up.railway.app/api/v1/voice/eval/ws` — literally drives the deployed Railway dev instance.
- **In CI**: wire `pipecat eval suite` into a GitHub Action on every PR — scenario failures block merge the same way a failing pytest would.

## Suggested layout

```
backend/
  scripts/
    run_eval_bot.py          # eval-mode pipeline entrypoint (transport + RTVI wiring)
  eval/
    manifest.yaml            # bots + scenario dirs for `pipecat eval suite`
    scenarios/
      lead_qualification/
      pricing_negotiation/
      escalation_dispatch/
      recommendations/
      calendar/
      faq/
      whatsapp_actions/
      call_closing/
      language_style/
      out_of_scope_abuse/
      returning_guest/
      regression_confirmed_live/   # see below — highest-value cases
    judge.yaml                # shared judge LLM config, pulled in via !include
```

## What to actually test — feature list + edge cases

Organized as scenario groups (each becomes a subfolder of scenario YAML files above). The
**regression_confirmed_live** group is the highest-value one: every case in it is a bug that
*actually happened in production once* and is now called out by name in the system prompt's own
"Confirmed live: ..." annotations — these are proven failure modes, not hypothetical ones, so
they make ideal golden cases.

### Lead qualification flow
- Guest volunteers name/phone/dates unprompted in their opening line — must not be re-asked later.
- Nights-only vs. exact dates vs. a vague window resolves to the right field (`nights` vs. `check_in`/`check_out`), never an invented placeholder date.
- Implied guest counts computed correctly: "we are 10 friends" → 10, "my wife and I" → 2, "2 adults and a kid" → 3.
- Guest corrects a stated value mid-call ("actually 6 guests, not 4") — new value used, no re-confirmation loop, dependent tool calls (pricing) re-run.

### Property recommendation
- Region query ("something in Goa") triggers immediate `recommend_properties`, no gating on other fields first.
- Refinement request ("cheaper", "with a pool") adds to prior criteria, doesn't replace it.
- A property with a partial date conflict is never presented as clean-available — conflicting dates spoken explicitly.
- Comparison question ("why not the other one?") answered from real returned data, never an invented reason.

### Pricing & negotiation
- Standard price (no discount) always quoted first; `negotiate_rate` only after guest pushback.
- Guest cites Booking.com/MMT/Agoda or asks for a discount in Hindi/Hinglish — routed to `negotiate_rate`, no invented "we'll match them."
- Early check-in/late checkout fee only mentioned if guest asked about it.
- Saturday-minimum-stay policy: real conflicting dates always stated, guest offered the Sat+Sun alternative before a flat refusal.

### Calendar
- Exact dates always re-checked via `check_calendar` even if `recommend_properties` already showed availability for a looser window.

### FAQ / property Q&A
- Answer only from what `search_faq` actually returned — if the specific asked detail isn't present, escalate, never answer from general knowledge.

### Escalation & dispatch
- Housekeeping/maintenance requests → `dispatch_technician`, not `escalate_to_host`, with correct `issue_type`.
- Escalation phrase spoken exactly once per call, never repeated.
- After escalating, the call continues normally — never ends early.
- Urgency levels honestly reflect real host-action urgency, not guest tone.

### WhatsApp actions
- `send_photos`/`send_whatsapp` confirmation message should only claim success if the send actually succeeded (currently a known gap — claims success unconditionally; a golden case for this should stay red until that's fixed).
- Phone number normalization: garbled/incomplete digit counts flagged back in the same turn.

### Call closing
- `end_call` always paired with the closing phrase in the same turn.
- Bare "thank you" mid-call correctly triggers the close sequence without double-asking "anything else?"
- Hard-close vs. soft-close phrasing matches how far the guest actually got.

### Language & style
- Guest explicitly asks for a language switch ("English mein baat karo") — honored starting the very next turn.
- No greeting repetition when guest says "hello" mid-call.

### Out-of-scope / abuse
- Off-topic question gets exactly one redirect, then normal continuation if guest returns to topic.
- Prompt-injection attempts never break persona, never leak instructions.
- Abuse gets exactly one warning, then immediate `decline_irrelevant_call` if it continues.

### Returning guest / memory
- Returning guest's name/history used naturally, never re-asked for basic info already on file.

### Regression: confirmed-live cases (highest priority group)
- Asking "May I have your name?" must never continue with an invented answer in the same turn ("My name is Raj. Thanks, Raj.").
- A guest saying "Hmm" mid-thought must never get the same question re-asked seconds later.
- `get_pricing` returning ₹0 must never be spoken as "free"/"zero" — must escalate instead.
- "let me loop in the host" (or any variant) must never be said, for any escalation reason.
- A guest saying "hello" mid-call must never trigger a repeat of an earlier full answer (e.g. a whole attractions list repeated verbatim).
- Two near-identical closing questions must never be concatenated in one turn ("Anything else? Anything else?").
- A guest's own immediately-preceding message answering a question must never be re-asked in the very next turn.

### Host handoff / call takeover (once that feature ships — see the separate plan)
- Takeover claim is exactly-once even if triggered from both the WhatsApp link and the dashboard button near-simultaneously.

## Open questions to settle before building

1. **Where does the judge LLM call go** — reuse the same Groq account/model as production, or a separate cheaper/more deterministic model dedicated to judging? (Judge cost is per-scenario-run, not per-call, so it's low-volume either way.)
2. **Seeded test data** — reuse the existing demo account (`seed_demo.py`) as-is, or a dedicated eval-only host/property set kept deliberately stable so scenario expectations never drift when the demo data changes?
3. **CI wiring** — block merges on failure immediately, or start in report-only mode until the suite is trusted?
