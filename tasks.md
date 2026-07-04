# Mira Dashboard Design-System Rollout — Task Breakdown

Source plan: `/Users/abhaya/.claude/plans/idempotent-floating-fox.md`. This file breaks that plan
into sequential batches. Each batch is a complete, end-to-end vertical slice (backend + frontend +
verification where applicable) — do not start a batch until the previous one is fully verified,
since later batches depend on earlier ones (shared components, date-range context, API client
shapes). Check off subtasks as they're completed.

Legend: `[ ]` not started · `[~]` in progress · `[x]` done

---

## Batch 0 — Foundation: backend date-range filtering

Goal: every list endpoint that should support date filtering does, real data in/out, verified via
`/docs` before any frontend work touches it. No visible UI change yet.

- [ ] 0.1 `backend/app/api/v1/common.py`: add `DateRange` dataclass (`start_date`, `end_date`,
      `.since`/`.until` properties, exclusive-upper-bound semantics) + `date_range_query`
      dependency function.
- [ ] 0.2 `backend/app/api/v1/analytics.py`: wire `date_range` into `/summary` (keep `days` as
      fallback when no explicit dates sent); add `start_date`/`end_date` to the response.
- [ ] 0.3 `backend/app/api/v1/analytics.py`: add new `/analytics/timeseries` endpoint
      (`metric=total_calls|completed_calls|escalated_calls|pipeline_value`, daily `date_trunc`
      buckets, zero-filled, Postgres-specific SQL confirmed safe).
- [ ] 0.4 `backend/app/api/v1/calls.py`: add `date_range` filter to `/calls` list.
- [ ] 0.5 `backend/app/api/v1/leads.py` + `backend/app/services/lead_service.py`: thread
      `date_range` through router → service → query.
- [ ] 0.6 `backend/app/api/v1/guests.py`: add `date_range` filter on the `CallSession` join query
      (not on `GuestProfile` directly).
- [ ] 0.7 `backend/app/api/v1/pricing.py`: add `date_range` to `/pricing/rules` list only (verify
      `PricingRule` has `created_at` via `TimestampMixin` first).
- [ ] 0.8 Verify: syntax-check all touched backend files; boot the app locally
      (`uvicorn app.main:app`) and hit `/docs`, exercise `/analytics/summary`,
      `/analytics/timeseries`, `/calls`, `/leads`, `/guests`, `/pricing/rules` with and without
      `start_date`/`end_date`, confirming a record dated exactly on `end_date` is included
      (inclusive-day semantics working correctly).

**Exit criteria**: all 6 endpoints accept optional `start_date`/`end_date`, boundary behavior
confirmed via manual `/docs` testing, zero frontend changes yet, nothing broken (existing calls
without the new params behave exactly as before).

---

## Batch 1 — Frontend foundation: shared components + date-range state

Goal: every new shared component exists, compiles, and is unit-testable in isolation (e.g. via a
throwaway page or Storybook-less manual mount) before Overview consumes them. No page rewired yet.

- [ ] 1.1 Install new deps: shadcn `calendar` + `popover` (`react-day-picker`,
      `@radix-ui/react-popover`) — confirm `package.json`/lockfile updated, app still builds.
- [ ] 1.2 `frontend/src/components/sparkline.tsx`: inline-SVG sparkline, min/max normalize, guard
      empty-array and `min === max` cases.
- [ ] 1.3 `frontend/src/components/status-chip.tsx`: typed wrapper (`status`, explicit `tone`)
      around existing `Badge` + `.badge-status-*` classes; add a leading dot glyph if the current
      CSS doesn't already render one (check visually against the screenshot).
- [ ] 1.4 `frontend/src/components/actionable-card.tsx`: `title`/`summary`/`metadata`/`priority`/
      `onClick` row atom, chevron rendered only when `onClick` is provided.
- [ ] 1.5 `frontend/src/components/date-range-context.tsx` + `frontend/src/hooks/use-date-range.ts`:
      `DateRangeProvider` (default = last 30 days), `useDateRange()` hook exposing
      `startDate`/`endDate`/`setRange`/ISO getters.
- [ ] 1.6 `frontend/src/components/date-range-picker.tsx`: built on the new calendar+popover,
      reads/writes `useDateRange()`, trigger shows formatted range (e.g. "Mar 8 – Apr 7, 2026").
- [ ] 1.7 `frontend/src/components/stat-card.tsx`: icon badge + label + value + optional
      sparkline; `trend` prop present in the type but left unwired/hidden (Batch 6 decides this).
- [ ] 1.8 Mount `<DateRangeProvider>` in `frontend/src/app/dashboard/layout.tsx`, wrapping page
      content.
- [ ] 1.9 `frontend/src/lib/api.ts` + `frontend/src/lib/types.ts`: update `analytics.summary`
      (object params, `start_date`/`end_date`/`days` fallback), add `analytics.timeseries`,
      update `calls.list`/`leads.list`/`guests.list`/`pricing.rules` to accept optional
      `startDate`/`endDate` (and finally wire `status`/`urgency`/`limit` on `calls.list`, which
      the backend already supported). Add `AnalyticsTimeseriesPoint`/`AnalyticsTimeseries` types.
