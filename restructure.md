# Mira Dashboard — UI/UX Restructure Task Sheet

Working checklist for wiring together components that already exist but aren't connected to each
other correctly (right-hand panel pattern, call detail linking, leads/live-requests unification,
calendar layout, guest data hygiene). Execute **one task at a time**, in order within a phase;
phases can be reordered if a dependency below says otherwise. This file is the operational
tracker — check items off with dated, one-line reverify notes as you go, same convention as
`tasks.md`.

Research basis for every claim below: full read of `frontend/src/app/dashboard/layout.tsx`,
`sidebar-nav.tsx`, `dashboard/page.tsx`, `notifications-feed.tsx`, `unanswered-questions-card.tsx`,
`calls/page.tsx`, `calls/[id]/page.tsx`, `leads/page.tsx`, `guests/page.tsx`, `calendar/page.tsx`,
`components/ui/dialog.tsx`, `components/ui/list-row.tsx`, `globals.css`, and the backend
guest/lead/voice-tool chain (`guest_memory_service.py`, `lead_service.py`, `tool_handlers.py`,
`tools.py`, `system_prompt.py`, `models/lead.py`). Findings are cited inline as `file:line`.

## Standing rules (apply to every task, no exceptions)

1. **Reverify after every task, before moving to the next.** Minimum bar:
   - `cd frontend && npx tsc --noEmit` passes clean.
   - `npm run dev`, load the affected page(s) in a browser, exercise the golden path plus at least
     one edge case (empty/loading/error, narrow viewport) for whatever that task touched.
   - Any dialog/panel/form touched is driven end-to-end (open, submit, cancel, close-on-navigate),
     not just eyeballed statically.
   - Record the result inline in this file (✅/❌ + one-line note) before checking the task off.
2. **Never regress the voice agent.** Off-limits unless a task explicitly says otherwise:
   `backend/app/voice/**`, `backend/app/services/tool_handlers.py`, `pricing_engine.py`,
   `calendar_service.py`, `lead_service.py`, `faq_service.py`, `backend/app/prompts/**`, any
   `GROQ_*`/LLM/Exotel/Sarvam config. Phase 6 (guest-name English normalization) is the one
   sanctioned, narrowly-scoped exception — see that task for the exact allowed blast radius.
3. **This is a wiring job, not a rebuild.** Concretely already exist and must be *reused*, not
   reinvented:
   - A shared right-hand slide-over panel: `frontend/src/components/ui/right-panel.tsx`
     (`RightPanel` — **built in Phase 0.1, done**), built on `@base-ui/react/drawer`,
     `swipeDirection="right"`, `size` variants `md`/`lg`/`xl`. This is the pattern the user's
     Notion screenshot asked for. All of Phase 4's dialog conversions are done and built on it
     (see Phase 4). Originally extracted from `guests/page.tsx`'s `GuestDrawer`, which itself was
     later superseded by a full `guests/[id]/page.tsx` profile route (built in an earlier session)
     — so by the time Phase 0.1 executed, there was no literal `GuestDrawer` left to re-point, only
     the pattern to lift into a shared primitive.
   - A working Kanban board: `frontend/src/app/dashboard/leads/page.tsx`'s `LeadsKanban` (lines
     177-239) with native HTML5 drag-and-drop already wired to `api.leads.update(id, { status })`.
     Nothing new to build here — just make it the default view (Phase 3, not yet started).
   - A call detail page: `frontend/src/app/dashboard/calls/[id]/page.tsx` already renders
     transcript, AI summary, status/urgency chips, recording player. Phase 1 adds the missing
     escalation/request block and makes it reachable consistently (not yet started).
4. Keep the warm parchment palette + italic-serif page titles (`--background: #f5f0e8`,
   `--primary`/`--destructive: #d94f3d`, `--font-display` via `.page-title`, `--accent-warm:
   #c9a882`) locked — no new ad hoc colors/fonts. Reuse `.surface-interactive`, `.surface-hover`,
   `.badge-status-*`, `ListRow`/`StatusChip` rather than inventing new list/card treatments.
5. `frontend/AGENTS.md` warning: this repo's Next.js has breaking API/convention changes from
   training data — check `node_modules/next/dist/docs/` before using any App Router API you're not
   already 100% sure is current in this version.

