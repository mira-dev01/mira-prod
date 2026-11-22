"use client";

import Link from "next/link";
import { ArrowRight, PhoneCall } from "lucide-react";
import { InfoCard } from "@/components/overview/info-card";
import { StatusChip } from "@/components/status-chip";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDuration } from "@/components/calls-table";
import { formatLeadTimestamp } from "@/lib/leads";
import { isBrowserTestIdentity } from "@/lib/utils";
import type { CallSessionOut, OverviewMetricInfo } from "@/lib/types";

const MISSED = new Set(["MISSED_AGENT_BUSY", "MISSED_SYSTEM_FAILURE", "TRANSFERRED_TO_HOST_MISSED", "UNRESPONSIVE"]);

/** Latest calls in the selected period. Mira only takes inbound calls, so the state is
 * Answered (with duration) or Missed -- never a made-up direction. */
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
    <InfoCard info={info} className={`flex flex-col gap-4 p-5 ${className ?? ""}`}>
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
            <p className="text-sm text-muted-foreground">No calls in this period.</p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/60">
                    <TableHead>Caller</TableHead>
                    <TableHead>Date &amp; time</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Details</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {calls.map((call) => {
                    const missed = MISSED.has(call.call_type) || call.status === "failed";
                    const href = `/dashboard/calls/${call.id}`;
                    return (
                      <TableRow key={call.id}>
                        <TableCell className="font-medium">
                          {isBrowserTestIdentity(call.caller_number) ? "Browser test" : call.guest_name ?? call.caller_number ?? "Unknown"}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-muted-foreground">
                          {call.started_at ? formatLeadTimestamp(call.started_at) : "—"}
                        </TableCell>
                        <TableCell>
                          <StatusChip
                            status={missed ? "Missed" : `Answered · ${formatDuration(call.duration_minutes)}`}
                            tone={missed ? "destructive" : "live"}
                            className="normal-case"
                          />
                        </TableCell>
                        <TableCell className="whitespace-nowrap">
                          <Link href={href} className="font-medium text-primary hover:underline">
                            {call.ai_summary ? "AI summary" : "Details"}
                          </Link>
                          {call.transcript && (
                            <>
                              <span className="px-1.5 text-muted-foreground">·</span>
                              <Link href={href} className="font-medium text-primary hover:underline">
                                Transcript
                              </Link>
                            </>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
          <Link href="/dashboard/calls" className="mt-auto flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
            View all calls <ArrowRight className="size-3.5" />
          </Link>
        </>
      )}
    </InfoCard>
  );
}
