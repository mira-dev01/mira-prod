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
last_updated: 2026-08-08
---

# Session Bootstrap

If you haven't already read `AGENTS.md`, read it now — it contains the project identity, non-negotiables, and commands.

Then read this file fully before doing anything else in this session.

## Current Project State

**Working:**
- Real-time voice calls via Exotel (Guest Support + Lead Agent modes) on Railway backend
- Browser voice test (WebRTC) from the host dashboard
- 7 guard processors in the pipeline enforcing code-level compliance for confirmed-live LLM failures
- Multi-model Groq fallback chain with 60s health checks; OpenRouter as last resort
- iCal calendar sync (APScheduler, every 15 min)
- Host dashboard: properties, bookings, calls, leads, pricing, FAQs, guests, technicians, notifications
- Airbnb listing import via Bright Data scrape
- In-app notifications + Twilio WhatsApp sandbox (escalations, photo links)
- JWT auth; demo account (`demo@mira.ai` / `MiraDemo2024`) with 12 Indian properties
- Deployment: Railway (backend) + Vercel (frontend); Render kept as fallback

**Not yet built:**
- HTTP-based email API (Resend/SendGrid) — current SMTP is blocked on Railway Trial tier (port 587)
- Redis provisioned on Railway — `REDIS_URL` not set; pricing cache is fully wired but inert in production
- Pre-approved WhatsApp template for photo/general sends (only escalation has a template; photo sends hit the 24h window limit)
- Real WhatsApp Business number (currently Twilio sandbox — requires Facebook Business Manager + Exotel KYC)
- Booking finalization tool — Mira qualifies leads and escalates; no tool confirms/finalizes a booking

**Known issues:**
- Railway Trial tier blocks outbound SMTP (port 587) — escalation emails silently fail; fix is switching to HTTP-based email API
- Railway's generated domain has shown DNS resolution failures from some home networks/resolvers (works via 8.8.8.8)
- Double greeting occasionally seen in browser voice test — most likely two separate `/test/offer` POSTs, root cause not confirmed
- `TURN_URL_TLS` (TURNS-over-TCP:443) needed for mobile networks that block UDP; not always configured
- Twilio 24h customer-service window: `send_photos`/`send_whatsapp` fail with error `63016` after 24h of no guest inbound to the sandbox

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