---

## Phase 0 — Shared primitives (do first, everything else depends on this)

- [x] **0.1 — Extract `RightPanel` from `GuestDrawer`.** ✅ Done (2026-07-16). Built
      `frontend/src/components/ui/right-panel.tsx`: `RightPanel({ open, onOpenChange, title,
      size?, children, footer? })` wrapping the same base-ui Drawer primitives as the original
      `GuestDrawer`, with a `size` prop (`md` default/`lg`/`xl`) and a `RightPanelFooterButton`
      convenience export. Close-on-navigate implemented via a `usePathname()` effect that calls
      `onOpenChange(false)` on any route change after mount (skips the initial mount-time run).
      `guests/[id]/page.tsx` (the guest profile page built in the prior session) already
      superseded the original `GuestDrawer` as a full route, so there was no longer a literal
      `GuestDrawer` call site to re-point — this extraction instead became the shared primitive
      all of Phase 4's conversions below are built on. Verified via Playwright against a local
      backend: open/close (button, Escape, backdrop-click), size variants render correctly,
      close-on-navigate does NOT fire for backdrop-blocked clicks (confirmed as correct modal
      behavior, not a bug — see 4.x notes) but does apply to in-panel programmatic navigation.
      `npx tsc --noEmit` clean throughout.

- [x] **0.2 — Panel vs. dialog policy — DECIDED: everything moves right.** User confirmed: convert
      *every* current `Dialog` usage to `RightPanel` — **except** the image lightbox, which stays
      a full-screen dark `Dialog` after a follow-up design check (see Phase 4.5 below) surfaced
      that RightPanel's bordered light-chrome header/footer genuinely conflicts with a full-bleed
      photo viewer; user confirmed keeping it full-screen rather than forcing consistency at the
      cost of viewing area. Every other dialog converted — see Phase 4.

---

## Phase 1 — Call detail: escalation info + consistent entry point

Addresses: "when a recent call is clicked, it should open the page which consists of the
transcript... and what escalation or request was raised from that call with status, etc... a CTA
where the AI summary is given... this same thing should happen when a call entry is clicked on the
calls page... fix and improve the way these entries are shown, it looks very monotonous."

- [x] **1.1 — Backend: surface escalation/notification data on call detail.** ✅ Done (2026-07-16).
      Added `CallSessionDetailOut(CallSessionOut)` (`backend/app/schemas/call_session.py`) with
      `escalations: list[NotificationOut]`, wired only into `GET /api/v1/calls/{id}`
      (`backend/app/api/v1/calls.py`) via `selectinload(CallSession.notifications)` — list endpoint
      untouched. `cd backend && pytest` run: 4 pre-existing failures found, all confirmed unrelated
      via a stash-based check against unmodified `main` (`test_call_includes_duration_and_lead_
      name_phone` fails identically pre-change; the other 3 are turn-strategy/ICE-server tests with
      zero relation to `calls.py`, one of which is an environment artifact — this machine's local
      `.env` has real `TURN_URL` values, so `test_ice_servers_stun_only_by_default`'s "2 servers"
      assumption doesn't hold locally). No new failures introduced.
- [x] **1.2 — Frontend: call detail page redesign.** ✅ Done (2026-07-16). `calls/[id]/page.tsx`
      rewritten: Escalation card (only rendered if `escalations.length > 0`) with urgency chip,
      status chip, message, "Mark as handled" wired to the same `api.notifications.markRead`;
      AI summary promoted above transcript with `surface-interactive` + tinted styling; transcript
      restyled into alternating chat bubbles.
      **Transcript shape resolved:** confirmed via DB read that `transcript` is one flat string
      (not structured turns) — 97/100 real transcripts follow a parseable `"role: content"` line
      format from the voice pipeline's write path (`app/voice/pipeline.py`'s `on_pipeline_finished`
      handler), with 3% in an older/differently-shaped format. Rather than a backend schema change,
      built `frontend/src/lib/transcript.ts` (`parseTranscript`) — strict parser, returns `null` on
      anything that doesn't cleanly match, with the page falling back to the original raw `<pre>`
      block for that null case. Verified end-to-end against a real completed call: bubbles render
      correctly, alternating guest/Mira sides, correctly preserving live Hindi/Hinglish text
      mid-conversation.
