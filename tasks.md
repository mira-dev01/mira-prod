# Mira Dashboard Redesign — Task Sheet

Working checklist for the current design-system redesign (warm-palette elevation to
Stripe/Linear/Airbnb-host-dashboard-level polish, per the approved plan at
`~/.claude/plans/cached-watching-cosmos.md`). Execute **one task at a time**, in order. This file
is the operational tracker — check items off with dated, one-line reverify notes as you go.

> Note: an earlier, unrelated task file (date-range/StatCard/ActionableCard rollout, batches 0–9)
> previously lived at this path and has been superseded — that work is already present in the
> current codebase (Overview/Calls/Leads already use DateRangePicker, StatCard, StatusChip,
> ActionableCard). This file starts fresh for the design-system redesign effort.

## Standing rules (apply to every task, no exceptions)

1. **Reverify after every task, before moving to the next.** Minimum bar:
   - `cd frontend && npx tsc --noEmit` (or `npm run build`) passes clean.
   - `npm run dev`, load the affected page(s) in a browser, exercise the golden path plus at least
     one edge case (empty/loading/error state) for whatever that task touched.
   - Any dialog/form/mutation touched by the task is driven end-to-end (submit, cancel, error), not
     just eyeballed statically.
   - Record the result inline in this file (✅/❌ + one-line note) before checking the task off.
2. **Never regress the voice agent.** These directories/files are the live-call path and are
   **off-limits** for every task in this sheet unless a task explicitly says otherwise (none
   currently do):
   - `backend/app/voice/**` (pipeline, tool wrappers, turn strategies)
   - `backend/app/services/tool_handlers.py`
   - `backend/app/services/pricing_engine.py`, `calendar_service.py`, `lead_service.py`,
     `faq_service.py` (business logic the voice tools call)
   - `backend/app/prompts/**` (system prompt builders)
   - Any `GROQ_*`/LLM/Exotel/Sarvam config
   - Concretely: if a task needs new data, add a **new, additive, read-only** endpoint/response
     shape. Never change an existing tool's schema, an existing endpoint's response shape (only
     extend it), or any write path the agent already uses (e.g. `Lead.status`, which per CLAUDE.md
     only the dashboard edit dialog sets today — a new Kanban drag path must call that exact same
     update, never a bespoke one).
   - After any backend touch: run `cd backend && pytest` (real DB, per CLAUDE.md — do not mock it)
     before touching the frontend that consumes it.
3. **Backend work is in-scope, not deferred.** Where the original brief needs data the API doesn't
   expose, add a small backend sub-task (additive endpoint/response field) right before the
   frontend task that needs it, instead of scoping the frontend down. Only fall back to "visual
   scaffold, real fields only" if the aggregation genuinely isn't derivable from existing tables.
4. Keep the warm parchment palette + italic-serif page titles (locked in, not up for
   relitigation).

---

## Phase 0 — Backend additions identified so far

Scoped by checking `backend/app/models/`, `backend/app/api/v1/guests.py`, `technicians.py`, and
confirming no voice-path file (`app/voice/**`, `tool_handlers.py`) references `GuestProfile` or
`Technician` at all — these are safe, additive, read-only changes isolated from the live-call path.

