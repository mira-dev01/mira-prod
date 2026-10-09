"use client";

import Link from "next/link";
import type { LucideIcon } from "lucide-react";
import { ArrowRight, BadgeCheck, BedDouble, CalendarCheck, Flame, IndianRupee, Phone, Sparkles, Tag } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatINR, formatINRCompact, formatPercent } from "@/lib/format";
import { cn, glassCardClassName } from "@/lib/utils";
import type { Completeness, OverviewData } from "@/lib/types";

/**
 * Overview's "Portfolio snapshot". Renders what GET /analytics/overview
 * returns and computes nothing itself -- every definition lives in
 * backend/app/services/analytics_service.py. "No data" is never shown as a
 * zero: a null value renders as a quiet "—" with a line saying why.
 */

function IconChip({ icon: Icon }: { icon: LucideIcon }) {
  return (
    <span
      className="flex size-8 items-center justify-center rounded-full"
      style={{ backgroundColor: "color-mix(in oklch, var(--accent-warm) 16%, transparent)" }}
    >
      <Icon className="size-4" style={{ color: "var(--accent-warm)" }} />
    </span>
  );
}

function PrimaryMetric({
  icon,
  label,
  value,
  note,
  unavailable,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  note: string;
  unavailable?: boolean;
}) {
  return (
    <div className="space-y-2 px-1 py-2 sm:px-4">
      <div className="flex items-center gap-2">
        <IconChip icon={icon} />
        <p className="text-sm text-muted-foreground">{label}</p>
      </div>
      <p
        className={cn(
          "text-3xl font-semibold tracking-tight tabular-nums",
          unavailable && "text-2xl font-medium text-muted-foreground/70"
        )}
      >
        {value}
      </p>
      <p className="text-xs text-muted-foreground">{note}</p>
    </div>
  );
}

/** One segment of the snapshot's summary line -- a number with its label,
 * not a tile, so the four headline metrics above stay the only "cards". */
function SummaryItem({ icon: Icon, value, label, title }: { icon: LucideIcon; value: string; label: string; title?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5" title={title}>
      <Icon className="size-3.5 text-muted-foreground" />
      <span className="font-medium text-foreground tabular-nums">{value}</span>
      <span>{label}</span>
    </span>
  );
}

function pricedNote(c: Completeness, emptyNote: string): string {
  if (c.bookings_total === 0) return emptyNote;
  if (c.bookings_priced === 0) return "No confirmed booking prices yet";
  if (c.is_complete) return `From ${c.bookings_priced} booking${c.bookings_priced === 1 ? "" : "s"}`;
  return `Based on ${c.bookings_priced} of ${c.bookings_total} bookings`;
}

// Upcoming revenue is forward-looking (nights from today), not tied to the
// selected period -- the note says so explicitly.
function upcomingNote(c: Completeness): string {
  if (c.bookings_total === 0) return "From today onward · no upcoming bookings";
  if (c.is_complete) return "From today onward";
  return `From today onward · ${c.bookings_priced} of ${c.bookings_total} priced`;
}

export function PortfolioSnapshotSkeleton() {
  return (
    <Card className={glassCardClassName} aria-busy="true" aria-label="Loading portfolio snapshot">
      <CardContent className="space-y-5 py-2">
        <Skeleton className="h-5 w-40" />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-9 w-28" />
              <Skeleton className="h-3 w-32" />
            </div>
          ))}
        </div>
        <Skeleton className="h-4 w-full max-w-xl" />
      </CardContent>
    </Card>
  );
}

export function PortfolioSnapshotError({ onRetry }: { onRetry: () => void }) {
  return (
    <Card className={glassCardClassName}>
      <CardContent className="flex flex-wrap items-center justify-between gap-3 py-2 text-sm">
        <div>
          <p className="font-medium">Couldn&apos;t load your portfolio snapshot</p>
          <p className="text-muted-foreground">The rest of your dashboard is still up to date.</p>
        </div>
        <Button size="sm" variant="outline" onClick={onRetry}>
          Try again
        </Button>
      </CardContent>
    </Card>
  );
}

export function PortfolioSnapshot({ data }: { data: OverviewData }) {
  const p = data.portfolio;
  const { activity, opportunities } = data;

  const occupancyNote = !data.has_properties
    ? "Add a property to start tracking"
    : p.booked_nights === 0
      ? "No bookings in this period"
      : `${p.booked_nights} of ${p.available_nights} nights booked`;

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
    <Card className={glassCardClassName}>
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div>
          <CardTitle>Portfolio snapshot</CardTitle>
          <p className="text-sm text-muted-foreground">Across all properties</p>
        </div>
        <Button variant="ghost" size="sm" render={<Link href="/dashboard/analytics" />}>
          Analytics
          <ArrowRight className="size-3.5" />
        </Button>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-y-3 sm:grid-cols-2 xl:grid-cols-4 xl:divide-x xl:divide-border/60">
          <PrimaryMetric
            icon={BedDouble}
            label="Occupancy"
            value={formatPercent(p.occupancy)}
            unavailable={p.occupancy === null}
            note={occupancyNote}
          />
          <PrimaryMetric
            icon={IndianRupee}
            label="Revenue"
            value={p.revenue === null ? "—" : formatINRCompact(p.revenue)}
            unavailable={p.revenue === null}
            note={pricedNote(p.completeness, "No bookings in this period")}
          />
          <PrimaryMetric
            icon={CalendarCheck}
            label="Upcoming revenue"
            value={p.upcoming_revenue === null ? "—" : formatINRCompact(p.upcoming_revenue)}
            unavailable={p.upcoming_revenue === null}
            note={upcomingNote(p.upcoming_completeness)}
          />
          <PrimaryMetric
            icon={Tag}
            label="ADR"
            value={formatINR(p.adr)}
            unavailable={p.adr === null}
            note={p.revpar !== null ? `RevPAR ${formatINR(p.revpar)}` : "Average daily rate"}
          />
        </div>

        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-border/60 pt-4 text-sm text-muted-foreground">
          <SummaryItem
            icon={Sparkles}
            value={String(opportunities.booking_opportunities)}
            label={opportunities.booking_opportunities === 1 ? "booking opportunity" : "booking opportunities"}
            title="Qualified guests (warm or above) from booking conversations in this period"
          />
          {opportunities.high_intent > 0 && (
            <SummaryItem
              icon={Flame}
              value={String(opportunities.high_intent)}
              label="high-intent"
              title="Of those, guests at hot or booking intent"
            />
          )}
          <SummaryItem
            icon={BadgeCheck}
            value={String(data.mira_attributed_bookings)}
            label={data.mira_attributed_bookings === 1 ? "Mira-attributed booking" : "Mira-attributed bookings"}
            title="Bookings made in this period that you confirmed came from Mira"
          />
          <SummaryItem
            icon={Phone}
            value={String(activity.total_calls)}
            label={activity.total_calls === 1 ? "call" : "calls"}
          />
          <span>{callsNote}</span>
        </div>
      </CardContent>
    </Card>
  );
}
