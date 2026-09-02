# Graph Report - mira-prod  (2026-09-02)

## Corpus Check
- 498 files · ~621,471 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 5261 nodes · 13022 edges · 293 communities (195 shown, 32 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 872 edges (avg confidence: 0.92)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `f9bd2cdf`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- _user
- acquire
- get_owned_property
- build_state_block_content
- ConversationState
- NegotiationRule
- test_negotiation_policy.py
- utils.ts
- cn
- build_voice_tools
- types.ts
- test_response_shape_guard.py
- CallSession
- test_property_recommendation_guard.py
- resolve_effective_call_owner
- models/property.py
- SlowToolFillerProcessor
- ai-training-section.tsx
- test_auth.py
- test_take_call.py
- call_service.py
- Property
- test_property_card_and_pitch_formatter.py
- test_conversation_style.py
- tool_handlers.py
- test_host_handoff.py
- upsert_lead
- test_max_call_duration.py
- profile/page.tsx
- integrations/__init__.py
- test_silence_watchdog.py
- test_run_voice_pipeline_ringing.py
- RepetitionGuardProcessor
- test_properties_api.py
- test_property_retrieval_filter_builder.py
- test_ringing_audio.py
- Booking
- call_summary_service.py
- pricing_engine.py
- Lead
- properties.py
- test_exotel_call_routing.py
- searchapi_client.py
- timedelta
- test_style_compliance_monitor.py
- leads/page.tsx
- card.py
- call-ownership-card.tsx
- HybridCompletenessUserTurnStopStrategy
- config.py
- comparison_notes
- ConversationQuality
- recommend_properties
- AiTrainingSection
- test_tool_handlers.py
- build_lead_system_prompt
- test_exotel_connect_routing.py
- test_negotiation_rules.py
- status-chip.tsx
- normalize_property_name
- test_language_heuristics.py
- User
- FaqEntry
- main.py
- redis_client.py
- faq_service.py
- call_classification_service.py
- test_property_retrieval_ranking.py
- Mira Memory Architecture — Implementation Plan
- system_prompt.py
- SilenceWatchdogProcessor
- EscalationPhraseGuardProcessor
- dependencies
- test_negotiation_policy_service.py
- LanguageSyncProcessor
- test_airbnb_import.py
- run_sql_search
- test_sarvam_vad_config.py
- twilio_voice.py
- components.json
- match_reasons_for_card
- test_availability_recovery.py
- GuestProfile
- MetaCommentaryGuardProcessor
- run_browser_lead_pipeline
- pipeline.py
- UnansweredQuestion
- test_embedding_service.py
- build_property_chunks
- EndCallReliabilityGuardProcessor
- twilio_client.py
- UserUpdate
- conversation_style.py
- RedundantContextGuardProcessor
- _completed_turn
- calendar_service.py
- StatePromptSyncProcessor
- MIRA Conversational Architecture — Current State, 5 Aug
- PrematureEndCallGuardProcessor
- negotiation_policy_service.py
- test_negotiate_rate_tool_wrapper.py
- test_property_lock.py
- compilerOptions
- compilerOptions
- PropertyChunk
- get
- test_negotiate_rate_guest_memory.py
- PropertiesPage
- answer_faq_gap
- _call_session_for
- devDependencies
- test_email_client.py
- exotel_client.py
- schemas/negotiation_rule.py
- Tables
- to_india_whatsapp_digits
- deploy
- test_database.py
- ConversationStyleProcessor
- guests.py
- ._recompute_goal
- Settings
- property_recommendation_guard.py
- frontend/package.json
- REST API Reference
- test_system_prompt.py
- _normalize_phone
- founder-console/package.json
- BrightDataError
- test_faq_api.py
- leads.py
- frontend/src/app/layout.tsx
- _clean_stages
- technician_service.py
- test_negotiate_rate_host_policy.py
- founder-console/src/app/page.tsx
- Voice Agent Design
- amenity_checklist_note
- Building Intelligence — Task List
- Mira Conversational Behaviour — Superhost-Quality Task Sheet
- Voice Pipeline
- test_explicit_language_preference.py
- generate_busy_message_speech.py
- generate_busy_message_tone.py
- generate_ringing_tone.py
- test_today_anchor_january_has_no_earlier_month_this_year_to_roll_over
- photos/page.tsx
- Patterns
- _parse_iso_date
- _FixedDatetime
- founder-console/src/app/layout.tsx
- LoginPage
- middleware.ts
- sparkline.tsx
- proxy.ts
- PropertyUpdate
- test_repeat_guest_booking_vs_enquiry.py
- test_e18_true_silence_timeout_behavior_is_unchanged
- clsx
- founder-console/next.config.ts
- next-env.d.ts
- eslint.config.mjs
- frontend/next.config.ts
- Task 3.2 — PR Review
- Call Qualification / Junk-Call Detection — Task List
- postcss.config.mjs
- Current Architecture (as of 2026-09-02)
- Mira Dashboard — UI/UX Restructure Task Sheet
- faq.py
- Pricing, Negotiation, Lead Qualification & Airbnb Import
- Availability-First Recommendations — Task List
- Project State
- Architecture
- Task 5.2 — PR Review
- Task 3.3 — Reverify
- Task 5.3 — Reverify
- Decision Log
- test_property_display_name_backfill.py
- cloudinary_client.py
- handle_negotiate_rate
- handle_search_faq
- MIRA — Codebase Guide
- Task 4.2 — PR Review
- Mira Dashboard Redesign — Task Sheet
- Voice Pipeline Changes
- silence_watchdog.py
- test_call_summary_notification.py
- Task 4.3 — Reverify
- Setup — Populate This Scaffold
- voice/__init__.py
- Pipeline stages
- Refactoring plan
- MIRA — AI Property Management Assistant
- Setup
- Add Endpoint
- Add Model
- Step 2: Identify the Failure Mode
- Debug Voice Call
- Architecture
- Conventions
- Stack
- Handoff — 2026-07-15
- Founder Console
- INDEX.md
- Session Bootstrap
- Sync — Realign This Scaffold
- frontend/README.md
- [hostId]/page.tsx
- Task: Add or Modify a Voice Tool
- Task: Tune a Pipeline Parameter
- test_bot_started_speaking_after_ended_is_a_safe_no_op
- test_production_wired_timeout_is_nine_seconds
- test_13_guest_interrupts_mid_bot_speech_existing_behavior_intact
- test_golden_rules_covers_guest_self_correction
- test_golden_rules_covers_answer_first_then_return_to_flow
- test_golden_rules_covers_weekend_minimum_stay_requirement
- test_guest_support_has_its_own_name_phone_timing_guidance
- test_lead_agent_instructed_to_speak_partial_availability_with_real_conflicting_dates
- test_lead_agent_told_recommend_properties_is_already_availability_aware
- class-variance-authority
- @clerk/nextjs
- frontend/AGENTS.md
- lucide-react
- next
- next-themes
- react-day-picker
- tw-animate-css

## God Nodes (most connected - your core abstractions)
1. `ConversationState` - 252 edges
2. `Property` - 206 edges
3. `User` - 156 edges
4. `cn()` - 146 edges
5. `build_voice_tools()` - 135 edges
6. `NegotiationRule` - 114 edges
7. `negotiate_rate()` - 110 edges
8. `SilenceWatchdogProcessor` - 99 edges
9. `_user()` - 90 edges
10. `_property()` - 84 edges

## Surprising Connections (you probably didn't know these)
- `test_reimport_does_not_duplicate_property_chunks()` --uses--> `PropertyChunk`  [INFERRED]
  backend/tests/test_airbnb_import.py → backend/app/models/property_chunk.py
- `test_reimport_with_changed_amenities_replaces_stale_chunk_content()` --uses--> `PropertyChunk`  [INFERRED]
  backend/tests/test_airbnb_import.py → backend/app/models/property_chunk.py
- `test_handle_busy_recovery_reads_host_phone_after_earlier_commits()` --uses--> `User`  [INFERRED]
  backend/tests/test_recovery_service.py → backend/app/models/user.py
- `analytics_summary()` --uses--> `CallSession`  [INFERRED]
  backend/app/api/v1/analytics.py → backend/app/models/call_session.py
- `analytics_summary()` --uses--> `Lead`  [INFERRED]
  backend/app/api/v1/analytics.py → backend/app/models/lead.py

## Import Cycles
- None detected.

## Communities (293 total, 32 thin omitted)

### Community 0 - "_user"
Cohesion: 0.08
Nodes (62): build_system_prompt(), _property(), Phase 3.3 (documentation/agent-conversation-improvement.md, catalogue item C5):…, Phase 3.3: no regression for the overwhelming majority of hosts who won't set…, Regression test: _user()'s in-memory User never went through a DB flush, so…, Phase 4D (Phase 4C/S.1 finding): unquantified pushback ("can you do better?",…, Property.faq (the legacy inline column) must keep working for hosts/ properties…, The per-property FAQ editor now writes structured FaqEntry rows instead of the… (+54 more)

### Community 1 - "acquire"
Cohesion: 0.08
Nodes (58): acquire(), acquire_or_reject(), Decision, _encode_lease(), is_busy(), Lease, _lease_key(), _now() (+50 more)

### Community 2 - "get_owned_property"
Cohesion: 0.08
Nodes (42): cancel_booking(), check_availability(), create_booking(), list_bookings(), AsyncSession, delete, post, UUID (+34 more)

### Community 3 - "build_state_block_content"
Cohesion: 0.05
Nodes (75): build_state_block_content(), _closing_hint(), _format_slots(), _language_hint(), _negotiation_hint(), Injects a compact, always-current summary of ConversationState (Phase 1,…, Orders known slots by attention score (most emphasized/most recently restated…, Renders the Conversation Style Engine's own structured block… (+67 more)

### Community 4 - "ConversationState"
Cohesion: 0.04
Nodes (61): ConversationState, Discards negotiation event history and the last negotiation decision fact --…, A real ceiling BELOW the cheapest property already shown -- cheapest, not…, A real floor ABOVE the largest capacity already shown -- largest, not average,…, Phase 4.1 -- always overwrites, never merges: a later quote (a discount applied…, Phase 4F -- always overwrites, never merges, same discipline as…, Phase 4F -- called by get_pricing's wrapper (app/voice/tools.py) whenever a…, Phase 5 (documentation/agent-conversation-improvement.md) -- called by… (+53 more)

### Community 5 - "NegotiationRule"
Cohesion: 0.12
Nodes (46): NegotiationRule, A host's single, unified negotiation/pricing training policy -- replaces what…, negotiate_rate(), prior_events (Phase 4D, generalized negotiation state -- see documentation…, NegotiationEvent, One negotiate_rate invocation, recorded for the lifetime of the current…, Called once per negotiate_rate invocation, AFTER pricing_engine has already…, _dates() (+38 more)

### Community 6 - "test_negotiation_policy.py"
Cohesion: 0.06
Nodes (75): GuestNegotiationContext, UUID, Pure policy-evaluation layer for host-authored negotiation/pricing rules. Phase…, The guest/booking facts a policy decision can depend on -- the "negotiation…, The policy engine's structured decision (Phase 4 brief, Step 8; extended Phase…, Among approved rules of ONE rule_type, resolves to the single "most generous…, Derives the CURRENT stage index from a call's negotiation event history --…, Reads one rule's stages list at a clamped index. `stages` is a plain JSONB list… (+67 more)

### Community 7 - "utils.ts"
Cohesion: 0.06
Nodes (33): bookingSourceColor, CalendarPage(), allBookingsForDay(), bookingsForDay(), openBlockDialog(), daysInMonth(), toISODate(), emptyForm (+25 more)

### Community 8 - "cn"
Cohesion: 0.05
Nodes (47): DateRangePicker(), formatRange(), ImageLightbox(), goNext(), goPrev(), handleKeyDown(), LightboxImage(), PropertyPhotosManager() (+39 more)

### Community 9 - "build_voice_tools"
Cohesion: 0.08
Nodes (64): build_voice_tools(), UUID, Build the tool functions for one call, bound to its…, _dates(), _FakeFunctionCallParams, Phase 4F (conversation-level negotiation integration) -- live-flow-level tests…, Acceptance ("okay, that's fine") is a routing decision the LLM makes per the…, An unrelated question (e.g. "is breakfast included?") routes to search_faq, not… (+56 more)

### Community 10 - "types.ts"
Cohesion: 0.05
Nodes (64): SettingsPageContent(), handleTestLeadAgent(), connect(), Listener, listeners, parseSseEvent(), scheduleReconnect(), seenNotificationIds (+56 more)

### Community 11 - "test_response_shape_guard.py"
Cohesion: 0.07
Nodes (62): count_greeting_openers(), count_questions(), count_recommendation_blocks(), ends_mid_clause(), first_clean_sentence_or_original(), has_duplicated_punctuation(), has_duplicated_safe_line(), has_multiple_recommendation_blocks() (+54 more)

### Community 12 - "CallSession"
Cohesion: 0.07
Nodes (45): list_service_requests(), ServiceRequestOut, CallSession, BookingSnapshot, CallSummary, BaseModel, Structured, host-facing end-of-call summary -- see…, SummaryOutcome (+37 more)

### Community 13 - "test_property_recommendation_guard.py"
Cohesion: 0.08
Nodes (77): PropertyRecommendationGuardProcessor, FrameProcessor, Strips leaked property IDs and backstops a skipped recommend_properties…, _call_started(), _card(), _partial_result(), asyncio, FunctionCallsStartedFrame (+69 more)

### Community 14 - "resolve_effective_call_owner"
Cohesion: 0.08
Nodes (67): CallOwner, InvalidCallOwnershipConfigError, _parse_hh_mm(), datetime, ValueError, resolve_effective_call_owner: the one pure domain decision Phase 1's Call…, Who owns an inbound call for `property_` at `current_time_utc`.…, Half-open [start, end) wall-clock membership, handling both same-day windows… (+59 more)

### Community 15 - "models/property.py"
Cohesion: 0.12
Nodes (29): do_run_migrations(), run_migrations_online(), Base, CallLease, STAGED FOR REMOVAL -- NOT WRITTEN TO BY ANY PRODUCTION CODE PATH. As of the…, CallQualityEvent, Persisted copy of one ValidationResult (app/voice/conversation_quality.py)…, TimestampMixin (+21 more)

### Community 16 - "SlowToolFillerProcessor"
Cohesion: 0.08
Nodes (51): _PendingCall, Frame, FrameDirection, FrameProcessor, Speaks a short filler line while a slow tool call is still running, so a guest…, Speaks a short filler line if a scoped tool call is still running after a short…, One concurrently-pending scoped tool call's tracking state -- see…, SlowToolFillerProcessor (+43 more)

### Community 17 - "ai-training-section.tsx"
Cohesion: 0.10
Nodes (45): CallDetailPage(), formatDuration(), statusTone, urgencyTone, callStatusTone, capitalize(), entriesToPrefs(), guestInitials() (+37 more)

### Community 18 - "test_auth.py"
Cohesion: 0.17
Nodes (12): _onboarding_payload(), Phase 5: User.phone is the canonical host contact number, reused (not…, Setting phone must not clobber other settings -- exclude_unset means only the…, A host can only ever update current_user's own row -- PATCH /auth/me has no…, test_onboarding_rejects_duplicate_business_phone(), test_onboarding_requires_airbnb_url(), test_onboarding_requires_auth(), test_onboarding_triggers_scrape_when_configured() (+4 more)

### Community 19 - "test_take_call.py"
Cohesion: 0.08
Nodes (50): _call_ended_page(), _invalid_token_page(), _page(), _property_label(), AsyncSession, post, Phase 6: the secure Take Call action a host reaches by tapping the WhatsApp…, Renders the confirmation page. Performs NO write -- validating the token and… (+42 more)

### Community 20 - "call_service.py"
Cohesion: 0.07
Nodes (46): _destination_response(), _empty_destination_response(), exotel_call_routing(), exotel_call_status(), exotel_connect_routing(), _normalize_destination_phone(), AsyncSession, post (+38 more)

### Community 21 - "Property"
Cohesion: 0.15
Nodes (31): Property, RecommendPropertiesArgs, handle_recommend_properties(), date, Thin delegate to app/services/property/retrieval/orchestrator.py -- the actual…, test_update_property_amenities_recomputes_amenity_tags_for_filtering(), Phase 2.5 (documentation/agent-conversation-improvement.md), wired end-to-end…, Confirms this is purely additive when call_session_id isn't passed -- behavior… (+23 more)

### Community 22 - "test_property_card_and_pitch_formatter.py"
Cohesion: 0.07
Nodes (65): build_property_card(), PropertyCard, confidence_for_result(), _format_partial_availability_line(), format_property_pitch_line(), _join_natural(), _number_word(), PartiallyAvailableProperty (+57 more)

### Community 23 - "test_conversation_style.py"
Cohesion: 0.09
Nodes (39): Per-call mutable state tracked programmatically alongside the LLM's own…, ConversationAnalyzer, ConversationStyle, Turns raw guest transcript text into a TurnSignal. Deterministic, O(n) over the…, Exactly the structured prompt block the spec asks for -- the ONLY place…, A single immutable snapshot of how Mira should currently speak. Constructed…, render_style_block(), Covers the Conversation Style Engine (app/voice/conversation_style.py) --… (+31 more)

### Community 24 - "tool_handlers.py"
Cohesion: 0.10
Nodes (43): build_escalation_email_html(), build_photos_email_html(), HTML templates for host-facing transactional emails. Inline CSS only (no…, _whatsapp_link(), DispatchTechnicianArgs, EscalateToHostArgs, BaseModel, Pydantic argument models for the LLM tool functions, matching the parameter… (+35 more)

### Community 25 - "test_host_handoff.py"
Cohesion: 0.08
Nodes (40): UUID, Phase 7: the in-process signal a live voice pipeline coroutine waits on to…, Called once, at the start of a live call's pipeline, before the call could…, Called from the pipeline's own cleanup path, unconditionally -- every call that…, Called by app/api/v1/take_call.py's POST handler, immediately after its own…, Blocks until request_handoff(call_session_id) is called, or forever if it never…, register_call(), request_handoff() (+32 more)

### Community 26 - "upsert_lead"
Cohesion: 0.08
Nodes (55): backfill_lead(), backfill_lead_from_engagement(), delete_for_unqualified_call(), delete_if_empty(), get_active_booking(), _get_or_create_lead_for_call(), get_owned_lead(), list_leads() (+47 more)

### Community 27 - "test_max_call_duration.py"
Cohesion: 0.08
Nodes (29): _continuous_activity_frames(), _FakeWorker, Phase 2: independent hard ceiling on live-call lifetime (a reliability…, Sanity check on the other side of Test 1 -- the ceiling must not fire early. A…, Test 2 (brief), the mechanism half: a normal call finishing before the ceiling…, Structural proof, not behavioral: _enforce_max_call_duration's own signature…, Builds a frames_to_send list for pipecat.tests.utils.run_test: a…, Test 3 (brief), the most important regression test: simulates exactly the… (+21 more)

### Community 28 - "profile/page.tsx"
Cohesion: 0.05
Nodes (36): DashboardLayout(), OnboardingPage(), ProfilePage(), AiTrainingPage(), RootIndex(), DateRangeContext, DateRangeContextValue, DateRangeProvider() (+28 more)

### Community 29 - "integrations/__init__.py"
Cohesion: 0.18
Nodes (17): AsyncScript, acquire(), _get_scripts(), Exception, Redis, Low-level Redis primitives backing CallCoordinator's active-call ownership…, Unlike redis_client.get_client()'s callers (which treat None as "no-op, fall…, SET key value NX EX ttl_seconds -- ONE atomic command, no GET/EXISTS… (+9 more)

### Community 30 - "test_silence_watchdog.py"
Cohesion: 0.08
Nodes (56): _blank_transcript(), _capture_shadow_log(), asyncio, TranscriptionFrame, The core reported bug: a bot answer that takes longer than timeout_seconds to…, Once the long answer actually finishes, BotStoppedSpeakingFrame must still…, The confirmed auto-disconnect mechanism: before this fix, nothing reset…, Confirmed live: nudges were previously invisible on the calls page because the… (+48 more)

### Community 31 - "test_run_voice_pipeline_ringing.py"
Cohesion: 0.09
Nodes (35): WebSocket, The guest-facing side of a BUSY_RECOVERY decision: stop the ring tone, play the…, Keeps a CallCoordinator lease alive for the duration of a live call. Runs as a…, _reject_call_as_busy(), _renew_call_lease_periodically(), _run_pipeline(), run_voice_pipeline(), mock (+27 more)

### Community 32 - "RepetitionGuardProcessor"
Cohesion: 0.12
Nodes (33): _prices_in(), Frame, FrameDirection, FrameProcessor, Cuts a response short, mid-stream, the moment it starts repeating itself --…, Returns True if this sentence proves the response is repeating itself and…, Phase 4.2: catches a same-fact-different-wording repeat ACROSS turns that the…, Extracts every ₹-prefixed number as a normalized digit string (commas stripped)… (+25 more)

### Community 33 - "test_properties_api.py"
Cohesion: 0.04
Nodes (29): Call Ownership Schedule, Phase 1: a freshly created property (which never…, Regression test: exophone is unique in the DB, so a blank "" used to collide…, The test_property fixture never sets call_handling_mode either -- covers the…, 22:00 -> 06:00 (start > end) must remain a valid, storable value -- the…, An unrelated PATCH (e.g. just renaming) must not be forced to also supply…, SCHEDULED is a real, distinct third state -- not inferred from a populated…, The data-model fix: SCHEDULED with no window is meaningless -- there would be…, MIRA/HOST are complete, unconditional configurations on their own -- unlike… (+21 more)

### Community 34 - "test_property_retrieval_filter_builder.py"
Cohesion: 0.11
Nodes (37): apply_amenity_boost(), apply_landmark_boost(), apply_premium_boost(), matches_landmark(), Turns a RecommendPropertiesArgs into SQLAlchemy WHERE clauses -- pure, no I/O.…, Python-side fuzzy match against a property's own small `landmarks` list --…, Soft rank signal, not a hard filter -- a property lacking a landmark match…, Soft rank signal, same shape as apply_landmark_boost/apply_premium_boost --… (+29 more)

### Community 35 - "test_ringing_audio.py"
Cohesion: 0.09
Nodes (35): _load_pcm(), play_busy_message(), play_ringing_tone(), ValueError, WebSocket, Plays audio directly on the raw Exotel WebSocket during the window before the…, Never raises: this runs at MODULE IMPORT time, and this module is imported…, Raised by _load_pcm when a committed asset's actual WAV header doesn't match… (+27 more)

### Community 36 - "Booking"
Cohesion: 0.08
Nodes (46): patch, update_lead(), Booking, test_cancel_booking_from_other_host_forbidden(), test_cancel_booking_unblocks_dates(), _FakeFunctionCallParams, Covers Phase 1.2 (slot capture wired into tool wrappers) and Phase 1.4…, Availability-first recommendations, Implementation 3: a guest who gives both a… (+38 more)

### Community 37 - "call_summary_service.py"
Cohesion: 0.14
Nodes (25): _as_objection_tags(), _as_str_list(), _call_anthropic(), _call_groq(), _call_llm_with_fallback(), _call_openrouter(), CallSummaryError, _parse_summary_response() (+17 more)

### Community 38 - "pricing_engine.py"
Cohesion: 0.14
Nodes (26): _approved_negotiation_rules(), _approved_property_pricing_rules(), _condition_number(), _flat_fee(), _get_host_negotiation_policy(), HostNegotiationPolicy, _is_repeat_guest_for_host(), _length_of_stay_discount_percent() (+18 more)

### Community 39 - "Lead"
Cohesion: 0.09
Nodes (39): Lead, get_or_create_guest_profile(), Scoped by (phone, host_id) -- see memory-architecture-plan.md section 1 -- so…, AsyncSession, UUID, Guest Memory (memory-architecture-plan.md section 1) write path -- runs once,…, The one piece of GuestProfile.last_property_id's write contract that has a…, property_id/property_name come from the pipeline's own closure state (the… (+31 more)

### Community 40 - "properties.py"
Cohesion: 0.08
Nodes (49): add_property_photo(), _brand_candidate(), create_property(), delete_property(), get_portfolio_gallery(), get_property(), get_property_gallery(), import_airbnb_urls_trigger() (+41 more)

### Community 41 - "test_exotel_call_routing.py"
Cohesion: 0.07
Nodes (32): _FixedDatetime, _property_with(), datetime, Phase 4: GET /webhooks/exotel/call-routing -- the initial call-ownership…, Fixed clock 06:30 UTC = 12:00 IST -- inside a 22:00->06:00 overnight window…, Same overnight 22:00->06:00 window, fixed clock at 06:30 IST -- just past the…, Exact schedule start, [start, end) inclusive -> HOST. Fixed clock: 2026-08-11…, Exact schedule end, exclusive -> MIRA. Fixed clock: 2026-08-11 11:30 UTC =… (+24 more)

### Community 42 - "searchapi_client.py"
Cohesion: 0.09
Nodes (34): fetch_comparable_nightly_rates(), _fetch_listing_price_uncached(), fetch_listing_total_price(), fetch_nightly_rate(), fetch_property_coordinates(), nightly_rate_cache_key(), date, Exception (+26 more)

### Community 43 - "timedelta"
Cohesion: 0.10
Nodes (41): calculate_price(), host_id/requested_early_checkin/requested_late_checkout (Phase 6, Negotiation…, Step 18 item 20: the full authoring pipeline (parse -> approve) must produce a…, test_parsed_staged_policy_reaches_phase_4d_runtime_correctly(), _FakeRedis, _install_fake_redis(), _next_weekday(), date (+33 more)

### Community 44 - "test_style_compliance_monitor.py"
Cohesion: 0.12
Nodes (33): Frame, FrameDirection, FrameProcessor, Streaming observer wiring StyleComplianceRule into the live pipeline. Sits…, Never blocks, never withholds -- purely bookkeeping for the eventual finalize()…, The style/language validator. Deterministic script/token heuristic only -- no…, StyleComplianceMonitor, StyleComplianceRule (+25 more)

### Community 45 - "leads/page.tsx"
Cohesion: 0.06
Nodes (47): BookingRequestsTabContent(), isEmptyLead(), isReadyForHotLead(), KANBAN_COLUMNS, LeadCard(), leadDatesLabel(), leadReceivedLabel(), LeadsKanban() (+39 more)

### Community 46 - "card.py"
Cohesion: 0.19
Nodes (15): canonicalize_amenities(), canonicalize_amenity(), rank_amenities_for_pitch(), Small fixed synonym map for canonicalizing free-text amenity strings (e.g.…, Picks up to `limit` amenities from a property's free-text amenities list,…, UUID, PropertyCard -- a compact, formatting-layer projection of a Property row. Never…, test_canonicalize_amenities_dedupes_and_sorts() (+7 more)

### Community 47 - "call-ownership-card.tsx"
Cohesion: 0.06
Nodes (53): CALL_LOG_FILTERS, CallsPage(), FaqPage(), guestInitials(), GuestsPage(), OverviewPage(), OBJECTION_TAG_LABELS, PricingPageContent() (+45 more)

### Community 48 - "HybridCompletenessUserTurnStopStrategy"
Cohesion: 0.11
Nodes (20): HybridCompletenessUserTurnStopStrategy, _is_incomplete(), Frame, TranscriptionFrame, Experimental turn-detection strategy (shagun branch only -- see app/config.py's…, Fast, pure-string heuristic -- no LLM call, so it can't add latency., VAD-driven turn stop with a transcript-completeness extension. After VAD…, test_is_complete_full_sentence_with_terminal_punctuation() (+12 more)

### Community 49 - "config.py"
Cohesion: 0.05
Nodes (60): onboard_host(), AsyncSession, patch, post, UploadFile, Transcribes a prospective host's recorded voice agent intro (the "Add your…, Fills in the business/Airbnb-import fields Clerk's own sign-up form doesn't…, Shared by /me/photo and /me/banner -- same Cloudinary upload pattern as POST… (+52 more)

### Community 50 - "comparison_notes"
Cohesion: 0.13
Nodes (30): comparison_notes(), For each card, one clause naming the clearest way it differs from the CHEAPEST…, _card(), Covers comparison_notes (Recommendation engine v2 -- "why not that one" /…, Comparing against a property with no real rate on file would produce a…, Deterministic single baseline (the cheapest), not an every-pair matrix --…, Regression: a pricier option that ALSO sleeps fewer people than the cheapest (a…, exact_airbnb_pricing properties' stored base_price can be stale or a… (+22 more)

### Community 51 - "ConversationQuality"
Cohesion: 0.12
Nodes (28): Persists ConversationQuality's in-memory ValidationResults (app/voice/…, record_quality_events(), ConversationQuality, ConversationQuality -- the single, generic home for validator output and…, Generic, reusable result shape for ANY validator -- language compliance today,…, Per-call system-health/compliance record. Constructed fresh alongside…, ValidationResult, StyleComplianceMonitor -- a streaming observer that checks whether the LLM's… (+20 more)

### Community 52 - "recommend_properties"
Cohesion: 0.09
Nodes (40): AsyncSession, date, UUID, check_in/check_out/nights are optional and NOT part of RecommendPropertiesArgs…, recommend_properties(), Confirms this is purely additive -- behavior when dates aren't yet known (the…, Availability-first recommendations, Implementation 3: a candidate set with a…, When nights isn't explicitly passed, orchestrator.recommend_properties must… (+32 more)

### Community 53 - "AiTrainingSection"
Cohesion: 0.14
Nodes (11): AiTrainingSection(), handleApprove(), handleSaveEdit(), openEdit(), selectedFor(), togglePropertyForRule(), isDiscountTrigger(), ruleConditionSummary() (+3 more)

### Community 54 - "test_tool_handlers.py"
Cohesion: 0.13
Nodes (33): CheckCalendarArgs, GetPricingArgs, handle_check_calendar(), handle_get_pricing(), on_checked (Phase 4b.1, documentation/agent-conversation-improvement.md): same…, on_priced (Phase 4.1, documentation/agent-conversation-improvement.md):…, _next_saturday(), date (+25 more)

### Community 55 - "build_lead_system_prompt"
Cohesion: 0.06
Nodes (36): build_lead_system_prompt(), Availability-first recommendations, Implementation 4: per the task's own…, Availability-first recommendations, Implementation 4: confirms the rewritten…, Every existing call site that doesn't pass guest= must keep working exactly as…, Phase 8: next_follow_up must be written as a concrete next action for the host,…, Phase 8: escalate_to_host's urgency levels (low/medium/high/emergency) had no…, Recommendation conversations ("Phase X"): a guest narrowing down ("something…, Recommendation conversations ("Phase X"): required_amenities became a soft… (+28 more)

### Community 56 - "test_exotel_connect_routing.py"
Cohesion: 0.16
Nodes (28): _numbers(), _property_with(), Phase 8: GET /webhooks/exotel/connect-routing -- the Exotel Connect applet's…, Simulated via a monkeypatched User lookup rather than a dangling…, Regression for a Phase 8 review finding: User.phone has no format validator…, No CallSession at all, and the property is MIRA-mode -- neither routable path…, The endpoint must never read a caller-supplied destination -- only CallSid/To…, test_arbitrary_destination_cannot_override_handoff_routing() (+20 more)

### Community 57 - "test_negotiation_rules.py"
Cohesion: 0.07
Nodes (19): Covers the unified negotiation/pricing-training endpoint (see NegotiationRule's…, Phase 4D: stages is a new, optional field on NegotiationRuleUpdate/Out --…, Backward compatibility: every rule authored before Phase 4D has no stages value…, The exact Step 2 problem this phase closes: a host describing a 3-step pushback…, Example B from Step 15 -- "I don't negotiate" / a plain flat policy must not be…, Step 11/18 item 16: approving a staged rule approves the WHOLE ordered sequence…, Step 18 item 17: editing a staged rule's values (via PATCH stages, the same…, Self-review regression: a "custom" (property-scoped) staged rule approved with… (+11 more)

### Community 58 - "status-chip.tsx"
Cohesion: 0.24
Nodes (11): ActionableCard(), ActionableCardPriority, ActionableCardProps, priorityStatusTone, StatusChipProps, Badge(), badgeVariants, StatusTone (+3 more)

### Community 59 - "normalize_property_name"
Cohesion: 0.14
Nodes (26): _call_anthropic(), _call_groq(), _call_openrouter(), _clean_display_segment(), _derive_spoken_name(), _extract_counts(), _extract_property_type(), normalize_property_name() (+18 more)

### Community 60 - "test_language_heuristics.py"
Cohesion: 0.12
Nodes (25): devanagari_ratio(), english_word_ratio(), has_hinglish_token(), Shared, deterministic text-language heuristics -- no NLP, no embeddings, no LLM…, O(n) single pass. Returns (devanagari_fraction_of_letters, letter_count) --…, O(n). Fraction of whitespace-split tokens that are pure ASCII-letter words -- a…, _expected_language_family(), Returns "hindi", "english", or None (nothing known yet, e.g. before the Style… (+17 more)

### Community 61 - "User"
Cohesion: 0.09
Nodes (35): User, Demo seed script — creates a demo login + 12 realistic Indian properties. Usage…, seed(), auth_headers(), auth_headers_for(), _clean_call_leases(), _clean_tables(), client() (+27 more)

### Community 62 - "FaqEntry"
Cohesion: 0.18
Nodes (18): answer_faq_gap(), answer_faq_gap_voice(), create_faq_entry(), delete_faq_entry(), list_faq_entries(), AsyncSession, delete, patch (+10 more)

### Community 63 - "main.py"
Cohesion: 0.16
Nodes (15): _backfill_property_display_names(), _check_db_health(), _check_llm_health(), lifespan(), llm_health_status(), _PropagateToStdlib, Per-model health/latency from the last periodic check (see _check_llm_health…, One-shot, idempotent startup task: backfills display_name/… (+7 more)

### Community 64 - "redis_client.py"
Cohesion: 0.15
Nodes (19): cache_get_json(), cache_set_json(), get_client(), Any, Redis, Optional TTL cache for outbound API responses -- currently only SearchApi.io…, The one place a redis.asyncio.Redis instance is constructed for this process.…, _FakeRedis (+11 more)

### Community 65 - "faq_service.py"
Cohesion: 0.15
Nodes (24): cosine_similarity(), _attach_suggested_answers(), faq_gap_analytics(), FaqGap, full_property_context(), get_owned_unanswered_question(), list_faq_entries(), list_verified_property_faq() (+16 more)

### Community 66 - "call_classification_service.py"
Cohesion: 0.13
Nodes (25): _call_anthropic(), _call_groq(), _call_llm_with_fallback(), _call_openrouter(), CallClassificationError, classify_call(), _parse_classification_response(), _pre_check() (+17 more)

### Community 67 - "test_property_retrieval_ranking.py"
Cohesion: 0.15
Nodes (24): diversify_leading_candidates(), merge_and_rank(), Merges SQL and (optional) semantic search candidates into one ordered list.…, sql_results keeps its own order (SQL is authoritative). Any semantic_results…, Deterministic per-call seed -- the SAME call always gets the SAME rotation (a…, Rotates which property leads among a comparable-price band at the FRONT of an…, _rotation_seed(), _property() (+16 more)

### Community 68 - "Mira Memory Architecture — Implementation Plan"
Cohesion: 0.06
Nodes (34): 0.1 Non-negotiable guardrail: the live agent must not get slower, worse, or less reliable, 0.2 Standing verification protocol (applies after every single task below), 0. Cross-cutting decisions to lock in before coding, 1.1 Schema, 1.2 Population (write path), 1.3 Read path (prompt injection), 1.4 Frontend, 1.5 Verification (Standard verification, §0.2 — items 1, 3 required; 2 recommended since prompt-builder is touched) (+26 more)

### Community 69 - "system_prompt.py"
Cohesion: 0.08
Nodes (29): _active_seasonal_notes(), _BlankOnMissing, _caller_phone_section(), first_message_for(), _guest_memory_section(), lead_first_message_for(), _persona_and_escalation_sections(), date (+21 more)

### Community 70 - "SilenceWatchdogProcessor"
Cohesion: 0.08
Nodes (21): Frame, FrameDirection, FrameProcessor, Nudges, then hangs up on, a guest who's gone silent., True once a hangup has been armed (request_end_after_current_turn) or already…, Called by the end_call tool (app/voice/tools.py) once the LLM has committed to…, Called by PrematureEndCallGuardProcessor (app/voice/…, SilenceWatchdogProcessor (+13 more)

### Community 71 - "EscalationPhraseGuardProcessor"
Cohesion: 0.17
Nodes (21): EscalationPhraseGuardProcessor, Frame, FrameDirection, FrameProcessor, Code-level backstop for the "let me loop in the host" ban in GOLDEN_RULES…, Unconditionally replaces the one LLM response right after escalate_to_host…, _escalate_call_started(), _other_call_started() (+13 more)

### Community 72 - "dependencies"
Cohesion: 0.12
Nodes (17): @base-ui/react, date-fns, framer-motion, dependencies, @base-ui/react, date-fns, framer-motion, react (+9 more)

### Community 73 - "test_negotiation_policy_service.py"
Cohesion: 0.18
Nodes (23): _extract_json_rules(), Phase 4E (generalized negotiation policy authoring pipeline) -- direct, DB-free…, 7 stages -- explicitly proves the parser doesn't assume the classic 3-tier…, A length-1 'stages' is never a real progression (see _clean_stages' own…, Ambiguous/malformed stage data (two entries claiming the same order) must not…, discount_repeat_guest (loyalty) is not a pushback-progression concept -- a…, If neither a usable stages list nor a flat value is extractable, the rule is…, 5 stages, deliberately non-round values -- proves no hardcoded count or "nice… (+15 more)

### Community 74 - "LanguageSyncProcessor"
Cohesion: 0.16
Nodes (18): LanguageSyncProcessor, Frame, FrameDirection, FrameProcessor, Keeps Sarvam TTS synthesizing in whatever language the guest is actually…, Mirrors the guest's detected speech language onto the TTS service., asyncio, TranscriptionFrame (+10 more)

### Community 75 - "test_airbnb_import.py"
Cohesion: 0.09
Nodes (34): _apply_normalized_name_fields(), _extract_amenities(), _extract_cancellation_faq(), _extract_city(), _extract_description_faq(), _extract_guest_favorite_faq(), _extract_house_rules_and_times(), _extract_layout_faq() (+26 more)

### Community 76 - "run_sql_search"
Cohesion: 0.14
Nodes (23): apply_guest_count_filter(), build_base_filters(), _goa_region_localities(), Select, UUID, Budget/location/amenity filters only -- deliberately excludes the guest-count…, AsyncSession, Select (+15 more)

### Community 77 - "test_sarvam_vad_config.py"
Cohesion: 0.12
Nodes (20): SarvamSTTService.run_stt has no reconnect-on-failure logic at all -- confirmed…, _ReconnectingSarvamSTTService, _build_stt(), _connect_kwargs_for(), Phase 1 (background-audio false-turn fix): confirms the server-side Sarvam…, When an operator explicitly configures the noise-rejection knobs (e.g. after…, Regression guard on config.py's own documented starting-point default -- a…, Every sarvam_vad_* field except min_speech_frames must default to None -- Phase… (+12 more)

### Community 78 - "twilio_voice.py"
Cohesion: 0.11
Nodes (23): post, Request, Response, UploadFile, Twilio's "A call comes in" webhook -- see the module docstring for the two-step…, General-purpose "dictate into this field" transcription -- the mic button on…, transcribe_dictation(), twilio_voice_incoming() (+15 more)

### Community 79 - "components.json"
Cohesion: 0.09
Nodes (21): aliases, components, hooks, lib, ui, utils, iconLibrary, menuAccent (+13 more)

### Community 80 - "match_reasons_for_card"
Cohesion: 0.21
Nodes (20): match_reasons_for_card(), _purpose_phrase(), Compares a card's own fields against whichever RecommendPropertiesArgs fields…, _card(), Covers Phase 2.1 (documentation/agent-conversation-improvement.md):…, A guest asking for "pet friendly" must match a card whose top_amenities entry…, The most concrete/specific reason (a named amenity) should win a slot over a…, A property that just barely meets the count (exactly equal) is still a valid,… (+12 more)

### Community 81 - "test_availability_recovery.py"
Cohesion: 0.26
Nodes (21): _availability_calls(), _busy_lead(), _mock_twilio(), mock, Filters a mocked Twilio route's calls down to just the availability ("Mira is…, test_already_converted_guest_does_not_receive_availability_message(), test_already_notified_recovery_does_not_receive_duplicate_message(), test_busy_caller_becomes_pending_recovery() (+13 more)

### Community 82 - "GuestProfile"
Cohesion: 0.08
Nodes (38): GuestProfile, Guest Memory (memory-architecture-plan.md section 1) -- cross-call, per-host…, list_notifications(), mark_read(), AsyncSession, datetime, UUID, In-app notifications -- the dashboard's live Requests feed polls/streams from… (+30 more)

### Community 83 - "MetaCommentaryGuardProcessor"
Cohesion: 0.21
Nodes (16): MetaCommentaryGuardProcessor, Frame, FrameDirection, FrameProcessor, Code-level backstop for GOLDEN_RULES' ban on narrator/stage-direction text…, Drops parenthetical narrator/stage-direction asides like "(Waiting for guest…, asyncio, Regression for the exact live failure on 2026-07-27: 'May I have the best phone… (+8 more)

### Community 84 - "run_browser_lead_pipeline"
Cohesion: 0.24
Nodes (11): browser_test_offer(), BrowserOfferRequest, BrowserOfferResponse, AsyncSession, BaseModel, WebRTC signaling endpoint for the in-dashboard browser test client. Accepts an…, Same pipeline as a real call, but over WebRTC from the dashboard's "test in…, Same as run_browser_voice_pipeline, but for testing the Lead Agent flow across… (+3 more)

### Community 85 - "pipeline.py"
Cohesion: 0.08
Nodes (34): _build_llm(), _build_openrouter_llm(), _enforce_max_call_duration(), _FallbackGroqLLMService, _HandoffOutcome, _pick_groq_model(), UUID, Builds and runs one Pipecat voice pipeline per call. Two entry points share the… (+26 more)

### Community 86 - "UnansweredQuestion"
Cohesion: 0.12
Nodes (28): A guest question search_faq (app/services/tool_handlers.py handle_search_faq)…, UnansweredQuestion, backfill_faq_entry_embedding(), backfill_property_chunk_embedding(), backfill_unanswered_question_embedding(), EmbeddingError, get_embedding(), Exception (+20 more)

### Community 87 - "test_embedding_service.py"
Cohesion: 0.15
Nodes (15): _fake_openai_client(), _FakeRedis, _install_fake_redis(), No REDIS_URL (the pre-existing default) must behave exactly as before this…, A failed embedding call must return None and must not poison the cache with a…, Monkeypatches the AsyncOpenAI import get_embedding does internally -- counts…, Same in-memory fake as tests/test_redis_client.py -- avoids needing a real…, Cost Optimization ("Phase 16"): a second call for the exact same text must hit… (+7 more)

### Community 88 - "build_property_chunks"
Cohesion: 0.21
Nodes (17): _amenities_chunk_text(), build_property_chunks(), _house_rules_chunk_text(), _location_chunk_text(), _overview_chunk_text(), AsyncSession, Assembles a property's PropertyChunk text bodies from fields already extracted…, Returns {chunk_type: text} for every chunk type this property has real content… (+9 more)

### Community 89 - "EndCallReliabilityGuardProcessor"
Cohesion: 0.17
Nodes (22): EndCallReliabilityGuardProcessor, _normalize(), Frame, FrameDirection, FrameProcessor, Code-level backstop guaranteeing end_call actually fires whenever the model…, Arms the hangup itself if the model spoke the closing line without also calling…, _end_call_started() (+14 more)

### Community 90 - "twilio_client.py"
Cohesion: 0.06
Nodes (45): create_call_to_action_template(), create_text_template(), Exception, Twilio WhatsApp client -- real WhatsApp Business API delivery via a production…, Fire-and-forget wrapper around send_whatsapp_template -- same "log…, One-off setup call, not used on the hot path -- creates a twilio/call-to-action…, Same one-off provisioning shape as create_call_to_action_template above, for a…, Raised for any non-2xx response or unexpected shape from Twilio. (+37 more)

### Community 91 - "UserUpdate"
Cohesion: 0.18
Nodes (15): HostOnboarding, HostOnboardingResponse, BaseModel, field_validator, The Bright Data snapshot_id for the first property's scrape, still running when…, Business/Airbnb-import data collected on the post-signup onboarding page, once…, UserOut, UserUpdate (+7 more)

### Community 92 - "conversation_style.py"
Cohesion: 0.15
Nodes (19): _consecutive_turns_agreeing_with(), _default_style(), _language_family_from_score(), The Conversation Style Engine -- computes a single, immutable ConversationStyle…, history[0] is the OLDEST turn, history[-1] is the CURRENT turn (same append-…, Deterministic mapping from the weighted score to a 3-way language family.…, Whether the weighted score sits solidly inside the candidate family's own band,…, Owns its own rolling-window history internally (_history) -- this is the Style… (+11 more)

### Community 93 - "RedundantContextGuardProcessor"
Cohesion: 0.16
Nodes (16): Frame, FrameDirection, FrameProcessor, Guard against pipecat re-invoking the LLM with an unchanged context. Root-…, Drops an LLMContextFrame if nothing was added to context since the last one…, RedundantContextGuardProcessor, _context_frame(), asyncio (+8 more)

### Community 94 - "_completed_turn"
Cohesion: 0.09
Nodes (23): _completed_turn(), Even once repetition_shadow_candidate becomes True, the TIMER is still…, Phase 5D: the real pipeline sequence for one genuine, completed guest turn -- a…, Phase 5D update of Phase 5A's own root-cause characterization test: with the…, Step 15/matrix item 11 (Phase 5C origin, re-verified under Phase 5D semantics):…, Items 2/3 (7s / 8.9s of a 9s window): answering with any real margin before the…, After nudge #1 fires, the guest answers -- the completed turn resets strikes…, Item 9: 'umm, one second' is a single, non-repeated utterance -- a genuinely… (+15 more)

### Community 95 - "calendar_service.py"
Cohesion: 0.09
Nodes (47): fetch_ical(), ICalEvent, parse_ical(), date, datetime, Fetches and parses Airbnb (or any) iCal export feeds into booking date ranges., Parse raw .ics text into a list of booking events. Airbnb's exported iCal marks…, _to_date() (+39 more)

### Community 96 - "StatePromptSyncProcessor"
Cohesion: 0.15
Nodes (20): Frame, FrameDirection, FrameProcessor, Keeps one state-summary system message in context, always up to date, right…, StatePromptSyncProcessor, _context_frame(), _is_injected_state_block(), asyncio (+12 more)

### Community 97 - "MIRA Conversational Architecture — Current State, 5 Aug"
Cohesion: 0.10
Nodes (20): 10. Where booking confirmation happens, 11. Deterministic vs. LLM-driven, 1. Complete request lifecycle: Exotel → Pipecat → LLM → tools → TTS, 2. Where conversation state is stored, 3. How booking information is currently collected, 4. Whether conversation memory exists, 5. How prompts are constructed, 6. Files controlling conversation flow (+12 more)

### Community 98 - "PrematureEndCallGuardProcessor"
Cohesion: 0.19
Nodes (16): PrematureEndCallGuardProcessor, Frame, FrameDirection, FrameProcessor, Code-level backstop for a call ending itself without ever giving the guest a…, Cancels a same-turn end_call if that turn also asked the guest a real question., _end_call_started(), _other_call_started() (+8 more)

### Community 99 - "negotiation_policy_service.py"
Cohesion: 0.18
Nodes (15): parse_negotiation_policy(), post, Parses the host's free-text (typed or dictated) negotiation/pricing policy into…, build_pending_rules(), _call_anthropic(), call_configured_llm_for_json(), _call_groq(), _call_openrouter() (+7 more)

### Community 100 - "test_negotiate_rate_tool_wrapper.py"
Cohesion: 0.23
Nodes (16): _dates(), _FakeFunctionCallParams, Phase 4D -- wrapper-level tests for app/voice/tools.py's negotiate_rate…, Simulates noisy/background speech producing a duplicate tool call -- two…, Negative case -- calling again with the SAME dates/guest count must NOT reset…, test_abuse_1_repeated_unquantified_pushback_does_not_burn_stages(), test_abuse_2_repeated_identical_offer_does_not_progress(), test_abuse_5_duplicate_tool_call_same_offer_cannot_consume_another_stage() (+8 more)

### Community 101 - "test_property_lock.py"
Cohesion: 0.19
Nodes (13): _FakeFunctionCallParams, Covers the property-lock bug fix in app/voice/conversation_state.py +…, Fix B: once a property is locked, calling recommend_properties again with no…, A guest explicitly asking to compare/switch ('something in Goa instead')…, Guest Support calls already have a fixed property_id at the closure level --…, Reproduces the reported bug: Lead Agent call (property_id=None), guest names a…, I'd like to look at Ocean View instead' -- the next tool call naming a…, _second_property() (+5 more)

### Community 102 - "compilerOptions"
Cohesion: 0.07
Nodes (26): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+18 more)

### Community 103 - "compilerOptions"
Cohesion: 0.07
Nodes (28): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+20 more)

### Community 104 - "PropertyChunk"
Cohesion: 0.23
Nodes (15): PropertyChunk, One chunk of a property's text, embedded separately by chunk_type…, AsyncSession, The one place in this codebase that makes a synchronous embedding API call on…, Never raises, never blocks longer than timeout_seconds -- any failure/timeout…, run_semantic_search(), _search(), test_run_semantic_search_blank_query_returns_empty() (+7 more)

### Community 105 - "get"
Cohesion: 0.08
Nodes (37): analytics_objection_insights(), analytics_quality_events(), analytics_recovery(), analytics_summary(), analytics_timeseries(), AsyncSession, Busy Call Recovery funnel/KPIs (documentation/architecture: Phase 7). Read-only…, Cross-call guard/validator-firing analytics (docs/tasks/ building-… (+29 more)

### Community 106 - "test_negotiate_rate_guest_memory.py"
Cohesion: 0.29
Nodes (11): _approved_repeat_guest_rule(), _next_weekday(), date, Covers negotiate_rate's real Guest Memory wiring (memory-architecture-plan.md…, The real signal (GuestProfile.total_stays >= 2 for THIS host) must win even if…, total_stays == 1 means this is their first-ever call with this host (the…, No resolvable guest profile at all (e.g. a caller_number that never got…, test_guest_profile_lookup_failure_falls_back_to_guest_loyalty() (+3 more)

### Community 107 - "PropertiesPage"
Cohesion: 0.17
Nodes (7): normalizeForSubmit(), PropertiesPage(), handleCreate(), handleSaveEdit(), handleTestInBrowser(), openEdit(), propertyToForm()

### Community 108 - "answer_faq_gap"
Cohesion: 0.35
Nodes (10): answer_faq_gap(), Converts an unanswered-question group into a real, verified FaqEntry, and marks…, _add_gap(), test_answer_faq_gap_apply_to_property_scopes_the_new_entry(), test_answer_faq_gap_creates_verified_entry_and_clears_whole_group(), test_faq_gap_analytics_breakdowns(), test_faq_gaps_api_list_and_answer(), test_get_owned_unanswered_question_enforces_ownership() (+2 more)

### Community 109 - "_call_session_for"
Cohesion: 0.18
Nodes (11): _call_session_for(), MIRA-mode property (so the initial-HOST resolution path would itself refuse to…, A CallSession exists (e.g. attach_exotel_call already ran off the status…, A handoff_status of "connecting" (a future lifecycle value, not yet written by…, Baseline for the bug below: finalize_call_session's own default…, The actual fix, verified end-to-end against the real service function and the…, test_call_finalized_as_completed_by_default_cannot_be_routed(), test_call_finalized_as_in_progress_for_handoff_can_still_be_routed() (+3 more)

### Community 110 - "devDependencies"
Cohesion: 0.12
Nodes (17): eslint, eslint-config-next, devDependencies, eslint, eslint-config-next, tailwindcss, @tailwindcss/postcss, @types/node (+9 more)

### Community 111 - "test_email_client.py"
Cohesion: 0.36
Nodes (7): Thin SMTP wrapper for host-facing email notifications. Interim stand-in for a…, send_email(), _configure_smtp(), Scale Readiness ("Phase 17"): aiosmtplib.send's own default is 60s -- confirms…, test_send_email_passes_a_bounded_timeout_to_aiosmtplib(), test_send_email_skipped_when_smtp_not_configured(), test_send_email_timeout_is_overridable()

### Community 112 - "exotel_client.py"
Cohesion: 0.22
Nodes (12): hangup_call(), Thin wrapper around Exotel's REST API. The actual voice conversation runs…, Force-terminates a live call via Exotel's Calls API. Closing our end of the…, _hangup_exotel_call(), The one place that calls exotel_client.hangup_call from this module -- shared…, mock, test_hangup_call_as_detached_task_does_not_block_the_caller(), test_hangup_call_failure_does_not_permanently_block_a_retry() (+4 more)

### Community 113 - "schemas/negotiation_rule.py"
Cohesion: 0.31
Nodes (9): NegotiationPolicyParseRequest, NegotiationPolicyParseResponse, NegotiationRuleOut, NegotiationRuleUpdate, NegotiationStage, BaseModel, One entry in an OPTIONAL, host-defined negotiation ladder (Phase 4D, see…, Drafts only -- nothing here has been applied to pricing/negotiation yet. The… (+1 more)

### Community 114 - "Tables"
Cohesion: 0.11
Nodes (19): Alembic, `bookings` (`Booking`, `app/models/booking.py`), `call_leases` (`CallLease`, `app/models/call_lease.py`) — STAGED FOR REMOVAL, NOT WRITTEN TO, `call_quality_events` (`CallQualityEvent`, `app/models/call_quality_event.py`), `call_sessions` (`CallSession`, `app/models/call_session.py`), Common pitfalls specific to this schema, Database Schema, `faq_entries` (`FaqEntry`, `app/models/faq_entry.py`) (+11 more)

### Community 115 - "to_india_whatsapp_digits"
Cohesion: 0.33
Nodes (8): Bare digit string, country-code-prefixed, for WhatsApp addressing. Exotel…, to_india_whatsapp_digits(), Staff-engineer review finding: email_templates._whatsapp_link and…, test_already_91_prefixed_number_is_untouched(), test_bare_ten_digit_number_gets_91_prefix(), test_empty_string_returns_empty_string(), test_non_ten_digit_non_91_number_is_left_as_is(), test_plus_and_spaces_are_stripped()

### Community 116 - "deploy"
Cohesion: 0.20
Nodes (9): build, builder, dockerfilePath, deploy, healthcheckPath, healthcheckTimeout, restartPolicyMaxRetries, restartPolicyType (+1 more)

### Community 117 - "test_database.py"
Cohesion: 0.20
Nodes (5): Scale Readiness ("Phase 17"): confirms the engine's connection pool is…, Regression: pool_recycle is a proactive age ceiling that complements…, Behavior-preserving by default: unless overridden via env vars, the new…, test_default_pool_settings_match_previous_implicit_sqlalchemy_defaults(), test_engine_pool_pre_ping_still_enabled()

### Community 118 - "ConversationStyleProcessor"
Cohesion: 0.18
Nodes (17): ConversationStyleProcessor, Frame, FrameDirection, FrameProcessor, Wires ConversationAnalyzer + StyleEngine into the live pipeline. Sits right…, asyncio, TranscriptionFrame, Hard requirement: this processor must be fully additive alongside… (+9 more)

### Community 119 - "guests.py"
Cohesion: 0.24
Nodes (15): get_guest(), get_guest_detail(), list_guests(), AsyncSession, patch, UUID, update_guest(), ConversationSummaryEntry (+7 more)

### Community 120 - "._recompute_goal"
Cohesion: 0.13
Nodes (10): _dates_known(), Any, One key's raw attention bookkeeping. Generic over what `key` means (a…, Repetition count decayed by how many turns ago it was last touched -- a fact…, Records one more mention of `key` at the current turn. Callers: set_slot…, Set a single slot field. Never call this with a blind dict merge -- a tool call…, Called by silence_watchdog when a pending close gets cancelled -- either…, Derive conversation_goal from the strongest available signal. Tool-driven… (+2 more)

### Community 121 - "Settings"
Cohesion: 0.22
Nodes (5): get_settings(), field_validator, model_validator, Settings, BaseSettings

### Community 122 - "property_recommendation_guard.py"
Cohesion: 0.17
Nodes (15): _amount_present(), _extract_amounts(), _fallback_availability_text(), _fallback_price_text(), _fallback_recommendation_text(), _format_partial_availability_correction(), Frame, FrameDirection (+7 more)

### Community 123 - "frontend/package.json"
Cohesion: 0.22
Nodes (8): name, private, scripts, build, dev, lint, start, version

### Community 124 - "REST API Reference"
Cohesion: 0.12
Nodes (17): `analytics.py` — `/analytics`, `auth.py` — `/auth`, `bookings.py` — `/bookings`, `calls.py` — `/calls`, `faq.py` — `/faq`, `GET /health` and `GET /api/v1/health/llm`, `guests.py` — `/guests`, `leads.py` — `/leads` (+9 more)

### Community 125 - "test_system_prompt.py"
Cohesion: 0.18
Nodes (19): _active_booking_section(), Surfaces a guest's own upcoming/current confirmed booking (property + dates),…, _booking(), _guest(), A GuestProfile row that was JUST created for this call (total_stays still 0,…, test_active_booking_section_empty_when_no_booking(), test_active_booking_section_handles_missing_dates(), test_active_booking_section_handles_missing_guest_name() (+11 more)

### Community 126 - "_normalize_phone"
Cohesion: 0.39
Nodes (3): _normalize_phone(), field_validator, Keep only digits, then the last 10. Confirmed live: a guest self-correcting…

### Community 127 - "founder-console/package.json"
Cohesion: 0.09
Nodes (21): dependencies, next, react, react-dom, devDependencies, @types/node, @types/react, typescript (+13 more)

### Community 128 - "BrightDataError"
Cohesion: 0.20
Nodes (15): AirbnbUrlImportStatus, import_airbnb_urls_status(), _listing_id_from_url(), Poll endpoint for a Bright Data scrape job. While "running", the frontend…, BrightDataError, get_snapshot_data(), get_snapshot_status(), _headers() (+7 more)

### Community 130 - "leads.py"
Cohesion: 0.25
Nodes (14): BulkDismissRequest, BulkDismissResponse, dismiss_service_requests(), get_lead(), list_leads(), AsyncSession, BaseModel, post (+6 more)

### Community 131 - "frontend/src/app/layout.tsx"
Cohesion: 0.33
Nodes (4): libreBaskerville, metadata, montserrat, Toaster()

### Community 132 - "_clean_stages"
Cohesion: 0.33
Nodes (6): _clean_stages(), Validates a parsed `stages` value with the same fail-closed discipline as…, Direct unit coverage of _clean_stages itself, arbitrary/varied values, not…, Self-review regression: JSON's number type doesn't distinguish int/float, so an…, test_clean_stages_accepts_integer_valued_float_order(), test_clean_stages_directly_arbitrary_values()

### Community 133 - "technician_service.py"
Cohesion: 0.50
Nodes (4): find_technician(), AsyncSession, UUID, Tier 1-adjacent technician lookup. Matches the best-rated technician for a…

### Community 135 - "test_negotiate_rate_host_policy.py"
Cohesion: 0.19
Nodes (15): _next_weekday(), date, Covers negotiate_rate's Host Memory wiring (memory-architecture-plan.md section…, Simulates the NegotiationRule DB query itself failing (e.g. a transient error)…, host_id=None (the default, and what every call site used before this change)…, A host_id that exists but has zero approved discount_* NegotiationRule rows…, A rule that hasn't been approved yet must have zero effect on live negotiation…, test_approved_guest_requests_rule_sets_discount_ceiling() (+7 more)

### Community 136 - "founder-console/src/app/page.tsx"
Cohesion: 0.50
Nodes (4): COST_REFERENCE, FounderDashboard(), getLlmHealth(), LlmModelHealth

### Community 137 - "Voice Agent Design"
Cohesion: 0.12
Nodes (16): Availability-first recommendations (Lead Agent), Busy Call Recovery, Call teardown, GOLDEN_RULES (`app/prompts/system_prompt.py`), Groq multi-model fallback, Host/guest notifications (in-app + email + Twilio WhatsApp Business API), Host handoff ("Take Call"), Post-call classification (`JUNK`/spam labeling, separate from live aversion) (+8 more)

### Community 138 - "amenity_checklist_note"
Cohesion: 0.19
Nodes (14): amenity_checklist_note(), Recommendation conversations ("Phase X"): required_amenities is now a SOFT…, Covers amenity_checklist_note (Recommendation conversations, "Phase X"):…, A single requested amenity is already covered by match_reasons_for_card's own…, An all-matched property already reads as a clean fit -- no need for an explicit…, An all-missing property states nothing present -- a checklist of pure absence…, The actual case this function exists for: has SOME but not all -- both halves…, swimming pool" (a real, differently-worded amenity_tags entry) must still match… (+6 more)

### Community 139 - "Building Intelligence — Task List"
Cohesion: 0.13
Nodes (14): Building Intelligence — Task List, Closing regression pass (after all four implementations), Implementation 1 — Guard/quality telemetry persistence, Implementation 2 — Objection/failure tagging in post-call summary, Implementation 3 — FAQ-gap-style aggregate: guard/quality weekly digest, Implementation 4 — Objection-correlated pricing insight surface (human-gated), Task 1.1 — Implementation, Task 1.2 — PR Review (+6 more)

### Community 140 - "Mira Conversational Behaviour — Superhost-Quality Task Sheet"
Cohesion: 0.13
Nodes (14): Mira Conversational Behaviour — Superhost-Quality Task Sheet, Non-goals (explicit, per the codebase's own established discipline), Phase 0 — Baseline instrumentation (do first; every later phase's verification depends on this existing), Phase 1 — Extend `ConversationState` to carry real slot/lifecycle state (requirement #7, #9, #10), Phase 2 — Recommendation quality: explain the "why" (requirement #3, #4), Phase 3 — Language adaptation made continuous and code-aware, not prompt-only (requirement #1, #2), Phase 4 — Repetition and "already said" awareness upgraded from text-similarity to state-aware (requirement #6), Phase 4a — In-call memory has no ceiling; bound it before it becomes a real cost/latency risk (+6 more)

### Community 141 - "Voice Pipeline"
Cohesion: 0.13
Nodes (14): 12 Voice Tools, Bot Speaks First (Greeting), GOLDEN_RULES (key constraints for the LLM), Guard Processors — What Each Guards Against (Confirmed Live Failures), LLM Routing — Groq Fallback Chain, Pipeline Stage Order, Ringing Tone (Exotel calls only), Silence Watchdog (+6 more)

### Community 142 - "test_explicit_language_preference.py"
Cohesion: 0.20
Nodes (11): _FakeFunctionCallParams, Covers Phase 3.3 (documentation/agent-conversation-improvement.md):…, Reproduces catalogue item C5 directly: guest asks 'can you speak Hindi?' -- the…, A later update_lead call for an unrelated field (e.g. just num_guests) must not…, A value outside the constrained english/hindi vocabulary (e.g. the model…, Deliberately NOT persisted to the Lead DB row -- this only ever needs to live…, test_update_lead_preferred_language_never_written_to_lead_record(), test_update_lead_sets_explicit_language_preference_english() (+3 more)

### Community 143 - "generate_busy_message_speech.py"
Cohesion: 0.67
Nodes (3): main(), One-off setup: generates the real spoken busy-recovery message played once,…, _synthesize()

### Community 144 - "generate_busy_message_tone.py"
Cohesion: 0.67
Nodes (3): _generate_beep(), main(), One-off setup: generates the placeholder clip played once, then hung up, when…

### Community 145 - "generate_ringing_tone.py"
Cohesion: 0.67
Nodes (3): _generate_cycle(), main(), One-off setup: generates the phone-ringing tone played on the raw Exotel…

### Community 146 - "test_today_anchor_january_has_no_earlier_month_this_year_to_roll_over"
Cohesion: 0.33
Nodes (5): _FixedDatetime, datetime, January is the one month with no earlier month within the same year to…, Patches system_prompt's `datetime.now(IST)` call to a fixed instant, so…, test_today_anchor_january_has_no_earlier_month_this_year_to_roll_over()

### Community 147 - "photos/page.tsx"
Cohesion: 0.67
Nodes (3): getGallery(), PropertyGallery, PropertyPhotosPage()

### Community 148 - "Patterns"
Cohesion: 0.14
Nodes (13): Category 1 — Common task patterns, Category 2 — Integration patterns, Category 3 — Debug/diagnosis patterns, Category 4 — Deploy/release patterns, Format, How many patterns to generate, How patterns get created, Multi-section pattern (one file = multiple related tasks) (+5 more)

### Community 202 - "_parse_iso_date"
Cohesion: 0.40
Nodes (5): _parse_iso_date(), date, state.slots holds check_in/check_out as EITHER an ISO string (update_lead's…, Availability-first recommendations, Implementation 2 (self-review find):…, test_parse_iso_date_accepts_both_iso_string_and_raw_date_object()

### Community 209 - "PropertyUpdate"
Cohesion: 0.22
Nodes (5): PropertyUpdate, field_validator, model_validator, Blocks the obvious SSRF case: a host pointing ical_url at an internal/ cloud-…, _validate_ical_url()

### Community 210 - "test_repeat_guest_booking_vs_enquiry.py"
Cohesion: 0.23
Nodes (12): _approved_repeat_guest_rule(), _next_weekday(), date, Phase 4E, Step 5/8 -- regression tests documenting the CURRENT, CONFIRMED…, CONFIRMED CURRENT BEHAVIOR: even a guest with an EXPLICITLY host-confirmed…, CONFIRMED CURRENT BEHAVIOR: the eligibility threshold ("how many stays make…, CONFIRMED CURRENT BEHAVIOR (partially satisfies Step 6): GuestProfile is…, CONFIRMED CURRENT BEHAVIOR (not a product decision this test makes): a guest… (+4 more)

### Community 218 - "Task 3.2 — PR Review"
Cohesion: 0.15
Nodes (13): 1. The "partial was dead code before window_start/window_end" claim — CONFIRMED, independently, 2. Boundary-case interval math — CONFIRMED correct, clamp fix is real and load-bearing, 3. Status definition consistency — CONFIRMED, 4. No duplicate/divergent "booked" definition — CONFIRMED, 5. Batched variant is genuinely one query — CONFIRMED, 6. `next_available_window` and its callers — CONFIRMED untouched, 7. `not_found` vs. partial-only distinction — CONFIRMED correct, 8. Real-adversarial-LLM transcript check — RAN, RESULT: NOT FULLY CONFIRMED (non-blocking finding, see below) (+5 more)

### Community 219 - "Call Qualification / Junk-Call Detection — Task List"
Cohesion: 0.15
Nodes (12): Call Qualification / Junk-Call Detection — Task List, Task 10 — Frontend: filter tabs + Hidden Filters chip, Task 11 — Full end-to-end regression pass, Task 1 — DB migration: `call_type` / `classification_confidence` / `classification_reason`, Task 2 — Classification schema + centralized service, Task 3 — Persist classification: `call_service.set_call_classification`, Task 4 — Lead suppression: `lead_service.delete_for_unqualified_call`, Task 5 — Wire into `on_pipeline_finished` (+4 more)

### Community 230 - "Current Architecture (as of 2026-09-02)"
Cohesion: 0.15
Nodes (13): 10. Common failure modes, 11. Development rules for modifying this architecture, 1. High-level call flow, 2. Voice pipeline (live call), 3. CallCoordinator (concurrency ownership), 4. Busy Call Recovery, 5. Lead safety / lead preservation, 5b. Availability-first recommendations (+5 more)

### Community 231 - "Mira Dashboard — UI/UX Restructure Task Sheet"
Cohesion: 0.15
Nodes (12): Decisions locked in, Mira Dashboard — UI/UX Restructure Task Sheet, Other wiring gaps noticed during research (not explicitly requested, flagging per your ask), Phase 0 — Shared primitives (do first, everything else depends on this), Phase 1 — Call detail: escalation info + consistent entry point, Phase 2 — Overview page: fixed sidebar + live requests/FAQ both visible above the fold, Phase 3 — Merge Live Requests into Leads; right-panel lead detail; Kanban default, Phase 4 — Right-panel conversion for remaining dialogs (+4 more)

### Community 232 - "faq.py"
Cohesion: 0.36
Nodes (8): FaqEntryCreate, FaqEntryOut, FaqEntryUpdate, BaseModel, FaqGapAnalyticsOut, FaqGapAnswerIn, FaqGapOut, BaseModel

### Community 233 - "Pricing, Negotiation, Lead Qualification & Airbnb Import"
Cohesion: 0.18
Nodes (11): Airbnb import: two paths, one convergence point, `exact_airbnb_pricing` — why it exists, Lead qualification (`lead_service.py`), Negotiation (`pricing_engine.negotiate_rate`), Negotiation & pricing rules (`negotiation_rules` ↔ `pricing_rules`), Path 1: Bright Data URL-paste (primary, `POST /properties/import-airbnb-urls`), Path 2: JSON-upload (advanced, `POST /properties/import`), Price calculation (`pricing_engine.calculate_price`) (+3 more)

### Community 234 - "Availability-First Recommendations — Task List"
Cohesion: 0.18
Nodes (10): Availability-First Recommendations — Task List, Closing regression pass (after all five implementations), Implementation 1 — Duration-first slot: ask "how many nights" before exact dates, Implementation 2 — Unconditional availability pre-filter in `recommend_properties`, Task 1.1 — Implementation, Task 1.2 — PR Review, Task 1.3 — Reverify, Task 2.1 — Implementation (+2 more)

### Community 235 - "Project State"
Cohesion: 0.18
Nodes (11): Active branch, Currently implemented, In progress / uncommitted (implemented and tested, not yet merged to `main`), Known issues / in-flight work, Known limitations, Known risks, Next priorities, Open design questions (+3 more)

### Community 236 - "Architecture"
Cohesion: 0.20
Nodes (10): After first deploy (any host), Architecture, Backend, Deployment — current topology (as of 2026-07-21), End-to-end data flow: a guest phone call, Frontend, Railway (backend), Render (backend + frontend, kept as fallback) (+2 more)

### Community 237 - "Task 5.2 — PR Review"
Cohesion: 0.20
Nodes (10): 1. Append/continue fix — CONFIRMED real and correct, independently reproduced with new scenarios, 2. `_real_dates_stated` edge-case hunt — CONFIRMED a real, non-blocking gap: some correct rephrasings still redundantly re-corrected, 3. Zero new LLM call — CONFIRMED, 4. `_fallback_recommendation_text` scope decision — CONFIRMED correct by direct test, not just asserted, 5. `conflicting_days` false-positive hunt — CONFIRMED a real, reproducible false-positive; not the scenario proposed in the review brief, but a materially similar one found nearby, 6. Test suite — CONFIRMED exact counts, 7. Whole-file read — CONFIRMED natural extension, consistent conventions, with one stale-docstring finding, Findings resolved (both fixed before Task 5.3, per the review's own recommendation) (+2 more)

### Community 238 - "Task 3.3 — Reverify"
Cohesion: 0.20
Nodes (10): 1. Boundary-case interval math — CONFIRMED correct, independently, 2. Fresh seeded-partial-overlap check through the real live call path — CONFIRMED, 3. Fresh real-LLM adversarial check — THE MOST IMPORTANT ITEM, 4. `RecommendationResult`/`PropertyCard`/`.partially_available` consumer audit — CONFIRMED safe, 5. Full test suite — CONFIRMED matches Task 3.2's claimed count exactly, 6. `CLAUDE.md` invariants — CONFIRMED directly against the current diff, Cleanup confirmation, Implementation 3 — Partial-availability classification (+2 more)

### Community 239 - "Task 5.3 — Reverify"
Cohesion: 0.20
Nodes (10): 1. Whole-file read — CONFIRMED clean, no leftover cruft from the abandoned fix attempts, 2. Independent date-matching re-verification — the fix holds against new adversarial input; one new (non-blocking) gap found, no new false-positive found beyond the already-known/accepted class, 3. Test suite — CONFIRMED exact counts, run independently, 4. "No hidden LLM regeneration" invariant — CONFIRMED against the current file, 5. Closing cross-implementation sanity check, Implementation 5 — Extend `PropertyRecommendationGuardProcessor` for partial-availability claims, New findings summary (beyond what Task 5.2 already recorded), Task 5.1 — Implementation (+2 more)

### Community 240 - "Decision Log"
Cohesion: 0.20
Nodes (9): Decision Log, Decisions, Exotel WSS token as path segment, not query param, No DB mocking in tests — real Postgres only, pipecat-ai pinned with explicit floor+cap (not unbounded >=), Pipecat STT/TTS connected sequentially (pre-connect attempt reverted), Railway (backend) + Vercel (frontend) as primary; Render kept as fallback, Use Groq with health-checked multi-model fallback chain (+1 more)

### Community 241 - "test_property_display_name_backfill.py"
Cohesion: 0.36
Nodes (8): backfill_missing_display_names(), Cross-host, one-time (but safe to re-run) backfill for properties imported…, test_backfill_is_genuinely_idempotent_even_when_normalizer_derives_nothing(), test_backfill_is_idempotent_and_skips_already_backfilled_properties(), test_backfill_populates_display_name_for_pre_existing_properties(), test_backfill_runs_across_multiple_hosts(), test_renormalize_one_llm_fallback_still_enabled_by_default(), test_startup_backfill_never_calls_the_llm_fallback()

### Community 242 - "cloudinary_client.py"
Cohesion: 0.31
Nodes (8): _ensure_configured(), Re-hosts property photos scraped via Bright Data (see…, Uploads one remote image (Cloudinary fetches it server-side, no download…, Uploads up to max_images of source_urls concurrently, in listing order. Airbnb…, Uploads a host-provided image file (from the property edit dialog's "add…, upload_image_bytes(), upload_image_from_url(), upload_images_from_urls()

### Community 243 - "handle_negotiate_rate"
Cohesion: 0.33
Nodes (9): NegotiateRateArgs, NegotiationResult, handle_negotiate_rate(), on_priced (Phase 4b.1, documentation/agent-conversation-improvement.md): same…, Confirms handle_negotiate_rate's new host_user_id parameter actually reaches…, test_negotiate_rate_never_quotes_zero_when_base_price_is_zero(), test_negotiate_rate_on_priced_fires_with_real_property_and_result(), test_negotiate_rate_returns_message() (+1 more)

### Community 244 - "handle_search_faq"
Cohesion: 0.33
Nodes (9): SearchFaqArgs, handle_search_faq(), on_answered (Phase 4b.3, documentation/agent-conversation-improvement.md):…, Phase 4b.3 (documentation/agent-conversation-improvement.md): on_answered is…, No property_id resolved at all (a true portfolio-wide query) -- there is no…, test_search_faq_logs_gap_when_no_verified_answer(), test_search_faq_no_gap_logged_when_answer_found(), test_search_faq_on_answered_fires_with_real_property_when_resolved() (+1 more)

### Community 246 - "MIRA — Codebase Guide"
Cohesion: 0.22
Nodes (9): Architecture overview, Common pitfalls, Critical invariants, Demo account, graphify, Key env vars (`backend/app/config.py`), MIRA — Codebase Guide, Quick start (+1 more)

### Community 247 - "Task 4.2 — PR Review"
Cohesion: 0.22
Nodes (9): 1. Full `LEAD_AGENT_INSTRUCTIONS` re-read top to bottom — CONFIRMED internally consistent, 2. Genuinely prompt-only — CONFIRMED, 3. The two deliberately-updated tests — CONFIRMED legitimate, not weakened, 4. Independent real-transcript check — RAN, RESULTS BELOW, 5. Doc accuracy — CONFIRMED against real shipped prompt text, not just plausible-sounding, 6. Guest Support mode untouched — CONFIRMED, 7. Full regression suite — CONFIRMED matches baseline exactly, Findings summary (+1 more)

### Community 248 - "Mira Dashboard Redesign — Task Sheet"
Cohesion: 0.22
Nodes (8): Execution model, Mira Dashboard Redesign — Task Sheet, Phase 0 — Backend additions identified so far, Phase 1 — Foundation (infrastructure only, no visual page changes), Phase 2 — Simple page polish (styling-only), Phase 3 — Component-heavy restyle, Phase 4 — New interaction patterns (highest risk, reviewed independently), Standing rules (apply to every task, no exceptions)

### Community 249 - "Voice Pipeline Changes"
Cohesion: 0.22
Nodes (8): Context, Debug, Gotchas, Steps, Task: Add a Guard Processor, Update Scaffold, Verify, Voice Pipeline Changes

### Community 250 - "silence_watchdog.py"
Cohesion: 0.36
Nodes (5): _normalize(), Code-level backstop guaranteeing a single LLM turn can't repeat itself into the…, _word_overlap(), Ends a call when the guest goes silent/unresponsive for too long, or when the…, Computes, logs, and returns whether this transcript participates in a repeated…

### Community 251 - "test_call_summary_notification.py"
Cohesion: 0.50
Nodes (7): _mock_twilio(), mock, call_summary_notification.py -- the host-facing end-of-call WhatsApp (property…, test_reports_escalation_raised_when_an_escalation_notification_exists(), test_sends_summary_with_expected_fields_and_no_escalation(), test_skipped_when_host_has_no_phone(), test_skipped_when_no_ai_summary_yet()

### Community 252 - "Task 4.3 — Reverify"
Cohesion: 0.25
Nodes (8): 1. Full `LEAD_AGENT_INSTRUCTIONS` re-read, line 900–1015 — CONFIRMED internally consistent, 2. Third independent real-transcript pass — RAN, RESULTS BELOW, 3. Full test suite — CONFIRMED matches baseline exactly, 4. `CLAUDE.md` invariants — CONFIRMED, independently re-derived, Findings summary, Implementation 4 — Sequencing: `LEAD_AGENT_INSTRUCTIONS` rewrite for the new decision order, Task 4.1 — Implementation, Task 4.3 — Reverify

### Community 253 - "Setup — Populate This Scaffold"
Cohesion: 0.25
Nodes (7): After Setup, Detecting Your State, Keeping It Fresh, Option A — Existing Codebase, Option B — Fresh Project, Recommended: Use setup.sh, Setup — Populate This Scaffold

### Community 254 - "voice/__init__.py"
Cohesion: 0.38
Nodes (5): asyncio.create_task() that survives GC -- see _background_tasks., _spawn_background_task(), test_spawn_background_task_exception_does_not_propagate_to_caller(), test_spawn_background_task_removes_itself_from_the_registry_once_done(), test_spawn_background_task_survives_gc_with_no_external_reference()

### Community 255 - "Pipeline stages"
Cohesion: 0.29
Nodes (7): Bot speaks first, Call-connect-to-greeting latency, Connect-to-greeting gap / ringing tone (Exotel calls only), Conversation architecture: three separate concerns, not one state blob, `pipecat-ai` version pin, Pipeline stages, `StyleComplianceMonitor` — observer, not a guard

### Community 256 - "Refactoring plan"
Cohesion: 0.29
Nodes (7): 0. Reconcile documentation before touching code — CLOSED, 1. Confirm build status of already-planned phases before adding new ones — CLOSED, 2. A shared booking-lifecycle vocabulary (Debt #3) — PARTIALLY CLOSED, 3. Guard-firing observability (Debt #6) — PARTIALLY CLOSED, 4. Groq prompt-cache section reordering (Debt #7) — CLOSED, Explicitly out of scope for this plan (unchanged), Refactoring plan

### Community 257 - "MIRA — AI Property Management Assistant"
Cohesion: 0.29
Nodes (6): Commands, MIRA — AI Property Management Assistant, Navigation, Non-Negotiables, Scaffold Growth, What This Is

### Community 258 - "Setup"
Cohesion: 0.29
Nodes (6): Common Commands, Common Issues, Environment Variables, First-time Setup, Prerequisites, Setup

### Community 259 - "Add Endpoint"
Cohesion: 0.29
Nodes (7): Add Endpoint, Context, Debug, Gotchas, Steps, Update Scaffold, Verify

### Community 260 - "Add Model"
Cohesion: 0.29
Nodes (7): Add Model, Context, Debug, Gotchas, Steps, Update Scaffold, Verify

### Community 261 - "Step 2: Identify the Failure Mode"
Cohesion: 0.29
Nodes (7): Call Ended Early / Prematurely, "Let me loop in the host" or Similar Escalation Phrasing, LLM 429 / Rate Limit, Repeated / Degenerate Output (Same Sentence Many Times), Silence / No Response from Mira, Step 2: Identify the Failure Mode, Wrong Answer / Hallucinated Content

### Community 262 - "Debug Voice Call"
Cohesion: 0.29
Nodes (7): Common Traps, Context, Debug Voice Call, Step 1: Identify the Call in Logs, Step 3: Reproduce Locally, Step 4: Fix, Update Scaffold

### Community 263 - "Architecture"
Cohesion: 0.33
Nodes (5): Architecture, External Dependencies, Key Components, System Overview, What Does NOT Exist Here

### Community 264 - "Conventions"
Cohesion: 0.33
Nodes (5): Conventions, Naming, Patterns, Structure, Verify Checklist

### Community 265 - "Stack"
Cohesion: 0.33
Nodes (5): Core Technologies, Key Libraries, Stack, Version Constraints, What We Deliberately Do NOT Use

### Community 266 - "Handoff — 2026-07-15"
Cohesion: 0.40
Nodes (4): Explicitly NOT built yet — needs your input first, Handoff — 2026-07-15, Loose end, not from today, Shipped today

### Community 267 - "Founder Console"
Cohesion: 0.40
Nodes (4): Deployment, Founder Console, Running locally, What's real vs. estimated

### Community 269 - "Session Bootstrap"
Cohesion: 0.40
Nodes (4): Behavioural Contract, Current Project State, Routing Table, Session Bootstrap

### Community 270 - "Sync — Realign This Scaffold"
Cohesion: 0.40
Nodes (4): Manual Resync, Quick Check, Recommended: Use sync.sh, Sync — Realign This Scaffold

### Community 271 - "frontend/README.md"
Cohesion: 0.50
Nodes (3): Deploy on Vercel, Getting Started, Learn More

### Community 272 - "[hostId]/page.tsx"
Cohesion: 0.67
Nodes (3): getPortfolioGallery(), PortfolioPhotosPage(), PropertyGallery

### Community 273 - "Task: Add or Modify a Voice Tool"
Cohesion: 0.50
Nodes (4): Gotchas, Steps, Task: Add or Modify a Voice Tool, Verify

### Community 274 - "Task: Tune a Pipeline Parameter"
Cohesion: 0.50
Nodes (4): Gotchas, Steps, Task: Tune a Pipeline Parameter, Verify

## Knowledge Gaps
- **564 isolated node(s):** `$schema`, `builder`, `dockerfilePath`, `healthcheckPath`, `healthcheckTimeout` (+559 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 2058 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **32 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Property` connect `Property` to `_user`, `acquire`, `get_owned_property`, `test_faq_api.py`, `NegotiationRule`, `build_voice_tools`, `resolve_effective_call_owner`, `models/property.py`, `test_take_call.py`, `call_service.py`, `test_property_card_and_pitch_formatter.py`, `tool_handlers.py`, `test_run_voice_pipeline_ringing.py`, `test_properties_api.py`, `test_property_retrieval_filter_builder.py`, `Booking`, `pricing_engine.py`, `Lead`, `properties.py`, `test_exotel_call_routing.py`, `searchapi_client.py`, `timedelta`, `recommend_properties`, `test_tool_handlers.py`, `build_lead_system_prompt`, `test_exotel_connect_routing.py`, `test_negotiation_rules.py`, `User`, `faq_service.py`, `test_property_retrieval_ranking.py`, `system_prompt.py`, `test_airbnb_import.py`, `run_sql_search`, `test_availability_recovery.py`, `GuestProfile`, `test_repeat_guest_booking_vs_enquiry.py`, `run_browser_lead_pipeline`, `pipeline.py`, `build_property_chunks`, `calendar_service.py`, `test_negotiate_rate_tool_wrapper.py`, `test_property_lock.py`, `PropertyChunk`, `test_property_display_name_backfill.py`, `handle_negotiate_rate`, `handle_search_faq`, `guests.py`, `test_system_prompt.py`?**
  _High betweenness centrality (0.127) - this node is a cross-community bridge._
- **Why does `User` connect `User` to `BrightDataError`, `_user`, `get_owned_property`, `leads.py`, `acquire`, `NegotiationRule`, `CallSession`, `models/property.py`, `test_auth.py`, `test_take_call.py`, `call_service.py`, `tool_handlers.py`, `test_run_voice_pipeline_ringing.py`, `test_properties_api.py`, `Booking`, `pricing_engine.py`, `Lead`, `properties.py`, `searchapi_client.py`, `config.py`, `build_lead_system_prompt`, `test_negotiation_rules.py`, `FaqEntry`, `system_prompt.py`, `twilio_voice.py`, `GuestProfile`, `run_browser_lead_pipeline`, `pipeline.py`, `negotiation_policy_service.py`, `faq.py`, `get`, `test_property_display_name_backfill.py`, `guests.py`, `test_system_prompt.py`?**
  _High betweenness centrality (0.076) - this node is a cross-community bridge._
- **Why does `ConversationState` connect `ConversationState` to `build_state_block_content`, `NegotiationRule`, `build_voice_tools`, `test_explicit_language_preference.py`, `test_conversation_style.py`, `tool_handlers.py`, `test_silence_watchdog.py`, `RepetitionGuardProcessor`, `Booking`, `test_style_compliance_monitor.py`, `recommend_properties`, `SilenceWatchdogProcessor`, `LanguageSyncProcessor`, `pipeline.py`, `conversation_style.py`, `StatePromptSyncProcessor`, `test_negotiate_rate_tool_wrapper.py`, `test_property_lock.py`, `ConversationStyleProcessor`, `._recompute_goal`, `silence_watchdog.py`?**
  _High betweenness centrality (0.067) - this node is a cross-community bridge._
- **Are the 42 inferred relationships involving `ConversationState` (e.g. with `ConversationStyleProcessor` and `LanguageSyncProcessor`) actually correct?**
  _`ConversationState` has 42 INFERRED edges - model-reasoned connections that need verification._
- **Are the 63 inferred relationships involving `Property` (e.g. with `get_owned_property()` and `owned_property_ids()`) actually correct?**
  _`Property` has 63 INFERRED edges - model-reasoned connections that need verification._
- **Are the 84 inferred relationships involving `User` (e.g. with `analytics_objection_insights()` and `analytics_quality_events()`) actually correct?**
  _`User` has 84 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `build_voice_tools()` (e.g. with `update_lead()` and `negotiate_rate()`) actually correct?**
  _`build_voice_tools()` has 5 INFERRED edges - model-reasoned connections that need verification._