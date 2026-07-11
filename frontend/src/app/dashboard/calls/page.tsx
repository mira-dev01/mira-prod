"use client";

import { useState } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAsync } from "@/hooks/use-async";
import { useDateRange } from "@/hooks/use-date-range";
import { api } from "@/lib/api";
import { isBrowserTestIdentity } from "@/lib/utils";
import { DateRangePicker } from "@/components/date-range-picker";
import { StatusChip, type StatusTone } from "@/components/status-chip";

const statusTone: Record<string, StatusTone> = {
  completed: "live",
  active: "progress",
  in_progress: "progress",
  escalated: "destructive",
  failed: "destructive",
  missed: "destructive",
};

function formatDuration(minutes: number | null): string {
  if (minutes === null) return "—";
  const whole = Math.floor(minutes);
  const seconds = Math.round((minutes - whole) * 60);
  return `${whole}m ${seconds}s`;
}

export default function CallsPage() {
  const [includeTestCalls, setIncludeTestCalls] = useState(false);
  const { startDateISO, endDateISO } = useDateRange();
  const { data: calls, loading } = useAsync(
    () => api.calls.list({ startDate: startDateISO, endDate: endDateISO, includeTestCalls }),
    [startDateISO, endDateISO, includeTestCalls]
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="page-title">Calls</h1>
          <p className="text-sm text-muted-foreground">Every call MIRA has answered across your properties</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <Switch id="include-test-calls" checked={includeTestCalls} onCheckedChange={setIncludeTestCalls} />
            <Label htmlFor="include-test-calls" className="text-sm text-muted-foreground">
              Include browser test calls
            </Label>
          </div>
          <DateRangePicker />
        </div>
      </div>

      {loading ? (
        <Skeleton className="h-64 w-full" />
      ) : !calls || calls.length === 0 ? (
        <p className="text-sm text-muted-foreground">No calls yet.</p>
      ) : (
        <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Caller</TableHead>
              <TableHead>Name</TableHead>
              <TableHead>Phone</TableHead>
              <TableHead>Duration</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Urgency</TableHead>
              <TableHead>Started</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {calls.map((call) => (
              <TableRow key={call.id} className="cursor-pointer">
                <TableCell>
                  <Link href={`/dashboard/calls/${call.id}`} className="hover:underline">
                    {isBrowserTestIdentity(call.caller_number) ? (
                      <Badge variant="outline">Browser test</Badge>
                    ) : (
                      call.caller_number ?? "Unknown"
                    )}
                  </Link>
                </TableCell>
                <TableCell>{call.guest_name ?? "—"}</TableCell>
                <TableCell>
                  {isBrowserTestIdentity(call.guest_phone) ? "—" : call.guest_phone ?? "—"}
                </TableCell>
                <TableCell>{formatDuration(call.duration_minutes)}</TableCell>
                <TableCell>
                  <StatusChip status={call.status} tone={statusTone[call.status] ?? "neutral"} />
                </TableCell>
                <TableCell className="capitalize">{call.urgency ?? "—"}</TableCell>
                <TableCell>{call.started_at ? new Date(call.started_at).toLocaleString() : "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        </div>
      )}
    </div>
  );
}