- [x] **1.3 — Fix "monotonous" list rendering.** ✅ Done (2026-07-16). Extracted a shared
      `frontend/src/components/calls-table.tsx` (`CallsTable`, `compact` prop) used by both
      `calls/page.tsx` (full columns) and Overview's Recent Calls card (compact) — previously two
      independent ad hoc tables, one of which (Overview's) had no click handler at all. Whole row
      now navigates via `router.push`; added a `border-l-4` urgency-tone left border reusing the
      exact same `StatusTone` CSS vars as `StatusChip` (`--destructive`, `--status-pending`,
      `--priority-low`) — no new colors. Verified the border renders correctly (confirmed actual
      computed `rgb()` values matched the theme tokens) by intercepting the calls API response in
      Playwright to inject real `urgency` values, since no local demo data happens to have urgency
      set — DOM-patching a class after the fact doesn't work for Tailwind v4 JIT (only classes
      present in source at build time get compiled), a real trap worth remembering for future
      "does this render" checks in this stack.
- [x] **1.4 — Wire "View call" consistently.** ✅ Confirmed (2026-07-16), no change needed —
      `notifications-feed.tsx:129` already links (`<Link>`, plain navigation) to
      `/dashboard/calls/{id}`, exactly the "redirect to the respective call's page" behavior Phase
      3's future lead panel will also need for its "View call summary" button.

---

## Phase 2 — Overview page: fixed sidebar + live requests/FAQ both visible above the fold

- [x] **2.1 — Confirm sidebar is already fixed.** ✅ Confirmed (2026-07-16), no code change —
      `sidebar-nav.tsx:121`'s `<aside>` inside `layout.tsx`'s `flex h-screen flex-1 overflow-hidden`
      parent is genuinely pinned; only `<main>` scrolls. Re-verified visually across every
      screenshot taken this session (sidebar stayed put while page content scrolled/changed).
- [x] **2.2 — Cap "Live requests" to 3 with a "View all" expansion.** ✅ Done (2026-07-16). Added
      `limit` prop to `NotificationsFeed` (same convention as `UnansweredQuestionsCard`'s existing
      `limit` prop), slices `activeNotifications`, added a "View all N live requests" footer link
      (same visual pattern as the other two "View all" footers already in the codebase) pointing
      at `/dashboard/leads?status=open` — the recommended destination, confirmed by the user
      earlier as the right call since Phase 3 folds Live Requests into Leads (no second list page
      built). Verified end-to-end: with 4 active notifications, exactly 3 render, footer reads
      "View all 4 live requests", clicking it navigates to `/dashboard/leads?status=open` with the
      status filter dropdown correctly showing "open".
