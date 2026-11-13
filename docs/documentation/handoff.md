# Handoff — 2026-07-15

Branch: `shagun`. See `project_state.md` for the living state snapshot and `docs/` for stable
reference docs (architecture, agents, database, research-flow, api).

## Shipped today

1. **Demo login 500 fixed.** DB was 6 Alembic migrations behind the code; `alembic upgrade head`
   applied the missing ones. No code change — pure schema drift.
2. **Session-continuity docs added**: `docs/architecture.md`, `docs/agents.md`, `docs/database.md`,
   `docs/research-flow.md`, `docs/api.md`. `CLAUDE.md` trimmed from 213 to 113 lines and now points
   into `docs/` instead of duplicating it. `project_state.md` added as a living snapshot.
3. **Escalation email spam fix + redesign** (`backend/app/integrations/email_client.py`,
   `email_templates.py`, `backend/app/services/tool_handlers.py`): multipart text+HTML, proper
   `From`/`Date`/`Message-ID` headers, HTML template in Mira's palette with "Open Dashboard" and
   "Message Guest on WhatsApp" (wa.me link) buttons. **Caveat**: header hygiene alone won't fully
   fix spam placement if the sending domain has no SPF/DKIM — see the comment at the top of
   `email_client.py` for what to check if it's still landing in spam after this.
4. **Photo-delivery automation** (`send_photos` voice tool): guest asks to see photos on a call →
   Mira sends one gallery link (not N images) to a new public, no-auth page at
   `/p/{propertyId}/photos`, backed by `GET /api/v1/properties/{id}/gallery`. Reuses the existing
   Cloudinary `Property.photos` field instead of a separate Drive folder, so it can't drift out of
   sync with what Airbnb import already populated.
5. **Founder Console** (`founder-console/`) — new, fully separate Next.js app (not part of
   `frontend/`, not linked from the host dashboard), gated by a `FOUNDER_PASSCODE` env var. Shows
   live LLM model health and a static external-API cost reference table. See its own `README.md`.

All of the above were verified running (backend + frontend + founder-console started locally,
exercised in the browser pane, screenshots taken) — not just typechecked.

## Explicitly NOT built yet — needs your input first

- **Daily Airbnb smart-pricing fetch.** "Search API" was underspecified — no market/comp pricing
  source is currently wired up. Before building: do we re-scrape the property's own Airbnb listing
  daily via the existing Bright Data integration (cheap, reuses what's already there, but only
  tells us if the host's *own* price on Airbnb has drifted, not what comparable properties charge),
  or is there a specific market-rate API you have in mind? Open question in `project_state.md`.
- **Booking confirmation + QR + payment verification.** Researched payment gateways (Razorpay vs
  Cashfree vs PayU, UPI-focused — numbers in `project_state.md`), but didn't build anything yet:
  the `Booking` model has no price/payment-status columns, and there are real open decisions
  (screenshot-upload flow for Phase 1 manual approval, 50-50 vs full-upfront split, which gateway
  for Phase 2). Flag when you're ready to lock in the flow and I'll build Phase 1 first.

## Loose end, not from today

`test_escalate_to_host_also_saves_lead_so_it_isnt_left_empty` in
`backend/tests/test_tool_handlers.py` fails on phone-number normalization (confirmed pre-existing,
unrelated to anything touched today).
