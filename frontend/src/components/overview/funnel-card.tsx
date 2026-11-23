"use client";

import Link from "next/link";
import { ArrowRight, Filter } from "lucide-react";
import { InfoCard } from "@/components/overview/info-card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { FunnelStage, OverviewMetricInfo } from "@/lib/types";

// Where each stage's number can be inspected. Keyed by the backend stage key; a stage
// with no entry here is shown without a link rather than pointing somewhere wrong.
const STAGE_HREF: Record<string, string> = {
  all_conversations: "/dashboard/calls",
  booking_related: "/dashboard/calls",
  qualified_leads: "/dashboard/leads?tab=leads",
  hot_leads: "/dashboard/leads?tab=leads",
  booking_intent: "/dashboard/leads?tab=leads",
  converted: "/dashboard/leads?tab=leads&status=booked",
};

/** Booking funnel: bars sized from the stage counts the API returns, nothing computed here
 * except the bar width relative to the first stage. */
export function FunnelCard({
  stages,
  info,
  loading,
  className,
}: {
  stages: FunnelStage[] | undefined;
  info: OverviewMetricInfo | undefined;
  loading: boolean;
  className?: string;
}) {
  const max = Math.max(1, ...(stages ?? []).map((s) => s.value));
  return (
    <InfoCard info={info} className={cn("flex flex-col gap-4 p-5", className)}>
      {(infoButton) => (
        <>
          <div className="flex items-center justify-between gap-2">
            <h2 className="flex items-center gap-2 font-heading text-[15px] leading-5 font-semibold text-heading">
              <Filter className="size-4 text-primary" aria-hidden />
              <Link href="/dashboard/analytics" className="hover:underline">
                Booking funnel
              </Link>
              {infoButton}
            </h2>
          </div>
          {loading ? (
            <Skeleton className="h-40 w-full" />
          ) : !stages ? (
            <p className="flex-1 content-center text-center text-sm text-muted-foreground">Funnel unavailable right now.</p>
          ) : stages.every((s) => s.value === 0) ? (
            <p className="flex-1 content-center text-center text-sm text-muted-foreground">No conversations in this period.</p>
          ) : (
            <div className="flex flex-1 flex-col justify-center">
            <ol className="space-y-2.5">
              {stages.map((stage, i) => {
                const href = STAGE_HREF[stage.key];
                const row = (
                  <div className="grid grid-cols-[minmax(0,9rem)_1fr_auto] items-center gap-3 text-sm">
                    <span className="truncate text-muted-foreground" title={stage.label}>{stage.label}</span>
                    <span className="h-5 rounded-md bg-muted">
                      <span
                        className="block h-full rounded-md"
                        style={{ width: `${Math.max(2, (stage.value / max) * 100)}%`, background: `var(--funnel-${i + 1})` }}
                      />
                    </span>
                    <span className="flex items-baseline gap-2 tabular-nums">
                      <span className="font-semibold">{stage.value}</span>
                      <span className="w-10 text-right text-xs text-muted-foreground">
                        {stage.rate_from_previous === null ? "—" : formatPercent(stage.rate_from_previous)}
                      </span>
                    </span>
                  </div>
                );
                return (
                  <li key={stage.key}>
                    {href ? (
                      <Link href={href} className="block rounded-md hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none">
                        {row}
                      </Link>
                    ) : (
                      row
                    )}
                  </li>
                );
              })}
            </ol>
            </div>
          )}
          <Link href="/dashboard/analytics" className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
            Percentages are step to step <ArrowRight className="size-3" />
          </Link>
        </>
      )}
    </InfoCard>
  );
}
