# Call Fluency Fixes — Task List

Source: test call `56947e99-1e17-4315-89ac-6e993cfcbf1d` (2026-11-13, Railway `dev`, SPACEZ DEV). Mira said
there was no stay in Indiranagar (one is on file), invented "Juhu" as a Bengaluru area, and asked the same
question four times because the guest's words were replaced with "Sorry, I didn't quite catch that".

One task per unit of work. Run each task's **Verify** block before starting the next. Mark `[x]` when it passes.

**Rules for every task**

- **No dead code.** Each task has a **Remove** list. Delete replaced code, its comments, its tests and its
  config in the same change. Do not leave an old path behind a flag "just in case".
- **Prove the removal.** Every Verify block ends with a `grep` that must return nothing.
- **Prove the test can fail.** For each new test, break the fix once and confirm the test goes red.
- **Repo conventions still apply:** real Postgres in tests, `ruff check .`, Spacez-only code in `spacez_*`
  files, no hidden LLM regeneration in validators.
- Line numbers below were read on 2026-11-13. Re-check them before editing.

---

## Phase 0 — Check the data before writing code

### Task 0 — Find out what location data exists

- [x] (dev done; production still open — see "Task 0 results") Run against the database that served the call (Railway `dev`), then against production:
  ```sql
  select name, city, state, latitude, airbnb_latitude, left(neighborhood_info,150)
    from properties where name ilike '%juggler%' or neighborhood_info ilike '%indira%';
  select count(*) from places;
  select city, count(*), count(latitude), count(airbnb_latitude), count(state)
    from properties group by 1 order by 2 desc;
  ```
- [x] Write the results at the bottom of this file under "Task 0 results".

**Verify before moving on:**
1. You can state, per city, how many properties have `latitude`, `airbnb_latitude` and `state`.
2. You know whether `places` (the gazetteer) is loaded. This decides the branch in Task 13.

---

## Phase 1 — Small fixes with the largest effect

### Task 1 — Barge-in ducking: fix the clock name collision

`BargeInDuckingProcessor.__init__` stores its timer as `self._clock` (`app/voice/barge_in_ducking.py`).
pipecat's `FrameProcessor` owns `self._clock` and overwrites it with a `SystemClock` object at setup
(`pipecat/processors/frame_processor.py:535`). Result: `TypeError: 'SystemClock' object is not callable` on
every guest speech start. Ducking has never run on a live call.

- [x] Rename the attribute and constructor argument to `_now` / `now`.
- [x] Update `tests/test_barge_in_ducking.py` (the helper subclass near lines 32–40 uses `_clock`).
- [x] Add one test that runs the processor through a real pipecat setup/`StartFrame` before sending
  `VADUserStartedSpeakingFrame`, then asserts the next `OutputAudioRawFrame` is scaled. The existing tests
  pass only because they never run setup.

**Remove:** nothing beyond the renamed lines.

**Verify before moving on:**
1. `pytest tests/test_barge_in_ducking.py` passes.
2. Rename `_now` back to `_clock` locally: the new test fails. Restore.
3. One dev test call: Railway search `barge_in_ducking_failed` returns nothing for that call.
4. Listen on that call: Mira dips while you talk over her and recovers. This is the first time the feature
   runs live, so if it sounds wrong, set `barge_in_ducking_enabled=false` and open a follow-up task.
5. `grep -rn "_clock" app/voice/barge_in_ducking.py tests/test_barge_in_ducking.py` → nothing.

**Status 2026-11-13:** code + tests done and verified locally (Verify 1, 2, 5 pass). **Open — needs a deploy and a phone call:** Verify 3 (`barge_in_ducking_failed` absent on a dev call) and 4 (listen for the dip).

### Task 2 — Remove the dead LLM fallback model

`groq_models` in `app/config.py` (line ~148) lists `llama-3.1-8b-instant` second. The health check gets
`404 model_not_found` for it every minute. If the primary model is rate-limited mid-call, the first fallback
is a model that does not exist.

- [x] List the models the Groq account can call. Pick the fallback order from that list. Each must support
  function calling.
- [x] Update `groq_models` and rewrite the comment above it to match (it currently says all three IDs were
  confirmed live).

**Remove:** the `llama-3.1-8b-instant` entry and every sentence in the comment that refers to it.

**Verify before moving on:**
1. After deploy, Railway search `LLM health check failed` returns nothing over 10 minutes.
2. Every model in the list logs `LLM health OK`.
3. `grep -rn "llama-3.1-8b-instant" app/ tests/` → nothing.

