"use client";

import Link from "next/link";
import { ArrowRight, PhoneCall } from "lucide-react";
import { InfoCard } from "@/components/overview/info-card";
import { StatusChip, type StatusTone } from "@/components/status-chip";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDuration } from "@/components/calls-table";
import { CALL_LOG_LABEL, callLogState, type CallLogState } from "@/lib/call-log";
import { formatLeadTimestamp } from "@/lib/leads";
import { cn, isBrowserTestIdentity } from "@/lib/utils";
import type { CallSessionOut, OverviewMetricInfo } from "@/lib/types";

const STATE_TONE: Record<CallLogState, StatusTone> = {
  incoming: "live",
  outgoing: "progress",
  missed: "destructive",
  failed: "destructive",
};

/** Latest calls in the selected period, laid out like a phone's call log: who, when, and
 * one of Incoming / Outgoing / Missed / Failed. Missed and failed calls are red. The summary,
 * transcript and anything finer live on the call's page (Calls tab). */
export function RecentCallsCard({
  calls,
  info,
  loading,
  className,
}: {
  calls: CallSessionOut[];
  info: OverviewMetricInfo | undefined;
  loading: boolean;
  className?: string;
}) {
  return (
    <InfoCard info={info} className={cn("flex flex-col gap-4 p-5", className)}>
      {(infoButton) => (
        <>
          <h2 className="flex items-center gap-2 font-heading text-[15px] leading-5 font-semibold text-heading">
            <PhoneCall className="size-4 text-primary" aria-hidden />
            <Link href="/dashboard/calls" className="hover:underline">
              Recent call activity
            </Link>
            {infoButton}
          </h2>
          {loading ? (
            <Skeleton className="h-40 w-full" />
          ) : calls.length === 0 ? (
            <p className="flex-1 content-center text-center text-sm text-muted-foreground">No calls in this period.</p>
          ) : (
            <ul className="flex-1">
              {calls.map((call) => {
                const state = callLogState(call);
                const red = state === "missed" || state === "failed";
                const label =
                  !red && call.duration_minutes !== null
                    ? `${CALL_LOG_LABEL[state]} · ${formatDuration(call.duration_minutes)}`
                    : CALL_LOG_LABEL[state];
                return (
                  <li key={call.id} className="flex items-center justify-between gap-3 border-b border-border py-2.5 first:pt-0 last:border-0 last:pb-0">
                    <div className="min-w-0">
                      <p className={cn("truncate text-sm font-medium", red && "text-destructive")}>
                        {isBrowserTestIdentity(call.caller_number) ? "Browser test" : call.guest_name ?? call.caller_number ?? "Unknown"}
                      </p>
                      <p className={cn("text-xs", red ? "text-destructive/80" : "text-muted-foreground")}>
                        {call.started_at ? formatLeadTimestamp(call.started_at) : "—"}
                      </p>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-1">
                      <StatusChip status={label} tone={STATE_TONE[state]} className="normal-case" />
                      <Link href={`/dashboard/calls/${call.id}`} className="text-xs font-medium text-primary hover:underline">
                        {call.ai_summary ? "AI summary" : "Details"}
                      </Link>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          <Link href="/dashboard/calls" className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
            View all calls <ArrowRight className="size-3.5" />
          </Link>
        </>
      )}
    </InfoCard>
  );
}
