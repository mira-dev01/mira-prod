# Event-loop isolation and service split — phased plan

Written 2026-11-09. Not started — to be executed after the access-control, lease and LLM-failover fixes (those are done; see "Already done" below).

## Problem
One Python process runs all of this on a single asyncio event loop:
live call audio (pipecat: VAD, STT/TTS websockets, LLM streaming), 12 APScheduler jobs (five fire every 60 s),
dashboard REST + SSE polling, PDF/Excel/DOCX parsing, Airbnb imports, and the post-call LLM work.
Any slow synchronous step, or a burst of heavy work, delays **every live call's audio** — heard as choppy or
late speech. It also means a deploy or crash kills all live calls at once, and the API can't scale
independently of calls.

## Constraints discovered in the code (these shape the plan)
1. ~~**Host hand-off is in-process.**~~ **Solved 2026-11-09.** `handoff_signal.request_handoff_anywhere` sets the Event directly when the call is in this process and otherwise publishes on Redis (`mira:handoff_requested`); one listener per process (`run_listener`, started in `lifespan`) sets it where the call lives. Splitting voice from API no longer breaks Take Call (Redis must be reachable from both; an undeliverable signal is logged as `handoff_signal_not_delivered`).
2. **Scheduler runs in every replica.** `AsyncIOScheduler` starts inside `lifespan`; a second replica would run
   every job twice (payment sweep, busy-callback checks, digests). Needs a single owner before scaling.
