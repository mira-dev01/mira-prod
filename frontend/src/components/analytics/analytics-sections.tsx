"use client";

import {
  BadgeCheck,
  BedDouble,
  CalendarCheck,
  CircleAlert,
  IndianRupee,
  MessageSquare,
  Moon,
  Percent,
  PhoneForwarded,
  Tag,
  TrendingUp,
  Wallet,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { BarList, MetricTile } from "@/components/analytics/metric-tile";
import { StatusChip } from "@/components/status-chip";
import { formatINR, formatINRCompact, formatPercent, formatStayDates } from "@/lib/format";
import type {
  AnalyticsDashboard,
  Completeness,
  ReconciliationQueue,
  SampledMetric,
} from "@/lib/types";

/** "Revenue based on 16 of 20 bookings with confirmed pricing." + action. */
function CompletenessNote({
  completeness,
  subject,
  onConfirm,
}: {
  completeness: Completeness;
  subject: string;
  onConfirm?: () => void;
}) {
  if (completeness.bookings_total === 0 || completeness.is_complete) return null;
  const missing = completeness.bookings_missing_price;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-muted/50 px-3 py-2 text-sm">
      <p className="text-muted-foreground">
        {subject} based on {completeness.bookings_priced} of {completeness.bookings_total} booking
        {completeness.bookings_total === 1 ? "" : "s"} with confirmed pricing.
      </p>
      {onConfirm && (
        <Button size="sm" variant="outline" onClick={onConfirm}>
          Confirm {missing} booking price{missing === 1 ? "" : "s"}
        </Button>
      )}
    </div>
  );
}

function sampledHint(metric: SampledMetric): string | undefined {
  if (metric.sufficient) return `Based on ${metric.sample_size}`;
  return `Not enough data yet (${metric.sample_size} of ${metric.min_sample_size} needed)`;
}

function sampledValue(metric: SampledMetric, format: (v: number) => string): string {
  return metric.value === null ? "—" : format(metric.value);
}

// --- A. Portfolio performance ---------------------------------------------

export function PortfolioPerformanceSection({
  data,
  onConfirmPrices,
}: {
  data: AnalyticsDashboard;
  onConfirmPrices: () => void;
}) {
  const p = data.portfolio;
  const change = p.comparison?.change;
  const vsLabel = "vs previous period";
  return (
    <Card>
      <CardHeader>
        <CardTitle>Portfolio performance</CardTitle>
        <CardDescription>
          {p.booked_nights} of {p.available_nights} available nights booked
          {p.blocked_nights ? ` · ${p.blocked_nights} blocked night${p.blocked_nights === 1 ? "" : "s"} excluded` : ""}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          <MetricTile
            icon={BedDouble}
            label="Occupancy"
            value={formatPercent(p.occupancy)}
            muted={p.occupancy === null}
            change={change?.occupancy}
            changeLabel={vsLabel}
          />
          <MetricTile
            icon={IndianRupee}
            label="Revenue"
            value={formatINRCompact(p.revenue)}
            muted={p.revenue === null}
            change={change?.revenue}
            changeLabel={vsLabel}
            hint={p.revenue === null && p.completeness.bookings_total > 0 ? "No confirmed prices yet" : undefined}
          />
          <MetricTile
            icon={Tag}
            label="ADR"
            value={formatINR(p.adr)}
            muted={p.adr === null}
            change={change?.adr}
            changeLabel={vsLabel}
            hint="Average daily rate"
          />
          <MetricTile
            icon={TrendingUp}
            label="RevPAR"
            value={formatINR(p.revpar)}
            muted={p.revpar === null}
            change={change?.revpar}
            changeLabel={vsLabel}
            hint="Revenue per available night"
          />
          <MetricTile
            icon={CalendarCheck}
            label="Upcoming revenue"
            value={formatINRCompact(p.upcoming_revenue)}
            muted={p.upcoming_revenue === null}
            hint={
              p.upcoming_completeness.is_complete
                ? "Confirmed future stays"
                : `${p.upcoming_completeness.bookings_priced} of ${p.upcoming_completeness.bookings_total} future stays priced`
            }
          />
        </div>
        <CompletenessNote completeness={p.completeness} subject="Revenue" onConfirm={onConfirmPrices} />
      </CardContent>
    </Card>
  );
}

