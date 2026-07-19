"use client";

import { useRouter } from "next/navigation";
import { ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { StatusChip, type StatusTone } from "@/components/status-chip";
import { cn, isBrowserTestIdentity } from "@/lib/utils";
import type { CallSessionOut, CallType } from "@/lib/types";

export const callStatusTone: Record<string, StatusTone> = {
  completed: "live",
  active: "progress",
  in_progress: "progress",
  escalated: "destructive",
  failed: "destructive",
  missed: "destructive",
};

// Same vocabulary/meaning as lead-detail-panel.tsx's leadUrgencyTone (both
// read escalate_to_host's `urgency: low|medium|high|emergency`) -- kept
// local rather than importing that one since these are unrelated
// components coincidentally sharing a domain value, not a shared dependency.
export const callUrgencyTone: Record<string, StatusTone> = {
  emergency: "destructive",
  high: "destructive",
  medium: "pending",
  low: "neutral",
};

// call_type badge colors -- BOOKING_LEAD and GENERAL_QUERY share the same
// green "qualified" look (distinguished only by label text), matching the
// 6-color legend the host-facing spec gives for a 7-value taxonomy.
export const callTypeTone: Record<CallType, StatusTone> = {
  BOOKING_LEAD: "live",
  GENERAL_QUERY: "live",
  GUEST_SUPPORT: "progress",
  EXISTING_BOOKING: "purple",
  INCOMPLETE: "orange",
  UNKNOWN: "neutral",
  JUNK: "destructive",
};

export const callTypeLabel: Record<CallType, string> = {
  BOOKING_LEAD: "Booking Lead",
  GENERAL_QUERY: "Qualified",
  GUEST_SUPPORT: "Guest Support",
  EXISTING_BOOKING: "Existing Guest",
  INCOMPLETE: "Incomplete",
  UNKNOWN: "Unknown",
  JUNK: "Junk",
};

// Left-border color per row, reusing the exact same StatusTone CSS vars as
// StatusChip's dot glyph (status-chip.tsx / lib/tone.ts) rather than
// inventing new colors -- gives each row a glanceable urgency signal at a
// glance instead of a wall of identical white rows.
const urgencyBorderClass: Record<string, string> = {
  emergency: "border-l-destructive",
  high: "border-l-destructive",
  medium: "border-l-(--status-pending)",
  low: "border-l-(--priority-low)",
};

function formatDuration(minutes: number | null): string {
  if (minutes === null) return "—";
  const whole = Math.floor(minutes);
  const seconds = Math.round((minutes - whole) * 60);
  return `${whole}m ${seconds}s`;
}

/**
 * One shared row rendering for both the full Calls page and Overview's
 * "Recent calls" card -- previously two independent ad hoc <Table>s with
 * different columns and no click handler on Overview's version. `compact`
 * drops Name/Phone/Duration/Urgency down to just Caller/Status/Started for
 * the Overview card's narrower space.
 */
export function CallsTable({ calls, compact = false }: { calls: CallSessionOut[]; compact?: boolean }) {
  const router = useRouter();

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Caller</TableHead>
            {!compact && (
              <>
                <TableHead>Name</TableHead>
                <TableHead>Phone</TableHead>
                <TableHead>Duration</TableHead>
              </>
            )}
            <TableHead>Status</TableHead>
            {!compact && (
              <>
                <TableHead>Urgency</TableHead>
                <TableHead>Type</TableHead>
              </>
            )}
            <TableHead>Started</TableHead>
            <TableHead className="w-8" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {calls.map((call) => (
            <TableRow
              key={call.id}
              onClick={() => router.push(`/dashboard/calls/${call.id}`)}
              className={cn(
                "cursor-pointer border-l-4",
                call.urgency ? urgencyBorderClass[call.urgency] ?? "border-l-transparent" : "border-l-transparent"
              )}
            >
              <TableCell>
                {isBrowserTestIdentity(call.caller_number) ? (
                  <Badge variant="outline">Browser test</Badge>
                ) : (
                  call.caller_number ?? "Unknown"
                )}
              </TableCell>
              {!compact && (
                <>
                  <TableCell>{call.guest_name ?? "—"}</TableCell>
                  <TableCell>{isBrowserTestIdentity(call.guest_phone) ? "—" : call.guest_phone ?? "—"}</TableCell>
                  <TableCell>{formatDuration(call.duration_minutes)}</TableCell>
                </>
              )}
              <TableCell>
                <StatusChip status={call.status} tone={callStatusTone[call.status] ?? "neutral"} />
              </TableCell>
              {!compact && (
                <>
                  <TableCell>
                    {call.urgency ? (
                      <StatusChip status={call.urgency} tone={callUrgencyTone[call.urgency] ?? "neutral"} />
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell>
                    <StatusChip status={callTypeLabel[call.call_type]} tone={callTypeTone[call.call_type]} />
                  </TableCell>
                </>
              )}
              <TableCell>{call.started_at ? new Date(call.started_at).toLocaleString() : "—"}</TableCell>
              <TableCell>
                <ChevronRight className="size-4 text-muted-foreground" />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
