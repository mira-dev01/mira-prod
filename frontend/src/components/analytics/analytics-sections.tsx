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
import { InfoTip } from "@/components/ui/info-tip";
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

// --- Per-metric renderers (the customizable Analytics layout) -------------

/** Everything the page already loaded, shared by every renderer. */
export type AnalyticsWidgetContext = {
  data: AnalyticsDashboard;
  queue: ReconciliationQueue | null;
  propertyId: string | null;
  queueExpanded: boolean;
  onToggleQueue: () => void;
  onOpenBooking: (bookingId: string) => void;
  onConfirmPrices: () => void;
  onReviewMatches: () => void;
};

/** The ⓘ content for a metric: what it means, then how it's calculated. */
export function MetricInfo({ description, formula }: { description: string | null; formula: string | null }) {
  return (
    <>
      {description && <p className="text-foreground">{description}</p>}
      {formula && (
        <p className="text-muted-foreground">
          <span className="font-medium text-foreground">Formula: </span>
          {formula}
        </p>
      )}
    </>
  );
}

type TileSpec = Omit<React.ComponentProps<typeof MetricTile>, "info" | "label">;

const VS_PREVIOUS = "vs previous period";

/** Each tile id from backend app/services/analytics_widget_registry.py ->
 * its MetricTile props, read from the one /analytics/dashboard response. */
const TILES: Record<string, (c: AnalyticsWidgetContext) => TileSpec> = {
  occupancy: ({ data }) => {
    const p = data.portfolio;
    return {
      icon: BedDouble,
      value: formatPercent(p.occupancy),
      muted: p.occupancy === null,
      change: p.comparison?.change?.occupancy,
      changeLabel: VS_PREVIOUS,
      hint: `${p.booked_nights} of ${p.available_nights} nights booked${
        p.blocked_nights ? ` · ${p.blocked_nights} blocked excluded` : ""
      }`,
    };
  },
  revenue: ({ data, onConfirmPrices }) => {
    const p = data.portfolio;
    return {
      icon: IndianRupee,
      value: formatINRCompact(p.revenue),
      muted: p.revenue === null,
      change: p.comparison?.change?.revenue,
      changeLabel: VS_PREVIOUS,
      hint: p.revenue === null && p.completeness.bookings_total > 0 ? "No confirmed prices yet" : undefined,
      footer: <CompletenessNote completeness={p.completeness} subject="Revenue" onConfirm={onConfirmPrices} />,
    };
  },
  adr: ({ data }) => ({
    icon: Tag,
    value: formatINR(data.portfolio.adr),
    muted: data.portfolio.adr === null,
    change: data.portfolio.comparison?.change?.adr,
    changeLabel: VS_PREVIOUS,
  }),
  revpar: ({ data }) => ({
    icon: TrendingUp,
    value: formatINR(data.portfolio.revpar),
    muted: data.portfolio.revpar === null,
    change: data.portfolio.comparison?.change?.revpar,
    changeLabel: VS_PREVIOUS,
  }),
  upcoming_revenue: ({ data }) => {
    const p = data.portfolio;
    return {
      icon: CalendarCheck,
      value: formatINRCompact(p.upcoming_revenue),
      muted: p.upcoming_revenue === null,
      hint: p.upcoming_completeness.is_complete
        ? "Confirmed future stays"
        : `${p.upcoming_completeness.bookings_priced} of ${p.upcoming_completeness.bookings_total} future stays priced`,
    };
  },
  attributed_bookings: ({ data, onReviewMatches }) => {
    const i = data.impact;
    return {
      icon: BadgeCheck,
      value: String(i.attributed_bookings),
      footer:
        i.awaiting_confirmation > 0 ? (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-muted/50 px-3 py-2 text-xs">
            <p className="text-muted-foreground">
              {i.awaiting_confirmation} possible Mira booking{i.awaiting_confirmation === 1 ? "" : "s"} waiting for
              your confirmation — not counted.
            </p>
            <Button size="sm" variant="outline" onClick={onReviewMatches}>
              Review
            </Button>
          </div>
        ) : undefined,
    };
  },
  attributed_revenue: ({ data }) => {
    const i = data.impact;
    return {
      icon: Wallet,
      value: formatINRCompact(i.attributed_revenue),
      muted: i.attributed_revenue === null,
      hint: i.attributed_completeness.is_complete
        ? undefined
        : `${i.attributed_completeness.bookings_priced} of ${i.attributed_completeness.bookings_total} priced`,
    };
  },
  after_hours_enquiries: ({ data }) => {
    const afterHours = data.impact.after_hours_opportunities;
    return {
      icon: Moon,
      value: afterHours.configured ? String(afterHours.value ?? 0) : "—",
      muted: !afterHours.configured,
      hint: afterHours.configured
        ? `${formatPercent(afterHours.share)} of booking conversations`
        : "Set your call hours in Settings to see this",
    };
  },
  revenue_recovered: ({ data, propertyId }) => {
    const i = data.impact;
    return {
      icon: IndianRupee,
      value: formatINRCompact(i.revenue_recovered),
      muted: i.revenue_recovered === null,
      hint: `${i.recovery.busy_calls} busy-line call${i.recovery.busy_calls === 1 ? "" : "s"} · ${
        i.recovery.recovered
      } guest${i.recovery.recovered === 1 ? "" : "s"} re-engaged${propertyId ? " (all properties)" : ""}`,
    };
  },
  resolved_by_mira: ({ data }) => ({
    icon: MessageSquare,
    value: String(data.impact.resolved_by_mira),
    hint: data.impact.conversations ? `${formatPercent(data.impact.resolved_by_mira_rate)} of conversations` : undefined,
  }),
  host_escalations: ({ data }) => ({ icon: PhoneForwarded, value: String(data.impact.host_escalations) }),
  initial_quote: ({ data }) => sampledTile(Tag, data.pricing.avg_initial_quote_per_night, formatINR),
  negotiated_price: ({ data }) => sampledTile(Tag, data.pricing.avg_negotiated_price_per_night, formatINR),
  final_price: ({ data }) => sampledTile(IndianRupee, data.pricing.avg_final_price_per_night, formatINR),
  negotiation_conversion: ({ data }) =>
    sampledTile(Percent, data.pricing.negotiation_conversion, (v) => formatPercent(v)),
  avg_discount: ({ data }) => sampledTile(Percent, data.pricing.avg_discount, (v) => formatPercent(v, 1)),
  price_objections: ({ data }) =>
    sampledTile(CircleAlert, data.pricing.price_objection_rate, (v) => formatPercent(v)),
};