// --- B. Booking funnel -----------------------------------------------------

export function BookingFunnelSection({ data }: { data: AnalyticsDashboard }) {
  const stages = data.funnel.stages;
  const empty = stages[0]?.value === 0;
  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>Booking funnel</CardTitle>
        <CardDescription>From every Mira conversation to a booking.</CardDescription>
      </CardHeader>
      <CardContent>
        {empty ? (
          <p className="text-sm text-muted-foreground">No Mira conversations in this period yet.</p>
        ) : (
          <BarList
            rows={stages.map((s) => ({
              key: s.key,
              label: s.label,
              sublabel: s.unit === "guests" ? "guests" : undefined,
              value: s.value,
              detail: s.rate_from_previous !== null ? `${formatPercent(s.rate_from_previous)} of previous` : undefined,
            }))}
          />
        )}
      </CardContent>
    </Card>
  );
}

// --- C. Mira impact --------------------------------------------------------

export function MiraImpactSection({
  data,
  onReviewMatches,
}: {
  data: AnalyticsDashboard;
  onReviewMatches: () => void;
}) {
  const i = data.impact;
  const afterHours = i.after_hours_opportunities;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Mira impact</CardTitle>
        <CardDescription>Only bookings you&apos;ve confirmed as Mira bookings count here.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
          <MetricTile icon={BadgeCheck} label="Mira-attributed bookings" value={String(i.attributed_bookings)} />
          <MetricTile
            icon={Wallet}
            label="Mira-attributed revenue"
            value={formatINRCompact(i.attributed_revenue)}
            muted={i.attributed_revenue === null}
            hint={
              i.attributed_completeness.is_complete
                ? undefined
                : `${i.attributed_completeness.bookings_priced} of ${i.attributed_completeness.bookings_total} priced`
            }
          />
          <MetricTile
            icon={Moon}
            label="After-hours booking enquiries"
            value={afterHours.configured ? String(afterHours.value ?? 0) : "—"}
            muted={!afterHours.configured}
            hint={
              afterHours.configured
                ? `${formatPercent(afterHours.share)} of booking conversations`
                : "Set your call hours in Settings to see this"
            }
          />
          <MetricTile
            icon={IndianRupee}
            label="Revenue recovered"
            value={formatINRCompact(i.revenue_recovered)}
            muted={i.revenue_recovered === null}
            hint={`${i.recovery.busy_calls} busy-line call${i.recovery.busy_calls === 1 ? "" : "s"} · ${i.recovery.recovered} guest${i.recovery.recovered === 1 ? "" : "s"} re-engaged`}
          />
          <MetricTile
            icon={MessageSquare}
            label="Resolved by Mira"
            value={String(i.resolved_by_mira)}
            hint={i.conversations ? `${formatPercent(i.resolved_by_mira_rate)} of conversations` : undefined}
          />
          <MetricTile icon={PhoneForwarded} label="Host escalations" value={String(i.host_escalations)} />
        </div>
        {i.awaiting_confirmation > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-muted/50 px-3 py-2 text-sm">
            <p className="text-muted-foreground">
              {i.awaiting_confirmation} possible Mira booking{i.awaiting_confirmation === 1 ? "" : "s"} waiting for
              your confirmation — not counted above.
            </p>
            <Button size="sm" variant="outline" onClick={onReviewMatches}>
              Review
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// --- D. Guest intent -------------------------------------------------------

export function GuestIntentSection({ data }: { data: AnalyticsDashboard }) {
  const { categories, conversations_with_data } = data.guest_intent;
  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>What guests ask about</CardTitle>
        <CardDescription>
          {conversations_with_data
            ? `Across ${conversations_with_data} conversation${conversations_with_data === 1 ? "" : "s"} with recorded questions`
            : "From questions and objections recorded in Mira conversations."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {conversations_with_data === 0 ? (
          <p className="text-sm text-muted-foreground">No guest questions recorded in this period yet.</p>
        ) : (
          <BarList
            rows={categories
              .filter((c) => c.count > 0)
              .map((c) => ({
                key: c.key,
                label: c.label,
                value: c.count,
                detail: formatPercent(c.share),
              }))}
          />
        )}
      </CardContent>
    </Card>
  );
}

// --- E. Pricing & negotiation ----------------------------------------------

export function PricingSection({ data }: { data: AnalyticsDashboard }) {
  const pr = data.pricing;
  const tiles = [
    { icon: Tag, label: "Initial quote / night", metric: pr.avg_initial_quote_per_night, format: formatINR },
    { icon: Tag, label: "Negotiated price / night", metric: pr.avg_negotiated_price_per_night, format: formatINR },
    { icon: IndianRupee, label: "Final booking price / night", metric: pr.avg_final_price_per_night, format: formatINR },
    {
      icon: Percent,
      label: "Negotiation → booking",
      metric: pr.negotiation_conversion,
      format: (v: number) => formatPercent(v),
    },
    { icon: Percent, label: "Average discount", metric: pr.avg_discount, format: (v: number) => formatPercent(v, 1) },
    {
      icon: CircleAlert,
      label: "Price objections",
      metric: pr.price_objection_rate,
      format: (v: number) => formatPercent(v),
    },
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Pricing &amp; negotiation</CardTitle>
        <CardDescription>
          {pr.negotiations} negotiation{pr.negotiations === 1 ? "" : "s"} · {pr.price_objections} price objection
          {pr.price_objections === 1 ? "" : "s"} in this period. Prices compared per night.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
          {tiles.map((t) => (
            <MetricTile
              key={t.label}
              icon={t.icon}
              label={t.label}
              value={sampledValue(t.metric, t.format)}
              muted={t.metric.value === null}
              hint={sampledHint(t.metric)}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// --- F. Needs confirmation -------------------------------------------------

export function NeedsConfirmationSection({
  queue,
  propertyId,
  onOpen,
  expanded,
  onToggleExpanded,
}: {
  queue: ReconciliationQueue;
  propertyId: string | null;
  onOpen: (bookingId: string) => void;
  expanded: boolean;
  onToggleExpanded: () => void;
}) {
  const items = propertyId ? queue.items.filter((i) => i.booking.property_id === propertyId) : queue.items;
  if (items.length === 0) return null;
  const visible = expanded ? items : items.slice(0, 3);
  return (
    <Card id="needs-confirmation">
      <CardHeader>
        <CardTitle>Booking details need confirmation</CardTitle>
        <CardDescription>
          A quick answer from you keeps revenue and Mira impact accurate. We only ask once per booking.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-1">
        {visible.map((item) => (
          <button
            key={item.booking.id}
            type="button"
            onClick={() => onOpen(item.booking.id)}
            className="flex w-full flex-wrap items-center justify-between gap-2 rounded-lg px-3 py-2.5 text-left text-sm transition-colors hover:bg-accent"
          >
            <span className="min-w-0">
              <span className="font-medium">{item.property_name}</span>
              <span className="ml-2 text-muted-foreground">
                {formatStayDates(item.booking.check_in, item.booking.check_out)}
              </span>
            </span>
            <span className="flex flex-wrap gap-1.5">
              {item.booking.needs_attribution_review && <StatusChip status="Mira match?" tone="purple" />}
              {item.booking.needs_price_confirmation && <StatusChip status="Price missing" tone="pending" />}
            </span>
          </button>
        ))}
        {items.length > 3 && (
          <Button variant="ghost" size="sm" onClick={onToggleExpanded}>
            {expanded ? "Show fewer" : `Show all ${items.length}`}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
