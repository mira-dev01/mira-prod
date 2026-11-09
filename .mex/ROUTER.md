---
name: router
description: Session bootstrap and navigation hub. Read at the start of every session before any task. Contains project state, routing table, and behavioural contract.
edges:
  - target: context/architecture.md
    condition: when working on system design, integrations, or understanding how components connect
  - target: context/stack.md
    condition: when working with specific technologies, libraries, or making tech decisions
  - target: context/conventions.md
    condition: when writing new code, reviewing code, or unsure about project patterns
  - target: context/decisions.md
    condition: when making architectural choices or understanding why something is built a certain way
  - target: context/setup.md
    condition: when setting up the dev environment or running the project for the first time
  - target: context/voice-pipeline.md
    condition: when working on the real-time voice pipeline, guard processors, or LLM routing
  - target: patterns/INDEX.md
    condition: when starting a task — check the pattern index for a matching pattern file
last_updated: 2026-11-09
---

# Session Bootstrap

If you haven't already read `AGENTS.md`, read it now — it contains the project identity, non-negotiables, and commands.

Then read this file fully before doing anything else in this session.

## Current Project State

(Updated 2026-11-09. The authoritative, dated status is `documentation/project_state.md`; the technical picture is `documentation/current_architecture.md`.)

**Working:**
- Real-time voice calls via Exotel (and Twilio Voice as a fallback path), Guest Support + Lead Agent modes, on the Railway backend; browser WebRTC test from the dashboard
- Pipeline guard chain (7 deterministic guards + `StyleComplianceMonitor`, knowledge-grounding guard); no hidden LLM regeneration
- Voice LLM failover across the Groq chain on 429/5xx/timeout/stalled first token, with a spoken "one moment" line and the silence watchdog held meanwhile; OpenRouter as last resort
- `CallCoordinator` (Redis leases): one live call per host/property; lease released the moment the guest disconnects; Busy Call Recovery (WhatsApp) for rejected calls
- Post-call work as isolated steps (classification, summary, tickets, telemetry, lead backfill); a failed classification never deletes a lead
- Live "Take Call" host handoff, across processes via Redis pub/sub
- Clerk authentication (token `iss`/`azp`/`exp` verified), host dashboard, admin panel (`/admin`, emailed one-time code), payments (Razorpay links + date holds), tickets, knowledge base (`search_faq`), policies
- Location search by map position (local GeoNames gazetteer, nearest-first) — applied on Railway `dev` for Hushstays only; production has none of it yet
- Optional full-duplex pieces (backchannel filter, volume ducking, echo cancellation), off by default; first two enabled on `dev`
- Host email via Resend (HTTPS; **absent on `production`**, so emails are skipped there); WhatsApp via a real Twilio WhatsApp Business number
- Live pricing time-boxed at 4s with an honest estimate fallback; Redis cache for pricing and query embeddings
- Dashboard: Settings, Overview, Leads, Calls and Properties laid out in the "Visible Grid" system (`DESIGN.md`); other pages still to do
- Hosting: Railway backend with two environments — `dev` (Supabase Postgres, deploys from the `dev` branch) and `production` (Neon Postgres, the live system) — plus Vercel (frontend) and Redis. Render is retired (`render.yaml` deleted deliberately)
- CI (`.github/workflows/ci.yml`) runs ruff + pytest; dependencies install from the hashed `backend/requirements.lock`; tests are hermetic (no `.env`, no real services)

**Not yet done:**
- Per-host call capacity (today: one live call per host/property; a second caller gets the busy path)
- `exec`/graceful-shutdown fix so a deploy doesn't cut off live calls (Dockerfile `CMD` lacks `exec`; Railway draining defaults to 0s)
- Event-loop / service split (`docs/event-loop-split-plan.md`); a single owner for the scheduler before running >1 replica
- VAPT phases (`docs/vapt-plan.md`); a short-lived ticket instead of the session token in `GET /voice/test?token=`
- Applying location search to `production` (migration + gazetteer load + coordinates per host)
- `production`'s deploy branch is not recorded anywhere — fill it in at the top of `docs/deployment.md`

**Known issues / things to watch:**
- Production refuses to start without a real `TAKE_CALL_TOKEN_SECRET` and `EXOTEL_WEBHOOK_TOKEN` — set them on **both** Railway environments before the next deploy (a push to `dev` auto-deploys `dev`)
- Railway's generated domain has shown DNS resolution failures from some home networks/resolvers (works via 8.8.8.8)
- Double greeting occasionally seen in browser voice test — most likely two separate `/test/offer` POSTs, root cause not confirmed
- `TURN_URL_TLS` (TURNS-over-TCP:443) is needed for mobile networks that block UDP; not always configured
- Twilio's 24h customer-service window: freeform `send_photos`/`send_whatsapp` fail with error `63016` after 24h without guest inbound
- Size the DB pool so `(DB_POOL_SIZE + DB_MAX_OVERFLOW) × replicas` stays under the pooler limit (Supabase on `dev`)

## Routing Table

Load the relevant file based on the current task. Always load `context/architecture.md` first if not already in context this session.

| Task type | Load |
|-----------|------|
| Understanding how the system works | `context/architecture.md` |
| Working with a specific technology | `context/stack.md` |
| Writing or reviewing code | `context/conventions.md` |
| Making a design decision | `context/decisions.md` |
| Setting up or running the project | `context/setup.md` |
| Voice pipeline / guard processors / LLM routing | `context/voice-pipeline.md` |
| Any specific task | Check `patterns/INDEX.md` for a matching pattern |

## Behavioural Contract

For every task, follow this loop:

1. **CONTEXT** — Load the relevant context file(s) from the routing table above. Check `patterns/INDEX.md` for a matching pattern. If one exists, follow it. Narrate what you load: "Loading architecture context..."
2. **BUILD** — Do the work. If a pattern exists, follow its Steps. If you are about to deviate from an established pattern, say so before writing any code — state the deviation and why.
3. **VERIFY** — Load `context/conventions.md` and run the Verify Checklist item by item. State each item and whether the output passes. Do not summarise — enumerate explicitly.
4. **DEBUG** — If verification fails or something breaks, check `patterns/INDEX.md` for a debug pattern. Follow it. Fix the issue and re-run VERIFY.
5. **GROW** — After meaningful work, run this binary checklist:
   - **Ground:** What changed in reality? Name the changed behavior, system, command, dependency, or workflow.
   - **Record:** If project state changed, update the "Current Project State" section above. If documented facts changed, update the relevant `context/` file surgically.
   - **Orient:** If this task can recur and no pattern exists, create one in `patterns/` using `patterns/README.md`, then add it to `patterns/INDEX.md`. If a pattern exists but you learned a gotcha, update it.
   - **Write:** Bump `last_updated` in every scaffold file you changed. If the why matters, run `mex log --type decision "<what changed and why>"` or `mex log "<note>"`.