3. **Per-process caches/state:** `llm_health` (model up/down), `kb_retrieval._fact_cache`, `main._job_results`.
   Harmless when duplicated, but `llm_health` must be per-voice-process (it reflects that process's rate-limit hits).
4. **Post-call work runs inside `on_pipeline_finished`** (classification + summary LLM ≤ 15–20 s each, ticket reconciliation,
   telemetry, guest memory, emails) — in the call process, delaying teardown. The lease no longer waits on it
   (`_CallLeaseGuard`) and each step is now isolated (`_PostCallSteps`: one failure no longer skips the rest, with a
   traceback), but it is still not durable (lost if the process dies mid-way) and still shares the loop, DB pool and Groq quota.
5. **DB connection count is the scaling limit** (Supabase session-mode pooler on `dev`, Neon on `production`; pool set via `DB_POOL_*`). Connection count is the scaling limit, not CPU: every extra service/replica multiplies
   pool size. Use the transaction pooler (port 6543) and check asyncpg's `statement_cache_size=0` requirement there.
6. **Redis (Upstash)** already holds call leases; it is the natural cross-service bus (pub/sub, queue).

## Already done (2026-11-09, uncommitted)
Not phases of this plan, but prerequisites it relied on: the JWKS lookup runs in a thread; all detached tasks go through `app/utils/background.spawn`; shared, time-limited LLM clients; explicit DB pool settings with a pressure warning; Take Call over Redis pub/sub (Phase 3's first bullet); post-call steps isolated (Phase 2's "no single failure skips the rest"); post-call LLM concurrency cap and optional separate Groq model/key; CI and a hashed lock file. Hosting is Railway (backend), Supabase (Postgres), Upstash (Redis).

## Phases
Each phase is independently shippable and has a rollback. Don't start N+1 until N's exit criteria are met.

### Phase 0 — Measure first (½–1 day)
- Add an **event-loop lag sampler**: a task that sleeps 100 ms and logs/metrics the overshoot; export p50/p99 via
  `app/observability` and include max-lag-during-call in `call_metrics`.
- In staging only: `PYTHONASYNCIODEBUG=1` with `loop.slow_callback_duration = 0.05` to log every callback > 50 ms
  with its stack. Run a normal day / a synthetic load and collect the offenders.
- Record baseline: concurrent calls the current instance handles before lag p99 > 50 ms; DB connections in use.
- **Exit:** a ranked list of blocking offenders and a number to beat. **Rollback:** n/a (observation only).

### Phase 1 — Stop blocking the loop, in place (1–2 days)
No new infrastructure. Targets from Phase 0, expected to include:
- ✅ JWKS fetch (done: `asyncio.to_thread` in `_verify_session_token`).
- Offload to threads / a process pool: iCal parsing, Airbnb import normalisation, PDF/DOCX/XLSX extraction paths not
  already threaded (`policy_documents.extract_text` is), image handling, large JSON builds, `bcrypt`.
- Bound concurrency: a semaphore around heavy jobs (imports, policy parsing) so a burst can't saturate the loop/thread pool.
- Scheduler hygiene: `max_instances=1, coalesce=True, misfire_grace_time` on all 12 jobs (only some have it);
  stagger the five 60 s jobs so they don't fire together; make probes use short timeouts.
- **Exit:** lag p99 under load beats the Phase 0 baseline; no callback > 50 ms from our code. **Rollback:** revert PR.

### Phase 2 — Durable post-call job queue (2–3 days)  *(also completes "release the line immediately")*
- Move everything after `finalize_call_session` out of `on_pipeline_finished` into jobs: classification, summary,
  ticket reconciliation, telemetry persist, lead backfill/cleanup, guest memory, host email.
  Keep **inline** only what hand-off routing needs (status + `TRANSFERRED_TO_HOST` classification).
- Queue: a Postgres `jobs` table (`SELECT … FOR UPDATE SKIP LOCKED`, `run_at`, `attempts`, `last_error`, `dedupe_key =
  kind:call_session_id`) — no new infra, durable across deploys, survives a crash mid-call-end. (Alternative: arq on
  Upstash Redis; choose only if Postgres polling load becomes a problem.)
- Worker: start as an **in-process** background task (a few concurrent workers) so this phase changes no deployment.
  Idempotent handlers, exponential backoff, max attempts, dead-letter state surfaced in the admin panel + health alert.
- **Exit:** `on_pipeline_finished` p99 < 200 ms; killing the process mid-job loses nothing (job re-runs); calls still
  classified/summarised/emailed exactly once. **Rollback:** feature flag `POST_CALL_JOBS_ENABLED` falling back to inline.

### Phase 3 — Move shared state off process memory (2 days)
- ~~Hand-off signal → Redis pub/sub~~ — **done** (see above); the in-process path remains the fallback when Redis is unavailable.
- Scheduler leader election: Redis `SET NX EX` lock so only one process runs jobs (or just disable the scheduler by env
  on all but the worker). Add `RUN_SCHEDULER=true|false`.
- Decide per-process vs shared for `llm_health` (keep per voice process; expose via `/health/llm`).
- **Exit:** with two API replicas running, Take Call still reaches the call and each scheduled job runs once.

### Phase 4 — Separate worker service (1–2 days)
- Same Docker image, new entrypoint `python -m app.worker`: runs the scheduler, the job-queue consumers, Airbnb/policy
  imports and anything long-running. `RUN_SCHEDULER=false` on the API/voice service.
- Pool sizes: worker small (e.g. 5), sized so `Σ(services × replicas × pool)` stays under the database's connection limit.
- **Exit:** a worker restart/crash has zero effect on live calls; scheduler and jobs only run in the worker.

### Phase 5 — Split voice from API (2–3 days)
- Two ASGI apps from one codebase: `app.voice_main` (only `/voice/*` WebSockets/webhooks needed by Exotel/Twilio, `/health`,
  lease + LLM health) and `app.main` (dashboard REST, SSE, admin). Point the Exotel/Twilio WebSocket URL at the voice host.
- Voice service: no scheduler, minimal imports, tuned for many concurrent websockets; scale by active-call count. API
  service scales on request rate. Shared secrets/env via the host platform's shared variables.
- Routing: separate subdomain (`voice.…`) — also lets the voice host have different request-size/timeouts than the API.
- **Exit:** a dashboard load spike or a large import produces no change in call-audio lag; deploying the API doesn't touch calls.
  **Rollback:** point Exotel back at the monolith (kept deployable until Phase 6).

### Phase 6 — Graceful lifecycle and capacity (2 days)
- Voice service shutdown: on SIGTERM stop accepting new calls (new calls go to another replica or `BUSY_RECOVERY`),
  wait for live calls up to `MAX_CALL_DURATION`, then exit. Entrypoint must `exec uvicorn` so it actually receives
  the signal (current `sh -c "alembic … && uvicorn …"` does not forward it). Run migrations as a pre-deploy step, not on every boot.
- Capacity model: calls per CPU core measured in Phase 0 → autoscale target on active-call gauge; keep ≥ 2 voice replicas
  so a deploy never drops the line.
- Load test with a WebSocket call simulator against staging; define SLOs (loop lag p99 < 50 ms, time-to-first-audio,
  zero dead-air > 3 s); alert on them.
- Runbook: deploy order (worker → API → voice), rollback, "calls are choppy" triage using the Phase 0 metrics.

## Suggested order and rough effort
Phase 0 → 1 → 2 → 3 → 4 → 5 → 6, about **2–3 engineer-weeks** total. Phases 0–2 deliver most of the benefit
(measurable lag, immediate line release, no post-call work on the call loop) with no new services; Phases 3–5 are
what let API/worker/voice scale and deploy independently.

## Decisions needed before Phase 3
1. ~~Where do the containers run?~~ Answered: Railway (two environments, `dev` and `production`; the databases are Supabase and Neon). Railway supports multiple services per project with shared variables and `RAILWAY_DEPLOYMENT_DRAINING_SECONDS` for drain time — Phases 4–6 map onto that directly.
2. Queue backend for Phase 2: Postgres jobs table (recommended) vs Redis/arq.
3. Minimum number of voice replicas you are willing to pay for.