- [ ] 1.10 Verify: `npx tsc --noEmit` and `npm run build` both clean. No page imports these yet,
      so this is purely "does it compile," not "does it look right" — visual verification happens
      in Batch 2.

**Exit criteria**: 6 new components + context/hook + api.ts/types.ts changes all compile clean;
zero pages modified yet; build passes.

---

## Batch 2 — Overview page rebuild (the flagship, full integration)

Goal: Overview visually and functionally matches the reference screenshot (minus trend indicators
and the bottom insights strip, both deferred — see Batches 6/7), using only the Batch 0/1
components, with real backend data end to end.

- [ ] 2.1 Wire header: replace hardcoded `days=30` with `useDateRange()`; add `<DateRangePicker />`
      next to the existing "Include browser test calls" toggle.
- [ ] 2.2 Wire data fetching: `api.analytics.summary({...})`, `api.calls.list({startDate, endDate,
      limit: 8})` (replacing the old fetch-100-then-slice), `api.notifications.list()` (unchanged,
      intentionally not date-scoped), 4x `api.analytics.timeseries({metric, ...})`.
- [ ] 2.3 Replace the 5 inline `<SummaryCard>` calls with 5 `<StatCard>` calls (icon + color per
      metric per the plan's mapping); delete the now-dead inline `SummaryCard` function.
- [ ] 2.4 Recent Calls panel: swap plain-text status cell for `<StatusChip>`; add trailing chevron
      cell; add "View all calls" footer link to `/dashboard/calls`.
- [ ] 2.5 Live Requests panel: add "{count} active" badge next to the title; replace flat
      notification rendering with `<ActionableCard>` per notification, using the
      synthesized-title/summary mapping from the plan; render "View all requests" link
      disabled/omitted (no destination page exists).
- [ ] 2.6 Verify empty/loading/edge states: zero-data date range (sparkline flat-line guard
      actually exercised), `include_test_calls` toggled both ways, loading skeletons still work.
- [ ] 2.7 Verify: `npx tsc --noEmit`, `npm run build`, then manually run the dev server and click
      through Overview in a browser — confirm visual match against the reference screenshot and
      that every data point (stat values, sparklines, recent calls, live requests) reflects real
      backend data, not placeholders.

**Exit criteria**: Overview page fully matches the screenshot (excluding trend arrows and the
bottom insights strip), backed by real data, verified live in a browser — this is the reference
implementation every later page mirrors.

---

## Batch 3 — Sidebar icons

Goal: every nav item has an icon, nothing else about the sidebar changes.

- [ ] 3.1 `frontend/src/components/sidebar-nav.tsx`: add `icon: LucideIcon` to each of the 9
      `links` entries (Home, Building2, Calendar, Phone, Users, UserRound, HelpCircle, Wrench,
      Settings).
- [ ] 3.2 Update `NavLinks` JSX to render the icon before the label in a flex row; keep the `✳︎`
      glyph unchanged for the Mira logo and "Talk to Mira" entry.
- [ ] 3.3 (Optional, cheap) swap mobile hamburger/close inline SVGs for lucide `Menu`/`X`.
- [ ] 3.4 Verify: build passes; manually check desktop sidebar + mobile drawer render correctly,
      active-link styling still works.

**Exit criteria**: icons visible on every nav link, desktop and mobile, no regressions to
active-state highlighting or the pinned Talk-to-Mira/logout areas.

---

## Batch 4 — Propagate: Properties + FAQ (StatusChip only, lowest risk)

Goal: prove `StatusChip` works correctly in a second and third context beyond Overview, with
minimal risk, before moving to the more involved pages.

- [ ] 4.1 `frontend/src/app/dashboard/properties/page.tsx`: replace the conditional "Voice agent
      live" `Badge`/`.badge-status-live` usage with `<StatusChip status="live" tone="live">`.
- [ ] 4.2 `frontend/src/app/dashboard/faq/page.tsx`: replace the verified/pending `Badge` with
      `<StatusChip>` (verified→live, pending→pending tone).
- [ ] 4.3 Verify: build passes; manually confirm both pages render identically in substance (same
      information, new chip styling) and that Properties' "Set Discount"/edit/sync/remove actions
      and FAQ's verify-toggle button still work unchanged.

**Exit criteria**: both pages visually consistent with the new chip style, zero functional
regressions.

---

## Batch 5 — Propagate: Calls (list + detail)

Goal: Calls page gets StatusChip + real date-range filtering, matching Overview's pattern.

- [ ] 5.1 `frontend/src/app/dashboard/calls/page.tsx`: add `<DateRangePicker />` to the page
      header; wire `api.calls.list({startDate, endDate, status, urgency})` (finally using the
      status/urgency filters the backend already supported).
- [ ] 5.2 Replace the local `statusVariant`/`statusClassName` maps with `<StatusChip>`.
- [ ] 5.3 `frontend/src/app/dashboard/calls/[id]/page.tsx`: replace the status `Badge` with
      `<StatusChip>`.
- [ ] 5.4 Verify: build passes; manually confirm date-range filtering actually changes the visible
      call list, status filter dropdown (if present) still works alongside the new date picker,
      and the detail page link/navigation from the list is unaffected.

**Exit criteria**: Calls list is fully date-range-aware end to end (UI → api.ts → backend →
filtered results), status display consistent with Overview.

---

## Batch 6 — Propagate: Guests (date-range wiring only)

Goal: simplest remaining backend-wiring page, quick to verify before tackling Leads.

- [ ] 6.1 `frontend/src/app/dashboard/guests/page.tsx`: add `<DateRangePicker />`; wire
      `api.guests.list({startDate, endDate})`.
- [ ] 6.2 Verify: build passes; manually confirm the guest list actually narrows/widens correctly
      as the date range changes (spot-check against a guest known to have a call inside vs.
      outside the selected range).

**Exit criteria**: Guests list correctly reflects "guests with a call in this date range."

---

## Batch 7 — Propagate: Leads (full ActionableCard restructure — biggest single change)

Goal: Leads page converted from a dense table to ActionableCard rows, matching the Live-Requests
visual language, with the existing edit-dialog flow preserved exactly.

- [ ] 7.1 Design the per-lead ActionableCard field mapping: `title` (guest name + destination/
      interest), `summary` (key qualifying details), `metadata` (phone · dates · guest count,
      `·`-joined per the existing convention), `priority` (derived from `lead_temperature`:
      hot→high/destructive, warm→medium, cold→low).
- [ ] 7.2 `frontend/src/app/dashboard/leads/page.tsx`: replace the `<Table>` with a list of
      `<ActionableCard>`s per the mapping in 7.1; `onClick` opens the existing edit dialog
      unchanged (same dialog component/state, just a different trigger element).
- [ ] 7.3 Add `<DateRangePicker />` to the page header; wire `api.leads.list({startDate, endDate})`.
- [ ] 7.4 Verify every existing interaction still works from the new card trigger: opening the
      edit dialog, changing temperature/follow-up/summary, saving, deleting/escalation-related
      fields — nothing in the dialog itself changes, only how it's triggered.
- [ ] 7.5 Verify: build passes; manually click through the full edit flow end to end on the new
      card layout; confirm date-range filtering narrows the list correctly.

**Exit criteria**: Leads page fully restructured into ActionableCards, date-range-aware, every
pre-existing edit/save/delete interaction verified working unchanged.

---

## Batch 8 — Propagate: Pricing (optional, lowest priority)

Goal: minor backend-consistency pass, only if time allows — explicitly not blocking anything else.

- [ ] 8.1 `frontend/src/app/dashboard/pricing/page.tsx` + `backend/app/api/v1/pricing.py`: confirm
      `PricingRule` has `created_at`; if so, wire `date_range` on `/pricing/rules` and add the
      param to the frontend client call (UI picker optional here — lowest priority per the plan).
- [ ] 8.2 Verify: build passes; no regression to the existing rules-table/quote-calculator flows.

**Exit criteria**: `/pricing/rules` supports date filtering for API consistency; UI change here is
optional and can be skipped if deprioritized.

---

## Batch 9 — Trend data ("% vs last period") — ON HOLD

**Do not start this batch until you explicitly approve an approach.** Two designs are documented
in the plan file (`idempotent-floating-fox.md`, Section 2): (a) recommended — extend `/summary`
server-side with a `previous`-period block; (b) alternative — second client-side round-trip. Also
unresolved: whether "down" should render green instead of red for the Escalated-calls metric
specifically (a drop in escalations is good news, but the default up=green/down=red rule would
color it red).

- [ ] 9.0 **Decision checkpoint** — confirm approach (a) or (b), and the escalation-coloring
      question, before any code is written.
- [ ] 9.1 (If approach a) `backend/app/api/v1/analytics.py`: compute previous-period window,
      re-run the 4 metric queries against it, add `previous` key to `/summary` response.
- [ ] 9.2 `frontend/src/lib/types.ts`: add `previous` field to `AnalyticsSummary` type.
- [ ] 9.3 `frontend/src/app/dashboard/page.tsx`: compute `percent_change`/`direction` per metric,
      pass into each `<StatCard trend={...}>`.
- [ ] 9.4 Verify: build passes; manually confirm trend arrows/percentages match a hand-calculated
      expected value for at least one metric in a known date range.

**Exit criteria**: not started until Batch 9.0's decision checkpoint is explicitly resolved by you.

---

## Deferred / explicitly out of scope (not batched — future follow-up only)

- Sidebar bottom "Pipeline value" card + "Host Account" dropdown block.
- Bottom "Call insights" 4-stat strip on Overview (needs 3 new backend aggregations: peak-hour
  bucketing, top-property-by-volume, avg call duration).
- A dedicated Notifications/Requests list page (so "View all requests" has somewhere to go).
- Fixing pre-existing scoping quirks (`escalated_calls`/`open_notifications` scoped by
  owned-property-ids rather than user_id).
- Weekly/monthly bucketing for long date ranges on `/timeseries` (always daily buckets for now).
- Calendar, Technicians, Settings pages — no natural fit for any of the 5 patterns, left as-is.
