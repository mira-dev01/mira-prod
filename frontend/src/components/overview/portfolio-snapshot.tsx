"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Cell, CellTitle, Hint } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { formatINR, formatINRCompact, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Completeness, OverviewData } from "@/lib/types";

/**
 * Overview's "Portfolio snapshot": four headline numbers as equal cells and a
 * one-line summary. Renders inside the page's Sheet. Renders what
 * GET /analytics/overview returns and computes nothing itself -- every
 * definition lives in backend/app/services/analytics_service.py. "No data" is
 * never shown as a zero: a null value renders as a quiet "—" with a note.
 */

function Metric({ label, value, note, unavailable }: { label: string; value: string; note: string; unavailable?: boolean }) {
  return (
    <Cell className="col-span-6 p-4 sm:p-5 xl:col-span-3">
      <CellTitle>{label}</CellTitle>
      <p
        className={cn(
          "font-heading text-[clamp(1.75rem,2.4vw,2.25rem)] leading-tight font-semibold tracking-tight text-primary tabular-nums",
          unavailable && "text-muted-foreground"
        )}
      >
        {value}
      </p>
      <Hint>{note}</Hint>
    </Cell>
  );
}

function SummaryItem({ value, label, title }: { value: string; label: string; title?: string }) {
  return (
    <span className="inline-flex items-baseline gap-1.5" title={title}>
      <span className="font-semibold text-foreground tabular-nums">{value}</span>
      <span>{label}</span>
    </span>
  );
}

function pricedNote(c: Completeness, emptyNote: string): string {
  if (c.bookings_total === 0) return emptyNote;
  if (c.bookings_priced === 0) return "No prices yet";
  if (c.is_complete) return `${c.bookings_priced} booking${c.bookings_priced === 1 ? "" : "s"}`;
  return `${c.bookings_priced} of ${c.bookings_total} priced`;
}

// Upcoming revenue is forward-looking (nights from today), not tied to the
// selected period -- the note says so.
function upcomingNote(c: Completeness): string {
  if (c.bookings_total === 0) return "None upcoming";
  if (c.is_complete) return "From today";
  return `From today · ${c.bookings_priced} of ${c.bookings_total} priced`;
}

export function PortfolioSnapshotSkeleton() {
  return (
    <>
      {Array.from({ length: 4 }).map((_, i) => (
        <Cell key={i} className="col-span-6 p-4 sm:p-5 xl:col-span-3" aria-busy="true" aria-label="Loading portfolio snapshot">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-9 w-28" />
          <Skeleton className="h-3 w-32" />
        </Cell>
      ))}
    </>
  );
}

export function PortfolioSnapshotError({ onRetry }: { onRetry: () => void }) {
  return (
    <Cell className="flex-row flex-wrap items-center justify-between gap-3">
      <p className="text-sm font-medium">Couldn&apos;t load your portfolio snapshot</p>
      <Button size="sm" variant="outline" onClick={onRetry}>
        Try again
      </Button>
    </Cell>
  );
}

export function PortfolioSnapshot({ data }: { data: OverviewData }) {
  const p = data.portfolio;
  const { activity, opportunities } = data;

  const occupancyNote = !data.has_properties
    ? "Add a property to start tracking"
    : p.booked_nights === 0
      ? "No bookings"
      : `${p.booked_nights} of ${p.available_nights} nights`;

  const callsNote =
    activity.total_calls === 0
      ? ""
      : [
          activity.answer_rate !== null ? `${formatPercent(activity.answer_rate)} answered` : null,
          `${activity.escalated_calls} escalation${activity.escalated_calls === 1 ? "" : "s"}`,
        ]
          .filter(Boolean)
          .join(" · ");

  return (
    <>
      <Metric label="Occupancy" value={formatPercent(p.occupancy)} unavailable={p.occupancy === null} note={occupancyNote} />
      <Metric
        label="Revenue"
        value={p.revenue === null ? "—" : formatINRCompact(p.revenue)}
        unavailable={p.revenue === null}
        note={pricedNote(p.completeness, "No bookings")}
      />
      <Metric
        label="Upcoming revenue"
        value={p.upcoming_revenue === null ? "—" : formatINRCompact(p.upcoming_revenue)}
        unavailable={p.upcoming_revenue === null}
        note={upcomingNote(p.upcoming_completeness)}
      />
      <Metric
        label="ADR"
        value={formatINR(p.adr)}
        unavailable={p.adr === null}
        note={p.revpar !== null ? `RevPAR ${formatINR(p.revpar)}` : "Per night"}
      />

      <Cell className="flex-row flex-wrap items-center gap-x-6 gap-y-2 py-3 text-sm text-muted-foreground">
        <SummaryItem
          value={String(opportunities.booking_opportunities)}
          label={opportunities.booking_opportunities === 1 ? "booking opportunity" : "booking opportunities"}
          title="Qualified guests (warm or above) from booking conversations in this period"
        />
        {opportunities.high_intent > 0 && (
          <SummaryItem
            value={String(opportunities.high_intent)}
            label="high-intent"
            title="Of those, guests at hot or booking intent"
          />
        )}
        <SummaryItem
          value={String(data.mira_attributed_bookings)}
          label={data.mira_attributed_bookings === 1 ? "Mira-attributed booking" : "Mira-attributed bookings"}
          title="Bookings made in this period that you confirmed came from Mira"
        />
        <SummaryItem value={String(activity.total_calls)} label={activity.total_calls === 1 ? "call" : "calls"} />
        {callsNote && <span>{callsNote}</span>}
        <Button className="ml-auto" variant="ghost" size="sm" render={<Link href="/dashboard/analytics" />}>
          Analytics
          <ArrowRight className="size-3.5" />
        </Button>
      </Cell>
    </>
  );
}
