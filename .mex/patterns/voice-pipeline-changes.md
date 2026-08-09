---
name: voice-pipeline-changes
description: Modify the voice pipeline — add a guard processor, tune a VAD/LLM/TTS parameter, add or modify a voice tool, or change the system prompt GOLDEN_RULES.
triggers:
  - "add guard"
  - "guard processor"
  - "modify pipeline"
  - "add tool"
  - "voice tool"
  - "tune VAD"
  - "TTS pace"
  - "GOLDEN_RULES"
  - "system prompt"
  - "silence watchdog"
  - "LLM param"
edges:
  - target: context/voice-pipeline.md
    condition: for full pipeline stage order, guard processor details, and tool list
  - target: context/decisions.md
    condition: for why guard processors exist and why certain approaches were reverted
  - target: patterns/debug-voice-call.md
    condition: when testing changes that need to be verified against real call logs
last_updated: 2026-08-08
---

# Voice Pipeline Changes

## Context

Load `context/voice-pipeline.md` before starting. The pipeline file is `backend/app/voice/pipeline.py`. Guard processors are in `backend/app/voice/`. Tool handlers are in `backend/app/services/tool_handlers.py`.

**The single most important rule**: a repeated LLM compliance failure → new guard processor, not just a GOLDEN_RULES update. Prompt-only fixes for confirmed-live failures have consistently failed (different phrasing slips through). Code wins.

## Task: Add a Guard Processor

### Steps

1. **Create** `app/voice/<my_guard>.py`. Follow the pattern of existing guards:
   - Subclass `FrameProcessor` from pipecat
   - **Default: pass all frames through** — `await self.push_frame(frame, direction)` in the normal case
   - **Arm via `FunctionCallsStartedFrame`** (not an external `arm()` method) — arm/disarm races with `arm()` on long-running calls were confirmed live (2026-07-26 incident). Watch `FunctionCallsStartedFrame.function_name` directly:
     ```python
     if isinstance(frame, FunctionCallsStartedFrame):
         if any(f.function_name == "my_tool" for f in frame.function_calls):
             self._armed = True
         await self.push_frame(frame, direction)
         return
     ```
   - **Never rewrite to empty** — always pass through at minimum the first valid sentence. A silent response is more disorienting than an imperfect one.
   - **For unconditional replacement** (e.g. after `escalate_to_host`): buffer `TextFrame`s while armed; on the first text-bearing frame, replace entirely with a fixed safe line; disarm. See `EscalationPhraseGuardProcessor` for the exact pattern.
   - **For detection-based filtering** (e.g. meta-commentary): stream text immediately by default; hold back only while inside a detectable bad span. Disarm after the span closes.

2. **Import and wire** into `_run_pipeline` in `pipeline.py`. Guards sit between the LLM and TTS. Find the existing chain and add the new processor in the right position:
   ```python
   my_guard = MyGuardProcessor()
   pipeline = Pipeline([
       ...,
       llm,
       repetition_guard,
       meta_guard,
       property_guard,
       escalation_guard,
       my_guard,          # ← add here, before premature_end_call_guard or after, depending on dependency
       premature_end_call_guard,
       response_shape_guard,
       tts,
       ...
   ])
   ```

3. **Write tests** in `backend/tests/test_<my_guard>.py`. At minimum:
   - Normal turn passes through unchanged
   - Armed condition triggers the replacement/drop
   - Disarms correctly after one activation

### Gotchas

- **`FunctionCallsStartedFrame` vs an external `arm()` call**: the completion the guard needs to intercept is causally downstream of the same `result_callback` that triggers the tool call. An external `arm()` on the tool-handler side raced and lost in production (2026-07-26). Watch the frame directly in `process_frame`.
- **Never add latency on normal turns** — the guard must call `push_frame` immediately in every branch that isn't the armed/triggered condition. Any `await` before `push_frame` on a normal turn adds per-turn latency to every call.
- **Position matters**: `ResponseShapeValidatorProcessor` must remain LAST before TTS — it runs after all other rewrites. If your guard rewrites text, it must come before `response_shape_guard`.
- **Both arm and disarm**: a guard that never disarms will stay armed for the rest of the call. Disarm after the first activation (or after the trigger condition clears).

### Verify

- [ ] Guard is pass-through (no latency) on a normal turn — verified by reading the code, not just by reasoning
- [ ] Arms via `FunctionCallsStartedFrame` directly, not via an external `arm()` call
- [ ] Never rewrites to an empty string — always a complete fallback sentence at minimum
- [ ] Disarms after the triggered condition resolves
- [ ] Positioned correctly in the pipeline stage order (before `response_shape_guard` if it rewrites text)
- [ ] Tests cover: normal pass-through, armed trigger, disarm behavior

---

## Task: Add or Modify a Voice Tool

### Steps