**Status 2026-11-13:** done locally. Chain is now `gpt-oss-120b`, `gpt-oss-20b`, `qwen/qwen3.8-27b` (confirmed on this account's `models.list()` and with a real tool call; `qwen` returned a clean plain reply, no reasoning leak). **Open:** if Railway sets a `GROQ_MODELS` env var it overrides this default; Verify 1 and 2 need a deploy. Verify 3 (grep) passes.

### Task 3 — Fix credentials that fail on every probe (config, no code)

Seen on `dev`: Exotel REST calls use account `mira66` and get 403, while the audio stream reports account
`hostwithmira2`; both hangup attempts failed. The OpenRouter key check returns 401. `docs/CLAUDE.md` says
knowledge-base retrieval falls back to lexical-only matching when embeddings fail, so a bad OpenRouter key
probably weakens `search_faq`.

- [ ] Set `EXOTEL_SID`, API key and token on `dev` to the account that owns the number being called.
- [ ] Fix or remove the OpenRouter key on `dev`.
- [ ] Check the same three values on production.

**Verify before moving on:**
1. Railway search `403 Forbidden` and `401 Unauthorized` returns nothing over 10 minutes, on both
   environments.
2. One test call where Mira ends the call: the log shows the Exotel hangup returning 200 and no
   `Failed to hang up Exotel call`.

### Task 4 — Stop logging full prompts in production

`app/observability/logging_setup.py` (lines ~135–138) adds every sink at `DEBUG`. pipecat then logs the whole
prompt ("Generating chat from context", about 80 KB with guest name and booking) on every turn.

- [x] Add a `log_level` setting: `INFO` when `ENVIRONMENT` is production, `DEBUG` otherwise. Use it for all
  three sinks.
- [x] The guard's score line `stt_language_probability_observed` is logged at `debug`. Raise it to `info`
  inside Task 5, or production loses the data needed to tune the guard.
- [x] Add `LOG_LEVEL` to the environment table in `docs/CLAUDE.md`.

**Remove:** the three hard-coded `level="DEBUG"` arguments.

**Verify before moving on:**
1. Production, after one call: Railway search `Generating chat from context` returns nothing.
2. Dev still shows it.
3. `grep -n 'level="DEBUG"' app/observability/logging_setup.py` → nothing.

**Status 2026-11-13:** done locally (`LOG_LEVEL`, tests incl. a subprocess check that a production sink drops DEBUG). **Open:** Verify 1 and 2 need a deploy. If Railway `dev` also runs with `ENVIRONMENT=production`, set `LOG_LEVEL=DEBUG` there. `stt_language_probability_observed` was replaced by the single info line `low_confidence_guard_decision` (Task 5).

### Task 5 — Rewrite the low-confidence guard so it never puts words in the guest's mouth

`app/voice/low_confidence_transcript_guard.py` replaces any transcript with `language_probability < 0.4`
with an English sentence, as the guest's turn. On the test call it replaced four correct transcripts
("Juhu Bangalore" 0.098, "Indira Nagar" 0.324, "Koramangala" 0.177, "Wow" 0.221).

New behaviour, as one pure function so there is a single copy of the rule:

`classify(text, probability, language_code, known_entities) -> "pass" | "pass_flagged" | "inaudible"`

1. Text matches a known entity after normalising (cities and property names now; localities after Task 12)
   → `pass`.
2. Three tokens or fewer, or digits / a number word / yes-no word → `pass`.
3. Script is neither Devanagari nor Latin → `inaudible`, at any score. (Product decision to confirm: this
   treats Gurmukhi and other scripts as mis-hearing. Change it if a client needs Punjabi.)
4. `probability < threshold` and none of the above → `pass_flagged` (real text kept).
5. Otherwise → `pass`.

- [x] Implement `classify` and call it from the processor.
- [x] `inaudible`: set `frame.text` to the marker `[inaudible]`. Never a sentence.
- [x] `pass_flagged`: keep the text, set a one-turn flag on `ConversationState`.
- [x] `app/voice/state_prompt_sync.py`: when the flag is set, add one hint for that turn: the last transcript
  was low-confidence; if it carries a place, number or date, confirm it back in one short sentence.
- [x] System prompt: one rule. `[inaudible]` means unclear audio; ask the guest to repeat in your own words,
  in their language; never treat it as something they said; never re-ask your previous question unchanged.
- [x] `language_sync.py` and `conversation_style.py`: skip a frame whose text is the marker, so it cannot
  move the language or style tracking.
- [x] `silence_watchdog.py`: the marker still counts as a completed guest turn.
- [x] Pass `known_entities` at pipeline build (`app/voice/pipeline.py` line ~1586), from the properties
  already loaded there.
- [x] `app/voice/audio_input_observer.py` line ~437 recomputes "would the guard trigger" from the threshold.
  Make it call `classify` so the telemetry cannot drift from the guard.
- [x] Log every decision at `info`: decision, reason, probability, token count, script. No guest text.
- [x] Dashboard transcript: render `[inaudible]` as a muted "(unclear audio)" row, not as a guest quote.

**Remove:**
- `DEFAULT_CLARIFICATION_TEXT` and the `clarification_text` constructor argument.
- The module docstring sections that justify substitution, and the comment block in `pipeline.py` lines
  ~1574–1585 that describes substituting a clarification request. Rewrite both to describe the new behaviour
  in a few lines.
- In `tests/test_low_confidence_transcript_guard.py`: the tests that assert the text equals the
  clarification sentence (near lines 24–34 and 119–131). Replace them; do not keep them skipped.

**Verify before moving on:**
1. New table-driven test using the real values from the call:
   | transcript | language | score | expected |
   |---|---|---|---|
   | Juhu Bangalore | en-IN | 0.098 | pass |
   | Indira Nagar | en-IN | 0.324 | pass |
   | Koramangala | pa-IN | 0.177 | pass |
   | Wow | en-IN | 0.221 | pass |
   | ਹੋਰ ਮੰਗਲਾ ਹੋਰ ਮੰਗਲਾ | pa-IN | 0.611 | inaudible |
   | हाय मुझे 20th November का booking करना था। | hi-IN | 0.783 | pass |
   | a 12-word Hindi sentence | hi-IN | 0.20 | pass_flagged |
2. Test: an `[inaudible]` frame leaves the TTS language and the style state unchanged.
3. Break rule 2 (token limit 0): the "Koramangala" row fails. Restore.
4. `pytest tests/test_low_confidence_transcript_guard.py tests/test_call_telemetry_wiring.py
   tests/test_conversation_style.py` passes.
5. Dev call: say "Koramangala" alone. The dashboard shows your word, and Mira acts on it.
6. `grep -rn "didn't quite catch\|clarification_text\|DEFAULT_CLARIFICATION_TEXT" app/ tests/` → nothing.

**Status 2026-11-13:** done locally; Verify 1-4 and 6 pass (the grep also matches the unrelated budget helper `_clarification_text` in `pitch_formatter.py`, left alone). **Open:** Verify 5, the dev call saying "Koramangala". Rule order follows the spec exactly, so a 1-3 token utterance in Gurmukhi passes (only 4+ tokens become `[inaudible]`).

### Task 6 — Fix the "area collected" / "still need area" contradiction

`_SLOT_GOAL_PRIORITY` in `app/voice/conversation_state.py` (lines ~150–156) requires both
`preferred_location` and `purpose_of_stay`, while the hint in `app/voice/state_prompt_sync.py` (line ~68)
says "area **or** purpose". With a location and no purpose, the state prompt says both "already collected:
area" and "still need area". Also, the `recommend_properties` wrapper in `app/voice/tools.py` (line ~1503)
records the area as collected even when the result says the area is not served.

- [x] In `_recompute_goal` (line ~626), treat `collecting_location_or_purpose` as satisfied when either slot
  is present.
- [x] In the wrapper, when the result has `missing_location`, do not set `preferred_location`. Set
  `area_not_served` to the requested name, and clear it when a later search returns options.
- [x] Render `area_not_served` in the state prompt as: the guest asked for X, we have nothing there, do not
  ask for the area again, offer a listed area.

**Remove:** the second `collecting_location_or_purpose` entry in `_SLOT_GOAL_PRIORITY` if the either-or check
makes it unreachable.

**Verify before moving on:**
1. New tests in `tests/test_conversation_state.py`: location only → goal is not
   `collecting_location_or_purpose`; purpose only → same; neither → it is.
2. New test: a not-served result leaves `preferred_location` unset and sets `area_not_served`.
3. Rebuild the state prompt for the call's sequence (guests 2, nights 3, area Indiranagar): the text does not
   contain both "Already collected" with the area and "Still need the guest's preferred area".
4. Revert the either-or check: test 1 fails. Restore.

**Status 2026-11-13:** done; all Verify items pass. Also `update_lead` no longer records an area the last search found nothing in, which is how the contradiction reached the prompt on the real call.

### Task 7 — On a miss, give the model real places and forbid others

`_missing_location_text` in `app/services/property/pitch_formatter.py` (line ~329) tells the model to "offer
to look at another area". The areas come from `list_areas` in `app/services/geo/search.py` (line ~70), which
returns distinct `Property.state` values: empty for unlocated properties, and states rather than areas when
populated. The model was told to name areas and given none.

- [x] Change `list_areas` to return the host's distinct cities with property counts, the guest's city first
  when it is known. (Task 14 upgrades this to localities.)
- [x] Rewrite the miss text: "We don't have a property in or near {location}. We do have properties in:
  {list}. Name only places from this list. Never name any other area. If the guest asks for one not listed,
  say we don't have it."
- [x] System prompt: one global rule in GOLDEN_RULES: never name an area, neighbourhood or city that is not
  in the Locations section or a tool result.

**Remove:**
- The `Property.state` query inside `list_areas`.
- The "offer to look at another area instead" wording.
- "Only use locations listed here -- never invent one" in `app/prompts/system_prompt.py` (line ~1396). The
  global rule covers that branch, and two copies will drift.

**Verify before moving on:**
1. Orchestrator test: an unknown area returns text that contains the host's real cities and no other place
   name.
2. Test: `list_areas` returns cities for properties with `state = NULL`.
3. `grep -rn "another area instead" app/` → nothing.
4. `grep -n "Property.state" app/services/geo/search.py` → nothing inside `list_areas`.

**Status 2026-11-13:** done; all Verify items pass. The gap noted here at the time (`_describe_missing_location` ran for ANY `not_found` with a location) was fixed in the Phase 3 recheck: an area is called missing only when it has no property at all, ignoring budget, group size and amenities (`_area_has_properties`).

### Task 8 — Stop the silence watchdog after the call has ended

After the caller hung up at 21:38:00 the watchdog still logged a prompt at 21:38:05 and 21:38:14 and "ending
call" at 21:38:23. `SilenceWatchdogProcessor.process_frame` (`app/voice/silence_watchdog.py`) has no branch
for `CancelFrame` or `EndFrame`, so the timer survives teardown.

- [x] Cancel the timer and set `_ended` on `CancelFrame` and `EndFrame`, and in `cleanup()`.

**Remove:** nothing.

**Verify before moving on:**
1. New test: start the timer, send `CancelFrame`, advance past the timeout: no prompt frame is pushed and
   nothing is logged.
2. Dev call, hang up from the phone: no `SilenceWatchdogProcessor` line appears after
   `transport_client_disconnected`.

**Status 2026-11-13:** done locally; Verify 1 passes (4 tests, each fails when its part of the fix is removed). **Open:** Verify 2, hang up a dev call and check the logs.

---

## Phase 2 — A test that replays calls, so fixes are not verified by phoning in

### Task 9 — Build the eval entrypoint already designed in `docs/documentation/golden-eval-suite.md`

`pipecat.evals` ships with the installed pipecat 1.6.0 (`venv312/.../pipecat/evals`). The design doc lists
the gap: an eval-mode entrypoint, RTVI events, a gated WebSocket route and seeded data.

- [x] `backend/scripts/run_eval_bot.py`: same prompt builders, LLM service and tool wrappers as production,
  on `pipecat.evals.transport.EvalTransport`, with RTVI enabled in eval mode only.
- [x] Seeded eval host with a fixed property set that includes one property per locality used below. Do not
  point scenarios at live client data.
- [x] `backend/eval/manifest.yaml` and `backend/eval/scenarios/regression_confirmed_live/` with these first
  scenarios, text mode:
  1. Guest asks for Indiranagar → `recommend_properties` is called with that location → the reply names the
     Indiranagar property (red until Task 12).
  2. Guest asks for an area the host does not have → the reply names only places from the tool result.
  3. Guest turn is `[inaudible]` → Mira asks them to repeat and does not repeat her previous question
     unchanged.
  4. Guest answers "Koramangala" to "which area?" → `recommend_properties` is called with Koramangala.
  5. Three more from the "Regression: confirmed-live cases" list in the design doc.
- [x] CI: run `pipecat eval suite backend/eval/manifest.yaml` in report-only mode. Make it blocking after two
  weeks without a flaky result.
- [ ] Settle the three open questions at the end of the design doc and record the answers there.

**Status 2026-11-14 (built; NOT yet run end to end).**

Built: `app/voice/eval_mode.py` (STT/TTS stand-in, database guard), an `eval_mode` flag on `_run_pipeline`
(skips post-call steps), `run_lead_pipeline_on_transport` (the browser lead test now shares it),
`scripts/run_eval_bot.py`, `eval/seed.py` (6 properties, race-safe), `eval/judge_llm.py`, `eval/manifest.yaml`,
7 scenarios, `requirements-eval.txt` (adds the `pipecat` CLI, kept out of the production image).

Verified: the bot starts, builds the whole production guard chain with no Sarvam, seeds the host, accepts the
harness over RTVI and reaches the LLM call. The judge was tried on a known-good and a known-bad reply
(qwen3.8-27b right on both, so it is the default). 15 unit tests cover the database guard and the stand-in.

Harness wiring verified with a stand-in LLM (scratch server speaking the OpenAI protocol; not in the repo): scenario
`area_not_on_file_names_only_real_places` PASSED on a correct reply, with the real `recommend_properties` tool
returning the Task 7 text ("Bengaluru (4), Goa (2)"), and went RED when the stand-in invented "Juhu is a lovely
area in Bengaluru". All 7 scenarios parse and the manifest lists all 7.

Blocked, needs a working LLM key: no scenario has run against a real model yet, so none of the 7 shows whether
Mira's real behaviour passes. The Groq key in `backend/.env` is the
free tier (8,000 tokens per minute on all three models); one Lead Agent request is ~19,000 tokens, so Groq
returns 413. The OpenRouter key in `.env` returns 401 "User not found". Run the suite with the key Railway uses.

Built after the first status note (audit, 2026-11-14): (a) the deployed route `/api/v1/voice/eval/ws/{token}`, off unless `EVAL_ROUTE_TOKEN` (24+ characters) is set, matched in constant time, and refused on any database not named eval/test unless `EVAL_ALLOW_ANY_DATABASE=1`, so production refuses it on its database name alone. Verify 4 as literally worded ("returns 404 without its token") cannot hold for a websocket: a refused handshake is HTTP 403 (`code 4401` in-process), and the plain HTTP path answers 404; both are tested. Run through a live uvicorn server with the real harness: a wrong token is refused at the handshake, the right token passes a scenario. (b) `.github/workflows/eval.yml`, report-only (`continue-on-error`, artifact + job summary), triggered by hand or by a pull request that touches voice / prompts / property code; delete the `continue-on-error` line to make it blocking after two clean weeks. The repo's earlier `ci.yml` was deleted in commit `b7b74f8`, so this is the only workflow: decide whether you want the unit-test job back. Verify 1 (the suite prints a pass/fail per scenario): `pipecat eval suite eval/manifest.yaml` ran all 7 scenarios, two at a time, one bot each, and printed a verdict per scenario (2 of 7 with the canned stand-in LLM, which is expected: its replies do not satisfy most scenarios). Verify 2 and 3 (scenarios 2-4 pass; deleting the Task 7 rule turns scenario 2 red) need a real model and are still open.

Known limit: text-mode `send-text` goes straight into the LLM context, so scenarios do not exercise the
transcript path (low-confidence guard, language sync). Those are unit-tested.

Scenario 1 (`indiranagar_stay_is_found`) is written to stay red until Task 12.

**Remove:** the "design only, nothing built yet" status line in the design doc once this runs.

**Verify before moving on:**
1. `pipecat eval suite backend/eval/manifest.yaml` runs locally and prints a pass/fail per scenario.
2. Scenarios 2, 3 and 4 pass. Scenario 1 is red and named as expected-red until Task 12.
3. Delete the global rule from Task 7 locally: scenario 2 goes red. Restore.
4. The eval route returns 404 without its token.

**Rule from here on:** every bug found on a real call becomes a scenario in `regression_confirmed_live/`
before its fix is merged.

---

## Phase 3 — Locality as real data

### Task 10 — Migration: `properties.locality` and `properties.locality_key`

- [x] Add `locality: Mapped[str | None]` (String 120) and `locality_key: Mapped[str | None]` (String 120,
  indexed) to `app/models/property.py`, next to `city`.
- [x] New alembic migration. Run `alembic heads` first and set `down_revision` to the real head.

**Verify before moving on:**
1. `alembic heads` → exactly one head.
2. `alembic upgrade head` runs clean on dev Postgres.
3. `alembic downgrade -1` then `alembic upgrade head` → clean.
4. `\d properties` shows both columns and the index.

**Status 2026-11-14.** Verified on a LOCAL copy, not dev: migration chain upgrade / downgrade / upgrade clean from empty, one head (`c7f1a3d9e5b2`), both columns and `ix_properties_locality_key` present. NOT run on dev: that is a shared database, so `alembic upgrade head` there needs your go-ahead.

**Supabase dev (2026-11-14).** With your go-ahead, `alembic upgrade head` was applied to the Supabase dev database: it was at `b4e8a1c6d2f9`, so only `c7f1a3d9e5b2` ran; `locality` and `locality_key` (varchar 120, nullable) and `ix_properties_locality_key` are present and all 91 properties are untouched (0 with a locality). Verify items 2 and 4 of Task 10 are now met on dev; the downgrade/upgrade round trip was only done locally. The Spacez locality script was run as a DRY RUN against dev (host `hello@spacez.dev`, 72 properties): 8 of 72 resolve, identical to the local result, and nothing was written. Not done on dev, awaiting your say: the gazetteer load (Karnataka, Maharashtra, Telangana), `spacez_set_localities --apply`, and `--with-points`. Production (Neon) was not touched.

**Supabase dev, brought up to date (2026-11-14, production left alone as asked).** In order: the gazetteer was reloaded for nine states (Goa, Himachal, J&K, Rajasthan, Tamil Nadu, Uttarakhand, plus Karnataka, Maharashtra, Telangana): 105,376 -> 175,657 places, the six original states with identical counts, Bengaluru / IndiraNagar / Mumbai / Hyderabad now present. Then `spacez_set_localities --with-points --apply` for `hello@spacez.dev`: 8 localities and 8 map points set (all `gazetteer` source); the other host's data untouched. Coverage after (Spacez, city: with locality / with point / properties): Bengaluru 6/6/53, Hyderabad 2/2/5, Mumbai 0/0/9, Saligao 0/0/3, Assagao 0/0/1, Madanayakanahalli 0/0/1; the 64 properties without a locality are listed by the script's dry run and need the host to fill them in (or rows in `scripts/data/spacez_localities.json`). Replayed on that real data, read-only: "Indiranagar" and "Indira Nagar" -> the Jugglers Rest villa first ("in Indiranagar"), then nearby with straight-line km; "Whitefield" (no villa there) -> the three closest with "we have no property in Whitefield itself"; "Juhu" -> missing, offering Bengaluru (53; areas: Koramangala (3), Indiranagar (1), Jakkur (1), Sarjapur (1)), Mumbai (9), Hyderabad (5; areas: Kukatpally (1), Madhapur (1)).

### Task 11 — Fill locality: backfill script and host-editable field

- [x] `backend/scripts/spacez_set_localities.py` (Spacez-only, so it follows the `spacez_*` rule): parse
  "in <X>" and trailing area names from titles, write `locality` and `locality_key` using
  `app/services/geo/text.py` normalisation. Default to `--dry-run`, which prints
  `name | city | parsed locality` and writes nothing.
- [ ] Review the dry-run table by hand. Fix wrong rows with an override file
  `scripts/data/spacez_localities.json`.
- [x] Set `locality_key` wherever `locality` is written (one helper, used by the script and the API).
- [x] Add `locality` to the property API schema and to `frontend/src/components/property-form-fields.tsx`.

**Verify before moving on:**
1. Dry run prints a row for every Spacez property; "Jugglers Rest Spacez in Indiranagar" → `Indiranagar`.
2. After the real run: `select city, count(*), count(locality) from properties group by 1;` Record the
   coverage here. List every property left without a locality.
3. Edit a locality in the dashboard, reload: the value persists and `locality_key` changed with it.
4. Test: saving `"Indira Nagar"` and `"Indiranagar"` produces the same `locality_key`.

**Status 2026-11-14.** Built and tested; the script has NOT been run against dev or production (it needs the migration first). Dry run on the 72 real Spacez titles copied into a local database: 8 of 72 get a locality (Indiranagar, Koramangala x3, Sarjapur, Jakkur, Madhapur, Kukatpally); the other 64 name no area in their title and stay empty. They need the host to fill the Locality field in the dashboard (or add rows to `scripts/data/spacez_localities.json`). "4BHK Jugglers Rest Spacez Villa in Indiranagar" -> Indiranagar. Dashboard edit/reload is covered by API tests, not clicked in a browser. Coverage by city after the local run: Bengaluru 6/53, Hyderabad 2/5, Mumbai 0/9, Saligao 0/3, Assagao 0/1, Madanayakanahalli 0/1.

### Task 12 — Match on locality

`build_base_filters` in `app/services/property/retrieval/filter_builder.py` (lines ~128–135) matches the
guest's place with `city ILIKE` or `neighborhood_info ILIKE`. Property names are not searched, and a
substring match cannot join "Indira Nagar" to "Indiranagar".

- [x] New `app/services/property/retrieval/location_match.py`:
  `resolve(text, host_localities, host_cities) -> {city, locality, via}`. Order: exact key match, then the
  skeleton match from `geo/text.py` scoped to this host's own localities, then city only.
- [x] `build_base_filters`: when a locality resolves, filter on `locality_key`. Keep
  `neighborhood_info ILIKE` only for properties whose `locality` is null.
- [x] `_area_needs_confirmation` in `app/voice/tools.py` (line ~233) compares raw lower-cased strings.
  Compare resolved localities.
- [x] Store the resolved canonical locality in the `preferred_location` slot, not the raw phrase.
- [x] Tool schema description for `preferred_location`: write place names in English letters. The geo
  normaliser folds to ASCII, so Devanagari input becomes empty.
- [x] Pass the host's localities into the guard's `known_entities` (Task 5).

**Remove:**
- The unconditional `neighborhood_info ILIKE` branch for the general case.
- The raw string comparison lines in `_area_needs_confirmation`.
- Any helper left without a caller. Check with `grep` for each function you touched.

**Verify before moving on:**
1. Tests in `tests/test_property_retrieval_filter_builder.py` and `test_property_retrieval_orchestrator.py`:
   "Indiranagar", "Indira Nagar" and "indiranagar " all return the Indiranagar property.
2. Test: a property whose `neighborhood_info` mentions "Indiranagar" only as a landmark, with a different
   locality set, is not returned.
3. Test: the area lock treats "Indira Nagar" and a locked "Indiranagar" as the same area.
4. Eval scenario 1 from Task 9 is now green. Remove its expected-red label.
5. Replay of the test call's tool call (`nights=3, num_guests=2, preferred_location="Indiranagar"`) logs
   `candidate_count >= 1`.

**Status 2026-11-14.** Verify 1-3 pass as tests; each was broken once and went red. Verify 4: scenario `indiranagar_stay_is_found` PASSED through the harness against a stand-in LLM that quotes the real tool result (the search half is real; the wording half is not, no working LLM key here), and its expected-red label is removed. Verify 5: replaying `nights=3, num_guests=2, preferred_location="Indiranagar"` on the copied Spacez titles returns the Jugglers Rest villa ("Indira Nagar" too; Juhu is reported missing). Deviation: "keep `neighborhood_info ILIKE` only for properties whose locality is null" is applied to the locality case only; a city-only or unrecognised phrase keeps the old city/neighborhood match, otherwise a region word ("Kerala", "Goa" on a Colva property) would lose properties.

**Phase 3 recheck (2026-11-14), three more defects found and fixed.** (1) A search that found nothing for a reason other than the area (budget, group size, amenity) was reported as "We don't have a property in or near Indiranagar ... we do have Indiranagar (1)" and set `area_not_served`; the area is now called missing only when it has no property at all, ignoring every other constraint (`_area_has_properties`). This also closes the Phase 1 "known unfixed gap". (2) "Bandra" did not resolve to a host area stored as "Bandra West": a first-word (prefix) tier now resolves it, only when exactly one area starts that way, never from a middle or last word. (3) "Indiranagar Mumbai" resolved to the Bengaluru Indiranagar; an area is now rejected when the guest named a different city the host has. Each was broken once to confirm its test goes red.

### Task 13 — "Near X" using the geo path that already exists

`orchestrator._geo_match` and `app/services/geo/` already search by distance. They need
`properties.latitude/longitude` and a loaded gazetteer (`places`). Use Task 0's results.

- [ ] If `places` is empty: run `scripts/load_gazetteer.py`.
- [ ] If `latitude` is empty: run `scripts/set_property_locations.py`. If `airbnb_latitude/longitude` are
  filled, check whether that script can take them as the source before geocoding anything by hand.
- [x] Label each result tier in the tool text so Mira says what she found: exact area; nearby, with the
  distance in km; same city, "we don't have X itself"; not found.
- [x] Never state a drive time. Distance only.

**Remove:** nothing expected. If a hand-written neighbour list was started anywhere, delete it; distance
replaces it.

**Verify before moving on:**
1. `select city, count(*), count(latitude) from properties group by 1;` shows coordinates for the client's
   properties. Record coverage here.
2. Test: a search for a locality with no property returns the nearest properties with a distance, labelled
   nearby.
3. Dev call: "kuch Indiranagar ke paas" returns Indiranagar first, then nearby options with distances.
4. Tool latency for the call stays under the slow-tool filler threshold (1.2 s) in the log line
   `voice_tool_call ... duration_ms`.

**Status 2026-11-14.** Found (read-only, dev): the gazetteer holds only Goa, Himachal, J&K, Rajasthan, Tamil Nadu and Uttarakhand. It has NO Karnataka, Maharashtra or Telangana, so Bengaluru, Mumbai and Hyderabad are not in it at all, and "near Indiranagar" cannot work on dev until they are loaded (`python -m scripts.load_gazetteer --download --dir ... --admin1 19 16 40`; this writes to the database). Spacez properties also have no coordinates. Instead of typing 72 coordinates by hand, `guess_point_from_locality` places a property at its locality's gazetteer point (single match within 40 km of the city, or the nearest when it is at least twice as close as the next); `spacez_set_localities.py --with-points` runs it. Proven on REAL GeoNames Karnataka data loaded locally: Indiranagar, Jakkur, Sarjapur and all three Koramangala villas placed correctly (the Koramangala 5 km from the centre, not the one 39 km out); Madhapur and Kukatpally (Hyderabad) could not be placed because Telangana was not loaded. With those points: "near Indiranagar" returns the Indiranagar villa first ("in Indiranagar"), then Koramangala ("about 5 km from Indiranagar in a straight line"); "Whitefield" (no villa there) returns the three closest with "about 13 km ... we have no property in Whitefield itself" (said only when the nearest is 5 km or more away); distance only, never a drive time. Not measured: tool latency on dev (6-14 ms locally), and the dev-call check ("kuch Indiranagar ke paas").

**Task 13 audit (2026-11-14).** Activating the map data exposed a regression on the real Spacez data: a plain city search ("Bangalore") came back ranked by distance from the city centre and labelled "about 12 km from Bangalore ... we have no property in Bangalore itself", although every result is in Bangalore. Fixed: properties in the city the guest named get no distance note and sort first (then the rest by distance, so the other host's "near Kasol" still finds nearby villages in other cities); three pure tests and one database test pin it, and each turns red when the fix is removed. Verify 4 (tool latency): measured from this laptop to Supabase, a search takes a median of about 1.2 s (1.05 s without the new area-resolution query, which costs about 155 ms here; one database round trip from here is several hundred ms, so most of this is network). That is not the number that matters: read `voice_tool_call ... duration_ms` on a dev call from Railway.

### Task 14 — Put locality in the prompt

- [x] Portfolio line in `app/prompts/system_prompt.py` (lines ~1581–1589): `name -- locality, city, ₹, sleeps`.
- [x] `_locations_section`: per city, list areas with counts, for example
  `Bengaluru areas: Indiranagar (1), Koramangala (3)`.
- [x] `list_areas` (Task 7): return localities in the guest's city first, then other cities.
- [x] Keep the ordering deterministic so the prompt stays byte-stable for the provider's prompt cache.

**Remove:** the city-only fallback wording in `list_areas` once localities are returned, and any now-unused
parameter.

**Verify before moving on:**
1. Build the prompt twice for the same host: the two strings are identical.
2. The log line `prompt tokens` on a dev call: record the before and after counts here.
3. All Task 9 scenarios pass.
4. Dev call: ask "Bengaluru mein kahan kahan hai?" → Mira names only areas from the Locations section.

**Status 2026-11-14.** Portfolio lines now read `name -- locality, city, price, sleeps`, sorted so the prompt is byte-identical across builds and input order (tested, and the sort mutation turns it red). The Locations section adds `Bengaluru areas: Indiranagar (3), Koramangala (2)` per city plus a "never name another area" rule when any locality is set. The missing-area reply lists a city's areas (`Bengaluru (4; areas: Koramangala (3), Indiranagar (1))`) through a new `geo_search.list_localities`; `list_areas` itself is unchanged and still supplies the city counts. Not done: prompt-token before/after (needs a dev call), the dev call "Bengaluru mein kahan kahan hai?", and running all 7 eval scenarios against a real model.

**Task 14 audit (2026-11-14).** Verify 1: the prompt is byte-identical across two builds and across input order (tested, and checked on the real 72-property Spacez portfolio). Verify 2, measured on that real portfolio: the prompt grows by 369 characters (about 90 tokens) from the localities, 82,600 -> 82,969 characters (about 20,650 -> 20,742 tokens, characters/4 estimate), so the locality lines are not what makes the prompt large; the dev-call `prompt tokens` log line is still the exact figure. Verify 3 and 4 need a real model and a dev call.

---

## Phase 4 — Know about bad calls without phoning in

The repo already covers infrastructure: `health_monitor_service.py` (incidents, alert emails, credit
balances, daily digest), `call_quality_event`, `service_usage_event`. The gap is call quality.

### Task 15 — A per-call scorecard

- [x] In the post-call steps (`persist_telemetry`, `app/voice/pipeline.py` line ~2174), store per call:
  guard decisions by type, count of `[inaudible]` turns, count of repeated assistant questions, tool calls
  with status (`no_results`, error), longest gap between guest stop and Mira start, who ended the call, and
  whether a lead, booking or escalation resulted.
- [x] Flag a call when any of these holds: an assistant question repeated twice or more; two or more
  `[inaudible]` turns; any tool error; the guest hung up within 10 seconds of Mira speaking; a response gap
  over 3 seconds.
- [x] Founder console: a "Flagged calls" list with the reason and a link to the transcript and recording.

**Verify before moving on:**
1. Re-run the scorecard on the test call's stored data: it is flagged for a repeated question.
2. A clean call is not flagged.
3. Add the flagged count and the top reason to the existing daily digest email.

**Status 2026-11-14.** Built. A `call_scorecard` row (an internal telemetry rule in `call_quality_events`, so no migration and host analytics ignore it) is written for every call in the post-call step: guard decision counts, `[inaudible]` turns, repeated-question repeats, tool calls / errors / no_results, longest response gap, who ended the call, how long before the end Mira last spoke, what the call led to (lead / booking / escalation), and the flags. Founder console: `/admin/flagged` (list, reason counts, three weekly numbers per host, transcript and recording per call). The daily digest carries the flagged count and top reason.

Verify 1: re-scoring the REAL test call 56947e99 from its stored rows (`python -m scripts.rescore_call`, read-only) first came back NOT flagged: my repeat check compared spelling, and the call's four repeats were Hindi rewordings. It now clusters questions by word overlap and flags it ("asked 5 times"); checked against distinct questions that share a shape ("How many guests...?" / "How many nights...?") so those are not flagged. Verify 2: a clean call is not flagged (tested). Verify 3: the digest shows the count and top reason (tested, and still sends if the quality query fails).

Decision (confirmed 2026-11-14): "repeated twice or more" means the same question asked again at least twice, so three asks in all; two asks does not flag. "Guest hung up within 10 seconds of Mira speaking" is measured from the last time Mira started speaking to the end of the call, when the guest (not Mira) ended it. Calls from before this shipped have no scorecard, and the page says how many in range do. Tool `no_results` is recorded but is not a flag by itself.

**Phase 4 recheck (2026-11-14), three more defects found and fixed.** (1) The "seconds since Mira last spoke" figure was taken when telemetry was persisted, which is after the classification and summary LLM calls, so it was inflated by those seconds and the 10-second hang-up flag could almost never fire; the collector now stamps the end when the terminal frame passes through it (proved with a real pipeline run). (2) The scorecard was written before the steps that back-fill or delete leads, so "ended in a lead" could count a lead that was then removed; it is now its own last post-call step (`call_scorecard`, after `unqualified_lead_cleanup`; a test pins the order). (3) The weekly routine says to read ten unflagged calls but the console gave no way to pick them; the Flagged calls page now lists a random ten. Also: the call count no longer loads every call, and the logger in `pipeline.py` is the standard-library one (`%s`, not `{}`), which my first version got wrong.

### Task 16 — Weekly review habit (process, no code)

- [ ] Every week: read every flagged call and ten unflagged ones. Tag each failure with one cause.
- [ ] The most frequent cause becomes the next fix. Each reviewed failure becomes a Task 9 scenario.
- [ ] Track three numbers per week per client: percent of calls flagged, percent ending in a lead, booking
  or correct escalation, and percent where the guest hung up mid-conversation.

**Verify:** after two weeks there are two dated review notes in `docs/documentation/`, and at least one
scenario was added from each.

**Status 2026-11-14.** The routine, cause tags, note template and the three numbers are written down in `docs/documentation/weekly-call-review.md`, and the three numbers per host are on the Flagged calls page. The Verify (two dated notes, a scenario from each) needs two real weeks of calls and has not happened.

---

## Deferred on purpose

- **Area-grounding guard** (flag any place name not in the allowed set). Add it log-only if scenario 2 or
  the weekly review still shows invented places after Tasks 7 and 14.
- **Fuzzy matching with `difflib`** and **cross-city detection** ("Juhu is in Mumbai"). Add when a real call
  needs them.
- **Stop sending the whole portfolio every turn.** About 24k tokens for 116 properties today. Needed before
  a client with 250+ properties.
- **Speech-to-text biasing with place names.** Depends on what Sarvam's streaming endpoint and pipecat 1.6.0
  expose. Spike later.

## Go-live gate for the client

- [ ] Tasks 1–8 and 10–14 done, each Verify block passed.
- [ ] Task 9 suite green on at least 20 scenarios.
- [ ] 20 scripted phone calls across Hindi, English and Hinglish, each naming a place: no invented place, no
  question asked more than twice, no substituted text in any transcript.
- [ ] Task 15 scorecard live, with the flagged-call count in the daily digest.

### Audit, 2026-11-14 (every task, against the code as it stands)

Method: each Remove grep and Verify block re-run; every fix broken once to confirm its test goes red; the real harness, the real dev data and a live server used wherever a stand-in was not needed.

| Task | Code and tests | Still open (cannot be done from here) |
|---|---|---|
| 0 data check | done | production (Neon) queries |
| 1 ducking clock | done, all greps clean | Verify 3-4: a dev call |
| 2 dead fallback model | done | Verify 1-2: deploy + 10 min of logs; a Railway `GROQ_MODELS` var would override the new default |
| 3 credentials | **not started** (config only) | Exotel account, OpenRouter key: the Groq key is free-tier and OpenRouter returns 401 |
| 4 log level | done | Verify 1-2: deploy; `LOG_LEVEL=DEBUG` on Railway dev if it runs as production |
| 5 guard | done, all greps clean | Verify 5: say "Koramangala" on a dev call |
| 6 contradiction | done | none |
| 7 miss text | done (and the "any not_found" gap fixed) | none |
| 8 watchdog | done | Verify 2: hang up a dev call |
| 9 eval suite | entrypoint, seed, 7 scenarios, judge, suite, deployed route, report-only CI: built and exercised | a real LLM key: no scenario has a real-model verdict, so Verify 2-3 are open |
| 10 migration | done; applied to Supabase dev | production |
| 11 backfill | done; applied to Supabase dev (8 of 72) | the 64 other Spacez properties need a locality from the host |
| 12 matching | done; replayed on real dev data | none beyond the real-model scenarios |
| 13 near X | done; gazetteer (9 states) and map points applied on dev | the dev call "kuch Indiranagar ke paas"; real tool latency |
| 14 prompt | done | the dev call "Bengaluru mein kahan kahan hai?" |
| 15 scorecard | done, two timing/ordering defects found and fixed in the recheck | goes live on the next deploy |
| 16 weekly review | doc, tracked numbers and sample on the page | two real weeks of notes |

Go-live gate: items 1 (Verify blocks) and 2 (20 scenarios green) and 3 (20 scripted phone calls) are open for the reasons in the right-hand column; item 4 (scorecard live with the digest count) is built and waits for a deploy. Task 3 is the only task with no work done on it.

## Task 0 results

Run 2026-11-13, read-only (`SET TRANSACTION READ ONLY`), against the database in `backend/.env`
(Supabase = Railway `dev`; 91 properties, 2 hosts). **Production (Neon) was NOT checked** — no credentials
in this checkout. Run the three queries from Task 0 there before Task 13.

- "4BHK Jugglers Rest Spacez Villa in Indiranagar": `city='Bengaluru'`, `state` NULL, `latitude` NULL,
  `airbnb_latitude` NULL, `neighborhood_info` NULL. So the free-text match had nothing to find ("Indiranagar"
  is only in `name`, which is not searched) and the geo path had no point to judge.
- `places` (gazetteer): **loaded, 105,376 rows.**

| city | properties | `latitude` | `airbnb_latitude` | `state` |
|---|---|---|---|---|
| Bengaluru | 53 | 0 | 0 | 0 |
| Mumbai | 9 | 0 | 0 | 0 |
| Hyderabad | 5 | 0 | 0 | 0 |
| Saligao | 3 | 0 | 0 | 0 |
| Assagao | 1 | 0 | 0 | 0 |
| Madanayakanahalli | 1 | 0 | 0 | 0 |
| Dehradun | 3 | 3 | 0 | 3 |
| Nainital | 2 | 2 | 0 | 2 |
| Tangmarg | 2 | 2 | 0 | 2 |
| 12 other single-property villages (Pangan, Jari, Kukas, ...) | 12 | 12 | 0 | 12 |

Reading: every Spacez property (Bengaluru, Mumbai, Hyderabad, Goa) has no point and no state; the other
host's villas are all located. `airbnb_latitude` is empty everywhere, so there is no ready-made source for
coordinates. This confirms Task 7's cause: `list_areas` selects distinct `state`, which is empty for Spacez.
Task 13 branch: the gazetteer is loaded, so only the property coordinates are missing and must be set by hand
(or geocoded once), not from `airbnb_latitude`.

Also noticed: `backend/.env` sets `ENVIRONMENT=production` while pointing at the dev database, so any local
script that imports `app.config` fails the production secrets check unless `ENVIRONMENT` is overridden.