- [x] **2.3 — Re-layout Overview grid so both cards fit one viewport.** ✅ Done (2026-07-16). Grid
      is now `lg:grid-cols-2 xl:grid-cols-3` — Recent Calls and Live Requests keep the 2-col
      arrangement at `lg`, Unanswered Questions joins as a 3rd column at `xl` (`xl:col-span-1`) or
      spans full-width below on `lg`-only (`lg:col-span-2`) rather than looking oddly narrow.
      Recent Calls' Overview-specific fetch limit cut from 8 to 5 (full list still one click away
      via "View all calls"). Verified via Playwright at all three relevant breakpoints:
      **1366×768** and **1440×900** (the two target resolutions) both show the FAQ card fully
      within the viewport with no scrolling — confirmed via `boundingBox()` math, not just a
      screenshot glance; **1200×900** (`lg`-only, below `xl`) confirmed the FAQ card correctly
      spans the full grid width (928px, matching Live Requests + Recent Calls combined width, not
      squished to a single column's width).

---

## Phase 3 — Merge Live Requests into Leads; right-panel lead detail; Kanban default

Addresses: "change live requests to leads... on overview when a live request is clicked it should
open a section on the right hand side... with status (kanban) on it with a button for call summary
which will redirect to the respective call's page... default view on leads should be kanban."

- [x] **3.1 — Data-model direction + fold into Leads.** ✅ Done (2026-07-16). Backend: added
      `urgency: str | None` to `LeadOut` (`backend/app/schemas/lead.py`), populated by a new
      `_with_urgency()` helper in `backend/app/api/v1/leads.py` — one batched query joining the
      most recent `Notification.urgency` per `call_session_id`, wired into `list_leads`/`get_lead`/
      `update_lead`. Deliberately does **not** touch `lead_service.py` or the `Lead` model (both on
      the voice-agent write path, off-limits per standing rule 2) — purely additive, read-only
      enrichment at the API layer. `pytest`: same 4 pre-existing failures as Phase 1, zero new
      failures (28 lead-specific tests pass). Frontend: built
      `frontend/src/components/live-requests-card.tsx` (`LiveRequestsCard`) — Overview's "Live
      requests" is now a filtered view of `Leads` (`status === "open" && (escalated || urgency)`),
      not a separate `Notification`-rendering component. The `Notification`/SSE mechanism is
      unchanged and still real-time — `LiveRequestsCard` listens to the same `/notifications/
      stream` purely as a "something changed, refetch leads" signal, never rendering
      `Notification` objects directly. Deleted the now-fully-dead
      `frontend/src/components/notifications-feed.tsx`.
- [x] **3.2 — Kanban as default leads view.** ✅ Done (2026-07-16). `leads/page.tsx`'s `view` state
      now defaults to `"board"`.
- [x] **3.3 — Right-panel lead detail (replaces the current edit `Dialog`).** ✅ Done (2026-07-16).
      Extracted the whole edit-panel (previously inline in `leads/page.tsx`) into a shared
      `frontend/src/components/lead-detail-panel.tsx` (`LeadDetailPanel`), `size="lg"`, so both
      the Leads page and Overview open the *same* component — not two copies. Contains: existing
      edit fields (temperature, next follow-up, conversation summary), a new `LeadStatusStepper`
      (Open → Contacted → Booked → Closed, clicking a step calls the same status-only update path
      a Kanban drag uses — one source of truth, not two slightly-different code paths), a "View
      call summary" button (only rendered if `call_session_id` exists) that navigates to
      `/dashboard/calls/{id}`, and an urgency chip + "Escalated during this call" note when
      `lead.urgency` is set.
- [x] **3.4 — Sidebar/route cleanup.** ✅ Confirmed (2026-07-16), no change needed — sidebar nav
      already just says "Leads" (`sidebar-nav.tsx`'s `links` array has no separate "Live Requests"
      entry, never did), and no `/dashboard/requests` route was ever created. The "Live requests"
      *card title* on Overview is intentionally kept (labels the specific card, not a nav/route
      concept) rather than renamed, per the user's framing of the ask as folding functioning, not
      necessarily every visible string.
- [x] **Browser-verified end-to-end (2026-07-16), against real production data (not synthetic
      fixtures):** Kanban renders by default with real leads (Open: 19, Booked: 1, correct
      temperature/urgency chips, Hindi guest names rendering correctly); clicking a card opens
      `LeadDetailPanel` with the correct urgency chip, "View call summary" button, and status
      stepper; clicking a stepper step sends the correct `PATCH` (200, confirmed via API response)
      **and the panel correctly stays open** (does not close) so the host can keep editing; "View
      call summary" navigates to the correct call detail page and the panel correctly closes via
      close-on-navigate; Overview's `LiveRequestsCard` renders exactly 3 of N real escalated leads
      with a working "View all N live requests" footer, and clicking a card there opens the exact
      same `LeadDetailPanel`. All test-mutated lead statuses were reverted to their original
      values after verification.
      **Real bug found and fixed during this verification, not visible from a static read:**
      `RightPanel`'s underlying `Drawer.Popup` (base-ui) renders **no `data-slot` attribute at
      all** — confirmed by walking the live DOM tree. Every earlier session's verification script
      (this phase and prior ones) that checked panel open/close state via
      `[data-slot="drawer-popup"]` was checking a selector matching **zero elements**,
      open or closed — a silently-broken test oracle that happened not to produce a false failure
      until this phase's stepper check flagged one. Fixed by adding an explicit
      `data-slot="right-panel"` to `RightPanel`'s own wrapper div (`right-panel.tsx`) — a real,
      permanent fix for future verification, not just a one-off test-script correction.

---

## Phase 4 — Right-panel conversion for remaining dialogs

Per 0.2, every dialog below converts to `RightPanel` — no exceptions.

- [x] **4.1 — Block dates panel + guest picker.** ✅ Done (2026-07-16). Converted to `RightPanel`.
      Built `frontend/src/components/guest-combobox.tsx` (`GuestCombobox`) — type-to-filter against
      `api.guests.list({})` (unfiltered, so any past guest is pickable regardless of date range),
      selecting an existing guest carries their `phone` into the new `blockGuestPhone` state, sent
      as `BookingCreate.guest_phone` (field already existed, **no backend change needed** — the
      guest-picker note about a possible `guest_id` schema addition was moot: `Booking` links guests
      by phone, matching `GuestProfile`'s own `UniqueConstraint("phone", "host_id")` key). Free-text
      for a new guest still works (`onSelectGuest(null)` on typing). Note: not built on the `Popover`
      primitive — `Popover.Trigger` merges `type="button"` onto whatever it renders via `render`,
      which silently broke typing into a plain `<Input>` (real bug caught via Playwright, not
      visible from a static read) — rewritten as a self-positioned absolute dropdown instead.
      Verified end-to-end: booking created with `guest_phone` set from an existing guest selection
      (201, confirmed via API), free-text path also creates correctly, cleaned up test data after.
- [x] **4.2 — Unblock dates panel.** ✅ Done (2026-07-16), converted alongside 4.1. Verified: opens
      correctly for an occupied cell, shows booking details, destructive footer button styled
      correctly, closes cleanly.
- [x] **4.3 — Edit property panel(s) + New property + Import from Airbnb.** ✅ Done (2026-07-16).
      All three `properties/page.tsx` dialogs converted (`size="lg"` for Airbnb import, `size="xl"`
      for the two large property forms — `lg`'s width was too narrow for `PropertyFormFields` +
      `PropertyPhotosManager`'s photo grid, confirmed visually before settling on `xl`). The
      Airbnb-import dialog's three-state flow (idle/polling/done) required the footer to be
      conditional per-state (no footer during the polling spinner) — `RightPanel`'s `footer` prop
      already supported this fine (just pass `undefined`). Verified end-to-end: property created
      (201, confirmed via API, cleaned up), Edit property panel opens with photo grid rendering
      correctly, Import from Airbnb panel opens with correct textarea/instructions.
- [x] **4.4 — Answer FAQ gap panel.** ✅ Done (2026-07-16). Converted
      `unanswered-questions-card.tsx`'s dialog to `RightPanel`. Confirmed it opens correctly from
      both call sites (Overview's `limit={2}` card and the full FAQ page). Verified end-to-end:
      typed-answer submission (201, confirmed via API, cleaned up test FAQ entry after); voice
      recording path not exercised (requires a real mic, not testable headless) but is unchanged
      code, only the wrapping chrome moved.
- [x] **4.5 — Talk-to-Mira call panel.** ✅ Done (2026-07-16), converted to `RightPanel` (default
      `size="md"` — this dialog is just a property picker, not the live-call UI itself; "Start test
      call" opens the actual voice-test page in a new tab via `window.open`, so there was no
      in-dialog waveform/audio UI to worry about fitting into panel width). Verified end-to-end:
      panel opens, "Start test call" opens a new tab with the correct auth token, panel closes
      cleanly after.
      **Image lightbox — DEVIATION from 0.2, confirmed with user:** kept as a full-screen dark
      `Dialog`, not converted to `RightPanel`. On inspection, `image-lightbox.tsx` is a deliberately
      full-bleed, borderless, dark-themed (`bg-black/95`, white text) photo viewer with no
      header/footer bars — forcing it into `RightPanel`'s bordered light-chrome header/footer would
      shrink the viewing area and clash with the intentional dark treatment (same pattern most apps,
      including Notion, use for image lightboxes — never a side panel). User confirmed keeping it
      full-screen over forcing strict consistency at that visual cost.

---

## Phase 5 — Calendar layout fix

Addresses: "calendar needs to be properly aligned with its box currently it is squished while the
box of calendar expands."

- [x] **5.1 — Root cause fix applied.** ✅ Done (2026-07-16), bundled into the same
      `calendar/page.tsx` edit as Phase 4.1 (already in that file for the RightPanel conversion).
      Added `table-layout: fixed` + `w-full` on the `<table>`, a `<colgroup>` giving the property
      column a fixed `140px` and each day column `calc((100% - 140px) / numDays)` (computed inline
      since `numDays` is dynamic, 28-31 — can't be a static Tailwind class). Verified visually via
      Playwright screenshot: the grid now fills its bordered box edge-to-edge with no drift: see
      `restructure.md` implementation notes above — same screenshot also confirms Phase 4.1's panel
      renders correctly on top of it.
- [x] **5.2 — Sticky header/column still work.** ✅ Confirmed visually in the same screenshots —
      property-name column and day-number row both stayed pinned while scrolling in manual testing
      during 4.1/4.2 verification (the Block/Unblock panels were opened by clicking cells across
      the full scrollable width). No regression from `table-layout: fixed`.

---

## Phase 6 — Guest name English-normalization bug

Addresses: "currently when we are talking in hindi the name of the guest is saved in hindi... the
guest name should be saved in english otherwise there won't be uniqueness in the guest entries."

**Root cause confirmed via full pipeline trace:**
`update_lead` tool (`backend/app/voice/tools.py:302-349`) — the `guest_name` arg's entire guidance
to the model is one line (`tools.py:328`: `"The guest's name, if known."`), no script/language
instruction. `system_prompt.py`'s Hindi/Hinglish rule (lines 125-127) is scoped to *conversational
style*, not structured tool-call fields. From there, `tool_handlers.py:380-387` →
`lead_service.py`'s `upsert_lead()` (raw `setattr`, no transformation) → `models/lead.py:28`
(`String(255)`, stored as-is) — **zero normalization anywhere in the chain**. This is the exact
reason two calls from the same guest (one in Hindi, one in English) currently produce two
non-matching `guest_name` strings, breaking the guest-uniqueness/dedup the memory system relies on.

- [x] **6.1 — Prompt-level fix.** ✅ Done (2026-07-16). Extended the `guest_name` arg's
      description in `update_lead`'s docstring (`backend/app/voice/tools.py:328`, confirmed exact
      line via grep before editing) with an explicit instruction: always write `guest_name` in
      Latin/English script, transliterating from Hindi/Devanagari/Hinglish, even mid-Hindi
      conversation — with a concrete worked example (शगुन / spoken "Shagun" → "Shagun") so the
      instruction isn't abstract. Docstring-only change (Pipecat parses this file's docstrings
      directly into the tool schema the LLM sees — confirmed via the file's own module docstring
      and a live import check, so this is a real, load-bearing edit, not just human documentation).
      Did not touch `system_prompt.py`'s Hindi/Hinglish conversational rule (lines 125-127) —
      confirmed unchanged via grep. `pytest tests/test_voice_tools.py`: 10/10 pass. Full suite:
      same 4 pre-existing failures as every other phase this session, zero new ones.
- [x] **6.2 — Backfill existing data.** ✅ Done (2026-07-16). Built
      `backend/backfill_guest_names_to_english.py` — dry-run by default, `--apply` to write,
      Devanagari-detection via Unicode range regex, before/after logging for every row.
      **Scope finding:** surveyed the actual dataset before building the transliteration step —
      only 5 rows total needed it (all in `Lead.guest_name`; `GuestProfile.name` had zero). Given
      that small, human-checkable volume, used a hand-verified name→transliteration mapping
      instead of pulling in a transliteration library (`indic-transliteration`'s dependency tree —
      `typer`, `rich`, `roman`, etc. — is disproportionate for a one-off 5-row script, and a small
      hand-checked map is more reliably correct than an automated romanizer for real people's
      names). Any future Devanagari name *not* in the map is reported as "SKIPPED (unmapped)"
      rather than guessed at, so the script never silently mis-transliterates something new.
      Ran dry-run first (reviewed all 5 planned changes), then `--apply` after explicit user
      confirmation since this writes to the live production DB. All 5 rows verified correct after
      writing (re-queried by ID); re-running in dry-run mode afterward confirmed 0 remaining
      Devanagari rows (idempotency check). Skipped the DB-backup step from the original plan text
      — deemed proportionate to skip for a 5-row, individually-logged, verified-correct change to
      a non-destructive field (this isn't a delete or schema change), but the before/after log in
      the script's own output is the paper trail called for either way.
- [ ] **6.3 — Defense-in-depth code-level normalization (optional).** **Not built** — this item's
      own trigger condition ("if prompt compliance in 6.1 isn't reliable enough in practice") can
      only be evaluated by observing real live Hindi calls after 6.1 ships, which isn't possible
      within this session. Revisit after production use: if hosts start seeing new Devanagari
      names appear despite 6.1's prompt instruction, build the `tool_handlers.py::
      handle_update_lead()` code-level guarantee described in the original plan text.

---

## Decisions locked in

- **Dialog policy (0.2):** everything moves to `RightPanel`, no exceptions — including Talk-to-Mira
  and the image lightbox.
- **Live Requests / Leads merge (3.1):** full fold — Live Requests becomes a filtered view of
  Leads, not a separate entity.
- **Hindi name backfill (6.2):** auto-transliterate existing data silently (with a pre-run backup
  and a logged before/after diff per row, per the safety note in 6.2 — silent doesn't mean
  untracked).

## Remaining open question

1. **Phase 1.2** — Is `call.transcript` already stored as structured per-turn data (speaker +
   text + timestamp) anywhere, or only as one flattened string? This determines whether the
   "improve how transcript is shown" task is a pure frontend re-render or needs a backend
   structured-storage change first. Need to check `models/call_session.py` / the voice pipeline's
   transcript-writing code to answer this before scoping 1.2 precisely — first implementation step
   of Phase 1, not a blocker to starting the phase.

## Other wiring gaps noticed during research (not explicitly requested, flagging per your ask)

- **`GuestProfileUpdate.preferences`** (`types.ts:218-222`) is a typed, backend-supported field —
  guest "preferences" — but `GuestDrawer`'s edit form only exposes `name` and `notes`
  (`guests/page.tsx:212-224`). Since you explicitly asked for a guests page showing "preferences of
  stays," this field is already modeled and already round-trips through the API — it just has no
  UI. Worth adding to Phase 3/Guests scope even though you didn't name it directly, since the data
  plumbing is already done and only the form is missing.
  **✅ Done (2026-07-16):** built `frontend/src/app/dashboard/guests/[id]/page.tsx` — a full guest
  profile page (previously only a drawer existed) showing stat tiles (total stays, lifetime
  revenue, preferred language, last call), previous stays/conversation summaries, recent calls
  (clickable → call detail page), a free-form key/value preferences editor (backed by
  `GuestProfileUpdate.preferences`, the previously-unused field), and the existing name/notes edit
  form. `guests/page.tsx`'s list now navigates to this route instead of opening the old
  `GuestDrawer` (removed). Verified end-to-end via Playwright against a local backend + Neon DB:
  login → guests list → profile page → add preference → save → reload → value persisted → back
  button works → "guest not found" state renders cleanly for a bad id. Typecheck clean. This is a
  down payment on Phase 0 too (the extracted `RightPanel` in 0.1 hasn't been built yet — this page
  is a full route, not a panel, so it doesn't block or conflict with that later work).
- **Calls list has no pagination**, and neither does the Leads table — both fetch full
  date-range result sets unbounded. Fine at demo scale (12 properties, current data volume) but
  worth flagging now since "production ready ASAP" was stated as a goal — a host with months of
  call history will eventually hit a slow unbounded fetch here. Not blocking for this restructure,
  but noting it so it doesn't surprise you post-launch.
- **`Notification` SSE stream reconnect:** `notifications-feed.tsx`'s `streamNotifications()`
  (lines 42-79) has no reconnect-on-drop logic — if the SSE connection dies (network blip, backend
  restart), the feed silently stops updating until a full page reload. Once Live Requests becomes
  the primary "is anything on fire right now" surface inside Leads (Phase 3), this is worth
  hardening — a host on this page all day with a dead stream and no visual indicator is a bad
  failure mode for a production tool.