function sampledTile(icon: TileSpec["icon"], metric: SampledMetric, format: (v: number) => string): TileSpec {
  return { icon, value: sampledValue(metric, format), muted: metric.value === null, hint: sampledHint(metric) };
}

/** A panel with its ⓘ in the top-right corner. */
function WithInfo({ info, children }: { info: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="relative h-full">
      {children}
      <div className="absolute top-4 right-4">{info}</div>
    </div>
  );
}

/**
 * Renders one layout entry's metric, or null when it has nothing to show
 * (e.g. no bookings need confirmation) so the page skips its slot. An id
 * with no renderer is skipped too -- never rendered broken.
 */
export function renderAnalyticsMetric(
  entry: { id: string; label: string | null; description: string | null; formula: string | null },
  c: AnalyticsWidgetContext
): React.ReactNode {
  const info = <MetricInfo description={entry.description} formula={entry.formula} />;
  const tile = TILES[entry.id];
  if (tile) {
    return (
      <div className="h-full rounded-xl border bg-card">
        <MetricTile label={entry.label ?? entry.id} info={info} {...tile(c)} />
      </div>
    );
  }
  const infoTip = <InfoTip label={`About ${entry.label ?? entry.id}`}>{info}</InfoTip>;
  switch (entry.id) {
    case "booking_funnel":
      return <WithInfo info={infoTip}><BookingFunnelSection data={c.data} /></WithInfo>;
    case "guest_intent":
      return <WithInfo info={infoTip}><GuestIntentSection data={c.data} /></WithInfo>;
    case "needs_confirmation":
      if (!c.queue) return null;
      return (
        <NeedsConfirmationSection
          queue={c.queue}
          propertyId={c.propertyId}
          onOpen={c.onOpenBooking}
          expanded={c.queueExpanded}
          onToggleExpanded={c.onToggleQueue}
        />
      );
    default:
      return null;
  }
}
