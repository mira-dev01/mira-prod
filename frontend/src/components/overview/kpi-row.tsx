"use client";

import { CalendarDays, ChartPie, CircleAlert, CircleCheck, HandCoins, TrendingUp, Users, Wallet, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { InfoCard } from "@/components/overview/info-card";
import { formatINR, formatINRCompact, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Completeness, OverviewData } from "@/lib/types";

/**
 * Overview's eight headline numbers. Renders what GET /analytics/overview returns and
 * computes nothing itself -- every definition lives in backend analytics_service.py and
 * the hover text comes from the same response (`data.info`). "No data" is never shown as
 * a zero: a null value renders as a quiet "—" with a note.
 */

type Tile = {
  id: string;
  label: string;
  value: string;
  note: string;
  unavailable?: boolean;
  href: string;
  icon: LucideIcon;
  tone: "brick" | "green";
};

// Revenue counts only the nights that fall inside the selected dates, so "none" means no stays
// in this period -- not that nothing was booked (see Booked value and Upcoming revenue).
const NO_STAYS = "No stays in this period";

function pricedNote(c: Completeness, emptyNote: string): string {
  if (c.bookings_total === 0) return emptyNote;
  if (c.bookings_priced === 0) return "No prices yet";
  if (c.is_complete) return `${c.bookings_priced} booking${c.bookings_priced === 1 ? "" : "s"}`;
  return `${c.bookings_priced} of ${c.bookings_total} priced`;
}

// Booked value is what was sold in the period, whatever dates the guests stay.
function bookedNote(c: Completeness): string {
  if (c.bookings_total === 0) return "None made in this period";
  if (c.bookings_priced === 0) return "No prices yet";
  if (c.is_complete) return `${c.bookings_priced} booking${c.bookings_priced === 1 ? "" : "s"} made`;
  return `${c.bookings_priced} of ${c.bookings_total} priced`;
}

// Upcoming revenue is forward-looking (nights from today), not tied to the selected period.
function upcomingNote(c: Completeness): string {
  if (c.bookings_total === 0) return "None upcoming";
  if (c.is_complete) return "From today";
  return `From today · ${c.bookings_priced} of ${c.bookings_total} priced`;
}

function buildTiles(data: OverviewData): Tile[] {
  const p = data.portfolio;
  const occupancyNote = !data.has_properties
    ? "Add a property to start tracking"
    : p.booked_nights === 0
      ? NO_STAYS
      : `${p.booked_nights} of ${p.available_nights} nights`;
  const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

  return [
    { id: "revenue", label: "Revenue", value: formatINRCompact(p.revenue), unavailable: p.revenue === null, note: pricedNote(p.completeness, NO_STAYS), href: "/dashboard/analytics", icon: Wallet, tone: "brick" },
    { id: "booked_value", label: "Booked value", value: formatINRCompact(p.booked_value), unavailable: p.booked_value === null, note: bookedNote(p.booked_completeness), href: "/dashboard/calendar", icon: HandCoins, tone: "brick" },
    { id: "upcoming_revenue", label: "Upcoming revenue", value: formatINRCompact(p.upcoming_revenue), unavailable: p.upcoming_revenue === null, note: upcomingNote(p.upcoming_completeness), href: "/dashboard/calendar", icon: CalendarDays, tone: "brick" },
    { id: "occupancy", label: "Occupancy", value: formatPercent(p.occupancy), unavailable: p.occupancy === null, note: occupancyNote, href: "/dashboard/analytics", icon: ChartPie, tone: "brick" },
    { id: "adr", label: "ADR", value: formatINR(p.adr), unavailable: p.adr === null, note: p.revpar !== null ? `RevPAR ${formatINR(p.revpar)}` : "Per night", href: "/dashboard/analytics", icon: TrendingUp, tone: "brick" },
    { id: "leads", label: "Leads", value: data.leads ? String(data.leads.total) : "—", note: data.leads ? `${data.leads.open} open · ${data.leads.hot} hot` : "", href: "/dashboard/leads?tab=leads", icon: Users, tone: "brick" },
    { id: "open_actions", label: "Open actions", value: data.actions ? String(data.actions.open) : "—", note: data.actions ? `${data.actions.escalations} ${plural(data.actions.escalations, "escalation", "escalations")}` : "", href: "/dashboard/tickets", icon: CircleAlert, tone: "brick" },
    { id: "resolution_rate", label: "Resolution rate", value: formatPercent(data.resolution?.value ?? null), unavailable: !data.resolution || data.resolution.value === null, note: !data.resolution || data.resolution.total === 0 ? "No conversations" : `${data.resolution.resolved} of ${data.resolution.total} conversations`, href: "/dashboard/calls", icon: CircleCheck, tone: "green" },
  ];
}

const GRID = "grid grid-cols-2 gap-4 sm:grid-cols-4 2xl:grid-cols-8";

export function KpiRowSkeleton() {
  return (
    <div className={GRID} aria-busy="true" aria-label="Loading key metrics">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="space-y-3 rounded-xl border border-border bg-card p-4">
          <Skeleton className="h-4 w-20" />
          <Skeleton className="h-7 w-24" />
          <Skeleton className="h-3 w-28" />
        </div>
      ))}
    </div>
  );
}

export function KpiRowError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card p-4">
      <p className="text-sm font-medium">Couldn&apos;t load your key metrics</p>
      <Button size="sm" variant="outline" onClick={onRetry}>
        Try again
      </Button>
    </div>
  );
}

export function KpiRow({ data }: { data: OverviewData }) {
  return (
    <section aria-label="Key metrics" className={GRID}>
      {buildTiles(data).map((tile) => {
        const Icon = tile.icon;
        const green = tile.tone === "green";
        return (
          <InfoCard key={tile.id} info={data.info?.[tile.id]} href={tile.href} label={`${tile.label}: ${tile.value}. Open details`} className="p-4">
            {(infoButton) => (
              <div className="flex flex-col gap-2.5">
                <div className="flex items-center justify-between gap-2">
                  <span className={cn("flex items-center gap-1 text-xs font-semibold", green ? "text-(--status-live)" : "text-heading")}>
                    {tile.label}
                    {infoButton}
                  </span>
                  <span
                    aria-hidden
                    className={cn(
                      "flex size-7 shrink-0 items-center justify-center rounded-lg",
                      green ? "bg-(--status-live-bg) text-(--status-live)" : "bg-primary/10 text-primary"
                    )}
                  >
                    <Icon className="size-4" />
                  </span>
                </div>
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span
                    className={cn(
                      "font-heading text-[clamp(1.25rem,1.6vw,1.5rem)] leading-tight font-semibold tracking-tight tabular-nums",
                      tile.unavailable && "text-muted-foreground"
                    )}
                  >
                    {tile.value}
                  </span>
                  <span className="text-xs text-muted-foreground">{tile.note}</span>
                </div>
              </div>
            )}
          </InfoCard>
        );
      })}
    </section>
  );
}
