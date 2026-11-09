"use client";

import { Skeleton } from "@/components/ui/skeleton";
import { Cell, CellTitle } from "@/components/ui/sheet";
import { useAsync } from "@/hooks/use-async";
import { useDateRange } from "@/hooks/use-date-range";
import { useNotificationStream } from "@/hooks/use-notification-stream";
import { api } from "@/lib/api";

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

// Fixed categorical order, one hue per funnel stage -- identity, not
// magnitude, matches the dashboard's existing --chart-1..5 ramp usage.
const STAGE_COLOR_VARS = ["--chart-1", "--chart-2", "--chart-3"];

function formatPercent(ratio: number | null): string {
  return ratio === null ? "—" : `${Math.round(ratio * 100)}%`;
}

export function RecoveryAnalyticsCard() {
  const { startDateISO, endDateISO } = useDateRange();

  const { data, loading, refetch } = useAsync(
    () => api.analytics.recovery({ startDate: startDateISO, endDate: endDateISO }),
    [startDateISO, endDateISO]
  );

  // Same shared-stream "signal to refetch" pattern as the lead list below
  // this card on the same page (dashboard/opportunities/page.tsx) -- without
  // this, a new busy call or guest reply updates the lead list live but
  // leaves this card silently stale until the date range changes or the
  // page reloads. Doesn't catch host-response-only events (marking a
  // notification read doesn't emit a stream event, only creation does --
  // see backend/app/api/v1/notifications.py's stream), same limitation the
  // rest of the dashboard's live updates already have.
  useNotificationStream((notification) => {
    if (notification.channel === "busy_recovery" || notification.channel === "busy_recovery_reply") refetch();
  });

  const funnel = data?.funnel ?? [];
  const maxValue = Math.max(1, ...funnel.map((s) => s.value));

  const metrics: { label: string; value: string | number | undefined }[] = [
    { label: "Busy calls", value: data?.busy_calls },
    { label: "Recovered", value: data?.recovered },
    { label: "Converted", value: data?.converted },
    { label: "Lost", value: data?.lost },
    { label: "Avg. recovery time", value: loading ? undefined : formatDuration(data?.avg_recovery_time_seconds ?? null) },
    { label: "Avg. host response", value: loading ? undefined : formatDuration(data?.avg_host_response_seconds ?? null) },
  ];

  // Renders ruled cells: place it inside a <Sheet>.
  return (
    <>
      {metrics.map((metric) => (
        <Cell key={metric.label} className="col-span-6 p-4 sm:p-5 md:col-span-4 xl:col-span-2">
          <CellTitle>{metric.label}</CellTitle>
          {loading ? (
            <Skeleton className="h-9 w-16" />
          ) : (
            <p className="font-heading text-[clamp(1.75rem,2.4vw,2.25rem)] leading-tight font-semibold tracking-tight tabular-nums">
              {metric.value ?? "—"}
            </p>
          )}
        </Cell>
      ))}

      <Cell>
        <CellTitle>Recovery funnel</CellTitle>
        {loading ? (
          <Skeleton className="h-24 w-full" />
        ) : funnel.length === 0 || funnel[0].value === 0 ? (
          <p className="text-sm text-muted-foreground">No busy-call recoveries in this period.</p>
        ) : (
          <div className="space-y-2">
            {funnel.map((stage, i) => {
              const widthPct = Math.max(4, Math.round((stage.value / maxValue) * 100));
              const colorVar = STAGE_COLOR_VARS[i % STAGE_COLOR_VARS.length];
              // First stage is always 100% of itself, not a fraction the
              // backend ships a field for. The other two stages use the
              // backend-computed recovery_rate/conversion_rate directly
              // (both are exactly "this stage's value / busy_calls",
              // computed once server-side) rather than re-deriving the
              // same ratio from stage.value/funnel[0].value client-side.
              const pctLabel =
                stage.stage === "busy_calls"
                  ? "100%"
                  : stage.stage === "recovered"
                    ? formatPercent(data?.recovery_rate ?? null)
                    : formatPercent(data?.conversion_rate ?? null);
              return (
                <div key={stage.stage} className="flex items-center gap-3">
                  <span className="w-24 shrink-0 text-sm text-muted-foreground">{stage.label}</span>
                  <div className="h-6 flex-1 overflow-hidden bg-muted">
                    <div
                      className="h-full transition-[width]"
                      style={{ width: `${widthPct}%`, backgroundColor: `var(${colorVar})` }}
                    />
                  </div>
                  <span className="w-20 shrink-0 text-right text-sm tabular-nums text-foreground">
                    {stage.value}
                    <span className="ml-1 text-xs text-muted-foreground">({pctLabel})</span>
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </Cell>
    </>
  );
}