1. **Add the function signature** to `app/voice/tools.py` inside `build_voice_tools()`. pipecat extracts name/description/schema from type hints + docstring — no separate JSON schema:
   ```python
   async def my_tool(arg1: str, arg2: int) -> str:
       """Description the LLM sees as a tool docstring. Be specific — the LLM reads this."""
       return await handle_my_tool(db, property_id, arg1, arg2)
   ```
   Call-session context (`property_id`, `host_user_id`, `db`, `conversation_state`) is bound via the factory closure — never accept these as LLM-supplied arguments.

2. **Add the handler** in `app/services/tool_handlers.py`:
   - Return a **natural-language string** — this is TTS-ready and read nearly verbatim to the guest
   - Never return a dict or structured type
   - Catch `ValidationError` at the tool-wrapper level in `tools.py` and return `INVALID_ARGS_MESSAGE`

3. **Add to GOLDEN_RULES** in `app/prompts/system_prompt.py` if the tool has ordering constraints (e.g. "always call X before Y") or can be misused by the LLM.

4. **If the tool surfaces structured results that the LLM might misrepresent**: add an `on_*` callback to `PropertyRecommendationGuardProcessor` following the existing pattern (e.g. `on_priced`, `on_checked`, `on_answered`). This guard verifies the spoken reply matches the tool's actual output.

### Gotchas

- **Tool result phrasing is TTS output**: the string returned from the handler is read aloud to the guest. Write it conversationally, not like a JSON field.
- **`apply_discounts` ordering**: `get_pricing` must be called with `apply_discounts=false` first. If adding a new pricing-adjacent tool, document its ordering constraint in GOLDEN_RULES.
- **Property locking in Lead Agent mode**: `check_calendar`, `get_pricing`, `negotiate_rate` each call `state.lock_property()` in their wrappers. If your tool should also lock, add `conversation_state.lock_property(property_id, property_name)` in the tool wrapper.
- **`ValidationError` → `INVALID_ARGS_MESSAGE`**: if the LLM supplies a bad arg (wrong type, missing required), the tool wrapper in `tools.py` catches it and returns a fixed "please re-ask" string. Don't let it raise.

### Verify

- [ ] Handler returns a natural-language string, not a dict
- [ ] Call-session context not accepted as LLM-supplied args (bound via closure)
- [ ] Ordering constraint documented in GOLDEN_RULES if applicable
- [ ] `ValidationError` caught in `tools.py` wrapper → `INVALID_ARGS_MESSAGE`
- [ ] If tool returns structured data the LLM could misrepresent: `PropertyRecommendationGuardProcessor` callback added

---

## Task: Tune a Pipeline Parameter

### Steps

For VAD, silence watchdog, turn detection, or LLM settings: change the constant, restart uvicorn, and **test against a real call or the browser voice test** — reasoning from code alone has been wrong before (e.g. 0.2s `start_secs` was thought fine until live calls showed it fires on mic bumps).

Key constants and their files:

| What | Where | Current value |
|---|---|---|
| VAD confidence/min_volume/start_secs | `_VAD_PARAMS` in `pipeline.py` | 0.85 / 0.7 / 0.35s |
| Silence watchdog timeout | `SilenceWatchdogProcessor(timeout_seconds=9.0)` in `pipeline.py` | 9.0s |
| Silence nudge count | `DEFAULT_MAX_PROMPTS` in `silence_watchdog.py` | 2 |
| Turn-stop timeout (vad_fixed) | `SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.9)` | 0.9s |
| TTS pace | `SarvamTTSService.Settings(pace=1.15)` | 1.15 |
| Groq max_completion_tokens | `GroqLLMService.Settings(..., max_completion_tokens=400)` | 400 |
| Repetition similarity threshold | `RepetitionGuardProcessor` (`app/voice/repetition_guard.py`) | 0.6 word overlap |

### Gotchas

- **`.env` changes → full restart required**: pydantic-settings reads `Settings()` once at startup. Kill uvicorn completely before retesting.
- **`reasoning_effort: "low"` only for gpt-oss models**: other Groq models return 400 with this param. Applied via `"gpt-oss" in model` check — don't add it unconditionally.
- **VAD `start_secs` needs live call confirmation**: 0.35s was chosen as plausible (raised from 0.2s on 2026-07-23). Confirm new values don't make genuine interruptions feel sluggish before committing.

### Verify

- [ ] Constant changed in exactly one place (check for duplicates — some params appear in multiple places, e.g. `reasoning_effort` appears in `_check_llm_health`, `_build_llm`, and `_build_openrouter_llm`)
- [ ] Tested against a real call or browser voice test — not just code review
- [ ] `.env` change: uvicorn fully restarted before testing

## Debug

See `patterns/debug-voice-call.md` for diagnosing failures after pipeline changes.

## Update Scaffold

- [ ] Update `context/voice-pipeline.md` with the new guard, tool, or tuned constant
- [ ] If a new guard was added for a confirmed-live failure, add a note to `context/decisions.md`
- [ ] Update `.mex/ROUTER.md` "Current Project State" if behavior meaningfully changed