- [x] **0.1 — Guest detail aggregation endpoint.** Added `GET /api/v1/guests/{guest_id}/detail`
      (`backend/app/api/v1/guests.py`) returning a new `GuestProfileDetailOut` schema
      (`backend/app/schemas/guest_profile.py`, extends `GuestProfileOut`) with `lifetime_revenue`
      (`SUM(CallSession.revenue_attributed)`, `COALESCE`d to 0 for a guest with no calls — same
      pattern as `analytics.py`'s pipeline_value query) and `recent_calls` (last 10, newest first:
      id, property_id/name via `LEFT OUTER JOIN` on `Property` since Lead Agent calls legitimately
      have `property_id IS NULL`, status, ai_summary, started_at — all fields already on
      `CallSessionOut`, nothing new invented). Reuses `get_guest`'s existing ownership check
      (only visible if the guest shares a `CallSession` with the requesting host) rather than
      duplicating it. Both the revenue sum and recent-calls query are explicitly scoped by
      `CallSession.user_id == current_user.id`, not just `guest_profile_id` — a `GuestProfile` row
      is keyed by phone number, not per-host, so without that filter a guest who called two
      different hosts on MIRA would leak the other host's revenue/call history into this host's
      view. Did not touch `backend/app/models/call_session.py` at all (even additively) since
      it's adjacent to the live voice-call path — confirmed via grep that no file under
      `app/voice/` or `tool_handlers.py` references `GuestProfile`/`Technician`, so this endpoint
      is fully isolated from the pipeline regardless.
      - Reverify: could not run `pytest` — local Postgres (`tests/conftest.py` needs
        `mira`/`mira_test` on `localhost:5432`) failed to start in this environment (`brew
        services start` hit a launchd bootstrap I/O error; direct `pg_ctl start` reported port
        5432 "already in use" with no listening process visible via `lsof`) after a few
        non-destructive diagnostic attempts. Per your direction, did NOT point tests at the
        real Neon DB in `.env` (`conftest.py`'s fixtures wipe all tables before/after every run —
        would have been destructive to production-adjacent data) and did NOT keep escalating
        system-service debugging. Substituted the most rigorous verification available without a
        live DB: ✅ AST syntax check on both changed files. ✅ Imported the real `app.main` module
        (via the backend's existing `venv`) with zero errors — this alone proves no import-time
        or route-registration-time exception. ✅ Sent real ASGI test requests (same
        `httpx.ASGITransport` mechanism `conftest.py`'s own `client` fixture uses, no DB
        connection required since `get_current_user` runs before any DB access): confirmed
        `GET /api/v1/guests/{uuid}/detail` returns `401 Not authenticated` (not `404`, proving
        the route is registered and reachable) both for a valid and a malformed UUID, and
        confirmed all three pre-existing guest routes (`GET ""`, `GET "/{id}"`, `PATCH "/{id}"`)
        still resolve correctly (401, not 404/405) — no path-collision regression from adding
        `/{guest_id}/detail`. ✅ Compiled both new SQL statements (`revenue_stmt`,
        `recent_calls_stmt`) against the real Postgres dialect with literal binds and read the
        generated SQL directly — confirmed correct `COALESCE(SUM(...), 0)` and
        `LEFT OUTER JOIN properties ON properties.id = call_sessions.property_id` with both
        `guest_profile_id`/`user_id` filters present. ⚠️ **Not verified**: actual query
        *results* against real seeded data (a guest with calls vs. one with none), and the
        cross-host revenue-isolation guarantee (would need two hosts + shared guest phone number
        seeded to prove empirically, not just by reading the WHERE clause). Recommend running
        `pytest` once local Postgres is working, or testing manually against a seeded guest —
        flagging this explicitly rather than claiming full confidence.
- [x] **0.2 — Technician distance: confirmed not derivable.** No lat/lng on `Property` or
      `Technician` models (`backend/app/models/technician.py`, `property.py`). Distance needs a
      geocoding integration — out of scope for a design pass. **Decision: exclude distance from
      Task 18. No backend work.** `rating` is already a real field and suffices.
- [x] **0.3 — Confirmed out of scope: call sentiment/intent, pricing forecasting, FAQ categories,
      Settings billing/API/team.** No backing tables exist for any of these. Adding them means
      designing new backend features, not a UI redesign. Flagged here so it isn't silently
      dropped; no task will be created unless requested separately.

---

## Phase 1 — Foundation (infrastructure only, no visual page changes)

- [x] **1. Spacing/elevation/hover tokens in `globals.css`** — elevation scale (`--shadow-xs` …
      `--shadow-xl`, light+dark), `.surface-interactive`/`.surface-hover` hover conventions,
      documented 8pt spacing rule (Tailwind's 4px base unit ⇒ even steps = 8pt grid).
      - Reverify: ✅ `npm run build` clean, all 15 routes compiled, TS passed. CSS-only, no
        voice-path files touched.
- [x] **2. Unify tone→className mapping** — new `src/lib/tone.ts` (`StatusTone`, `toneClassName`,
      `toneDotClassName`, `toneBadgeVariant`, `toneCssVar`) is now the single source of truth.
      `status-chip.tsx` re-exports `StatusTone` and imports the maps (no local copies left).
      `actionable-card.tsx`'s priority vocabulary (`high`/`medium`/`low`) now maps onto
      `StatusTone` (`destructive`/`pending`/`low`) instead of a second hand-rolled map; added
      `low` as a first-class `StatusTone` (was priority-only before) so both files share one
      vocabulary. Calendar's legend + booking-block cells centralized into one
      `bookingSourceColor` lookup — kept on `--chart-3`/`--chart-4` (categorical, not
      `StatusTone`) deliberately, since forcing it onto `StatusTone` would have silently
      recolored Airbnb bookings from coral to green (caught during this task, reverted to
      preserve exact colors).
      - Reverify: ✅ `npx tsc --noEmit` clean. ✅ `npm run build` clean, all 15 routes compiled.
        ✅ Diff-level check: `toneClassName`/`toneBadgeVariant` outputs are byte-identical to the
        pre-refactor maps for every existing tone value (verified via `git diff`, not just by
        construction); Calendar's resting/hover colors and all `onClick`/`title` handlers
        unchanged. ✅ `git status` confirms only frontend files touched, zero backend/voice-path
        files. ⚠️ No live browser walkthrough — no backend/DB running in this environment to
        serve real data; static/diff verification substituted. Recommend a visual spot-check of
        Overview, Leads, and Calendar next time `npm run dev` is run against a live backend.
- [x] **3. Unify list-row patterns** — new `src/components/ui/list-row.tsx`
      (`ListRow`/`ListRowHeader`/`ListRowTitle`/`ListRowBody`/`ListRowFooter`) with two framing
      variants: `divider` (bottom-border rows, last flush — used by `actionable-card.tsx` and
      `unanswered-questions-card.tsx`) and `boxed` (bordered/rounded box per row — used by
      `notifications-feed.tsx`). All three components now compose `ListRow` instead of hand-rolling
      their own wrapper className.
      - Correction to the original survey: `ActionableCard` currently has **zero consumers**
        anywhere in the app (grep confirmed) — Overview only renders `NotificationsFeed` and
        `UnansweredQuestionsCard`, not `ActionableCard`. Refactored it anyway for consistency
        (it's a well-defined shared primitive other Phase 3/4 tasks, e.g. Leads Kanban cards, may
        want), but couldn't visually verify it live since no page currently mounts it.
      - Reverify: ✅ `npx tsc --noEmit` clean. ✅ `npm run build` clean, all 15 routes compiled.
        ✅ Class-resolution check via `tailwind-merge` directly (not just visual inspection): for
        every row shape (ActionableCard compact, ActionableCard default, UnansweredQuestionsCard
        row), the final merged Tailwind class string is byte-identical to the pre-refactor
        literal className, confirming zero layout/spacing regression. Caught and fixed one real
        4px spacing loss in `notifications-feed.tsx`'s footer (`pt-1` was on the original footer
        div, dropped in the first pass, re-added). ✅ `git status` confirms only frontend files
        touched. ⚠️ No live browser walkthrough (no backend/DB running); relied on build +
        class-resolution verification instead. Recommend visually checking the Overview page's
        "Live requests" panel (NotificationsFeed, boxed variant) and "Questions Mira couldn't
        answer" panel (UnansweredQuestionsCard, divider variant) next time a live backend is
        available — particularly the notifications SSE stream still live-updating rows correctly,
        and the answer dialog's text/voice-recording flow still submitting.
- [x] **4. Input primitive upgrade** — `size?: "sm" | "default"` (matching `select.tsx`'s
      `data-size` convention exactly), `leadingIcon`/`trailingIcon` slots (wrap in a relative
      container + absolute-positioned icon only when provided), `errorMessage` (implies
      `aria-invalid` + renders a `text-destructive` message below the field). All additive; the
      native HTML `size` attribute (character-width number) is intentionally shadowed by the new
      visual-size prop via `Omit<..., "size">`, documented inline so it isn't confused with the
      native attribute later.
      - Reverify: ✅ `npx tsc --noEmit` clean (caught and fixed one real type error: the native
        `<input size>` attribute collided with the new `size` variant prop, fixed via
        `Omit<ComponentProps<"input">, "size">`). ✅ `npm run build` clean, all 15 routes
        compiled. ✅ Call-site count unchanged (36 before, 36 after, across 9 files) and zero of
        them needed edits — confirms the change is truly additive, not just intended to be. ✅
        `git status` confirms only frontend files touched. ⚠️ No live browser walkthrough (no
        backend/DB running); relied on build+typecheck. Recommend spot-checking
        property-form-fields, Settings, Pricing, Guests, and Technicians forms render/submit
        correctly next time a live backend is available, and trying the new `leadingIcon`/
        `errorMessage` props on at least one field before Task 15+ starts relying on them.
- [x] **5. Label + Textarea parity** — `Label` gets `size?: "sm" | "default"` (via `data-size`,
      same convention as Input/Select) and `required?: boolean` (renders a destructive-colored
      `*`, purely visual — pairs with the field's own `required`/`aria-required`, doesn't set it).
      `Textarea` gets the same `errorMessage` convention as Input (implies `aria-invalid`, renders
      a `text-destructive` message below). No existing call site used a manual `*`-for-required
      convention or passed `aria-invalid` to Textarea (grep-confirmed before starting), so nothing
      needed migrating.
      - Reverify: ✅ `npx tsc --noEmit` clean. ✅ `npm run build` clean, all 15 routes compiled.
        ✅ Call-site counts unchanged (57 Label, 12 Textarea, before and after) — confirms
        additive-only. ✅ `git status` confirms only frontend files touched. ⚠️ No live browser
        walkthrough (no backend/DB running); relied on build+typecheck. Recommend trying
        `required`/`errorMessage` on one real form field next time a live backend is available.
- [x] **6. Skeleton shape variants** — `variant?: "block" | "text" | "avatar"` on `Skeleton`
      (`block` = original bare rectangle, unchanged default; `text` = single body-text line;
      `avatar` = circle for Avatar placeholders). Wired a real loading branch into Settings using
      `useAuth()`'s existing `loading` boolean (was already there, just never read by this page)
      — renders 4 skeleton cards matching the real card count/order (Host account, Escalation
      notifications, Lead intake number, Voice agent personalization) before the real form JSX,
      untouched, renders below.
      - Reverify: ✅ `npx tsc --noEmit` clean. ✅ `npm run build` clean, all 15 routes compiled.
        ✅ Default `variant="block"` produces byte-identical className to the pre-change
        `Skeleton` for all 16 pre-existing call sites (grep-confirmed zero pass `variant`). ✅
        Confirmed the early `if (loading) return (...)` doesn't swallow the real form JSX below
        it (read the file post-edit — untouched). ✅ `git status` confirms only frontend files
        touched. ⚠️ No live browser walkthrough / network throttle test (no backend/DB running);
        relied on build+typecheck+code-read instead. Recommend throttling network in devtools on
        `/dashboard/settings` next time a live backend is available to see the skeleton actually
        render before user data loads, with no layout shift into the real cards.
- [x] **7. Table sticky-header support** — `TableHeader` gets `sticky?: boolean` (default
      `false`). When true: `sticky top-0 z-10 bg-card` on the `<thead>` plus forces row background
      to `bg-card` so scrolling body rows don't show through the pinned header. Deliberately
      opt-in and inert on its own: sticky positioning only does anything inside a
      height-bounded/`overflow-y-auto` container, and none of the app's 7 current `<Table>`
      consumers (page-scroll tables) provide one — this is infrastructure for Task 20 (Calendar
      rebuild), not something any current page should visually change from.
      - Reverify: ✅ `npx tsc --noEmit` clean. ✅ `npm run build` clean, all 15 routes compiled.
        ✅ Grep-confirmed all 7 existing `<TableHeader>` call sites (Overview, Calls, FAQ,
        Technicians, Leads, Pricing, Guests) pass no `sticky` prop, so each resolves to the exact
        original `"[&_tr]:border-b"` className — zero appended classes, fully inert. ✅
        `git status` confirms only frontend files touched. ⚠️ No live browser walkthrough (no
        backend/DB running); relied on build+typecheck+grep instead, appropriate here since the
        change is provably a no-op for every current consumer by construction.
- [x] **8. Real Sidebar primitive** — rebuilt `sidebar-nav.tsx`'s mobile overlay on `@base-ui/react`'s
      `Drawer` primitive (confirmed via its bundled docs at
      `node_modules/@base-ui/react/docs/react/components/drawer.md` before writing any code — it
      explicitly "extends Dialog," same portal/focus-trap/modal-scroll-lock foundation already
      proven by this codebase's `dialog.tsx`). `Drawer.Root open/onOpenChange swipeDirection="left"`
      replaces the hand-rolled `translate-x` + manual `document.body.style.overflow` toggle;
      `Drawer.Trigger`/`Drawer.Backdrop`/`Drawer.Viewport`/`Drawer.Popup`/`Drawer.Close` replace the
      manual backdrop `<div>`+conditional-render+click-handler panel. The **desktop rail stays
      plain markup** (not a Drawer instance) since it's a permanently-visible, non-dismissible
      sidebar, not an overlay — Drawer's machinery doesn't apply there. Fixed one real regression I
      introduced during the rewrite: the original's route-change `useEffect(() => setOpen(false),
      [pathname])` was dropped in the first pass (NavLinks' onNavigate already closes on direct
      link clicks, but the effect was a fallback for browser back/forward and any
      programmatic `router.push()`) — caught by re-reading against the original and re-added.
      `TalkToMiraDialog` itself was not touched at all (confirmed via `git diff --stat`, zero
      changes) — it's a fully separate, self-contained `Dialog` instance triggered by callback,
      exactly as before.
      - Reverify: ✅ `npx tsc --noEmit` clean. ✅ `npm run build` clean, all 15 routes compiled. ✅
        **Live backend reachable this session** — `.env` points `NEXT_PUBLIC_API_BASE_URL` at a
        real deployed Render backend; confirmed reachable (`/health` → 200 after a ~31s cold
        start) and the demo account (`demo@mira.ai` / `MiraDemo2024`, per CLAUDE.md) logs in
        successfully via direct API call. ✅ Ran `npm run dev` against that live backend: `/login`
        and `/dashboard` both return 200 with no server-side render errors in the dev log
        (`DashboardLayout` renders `<SidebarNav/>` before the client-side auth-redirect fires, so
        this does exercise the new Drawer-based component tree server-side without throwing). ⚠️
        **Could not do a full interactive browser walkthrough** — no browser-automation tool is
        available in this environment, so I could not actually click the hamburger, watch the
        drawer slide in from the left, swipe to dismiss, or visually confirm active-link
        highlighting/focus trap. Verified as rigorously as possible without one: confirmed
        `data-starting-style:`/`data-ending-style:` are valid Tailwind v4 arbitrary data-attribute
        variants (same mechanism as the already-proven `data-open:`/`data-closed:` in
        `dialog.tsx`/`select.tsx`), matched my Popup transform/backdrop styling directly against
        the docs' own "Position" example for `swipeDirection="left"`, and confirmed `keepMounted`
        defaults to `false` on `Drawer.Portal` (same unmount-when-closed behavior as Dialog).
        **Strongly recommend an actual manual click-through** (hamburger open/close, swipe-to-dismiss,
        route-change auto-close, Talk to Mira trigger from both desktop and mobile) the next time a
        real browser is available, before this is considered fully verified — this is the
        highest-risk task completed so far given every page renders inside this shell.

## Phase 2 — Simple page polish (styling-only)

- [x] **9. Overview** — fixed one off-8pt-grid spacing value (header controls row `gap-3`→`gap-4`,
      per the spacing convention documented in Task 1 — `gap-1` near the "View all calls" arrow
      link was left alone, that's a genuine optical correction, not layout rhythm). Gave
      `StatCard` a new opt-in `interactive`/`className` prop (additive) so the one genuinely
      clickable stat card (Open Leads, wrapped in a `Link`) gets the shared `.surface-interactive`
      hover-lift from Task 1 — the other 5 cards are purely informational and intentionally don't
      get it, so the lift affordance doesn't compete with real interactivity signals elsewhere.
      Considered opting the Recent Calls table into Task 7's sticky-header, but the query is
      hard-capped at `limit: 8` and never scrolls internally — sticky would add complexity with
      no actual effect, so left as plain page-scroll (this is correctly the sticky feature's
      first real "no" case, not a bug). Considered making table rows clickable given the existing
      chevron affordance implies it, but that's a behavior change, not styling — explicitly out of
      scope for this task, left as-is.
      - Reverify: ✅ `npx tsc --noEmit` clean. ✅ `npm run build` clean, all 15 routes compiled.
        ✅ Confirmed via grep that all 6 `StatCard` call sites are on this page (no other page
        affected by the new props). ✅ Ran `npm run dev` pointed at the live Render backend
        (`.env`'s `NEXT_PUBLIC_API_BASE_URL`) — `/dashboard` returns 200 with no server errors in
        the dev log. ⚠️ Still no browser-automation tool available, so the actual hover-lift
        motion and spacing change weren't visually confirmed pixel-by-pixel — recommend a quick
        look next time a browser is available, low risk given the changes are small and additive.
- [x] **10. Calls list** — fixed the same off-8pt-grid `gap-3`→`gap-4` pattern as Task 9 (header
      row + controls row). Added color-coding to the previously-plain-text `Urgency` column via a
      new local `urgencyTone` map + `StatusChip` (confirmed the value vocabulary — `low`/`medium`/
      `high`/`emergency` — by reading `app/schemas/tool.py`/`app/voice/tools.py` read-only, no
      backend files touched) — this is a real field already fetched (`CallSessionOut.urgency`),
      exactly the kind of "lean on real fields, don't fabricate sentiment" treatment this task
      calls for. `null` urgency still renders "—" exactly as before. Left row-hover as-is (already
      handled by `TableRow`'s base `hover:bg-muted/50`, nothing to add).
      - Reverify: ✅ `npx tsc --noEmit` clean. ✅ `npm run build` clean, all 15 routes compiled.
        ✅ **Verified against real production data**, not just a schema read: logged into the
        live demo account and fetched real calls (`GET /calls?include_test_calls=true` → 26 real
        rows). Confirms `status` is exercised (`completed` → `live` tone renders correctly) and
        that `urgency` is `null` on all 26 current demo calls, meaning production data doesn't
        currently exercise the new urgency-chip path — the `null` fallback (`—`) is what's live
        today, but the code path for a populated `urgency` was verified structurally, not against
        a real emergency/high call. (Also: caught and fixed my own mistake mid-verification — an
        earlier `curl` attempt hardcoded a fetched JWT into a followup shell command, which the
        environment correctly flagged as credential exposure; redid it as a single piped
        `login → fetch` command so no token ever appeared in a command or transcript.) ⚠️ Still
        no browser-automation tool; visual rendering of the new chip not pixel-confirmed.
- [x] **11. Call detail** — found and fixed a real inconsistency with Task 10: `urgency` here was
      always rendered as a flat `<Badge variant="destructive">` regardless of actual level (so
      even a "low" urgency call showed red) — replaced with the same `urgencyTone`-mapped
      `StatusChip` now used on the Calls list page, for visual consistency across the two pages
      showing the same field. Header badges row got `flex-wrap` added (was a plain `flex gap-2`,
      could overflow on narrow screens with a long duration/urgency combination). The
      `<audio controls>` block, transcript `<pre>`, and AI-summary fallback text are byte-for-byte
      untouched (confirmed via `git diff` showing no lines in that section).
      - Reverify: ✅ `npx tsc --noEmit` clean. ✅ `npm run build` clean, all 15 routes compiled.
        ✅ Confirmed via `git diff` that the audio/transcript/summary `Card` blocks have zero
        changed lines. ✅ Verified against real production data: fetched an actual call detail
        from the live demo account (`status: completed`, `urgency: null`, `ai_summary` absent,
        `transcript` present, no `recording_url`) — confirms the "no summary" and "no urgency
        badge" fallback paths render against real data exactly as coded. ⚠️ Checked all 26 demo
        calls: **zero have a `recording_url` or `ai_summary`** — could not exercise the
        audio-player-present branch or a populated urgency chip against real data; this is a data
        gap in the demo account, not something resolvable from this session. Recommend a manual
        check once a call with a recording exists, or seeding one for testing.
- [x] **12. Guests (styling only)** — first real call site for the previously-unused `Avatar`
      primitive: a small (`size="sm"`) avatar with initials-from-name fallback (new local
      `guestInitials` helper) or a generic `UserRound` icon when no name exists, in the table's
      Name column. Fixed the same off-8pt-grid `gap-3`→`gap-4` header pattern as the other pages.
      Explicitly did not touch anything toward the drawer/history/lifetime-revenue CRM rebuild —
      that's Task 22, still gated on Task 0.1 (already shipped).
      - Reverify: ✅ `npx tsc --noEmit` clean (twice — once before, once after a fix). ✅ `npm run
        build` clean, all 15 routes compiled. ✅ **Caught a real bug via edge-case testing before
        shipping**: `guestInitials("  ")` (whitespace-only name) returned `""` instead of `null`,
        which would have rendered a blank avatar fallback instead of the intended `UserRound`
        icon fallback (`"" ?? <Icon/>` doesn't catch empty string, only null/undefined) — found by
        running the helper against 7 cases in Node (`"Priya Sharma"`, `"Amit"`, `null`, `"  "`,
        `""`, `"A"`, `"Mary Jane Watson"`) before considering this done, fixed by trimming into a
        variable and checking falsiness on the trimmed result. Re-verified all 7 cases correct
        after the fix. ✅ Verified against real production data: the live demo account has
        exactly 1 real guest (`"Browser test guest"`), confirmed `guestInitials` produces the
        expected "BG" against that real name, and confirmed the existing
        `isBrowserTestIdentity`-based phone-badge branch (untouched) still applies correctly. ⚠️
        Still no browser-automation tool; visual avatar rendering not pixel-confirmed. Demo data
        is thin (1 guest) — a name with only 1 character, or a very long name, weren't checked
        against real rows, only synthetically.
- [x] **13. FAQ (styling only) — scoped down from plan.** Fixed the one genuine off-8pt-grid gap
      (`gap-3`→`gap-4` on the "Add FAQ entry" form's field grid). **Did NOT touch any of the 9
      `min-w-0` occurrences** — traced the actual grid/flex ancestry by reading `card.tsx` (`Card`
      is `flex flex-col`, so its children are cross-axis-stretched, not main-axis-constrained,
      meaning several of the `min-w-0`s are *probably* redundant) but concluded I cannot safely
      verify a CSS grid/flexbox overflow fix without a real browser to check computed layout —
      removing the wrong one would silently reintroduce the exact overflow bug this pattern
      exists to prevent, and that's not a risk worth taking blind. This is an explicit, disclosed
      scope reduction from the plan's "fix properly" goal, not a silent skip — the defensive
      `min-w-0` styling stays exactly as it was. Did not touch `faq_service.py` or the voice-answer
      recording flow at all (confirmed via `git diff --stat backend/` showing zero backend
      changes).
      - Reverify: ✅ `npx tsc --noEmit` clean. ✅ `npm run build` clean, all 15 routes compiled.
        ✅ Confirmed via live backend that real gap data exists (2 real unanswered questions:
        "distance from bus stand", "nearby party cafe") and 1 verified FAQ entry — the page's
        real/empty-state branches are both exercisable with current demo data. **Deliberately did
        NOT submit a real answer** (text or voice) against the live demo backend — that's a
        permanent mutation (creates a new verified entry, clears a real gap) against a flow I
        didn't modify this task (`unanswered-questions-card.tsx` was last touched in Task 3,
        already build+diff-verified there); asked you first rather than mutate
        production-adjacent data for a code path this task didn't change, and you agreed to skip
        it. ⚠️ No browser-automation tool; the `min-w-0` overflow behavior specifically remains
        visually unverified (unchanged from before, so no new risk, but also not confirmed fixed
        either) — flagging this as a real follow-up opportunity for whoever next has a browser on
        this page.

## Phase 3 — Component-heavy restyle

- [x] **14. Properties: fix glyph-buttons + banner** — replaced the raw `"✕"` dismiss button and
      `"⋯"` dropdown-trigger text glyphs with proper lucide icons (`X`, `MoreHorizontal`); fixed
      the same off-8pt-grid header `gap-3`→`gap-4` pattern as the other pages. Fixed
      `pending-import-banner.tsx`'s bespoke `border-primary/30 bg-primary/5` + manual pulsing dot
      to use the shared tone system (`toneCssVar.progress` via `color-mix`) instead — this is a
      real, visible color change (terracotta brand color → blue "in-progress" tone), explicitly
      confirmed with you first since it's shown during host signup, not a silent re-theme.
      - Reverify: ✅ `npx tsc --noEmit` clean. ✅ `npm run build` clean, all 15 routes compiled.
        ✅ `git diff` of `properties/page.tsx` shows exactly 3 surgical JSX/className hunks — zero
        touches to any `handle*` function, state, or effect, confirming the file's business logic
        (Airbnb import polling, file import, iCal sync) is completely untouched. ✅ Verified real
        demo data via the live backend: 12 properties, all missing `exophone` (confirms the phone
        banner's render condition is genuinely exercised in production) and none with `ical_url`
        (confirms the "Sync iCal" dropdown item's `disabled` condition is correctly active for
        all 12 today). **Deliberately did not run a real Airbnb import or file import** — asked
        you first since that would trigger an actual paid Bright Data scrape and permanently
        create/modify properties, for logic this task didn't touch; you agreed to skip it,
        consistent with the FAQ-answer decision in Task 13. Could not test the iCal sync button's
        real network call either, since no current property has an `ical_url` to sync (would
        require a mutation first). ⚠️ No browser-automation tool; icon rendering and banner color
        not pixel-confirmed.
- [x] **15. Properties: card redesign** — **re-verifying the image field turned up a real gap,
      not a "no."** The original survey said `PropertyOut` has no image field — true of the
      *frontend* type, but re-checking the *backend* schema (`backend/app/schemas/property.py`)
      showed `PropertyOut.photos: list[str]` already exists and is already returned by the API
      (populated via the Bright Data → Cloudinary import pipeline per
      `backend/app/services/airbnb_import.py`/`cloudinary_client.py`) — `frontend/src/lib/types.ts`
      had simply drifted out of sync and never declared it. Added `photos: string[]` to the
      frontend `PropertyOut` type (read-only display type; deliberately did NOT add it to
      `PropertyCreate`, since wiring a photo-upload UI into the manual add/edit form is a new
      feature, out of scope here) and wired a real property image at the top of each card
      (`photos[0]`, `Card`'s own `*:[img:first-child]:rounded-t-xl` rule handles the rounding for
      free) with a `Building2` icon placeholder when the array is empty. Used a raw `<img>` (not
      `next/image`) since these are external Cloudinary URLs and configuring `remotePatterns` in
      `next.config.ts` is a real config change I didn't want to make blind, especially given
      `AGENTS.md`'s breaking-changes warning for this Next.js version — accepted the resulting
      `no-img-element` ESLint warning (warn-only, doesn't fail the build) rather than guess at
      unfamiliar config.
      - Reverify: ✅ `npx tsc --noEmit` clean. ✅ `npm run build` clean, all 15 routes compiled
        (lint warning only, no errors). ✅ `git diff` confirms every quick-action handler
        (`handleSetDiscount`, `handleTestInBrowser`, `openEdit`, `handleSync`, `handleDelete`) is
        completely untouched — only the image slot was added above the existing content. ✅
        Verified against real production data: all 12 demo properties have `photos: []` (never
        imported via the Bright Data pipeline, only seeded directly), meaning the **placeholder
        path is what's actually exercised in production today** — good that this was built and
        confirmed correct via the `property.photos[0]` falsy-on-empty-array check, not just the
        happier photo-present path, which currently has zero real rows to exercise it against.
        ⚠️ No browser-automation tool; actual image rendering/placeholder appearance not
        pixel-confirmed, and the photo-present path is entirely unverified against real data
        (would need a property actually imported via Bright Data to test).
- [x] **16. Properties: Import-from-Airbnb dialog redesign** — confirmed `AirbnbUrlImportStatus`
      really is only `running|ready|failed` with no percentage field (re-read the schema, matches
      the plan's caution) — did not fabricate a progress bar. Instead surfaced one piece of real
      data that was already computed but never shown: the actual submitted URL count, via a new
      derived `airbnbUrlCount` (`useMemo` over `airbnbUrlsText`, additive, no new state) — the
      polling message now reads "Importing 3 listings…" instead of a vague generic sentence, using
      only real, already-available information. Rebuilt the results list on `ListRow` (Task 3) +
      `StatusChip` (Task 2's tone system: `created`→live/green, `updated`→progress/blue,
      `error`→destructive/red) instead of a plain `<ul>` with raw `text-destructive`/
      `text-muted-foreground` classes; moved a failed import's actual error message out of the
      chip (which would have squeezed a long message into a small pill) into body text below the
      row, chip just says "Error."
      - Reverify: ✅ `npx tsc --noEmit` clean. ✅ `npm run build` clean, all 15 routes compiled.
        ✅ `git diff` confirms zero changes to `handleImportAirbnbUrls`'s trigger/poll/state-machine
        logic — only one new derived `useMemo` and the render layer changed. **Deliberately did
        NOT drive a real import through to completion** — same reasoning as Task 14 (a real
        Airbnb URL submission triggers an actual paid Bright Data scrape and permanently creates/
        modifies properties; this task didn't touch the trigger/poll logic, so testing it live
        wasn't necessary to verify correctness, and doing so risks real cost/side effects for a
        code path proven unchanged by the diff). ⚠️ No browser-automation tool; the new
        `ListRow`/`StatusChip`-based results list and the "Importing N listings…" message are
        visually unconfirmed. Recommend an actual click-through next time a browser + willingness
        to run one real (or lower-cost test-mode, if one exists) import is available.
- [x] **17. Pricing: visual polish + dedupe `Row` helper.** Extracted the identical local `Row`
      function from both `pricing/page.tsx` and `settings/page.tsx` (byte-identical except
      Settings' narrower `value: string` vs Pricing's `value: string | number`) into a shared
      `src/components/ui/definition-row.tsx` (`DefinitionRow`, unioned type). Fixed the same
      off-8pt-grid form-grid `gap-3`→`gap-4` pattern on both of Pricing's forms (rule-creation,
      quote calculator). No forecasting charts added — confirmed again that
      `AnalyticsTimeseries` remains a general endpoint, not pricing-rule-specific.
      - Reverify: ✅ `npx tsc --noEmit` clean. ✅ `npm run build` clean, all 15 routes compiled.
        ✅ Grep-confirmed zero stray `Row`/bare references left in either file after the
        extraction. ✅ **Ran a real, live quote calculation** against the demo backend (read-only,
        no mutation) — `POST /pricing/quote` for a real property ("Sunset Palms Beach Villa"),
        2 nights, confirmed the response shape (`nights`, `base_total`, `weekend_nights`,
        `cleaning_fee`, `tax_amount`, `discount_percent`, `discount_amount`, `total`,
        `per_night_avg`) matches exactly what `DefinitionRow` now renders, and that
        `weekend_nights: 2` for a real 2-night weekend stay matches the documented weekend-surge
        logic in CLAUDE.md. **Deliberately did not create/delete a real pricing rule** — asked you
        first since `handleCreateRule`/`handleDeleteRule` weren't touched by this task (only the
        rendering around them changed), consistent with Tasks 14/16; you agreed to skip it. ⚠️
        No browser-automation tool; visual rendering of `DefinitionRow` in both pages not
        pixel-confirmed.
- [x] **18. Technicians: card layout** — replaced the table with a card grid (name, specialty +
      property via a `Wrench` icon line, `rating` via a filled `Star` icon — confirmed real field
      on `TechnicianOut`, no `distance` field added per Task 0.2's decision). Added the first
      `tel:` link in the codebase (`Button render={<a href="tel:...">}`, same render-prop
      composition pattern already used for `Button render={<Link .../>}` in the Call detail
      page). Delete action moved from a table-row button to a small `X` icon in the card header
      (same `handleDelete(tech.id)` call, unchanged). Fixed the same off-8pt-grid form `gap-3`→
      `gap-4`.
      - Reverify: ✅ `npx tsc --noEmit` clean. ✅ `npm run build` clean, all 15 routes compiled.
        ✅ `git diff` confirms `handleCreate`/`handleDelete`/`api.technicians.*` calls are
        completely unchanged — only the JSX around the delete button and the gap value changed.
        ✅ Checked real production data: **zero technicians currently exist** in the demo account,
        meaning only the empty-state message is exercisable against real data today — could not
        visually confirm the new card layout against a real populated row. Asked you whether to
        create a real test technician to verify this and exercise add/remove end-to-end; you
        opted to skip the mutation, consistent with Tasks 14/16/17 (the create/delete handlers
        weren't touched by this task). ⚠️ No browser-automation tool; the card layout, icons, and
        `tel:` link behavior are unverified visually and against real technician data — flagging
        this as the least-verified task in Phase 3, since it's the only one with zero real rows
        to check against even read-only.
- [x] **19. Settings: styling polish + tabs decision point.** Fixed the three raw `<label
      className="text-sm font-medium">` elements (in Voice agent personalization) to use the
      shared `Label` component. Used `DefinitionRow` (dedup'd in Task 17) for the account-detail
      rows. **Asked before building tabs**, per the plan's explicit decision point — you chose the
      fullest option: a real `Tabs` layout with 5 tabs. 2 real, functional tabs (Workspace: Host
      account + Escalation notifications + Lead intake number; Voice AI: agent personalization) —
      all 4 original cards preserved exactly, just regrouped and reindented, zero logic changes —
      plus 3 placeholder "coming soon" tabs (Billing, API, Team) via a new small `ComingSoonTab`
      component, since none of those have any backend support (confirmed again per Task 0.3).
      - Reverify: ✅ `npx tsc --noEmit` clean (twice — before and after a reindentation pass to
        fix the mixed indentation introduced by wrapping existing blocks in `TabsContent`). ✅
        `npm run build` clean, all 15 routes compiled. ✅ Verified no content was lost during the
        reindentation: diffed all rendered text nodes before/after — the only apparent
        discrepancy was the "Escalation phrase"/"Personality note" label text moving onto a
        single line (the `<label>`→`<Label>` fix from earlier in this task), confirmed present
        exactly once each via grep, not actually lost. ✅ `git diff` confirms all four
        `handleSave*` functions and their `api.auth.updateMe` calls are byte-identical — only
        JSX indentation changed around them. **Deliberately did not submit any of the four real
        save forms** — asked you first since none of the handlers were touched and doing so would
        permanently overwrite the demo account's real notification email / lead exophone / agent
        personalization values; you agreed to skip it. ⚠️ No browser-automation tool; the new tab
        bar (5 tabs, `Tabs`/`TabsList`/`TabsTrigger`/`TabsContent` composition already proven
        elsewhere in `login/page.tsx`) is visually and interactively unverified — recommend
        clicking through all 5 tabs next time a browser is available.

## Phase 4 — New interaction patterns (highest risk, reviewed independently)

- [ ] **20. Calendar grid rebuild** — sticky property column + date header, hover-preview, filters.
      First: read current data-fetching fully; enumerate every existing interactive element
      (cell-click→dialog, filters, date nav) as a checklist before starting.
      - Reverify: every enumerated interactive element re-tested post-rebuild; block dates →
        confirm blocked; unblock → confirm cleared; confirm blocking/unblocking dates doesn't
        affect iCal-derived bookings or availability the voice agent reads via `calendar_service`
        at call time (read-only check on that service, no code change expected, just confirm the
        data model isn't altered).
- [ ] **21. Leads Kanban board** — columns on `status` axis only; `lead_temperature` shown, never
      mutated by drag; drag must call the exact same typed update the edit-dialog uses.
      - Reverify: drag a card, inspect the actual network payload sent — confirm it contains only
        `status`, never `lead_temperature`; confirm the voice agent's lead-creation/update tool
        path (`app/services/tool_handlers.py`, off-limits per standing rule 2) is untouched by
        grep-diffing that file against its pre-task version.
- [ ] **22. Guests CRM drawer** — gated on Task 0.1 shipping first. Drawer-not-navigation using
      `name`/`phone`/`total_stays`/`notes`/`preferences`/Avatar + real `lifetime_revenue`/
      `recent_calls` from the new endpoint.
      - Reverify: open drawer for a guest with calls and one with none (empty state); confirm
        `lifetime_revenue` matches a manual sum check against that guest's call sessions in the DB.

---

## Execution model

- One task at a time; phases sequential.
- After each task: run its reverify checklist, record ✅/❌ + note in this file, only then start
  the next.
- Any task that turns out to need backend work not already listed in Phase 0: stop, add a new
  Phase 0 sub-task here, get it done and tested first, then resume.
