# AI-Authored Commits

Every commit in this repo's history (`git log --all`) carrying a `Co-Authored-By: Claude ...`
trailer, deduplicated by commit hash and sorted chronologically. Generated 2026-09-28 via:

```bash
git log --all --format="%H%x1f%ad%x1f%an%x1f%s%x1f%b%x1e" --date=short
# filtered to entries whose body contains "co-authored-by: claude" (case-insensitive)
```

**55 of 262 total commits** in the repo are AI-co-authored as of this date. Two Claude versions
appear: **Sonnet 4.6** (all commits through 2026-07-02, the earliest voice-pipeline/WebRTC work) and
**Sonnet 5** (2026-07-13 onward — Host/Guest/Property/Knowledge Memory, negotiation engine, closing
intelligence, deploy fixes, the low-confidence transcript guard recalibration). Human commit authors
shown as recorded in git (`shagun verma` / `Shagun Verma` / `abhaya` are separate git identities, not
typos).

| Date | Hash | Model | Author | Subject |
|---|---|---|---|---|
| 2026-06-30 | `689a61834d` | Sonnet 4.6 | shagun verma | Add STUN server config for browser-test WebRTC on cloud hosts |
| 2026-06-30 | `6dd8ba3af8` | Sonnet 4.6 | shagun verma | Add unblock-dates calendar feature; rebuild Render deploy config |
| 2026-06-30 | `7535f0ba22` | Sonnet 4.6 | shagun verma | Add TURN server support for browser-test WebRTC (STUN alone insufficient) |
| 2026-06-30 | `9152b385aa` | Sonnet 4.6 | shagun verma | Pre-compute weekend dates instead of trusting LLM date arithmetic |
| 2026-07-01 | `2fb9b6fb7a` | Sonnet 4.6 | shagun verma | Mobile-responsive UI, fix ✳ emoji on mobile, cap ICE gathering at 5s |
| 2026-07-01 | `3928d7e673` | Sonnet 4.6 | shagun verma | Wait for browser ICE gathering before sending WebRTC offer |
| 2026-07-01 | `3ef0b6253e` | Sonnet 4.6 | shagun verma | Fix prompt leakage: filter null-content turns; stop LLM hallucinating dialogue |
| 2026-07-01 | `51300a7263` | Sonnet 4.6 | shagun verma | Revert bot-first greeting — pipecat frame types unverified, pipeline was silenced |
| 2026-07-01 | `5590d14c8a` | Sonnet 4.6 | shagun verma | Bot speaks first: push TTSSpeakFrame on client connect (verified frame exists) |
| 2026-07-01 | `622162f076` | Sonnet 4.6 | shagun verma | Guard greeting handler with try/except — pipeline survives if greeting fails |
| 2026-07-01 | `80fb276eb7` | Sonnet 4.6 | shagun verma | Warm up LLM route at startup to eliminate 8s first-call latency on Render |
| 2026-07-01 | `9d79c46929` | Sonnet 4.6 | shagun verma | Mira speaks first: push greeting on client connect, skip LLM roundtrip |
| 2026-07-01 | `ed9e663399` | Sonnet 4.6 | shagun verma | Reduce VAD timeout 0.9s→0.6s; add demo seed script with 12 Indian properties |
| 2026-07-02 | `41a0d1e377` | Sonnet 4.6 | shagun verma | Fix greeting, Kerala search, and escalation repeat |
| 2026-07-02 | `5325d3e9bd` | Sonnet 4.6 | shagun verma | Fix greeting regression: push TTSSpeakFrame via llm not tts |
| 2026-07-02 | `54616aa739` | Sonnet 4.6 | shagun verma | Reduce greeting latency: pre-warm TTS + keep LLM route warm every 4min |
| 2026-07-02 | `68ad6152e4` | Sonnet 4.6 | shagun verma | Add TURNS-over-TCP fallback for mobile networks that block UDP TURN |
| 2026-07-02 | `6b5d9fbc08` | Sonnet 4.6 | shagun verma | UX: help first then collect contact; raise VAD to 1.1s; ban filler phrases |
| 2026-07-02 | `811c43e3ab` | Sonnet 4.6 | shagun verma | Fix voice UX: ban markdown, cap listing length, bump TTS pace to 1.15 |
| 2026-07-02 | `abb15208f4` | Sonnet 4.6 | shagun verma | Fix greeting: use worker.queue_frame() instead of asking LLM to improvise it |
| 2026-07-02 | `d0030f6e17` | Sonnet 4.6 | shagun verma | Fix post-restart greeting: pre-connect TTS with silent frame, not full greeting |
| 2026-07-02 | `e5e9ebafaa` | Sonnet 4.6 | shagun verma | Fix post-escalation silence, question bundling, VAD cutoffs, location answers |
| 2026-07-13 | `1470edd8c7` | Sonnet 5 | abhaya | Fix Lead Agent property scoping: lock selected property across search_faq/recommend_properties |
| 2026-07-14 | `6da15ce03a` | Sonnet 5 | abhaya | Add Host Memory: discount policy parsing + AI Training validation tab |
| 2026-07-14 | `74b9a529ab` | Sonnet 5 | abhaya | Wire Host Memory discount policy into negotiate_rate and GOLDEN_RULES |
| 2026-07-14 | `c650a3b551` | Sonnet 5 | abhaya | Add Guest Memory: cross-call guest continuity, host-scoped repeat-guest discounts |
| 2026-07-14 | `ee6af71a9f` | Sonnet 5 | abhaya | Add Knowledge Memory: semantic FAQ-gap dedup and auto-draft suggestions |
| 2026-07-14 | `fb1063d42c` | Sonnet 5 | abhaya | Add Property Memory: seasonal notes surfaced only when currently in effect |
| 2026-07-18 | `093ac0aec9` | Sonnet 5 | shagun verma | Add Railway deploy config for backend service |
| 2026-07-21 | `033ca24895` | Sonnet 5 | shagun verma | Broaden loop-in-host ban to all escalations, save unprompted name/phone in Guest Support |
| 2026-07-21 | `42fc5a34f5` | Sonnet 5 | shagun verma | Update docs and project state for Railway/Vercel migration, pricing cache, and recent fixes |
| 2026-07-21 | `536e520b27` | Sonnet 5 | shagun verma | Document SearchApi credit accounting for the live Redis pricing cache |
| 2026-07-21 | `72a07855f0` | Sonnet 5 | shagun verma | Fix Guest Memory name staleness, stop re-asking known phone/name, add caller-ID fallback |
| 2026-07-21 | `986929d997` | Sonnet 5 | shagun verma | Document Redis (Upstash) now live in production, verified via real pricing quote cache hit |
| 2026-07-22 | `357b3b9d3d` | Sonnet 5 | shagun verma | Remove holding-audio welcome message feature; fixes a second crash-on-teardown bug |
| 2026-07-22 | `6d1b4a609f` | Sonnet 5 | shagun verma | Correct stale Groq free-tier references now that the account is paid |
| 2026-07-22 | `6eae71613e` | Sonnet 5 | shagun verma | Fix NameError crashing every call: holding_audio_task never reached _run_pipeline |
| 2026-07-23 | `3c2e6aabfd` | Sonnet 5 | shagun verma | Fix silence watchdog and VAD interruption sensitivity based on real call evidence |
| 2026-07-23 | `83b5636aec` | Sonnet 5 | shagun verma | Fix recommend_properties results going unspoken before the model reacts to them |
| 2026-07-23 | `ca829024e3` | Sonnet 5 | shagun verma | Add a code-level backstop for the "let me loop in the host" ban |
| 2026-07-24 | `f12c3dd3b3` | Sonnet 5 | shagun verma | Never quote a zero/negative price as a real rate |
| 2026-07-26 | `4a0c3e174c` | Sonnet 5 | shagun verma | Fix alembic multiple-heads deploy blocker |
| 2026-08-01 | `1b2b36f201` | Sonnet 5 | abhaya | Fix P0: state-block marker field crashed every Groq completion after turn 2 |
| 2026-08-05 | `4a43d757b8` | Sonnet 5 | abhaya | Closing intelligence: hard/soft close framing, urgency, follow-up wording |
| 2026-08-05 | `dc874beec2` | Sonnet 5 | abhaya | Negotiation engine: host-configured pricing rules |
| 2026-08-05 | `e2ce9eaf50` | Sonnet 5 | abhaya | Recommendation conversations: refine without restarting retrieval |
| 2026-08-09 | `74c67d6c56` | Sonnet 5 | Shagun Verma | Add Saturday minimum-stay toggle for properties |
| 2026-08-11 | `379146c322` | Sonnet 5 | abhaya | Fix busy-message/ringing-tone playback drift under event-loop load |
| 2026-08-11 | `6f1d4ccaa4` | Sonnet 5 | abhaya | Fix robotic/slow busy-call voice and WhatsApp mispronunciation |
| 2026-08-11 | `6f942d09a8` | Sonnet 5 | abhaya | Fix Alembic multiple-heads deploy failure |
| 2026-08-12 | `47b15b0bca` | Sonnet 5 | abhaya | Fix busy-message sample-rate mismatch and add defensive validation |
| 2026-08-21 | `e0c40211bc` | Sonnet 5 | Shagun Verma | Fix missing await on getToken() in notification stream hook |
| 2026-09-14 | `9e80b8c175` | Sonnet 5 | Shagun Verma | Fix WhatsApp CTA link missing India country code |
| 2026-09-14 | `ea820405ed` | Sonnet 5 | Shagun Verma | Handle leading-0 domestic prefix in WhatsApp CTA link |
| 2026-09-23 | `caf2013235` | Sonnet 5 | Shagun Verma | Recalibrate low-confidence transcript guard threshold, 0.85 -> 0.4 |

## Notable gaps

Not every AI-assisted change carries this trailer — e.g. `9b48368` ("Configure Resend for Host
Summary emails", 2026-09-24) was written by Claude in the same session as several commits above but
committed directly by the user through their own terminal, so it has no co-author trailer and isn't
in this list. This file only reflects commits that were *actually attributed* in git, not every
commit where AI assistance was involved — treat it as a lower bound, not an exhaustive audit.
